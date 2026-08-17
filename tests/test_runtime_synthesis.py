import datetime as dt
import json
import sqlite3
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from semantic_traversal.build.canonical import CanonicalObject, CanonicalUnit
from semantic_traversal.build.parser import FrontmatterField
from semantic_traversal.runtime.config import ModelConfig, PacketConfig, RuntimeConfig
from semantic_traversal.runtime.conversation import append_message, create_conversation, get_conversation, initialize_runtime
from semantic_traversal.runtime.openai_provider import ProviderInference, ProviderUsage
from semantic_traversal.runtime.retrieval.hydration import HydratedCanonicalTarget, HydratedExactHit, HydratedExactResult, HydratedRequestResult, HydratedRetrievalResult
from semantic_traversal.runtime.retrieval.packet import assemble_retrieval_packet
from semantic_traversal.runtime.router import route_conversation
from semantic_traversal.runtime.synthesis import (
    SYNTHESIS_INPUT_CONTRACT_VERSION, SynthesisError, SynthesisMessage, SynthesisProviderError,
    SynthesisProviderResult, SynthesisUsage, SynthesisInput, serialize_synthesis_input,
    synthesis_input_sha256, synthesize_conversation,
)


class RecordingProvider:
    def __init__(self, response="answer", error=None):
        self.response = response
        self.error = error
        self.inputs = []

    def synthesize(self, config, synthesis_input):
        self.inputs.append((config, synthesis_input))
        if self.error is not None:
            raise self.error
        return SynthesisProviderResult(self.response, "provider-response", SynthesisUsage(1, 2, 3, 4, 5))


def runtime_config():
    return RuntimeConfig(
        ModelConfig("openai", "router", 1.0, "router"),
        ModelConfig("openai", "retrieval", 1.0, "retrieval"),
        PacketConfig(32),
        ModelConfig("openai", "synthesis", 1.0, "synthesis"),
    )


def unit_target(unit_id=1):
    owner = SimpleNamespace(source_object_uuid="owner")
    unit = SimpleNamespace(unit_id=unit_id, source_object_uuid="owner")
    return HydratedCanonicalTarget("semantic_unit", canonical_unit=unit, owning_object=owner)


def empty_packet(execution_id="execution", conformance_id="conformance", retrieval_run_id="retrieval", package_id="package"):
    hydrated = HydratedRetrievalResult(
        execution_id, conformance_id, retrieval_run_id, package_id, "succeeded", None,
        (HydratedRequestResult(0, {"operator": "exact.equals"}, "exact.equals", "succeeded", HydratedExactResult(()), None),),
    )
    return assemble_retrieval_packet(hydrated, PacketConfig(32)).packet


class SynthesisTests(unittest.TestCase):
    def prepared(self, route="direct"):
        directory = TemporaryDirectory()
        database = Path(directory.name) / "runtime.sqlite3"
        initialize_runtime(database)
        conversation = create_conversation(database)
        append_message(database, conversation.conversation_id, "user", "Explain transcendental arguments.")
        router = route_conversation(
            database, runtime_config(), conversation.conversation_id,
            provider=type("Router", (), {"infer_router": lambda self, config, messages: ProviderInference(route, json.dumps({"route": route}), "router-response", ProviderUsage())})(),
        )
        return directory, database, conversation, router

    def add_semantic_lineage(self, database, conversation, router, packet):
        connection = sqlite3.connect(database)
        connection.execute(
            "INSERT INTO model_runs (run_id, conversation_id, trigger_message_id, run_kind, parent_run_id, capability_catalog_sha256, provider, model, prompt_version, status, started_at, output_json) VALUES (?, ?, ?, 'retrieval_inference', ?, ?, 'openai', 'retrieval', 'retrieval', 'succeeded', 'started', ?)",
            (packet.retrieval_run_id, conversation.conversation_id, 1, router.run_id, 'catalog-lineage', '{"requests":[]}'),
        )
        connection.execute(
            "INSERT INTO retrieval_conformance (conformance_id, retrieval_run_id, retrieval_proposal_sha256, capability_catalog_sha256, contract_version, status, checked_at, result_json) VALUES (?, ?, 'proposal', 'catalog', 'catalog-conformance-v1', 'valid', 'checked', '{\"status\":\"valid\",\"requests\":[]}')",
            (packet.conformance_id, packet.retrieval_run_id),
        )
        connection.execute(
            "INSERT INTO retrieval_executions (execution_id, conformance_id, retrieval_run_id, retrieval_proposal_sha256, capability_catalog_sha256, retrieval_package_id, retrieval_package_identity_version, substrate_sha256, vectors_sha256, package_verification_contract_version, execution_contract_version, status, started_at, completed_at, result_json) VALUES (?, ?, ?, 'proposal', 'catalog', ?, 'retrieval-package-v1', 'substrate', 'vectors', 'verification', 'retrieval-execution-v1', 'succeeded', 'started', 'done', '{\"requests\":[],\"execution_failure\":null}')",
            (packet.execution_id, packet.conformance_id, packet.retrieval_run_id, packet.retrieval_package_id),
        )
        connection.commit()
        connection.close()

    def test_direct_recording_input_and_idempotent_success(self):
        directory, database, conversation, router = self.prepared()
        try:
            provider = RecordingProvider("exact response")
            first = synthesize_conversation(database, runtime_config(), conversation.conversation_id, router.run_id, provider=provider)
            second = synthesize_conversation(database, runtime_config(), conversation.conversation_id, router.run_id, provider=provider)
            self.assertEqual(len(provider.inputs), 1)
            self.assertEqual(provider.inputs[0][1].route, "direct")
            received_messages = provider.inputs[0][1].conversation
            self.assertEqual(received_messages[-1].content, "Explain transcendental arguments.")
            self.assertEqual(received_messages, (SynthesisMessage(0, "user", "Explain transcendental arguments."),))
            self.assertEqual(set(vars(received_messages[-1])), {"ordinal", "role", "content"})
            input_json = serialize_synthesis_input(provider.inputs[0][1])
            self.assertNotIn("message_id", input_json)
            self.assertNotIn("created_at", input_json)
            self.assertNotIn("conversation_id", input_json)
            self.assertIsNone(provider.inputs[0][1].retrieval_packet)
            self.assertEqual(first, second)
            self.assertEqual(get_conversation(database, conversation.conversation_id).messages[-1].role, "synthesis")
            connection = sqlite3.connect(database)
            row = connection.execute("SELECT status, parent_run_id, output_text, produced_message_id FROM model_runs WHERE run_kind='synthesis'").fetchone()
            self.assertEqual(row[0], "succeeded")
            self.assertEqual(row[1], router.run_id)
            self.assertEqual(row[2], "exact response")
            self.assertEqual(row[3], first.produced_message_id)
            connection.close()
        finally:
            directory.cleanup()

    def test_retrieval_recording_preserves_packet_and_lineage(self):
        directory, database, conversation, router = self.prepared("semantic_retrieval")
        try:
            packet = empty_packet()
            self.add_semantic_lineage(database, conversation, router, packet)
            provider = RecordingProvider("retrieval response")
            result = synthesize_conversation(database, runtime_config(), conversation.conversation_id, router.run_id, retrieval_packet=packet, provider=provider)
            received = provider.inputs[0][1]
            self.assertEqual(received.retrieval_packet, packet)
            self.assertEqual((received.retrieval_packet.execution_id, received.retrieval_packet.conformance_id, received.retrieval_packet.retrieval_run_id, received.retrieval_packet.retrieval_package_id), ("execution", "conformance", "retrieval", "package"))
            self.assertEqual(received.retrieval_packet.coverage.max_occurrences, 32)
            self.assertEqual(received.retrieval_packet.requests[0].exhaustive_exact_match_count, 0)
            self.assertEqual(result.parent_run_id, "retrieval")
        finally:
            directory.cleanup()

    def test_failed_packet_and_zero_hit_packet_reach_provider(self):
        directory, database, conversation, router = self.prepared("semantic_retrieval")
        try:
            hydrated = HydratedRetrievalResult("execution", "conformance", "retrieval", "package", "failed", {"kind": "partial"}, ())
            packet = assemble_retrieval_packet(hydrated, PacketConfig(32)).packet
            self.add_semantic_lineage(database, conversation, router, packet)
            connection = sqlite3.connect(database)
            connection.execute(
                "UPDATE retrieval_executions SET status='failed', result_json=?",
                ('{"requests":[],"execution_failure":{"kind":"partial"}}',),
            )
            connection.commit()
            connection.close()
            provider = RecordingProvider("partial response")
            synthesize_conversation(database, runtime_config(), conversation.conversation_id, router.run_id, retrieval_packet=packet, provider=provider)
            self.assertEqual(provider.inputs[0][1].retrieval_packet.execution_status, "failed")
            self.assertEqual(provider.inputs[0][1].retrieval_packet.execution_failure["kind"], "partial")
        finally:
            directory.cleanup()

    def test_wrong_route_and_lineage_fail_before_provider(self):
        directory, database, conversation, router = self.prepared("direct")
        try:
            provider = RecordingProvider()
            with self.assertRaises(SynthesisError):
                synthesize_conversation(database, runtime_config(), conversation.conversation_id, router.run_id, retrieval_packet=empty_packet(), provider=provider)
            self.assertEqual(provider.inputs, [])
        finally:
            directory.cleanup()

    def test_continuity_and_type_fidelity_have_stable_input_hash(self):
        directory, database, conversation, router = self.prepared()
        try:
            append_message(database, conversation.conversation_id, "synthesis", "It drank milk and later ate hay.")
            append_message(database, conversation.conversation_id, "user", "When did that change?")
            # A new router run is required for the new latest user trigger.
            router = route_conversation(database, runtime_config(), conversation.conversation_id, provider=type("Router", (), {"infer_router": lambda self, config, messages: ProviderInference("direct", '{"route":"direct"}', None, ProviderUsage())})())
            provider = RecordingProvider("continuity response")
            synthesize_conversation(database, runtime_config(), conversation.conversation_id, router.run_id, provider=provider)
            messages = provider.inputs[0][1].conversation
            self.assertEqual([(item.role, item.content) for item in messages], [("user", "Explain transcendental arguments."), ("synthesis", "It drank milk and later ate hay."), ("user", "When did that change?")])

            fields = (FrontmatterField("none", "present_value", None), FrontmatterField("bool", "present_value", True), FrontmatterField("int", "present_value", 4), FrontmatterField("float", "present_value", 1.25), FrontmatterField("date", "present_value", dt.date(2026, 1, 2)), FrontmatterField("datetime", "present_value", dt.datetime(2026, 1, 2, 3, 4, tzinfo=dt.timezone.utc)), FrontmatterField("list", "present_value", ["x", None]), FrontmatterField("mapping", "present_value", {"x": "y"}))
            unit = CanonicalUnit(1, "owner", "owner.md", 0, (), (), "raw", "parsed", fields, (), ())
            owner = CanonicalObject("owner", "owner.md", (), fields, (), ())
            target = HydratedCanonicalTarget("semantic_unit", canonical_unit=unit, owning_object=owner)
            packet = assemble_retrieval_packet(HydratedRetrievalResult("e", "c", "r", "p", "succeeded", None, (HydratedRequestResult(0, {"operator": "exact.equals"}, "exact.equals", "succeeded", HydratedExactResult((HydratedExactHit(1, target),)), None),)), PacketConfig(1)).packet
            value = SynthesisInput(
                SYNTHESIS_INPUT_CONTRACT_VERSION,
                "semantic_retrieval",
                tuple(SynthesisMessage(item.ordinal, item.role, item.content) for item in get_conversation(database, conversation.conversation_id).messages),
                packet,
            )
            first, second = serialize_synthesis_input(value), serialize_synthesis_input(value)
            self.assertEqual(first, second)
            self.assertEqual(synthesis_input_sha256(first), synthesis_input_sha256(second))
            self.assertIn('"type":"date"', first)
            self.assertIn('"type":"datetime"', first)
            changed = first.replace("owner.md", "changed.md")
            self.assertNotEqual(synthesis_input_sha256(first), synthesis_input_sha256(changed))
        finally:
            directory.cleanup()

    def test_provider_failure_is_durable_and_not_retried(self):
        directory, database, conversation, router = self.prepared()
        try:
            provider = RecordingProvider(error=SynthesisProviderError("connection", "offline"))
            with self.assertRaises(SynthesisError):
                synthesize_conversation(database, runtime_config(), conversation.conversation_id, router.run_id, provider=provider)
            with self.assertRaises(SynthesisError):
                synthesize_conversation(database, runtime_config(), conversation.conversation_id, router.run_id, provider=provider)
            self.assertEqual(len(provider.inputs), 1)
            self.assertEqual(get_conversation(database, conversation.conversation_id).messages[-1].role, "user")
            connection = sqlite3.connect(database)
            self.assertEqual(connection.execute("SELECT status, error_type FROM model_runs WHERE run_kind='synthesis'").fetchone(), ("failed", "connection"))
            connection.close()
        finally:
            directory.cleanup()

    def test_unexpected_provider_error_leaves_running_attempt_and_closes_replay(self):
        directory, database, conversation, router = self.prepared()
        try:
            provider = RecordingProvider(error=RuntimeError("unexpected bug"))
            with self.assertRaisesRegex(RuntimeError, "unexpected bug"):
                synthesize_conversation(database, runtime_config(), conversation.conversation_id, router.run_id, provider=provider)
            connection = sqlite3.connect(database)
            self.assertEqual(connection.execute("SELECT status, error_type FROM model_runs WHERE run_kind='synthesis'").fetchone(), ("running", None))
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM messages WHERE role='synthesis'").fetchone()[0], 0)
            connection.close()
            second_provider = RecordingProvider("must not run")
            with self.assertRaisesRegex(SynthesisError, "incomplete prior synthesis"):
                synthesize_conversation(database, runtime_config(), conversation.conversation_id, router.run_id, provider=second_provider)
            self.assertEqual(second_provider.inputs, [])
        finally:
            directory.cleanup()

    def test_concurrent_calls_claim_one_attempt(self):
        directory, database, conversation, router = self.prepared()
        try:
            entered = threading.Event()
            release = threading.Event()

            class BlockingProvider(RecordingProvider):
                def synthesize(self, config, synthesis_input):
                    self.inputs.append((config, synthesis_input))
                    entered.set()
                    if not release.wait(5):
                        raise AssertionError("blocking provider was not released")
                    return SynthesisProviderResult("concurrent response", "provider-response", SynthesisUsage())

            first_provider = BlockingProvider()
            first_result = []
            first_error = []

            def run_first():
                try:
                    first_result.append(synthesize_conversation(database, runtime_config(), conversation.conversation_id, router.run_id, provider=first_provider))
                except BaseException as exc:  # pragma: no cover - assertion reports the unexpected thread failure
                    first_error.append(exc)

            thread = threading.Thread(target=run_first)
            thread.start()
            self.assertTrue(entered.wait(5))
            second_provider = RecordingProvider("must not run")
            with self.assertRaisesRegex(SynthesisError, "incomplete prior synthesis"):
                synthesize_conversation(database, runtime_config(), conversation.conversation_id, router.run_id, provider=second_provider)
            self.assertEqual(second_provider.inputs, [])
            release.set()
            thread.join(5)
            self.assertFalse(thread.is_alive())
            self.assertEqual(first_error, [])
            self.assertEqual(len(first_result), 1)
            connection = sqlite3.connect(database)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM messages WHERE role='synthesis'").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM model_runs WHERE run_kind='synthesis'").fetchone()[0], 1)
            connection.close()
        finally:
            release.set() if 'release' in locals() else None
            directory.cleanup()

    def test_successful_replay_rejects_corrupt_persisted_input_hash(self):
        directory, database, conversation, router = self.prepared()
        untouched_directory, untouched_database, untouched_conversation, untouched_router = self.prepared()
        try:
            provider = RecordingProvider("stable response")
            first = synthesize_conversation(database, runtime_config(), conversation.conversation_id, router.run_id, provider=provider)
            connection = sqlite3.connect(database)
            connection.execute("UPDATE model_runs SET input_json = ? WHERE run_id = ?", ("{\"corrupted\":true}", first.run_id))
            connection.commit()
            connection.close()
            with self.assertRaisesRegex(SynthesisError, "input hash"):
                synthesize_conversation(database, runtime_config(), conversation.conversation_id, router.run_id, provider=provider)
            self.assertEqual(len(provider.inputs), 1)
            self.assertEqual(get_conversation(database, conversation.conversation_id).messages[-1].content, "stable response")

            untouched_provider = RecordingProvider("untouched response")
            untouched_first = synthesize_conversation(
                untouched_database, runtime_config(), untouched_conversation.conversation_id,
                untouched_router.run_id, provider=untouched_provider,
            )
            untouched_second = synthesize_conversation(
                untouched_database, runtime_config(), untouched_conversation.conversation_id,
                untouched_router.run_id, provider=untouched_provider,
            )
            self.assertEqual(untouched_first, untouched_second)
            self.assertEqual(len(untouched_provider.inputs), 1)
        finally:
            directory.cleanup()
            untouched_directory.cleanup()


if __name__ == "__main__":
    unittest.main()
