import datetime as dt
import dataclasses
import unittest
from uuid import uuid4

from semantic_traversal.runtime.retrieval.candidates import (
    CandidateCompositionError, ExactSupport, GraphDiscoverySupport, LexicalSupport,
    TemporalSupport, VectorSupport, compose_candidate_workspace,
    serialize_candidate_workspace,
)
from semantic_traversal.runtime.retrieval.execution import (
    EXECUTION_CONTRACT_VERSION, RetrievalExecutionRequestResult, RetrievalExecutionResult,
)


def execution(requests, status="succeeded"):
    return RetrievalExecutionResult(
        "execution", "conformance", "run", "proposal", "catalog", "package", "v1",
        "substrate", "vectors", "verification", EXECUTION_CONTRACT_VERSION, status,
        "started", "completed", tuple(
            RetrievalExecutionRequestResult(index, item[0], item[1], item[2], item[3])
            for index, item in enumerate(requests)
        ), None,
    )


class CandidateWorkspaceTests(unittest.TestCase):
    def test_incomplete_execution_is_rejected(self):
        with self.assertRaises(CandidateCompositionError):
            compose_candidate_workspace(execution([], status="running"))

    def test_temporal_wire_date_is_exact_and_calendar_valid(self):
        for value in ("20260403", "2026-4-03", "2026-04-03T00:00:00", "2026-02-30"):
            result = execution([({"operator": "temporal.before"}, "succeeded", {"kind": "temporal", "hits": [{"unit_id": 1, "date": value}]}, None)])
            with self.subTest(value=value), self.assertRaises(CandidateCompositionError):
                compose_candidate_workspace(result)

    def test_all_four_target_identity_shapes_are_pinned(self):
        handles = [
            {"node_kind": "semantic_unit", "identity": [42]},
            {"node_kind": "semantic_object", "identity": ["object-x"]},
            {"node_kind": "semantic_region", "identity": ["object-x", ["Region", "Child"]]},
            {"node_kind": "scope", "identity": [["folder", "nested"]]},
        ]
        requests = [({"operator": "graph.discovery.terms"}, "succeeded", {"kind": "graph_discovery", "hits": [{"node": handle, "score": 0.0}]}, None) for handle in handles]
        workspace = compose_candidate_workspace(execution(requests))
        self.assertEqual([candidate.target_ref.identity for candidate in workspace.candidates], [
            (42,), ("object-x",), ("object-x", ("Region", "Child")), (("folder", "nested"),),
        ])
        self.assertTrue(all(isinstance(candidate.target_ref.identity, tuple) for candidate in workspace.candidates))

    def test_unit_occurrences_converge_without_fusion(self):
        result = execution([
            ({"operator": "exact.equals"}, "succeeded", {"kind": "exact", "unit_ids": [42]}, None),
            ({"operator": "lexical.terms"}, "succeeded", {"kind": "lexical", "hits": [{"unit_id": 42, "score": -2.5}]}, None),
            ({"operator": "temporal.before"}, "succeeded", {"kind": "temporal", "hits": [{"unit_id": 42, "date": "2026-04-03"}]}, None),
            ({"operator": "vector.semantic_similarity"}, "succeeded", {"kind": "vector", "hits": [{"target_kind": "semantic_unit", "target_identity": 42, "score": 0.83, "segment_ordinal": 0}]}, None),
        ])
        workspace = compose_candidate_workspace(result)
        self.assertEqual(len(workspace.candidates), 1)
        self.assertEqual(workspace.candidates[0].target_ref.identity, (42,))
        self.assertEqual(len(workspace.candidates[0].supports), 4)
        self.assertIsInstance(workspace.candidates[0].supports[0], ExactSupport)
        self.assertIsInstance(workspace.candidates[0].supports[1], LexicalSupport)
        self.assertIsInstance(workspace.candidates[0].supports[2], TemporalSupport)
        self.assertIsInstance(workspace.candidates[0].supports[3], VectorSupport)
        self.assertEqual(workspace.candidates[0].supports[1].lexical_score, -2.5)
        self.assertEqual(workspace.candidates[0].supports[2].temporal_date, dt.date(2026, 4, 3))
        self.assertEqual(workspace.candidates[0].supports[3].vector_score, 0.83)

    def test_graph_vector_convergence_and_relation_roles(self):
        object_uuid = str(uuid4())
        a, b = str(uuid4()), object_uuid
        handle_b = {"node_kind": "semantic_object", "identity": [b]}
        result = execution([
            ({"operator": "vector.semantic_similarity"}, "succeeded", {"kind": "vector", "hits": [{"target_kind": "semantic_object", "target_identity": b, "score": 0.4, "segment_ordinal": 0}]}, None),
            ({"operator": "graph.discovery.terms"}, "succeeded", {"kind": "graph_discovery", "hits": [{"node": handle_b, "score": -1.0}]}, None),
            ({"operator": "graph.relation_occurrence_lookup"}, "succeeded", {"kind": "graph_relation_occurrences", "occurrences": [{"edge_id": 7, "relation_class": "semantic_identifier", "relation_name": "book_read_today", "source": {"node_kind": "semantic_object", "identity": [a]}, "target": handle_b}]}, None),
        ])
        workspace = compose_candidate_workspace(result)
        self.assertEqual(len(workspace.candidates), 2)
        b_candidate = next(candidate for candidate in workspace.candidates if candidate.target_ref.identity == (b,))
        self.assertEqual(len(b_candidate.supports), 2)
        self.assertIsInstance(b_candidate.supports[1], GraphDiscoverySupport)
        relation = workspace.relation_evidence[0]
        self.assertEqual(relation.edge_id, 7)
        self.assertEqual(relation.target_ref, b_candidate.target_ref)
        self.assertEqual(workspace.requests[2].occurrences[0].target_refs, (relation.source_ref, relation.target_ref))

    def test_self_edge_duplicate_support_failures_and_zero_success(self):
        failure = {"kind": "surface_error", "surface": "exact.equals", "exception_type": "X", "message": "no"}
        result = execution([
            ({"operator": "exact.equals"}, "succeeded", {"kind": "exact", "unit_ids": [1, 1]}, None),
            ({"operator": "graph.relation_occurrence_lookup"}, "succeeded", {"kind": "graph_relation_occurrences", "occurrences": [{"edge_id": 1, "relation_class": "x", "relation_name": "y", "source": {"node_kind": "semantic_unit", "identity": [1]}, "target": {"node_kind": "semantic_unit", "identity": [1]}}]}, None),
            ({"operator": "exact.equals"}, "failed", None, failure),
            ({"operator": "exact.equals"}, "not_executed", None, {"kind": "prior_request_failed", "failed_ordinal": 2}),
            ({"operator": "exact.equals"}, "succeeded", {"kind": "exact", "unit_ids": []}, None),
        ])
        workspace = compose_candidate_workspace(result)
        self.assertEqual(len(workspace.candidates), 1)
        self.assertEqual(len(workspace.candidates[0].supports), 2)
        self.assertEqual(len(workspace.relation_evidence), 1)
        self.assertEqual(workspace.relation_evidence[0].source_ref, workspace.relation_evidence[0].target_ref)
        self.assertIsNone(workspace.requests[2].returned_occurrences)
        self.assertEqual(workspace.requests[4].returned_occurrences, 0)

    def test_duplicate_graph_relations_are_not_deduplicated(self):
        source = {"node_kind": "semantic_object", "identity": ["a"]}
        target = {"node_kind": "semantic_object", "identity": ["b"]}
        occurrences = [
            {"edge_id": 10, "relation_class": "x", "relation_name": "y", "source": source, "target": target},
            {"edge_id": 11, "relation_class": "x", "relation_name": "y", "source": source, "target": target},
        ]
        workspace = compose_candidate_workspace(execution([({"operator": "graph.relation_occurrence_lookup"}, "succeeded", {"kind": "graph_relation_occurrences", "occurrences": occurrences}, None)]))
        self.assertEqual(len(workspace.candidates), 2)
        self.assertEqual([item.edge_id for item in workspace.relation_evidence], [10, 11])
        self.assertEqual([item.occurrence_ordinal for item in workspace.relation_evidence], [0, 1])

    def test_first_encounter_support_and_relation_order_is_representation_order(self):
        source = {"node_kind": "semantic_object", "identity": ["a"]}
        target = {"node_kind": "semantic_object", "identity": ["b"]}
        result = execution([
            ({"operator": "exact.equals"}, "succeeded", {"kind": "exact", "unit_ids": [2, 1]}, None),
            ({"operator": "exact.equals"}, "succeeded", {"kind": "exact", "unit_ids": [1, 2]}, None),
            ({"operator": "graph.relation_occurrence_lookup"}, "succeeded", {"kind": "graph_relation_occurrences", "occurrences": [{"edge_id": 1, "relation_class": "x", "relation_name": "y", "source": source, "target": target}]}, None),
            ({"operator": "graph.relation_occurrence_lookup"}, "succeeded", {"kind": "graph_relation_occurrences", "occurrences": [{"edge_id": 2, "relation_class": "x", "relation_name": "y", "source": source, "target": target}]}, None),
        ])
        first, second = compose_candidate_workspace(result), compose_candidate_workspace(result)
        self.assertEqual([candidate.target_ref.identity for candidate in first.candidates], [(2,), (1,), ("a",), ("b",)])
        self.assertEqual([support.occurrence_ordinal for support in first.candidates[0].supports], [0, 1])
        self.assertEqual([item.occurrence_ordinal for item in first.relation_evidence], [0, 0])
        self.assertEqual(serialize_candidate_workspace(first), serialize_candidate_workspace(second))

    def test_workspace_has_no_canonical_payload_fields(self):
        workspace = compose_candidate_workspace(execution([({"operator": "exact.equals"}, "succeeded", {"kind": "exact", "unit_ids": [1]}, None)]))
        forbidden = {"CanonicalUnit", "CanonicalObject", "CanonicalRegion", "raw_markdown", "parsed_text", "inherited_identifiers", "owning_object"}
        def inspect(value):
            if dataclasses.is_dataclass(value):
                self.assertNotIn(type(value).__name__, forbidden)
                for field in dataclasses.fields(value):
                    self.assertNotIn(field.name, forbidden)
                    inspect(getattr(value, field.name))
            elif isinstance(value, dict):
                for item in value.values(): inspect(item)
            elif isinstance(value, (tuple, list)):
                for item in value: inspect(item)
        inspect(workspace)

    def test_workspace_nested_state_is_immutable(self):
        result = execution([
            ({"operator": "exact.equals", "nested": {"value": 1}}, "succeeded", {"kind": "exact", "unit_ids": [1]}, None),
            ({"operator": "exact.equals"}, "failed", None, {"kind": "failure", "nested": {"value": 2}}),
        ], status="failed")
        workspace = compose_candidate_workspace(result)
        with self.assertRaises(TypeError):
            workspace.requests[0].request["nested"] = {}
        with self.assertRaises(TypeError):
            workspace.requests[1].failure["nested"] = {}
        with self.assertRaises(TypeError):
            workspace.requests[0].occurrences[0] = None

    def test_malformed_graph_identities_fail_without_lookup(self):
        cases = [
            {"node_kind": "unknown", "identity": [1]},
            {"node_kind": "semantic_unit", "identity": []},
            {"node_kind": "semantic_object", "identity": []},
            {"node_kind": "semantic_region", "identity": ["object"]},
            {"node_kind": "semantic_region", "identity": ["object", []]},
            {"node_kind": "scope", "identity": []},
            {"node_kind": "scope", "identity": [[]]},
        ]
        for handle in cases:
            result = execution([({"operator": "graph.discovery.terms"}, "succeeded", {"kind": "graph_discovery", "hits": [{"node": handle, "score": 0.0}]}, None)])
            with self.subTest(handle=handle), self.assertRaises(CandidateCompositionError):
                compose_candidate_workspace(result)

    def test_unbounded_and_deterministic_serialization(self):
        hits = [{"target_kind": "semantic_unit", "target_identity": index, "score": 0.1, "segment_ordinal": 0} for index in range(1000)]
        result = execution([({"operator": "vector.semantic_similarity"}, "succeeded", {"kind": "vector", "hits": hits}, None)])
        first = compose_candidate_workspace(result)
        second = compose_candidate_workspace(result)
        self.assertEqual(len(first.candidates), 1000)
        self.assertEqual(serialize_candidate_workspace(first), serialize_candidate_workspace(second))

    def test_malformed_success_is_explicit(self):
        result = execution([({"operator": "exact.equals"}, "succeeded", {"kind": "lexical", "hits": []}, None)])
        with self.assertRaises(CandidateCompositionError):
            compose_candidate_workspace(result)


if __name__ == "__main__":
    unittest.main()
