import unittest
from dataclasses import MISSING
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from semantic_traversal.runtime.config import PacketConfig, RuntimeConfigError, load_runtime_config
from semantic_traversal.runtime.retrieval.hydration import (
    HydratedCanonicalTarget, HydratedExactHit, HydratedExactResult,
    HydratedLexicalHit, HydratedLexicalResult, HydratedRequestResult,
    HydratedRetrievalResult, HydratedVectorHit, HydratedVectorResult,
    HydratedGraphOccurrence, HydratedGraphRelationResult,
)
from semantic_traversal.projection.graph import GraphHandle
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


def execution(requests, status="succeeded", failure=None, *, execution_id="execution", conformance_id="conformance", retrieval_run_id="run", retrieval_package_id="package"):
    return HydratedRetrievalResult(execution_id, conformance_id, retrieval_run_id, retrieval_package_id, status, failure, tuple(requests))


class RetrievalPacketTests(unittest.TestCase):
    def test_lineage_and_capacity_dial_are_preserved_without_reexecution(self):
        result = execution([
            request(0, "exact.equals", HydratedExactResult(tuple(HydratedExactHit(i, unit_target(i)) for i in (1, 2, 3)))),
            request(1, "lexical.terms", HydratedLexicalResult(tuple(HydratedLexicalHit(i, float(i), unit_target(i)) for i in (4, 5, 6)))),
        ], execution_id="execution-X", conformance_id="conformance-Y", retrieval_run_id="retrieval-Z", retrieval_package_id="package-P")
        small = assemble_retrieval_packet(result, PacketConfig(2)).packet
        large = assemble_retrieval_packet(result, PacketConfig(5)).packet
        self.assertEqual([(item.request_ordinal, item.occurrence_ordinal) for item in small.selected_occurrences], [(0, 0), (1, 0)])
        self.assertEqual([(item.request_ordinal, item.occurrence_ordinal) for item in large.selected_occurrences], [(0, 0), (1, 0), (0, 1), (1, 1), (0, 2)])
        for packet in (small, large):
            self.assertEqual((packet.execution_id, packet.conformance_id, packet.retrieval_run_id, packet.retrieval_package_id), ("execution-X", "conformance-Y", "retrieval-Z", "package-P"))
        self.assertEqual(result.requests[0].result.hits[0].unit_id, 1)

    def test_exact_count_and_removal_trace_survive_truncation(self):
        result = execution([request(0, "exact.equals", HydratedExactResult(tuple(HydratedExactHit(i, unit_target(i)) for i in range(1, 6))))])
        assembled = assemble_retrieval_packet(result, PacketConfig(2))
        coverage = assembled.packet.requests[0]
        self.assertEqual((coverage.returned_occurrences, coverage.selected_occurrences, coverage.omitted_occurrences, coverage.packet_truncated, coverage.exhaustive_exact_match_count), (5, 2, 3, True, 5))
        self.assertEqual((assembled.packet.coverage.total_returned_occurrences, assembled.packet.coverage.selected_occurrences, assembled.packet.coverage.omitted_occurrences, assembled.packet.coverage.packet_truncated), (5, 2, 3, True))
        self.assertEqual(len(assembled.removals), 3)
        for index, removal in enumerate(assembled.removals, 2):
            self.assertEqual((removal.execution_id, removal.request_ordinal, removal.occurrence_ordinal, removal.operator), ("execution", 0, index, "exact.equals"))
            self.assertEqual((removal.stage, removal.reason, removal.rule_authority, removal.selection_rule), ("packet_assembly", "max_occurrences_exhausted", "runtime_config.packet.max_occurrences", "request-round-robin-v1"))

    def test_distinct_units_share_one_complete_owner_payload(self):
        owner_uuid = "shared-owner"
        result = execution([request(0, "exact.equals", HydratedExactResult((HydratedExactHit(1, unit_target(1, owner_uuid)), HydratedExactHit(2, unit_target(2, owner_uuid)))))])
        packet = assemble_retrieval_packet(result, PacketConfig(2)).packet
        unit_payloads = [payload for payload in packet.canonical_payloads.values() if payload.canonical_unit is not None]
        object_payloads = [payload for payload in packet.canonical_payloads.values() if payload.canonical_object is not None]
        self.assertEqual((len(unit_payloads), len(object_payloads)), (2, 1))
        self.assertEqual({payload.owning_object_ref for payload in unit_payloads}, {object_payloads[0].target_ref})

    def test_graph_relation_is_one_occurrence_with_two_endpoint_payloads(self):
        source_handle = GraphHandle("semantic_object", ("source",))
        target_handle = GraphHandle("semantic_object", ("target",))
        source = HydratedCanonicalTarget("semantic_object", canonical_object=SimpleNamespace(source_object_uuid="source"), graph_handle=source_handle)
        target = HydratedCanonicalTarget("semantic_object", canonical_object=SimpleNamespace(source_object_uuid="target"), graph_handle=target_handle)
        occurrence = HydratedGraphOccurrence(7, "semantic_identifier", "related", source, target)
        result = execution([request(0, "graph.relation_occurrence_lookup", HydratedGraphRelationResult((occurrence,)))])
        packet = assemble_retrieval_packet(result, PacketConfig(1)).packet
        selected = packet.selected_occurrences[0]
        self.assertEqual(len(packet.selected_occurrences), 1)
        self.assertEqual((selected.edge_id, selected.relation_class, selected.relation_name, selected.source_handle, selected.target_handle), (7, "semantic_identifier", "related", source_handle, target_handle))
        self.assertEqual((selected.source_target_ref, selected.target_target_ref), (packet.canonical_payloads[selected.source_target_ref].target_ref, packet.canonical_payloads[selected.target_target_ref].target_ref))
        self.assertEqual(len(packet.canonical_payloads), 2)

    def test_runtime_config_packet_field_has_no_constructor_default(self):
        from semantic_traversal.runtime.config import RuntimeConfig
        self.assertIs(RuntimeConfig.__dataclass_fields__["packet"].default, MISSING)
        with self.assertRaises(TypeError):
            RuntimeConfig(object(), object())

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
