import json
import contextlib
import io
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from types import SimpleNamespace

from tests.test_cli import CliProvider
from tests.test_retrieval_package_verification import RetrievalPackageVerificationTests

from semantic_traversal.runtime.config import ModelConfig, PacketConfig, RuntimeConfig
from semantic_traversal.runtime.conversation import append_message, create_conversation, initialize_runtime
from semantic_traversal.runtime.openai_provider import ProviderInference, ProviderUsage, RetrievalProviderInference
from semantic_traversal.projection.vector import EmbeddingProviderError
from semantic_traversal.runtime.synthesis import SynthesisProviderResult, SynthesisUsage
from semantic_traversal.runtime.turn import RuntimeTurnError, execute_turn
from semantic_traversal.cli import main


class Router:
    def __init__(self, route, calls):
        self.route = route
        self.calls = calls

    def infer_router(self, config, messages):
        self.calls.append("router")
        return ProviderInference(self.route, json.dumps({"route": self.route}), None, ProviderUsage())


class Retrieval:
    def __init__(self, requests, calls):
        self.requests = requests
        self.calls = calls

    def infer_retrieval(self, config, catalog_text, messages):
        self.calls.append("retrieval")
        return RetrievalProviderInference(tuple(self.requests), "ignored", None, ProviderUsage())


class Synthesis:
    def __init__(self, calls):
        self.calls = calls

    def synthesize(self, config, synthesis_input):
        self.calls.append(("synthesis", synthesis_input))
        return SynthesisProviderResult("turn answer", None, SynthesisUsage())


def config() -> RuntimeConfig:
    return RuntimeConfig(
        ModelConfig("openai", "router", 1.0, "router"),
        ModelConfig("openai", "retrieval", 1.0, "retrieval"),
        PacketConfig(32),
        ModelConfig("openai", "synthesis", 1.0, "synthesis"),
    )


class TurnTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = TemporaryDirectory()
        cls.build = RetrievalPackageVerificationTests._build(Path(cls.directory.name) / "build", "relation")

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def prepared(self, route="direct"):
        directory = TemporaryDirectory()
        database = Path(directory.name) / "runtime.sqlite3"
        initialize_runtime(database)
        conversation = create_conversation(database)
        append_message(database, conversation.conversation_id, "user", "What should happen?")
        calls = []
        return directory, database, conversation, calls, Router(route, calls)

    def test_direct_turn_has_only_router_then_synthesis_and_no_retrieval_contact(self):
        directory, database, conversation, calls, router = self.prepared()
        try:
            synthesis = Synthesis(calls)
            result = execute_turn(database, config(), conversation.conversation_id, retrieval_build=self.build, router_provider=router, synthesis_provider=synthesis)
            self.assertEqual(calls[0], "router")
            self.assertEqual(calls[1][0], "synthesis")
            self.assertIsNone(result.retrieval_inference_result)
            self.assertIsNone(result.packet_assembly)
        finally:
            directory.cleanup()

    def test_semantic_exact_turn_runs_real_middle_stages_and_preserves_packet(self):
        directory, database, conversation, calls, router = self.prepared("semantic_retrieval")
        try:
            request = {"operator": "exact.equals", "field_class": "intrinsic", "field_name": "parsed_text", "target": "complete_value", "operand": {"shape": "scalar", "domain": "string", "value": "target body"}}
            retrieval = Retrieval((request,), calls)
            synthesis = Synthesis(calls)
            with patch("semantic_traversal.runtime.turn.OllamaEmbeddingProvider", side_effect=AssertionError("vector provider must be lazy")):
                result = execute_turn(
                    database, config(), conversation.conversation_id,
                    retrieval_build=self.build, router_provider=router,
                    retrieval_provider=retrieval, synthesis_provider=synthesis,
                )
            self.assertEqual([item for item in calls if isinstance(item, str)], ["router", "retrieval"])
            self.assertEqual(result.route, "semantic_retrieval")
            self.assertEqual(result.conformance_result.status, "valid")
            self.assertEqual(result.execution_result.status, "succeeded")
            self.assertEqual(result.hydrated_result.requests[0].status, "succeeded")
            self.assertEqual(result.packet_assembly.packet.execution_status, "succeeded")
            self.assertEqual(result.synthesis_result.response_text, "turn answer")
            self.assertEqual(len([item for item in calls if isinstance(item, tuple) and item[0] == "synthesis"]), 1)
            self.assertEqual(result.synthesis_result.produced_message_id, 2)
        finally:
            directory.cleanup()

    def test_zero_request_semantic_turn_still_hydrates_packets_and_synthesizes(self):
        directory, database, conversation, calls, router = self.prepared("semantic_retrieval")
        try:
            retrieval = Retrieval((), calls)
            synthesis = Synthesis(calls)
            result = execute_turn(
                database, config(), conversation.conversation_id,
                retrieval_build=self.build, router_provider=router,
                retrieval_provider=retrieval, synthesis_provider=synthesis,
            )
            self.assertEqual(result.conformance_result.status, "valid")
            self.assertEqual(result.execution_result.status, "succeeded")
            self.assertEqual(result.hydrated_result.requests, ())
            self.assertEqual(result.packet_assembly.packet.selected_occurrences, ())
            self.assertEqual(result.synthesis_result.response_text, "turn answer")
        finally:
            directory.cleanup()

    def test_partial_failed_execution_continues_with_factual_packet(self):
        directory, database, conversation, calls, router = self.prepared("semantic_retrieval")
        try:
            exact = {"operator": "exact.equals", "field_class": "intrinsic", "field_name": "parsed_text", "target": "complete_value", "operand": {"shape": "scalar", "domain": "string", "value": "target body"}}
            requests = (exact, {"operator": "vector.semantic_similarity", "query": "will fail"}, exact)
            retrieval = Retrieval(requests, calls)
            synthesis = Synthesis(calls)

            class FailingVector(CliProvider):
                def embed(self, text, *, truncate=False):
                    raise EmbeddingProviderError("expected vector surface failure")

            result = execute_turn(
                database, config(), conversation.conversation_id,
                retrieval_build=self.build, router_provider=router,
                retrieval_provider=retrieval, vector_provider=FailingVector(),
                synthesis_provider=synthesis,
            )
            self.assertEqual(result.execution_result.status, "failed")
            self.assertEqual([item.status for item in result.hydrated_result.requests], ["succeeded", "failed", "not_executed"])
            self.assertEqual(result.packet_assembly.packet.execution_status, "failed")
            self.assertEqual(len(result.packet_assembly.packet.selected_occurrences), 1)
            self.assertEqual(result.synthesis_result.response_text, "turn answer")
        finally:
            directory.cleanup()

    def test_prior_router_attempt_fails_before_any_provider(self):
        directory, database, conversation, calls, router = self.prepared()
        try:
            from semantic_traversal.runtime.router import route_conversation
            route_conversation(database, config(), conversation.conversation_id, provider=router)
            with self.assertRaisesRegex(RuntimeTurnError, "prior router attempt"):
                execute_turn(database, config(), conversation.conversation_id, router_provider=router, synthesis_provider=Synthesis(calls))
            self.assertEqual(calls, ["router"])
        finally:
            directory.cleanup()

    def test_cli_turn_run_preserves_plain_response_and_json_summary(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "runtime.yaml"
            config_path.write_text(
                "router:\n  provider: openai\n  model: r\n  timeout_seconds: 1\n  prompt: r\n"
                "retrieval_inference:\n  provider: openai\n  model: i\n  timeout_seconds: 1\n  prompt: i\n"
                "packet:\n  max_occurrences: 32\n"
                "synthesis:\n  provider: openai\n  model: s\n  timeout_seconds: 1\n  prompt: s\n",
                encoding="utf-8",
            )
            fake = SimpleNamespace(
                conversation_id="conversation",
                trigger_message_id=4,
                route="direct",
                router_result=SimpleNamespace(run_id="router"),
                retrieval_inference_result=None,
                conformance_result=None,
                execution_result=None,
                packet_assembly=None,
                synthesis_result=SimpleNamespace(run_id="synthesis", produced_message_id=5, response_text="exact\ntext"),
            )
            with patch("semantic_traversal.cli.execute_turn", return_value=fake) as execute:
                stdout = io.StringIO()
                with contextlib.redirect_stdout(stdout):
                    code = main(["runtime", "turn", "run", "--database", str(root / "runtime.sqlite3"), "--config", str(config_path), "--build", str(root / "build"), "--conversation-id", "conversation"])
                self.assertEqual(code, 0)
                self.assertEqual(stdout.getvalue(), "exact\ntext")
                execute.assert_called_once()
            with patch("semantic_traversal.cli.execute_turn", return_value=fake):
                stdout = io.StringIO()
                with contextlib.redirect_stdout(stdout):
                    code = main(["runtime", "turn", "run", "--database", str(root / "runtime.sqlite3"), "--config", str(config_path), "--build", str(root / "build"), "--conversation-id", "conversation", "--json"])
                self.assertEqual(code, 0)
                payload = json.loads(stdout.getvalue())
                self.assertEqual(payload["response_text"], "exact\ntext")
                self.assertEqual(payload["route"], "direct")
                self.assertIsNone(payload["retrieval_run_id"])


if __name__ == "__main__":
    unittest.main()
