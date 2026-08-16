import contextlib
import io
import inspect
import json
import sqlite3
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import UUID

from semantic_traversal.cli import main
from semantic_traversal.runtime.conversation import (
    RuntimeConversationError,
    _connect,
    append_message,
    create_conversation,
    get_conversation,
    initialize_runtime,
)


class RuntimeConversationTests(unittest.TestCase):
    def _clock(self, seconds: int):
        return lambda: datetime(2026, 1, 1, 0, 0, seconds, tzinfo=timezone.utc)

    def _run_cli(self, *argv):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = main(list(argv))
        return code, stdout.getvalue(), stderr.getvalue()

    def test_new_database_schema_is_v1_foreign_keyed_and_idempotent(self):
        with TemporaryDirectory() as directory:
            database = Path(directory) / "runtime.sqlite3"
            initialize_runtime(database)
            connection = sqlite3.connect(database)
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 1)
            self.assertEqual(connection.execute("PRAGMA foreign_keys").fetchone()[0], 0)
            self.assertEqual(
                {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")},
                {"conversations", "messages"},
            )
            self.assertEqual(
                tuple(row[1] for row in connection.execute("PRAGMA table_info(messages)")),
                ("message_id", "conversation_id", "ordinal", "role", "content", "created_at"),
            )
            self.assertEqual(len(tuple(connection.execute("PRAGMA foreign_key_list(messages)"))), 1)
            self.assertIn("CHECK", connection.execute("SELECT sql FROM sqlite_master WHERE name='messages'").fetchone()[0])
            connection.close()

            conversation = create_conversation(database)
            initialize_runtime(database)
            self.assertEqual(get_conversation(database, conversation.conversation_id), conversation)

            runtime_connection = _connect(database)
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

    def test_no_execution_tables_are_created(self):
        with TemporaryDirectory() as directory:
            database = Path(directory) / "runtime.sqlite3"
            initialize_runtime(database)
            connection = sqlite3.connect(database)
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            connection.close()
            self.assertFalse(tables & {"model_runs", "router", "retrieval_inference", "synthesis"})

    def test_conversation_identity_is_uuid_and_timestamps_are_utc_facts(self):
        with TemporaryDirectory() as directory:
            database = Path(directory) / "runtime.sqlite3"
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
                {"Conversation", "Message", "RuntimeConversationError", "initialize_runtime", "create_conversation", "append_message", "get_conversation"},
            )
            self.assertFalse(any(name in module.__all__ for name in ("edit_message", "delete_message", "truncate_thread")))

    def test_cli_runtime_round_trip_requires_explicit_database(self):
        with TemporaryDirectory() as directory:
            database = Path(directory) / "runtime.sqlite3"
            code, stdout, _ = self._run_cli("runtime", "init", "--database", str(database), "--json")
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(stdout)["schema_version"], 1)
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
