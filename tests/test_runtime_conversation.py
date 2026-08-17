import contextlib
import io
import inspect
import json
import sqlite3
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import UUID

from semantic_traversal.cli import main
from semantic_traversal.runtime.conversation import (
    RuntimeConversationError,
    _connect_runtime,
    append_message,
    create_conversation,
    get_conversation,
    initialize_runtime,
    migrate_runtime,
)


class RuntimeConversationTests(unittest.TestCase):
    def _clock(self, seconds: int):
        return lambda: datetime(2026, 1, 1, 0, 0, seconds, tzinfo=timezone.utc)

    def _run_cli(self, *argv):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = main(list(argv))
        return code, stdout.getvalue(), stderr.getvalue()

    def _write_schema(
        self,
        database: Path,
        *,
        conversation_created_at="TEXT NOT NULL",
        message_id="INTEGER PRIMARY KEY",
        message_conversation_id="TEXT NOT NULL",
        message_ordinal="INTEGER NOT NULL",
        message_role="TEXT NOT NULL CHECK (role IN ('user', 'synthesis'))",
        message_content="TEXT NOT NULL",
        message_created_at="TEXT NOT NULL",
        foreign_key=True,
        unique_order=True,
    ):
        foreign_key_sql = ", FOREIGN KEY (conversation_id) REFERENCES conversations(conversation_id)" if foreign_key else ""
        unique_sql = ", UNIQUE (conversation_id, ordinal)" if unique_order else ""
        connection = sqlite3.connect(database)
        connection.executescript(
            f"""
            CREATE TABLE conversations (
                conversation_id TEXT PRIMARY KEY,
                created_at {conversation_created_at}
            );
            CREATE TABLE messages (
                message_id {message_id},
                conversation_id {message_conversation_id},
                ordinal {message_ordinal},
                role {message_role},
                content {message_content},
                created_at {message_created_at}
                {foreign_key_sql}
                {unique_sql}
            );
            PRAGMA user_version = 1;
            """
        )
        connection.close()

    def test_new_database_schema_is_v2_foreign_keyed_and_idempotent(self):
        with TemporaryDirectory() as directory:
            database = Path(directory) / "runtime.sqlite3"
            initialize_runtime(database)
            connection = sqlite3.connect(database)
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 6)
            self.assertEqual(connection.execute("PRAGMA foreign_keys").fetchone()[0], 0)
            self.assertEqual(
                {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")},
                {"conversations", "messages", "model_runs", "retrieval_conformance", "retrieval_executions"},
            )
            self.assertEqual(
                tuple(row[1] for row in connection.execute("PRAGMA table_info(messages)")),
                ("message_id", "conversation_id", "ordinal", "role", "content", "created_at"),
            )
            self.assertEqual(len(tuple(connection.execute("PRAGMA foreign_key_list(messages)"))), 1)
            self.assertIn("CHECK", connection.execute("SELECT sql FROM sqlite_master WHERE name='messages'").fetchone()[0])
            self.assertEqual(
                tuple(row[1] for row in connection.execute("PRAGMA table_info(model_runs)")),
                (
                    "run_id", "conversation_id", "trigger_message_id", "run_kind", "parent_run_id",
                    "capability_catalog_sha256", "provider", "model", "prompt_version", "status", "started_at",
                    "completed_at", "provider_response_id",
                    "output_json", "input_json", "input_sha256", "output_text", "produced_message_id",
                    "error_type", "error_message",
                    "input_tokens", "cached_input_tokens",
                    "output_tokens", "reasoning_tokens", "total_tokens",
                ),
            )
            connection.close()

            initialize_runtime(database)
            conversation = create_conversation(database)
            initialize_runtime(database)
            self.assertEqual(get_conversation(database, conversation.conversation_id), conversation)

            runtime_connection = _connect_runtime(database)
            self.assertEqual(runtime_connection.execute("PRAGMA foreign_keys").fetchone()[0], 1)
            runtime_connection.close()

    def test_incompatible_schema_version_fails_closed(self):
        with TemporaryDirectory() as directory:
            database = Path(directory) / "runtime.sqlite3"
            connection = sqlite3.connect(database)
            connection.execute("PRAGMA user_version = 2")
            connection.commit()
            connection.close()
            with self.assertRaises(RuntimeConversationError):
                initialize_runtime(database)

    def test_v1_requires_explicit_migration_and_preserves_thread_state(self):
        with TemporaryDirectory() as directory:
            database = Path(directory) / "runtime.sqlite3"
            self._write_schema(database)
            connection = sqlite3.connect(database)
            connection.execute(
                "INSERT INTO conversations(conversation_id, created_at) VALUES (?, ?)",
                ("legacy-conversation", "2026-01-01T00:00:00.000000Z"),
            )
            connection.executemany(
                "INSERT INTO messages(conversation_id, ordinal, role, content, created_at) VALUES (?, ?, ?, ?, ?)",
                (
                    ("legacy-conversation", 0, "user", "hello  ", "2026-01-01T00:00:01.000000Z"),
                    ("legacy-conversation", 1, "synthesis", "hi\nthere", "2026-01-01T00:00:02.000000Z"),
                ),
            )
            connection.commit()
            before_conversations = tuple(connection.execute("SELECT * FROM conversations ORDER BY conversation_id"))
            before_messages = tuple(connection.execute("SELECT * FROM messages ORDER BY conversation_id, ordinal"))
            connection.close()

            with self.assertRaisesRegex(RuntimeConversationError, "requires explicit migration"):
                get_conversation(database, "legacy-conversation")
            with self.assertRaises(RuntimeConversationError):
                initialize_runtime(database)
            migrate_runtime(database)
            restored = get_conversation(database, "legacy-conversation")
            connection = sqlite3.connect(database)
            self.assertEqual(tuple(connection.execute("SELECT * FROM conversations ORDER BY conversation_id")), before_conversations)
            self.assertEqual(tuple(connection.execute("SELECT * FROM messages ORDER BY conversation_id, ordinal")), before_messages)
            connection.close()

            connection = sqlite3.connect(database)
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 6)
            self.assertEqual(
                {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")},
                {"conversations", "messages", "model_runs", "retrieval_conformance", "retrieval_executions"},
            )
            connection.close()
            migrate_runtime(database)

    def test_migration_rejects_incompatible_v1_without_partial_upgrade(self):
        with TemporaryDirectory() as directory:
            database = Path(directory) / "runtime.sqlite3"
            self._write_schema(database, message_content="TEXT")
            with self.assertRaises(RuntimeConversationError):
                migrate_runtime(database)
            connection = sqlite3.connect(database)
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT name FROM sqlite_master WHERE name='model_runs'").fetchone(), None)
            connection.close()

    def test_injected_candidate_v2_validation_failure_rolls_back_to_v1(self):
        with TemporaryDirectory() as directory:
            database = Path(directory) / "runtime.sqlite3"
            self._write_schema(database)
            connection = sqlite3.connect(database)
            connection.execute("INSERT INTO conversations VALUES (?, ?)", ("legacy", "created"))
            connection.execute("INSERT INTO messages(conversation_id, ordinal, role, content, created_at) VALUES (?, ?, ?, ?, ?)", ("legacy", 0, "user", "content", "created"))
            connection.commit()
            before_conversations = tuple(connection.execute("SELECT * FROM conversations"))
            before_messages = tuple(connection.execute("SELECT * FROM messages"))
            connection.close()
            with patch(
                "semantic_traversal.runtime.conversation._validate_schema",
                side_effect=[None, RuntimeConversationError("injected candidate-v2 validation failure")],
            ), self.assertRaisesRegex(RuntimeConversationError, "injected candidate-v2"):
                migrate_runtime(database)
            connection = sqlite3.connect(database)
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 1)
            self.assertEqual(tuple(connection.execute("SELECT * FROM conversations")), before_conversations)
            self.assertEqual(tuple(connection.execute("SELECT * FROM messages")), before_messages)
            self.assertIsNone(connection.execute("SELECT name FROM sqlite_master WHERE name='model_runs'").fetchone())
            connection.close()

    def test_injected_v2_construction_failure_rolls_back_new_initialization(self):
        with TemporaryDirectory() as directory:
            database = Path(directory) / "runtime.sqlite3"
            with patch(
                "semantic_traversal.runtime.conversation._create_model_runs_table",
                side_effect=sqlite3.OperationalError("injected schema-construction failure"),
            ), self.assertRaisesRegex(sqlite3.OperationalError, "injected schema-construction"):
                initialize_runtime(database)
            self.assertTrue(database.exists())
            connection = sqlite3.connect(database)
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 0)
            self.assertEqual(tuple(connection.execute("SELECT name FROM sqlite_master WHERE type='table'")), ())
            connection.close()

    def test_non_init_operations_never_create_missing_or_empty_databases(self):
        for operation in ("create", "append", "get"):
            with self.subTest(operation=operation), TemporaryDirectory() as directory:
                database = Path(directory) / "runtime.sqlite3"
                with self.assertRaises(RuntimeConversationError):
                    if operation == "create":
                        create_conversation(database)
                    elif operation == "append":
                        append_message(database, "missing", "user", "content")
                    else:
                        get_conversation(database, "missing")
                self.assertFalse(database.exists())

                sqlite3.connect(database).close()
                with self.assertRaises(RuntimeConversationError):
                    if operation == "create":
                        create_conversation(database)
                    elif operation == "append":
                        append_message(database, "missing", "user", "content")
                    else:
                        get_conversation(database, "missing")
                connection = sqlite3.connect(database)
                self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 0)
                self.assertEqual(
                    {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")},
                    set(),
                )
                connection.close()
                initialize_runtime(database)
                connection = sqlite3.connect(database)
                self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 6)
                connection.close()

    def test_schema_v3_requires_lineage_check(self):
        with TemporaryDirectory() as directory:
            database = Path(directory) / "runtime.sqlite3"
            connection = sqlite3.connect(database)
            connection.executescript(
                """
                CREATE TABLE conversations (conversation_id TEXT PRIMARY KEY, created_at TEXT NOT NULL);
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
                CREATE TABLE model_runs (
                    run_id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL,
                    trigger_message_id INTEGER NOT NULL,
                    run_kind TEXT NOT NULL CHECK (run_kind IN ('router', 'retrieval_inference')),
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
                    error_type TEXT,
                    error_message TEXT,
                    input_tokens INTEGER,
                    cached_input_tokens INTEGER,
                    output_tokens INTEGER,
                    reasoning_tokens INTEGER,
                    total_tokens INTEGER,
                    FOREIGN KEY (conversation_id) REFERENCES conversations(conversation_id),
                    FOREIGN KEY (trigger_message_id) REFERENCES messages(message_id),
                    FOREIGN KEY (parent_run_id) REFERENCES model_runs(run_id)
                );
                PRAGMA user_version = 3;
                """
            )
            connection.close()
            with self.assertRaisesRegex(RuntimeConversationError, "lineage"):
                initialize_runtime(database)
    def test_schema_v1_column_type_must_match(self):
        with TemporaryDirectory() as directory:
            database = Path(directory) / "runtime.sqlite3"
            self._write_schema(database, conversation_created_at="INTEGER NOT NULL")
            with self.assertRaises(RuntimeConversationError):
                initialize_runtime(database)

    def test_schema_v1_required_not_null_must_match(self):
        with TemporaryDirectory() as directory:
            database = Path(directory) / "runtime.sqlite3"
            self._write_schema(database, message_content="TEXT")
            with self.assertRaises(RuntimeConversationError):
                initialize_runtime(database)

    def test_schema_v1_primary_key_must_match(self):
        with TemporaryDirectory() as directory:
            database = Path(directory) / "runtime.sqlite3"
            self._write_schema(database, message_id="INTEGER")
            with self.assertRaises(RuntimeConversationError):
                initialize_runtime(database)

    def test_schema_v1_foreign_key_unique_and_role_check_must_match(self):
        variants = (
            {"foreign_key": False},
            {"unique_order": False},
            {"message_role": "TEXT NOT NULL CHECK (role IN ('user'))"},
        )
        for variant in variants:
            with self.subTest(variant=variant), TemporaryDirectory() as directory:
                database = Path(directory) / "runtime.sqlite3"
                self._write_schema(database, **variant)
                with self.assertRaises(RuntimeConversationError):
                    initialize_runtime(database)

    def test_model_runs_is_the_only_execution_evidence_table(self):
        with TemporaryDirectory() as directory:
            database = Path(directory) / "runtime.sqlite3"
            initialize_runtime(database)
            connection = sqlite3.connect(database)
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            connection.close()
            self.assertIn("model_runs", tables)
            self.assertIn("retrieval_conformance", tables)
            self.assertFalse(tables & {"router", "retrieval_inference", "synthesis", "model_runs_router"})

    def test_conversation_identity_is_uuid_and_timestamps_are_utc_facts(self):
        with TemporaryDirectory() as directory:
            database = Path(directory) / "runtime.sqlite3"
            initialize_runtime(database)
            first = create_conversation(database, clock=self._clock(1))
            second = create_conversation(database, clock=self._clock(2))
            UUID(first.conversation_id)
            UUID(second.conversation_id)
            self.assertNotEqual(first.conversation_id, second.conversation_id)
            self.assertEqual(first.created_at, "2026-01-01T00:00:01.000000Z")
            self.assertTrue(first.created_at.endswith("Z"))

    def test_roles_and_content_are_strictly_admitted_without_rewriting(self):
        with TemporaryDirectory() as directory:
            database = Path(directory) / "runtime.sqlite3"
            initialize_runtime(database)
            conversation = create_conversation(database)
            for role in ("assistant", "router", "retrieval_inference", "system", "tool"):
                with self.subTest(role=role), self.assertRaises(RuntimeConversationError):
                    append_message(database, conversation.conversation_id, role, "content")
            for content in ("", "   ", "\n\t"):
                with self.subTest(content=repr(content)), self.assertRaises(RuntimeConversationError):
                    append_message(database, conversation.conversation_id, "user", content)
            message = append_message(database, conversation.conversation_id, "user", "hello  ")
            self.assertEqual(message.content, "hello  ")
            self.assertEqual(get_conversation(database, conversation.conversation_id).messages[0].content, "hello  ")

    def test_append_is_linear_ordinal_zero_and_failed_append_is_empty(self):
        with TemporaryDirectory() as directory:
            database = Path(directory) / "runtime.sqlite3"
            initialize_runtime(database)
            first = create_conversation(database)
            second = create_conversation(database)
            one = append_message(database, first.conversation_id, "user", "one", clock=self._clock(2))
            two = append_message(database, first.conversation_id, "synthesis", "two", clock=self._clock(1))
            other = append_message(database, second.conversation_id, "user", "other")
            self.assertEqual((one.ordinal, two.ordinal, other.ordinal), (0, 1, 0))
            self.assertEqual([m.content for m in get_conversation(database, first.conversation_id).messages], ["one", "two"])
            with self.assertRaises(RuntimeConversationError):
                append_message(database, "missing", "user", "not stored")
            connection = sqlite3.connect(database)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM messages WHERE conversation_id='missing'").fetchone()[0], 0)
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "INSERT INTO messages(conversation_id, ordinal, role, content, created_at) VALUES (?, ?, ?, ?, ?)",
                    (first.conversation_id, 0, "user", "duplicate", "2026-01-01T00:00:00.000000Z"),
                )
            connection.close()
            self.assertNotIn("ordinal", inspect.signature(append_message).parameters)

    def test_exact_complete_thread_reconstruction_and_append_only_surface(self):
        with TemporaryDirectory() as directory:
            database = Path(directory) / "runtime.sqlite3"
            initialize_runtime(database)
            conversation = create_conversation(database)
            first = append_message(database, conversation.conversation_id, "user", "hello  ")
            second = append_message(database, conversation.conversation_id, "synthesis", "hi\nthere")
            third = append_message(database, conversation.conversation_id, "user", "what did I just say?")
            restored = get_conversation(database, conversation.conversation_id)
            self.assertEqual(restored.messages, (first, second, third))
            self.assertEqual(restored.messages, tuple(sorted(restored.messages, key=lambda message: message.ordinal)))
            self.assertEqual(
                set(restored.messages[0].__dataclass_fields__),
                {"message_id", "conversation_id", "ordinal", "role", "content", "created_at"},
            )
            import semantic_traversal.runtime.conversation as module
            self.assertEqual(
                set(module.__all__),
                {
                    "Conversation", "Message", "RuntimeConversationError", "initialize_runtime", "migrate_runtime",
                    "create_conversation", "append_message", "get_conversation",
                },
            )
            self.assertFalse(any(name in module.__all__ for name in ("edit_message", "delete_message", "truncate_thread")))

    def test_cli_runtime_round_trip_requires_explicit_database(self):
        with TemporaryDirectory() as directory:
            database = Path(directory) / "runtime.sqlite3"
            code, _, _ = self._run_cli("runtime", "conversation", "create", "--database", str(database), "--json")
            self.assertNotEqual(code, 0)
            self.assertFalse(database.exists())
            code, _, _ = self._run_cli("runtime", "conversation", "show", "--database", str(database), "--conversation-id", "missing", "--json")
            self.assertNotEqual(code, 0)
            self.assertFalse(database.exists())
            code, stdout, _ = self._run_cli("runtime", "init", "--database", str(database), "--json")
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(stdout)["schema_version"], 6)
            code, stdout, _ = self._run_cli("runtime", "conversation", "create", "--database", str(database), "--json")
            self.assertEqual(code, 0)
            conversation_id = json.loads(stdout)["conversation_id"]
            self.assertEqual(
                self._run_cli("runtime", "message", "append", "--database", str(database), "--conversation-id", conversation_id, "--role", "user", "--content", "hello  ", "--json")[0],
                0,
            )
            self.assertEqual(
                self._run_cli("runtime", "message", "append", "--database", str(database), "--conversation-id", conversation_id, "--role", "synthesis", "--content", "hi\nthere", "--json")[0],
                0,
            )
            code, stdout, _ = self._run_cli("runtime", "conversation", "show", "--database", str(database), "--conversation-id", conversation_id, "--json")
            self.assertEqual(code, 0)
            shown = json.loads(stdout)
            self.assertEqual([message["ordinal"] for message in shown["messages"]], [0, 1])
            self.assertEqual([message["content"] for message in shown["messages"]], ["hello  ", "hi\nthere"])
            with self.assertRaises(SystemExit):
                main(["runtime", "init"])
            with self.assertRaises(SystemExit):
                main(["runtime", "router"])


if __name__ == "__main__":
    unittest.main()
