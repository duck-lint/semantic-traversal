from __future__ import annotations

import unittest
from pathlib import Path

from semantic_traversal.config import load_runtime_config
from semantic_traversal.retrieval_resolver import validate_plan_executability
from semantic_traversal.runtime import _build_synthesis_traversal_summary


class PlanIntegrityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = load_runtime_config(repo_root=Path(__file__).resolve().parent.parent)

    def plan(self, *operators: str, **fields):
        return {"retrieval_layers": [{"operator": operator} for operator in operators], **fields}

    def assert_executable(self, plan, operator: str) -> None:
        result = validate_plan_executability(planner_retrieval_plan=plan, config=self.config)
        self.assertEqual(result["status"], "executable")
        self.assertIn(operator, result["executable_operators"])

    def test_empty_and_unsupported_plans_are_non_executable(self) -> None:
        self.assertEqual(validate_plan_executability(planner_retrieval_plan={}, config=self.config)["status"], "non_executable")
        result = validate_plan_executability(planner_retrieval_plan=self.plan("future_surface"), config=self.config)
        self.assertEqual(result["operator_results"][0]["status"], "invalid_input")
        self.assertEqual(result["status"], "non_executable")

    def test_completeness_is_independent_of_executability(self) -> None:
        result = validate_plan_executability(planner_retrieval_plan={"evidence_requirements": []}, config=self.config)
        self.assertEqual(result["status"], "non_executable")
        self.assertFalse(result["has_retrieval_intent"])

    def test_operator_input_contracts(self) -> None:
        cases = [
            (self.plan("exact_chunk_search"), "exact_chunk_search", False),
            (self.plan("exact_chunk_search", literal_terms=[{"term": "needle"}]), "exact_chunk_search", True),
            (self.plan("lexical_chunk_search"), "lexical_chunk_search", False),
            (self.plan("lexical_chunk_search", lexical_queries=["needle"]), "lexical_chunk_search", True),
            (self.plan("lexical_chunk_search", semantic_queries=["meaning"]), "lexical_chunk_search", True),
            (self.plan("vector_search"), "vector_search", False),
            (self.plan("vector_search", semantic_queries=["meaning"]), "vector_search", True),
            (self.plan("graph_expand"), "graph_expand", False),
            (self.plan("graph_expand", graph_seeds=["node"]), "graph_expand", True),
            (self.plan("graph_expand", semantic_queries=["meaning"]), "graph_expand", True),
            (self.plan("temporal_retrieve"), "temporal_retrieve", False),
            (self.plan("temporal_retrieve", semantic_queries=["meaning"]), "temporal_retrieve", True),
            (self.plan("temporal_retrieve", lexical_queries=["needle"]), "temporal_retrieve", True),
        ]
        for plan, operator, expected in cases:
            with self.subTest(operator=operator, plan=plan):
                result = validate_plan_executability(planner_retrieval_plan=plan, config=self.config)
                self.assertEqual(result["operator_results"][0]["status"] == "executable", expected)

    def test_synthesis_summary_is_whitelist_only(self) -> None:
        manifest = {
            "resource_inventory_summary": {"alias": "INVENTORY_CANARY", "path": "PATH_CANARY"},
            "inventory_diagnostics": {"title": "DIAGNOSTIC_CANARY"},
            "execution": {"layers_executed": ["lexical_chunk_search"], "layers_skipped": []},
            "candidate_counts": {"lexical": 2}, "selected_counts": {"lexical": 1},
            "coverage": {"exact_status": "not_requested", "literal_terms": ["QUERY_CANARY"], "negative_claims_allowed": False},
            "limits": ["bounded"], "plan_completeness": {"status": "complete"},
            "plan_executability": {"status": "executable"}, "unsupported_layer_requests": [],
            "layer_manifests": {"lexical": {"status": "completed_with_candidates", "candidate_count": 2, "candidates": ["CANDIDATE_CANARY"]}},
            "fusion": {"selector": "ordinal_note_breadth", "candidate_count": 2, "selected_count": 1, "selected_unique_note_count": 1, "max_selected_chunks_per_note": 1, "required_reservation": {}},
            "scope_resolution": {"bound_requests": [], "hard": {}, "preferred": {}},
        }
        summary = _build_synthesis_traversal_summary(manifest)
        text = repr(summary)
        self.assertIn("layers_executed", summary["execution"])
        self.assertIn("candidate_counts", summary)
        self.assertNotIn("INVENTORY_CANARY", text)
        self.assertNotIn("QUERY_CANARY", text)
        self.assertNotIn("CANDIDATE_CANARY", text)
        self.assertNotIn("resource_inventory_summary", text)


if __name__ == "__main__":
    unittest.main()
