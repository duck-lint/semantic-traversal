import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from semantic_traversal.runtime.config import PacketConfig, RuntimeConfigError, load_runtime_config
from semantic_traversal.runtime.retrieval.hydration import (
    HydratedCanonicalTarget, HydratedExactHit, HydratedExactResult,
    HydratedLexicalHit, HydratedLexicalResult, HydratedRequestResult,
    HydratedRetrievalResult, HydratedVectorHit, HydratedVectorResult,
)
from semantic_traversal.runtime.retrieval.packet import (
    ExactOccurrence, LexicalOccurrence, VectorOccurrence,
    assemble_retrieval_packet,
)


def unit_target(unit_id: int, object_uuid: str = "owner") -> HydratedCanonicalTarget:
    owner = SimpleNamespace(source_object_uuid=object_uuid)
    unit = SimpleNamespace(unit_id=unit_id, source_object_uuid=object_uuid)
    return HydratedCanonicalTarget("semantic_unit", canonical_unit=unit, owning_object=owner)


def request(ordinal, operator, result, status="succeeded", failure=None):
    return HydratedRequestResult(ordinal, {"operator": operator}, operator, status, result, failure)


def execution(requests, status="succeeded", failure=None):
    return HydratedRetrievalResult("execution", "conformance", "run", "package", status, failure, tuple(requests))


class RetrievalPacketTests(unittest.TestCase):
    def test_round_robin_and_complete_removal_trace(self):
        result = execution([
            request(0, "exact.equals", HydratedExactResult(tuple(HydratedExactHit(i, unit_target(i)) for i in (1, 2, 3)))),
            request(1, "lexical.terms", HydratedLexicalResult(tuple(HydratedLexicalHit(i, float(i), unit_target(i)) for i in (4, 5, 6, 7)))),
            request(2, "vector.semantic_similarity", HydratedVectorResult(tuple(HydratedVectorHit("semantic_unit", i, .1, 0, unit_target(i)) for i in (8, 9)))),
        ])
        assembled = assemble_retrieval_packet(result, PacketConfig(7))
        self.assertEqual([(item.request_ordinal, item.occurrence_ordinal) for item in assembled.packet.selected_occurrences], [(0, 0), (1, 0), (2, 0), (0, 1), (1, 1), (2, 1), (0, 2)])
        self.assertEqual([(item.request_ordinal, item.occurrence_ordinal) for item in assembled.removals], [(1, 2), (1, 3)])
        self.assertEqual(assembled.packet.coverage.total_returned_occurrences, 9)

    def test_duplicate_occurrences_share_payload_but_not_evidence(self):
        target = unit_target(42)
        result = execution([
            request(0, "exact.equals", HydratedExactResult((HydratedExactHit(42, target),))),
            request(1, "lexical.terms", HydratedLexicalResult((HydratedLexicalHit(42, -.001, target),))),
            request(2, "vector.semantic_similarity", HydratedVectorResult((HydratedVectorHit("semantic_unit", 42, .99, 0, target),))),
        ])
        packet = assemble_retrieval_packet(result, PacketConfig(3)).packet
        self.assertEqual(len(packet.selected_occurrences), 3)
        self.assertEqual(len(packet.canonical_payloads), 2)  # owner plus one unit
        self.assertIsInstance(packet.selected_occurrences[0], ExactOccurrence)
        self.assertIsInstance(packet.selected_occurrences[1], LexicalOccurrence)
        self.assertIsInstance(packet.selected_occurrences[2], VectorOccurrence)

    def test_failed_and_not_executed_are_unmeasured_but_preserved(self):
        result = execution([
            request(0, "exact.equals", HydratedExactResult(())),
            request(1, "lexical.terms", None, "failed", {"kind": "surface_failure"}),
            request(2, "vector.semantic_similarity", None, "not_executed", {"kind": "prior_request_failed"}),
        ], "failed", {"kind": "surface_failure"})
        packet = assemble_retrieval_packet(result, PacketConfig(32)).packet
        self.assertEqual(packet.execution_status, "failed")
        self.assertEqual(packet.requests[0].returned_occurrences, 0)
        self.assertEqual(packet.requests[0].exhaustive_exact_match_count, 0)
        self.assertIsNone(packet.requests[1].returned_occurrences)
        self.assertIsNone(packet.requests[2].returned_occurrences)
        self.assertEqual(packet.execution_failure["kind"], "surface_failure")

    def test_packet_config_yaml_is_strict(self):
        base = "router:\n  provider: openai\n  model: m\n  timeout_seconds: 1\n  prompt: p\nretrieval_inference:\n  provider: openai\n  model: m\n  timeout_seconds: 1\n  prompt: p\n"
        with TemporaryDirectory() as directory:
            path = Path(directory) / "runtime.yaml"
            for value in (None, "", "max_occurrences: 0", "max_occurrences: -1", "max_occurrences: true", "max_occurrences: 32.0", "max_occurrences: '32'", "max_occurrences: 32\n  extra: true"):
                packet = "packet:\n  " + value + "\n" if value is not None else ""
                path.write_text(base + packet, encoding="utf-8")
                with self.subTest(value=value), self.assertRaises(RuntimeConfigError):
                    load_runtime_config(path)
            path.write_text(base + "packet:\n  max_occurrences: 32\n", encoding="utf-8")
            self.assertEqual(load_runtime_config(path).packet, PacketConfig(32))


if __name__ == "__main__":
    unittest.main()
