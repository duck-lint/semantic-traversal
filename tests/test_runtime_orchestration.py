import unittest
import sqlite3
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from semantic_traversal.runtime.config import CandidateSelectionConfig, ModelConfig, RuntimeConfig
from semantic_traversal.runtime.orchestration import OrchestrationError, TurnResult, run_current_turn
from semantic_traversal.runtime.retrieval.execution import execute_retrieval
from semantic_traversal.runtime.synthesis import SynthesisProviderResult


class RuntimeOrchestrationTests(unittest.TestCase):
    def config(self):
        return RuntimeConfig(
            ModelConfig("router-provider", "router", 1.0, "router prompt"),
            ModelConfig("retrieval-provider", "retrieval", 1.0, "retrieval prompt"),
            CandidateSelectionConfig(12, 0.25),
            ModelConfig("synthesis-provider", "synthesis", 1.0, "synthesis prompt"),
        )

    def router(self, route="direct"):
        return SimpleNamespace(run_id="router-run", route=route)

    def synthesis(self, route="direct", parent_run_id="router-run"):
        return SimpleNamespace(
            conversation_id="conversation",
            trigger_message_id=7,
            route=route,
            parent_run_id=parent_run_id,
            run_id="synthesis-run",
            produced_message_id=8,
            response_text="answer",
        )

    def verified_package(self):
        package = SimpleNamespace(capability_catalog_path="admitted/catalog.json")
        return SimpleNamespace(package=package)

    def invoke(self, **overrides):
        arguments = {
            "database_path": "runtime.sqlite3",
            "runtime_config": self.config(),
            "conversation_id": "conversation",
            "router_provider": object(),
            "retrieval_provider": object(),
            "synthesis_provider": object(),
        }
        arguments.update(overrides)
        return run_current_turn(**arguments)

    def test_direct_fresh_path_is_exact_and_does_not_touch_retrieval(self):
        router = self.router()
        synthesis = self.synthesis()
        with patch("semantic_traversal.runtime.orchestration.route_conversation", return_value=router) as route, \
             patch("semantic_traversal.runtime.orchestration.load_synthesis_success", return_value=None) as load_success, \
             patch("semantic_traversal.runtime.orchestration.synthesize_conversation", return_value=synthesis) as synth, \
             patch("semantic_traversal.runtime.orchestration.load_retrieval_package") as load_package, \
             patch("semantic_traversal.runtime.orchestration.verify_retrieval_package") as verify, \
             patch("semantic_traversal.runtime.orchestration.infer_retrieval") as infer, \
             patch("semantic_traversal.runtime.orchestration.conform_retrieval") as conform, \
             patch("semantic_traversal.runtime.orchestration.execute_retrieval") as execute, \
             patch("semantic_traversal.runtime.orchestration.prepare_evidence") as evidence:
            result = self.invoke(retrieval_build_path="ignored")

        self.assertEqual(
            result,
            TurnResult("conversation", 7, "direct", "router-run", None, "synthesis-run", 8, "answer"),
        )
        route.assert_called_once()
        load_success.assert_called_once_with("runtime.sqlite3", "conversation", "router-run")
        self.assertIsNone(synth.call_args.kwargs["evidence"])
        for stage in (load_package, verify, infer, conform, execute, evidence):
            stage.assert_not_called()

    def test_success_replay_returns_before_any_mutable_retrieval_access(self):
        router = self.router("semantic_retrieval")
        success = self.synthesis("semantic_retrieval", "retrieval-run")
        with patch("semantic_traversal.runtime.orchestration.route_conversation", return_value=router), \
             patch("semantic_traversal.runtime.orchestration.load_synthesis_success", return_value=success), \
             patch("semantic_traversal.runtime.orchestration.synthesize_conversation") as synth, \
             patch("semantic_traversal.runtime.orchestration.load_retrieval_package") as load_package, \
             patch("semantic_traversal.runtime.orchestration.verify_retrieval_package") as verify:
            result = self.invoke(retrieval_build_path=None)

        self.assertEqual(result.retrieval_run_id, "retrieval-run")
        self.assertEqual(result.response_text, "answer")
        synth.assert_not_called()
        load_package.assert_not_called()
        verify.assert_not_called()

    def test_semantic_fresh_call_order_and_same_verified_package(self):
        calls = []
        router = self.router("semantic_retrieval")
        package = object()
        verified = self.verified_package()
        retrieval = SimpleNamespace(run_id="retrieval-run", requests=({"operator": "exact.equals"},))
        conformance = SimpleNamespace(status="valid", conformance_id="conformance")
        execution = SimpleNamespace(status="succeeded")
        evidence = object()
        synthesis = self.synthesis("semantic_retrieval", "retrieval-run")

        def stage(name, value):
            def call(*args, **kwargs):
                calls.append(name)
                return value
            return call

        with patch("semantic_traversal.runtime.orchestration.route_conversation", side_effect=stage("route", router)), \
             patch("semantic_traversal.runtime.orchestration.load_synthesis_success", side_effect=stage("success", None)), \
             patch("semantic_traversal.runtime.orchestration.load_retrieval_package", side_effect=stage("load", package)), \
             patch("semantic_traversal.runtime.orchestration.verify_retrieval_package", side_effect=stage("verify", verified)), \
             patch("semantic_traversal.runtime.orchestration.infer_retrieval", side_effect=stage("infer", retrieval)) as infer, \
             patch("semantic_traversal.runtime.orchestration.conform_retrieval", side_effect=stage("conform", conformance)) as conform, \
             patch("semantic_traversal.runtime.orchestration.execute_retrieval", side_effect=stage("execute", execution)) as execute, \
             patch("semantic_traversal.runtime.orchestration.prepare_evidence", side_effect=stage("evidence", evidence)) as prepare, \
             patch("semantic_traversal.runtime.orchestration.synthesize_conversation", side_effect=stage("synthesis", synthesis)):
            result = self.invoke(retrieval_build_path="build")

        self.assertEqual(calls, ["route", "success", "load", "verify", "infer", "conform", "execute", "evidence", "synthesis"])
        self.assertEqual(infer.call_args.args[2], verified.package.capability_catalog_path)
        self.assertEqual(conform.call_args.args[1], verified.package.capability_catalog_path)
        self.assertIs(execute.call_args.args[1], verified)
        self.assertIs(prepare.call_args.args[0], verified)
        self.assertEqual(result, TurnResult("conversation", 7, "semantic_retrieval", "router-run", "retrieval-run", "synthesis-run", 8, "answer"))

    def test_orchestration_forwards_lazy_factory_without_invoking_it(self):
        router = self.router("semantic_retrieval")
        verified = self.verified_package()
        retrieval = SimpleNamespace(requests=({"operator": "lexical.match"},), run_id="retrieval-run")
        conformance = SimpleNamespace(status="valid", conformance_id="conformance")
        execute = MagicMock(return_value=SimpleNamespace(status="succeeded"))
        factory = MagicMock(side_effect=RuntimeError("factory must not be called by orchestration"))
        with patch("semantic_traversal.runtime.orchestration.route_conversation", return_value=router), \
             patch("semantic_traversal.runtime.orchestration.load_synthesis_success", return_value=None), \
             patch("semantic_traversal.runtime.orchestration.load_retrieval_package", return_value=object()), \
             patch("semantic_traversal.runtime.orchestration.verify_retrieval_package", return_value=verified), \
             patch("semantic_traversal.runtime.orchestration.infer_retrieval", return_value=retrieval), \
             patch("semantic_traversal.runtime.orchestration.conform_retrieval", return_value=conformance), \
             patch("semantic_traversal.runtime.orchestration.execute_retrieval", execute), \
             patch("semantic_traversal.runtime.orchestration.prepare_evidence", return_value=object()), \
             patch("semantic_traversal.runtime.orchestration.synthesize_conversation", return_value=self.synthesis("semantic_retrieval", "retrieval-run")):
            self.invoke(retrieval_build_path="build", vector_provider_factory=factory)
        factory.assert_not_called()
        self.assertIs(execute.call_args.kwargs["vector_provider_factory"], factory)

        retrieval.requests = ({"operator": "vector.semantic_similarity"},)
        with patch("semantic_traversal.runtime.orchestration.route_conversation", return_value=router), \
             patch("semantic_traversal.runtime.orchestration.load_synthesis_success", return_value=None), \
             patch("semantic_traversal.runtime.orchestration.load_retrieval_package", return_value=object()), \
             patch("semantic_traversal.runtime.orchestration.verify_retrieval_package", return_value=verified), \
             patch("semantic_traversal.runtime.orchestration.infer_retrieval", return_value=retrieval), \
             patch("semantic_traversal.runtime.orchestration.conform_retrieval", return_value=conformance), \
             patch("semantic_traversal.runtime.orchestration.execute_retrieval", execute), \
             patch("semantic_traversal.runtime.orchestration.prepare_evidence", return_value=object()), \
             patch("semantic_traversal.runtime.orchestration.synthesize_conversation", return_value=self.synthesis("semantic_retrieval", "retrieval-run")):
            self.invoke(retrieval_build_path="build", vector_provider_factory=factory)
        factory.assert_not_called()
        self.assertIs(execute.call_args.kwargs["vector_provider_factory"], factory)

    def test_orchestration_restart_style_vector_replay_reaches_downstream_without_factory(self):
        router = self.router("semantic_retrieval")
        retrieval = SimpleNamespace(run_id="retrieval-run", requests=({"operator": "vector.semantic_similarity"},))
        factory = MagicMock(side_effect=RuntimeError("factory must not be called on replay"))
        execute = MagicMock(return_value=SimpleNamespace(status="succeeded"))
        with patch("semantic_traversal.runtime.orchestration.route_conversation", return_value=router), \
             patch("semantic_traversal.runtime.orchestration.load_synthesis_success", return_value=None), \
             patch("semantic_traversal.runtime.orchestration.load_retrieval_package", return_value=object()), \
             patch("semantic_traversal.runtime.orchestration.verify_retrieval_package", return_value=self.verified_package()), \
             patch("semantic_traversal.runtime.orchestration.infer_retrieval", return_value=retrieval), \
             patch("semantic_traversal.runtime.orchestration.conform_retrieval", return_value=SimpleNamespace(status="valid", conformance_id="c")), \
             patch("semantic_traversal.runtime.orchestration.execute_retrieval", execute), \
             patch("semantic_traversal.runtime.orchestration.prepare_evidence", return_value=object()) as prepare, \
             patch("semantic_traversal.runtime.orchestration.synthesize_conversation", return_value=self.synthesis("semantic_retrieval", "retrieval-run")) as synth:
            result = self.invoke(retrieval_build_path="build", vector_provider_factory=factory)
        factory.assert_not_called()
        self.assertEqual(result.route, "semantic_retrieval")
        prepare.assert_called_once()
        synth.assert_called_once()

    def test_empty_proposal_and_terminal_failed_execution_continue_to_synthesis(self):
        router = self.router("semantic_retrieval")
        retrieval = SimpleNamespace(run_id="retrieval-run", requests=())
        execution = SimpleNamespace(status="failed")
        prepare = MagicMock(return_value=object())
        synth = MagicMock(return_value=self.synthesis("semantic_retrieval", "retrieval-run"))
        with patch("semantic_traversal.runtime.orchestration.route_conversation", return_value=router), \
             patch("semantic_traversal.runtime.orchestration.load_synthesis_success", return_value=None), \
             patch("semantic_traversal.runtime.orchestration.load_retrieval_package", return_value=object()), \
             patch("semantic_traversal.runtime.orchestration.verify_retrieval_package", return_value=self.verified_package()), \
             patch("semantic_traversal.runtime.orchestration.infer_retrieval", return_value=retrieval), \
             patch("semantic_traversal.runtime.orchestration.conform_retrieval", return_value=SimpleNamespace(status="valid", conformance_id="c")), \
             patch("semantic_traversal.runtime.orchestration.execute_retrieval", return_value=execution) as execute, \
             patch("semantic_traversal.runtime.orchestration.prepare_evidence", prepare), \
             patch("semantic_traversal.runtime.orchestration.synthesize_conversation", synth):
            self.invoke(retrieval_build_path="build")
        prepare.assert_called_once()
        synth.assert_called_once()
        self.assertIs(execute.return_value, execution)

    def test_invalid_conformance_stops_before_execution(self):
        router = self.router("semantic_retrieval")
        with patch("semantic_traversal.runtime.orchestration.route_conversation", return_value=router), \
             patch("semantic_traversal.runtime.orchestration.load_synthesis_success", return_value=None), \
             patch("semantic_traversal.runtime.orchestration.load_retrieval_package", return_value=object()), \
             patch("semantic_traversal.runtime.orchestration.verify_retrieval_package", return_value=self.verified_package()), \
             patch("semantic_traversal.runtime.orchestration.infer_retrieval", return_value=SimpleNamespace(run_id="r", requests=())), \
             patch("semantic_traversal.runtime.orchestration.conform_retrieval", return_value=SimpleNamespace(status="invalid", conformance_id="c")), \
             patch("semantic_traversal.runtime.orchestration.execute_retrieval") as execute, \
             patch("semantic_traversal.runtime.orchestration.prepare_evidence") as prepare, \
             patch("semantic_traversal.runtime.orchestration.synthesize_conversation") as synth:
            with self.assertRaises(OrchestrationError) as raised:
                self.invoke(retrieval_build_path="build")
        self.assertEqual(raised.exception.stage, "conformance")
        execute.assert_not_called()
        prepare.assert_not_called()
        synth.assert_not_called()

    def test_lower_stage_failures_are_stage_tagged_and_preserve_their_causes(self):
        router = self.router("semantic_retrieval")
        verified = self.verified_package()
        retrieval = SimpleNamespace(run_id="retrieval-run", requests=())
        conformance = SimpleNamespace(status="valid", conformance_id="conformance")
        lower_stages = (
            ("package_load", "load_retrieval_package", RuntimeError("load")),
            ("package_verification", "verify_retrieval_package", RuntimeError("verify")),
            ("retrieval_inference", "infer_retrieval", RuntimeError("infer")),
            ("conformance", "conform_retrieval", RuntimeError("conform")),
            ("execution", "execute_retrieval", RuntimeError("execute")),
            ("evidence_preparation", "prepare_evidence", RuntimeError("evidence")),
            ("synthesis", "synthesize_conversation", RuntimeError("synthesis")),
        )
        for stage, target, error in lower_stages:
            with self.subTest(stage=stage):
                common = {
                    "route_conversation": patch("semantic_traversal.runtime.orchestration.route_conversation", return_value=router),
                    "load_synthesis_success": patch("semantic_traversal.runtime.orchestration.load_synthesis_success", return_value=None),
                    "load_retrieval_package": patch("semantic_traversal.runtime.orchestration.load_retrieval_package", return_value=object()),
                    "verify_retrieval_package": patch("semantic_traversal.runtime.orchestration.verify_retrieval_package", return_value=verified),
                    "infer_retrieval": patch("semantic_traversal.runtime.orchestration.infer_retrieval", return_value=retrieval),
                    "conform_retrieval": patch("semantic_traversal.runtime.orchestration.conform_retrieval", return_value=conformance),
                    "execute_retrieval": patch("semantic_traversal.runtime.orchestration.execute_retrieval", return_value=SimpleNamespace(status="succeeded")),
                    "prepare_evidence": patch("semantic_traversal.runtime.orchestration.prepare_evidence", return_value=object()),
                    "synthesize_conversation": patch("semantic_traversal.runtime.orchestration.synthesize_conversation", return_value=self.synthesis("semantic_retrieval", "retrieval-run")),
                }
                common[target] = patch(
                    f"semantic_traversal.runtime.orchestration.{target}",
                    side_effect=error,
                )
                with ExitStack() as stack:
                    for manager in common.values():
                        stack.enter_context(manager)
                    with self.assertRaises(OrchestrationError) as raised:
                        self.invoke(retrieval_build_path="build")
                self.assertEqual(raised.exception.stage, stage)
                self.assertIs(raised.exception.__cause__, error)

    def test_stage_failures_preserve_identity_and_stop(self):
        error = RuntimeError("router")
        with patch("semantic_traversal.runtime.orchestration.route_conversation", side_effect=error):
            with self.assertRaises(OrchestrationError) as raised:
                self.invoke()
        self.assertEqual(raised.exception.stage, "router")
        self.assertIs(raised.exception.__cause__, error)

        error = RuntimeError("replay")
        with patch("semantic_traversal.runtime.orchestration.route_conversation", return_value=self.router()), \
             patch("semantic_traversal.runtime.orchestration.load_synthesis_success", side_effect=error):
            with self.assertRaises(OrchestrationError) as raised:
                self.invoke()
        self.assertEqual(raised.exception.stage, "synthesis")
        self.assertIs(raised.exception.__cause__, error)

    def test_public_result_is_frozen_and_has_only_durable_fields(self):
        self.assertTrue(TurnResult.__dataclass_params__.frozen)
        self.assertEqual(
            set(TurnResult.__dataclass_fields__),
            {
                "conversation_id", "trigger_message_id", "route", "router_run_id",
                "retrieval_run_id", "synthesis_run_id", "synthesis_message_id", "response_text",
            },
        )


class RuntimeOrchestrationIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tests.test_runtime_retrieval_execution import RuntimeRetrievalExecutionTests
        cls.execution_fixture = RuntimeRetrievalExecutionTests
        cls.execution_fixture.setUpClass()

    @classmethod
    def tearDownClass(cls):
        cls.execution_fixture.tearDownClass()

    def test_terminal_vector_execution_replay_reaches_evidence_and_synthesis_without_factory(self):
        request = {"operator": "vector.semantic_similarity", "query": "query"}
        fixture = self.execution_fixture("runTest")
        database, package, conformance_id = fixture.prepared(
            "orchestration-vector-terminal-replay", [request]
        )
        connection = sqlite3.connect(database)
        connection.execute("UPDATE model_runs SET completed_at = 'done' WHERE status = 'succeeded'")
        connection.commit()
        connection.close()
        module = __import__("semantic_traversal.runtime.retrieval.execution", fromlist=["vector_lookup"])
        with patch.object(module, "vector_lookup", return_value=()):
            execute_retrieval(database, package, conformance_id, vector_provider=object())

        connection = sqlite3.connect(database)
        conversation_id = connection.execute("SELECT conversation_id FROM conversations").fetchone()[0]
        connection.close()

        class SynthesisDouble:
            calls = 0

            def synthesize(self, model_config, synthesis_input):
                self.calls += 1
                return SynthesisProviderResult("replayed evidence answer")

        synthesis_provider = SynthesisDouble()
        vector_provider_factory = MagicMock(side_effect=RuntimeError("replay factory must not be called"))
        config = RuntimeConfig(
            ModelConfig("router-provider", "router", 1.0, "router prompt"),
            ModelConfig("retrieval-provider", "retrieval", 1.0, "retrieval prompt"),
            CandidateSelectionConfig(12, 0.25),
            ModelConfig("synthesis-provider", "synthesis", 1.0, "synthesis prompt"),
        )
        result = run_current_turn(
            database,
            config,
            conversation_id,
            router_provider=object(),
            retrieval_provider=object(),
            synthesis_provider=synthesis_provider,
            retrieval_build_path=self.execution_fixture.build,
            vector_provider_factory=vector_provider_factory,
        )
        self.assertEqual(result.response_text, "replayed evidence answer")
        self.assertEqual(result.route, "semantic_retrieval")
        self.assertEqual(synthesis_provider.calls, 1)
        vector_provider_factory.assert_not_called()
