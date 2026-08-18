import datetime as dt
import unittest
from uuid import uuid4

from semantic_traversal.projection.graph import GraphHandle
from semantic_traversal.runtime.retrieval.candidates import (
    CandidateCompositionError, ExactSupport, GraphDiscoverySupport, LexicalSupport,
    TemporalSupport, VectorSupport, compose_candidate_workspace,
    serialize_candidate_workspace,
)
from semantic_traversal.runtime.retrieval.execution import (
    EXECUTION_CONTRACT_VERSION, RetrievalExecutionRequestResult, RetrievalExecutionResult,
)


def execution(requests):
    return RetrievalExecutionResult(
        "execution", "conformance", "run", "proposal", "catalog", "package", "v1",
        "substrate", "vectors", "verification", EXECUTION_CONTRACT_VERSION, "succeeded",
        "started", "completed", tuple(
            RetrievalExecutionRequestResult(index, item[0], item[1], item[2], item[3])
            for index, item in enumerate(requests)
        ), None,
    )


class CandidateWorkspaceTests(unittest.TestCase):
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
