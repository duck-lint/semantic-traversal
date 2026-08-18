import dataclasses
import unittest

from semantic_traversal.runtime.retrieval.candidates import compose_candidate_workspace
from semantic_traversal.runtime.retrieval.execution import (
    EXECUTION_CONTRACT_VERSION, RetrievalExecutionRequestResult, RetrievalExecutionResult,
)
from semantic_traversal.runtime.retrieval.selection import (
    CANDIDATE_SELECTION_CONTRACT_VERSION, CandidateSelectionError,
    select_candidates, serialize_candidate_selection,
)


def workspace(requests, status="succeeded"):
    execution = RetrievalExecutionResult(
        "execution", "conformance", "run", "proposal", "catalog", "package", "v1",
        "substrate", "vectors", "verification", EXECUTION_CONTRACT_VERSION, status,
        "started", "completed", tuple(
            RetrievalExecutionRequestResult(index, item[0], item[1], item[2], item[3])
            for index, item in enumerate(requests)
        ), None,
    )
    return compose_candidate_workspace(execution)


def exact(unit_ids):
    return ({"operator": "exact.equals"}, "succeeded", {"kind": "exact", "unit_ids": unit_ids}, None)


def relation(edge_id, source, target):
    return ({"operator": "graph.relation_occurrence_lookup"}, "succeeded", {
        "kind": "graph_relation_occurrences", "occurrences": [{
            "edge_id": edge_id, "relation_class": "x", "relation_name": "y",
            "source": {"node_kind": "semantic_unit", "identity": [source]},
            "target": {"node_kind": "semantic_unit", "identity": [target]},
        }],
    }, None)


def relation_lane(items):
    return ({"operator": "graph.relation_occurrence_lookup"}, "succeeded", {
        "kind": "graph_relation_occurrences", "occurrences": [{
            "edge_id": edge_id, "relation_class": "x", "relation_name": "y",
            "source": {"node_kind": "semantic_unit", "identity": [source]},
            "target": {"node_kind": "semantic_unit", "identity": [target]},
        } for edge_id, source, target in items],
    }, None)


class CandidateSelectionTests(unittest.TestCase):
    def test_breadth_first_request_lane_traversal(self):
        selected = select_candidates(workspace([
            exact([1, 2, 3, 4]), exact([5, 2, 6, 1]), exact([2]),
        ]), 5)
        self.assertEqual([item.target_ref.identity for item in selected.selected_candidates], [(1,), (5,), (2,), (3,), (6,)])
        self.assertEqual(tuple(item.identity for item in selected.omitted_candidate_refs), ((4,),))

    def test_selected_candidate_carries_complete_composed_support(self):
        selected = select_candidates(workspace([
            ({"operator": "temporal.before"}, "succeeded", {"kind": "temporal", "hits": [{"unit_id": 2, "date": "2026-04-03"}]}, None),
            ({"operator": "lexical.terms"}, "succeeded", {"kind": "lexical", "hits": [{"unit_id": 2, "score": -9.0}]}, None),
            ({"operator": "vector.semantic_similarity"}, "succeeded", {"kind": "vector", "hits": [{"target_kind": "semantic_unit", "target_identity": 2, "score": 0.01, "segment_ordinal": 0}]}, None),
        ]), 1)
        self.assertEqual(len(selected.selected_candidates), 1)
        self.assertEqual(len(selected.selected_candidates[0].supports), 3)

    def test_ordinary_two_endpoint_relation_admission(self):
        selected = select_candidates(workspace([relation(1, 10, 11)]), 2)
        self.assertEqual([item.target_ref.identity for item in selected.selected_candidates], [(10,), (11,)])
        self.assertEqual([item.admission_kind for item in selected.admissions], ["ordinary", "ordinary"])
        self.assertEqual([item.trigger_role for item in selected.admissions], ["source", "target"])
        self.assertFalse(selected.coverage.relation_endpoint_closure_used)

    def test_one_slot_graph_endpoint_closure_is_single_bounded_overflow(self):
        selected = select_candidates(workspace([
            exact([1]), exact([2]), relation(1, 10, 11), relation(2, 12, 13), exact([14]),
        ]), 3)
        self.assertEqual([item.target_ref.identity for item in selected.selected_candidates], [(1,), (2,), (10,), (11,)])
        self.assertEqual(selected.coverage.selected_candidate_count, 4)
        self.assertEqual(selected.coverage.max_candidates, 3)
        self.assertTrue(selected.coverage.relation_endpoint_closure_used)
        self.assertEqual([item.admission_kind for item in selected.admissions], ["ordinary", "ordinary", "ordinary", "relation_endpoint_closure"])
        self.assertEqual(selected.selected_candidates[-1].target_ref.identity, (11,))

    def test_no_second_closure_after_overflow(self):
        selected = select_candidates(workspace([
            exact([1]), exact([2]), relation(1, 10, 11), relation(2, 12, 13), exact([14]),
        ]), 3)
        self.assertEqual(selected.coverage.selected_candidate_count, 4)
        self.assertEqual(tuple(item.identity for item in selected.omitted_candidate_refs), ((12,), (13,), (14,)))

    def test_one_unseen_endpoint_is_ordinary_and_self_edge_is_one_candidate(self):
        one_unseen = select_candidates(workspace([exact([1]), relation(1, 1, 2)]), 2)
        self.assertEqual([item.target_ref.identity for item in one_unseen.selected_candidates], [(1,), (2,)])
        self.assertFalse(one_unseen.coverage.relation_endpoint_closure_used)
        self_edge = select_candidates(workspace([exact([1]), relation(2, 3, 3)]), 2)
        self.assertEqual([item.target_ref.identity for item in self_edge.selected_candidates], [(1,), (3,)])
        self.assertEqual(len(self_edge.admissions), 2)
        self.assertFalse(self_edge.coverage.relation_endpoint_closure_used)

    def test_zero_remaining_graph_and_zero_capacity(self):
        full = select_candidates(workspace([exact([1]), relation(1, 2, 3)]), 1)
        self.assertEqual(full.selected_candidates[0].target_ref.identity, (1,))
        self.assertEqual(tuple(item.identity for item in full.omitted_candidate_refs), ((2,), (3,)))
        zero = select_candidates(workspace([relation(1, 2, 3)]), 0)
        self.assertEqual(zero.selected_candidates, ())
        self.assertEqual(zero.admissions, ())
        self.assertFalse(zero.coverage.relation_endpoint_closure_used)
        self.assertTrue(zero.coverage.selection_truncated)

    def test_induced_and_omitted_endpoint_relations(self):
        induced = select_candidates(workspace([exact([1]), exact([2]), relation(1, 1, 2)]), 2)
        self.assertEqual([item.edge_id for item in induced.selected_relation_evidence], [1])
        omitted = select_candidates(workspace([exact([1]), relation(2, 1, 2)]), 1)
        self.assertEqual(omitted.selected_relation_evidence, ())

    def test_duplicate_relations_remain_distinct(self):
        selected = select_candidates(workspace([relation(10, 1, 2), relation(11, 1, 2)]), 2)
        self.assertEqual([item.edge_id for item in selected.selected_relation_evidence], [10, 11])

    def test_coverage_and_omitted_order(self):
        requests = [
            exact(list(range(1, 11))),
            relation_lane([(1, 1, 2), (2, 1, 4), (3, 5, 6), (4, 7, 8)]),
            exact([3]),
        ]
        selected = select_candidates(workspace(requests), 3)
        self.assertEqual((selected.coverage.workspace_candidate_count, selected.coverage.selected_candidate_count, selected.coverage.omitted_candidate_count), (10, 3, 7))
        self.assertEqual((selected.coverage.workspace_relation_count, selected.coverage.selected_relation_count, selected.coverage.omitted_relation_count), (4, 1, 3))
        self.assertEqual(tuple(item.identity for item in selected.omitted_candidate_refs), tuple((item,) for item in range(4, 11)))
        self.assertTrue(selected.coverage.selection_truncated)

    def test_admission_provenance_and_request_coverage(self):
        selected = select_candidates(workspace([exact([1]), relation(1, 2, 3)]), 3)
        self.assertEqual([(item.selection_ordinal, item.trigger_request_ordinal, item.trigger_occurrence_ordinal, item.trigger_role, item.admission_kind) for item in selected.admissions], [(0, 0, 0, "unary", "ordinary"), (1, 1, 0, "source", "ordinary"), (2, 1, 0, "target", "ordinary")])
        self.assertEqual(selected.requests[0].returned_occurrences, 1)
        self.assertEqual(selected.requests[0].status, "succeeded")

    def test_capacity_validation_and_workspace_contract(self):
        current = workspace([exact([1])])
        for capacity in (True, False, -1, 1.5, "3"):
            with self.subTest(capacity=capacity), self.assertRaises(CandidateSelectionError):
                select_candidates(current, capacity)
        self.assertEqual(select_candidates(current, 0).contract_version, CANDIDATE_SELECTION_CONTRACT_VERSION)
        broken = dataclasses.replace(current, contract_version="other")
        with self.assertRaises(CandidateSelectionError):
            select_candidates(broken, 1)

    def test_selection_is_score_independent_and_serialization_deterministic(self):
        def make(score):
            return workspace([({"operator": "lexical.terms"}, "succeeded", {"kind": "lexical", "hits": [{"unit_id": 1, "score": score}, {"unit_id": 2, "score": -score}]}, None)])
        first = select_candidates(make(0.1), 1)
        second = select_candidates(make(999.0), 1)
        self.assertEqual(first.selected_candidates[0].target_ref, second.selected_candidates[0].target_ref)
        self.assertNotEqual(first.selected_candidates[0].supports[0].lexical_score, second.selected_candidates[0].supports[0].lexical_score)
        self.assertEqual(serialize_candidate_selection(first), serialize_candidate_selection(select_candidates(make(0.1), 1)))

    def test_no_canonical_payload_or_packet_policy(self):
        selected = select_candidates(workspace([exact([1])]), 1)
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
        inspect(selected)


if __name__ == "__main__":
    unittest.main()
