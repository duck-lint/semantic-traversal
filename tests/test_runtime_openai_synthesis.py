import json
import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import openai

from semantic_traversal.build.canonical import CanonicalObject, CanonicalUnit
from semantic_traversal.runtime.config import ModelConfig, PacketConfig, RuntimeConfig
from semantic_traversal.runtime.conversation import append_message, create_conversation, initialize_runtime
from semantic_traversal.runtime.openai_provider import OpenAIResponsesProvider, ProviderInference, ProviderUsage
from semantic_traversal.runtime.retrieval.hydration import (
    HydratedCanonicalTarget, HydratedExactHit, HydratedExactResult,
    HydratedRequestResult, HydratedRetrievalResult,
)
from semantic_traversal.runtime.retrieval.packet import assemble_retrieval_packet
from semantic_traversal.runtime.router import route_conversation
from semantic_traversal.runtime.synthesis import (
    SynthesisError, SynthesisInput, SynthesisMessage, SynthesisProviderError,
    SynthesisUsage, serialize_synthesis_input, synthesize_conversation,
)


def config() -> RuntimeConfig:
    return RuntimeConfig(
        ModelConfig("openai", "router", 1.0, "router"),
        ModelConfig("openai", "retrieval", 1.0, "retrieval"),
        PacketConfig(32),
        ModelConfig("openai", "synthesis-model", 7.5, "configured synthesis prompt"),
    )


class OpenAISynthesisTests(unittest.TestCase):
    def test_exact_wire_and_completed_response_preserve_artifact_text_and_usage(self):
        unit = CanonicalUnit(1, "owner", "owner.md", 0, (), (), "raw", "parsed", (), (), ())
        owner = CanonicalObject("owner", "owner.md", (), (), (), ())
        target = HydratedCanonicalTarget("semantic_unit", canonical_unit=unit, owning_object=owner)
        packet = assemble_retrieval_packet(
            HydratedRetrievalResult(
                "execution", "conformance", "retrieval", "package", "succeeded", None,
                (HydratedRequestResult(0, {"operator": "exact.equals"}, "exact.equals", "succeeded", HydratedExactResult((HydratedExactHit(1, target),)), None),),
            ),
            PacketConfig(32),
        ).packet
        synthesis_input = SynthesisInput(
            "synthesis-input-v1",
            "semantic_retrieval",
            (SynthesisMessage(0, "user", "first"), SynthesisMessage(1, "synthesis", "prior answer")),
            packet,
        )
        response = SimpleNamespace(
            status="completed",
            output_text="  exact answer\n",
            id="response-id",
            usage=SimpleNamespace(
                input_tokens=11,
                input_tokens_details=SimpleNamespace(cached_tokens=2),
                output_tokens=7,
                output_tokens_details=SimpleNamespace(reasoning_tokens=3),
                total_tokens=18,
            ),
        )
        captured = {}

        class Responses:
            def create(self, **kwargs):
                captured.update(kwargs)
                return response

        class Client:
            def __init__(self, **kwargs):
                captured["client"] = kwargs
                self.responses = Responses()

        with patch("semantic_traversal.runtime.openai_provider.openai.OpenAI", Client):
            result = OpenAIResponsesProvider().synthesize(config().synthesis, synthesis_input)

        self.assertEqual(captured["client"], {"max_retries": 0, "timeout": 7.5})
        self.assertEqual(captured["model"], "synthesis-model")
        self.assertEqual(captured["instructions"], "configured synthesis prompt")
        self.assertEqual(captured["input"], serialize_synthesis_input(synthesis_input))
        self.assertIs(captured["store"], False)
        self.assertEqual(captured["truncation"], "disabled")
        self.assertFalse(set(captured) & {"tools", "previous_response_id", "max_output_tokens", "reasoning", "text"})
        self.assertEqual(result.response_text, "  exact answer\n")
        self.assertEqual(result.provider_response_id, "response-id")
        self.assertEqual(result.usage, SynthesisUsage(11, 2, 7, 3, 18))

    def test_incomplete_response_is_not_accepted(self):
        response = SimpleNamespace(status="incomplete", output_text="partial answer", incomplete_details=SimpleNamespace(reason="max_output_tokens"))
        with patch("semantic_traversal.runtime.openai_provider.openai.OpenAI") as opened:
            opened.return_value.responses.create.return_value = response
            with self.assertRaisesRegex(SynthesisProviderError, "max_output_tokens"):
                OpenAIResponsesProvider().synthesize(config().synthesis, SynthesisInput("synthesis-input-v1", "direct", (SynthesisMessage(0, "user", "x"),), None))

    def test_expected_openai_errors_are_narrowly_classified(self):
        request = httpx.Request("POST", "https://example.test")
        response = httpx.Response(500, request=request)
        errors = (
            (openai.APITimeoutError(request=request), "timeout"),
            (openai.APIConnectionError(request=request), "connection"),
            (openai.AuthenticationError("auth", response=response, body=None), "authentication"),
            (openai.RateLimitError("rate", response=response, body=None), "rate_limit"),
            (openai.BadRequestError("bad", response=response, body=None), "bad_request"),
            (openai.APIStatusError("status", response=response, body=None), "provider_status"),
        )
        synthesis_input = SynthesisInput("synthesis-input-v1", "direct", (SynthesisMessage(0, "user", "x"),), None)
        for error, expected in errors:
            with self.subTest(expected=expected), patch("semantic_traversal.runtime.openai_provider.openai.OpenAI", side_effect=error):
                with self.assertRaises(SynthesisProviderError) as raised:
                    OpenAIResponsesProvider().synthesize(config().synthesis, synthesis_input)
                self.assertEqual(raised.exception.error_type, expected)

    def test_unexpected_sdk_error_escapes(self):
        synthesis_input = SynthesisInput("synthesis-input-v1", "direct", (SynthesisMessage(0, "user", "x"),), None)
        with patch("semantic_traversal.runtime.openai_provider.openai.OpenAI", side_effect=RuntimeError("unexpected bug")):
            with self.assertRaisesRegex(RuntimeError, "unexpected bug"):
                OpenAIResponsesProvider().synthesize(config().synthesis, synthesis_input)

    def test_default_provider_wiring_persists_exact_input_and_success(self):
        with TemporaryDirectory() as directory:
            database = Path(directory) / "runtime.sqlite3"
            initialize_runtime(database)
            conversation = create_conversation(database)
            append_message(database, conversation.conversation_id, "user", "Explain the argument.")
            router = route_conversation(
                database,
                config(),
                conversation.conversation_id,
                provider=type("Router", (), {"infer_router": lambda self, model, messages: ProviderInference("direct", json.dumps({"route": "direct"}), "router-id", ProviderUsage())})(),
            )
            response = SimpleNamespace(status="completed", output_text="exact synthesis", id="synthesis-id", usage=None)
            captured = {}

            class Responses:
                def create(self, **kwargs):
                    captured.update(kwargs)
                    return response

            class Client:
                def __init__(self, **kwargs):
                    self.responses = Responses()

            with patch("semantic_traversal.runtime.openai_provider.openai.OpenAI", Client):
                result = synthesize_conversation(database, config(), conversation.conversation_id, router.run_id)

            connection = sqlite3.connect(database)
            persisted = connection.execute("SELECT input_json, input_sha256, status, produced_message_id, output_text FROM model_runs WHERE run_kind='synthesis'").fetchone()
            message = connection.execute("SELECT message_id, content FROM messages WHERE role='synthesis'").fetchone()
            connection.close()
            self.assertEqual(persisted[0], captured["input"])
            from semantic_traversal.runtime.synthesis import synthesis_input_sha256
            self.assertEqual(persisted[1], synthesis_input_sha256(captured["input"]))
            self.assertEqual(persisted[2:], ("succeeded", message[0], "exact synthesis"))
            self.assertEqual(result.produced_message_id, message[0])

    def test_default_provider_unexpected_error_leaves_running_attempt(self):
        with TemporaryDirectory() as directory:
            database = Path(directory) / "runtime.sqlite3"
            initialize_runtime(database)
            conversation = create_conversation(database)
            append_message(database, conversation.conversation_id, "user", "Explain the argument.")
            router = route_conversation(
                database,
                config(),
                conversation.conversation_id,
                provider=type("Router", (), {"infer_router": lambda self, model, messages: ProviderInference("direct", '{"route":"direct"}', None, ProviderUsage())})(),
            )
            with patch("semantic_traversal.runtime.openai_provider.openai.OpenAI", side_effect=RuntimeError("unexpected bug")):
                with self.assertRaisesRegex(RuntimeError, "unexpected bug"):
                    synthesize_conversation(database, config(), conversation.conversation_id, router.run_id)
            connection = sqlite3.connect(database)
            self.assertEqual(connection.execute("SELECT status FROM model_runs WHERE run_kind='synthesis'").fetchone(), ("running",))
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM messages WHERE role='synthesis'").fetchone()[0], 0)
            connection.close()

    def test_default_provider_expected_openai_error_fails_run_without_message(self):
        with TemporaryDirectory() as directory:
            database = Path(directory) / "runtime.sqlite3"
            initialize_runtime(database)
            conversation = create_conversation(database)
            append_message(database, conversation.conversation_id, "user", "Explain the argument.")
            router = route_conversation(
                database,
                config(),
                conversation.conversation_id,
                provider=type("Router", (), {"infer_router": lambda self, model, messages: ProviderInference("direct", '{"route":"direct"}', None, ProviderUsage())})(),
            )
            request = httpx.Request("POST", "https://example.test")
            with patch(
                "semantic_traversal.runtime.openai_provider.openai.OpenAI",
                side_effect=openai.APITimeoutError(request=request),
            ):
                with self.assertRaisesRegex(SynthesisError, "timeout"):
                    synthesize_conversation(database, config(), conversation.conversation_id, router.run_id)
            connection = sqlite3.connect(database)
            self.assertEqual(connection.execute("SELECT status, error_type FROM model_runs WHERE run_kind='synthesis'").fetchone(), ("failed", "timeout"))
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM messages WHERE role='synthesis'").fetchone()[0], 0)
            connection.close()


if __name__ == "__main__":
    unittest.main()
