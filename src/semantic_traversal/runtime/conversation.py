"""Durable runtime state for conversations, inference, conformance, and execution."""

from __future__ import annotations

import datetime as dt
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from uuid import uuid4


LEGACY_SCHEMA_VERSION = 1
ROUTER_SCHEMA_VERSION = 2
MODEL_RUN_SCHEMA_VERSION = 3
CONFORMANCE_SCHEMA_VERSION = 4
RETRIEVAL_SCHEMA_VERSION = 5
SCHEMA_VERSION = 7
CONFORMANCE_CONTRACT_VERSION = "catalog-conformance-v2"
HISTORICAL_CONFORMANCE_CONTRACT_VERSION = "catalog-conformance-v1"
ALLOWED_ROLES = frozenset({"user", "synthesis"})
HISTORICAL_ROUTER_V1_PROMPT_VERSION = "router-v1"
HISTORICAL_ROUTER_V1_PROMPT_SHA256 = "sha256:32abaecb56571dd80b6915ac2b6c01a0e02cbbec0487dc3aa1e6aeb74d0ca352"
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
_BASE_DEFINITIONS = {
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
_V2_MODEL_RUN_COLUMNS = (
    "run_id", "conversation_id", "trigger_message_id", "run_kind", "provider", "model",
    "prompt_version", "status", "started_at", "completed_at", "provider_response_id",
    "output_json", "error_type", "error_message", "input_tokens", "cached_input_tokens",
    "output_tokens", "reasoning_tokens", "total_tokens",
)
_V3_MODEL_RUN_COLUMNS = (
    "run_id", "conversation_id", "trigger_message_id", "run_kind", "parent_run_id",
    "capability_catalog_sha256", "provider", "model", "prompt_version", "status", "started_at",
    "completed_at", "provider_response_id", "output_json", "error_type", "error_message",
    "input_tokens", "cached_input_tokens", "output_tokens", "reasoning_tokens", "total_tokens",
)
_V6_MODEL_RUN_COLUMNS = (
    "run_id", "conversation_id", "trigger_message_id", "run_kind", "parent_run_id",
    "capability_catalog_sha256", "provider", "model", "prompt_version", "status", "started_at",
    "completed_at", "provider_response_id", "output_json", "input_json", "input_sha256",
    "output_text", "produced_message_id", "error_type", "error_message", "input_tokens",
    "cached_input_tokens", "output_tokens", "reasoning_tokens", "total_tokens",
)
_CONFORMANCE_COLUMNS = (
    "conformance_id", "retrieval_run_id", "retrieval_proposal_sha256", "capability_catalog_sha256",
    "contract_version", "status", "checked_at", "result_json",
)
_EXECUTION_COLUMNS = (
    "execution_id", "conformance_id", "retrieval_run_id", "retrieval_proposal_sha256",
    "capability_catalog_sha256", "retrieval_package_id", "retrieval_package_identity_version",
    "substrate_sha256", "vectors_sha256", "package_verification_contract_version",
    "execution_contract_version", "status", "started_at", "completed_at", "result_json",
)
_MODEL_RUN_DEFINITIONS = {
    "run_id": ("TEXT", 0, None, 1),
    "conversation_id": ("TEXT", 1, None, 0),
    "trigger_message_id": ("INTEGER", 1, None, 0),
    "run_kind": ("TEXT", 1, None, 0),
    "parent_run_id": ("TEXT", 0, None, 0),
    "capability_catalog_sha256": ("TEXT", 0, None, 0),
    "provider": ("TEXT", 1, None, 0),
    "model": ("TEXT", 1, None, 0),
    "prompt_version": ("TEXT", 1, None, 0),
    "status": ("TEXT", 1, None, 0),
    "started_at": ("TEXT", 1, None, 0),
    "completed_at": ("TEXT", 0, None, 0),
    "provider_response_id": ("TEXT", 0, None, 0),
    "output_json": ("TEXT", 0, None, 0),
    "error_type": ("TEXT", 0, None, 0),
    "error_message": ("TEXT", 0, None, 0),
    "input_tokens": ("INTEGER", 0, None, 0),
    "cached_input_tokens": ("INTEGER", 0, None, 0),
    "output_tokens": ("INTEGER", 0, None, 0),
    "reasoning_tokens": ("INTEGER", 0, None, 0),
    "total_tokens": ("INTEGER", 0, None, 0),
}
_V6_MODEL_RUN_DEFINITIONS = {
    **_MODEL_RUN_DEFINITIONS,
    "input_json": ("TEXT", 0, None, 0),
    "input_sha256": ("TEXT", 0, None, 0),
    "output_text": ("TEXT", 0, None, 0),
    "produced_message_id": ("INTEGER", 0, None, 0),
}
_CONFORMANCE_DEFINITIONS = {
    "conformance_id": ("TEXT", 0, None, 1),
    "retrieval_run_id": ("TEXT", 1, None, 0),
    "retrieval_proposal_sha256": ("TEXT", 1, None, 0),
    "capability_catalog_sha256": ("TEXT", 1, None, 0),
    "contract_version": ("TEXT", 1, None, 0),
    "status": ("TEXT", 1, None, 0),
    "checked_at": ("TEXT", 1, None, 0),
    "result_json": ("TEXT", 1, None, 0),
}
_EXECUTION_DEFINITIONS = {
    "execution_id": ("TEXT", 0, None, 1),
    "conformance_id": ("TEXT", 1, None, 0),
    "retrieval_run_id": ("TEXT", 1, None, 0),
    "retrieval_proposal_sha256": ("TEXT", 1, None, 0),
    "capability_catalog_sha256": ("TEXT", 1, None, 0),
    "retrieval_package_id": ("TEXT", 1, None, 0),
    "retrieval_package_identity_version": ("TEXT", 1, None, 0),
    "substrate_sha256": ("TEXT", 1, None, 0),
    "vectors_sha256": ("TEXT", 1, None, 0),
    "package_verification_contract_version": ("TEXT", 1, None, 0),
    "execution_contract_version": ("TEXT", 1, None, 0),
    "status": ("TEXT", 1, None, 0),
    "started_at": ("TEXT", 1, None, 0),
    "completed_at": ("TEXT", 0, None, 0),
    "result_json": ("TEXT", 1, None, 0),
}


def _definitions(schema_version: int) -> dict[str, dict[str, tuple[str, int, str | None, int]]]:
    definitions = {name: dict(values) for name, values in _BASE_DEFINITIONS.items()}
    if schema_version == ROUTER_SCHEMA_VERSION:
        definitions["model_runs"] = {
            key: _MODEL_RUN_DEFINITIONS[key]
            for key in _V2_MODEL_RUN_COLUMNS
            if key not in {"parent_run_id", "capability_catalog_sha256"}
        }
    elif schema_version in {MODEL_RUN_SCHEMA_VERSION, CONFORMANCE_SCHEMA_VERSION, RETRIEVAL_SCHEMA_VERSION, 6, SCHEMA_VERSION}:
        definitions["model_runs"] = dict(_V6_MODEL_RUN_DEFINITIONS if schema_version in {6, SCHEMA_VERSION} else _MODEL_RUN_DEFINITIONS)
        if schema_version in {CONFORMANCE_SCHEMA_VERSION, RETRIEVAL_SCHEMA_VERSION, 6, SCHEMA_VERSION}:
            definitions["retrieval_conformance"] = dict(_CONFORMANCE_DEFINITIONS)
        if schema_version in {RETRIEVAL_SCHEMA_VERSION, 6, SCHEMA_VERSION}:
            definitions["retrieval_executions"] = dict(_EXECUTION_DEFINITIONS)
    return definitions


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
    connection.execute("BEGIN IMMEDIATE")
    try:
        connection.execute("CREATE TABLE conversations (conversation_id TEXT PRIMARY KEY, created_at TEXT NOT NULL)")
        connection.execute(
            """
            CREATE TABLE messages (
                message_id INTEGER PRIMARY KEY,
                conversation_id TEXT NOT NULL,
                ordinal INTEGER NOT NULL,
                role TEXT NOT NULL CHECK (role IN ('user', 'synthesis')),
                content TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (conversation_id) REFERENCES conversations(conversation_id),
                UNIQUE (conversation_id, ordinal)
            )
            """
        )
        _create_model_runs_table(connection)
        _create_retrieval_conformance_table(connection)
        _create_retrieval_executions_table(connection)
        connection.execute("PRAGMA user_version = 7")
        _validate_schema(connection, SCHEMA_VERSION)
        connection.commit()
    except Exception:
        connection.rollback()
        raise


def _create_model_runs_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE model_runs (
            run_id TEXT PRIMARY KEY,
            conversation_id TEXT NOT NULL,
            trigger_message_id INTEGER NOT NULL,
            run_kind TEXT NOT NULL CHECK (run_kind IN ('router', 'retrieval_inference', 'synthesis')),
            parent_run_id TEXT,
            capability_catalog_sha256 TEXT,
            provider TEXT NOT NULL,
            model TEXT NOT NULL,
            prompt_version TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('running', 'succeeded', 'failed')),
            started_at TEXT NOT NULL,
            completed_at TEXT,
            provider_response_id TEXT,
            output_json TEXT,
            input_json TEXT,
            input_sha256 TEXT,
            output_text TEXT,
            produced_message_id INTEGER,
            error_type TEXT,
            error_message TEXT,
            input_tokens INTEGER,
            cached_input_tokens INTEGER,
            output_tokens INTEGER,
            reasoning_tokens INTEGER,
            total_tokens INTEGER,
            CHECK (
                (run_kind = 'router' AND parent_run_id IS NULL AND capability_catalog_sha256 IS NULL)
                OR
                (run_kind = 'retrieval_inference' AND parent_run_id IS NOT NULL AND capability_catalog_sha256 IS NOT NULL)
                OR
                (run_kind = 'synthesis' AND parent_run_id IS NOT NULL AND capability_catalog_sha256 IS NULL)
            ),
            FOREIGN KEY (conversation_id) REFERENCES conversations(conversation_id),
            FOREIGN KEY (trigger_message_id) REFERENCES messages(message_id),
            FOREIGN KEY (parent_run_id) REFERENCES model_runs(run_id),
            FOREIGN KEY (produced_message_id) REFERENCES messages(message_id)
        )
        """
    )


def _create_retrieval_conformance_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE retrieval_conformance (
            conformance_id TEXT PRIMARY KEY,
            retrieval_run_id TEXT NOT NULL UNIQUE,
            retrieval_proposal_sha256 TEXT NOT NULL,
            capability_catalog_sha256 TEXT NOT NULL,
            contract_version TEXT NOT NULL CHECK (contract_version IN ('catalog-conformance-v1', 'catalog-conformance-v2')),
            status TEXT NOT NULL CHECK (
                (contract_version = 'catalog-conformance-v1' AND status IN ('valid', 'invalid'))
                OR (contract_version = 'catalog-conformance-v2' AND status IN ('valid', 'partial', 'invalid'))
            ),
            checked_at TEXT NOT NULL,
            result_json TEXT NOT NULL,
            FOREIGN KEY (retrieval_run_id) REFERENCES model_runs(run_id)
        )
        """
    )


def _create_retrieval_executions_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE retrieval_executions (
            execution_id TEXT PRIMARY KEY,
            conformance_id TEXT NOT NULL UNIQUE,
            retrieval_run_id TEXT NOT NULL,
            retrieval_proposal_sha256 TEXT NOT NULL,
            capability_catalog_sha256 TEXT NOT NULL,
            retrieval_package_id TEXT NOT NULL,
            retrieval_package_identity_version TEXT NOT NULL,
            substrate_sha256 TEXT NOT NULL,
            vectors_sha256 TEXT NOT NULL,
            package_verification_contract_version TEXT NOT NULL,
            execution_contract_version TEXT NOT NULL CHECK (execution_contract_version IN ('retrieval-execution-v1', 'retrieval-execution-v2')),
            status TEXT NOT NULL CHECK (
                (execution_contract_version = 'retrieval-execution-v1' AND status IN ('running', 'succeeded', 'failed'))
                OR (execution_contract_version = 'retrieval-execution-v2' AND status IN ('running', 'succeeded', 'partial', 'failed'))
            ),
            started_at TEXT NOT NULL,
            completed_at TEXT,
            result_json TEXT NOT NULL,
            CHECK (
                (status = 'running' AND completed_at IS NULL)
                OR
                (status IN ('succeeded', 'partial', 'failed') AND completed_at IS NOT NULL)
            ),
            FOREIGN KEY (conformance_id) REFERENCES retrieval_conformance(conformance_id),
            FOREIGN KEY (retrieval_run_id) REFERENCES model_runs(run_id)
        )
        """
    )


def _table_info(connection: sqlite3.Connection, table: str) -> tuple[sqlite3.Row, ...]:
    return tuple(connection.execute(f"PRAGMA table_info({table})"))


def _validate_schema(connection: sqlite3.Connection, schema_version: int) -> None:
    expected_tables = set(_definitions(schema_version))
    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    if tables != expected_tables:
        raise RuntimeConversationError(f"runtime database schema-v{schema_version} tables are incompatible")
    for table, expected_definitions in _definitions(schema_version).items():
        expected_columns = (
            _CONVERSATION_COLUMNS if table == "conversations"
            else _MESSAGE_COLUMNS if table == "messages"
            else _CONFORMANCE_COLUMNS if table == "retrieval_conformance"
            else _EXECUTION_COLUMNS if table == "retrieval_executions"
            else _V2_MODEL_RUN_COLUMNS if schema_version == ROUTER_SCHEMA_VERSION
            else _V6_MODEL_RUN_COLUMNS if schema_version in {6, SCHEMA_VERSION}
            else _V3_MODEL_RUN_COLUMNS
        )
        info = _table_info(connection, table)
        if tuple(row[1] for row in info) != expected_columns:
            raise RuntimeConversationError(f"runtime {table} schema is incompatible")
        for row in info:
            expected_type, expected_not_null, expected_default, expected_primary_key = expected_definitions[row[1]]
            if (row[2].upper(), row[3], row[4], row[5]) != (expected_type, expected_not_null, expected_default, expected_primary_key):
                raise RuntimeConversationError(f"runtime {table}.{row[1]} definition is incompatible")

    message_foreign_keys = {(row[2], row[3], row[4]) for row in connection.execute("PRAGMA foreign_key_list(messages)")}
    if ("conversations", "conversation_id", "conversation_id") not in message_foreign_keys:
        raise RuntimeConversationError("runtime message foreign key is incompatible")
    unique_pairs = set()
    for index in connection.execute("PRAGMA index_list(messages)"):
        if index[2]:
            unique_pairs.add(tuple(row[2] for row in connection.execute(f"PRAGMA index_info([{index[1]}])")))
    if ("conversation_id", "ordinal") not in unique_pairs:
        raise RuntimeConversationError("runtime message ordering constraint is incompatible")
    messages_sql = connection.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'messages'").fetchone()[0]
    if not re.search(r"CHECK\s*\(\s*role\s+IN\s*\(\s*'user'\s*,\s*'synthesis'\s*\)\s*\)", messages_sql, re.IGNORECASE):
        raise RuntimeConversationError("runtime message role constraint is incompatible")

    if schema_version in {ROUTER_SCHEMA_VERSION, MODEL_RUN_SCHEMA_VERSION, CONFORMANCE_SCHEMA_VERSION, RETRIEVAL_SCHEMA_VERSION, 6, SCHEMA_VERSION}:
        model_runs_sql = connection.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'model_runs'").fetchone()[0]
        run_kind = r"'router'\s*\)" if schema_version == ROUTER_SCHEMA_VERSION else r"'router'\s*,\s*'retrieval_inference'\s*\)" if schema_version not in {6, SCHEMA_VERSION} else r"'router'\s*,\s*'retrieval_inference'\s*,\s*'synthesis'\s*\)"
        if not re.search(rf"CHECK\s*\(\s*run_kind\s+IN\s*\(\s*{run_kind}", model_runs_sql, re.IGNORECASE):
            raise RuntimeConversationError("runtime model-run kind constraint is incompatible")
        if not re.search(r"CHECK\s*\(\s*status\s+IN\s*\(\s*'running'\s*,\s*'succeeded'\s*,\s*'failed'\s*\)\s*\)", model_runs_sql, re.IGNORECASE):
            raise RuntimeConversationError("runtime model-run status constraint is incompatible")
        if schema_version in {MODEL_RUN_SCHEMA_VERSION, CONFORMANCE_SCHEMA_VERSION, RETRIEVAL_SCHEMA_VERSION, SCHEMA_VERSION} and not (
            re.search(
                r"run_kind\s*=\s*'router'\s+AND\s+parent_run_id\s+IS\s+NULL\s+AND\s+capability_catalog_sha256\s+IS\s+NULL",
                model_runs_sql,
                re.IGNORECASE,
            )
            and re.search(
                r"run_kind\s*=\s*'retrieval_inference'\s+AND\s+parent_run_id\s+IS\s+NOT\s+NULL\s+AND\s+capability_catalog_sha256\s+IS\s+NOT\s+NULL",
                model_runs_sql,
                re.IGNORECASE,
            )
            and (
                schema_version not in {6, SCHEMA_VERSION}
                or re.search(r"run_kind\s*=\s*'synthesis'\s+AND\s+parent_run_id\s+IS\s+NOT\s+NULL\s+AND\s+capability_catalog_sha256\s+IS\s+NULL", model_runs_sql, re.IGNORECASE)
            )
        ):
            raise RuntimeConversationError("runtime model-run lineage constraint is incompatible")
        model_run_foreign_keys = {(row[2], row[3], row[4]) for row in connection.execute("PRAGMA foreign_key_list(model_runs)")}
        required = {
            ("conversations", "conversation_id", "conversation_id"),
            ("messages", "trigger_message_id", "message_id"),
        }
        if schema_version in {MODEL_RUN_SCHEMA_VERSION, CONFORMANCE_SCHEMA_VERSION, RETRIEVAL_SCHEMA_VERSION, SCHEMA_VERSION}:
            required.add(("model_runs", "parent_run_id", "run_id"))
        if schema_version in {6, SCHEMA_VERSION}:
            required.add(("messages", "produced_message_id", "message_id"))
        if required - model_run_foreign_keys:
            raise RuntimeConversationError("runtime model-run foreign keys are incompatible")

    if schema_version in {CONFORMANCE_SCHEMA_VERSION, RETRIEVAL_SCHEMA_VERSION, 6, SCHEMA_VERSION}:
        conformance_sql = connection.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'retrieval_conformance'").fetchone()[0]
        contract_pattern = r"CHECK\s*\(\s*contract_version\s*=\s*'catalog-conformance-v1'\s*\)" if schema_version != SCHEMA_VERSION else r"CHECK\s*\(\s*contract_version\s+IN\s*\(\s*'catalog-conformance-v1'\s*,\s*'catalog-conformance-v2'\s*\)\s*\)"
        if not re.search(contract_pattern, conformance_sql, re.IGNORECASE):
            raise RuntimeConversationError("runtime conformance contract constraint is incompatible")
        status_pattern = r"CHECK\s*\(\s*status\s+IN\s*\(\s*'valid'\s*,\s*'invalid'\s*\)\s*\)" if schema_version != SCHEMA_VERSION else r"CHECK\s*\(.*contract_version\s*=\s*'catalog-conformance-v1'.*status\s+IN\s*\(\s*'valid'\s*,\s*'invalid'\s*\).*contract_version\s*=\s*'catalog-conformance-v2'.*status\s+IN\s*\(\s*'valid'\s*,\s*'partial'\s*,\s*'invalid'\s*\).*\)"
        if not re.search(status_pattern, conformance_sql, re.IGNORECASE | re.DOTALL):
            raise RuntimeConversationError("runtime conformance status constraint is incompatible")
        conformance_foreign_keys = {(row[2], row[3], row[4]) for row in connection.execute("PRAGMA foreign_key_list(retrieval_conformance)")}
        if ("model_runs", "retrieval_run_id", "run_id") not in conformance_foreign_keys:
            raise RuntimeConversationError("runtime conformance foreign key is incompatible")
        unique_pairs = set()
        for index in connection.execute("PRAGMA index_list(retrieval_conformance)"):
            if index[2]:
                unique_pairs.add(tuple(row[2] for row in connection.execute(f"PRAGMA index_info([{index[1]}])")))
        if ("retrieval_run_id",) not in unique_pairs:
            raise RuntimeConversationError("runtime conformance uniqueness constraint is incompatible")

    if schema_version in {RETRIEVAL_SCHEMA_VERSION, 6, SCHEMA_VERSION}:
        execution_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'retrieval_executions'"
        ).fetchone()[0]
        if not re.search(
            r"CHECK\s*\(.*execution_contract_version\s*=\s*'retrieval-execution-v1'.*status\s+IN\s*\(\s*'running'\s*,\s*'succeeded'\s*,\s*'failed'\s*\).*execution_contract_version\s*=\s*'retrieval-execution-v2'.*status\s+IN\s*\(\s*'running'\s*,\s*'succeeded'\s*,\s*'partial'\s*,\s*'failed'\s*\).*\)" if schema_version == SCHEMA_VERSION else r"CHECK\s*\(\s*status\s+IN\s*\(\s*'running'\s*,\s*'succeeded'\s*,\s*'failed'\s*\)\s*\)",
            execution_sql,
            re.IGNORECASE | re.DOTALL,
        ):
            raise RuntimeConversationError("runtime execution status constraint is incompatible")
        if not re.search(
            r"CHECK\s*\(.*status\s*=\s*'running'.*completed_at\s+IS\s+NULL.*status\s+IN\s*\(\s*'succeeded'\s*,\s*'partial'\s*,\s*'failed'\s*\).*completed_at\s+IS\s+NOT\s+NULL.*\)" if schema_version == SCHEMA_VERSION else r"CHECK\s*\(.*status\s*=\s*'running'.*completed_at\s+IS\s+NULL.*status\s+IN\s*\(\s*'succeeded'\s*,\s*'failed'\s*\).*completed_at\s+IS\s+NOT\s+NULL.*\)",
            execution_sql,
            re.IGNORECASE | re.DOTALL,
        ):
            raise RuntimeConversationError("runtime execution timestamp constraint is incompatible")
        execution_foreign_keys = {(row[2], row[3], row[4]) for row in connection.execute("PRAGMA foreign_key_list(retrieval_executions)")}
        required = {
            ("retrieval_conformance", "conformance_id", "conformance_id"),
            ("model_runs", "retrieval_run_id", "run_id"),
        }
        if required - execution_foreign_keys:
            raise RuntimeConversationError("runtime execution foreign keys are incompatible")
        unique_pairs = set()
        for index in connection.execute("PRAGMA index_list(retrieval_executions)"):
            if index[2]:
                unique_pairs.add(tuple(row[2] for row in connection.execute(f"PRAGMA index_info([{index[1]}])")))
        if ("conformance_id",) not in unique_pairs:
            raise RuntimeConversationError("runtime execution conformance uniqueness constraint is incompatible")


def _open_connection(database_path: str | Path, *, allow_create: bool) -> sqlite3.Connection:
    path = _path(database_path)
    if not allow_create and not path.is_file():
        raise RuntimeConversationError(f"runtime database does not exist: {path}")
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(str(path)) if allow_create else sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=rw", uri=True)
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
        return
    if version == LEGACY_SCHEMA_VERSION:
        _validate_schema(connection, LEGACY_SCHEMA_VERSION)
        raise RuntimeConversationError("runtime schema v1 is older and requires explicit migration")
    if version == ROUTER_SCHEMA_VERSION:
        _validate_schema(connection, ROUTER_SCHEMA_VERSION)
        raise RuntimeConversationError("runtime schema v2 is older and requires explicit migration")
    if version == MODEL_RUN_SCHEMA_VERSION:
        _validate_schema(connection, MODEL_RUN_SCHEMA_VERSION)
        raise RuntimeConversationError("runtime schema v3 is older and requires explicit migration")
    if version == CONFORMANCE_SCHEMA_VERSION:
        _validate_schema(connection, CONFORMANCE_SCHEMA_VERSION)
        raise RuntimeConversationError("runtime schema v4 is older and requires explicit migration")
    if version == RETRIEVAL_SCHEMA_VERSION:
        _validate_schema(connection, RETRIEVAL_SCHEMA_VERSION)
        raise RuntimeConversationError("runtime schema v5 is older and requires explicit migration")
    if version != SCHEMA_VERSION:
        raise RuntimeConversationError(f"unsupported runtime schema version: {version}")
    _validate_schema(connection, SCHEMA_VERSION)


def _connect_runtime(database_path: str | Path) -> sqlite3.Connection:
    connection = _open_connection(database_path, allow_create=False)
    try:
        _runtime_schema_state(connection, initialize_empty=False)
        return connection
    except Exception:
        connection.close()
        raise


def initialize_runtime(database_path: str | Path) -> None:
    """Create or validate the current runtime schema at the explicit path."""
    connection = _open_connection(database_path, allow_create=True)
    try:
        _runtime_schema_state(connection, initialize_empty=True)
    except Exception:
        connection.close()
        raise
    connection.close()


def _migrate_v2_to_v3(connection: sqlite3.Connection) -> None:
    rows = connection.execute("SELECT run_id, prompt_version FROM model_runs").fetchall()
    if any(row[1] != HISTORICAL_ROUTER_V1_PROMPT_VERSION for row in rows):
        raise RuntimeConversationError("runtime v2 contains an unknown historical router prompt identity")
    connection.execute("ALTER TABLE model_runs RENAME TO model_runs_v2_legacy")
    _create_model_runs_table(connection)
    connection.execute(
        """
        INSERT INTO model_runs (
            run_id, conversation_id, trigger_message_id, run_kind, parent_run_id,
            capability_catalog_sha256, provider, model, prompt_version, status, started_at,
            completed_at, provider_response_id, output_json, error_type, error_message,
            input_tokens, cached_input_tokens, output_tokens, reasoning_tokens, total_tokens
        )
        SELECT run_id, conversation_id, trigger_message_id, run_kind, NULL, NULL, provider,
               model, ?, status, started_at, completed_at, provider_response_id, output_json,
               error_type, error_message, input_tokens, cached_input_tokens, output_tokens,
               reasoning_tokens, total_tokens
        FROM model_runs_v2_legacy
        """,
        (HISTORICAL_ROUTER_V1_PROMPT_SHA256,),
    )
    connection.execute("DROP TABLE model_runs_v2_legacy")


def _migrate_v5_to_v6(connection: sqlite3.Connection) -> None:
    # Keep legacy foreign-key declarations pointed at the stable table name
    # while the model-run columns are rebuilt.
    connection.execute("PRAGMA legacy_alter_table = ON")
    has_conformance = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'retrieval_conformance'"
    ).fetchone() is not None
    has_executions = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'retrieval_executions'"
    ).fetchone() is not None
    if has_executions:
        connection.execute("ALTER TABLE retrieval_executions RENAME TO retrieval_executions_v5_legacy")
    if has_conformance:
        connection.execute("ALTER TABLE retrieval_conformance RENAME TO retrieval_conformance_v5_legacy")
    connection.execute("ALTER TABLE model_runs RENAME TO model_runs_v5_legacy")
    _create_model_runs_table(connection)
    connection.execute(
        """
        INSERT INTO model_runs (
            run_id, conversation_id, trigger_message_id, run_kind, parent_run_id,
            capability_catalog_sha256, provider, model, prompt_version, status, started_at,
            completed_at, provider_response_id, output_json, error_type, error_message,
            input_tokens, cached_input_tokens, output_tokens, reasoning_tokens, total_tokens
        )
        SELECT run_id, conversation_id, trigger_message_id, run_kind, parent_run_id,
               capability_catalog_sha256, provider, model, prompt_version, status, started_at,
               completed_at, provider_response_id, output_json, error_type, error_message,
               input_tokens, cached_input_tokens, output_tokens, reasoning_tokens, total_tokens
        FROM model_runs_v5_legacy
        """
    )
    connection.execute("DROP TABLE model_runs_v5_legacy")
    if has_conformance:
        _create_retrieval_conformance_table(connection)
        connection.execute(
            """
            INSERT INTO retrieval_conformance
            SELECT conformance_id, retrieval_run_id, retrieval_proposal_sha256,
                   capability_catalog_sha256, contract_version, status, checked_at, result_json
            FROM retrieval_conformance_v5_legacy
            """
        )
        connection.execute("DROP TABLE retrieval_conformance_v5_legacy")
    if has_executions:
        _create_retrieval_executions_table(connection)
        connection.execute(
            """
            INSERT INTO retrieval_executions
            SELECT execution_id, conformance_id, retrieval_run_id,
                   retrieval_proposal_sha256, capability_catalog_sha256,
                   retrieval_package_id, retrieval_package_identity_version,
                   substrate_sha256, vectors_sha256, package_verification_contract_version,
                   execution_contract_version, status, started_at, completed_at, result_json
            FROM retrieval_executions_v5_legacy
            """
        )
        connection.execute("DROP TABLE retrieval_executions_v5_legacy")
    connection.execute("PRAGMA legacy_alter_table = OFF")


def _migrate_v6_to_v7(connection: sqlite3.Connection) -> None:
    connection.execute("PRAGMA legacy_alter_table = ON")
    connection.execute("ALTER TABLE retrieval_executions RENAME TO retrieval_executions_v6_legacy")
    connection.execute("ALTER TABLE retrieval_conformance RENAME TO retrieval_conformance_v6_legacy")
    _create_retrieval_conformance_table(connection)
    _create_retrieval_executions_table(connection)
    connection.execute("INSERT INTO retrieval_conformance SELECT * FROM retrieval_conformance_v6_legacy")
    connection.execute("INSERT INTO retrieval_executions SELECT * FROM retrieval_executions_v6_legacy")
    connection.execute("DROP TABLE retrieval_executions_v6_legacy")
    connection.execute("DROP TABLE retrieval_conformance_v6_legacy")
    connection.execute("PRAGMA legacy_alter_table = OFF")


def migrate_runtime(database_path: str | Path) -> None:
    """Explicitly migrate the supported legacy schemas to schema v7."""
    connection = _open_connection(database_path, allow_create=False)
    transaction_started = False
    try:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version == LEGACY_SCHEMA_VERSION:
            _validate_schema(connection, LEGACY_SCHEMA_VERSION)
            connection.execute("BEGIN IMMEDIATE")
            transaction_started = True
            _create_model_runs_table(connection)
            _create_retrieval_conformance_table(connection)
            _create_retrieval_executions_table(connection)
            connection.execute("PRAGMA user_version = 7")
            _validate_schema(connection, SCHEMA_VERSION)
            connection.commit()
            transaction_started = False
            return
        if version == ROUTER_SCHEMA_VERSION:
            _validate_schema(connection, ROUTER_SCHEMA_VERSION)
            connection.execute("BEGIN IMMEDIATE")
            transaction_started = True
            _migrate_v2_to_v3(connection)
            _create_retrieval_conformance_table(connection)
            _create_retrieval_executions_table(connection)
            connection.execute("PRAGMA user_version = 7")
            _validate_schema(connection, SCHEMA_VERSION)
            connection.commit()
            transaction_started = False
            return
        if version == MODEL_RUN_SCHEMA_VERSION:
            _validate_schema(connection, MODEL_RUN_SCHEMA_VERSION)
            connection.execute("PRAGMA legacy_alter_table = ON")
            connection.execute("BEGIN IMMEDIATE")
            transaction_started = True
            _migrate_v5_to_v6(connection)
            _create_retrieval_conformance_table(connection)
            _create_retrieval_executions_table(connection)
            connection.execute("PRAGMA user_version = 7")
            _validate_schema(connection, SCHEMA_VERSION)
            connection.commit()
            connection.execute("PRAGMA legacy_alter_table = OFF")
            transaction_started = False
            return
        if version == CONFORMANCE_SCHEMA_VERSION:
            _validate_schema(connection, CONFORMANCE_SCHEMA_VERSION)
            connection.execute("PRAGMA legacy_alter_table = ON")
            connection.execute("BEGIN IMMEDIATE")
            transaction_started = True
            _migrate_v5_to_v6(connection)
            _create_retrieval_executions_table(connection)
            connection.execute("PRAGMA user_version = 7")
            _validate_schema(connection, SCHEMA_VERSION)
            connection.commit()
            connection.execute("PRAGMA legacy_alter_table = OFF")
            transaction_started = False
            return
        if version == RETRIEVAL_SCHEMA_VERSION:
            _validate_schema(connection, RETRIEVAL_SCHEMA_VERSION)
            connection.execute("PRAGMA legacy_alter_table = ON")
            connection.execute("BEGIN IMMEDIATE")
            transaction_started = True
            _migrate_v5_to_v6(connection)
            connection.execute("PRAGMA user_version = 7")
            _validate_schema(connection, SCHEMA_VERSION)
            connection.commit()
            connection.execute("PRAGMA legacy_alter_table = OFF")
            transaction_started = False
            return
        if version == 6:
            _validate_schema(connection, 6)
            connection.execute("BEGIN IMMEDIATE")
            transaction_started = True
            _migrate_v6_to_v7(connection)
            connection.execute("PRAGMA user_version = 7")
            _validate_schema(connection, SCHEMA_VERSION)
            connection.commit()
            transaction_started = False
            return
        if version == SCHEMA_VERSION:
            _validate_schema(connection, SCHEMA_VERSION)
            return
        raise RuntimeConversationError(f"unsupported runtime schema version: {version}")
    except RuntimeConversationError:
        if transaction_started:
            connection.rollback()
        raise
    except sqlite3.Error as exc:
        if transaction_started:
            connection.rollback()
        raise RuntimeConversationError(f"could not migrate runtime database: {exc}") from exc
    except Exception:
        if transaction_started:
            connection.rollback()
        raise
    finally:
        connection.close()


def create_conversation(database_path: str | Path, *, clock: Clock | None = None) -> Conversation:
    conversation_id = str(uuid4())
    created_at = _timestamp(clock)
    connection = _connect_runtime(database_path)
    try:
        connection.execute("BEGIN")
        connection.execute("INSERT INTO conversations (conversation_id, created_at) VALUES (?, ?)", (conversation_id, created_at))
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


def append_message(database_path: str | Path, conversation_id: str, role: str, content: str, *, clock: Clock | None = None) -> Message:
    _validate_message(role, content)
    created_at = _timestamp(clock)
    connection = _connect_runtime(database_path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        if connection.execute("SELECT 1 FROM conversations WHERE conversation_id = ?", (conversation_id,)).fetchone() is None:
            raise RuntimeConversationError(f"conversation does not exist: {conversation_id}")
        ordinal = connection.execute("SELECT COALESCE(MAX(ordinal), -1) + 1 FROM messages WHERE conversation_id = ?", (conversation_id,)).fetchone()[0]
        cursor = connection.execute(
            "INSERT INTO messages (conversation_id, ordinal, role, content, created_at) VALUES (?, ?, ?, ?, ?)",
            (conversation_id, ordinal, role, content, created_at),
        )
        connection.commit()
        return Message(cursor.lastrowid, conversation_id, ordinal, role, content, created_at)
    except RuntimeConversationError:
        connection.rollback()
        raise
    except sqlite3.Error as exc:
        connection.rollback()
        raise RuntimeConversationError(f"could not append conversation message: {exc}") from exc
    finally:
        connection.close()


def get_conversation(database_path: str | Path, conversation_id: str) -> Conversation:
    connection = _connect_runtime(database_path)
    try:
        row = connection.execute("SELECT conversation_id, created_at FROM conversations WHERE conversation_id = ?", (conversation_id,)).fetchone()
        if row is None:
            raise RuntimeConversationError(f"conversation does not exist: {conversation_id}")
        messages = []
        for message in connection.execute(
            "SELECT message_id, conversation_id, ordinal, role, content, created_at FROM messages WHERE conversation_id = ? ORDER BY ordinal ASC",
            (conversation_id,),
        ):
            if message["role"] not in ALLOWED_ROLES:
                raise RuntimeConversationError(f"malformed persisted conversation role: {message['role']!r}")
            messages.append(Message(message["message_id"], message["conversation_id"], message["ordinal"], message["role"], message["content"], message["created_at"]))
        return Conversation(row["conversation_id"], row["created_at"], tuple(messages))
    except RuntimeConversationError:
        raise
    except sqlite3.Error as exc:
        raise RuntimeConversationError(f"could not read conversation: {exc}") from exc
    finally:
        connection.close()


__all__ = [
    "Conversation", "Message", "RuntimeConversationError",
    "append_message", "create_conversation", "get_conversation", "initialize_runtime", "migrate_runtime",
]
