import contextlib
import io
import json
import sqlite3
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from semantic_traversal.cli import main
from semantic_traversal.runtime.config import (
    RouterConfig,
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
    ROUTER_PROMPT_V1,
    ROUTER_PROMPT_VERSION,
    RuntimeRouterError,
    route_conversation,
)


class ProviderDouble:
    def __init__(self, inference=None, error=None):
        self.inference = inference
        self.error = error
        self.calls = []

    def infer(self, config, prompt, messages):
        self.calls.append((config, prompt, tuple(messages)))
        if self.error is not None:
            raise self.error
        return self.inference


class RuntimeRouterTests(unittest.TestCase):
    def _config(self):
        return RuntimeConfig(RouterConfig("openai", "explicit-model-id", 7.5))

    def _clock(self, seconds=0):
        return lambda: datetime(2026, 1, 1, 0, 0, seconds, tzinfo=timezone.utc)

    def _conversation_with_latest_user(self, database):
        initialize_runtime(database)
        conversation = create_conversation(database, clock=self._clock(1))
        append_message(database, conversation.conversation_id, "user", "hello  ", clock=self._clock(2))
        append_message(database, conversation.conversation_id, "synthesis", "hi\nthere", clock=self._clock(3))
        trigger = append_message(database, conversation.conversation_id, "user", "What is in my notes?", clock=self._clock(4))
        return conversation, trigger

    def test_runtime_config_is_exact_and_secret_free(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "runtime.yaml"
            path.write_text(
                "router:\n  provider: openai\n  model: explicit-model-id\n  timeout_seconds: 7.5\n",
                encoding="utf-8",
            )
            self.assertEqual(load_runtime_config(path), self._config())
            for invalid in (
                "router:\n  provider: other\n  model: x\n  timeout_seconds: 1\n",
                "router:\n  provider: openai\n  model: ''\n  timeout_seconds: 1\n",
                "router:\n  provider: openai\n  model: x\n  timeout_seconds: 0\n",
                "router:\n  provider: openai\n  model: x\n  timeout_seconds: 1\nextra: {}\n",
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
            self.assertEqual(config, self._config())
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
            result = OpenAIResponsesProvider().infer(self._config(), ROUTER_PROMPT_V1, messages)
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
            config.write_text("router:\n  provider: openai\n  model: model\n  timeout_seconds: 1\n", encoding="utf-8")
            initialize_runtime(database)
            conversation = create_conversation(database)
            append_message(database, conversation.conversation_id, "user", "hello")
            stdout, stderr = io.StringIO(), io.StringIO()
            with patch("semantic_traversal.runtime.router.OpenAIResponsesProvider.infer", return_value=ProviderInference("direct", '{"route":"direct"}', None, ProviderUsage())), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                code = main(["runtime", "router", "infer", "--database", str(database), "--config", str(config), "--conversation-id", conversation.conversation_id, "--json"])
            self.assertEqual(code, 0, stderr.getvalue())
            self.assertEqual(json.loads(stdout.getvalue())["route"], "direct")


if __name__ == "__main__":
    unittest.main()
