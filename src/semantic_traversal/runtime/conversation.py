"""Durable, append-only conversation storage for the first runtime seam."""

from __future__ import annotations

import datetime as dt
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from uuid import uuid4


SCHEMA_VERSION = 1
ALLOWED_ROLES = frozenset({"user", "synthesis"})
Clock = Callable[[], dt.datetime]


class RuntimeConversationError(ValueError):
    """A runtime conversation operation could not be completed safely."""


@dataclass(frozen=True)
class Message:
    message_id: int
    conversation_id: str
    ordinal: int
    role: str
    content: str
    created_at: str


@dataclass(frozen=True)
class Conversation:
    conversation_id: str
    created_at: str
    messages: tuple[Message, ...] = ()


_CONVERSATION_COLUMNS = ("conversation_id", "created_at")
_MESSAGE_COLUMNS = ("message_id", "conversation_id", "ordinal", "role", "content", "created_at")
_EXPECTED_COLUMN_DEFINITIONS = {
    "conversations": {
        "conversation_id": ("TEXT", 0, None, 1),
        "created_at": ("TEXT", 1, None, 0),
    },
    "messages": {
        "message_id": ("INTEGER", 0, None, 1),
        "conversation_id": ("TEXT", 1, None, 0),
        "ordinal": ("INTEGER", 1, None, 0),
        "role": ("TEXT", 1, None, 0),
        "content": ("TEXT", 1, None, 0),
        "created_at": ("TEXT", 1, None, 0),
    },
}


def _path(database_path: str | Path) -> Path:
    if isinstance(database_path, Path):
        result = database_path
    elif isinstance(database_path, str) and database_path.strip():
        result = Path(database_path)
    else:
        raise RuntimeConversationError("an explicit runtime database path is required")
    return result


def _timestamp(clock: Clock | None) -> str:
    value = (clock or (lambda: dt.datetime.now(dt.timezone.utc)))()
    if not isinstance(value, dt.datetime) or value.tzinfo is None:
        raise RuntimeConversationError("runtime clock must return an aware UTC datetime")
    return value.astimezone(dt.timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _create_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE conversations (
            conversation_id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL
        );

        CREATE TABLE messages (
            message_id INTEGER PRIMARY KEY,
            conversation_id TEXT NOT NULL,
            ordinal INTEGER NOT NULL,
            role TEXT NOT NULL CHECK (role IN ('user', 'synthesis')),
            content TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (conversation_id) REFERENCES conversations(conversation_id),
            UNIQUE (conversation_id, ordinal)
        );

        PRAGMA user_version = 1;
        """
    )


def _table_info(connection: sqlite3.Connection, table: str) -> tuple[sqlite3.Row, ...]:
    return tuple(connection.execute(f"PRAGMA table_info({table})"))


def _validate_schema(connection: sqlite3.Connection) -> None:
    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    if tables != {"conversations", "messages"}:
        raise RuntimeConversationError("runtime database is missing schema-v1 tables")
    for table, expected_columns in (("conversations", _CONVERSATION_COLUMNS), ("messages", _MESSAGE_COLUMNS)):
        info = _table_info(connection, table)
        if tuple(row[1] for row in info) != expected_columns:
            raise RuntimeConversationError(f"runtime {table} schema is incompatible")
        expected_definitions = _EXPECTED_COLUMN_DEFINITIONS[table]
        for row in info:
            expected_type, expected_not_null, expected_default, expected_primary_key = expected_definitions[row[1]]
            if (
                row[2].upper(),
                row[3],
                row[4],
                row[5],
            ) != (expected_type, expected_not_null, expected_default, expected_primary_key):
                raise RuntimeConversationError(f"runtime {table}.{row[1]} definition is incompatible")

    foreign_keys = {
        (row[2], row[3], row[4])
        for row in connection.execute("PRAGMA foreign_key_list(messages)")
    }
    if ("conversations", "conversation_id", "conversation_id") not in foreign_keys:
        raise RuntimeConversationError("runtime message foreign key is incompatible")

    unique_pairs = set()
    for index in connection.execute("PRAGMA index_list(messages)"):
        if index[2]:
            unique_pairs.add(tuple(row[2] for row in connection.execute(f"PRAGMA index_info([{index[1]}])")))
    if ("conversation_id", "ordinal") not in unique_pairs:
        raise RuntimeConversationError("runtime message ordering constraint is incompatible")

    schema_sql = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'messages'"
    ).fetchone()[0]
    if not re.search(r"CHECK\s*\(\s*role\s+IN\s*\(\s*'user'\s*,\s*'synthesis'\s*\)\s*\)", schema_sql, re.IGNORECASE):
        raise RuntimeConversationError("runtime message role constraint is incompatible")


def _open_connection(database_path: str | Path, *, allow_create: bool) -> sqlite3.Connection:
    path = _path(database_path)
    if not allow_create and not path.is_file():
        raise RuntimeConversationError(f"runtime database does not exist: {path}")
    connection: sqlite3.Connection | None = None
    try:
        if allow_create:
            connection = sqlite3.connect(str(path))
        else:
            connection = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=rw", uri=True)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection
    except RuntimeConversationError:
        if connection is not None:
            connection.close()
        raise
    except sqlite3.Error as exc:
        if connection is not None:
            connection.close()
        raise RuntimeConversationError(f"could not open runtime database: {exc}") from exc


def _runtime_schema_state(connection: sqlite3.Connection, *, initialize_empty: bool) -> None:
    version = connection.execute("PRAGMA user_version").fetchone()[0]
    tables = tuple(connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall())
    if version == 0 and not tables and initialize_empty:
        _create_schema(connection)
        connection.commit()
        return
    if version != SCHEMA_VERSION:
        raise RuntimeConversationError(f"unsupported runtime schema version: {version}")
    _validate_schema(connection)


def _connect_runtime(database_path: str | Path) -> sqlite3.Connection:
    connection = _open_connection(database_path, allow_create=False)
    try:
        _runtime_schema_state(connection, initialize_empty=False)
        return connection
    except Exception:
        connection.close()
        raise


def initialize_runtime(database_path: str | Path) -> None:
    """Create or validate schema v1 at the explicitly supplied path."""

    connection = _open_connection(database_path, allow_create=True)
    try:
        _runtime_schema_state(connection, initialize_empty=True)
    except Exception:
        connection.close()
        raise
    connection.close()


def create_conversation(database_path: str | Path, *, clock: Clock | None = None) -> Conversation:
    """Create one empty conversation with a generated UUID."""

    conversation_id = str(uuid4())
    created_at = _timestamp(clock)
    connection = _connect_runtime(database_path)
    try:
        connection.execute("BEGIN")
        connection.execute(
            "INSERT INTO conversations (conversation_id, created_at) VALUES (?, ?)",
            (conversation_id, created_at),
        )
        connection.commit()
        return Conversation(conversation_id=conversation_id, created_at=created_at)
    except sqlite3.Error as exc:
        connection.rollback()
        raise RuntimeConversationError(f"could not create conversation: {exc}") from exc
    finally:
        connection.close()


def _validate_message(role: str, content: str) -> None:
    if not isinstance(role, str) or role not in ALLOWED_ROLES:
        raise RuntimeConversationError("conversation message role must be 'user' or 'synthesis'")
    if not isinstance(content, str) or not content or not content.strip():
        raise RuntimeConversationError("conversation message content must be nonblank text")


def append_message(
    database_path: str | Path,
    conversation_id: str,
    role: str,
    content: str,
    *,
    clock: Clock | None = None,
) -> Message:
    """Append one validated message in a transactionally assigned ordinal."""

    _validate_message(role, content)
    created_at = _timestamp(clock)
    connection = _connect_runtime(database_path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        conversation = connection.execute(
            "SELECT 1 FROM conversations WHERE conversation_id = ?", (conversation_id,)
        ).fetchone()
        if conversation is None:
            raise RuntimeConversationError(f"conversation does not exist: {conversation_id}")
        ordinal = connection.execute(
            "SELECT COALESCE(MAX(ordinal), -1) + 1 FROM messages WHERE conversation_id = ?",
            (conversation_id,),
        ).fetchone()[0]
        cursor = connection.execute(
            """
            INSERT INTO messages (conversation_id, ordinal, role, content, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (conversation_id, ordinal, role, content, created_at),
        )
        connection.commit()
        return Message(
            message_id=cursor.lastrowid,
            conversation_id=conversation_id,
            ordinal=ordinal,
            role=role,
            content=content,
            created_at=created_at,
        )
    except RuntimeConversationError:
        connection.rollback()
        raise
    except sqlite3.Error as exc:
        connection.rollback()
        raise RuntimeConversationError(f"could not append conversation message: {exc}") from exc
    finally:
        connection.close()


def get_conversation(database_path: str | Path, conversation_id: str) -> Conversation:
    """Return the complete exact transcript ordered by its persisted ordinal."""

    connection = _connect_runtime(database_path)
    try:
        row = connection.execute(
            "SELECT conversation_id, created_at FROM conversations WHERE conversation_id = ?",
            (conversation_id,),
        ).fetchone()
        if row is None:
            raise RuntimeConversationError(f"conversation does not exist: {conversation_id}")
        messages: list[Message] = []
        for message in connection.execute(
            """
            SELECT message_id, conversation_id, ordinal, role, content, created_at
            FROM messages WHERE conversation_id = ? ORDER BY ordinal ASC
            """,
            (conversation_id,),
        ):
            role = message["role"]
            if role not in ALLOWED_ROLES:
                raise RuntimeConversationError(f"malformed persisted conversation role: {role!r}")
            messages.append(
                Message(
                    message_id=message["message_id"],
                    conversation_id=message["conversation_id"],
                    ordinal=message["ordinal"],
                    role=role,
                    content=message["content"],
                    created_at=message["created_at"],
                )
            )
        return Conversation(row["conversation_id"], row["created_at"], tuple(messages))
    except RuntimeConversationError:
        raise
    except sqlite3.Error as exc:
        raise RuntimeConversationError(f"could not read conversation: {exc}") from exc
    finally:
        connection.close()


__all__ = [
    "Conversation",
    "Message",
    "RuntimeConversationError",
    "append_message",
    "create_conversation",
    "get_conversation",
    "initialize_runtime",
]
