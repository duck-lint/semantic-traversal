import hashlib
import json
import sqlite3
import unittest
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

from semantic_traversal.runtime.config import CandidateSelectionConfig, ModelConfig, RuntimeConfig
from semantic_traversal.runtime.conversation import append_message, create_conversation, get_conversation, initialize_runtime
from semantic_traversal.runtime.openai_provider import ProviderInference, ProviderUsage
from semantic_traversal.runtime.prompts import prompt_version
from semantic_traversal.runtime.router import route_conversation
from semantic_traversal.runtime.retrieval.candidate_hydration import HydratedCandidateSelection
from semantic_traversal.runtime.retrieval.evidence_projection import EvidenceCoverage, EvidenceProjection
from semantic_traversal.runtime.retrieval.execution import EXECUTION_CONTRACT_VERSION
from semantic_traversal.runtime.retrieval.package import IDENTITY_VERSION
from semantic_traversal.runtime.retrieval.package_verification import VERIFICATION_CONTRACT_VERSION
from semantic_traversal.runtime.retrieval.selection import CandidateSelection, SelectionCoverage
from semantic_traversal.runtime.synthesis import (
    SynthesisError,
    SynthesisProviderError,
    SynthesisProviderResult,
    SynthesisUsage,
    load_synthesis_success,
    synthesize_conversation,
)
from semantic_traversal.runtime.synthesis_input import serialize_synthesis_input


class RouterDouble:
    def __init__(self, route="direct"):
        self.route = route

    def infer_router(self, config, messages):
        return ProviderInference(self.route, json.dumps({"route": self.route}, separators=(",", ":")), None, ProviderUsage())


class SynthesisDouble:
    def __init__(self, result=None, error=None, mutate=None):
        self.result = result or SynthesisProviderResult("answer")
        self.error = error
        self.mutate = mutate
        self.calls = []

    def synthesize(self, config, synthesis_input):
        self.calls.append((config, synthesis_input))
        if self.mutate is not None:
            self.mutate()
        if self.error is not None:
            raise self.error
        return self.result


class SynthesisRuntimeTests(unittest.TestCase):
    def config(self):
        return RuntimeConfig(
            ModelConfig("openai", "router", 1.0, "router prompt"),
            ModelConfig("openai", "retrieval", 1.0, "retrieval prompt"),
            CandidateSelectionConfig(120, 0.20),
            ModelConfig("openai", "synthesis", 9.0, "synthesis prompt"),
        )

    def prepared(self, directory, route="direct"):
        database = Path(directory) / "runtime.sqlite3"
        initialize_runtime(database)
        conversation = create_conversation(database)
        trigger = append_message(database, conversation.conversation_id, "user", "hello")
        router = route_conversation(database, self.config(), conversation.conversation_id, provider=RouterDouble(route))
        return database, conversation, trigger, router

    def test_direct_happy_path_persists_exact_input_and_atomic_answer(self):
        with TemporaryDirectory() as directory:
            database, conversation, trigger, router = self.prepared(directory)
            provider = SynthesisDouble(SynthesisProviderResult("  exact answer  ", "provider-id", SynthesisUsage(1, 2, 3, 4, 5)))
            result = synthesize_conversation(database, self.config(), conversation.conversation_id, router.run_id, provider=provider)
            self.assertEqual(len(provider.calls), 1)
            config, synthesis_input = provider.calls[0]
            self.assertEqual(config, self.config().synthesis)
            self.assertEqual(synthesis_input.route, "direct")
            self.assertIsNone(synthesis_input.evidence)
            connection = sqlite3.connect(database)
            row = connection.execute(
                "SELECT run_kind, parent_run_id, status, input_json, input_sha256, output_text, produced_message_id, provider_response_id, input_tokens, total_tokens FROM model_runs WHERE run_id = ?",
                (result.run_id,),
            ).fetchone()
            message = connection.execute("SELECT role, content FROM messages WHERE message_id = ?", (result.produced_message_id,)).fetchone()
            connection.close()
            self.assertEqual(row[0:3], ("synthesis", router.run_id, "succeeded"))
            self.assertEqual(row[3], serialize_synthesis_input(synthesis_input))
            self.assertEqual(row[4], "sha256:" + hashlib.sha256(row[3].encode("utf-8")).hexdigest())
            self.assertEqual(row[5:10], ("  exact answer  ", result.produced_message_id, "provider-id", 1, 5))
            self.assertEqual(message, ("synthesis", "  exact answer  "))
            self.assertEqual(result.prompt_version, prompt_version(self.config().synthesis.prompt))
            self.assertEqual(result.trigger_message_id, trigger.message_id)

    def test_success_is_idempotent_without_provider_or_latest_user_precondition(self):
        with TemporaryDirectory() as directory:
            database, conversation, _, router = self.prepared(directory)
            provider = SynthesisDouble()
            first = synthesize_conversation(database, self.config(), conversation.conversation_id, router.run_id, provider=provider)
            second = synthesize_conversation(database, self.config(), conversation.conversation_id, router.run_id, provider=SynthesisDouble())
            self.assertEqual(load_synthesis_success(database, conversation.conversation_id, router.run_id), second)
            self.assertEqual(first, second)
            self.assertEqual(len(provider.calls), 1)
            connection = sqlite3.connect(database)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM model_runs WHERE run_kind = 'synthesis' AND status = 'succeeded'").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM messages WHERE role = 'synthesis'").fetchone()[0], 1)
            connection.close()

    def test_synthesis_success_lookup_is_non_mutating_and_does_not_need_evidence(self):
        with TemporaryDirectory() as directory:
            database, conversation, _, router = self.prepared(directory)
            result = synthesize_conversation(database, self.config(), conversation.conversation_id, router.run_id, provider=SynthesisDouble())
            replay = load_synthesis_success(database, conversation.conversation_id, router.run_id)
            self.assertEqual(replay, result)
            connection = sqlite3.connect(database)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM model_runs WHERE run_kind='synthesis'").fetchone()[0], 1)
            connection.close()

    def test_synthesis_success_lookup_returns_none_or_blocks_running(self):
        with TemporaryDirectory() as directory:
            database, conversation, trigger, router = self.prepared(directory)
            self.assertIsNone(load_synthesis_success(database, conversation.conversation_id, router.run_id))
            connection = sqlite3.connect(database)
            connection.execute(
                "INSERT INTO model_runs (run_id, conversation_id, trigger_message_id, run_kind, parent_run_id, provider, model, prompt_version, status, started_at, input_json, input_sha256) VALUES ('running', ?, ?, 'synthesis', ?, 'openai', 'synthesis', 'prompt', 'running', 'started', '{}', 'sha256:" + "0" * 64 + "')",
                (conversation.conversation_id, trigger.message_id, router.run_id),
            )
            connection.commit()
            connection.close()
            with self.assertRaisesRegex(SynthesisError, "already running"):
                load_synthesis_success(database, conversation.conversation_id, router.run_id)

    def test_provider_failure_is_durable_and_retryable(self):
        with TemporaryDirectory() as directory:
            database, conversation, _, router = self.prepared(directory)
            with self.assertRaises(SynthesisError) as raised:
                synthesize_conversation(database, self.config(), conversation.conversation_id, router.run_id, provider=SynthesisDouble(error=SynthesisProviderError("timeout", "provider timed out")))
            self.assertIsInstance(raised.exception.__cause__, SynthesisProviderError)
            self.assertIsNone(load_synthesis_success(database, conversation.conversation_id, router.run_id))
            second = synthesize_conversation(database, self.config(), conversation.conversation_id, router.run_id, provider=SynthesisDouble())
            connection = sqlite3.connect(database)
            rows = connection.execute("SELECT run_id, status, error_type FROM model_runs WHERE run_kind = 'synthesis' ORDER BY started_at").fetchall()
            connection.close()
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0][1:], ("failed", "timeout"))
            self.assertEqual(rows[1][0], second.run_id)
            self.assertEqual(rows[1][1], "succeeded")
            self.assertEqual(len(get_conversation(database, conversation.conversation_id).messages), 2)

    def test_invalid_provider_result_fails_without_message(self):
        with TemporaryDirectory() as directory:
            database, conversation, _, router = self.prepared(directory)
            provider = SynthesisDouble(result={"response_text": "not a provider result"})
            with self.assertRaises(SynthesisError):
                synthesize_conversation(database, self.config(), conversation.conversation_id, router.run_id, provider=provider)
            connection = sqlite3.connect(database)
            self.assertEqual(connection.execute("SELECT status, error_type FROM model_runs WHERE run_kind = 'synthesis'").fetchone(), ("failed", "runtime_validation"))
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM messages WHERE role = 'synthesis'").fetchone()[0], 0)
            connection.close()

    def test_running_claim_blocks_second_public_invocation_without_provider_call(self):
        with TemporaryDirectory() as directory:
            database, conversation, trigger, router = self.prepared(directory)
            connection = sqlite3.connect(database)
            connection.execute(
                "INSERT INTO model_runs (run_id, conversation_id, trigger_message_id, run_kind, parent_run_id, provider, model, prompt_version, status, started_at, input_json, input_sha256) "
                "VALUES ('running', ?, ?, 'synthesis', ?, 'openai', 'synthesis', 'prompt', 'running', 'started', '{}', 'sha256:" + "0" * 64 + "')",
                (conversation.conversation_id, trigger.message_id, router.run_id),
            )
            connection.commit()
            connection.close()
            provider = SynthesisDouble()
            with self.assertRaisesRegex(SynthesisError, "already running"):
                synthesize_conversation(database, self.config(), conversation.conversation_id, router.run_id, provider=provider)
            self.assertEqual(provider.calls, [])

    def test_semantic_happy_path_uses_retrieval_parent_and_validates_lineage(self):
        with TemporaryDirectory() as directory:
            database, conversation, trigger, router = self.prepared(directory, "semantic_retrieval")
            values = {
                "execution_id": "execution",
                "conformance_id": "conformance",
                "retrieval_run_id": "retrieval",
                "retrieval_proposal_sha256": "proposal",
                "capability_catalog_sha256": "catalog",
                "retrieval_package_id": "package",
                "retrieval_package_identity_version": IDENTITY_VERSION,
                "substrate_sha256": "substrate",
                "vectors_sha256": "vectors",
                "package_verification_contract_version": VERIFICATION_CONTRACT_VERSION,
                "execution_contract_version": EXECUTION_CONTRACT_VERSION,
            }
            selection = CandidateSelection(
                "candidate-selection-v2", "candidate-workspace-v1", values["execution_id"], values["conformance_id"],
                values["retrieval_run_id"], values["retrieval_proposal_sha256"], values["capability_catalog_sha256"],
                values["retrieval_package_id"], values["retrieval_package_identity_version"], values["substrate_sha256"],
                values["vectors_sha256"], values["package_verification_contract_version"], values["execution_contract_version"],
                "succeeded", None, 120, "candidate-ownership-topology-v1", 0.20, 24, False, (), (), (), (), (),
                SelectionCoverage(0, 0, 0, 0, 0, 0, 120, False, False),
            )
            evidence = EvidenceProjection(
                "evidence-projection-v2", HydratedCandidateSelection("candidate-hydration-v1", selection, ()), (),
                EvidenceCoverage("succeeded", None, 0, 0, 0, False, 0, 0, 0), (), (), (),
            )
            connection = sqlite3.connect(database)
            connection.execute(
                "INSERT INTO model_runs (run_id, conversation_id, trigger_message_id, run_kind, parent_run_id, capability_catalog_sha256, provider, model, prompt_version, status, started_at, output_json) VALUES (?, ?, ?, 'retrieval_inference', ?, ?, 'provider', 'model', 'prompt', 'succeeded', 'started', ?)",
                (values["retrieval_run_id"], conversation.conversation_id, trigger.message_id, router.run_id, values["capability_catalog_sha256"], "{}"),
            )
            connection.execute(
                "INSERT INTO retrieval_conformance VALUES (?, ?, ?, ?, 'catalog-conformance-v1', 'valid', 'checked', '{}')",
                (values["conformance_id"], values["retrieval_run_id"], values["retrieval_proposal_sha256"], values["capability_catalog_sha256"]),
            )
            connection.execute(
                "INSERT INTO retrieval_executions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'succeeded', 'started', 'completed', '{}')",
                tuple(values[field] for field in ("execution_id", "conformance_id", "retrieval_run_id", "retrieval_proposal_sha256", "capability_catalog_sha256", "retrieval_package_id", "retrieval_package_identity_version", "substrate_sha256", "vectors_sha256", "package_verification_contract_version", "execution_contract_version")),
            )
            connection.commit()
            connection.close()
            forged_candidates = replace(evidence, candidates=("forged",))
            forged_coverage = replace(evidence, coverage=replace(evidence.coverage, selected_candidate_count=1))
            for forged in (forged_candidates, forged_coverage):
                provider = SynthesisDouble()
                with self.subTest(forged=forged), self.assertRaisesRegex(SynthesisError, "deterministic projection"):
                    synthesize_conversation(database, self.config(), conversation.conversation_id, router.run_id, evidence=forged, provider=provider)
                self.assertEqual(provider.calls, [])
            connection = sqlite3.connect(database)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM model_runs WHERE run_kind = 'synthesis'").fetchone()[0], 0)
            connection.close()
            provider = SynthesisDouble()
            result = synthesize_conversation(database, self.config(), conversation.conversation_id, router.run_id, evidence=evidence, provider=provider)
            self.assertEqual(result.parent_run_id, values["retrieval_run_id"])
            self.assertEqual(provider.calls[0][1].route, "semantic_retrieval")
            self.assertIs(provider.calls[0][1].evidence, evidence)
            self.assertEqual(load_synthesis_success(database, conversation.conversation_id, router.run_id), result)

    def test_conversation_mutation_during_provider_call_invalidates_answer(self):
        with TemporaryDirectory() as directory:
            database, conversation, _, router = self.prepared(directory)
            provider = SynthesisDouble(mutate=lambda: append_message(database, conversation.conversation_id, "user", "new question"))
            with self.assertRaisesRegex(SynthesisError, "conversation changed"):
                synthesize_conversation(database, self.config(), conversation.conversation_id, router.run_id, provider=provider)
            connection = sqlite3.connect(database)
            self.assertEqual(connection.execute("SELECT status FROM model_runs WHERE run_kind = 'synthesis'").fetchone()[0], "failed")
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM messages WHERE role = 'synthesis'").fetchone()[0], 0)
            connection.close()

    def test_direct_route_rejects_evidence_and_semantic_route_requires_evidence(self):
        with TemporaryDirectory() as directory:
            database, conversation, _, router = self.prepared(directory, "direct")
            with self.assertRaises(SynthesisError):
                synthesize_conversation(database, self.config(), conversation.conversation_id, router.run_id, evidence=object(), provider=SynthesisDouble())
            semantic_directory = Path(directory) / "semantic"
            semantic_directory.mkdir()
            database2, conversation2, _, router2 = self.prepared(semantic_directory, "semantic_retrieval")
            with self.assertRaises(SynthesisError):
                synthesize_conversation(database2, self.config(), conversation2.conversation_id, router2.run_id, provider=SynthesisDouble())

    def test_malformed_success_replay_fails_closed(self):
        with TemporaryDirectory() as directory:
            database, conversation, _, router = self.prepared(directory)
            first = synthesize_conversation(database, self.config(), conversation.conversation_id, router.run_id, provider=SynthesisDouble())
            connection = sqlite3.connect(database)
            connection.execute("UPDATE model_runs SET input_sha256 = 'sha256:" + "0" * 64 + "' WHERE run_id = ?", (first.run_id,))
            connection.commit()
            connection.close()
            with self.assertRaisesRegex(SynthesisError, "hash"):
                synthesize_conversation(database, self.config(), conversation.conversation_id, router.run_id, provider=SynthesisDouble())


if __name__ == "__main__":
    unittest.main()
