import contextlib
import io
import json
import os
import sqlite3
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from semantic_traversal.cli import main
from semantic_traversal.runtime.config import (
    ModelConfig,
    PacketConfig,
    RuntimeConfig,
    RuntimeConfigError,
    load_runtime_config,
)
from semantic_traversal.runtime.conversation import append_message, create_conversation, initialize_runtime
from semantic_traversal.runtime.openai_provider import (
    OpenAIProviderError,
    OpenAIResponsesProvider,
    ProviderInference,
    ProviderUsage,
)
from semantic_traversal.runtime.router import (
    RouterResult,
    RuntimeRouterError,
    route_conversation,
)
from semantic_traversal.runtime.prompts import prompt_version

ROUTER_PROMPT_V1 = load_runtime_config(Path(__file__).parents[1] / "docs" / "runtime_config.yaml").router.prompt
ROUTER_PROMPT_VERSION = prompt_version(ROUTER_PROMPT_V1)



class ProviderDouble:
    def __init__(self, inference=None, error=None):
        self.inference = inference
        self.error = error
        self.calls = []

    def infer_router(self, config, messages):
        self.calls.append((config, config.prompt, tuple(messages)))
        if self.error is not None:
            raise self.error
        return self.inference


class RuntimeRouterTests(unittest.TestCase):
    def _config(self):
        return RuntimeConfig(
            ModelConfig("openai", "explicit-model-id", 7.5, ROUTER_PROMPT_V1),
            ModelConfig("openai", "retrieval-model", 8.5, "retrieval test prompt"),
            PacketConfig(32),
        )

    def _clock(self, seconds=0):
        return lambda: datetime(2026, 1, 1, 0, 0, seconds, tzinfo=timezone.utc)

    def _conversation_with_latest_user(self, database):
        initialize_runtime(database)
        conversation = create_conversation(database, clock=self._clock(1))
        append_message(database, conversation.conversation_id, "user", "hello  ", clock=self._clock(2))
        append_message(database, conversation.conversation_id, "synthesis", "hi\nthere", clock=self._clock(3))
        trigger = append_message(database, conversation.conversation_id, "user", "What is in my notes?", clock=self._clock(4))
        return conversation, trigger

    def _stub_router_result(self):
        return RouterResult(
            run_id="run",
            conversation_id="conversation",
            trigger_message_id=1,
            route="direct",
            provider="openai",
            model="model",
            prompt_version="router-v1",
            status="succeeded",
            provider_response_id=None,
            input_tokens=None,
            cached_input_tokens=None,
            output_tokens=None,
            reasoning_tokens=None,
            total_tokens=None,
        )

    def test_runtime_config_is_exact_and_secret_free(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "runtime.yaml"
            path.write_text(
                "router:\n  provider: openai\n  model: explicit-model-id\n  timeout_seconds: 7.5\n  prompt: router prompt\nretrieval_inference:\n  provider: openai\n  model: retrieval-model\n  timeout_seconds: 8.5\n  prompt: retrieval prompt\npacket:\n  max_occurrences: 32\n",
                encoding="utf-8",
            )
            loaded = load_runtime_config(path)
            self.assertEqual(loaded.router.provider, "openai")
            self.assertEqual(loaded.router.prompt, "router prompt")
            self.assertEqual(loaded.retrieval_inference.prompt, "retrieval prompt")
            for invalid in (
                "router: {}\nretrieval_inference: {}\n",
                "router:\n  provider: other\n  model: x\n  timeout_seconds: 1\n  prompt: p\nretrieval_inference:\n  provider: openai\n  model: x\n  timeout_seconds: 1\n  prompt: p\n",
                "router:\n  provider: openai\n  model: x\n  timeout_seconds: 1\nretrieval_inference:\n  provider: openai\n  model: x\n  timeout_seconds: 1\n  prompt: p\n",
                "router:\n  provider: openai\n  model: x\n  timeout_seconds: 1\n  prompt: p\nretrieval_inference:\n  provider: openai\n  model: x\n  timeout_seconds: 0\n  prompt: p\n",
            ):
                path.write_text(invalid, encoding="utf-8")
                with self.subTest(invalid=invalid), self.assertRaises(RuntimeConfigError):
                    load_runtime_config(path)
    def test_router_inserts_before_provider_and_persists_success_without_message(self):
        with TemporaryDirectory() as directory:
            database = Path(directory) / "runtime.sqlite3"
            conversation, trigger = self._conversation_with_latest_user(database)
            provider = ProviderDouble(
                ProviderInference(
                    "semantic_retrieval",
                    '{"route":"semantic_retrieval"}',
                    "response-1",
                    ProviderUsage(11, 2, 3, 4, 14),
                )
            )
            result = route_conversation(database, self._config(), conversation.conversation_id, provider=provider, clock=self._clock(5))
            self.assertEqual(result.route, "semantic_retrieval")
            self.assertEqual(result.trigger_message_id, trigger.message_id)
            self.assertEqual(len(provider.calls), 1)
            config, prompt, messages = provider.calls[0]
            self.assertEqual(config, self._config().router)
            self.assertEqual(prompt, ROUTER_PROMPT_V1)
            self.assertEqual([message.role for message in messages], ["user", "synthesis", "user"])
            self.assertEqual([message.content for message in messages], ["hello  ", "hi\nthere", "What is in my notes?"])

            connection = sqlite3.connect(database)
            row = connection.execute(
                "SELECT run_id, conversation_id, trigger_message_id, run_kind, provider, model, prompt_version, status, output_json, provider_response_id, input_tokens, cached_input_tokens, output_tokens, reasoning_tokens, total_tokens FROM model_runs"
            ).fetchone()
            self.assertEqual(row, (result.run_id, conversation.conversation_id, trigger.message_id, "router", "openai", "explicit-model-id", ROUTER_PROMPT_VERSION, "succeeded", '{"route":"semantic_retrieval"}', "response-1", 11, 2, 3, 4, 14))
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 3)
            connection.close()

    def test_direct_and_retrieval_routes_succeed_with_distinct_run_ids(self):
        with TemporaryDirectory() as directory:
            database = Path(directory) / "runtime.sqlite3"
            conversation, _ = self._conversation_with_latest_user(database)
            provider = ProviderDouble(ProviderInference("direct", '{"route":"direct"}', None, ProviderUsage()))
            first = route_conversation(database, self._config(), conversation.conversation_id, provider=provider)
            append_message(database, conversation.conversation_id, "user", "What is in my notes?")
            provider.inference = ProviderInference("semantic_retrieval", '{"route":"semantic_retrieval"}', None, ProviderUsage())
            second = route_conversation(database, self._config(), conversation.conversation_id, provider=provider)
            self.assertEqual(first.route, "direct")
            self.assertEqual(second.route, "semantic_retrieval")
            self.assertNotEqual(first.run_id, second.run_id)
            connection = sqlite3.connect(database)
            self.assertEqual(
                [row[0] for row in connection.execute("SELECT output_json FROM model_runs ORDER BY started_at")],
                ['{"route":"direct"}', '{"route":"semantic_retrieval"}'],
            )
            connection.close()

    def test_invalid_provider_route_values_fail_as_durable_runtime_validation(self):
        for invalid_route in ("code_work", "retrieval", None):
            with self.subTest(invalid_route=invalid_route), TemporaryDirectory() as directory:
                database = Path(directory) / "runtime.sqlite3"
                conversation, _ = self._conversation_with_latest_user(database)
                provider = ProviderDouble(ProviderInference(invalid_route, "ignored", None, ProviderUsage()))
                with self.assertRaisesRegex(RuntimeRouterError, "runtime_validation"):
                    route_conversation(database, self._config(), conversation.conversation_id, provider=provider)
                connection = sqlite3.connect(database)
                run = connection.execute("SELECT status, output_json, error_type FROM model_runs").fetchone()
                self.assertEqual(run, ("failed", None, "runtime_validation"))
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 3)
                connection.close()

    def test_provider_json_output_failures_keep_structured_output_vs_runtime_validation(self):
        cases = (
            ('{"route":"direct","reason":"extra"}', "runtime_validation"),
            ('"direct"', "runtime_validation"),
            ("arbitrary prose", "structured_output"),
        )
        for raw_output, expected_error in cases:
            with self.subTest(raw_output=raw_output):
                captured = {}

                class Responses:
                    def create(self, **kwargs):
                        captured["request"] = kwargs
                        return SimpleNamespace(id="response", output_text=raw_output, usage=None)

                class Client:
                    def __init__(self, **kwargs):
                        self.responses = Responses()

                with patch("semantic_traversal.runtime.openai_provider.openai.OpenAI", Client):
                    with self.assertRaises(OpenAIProviderError) as raised:
                        OpenAIResponsesProvider().infer_router(self._config().router, ())
                self.assertEqual(raised.exception.error_type, expected_error)

    def test_invalid_provider_json_creates_failed_run_without_touching_messages(self):
        cases = (
            ('{"route":"code_work"}', "runtime_validation"),
            ('{"route":"retrieval"}', "runtime_validation"),
            ('{"route":null}', "runtime_validation"),
            ('{"route":"direct","reason":"extra"}', "runtime_validation"),
            ('"direct"', "runtime_validation"),
            ("arbitrary prose", "structured_output"),
        )
        for raw_output, expected_error in cases:
            with self.subTest(raw_output=raw_output), TemporaryDirectory() as directory:
                database = Path(directory) / "runtime.sqlite3"
                conversation, _ = self._conversation_with_latest_user(database)

                class Responses:
                    def create(self, **kwargs):
                        return SimpleNamespace(id="response", output_text=raw_output, usage=None)

                class Client:
                    def __init__(self, **kwargs):
                        self.responses = Responses()

                with patch("semantic_traversal.runtime.openai_provider.openai.OpenAI", Client):
                    with self.assertRaisesRegex(RuntimeRouterError, expected_error):
                        route_conversation(database, self._config(), conversation.conversation_id)
                connection = sqlite3.connect(database)
                self.assertEqual(connection.execute("SELECT status, error_type, output_json FROM model_runs").fetchone(), ("failed", expected_error, None))
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 3)
                connection.close()

    def test_router_against_unmigrated_v1_fails_before_provider_contact(self):
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
                INSERT INTO conversations VALUES ('legacy', 'created');
                INSERT INTO messages(conversation_id, ordinal, role, content, created_at) VALUES ('legacy', 0, 'user', 'hello', 'created');
                PRAGMA user_version = 1;
                """
            )
            connection.close()
            provider = ProviderDouble(ProviderInference("direct", '{}', None, ProviderUsage()))
            with self.assertRaises(RuntimeRouterError):
                route_conversation(database, self._config(), "legacy", provider=provider)
            self.assertEqual(provider.calls, [])

    def test_router_provider_failure_is_terminal_and_does_not_append_message(self):
        with TemporaryDirectory() as directory:
            database = Path(directory) / "runtime.sqlite3"
            conversation, trigger = self._conversation_with_latest_user(database)
            provider = ProviderDouble(error=OpenAIProviderError("timeout", "provider timed out"))
            with self.assertRaisesRegex(RuntimeRouterError, "timeout"):
                route_conversation(database, self._config(), conversation.conversation_id, provider=provider, clock=self._clock(5))
            connection = sqlite3.connect(database)
            row = connection.execute(
                "SELECT conversation_id, trigger_message_id, status, completed_at, output_json, error_type, error_message FROM model_runs"
            ).fetchone()
            self.assertEqual(row[0:3], (conversation.conversation_id, trigger.message_id, "failed"))
            self.assertIsNotNone(row[3])
            self.assertIsNone(row[4])
            self.assertEqual(row[5:], ("timeout", "provider timed out"))
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 3)
            connection.close()

    def test_local_terminal_persistence_failure_is_runtime_validation_not_provider_status(self):
        with TemporaryDirectory() as directory:
            database = Path(directory) / "runtime.sqlite3"
            conversation, _ = self._conversation_with_latest_user(database)
            provider = ProviderDouble(ProviderInference("direct", '{"route":"direct"}', None, ProviderUsage()))
            with patch(
                "semantic_traversal.runtime.router.complete_router_run",
                side_effect=sqlite3.OperationalError("local persistence failed"),
            ), self.assertRaisesRegex(RuntimeRouterError, "runtime_validation"):
                route_conversation(database, self._config(), conversation.conversation_id, provider=provider)
            connection = sqlite3.connect(database)
            self.assertEqual(connection.execute("SELECT status, error_type FROM model_runs").fetchone(), ("failed", "runtime_validation"))
            connection.close()

    def test_router_requires_latest_user_and_does_not_create_run(self):
        with TemporaryDirectory() as directory:
            database = Path(directory) / "runtime.sqlite3"
            initialize_runtime(database)
            conversation = create_conversation(database)
            append_message(database, conversation.conversation_id, "synthesis", "already answered")
            provider = ProviderDouble()
            with self.assertRaises(RuntimeRouterError):
                route_conversation(database, self._config(), conversation.conversation_id, provider=provider)
            connection = sqlite3.connect(database)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM model_runs").fetchone()[0], 0)
            connection.close()
            self.assertEqual(provider.calls, [])

    def test_model_runs_structurally_reject_future_run_kinds_and_statuses(self):
        with TemporaryDirectory() as directory:
            database = Path(directory) / "runtime.sqlite3"
            conversation, trigger = self._conversation_with_latest_user(database)
            connection = sqlite3.connect(database)
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "INSERT INTO model_runs(run_id, conversation_id, trigger_message_id, run_kind, provider, model, prompt_version, status, started_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    ("bad-kind", conversation.conversation_id, trigger.message_id, "retrieval_inference", "openai", "model", "router-v1", "running", "now"),
                )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "INSERT INTO model_runs(run_id, conversation_id, trigger_message_id, run_kind, provider, model, prompt_version, status, started_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    ("bad-status", conversation.conversation_id, trigger.message_id, "router", "openai", "model", "router-v1", "queued", "now"),
                )
            connection.close()

    def test_openai_responses_request_contract_and_exact_role_mapping(self):
        messages = (
            type("Message", (), {"role": "user", "content": "hello  "})(),
            type("Message", (), {"role": "synthesis", "content": "hi\nthere"})(),
        )
        captured = {}

        class Responses:
            def create(self, **kwargs):
                captured["request"] = kwargs
                return SimpleNamespace(id="response-1", output_text='{"route":"direct"}', usage=SimpleNamespace(input_tokens=5, output_tokens=1, total_tokens=6, input_tokens_details=SimpleNamespace(cached_tokens=0), output_tokens_details=SimpleNamespace(reasoning_tokens=0)))

        class Client:
            def __init__(self, **kwargs):
                captured["client"] = kwargs
                self.responses = Responses()

        with patch("semantic_traversal.runtime.openai_provider.openai.OpenAI", Client):
            result = OpenAIResponsesProvider().infer_router(self._config().router, messages)
        self.assertEqual(result.route, "direct")
        self.assertEqual(captured["client"], {"max_retries": 0, "timeout": 7.5})
        request = captured["request"]
        self.assertEqual(request["model"], "explicit-model-id")
        self.assertEqual(request["store"], False)
        self.assertEqual(request["truncation"], "disabled")
        self.assertEqual(request["input"], [{"role": "user", "content": "hello  "}, {"role": "assistant", "content": "hi\nthere"}])
        self.assertNotIn("tools", request)
        self.assertNotIn("previous_response_id", request)
        self.assertEqual(request["text"]["format"]["type"], "json_schema")
        self.assertTrue(request["text"]["format"]["strict"])
        self.assertEqual(request["text"]["format"]["schema"], {"type": "object", "additionalProperties": False, "required": ["route"], "properties": {"route": {"type": "string", "enum": ["direct", "semantic_retrieval"]}}})

    def test_router_cli_json_uses_explicit_config_and_persists_result(self):
        with TemporaryDirectory() as directory:
            database = Path(directory) / "runtime.sqlite3"
            config = Path(directory) / "runtime.yaml"
            config.write_text("router:\n  provider: openai\n  model: model\n  timeout_seconds: 1\n  prompt: router prompt\nretrieval_inference:\n  provider: openai\n  model: retrieval-model\n  timeout_seconds: 1\n  prompt: retrieval prompt\npacket:\n  max_occurrences: 32\n", encoding="utf-8")
            initialize_runtime(database)
            conversation = create_conversation(database)
            append_message(database, conversation.conversation_id, "user", "hello")
            stdout, stderr = io.StringIO(), io.StringIO()
            with patch("semantic_traversal.runtime.router.OpenAIResponsesProvider.infer_router", return_value=ProviderInference("direct", '{"route":"direct"}', None, ProviderUsage())), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                code = main(["runtime", "router", "infer", "--database", str(database), "--config", str(config), "--conversation-id", conversation.conversation_id, "--json"])
            self.assertEqual(code, 0, stderr.getvalue())
            self.assertEqual(json.loads(stdout.getvalue())["route"], "direct")

    def test_router_cli_dotenv_process_key_wins_over_exact_cwd_file(self):
        with TemporaryDirectory() as directory:
            cwd = Path(directory)
            config = cwd / "runtime.yaml"
            config.write_text("router:\n  provider: openai\n  model: model\n  timeout_seconds: 1\n  prompt: router prompt\nretrieval_inference:\n  provider: openai\n  model: retrieval-model\n  timeout_seconds: 1\n  prompt: retrieval prompt\npacket:\n  max_occurrences: 32\n", encoding="utf-8")
            (cwd / ".env").write_text("OPENAI_API_KEY=dotenv-key\n", encoding="utf-8")
            with patch.dict(os.environ, {"OPENAI_API_KEY": "process-key"}), patch("semantic_traversal.cli.Path.cwd", return_value=cwd), patch("semantic_traversal.cli.route_conversation", return_value=self._stub_router_result()):
                code = main(["runtime", "router", "infer", "--database", str(cwd / "runtime.sqlite3"), "--config", str(config), "--conversation-id", "conversation", "--json"])
                self.assertEqual(code, 0)
                self.assertEqual(os.environ["OPENAI_API_KEY"], "process-key")

    def test_router_cli_dotenv_loads_only_exact_cwd_file_when_process_key_absent(self):
        with TemporaryDirectory() as directory:
            cwd = Path(directory)
            config = cwd / "runtime.yaml"
            config.write_text("router:\n  provider: openai\n  model: model\n  timeout_seconds: 1\n  prompt: router prompt\nretrieval_inference:\n  provider: openai\n  model: retrieval-model\n  timeout_seconds: 1\n  prompt: retrieval prompt\npacket:\n  max_occurrences: 32\n", encoding="utf-8")
            (cwd / ".env").write_text("OPENAI_API_KEY=dotenv-key\n", encoding="utf-8")
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("OPENAI_API_KEY", None)
                with patch("semantic_traversal.cli.Path.cwd", return_value=cwd), patch("semantic_traversal.cli.route_conversation", return_value=self._stub_router_result()):
                    code = main(["runtime", "router", "infer", "--database", str(cwd / "runtime.sqlite3"), "--config", str(config), "--conversation-id", "conversation", "--json"])
                self.assertEqual(code, 0)
                self.assertEqual(os.environ["OPENAI_API_KEY"], "dotenv-key")

    def test_router_cli_does_not_search_parent_dotenv(self):
        with TemporaryDirectory() as directory:
            parent = Path(directory)
            cwd = parent / "child"
            cwd.mkdir()
            config = cwd / "runtime.yaml"
            config.write_text("router:\n  provider: openai\n  model: model\n  timeout_seconds: 1\n  prompt: router prompt\nretrieval_inference:\n  provider: openai\n  model: retrieval-model\n  timeout_seconds: 1\n  prompt: retrieval prompt\npacket:\n  max_occurrences: 32\n", encoding="utf-8")
            (parent / ".env").write_text("OPENAI_API_KEY=parent-key\n", encoding="utf-8")
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("OPENAI_API_KEY", None)
                with patch("semantic_traversal.cli.Path.cwd", return_value=cwd), patch("semantic_traversal.cli.route_conversation", return_value=self._stub_router_result()):
                    code = main(["runtime", "router", "infer", "--database", str(cwd / "runtime.sqlite3"), "--config", str(config), "--conversation-id", "conversation", "--json"])
                self.assertEqual(code, 0)
                self.assertNotIn("OPENAI_API_KEY", os.environ)

    def test_non_model_cli_commands_do_not_load_dotenv(self):
        with TemporaryDirectory() as directory:
            cwd = Path(directory)
            (cwd / ".env").write_text("OPENAI_API_KEY=must-not-load\n", encoding="utf-8")
            database = cwd / "runtime.sqlite3"
            with patch("semantic_traversal.cli.Path.cwd", return_value=cwd), patch("semantic_traversal.cli.load_dotenv") as loader:
                self.assertEqual(main(["runtime", "init", "--database", str(database)]), 0)
                self.assertEqual(main(["runtime", "conversation", "create", "--database", str(database)]), 0)
                loader.assert_not_called()


if __name__ == "__main__":
    unittest.main()
