from __future__ import annotations

import unittest
from pathlib import Path

from semantic_traversal.config import load_runtime_config
from semantic_traversal.retrieval_plan import build_default_retrieval_plan, canonicalize_retrieval_plan
from semantic_traversal.semantic_compiler import _canonicalize_response_payload


REPO_ROOT = Path(__file__).resolve().parent.parent


class CompilerSchemaContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_runtime_config(repo_root=REPO_ROOT)
        self.defaults = self.config.retrieval_planner_defaults
        self.fallback = build_default_retrieval_plan(
            raw_user_input="explain a concept",
            query="explain a concept",
            concepts=["concept"],
            scope_requests=[],
            graph_seeds=["explain a concept"],
            planner_defaults=self.defaults,
        )

    def canonicalize(self, plan: dict) -> tuple[dict, dict]:
        return canonicalize_retrieval_plan(
            plan,
            fallback=self.fallback,
            planner_defaults=self.defaults,
            raw_user_input="explain a concept",
        )

    def test_canonical_model_shape_excludes_runtime_policy(self) -> None:
        plan, diagnostics = self.canonicalize({
            "intent_type": "semantic_traversal",
            "scope_requests": ["journal"],
            "concepts": ["concept"],
            "semantic_queries": ["concept"],
            "retrieval_layers": [{"operator": "vector_search", "required": False, "limit": 7}],
            "selection_policy": {"max_chunks": 1},
            "claim_policy": {"coverage_claims_allowed": True},
        })
        self.assertNotIn("selection_policy", plan)
        self.assertNotIn("claim_policy", plan)
        self.assertEqual(
            [item["field"] for item in diagnostics["retired_planner_fields"]],
            ["claim_policy", "selection_policy"],
        )

    def test_supported_fields_are_retained_without_hidden_defaults(self) -> None:
        plan, _ = self.canonicalize({
            "intent_type": "chronological_development",
            "literal_terms": [{"term": "alpha", "match": "case_sensitive_substring", "required": True}],
            "lexical_queries": ["alpha"],
            "retrieval_layers": [
                {"operator": "exact_chunk_search", "required": True, "return_total_count": True},
                {"operator": "lexical_chunk_search", "mode": "all_tokens", "limit": 5},
                {"operator": "graph_expand", "depth": 2},
                {"operator": "temporal_retrieve", "required": True, "mode": "earliest", "anchor_types": ["journal_entry"], "authorities": ["explicit_primary"]},
            ],
        })
        self.assertEqual(plan["literal_terms"][0]["match"], "case_sensitive_substring")
        self.assertTrue(plan["literal_terms"][0]["required"])
        self.assertEqual(plan["retrieval_layers"][1]["mode"], "all_tokens")
        self.assertEqual(plan["retrieval_layers"][2]["depth"], 2)
        self.assertEqual(plan["retrieval_layers"][3]["mode"], "earliest")

    def test_invalid_values_remain_visible_for_runtime_diagnostics(self) -> None:
        plan, _ = self.canonicalize({
            "retrieval_layers": [
                {"operator": "vector_search", "limit": "not-a-number"},
                {"operator": "graph_expand", "depth": "not-a-depth"},
            ]
        })
        self.assertEqual(plan["retrieval_layers"][0]["limit"], "not-a-number")
        self.assertEqual(plan["retrieval_layers"][1]["depth"], "not-a-depth")

    def test_duplicate_requests_are_deterministic(self) -> None:
        plan, _ = self.canonicalize({
            "scope_requests": ["journal", "journal", "personal_reflection"],
            "semantic_queries": ["one", "one", "two"],
            "retrieval_layers": [
                {"operator": "vector_search"},
                {"operator": "vector_search"},
            ],
        })
        self.assertEqual(plan["scope_requests"], ["journal", "personal_reflection"])
        self.assertEqual(plan["semantic_queries"], ["one", "two"])
        self.assertEqual(len(plan["retrieval_layers"]), 2)

    def test_raw_user_input_is_not_normalized_by_schema(self) -> None:
        raw = "  Preserve   this exact input: Δ  "
        packet = _canonicalize_response_payload(
            raw,
            {"query": "preserve", "planner_retrieval_plan": {"intent_type": "semantic_traversal"}},
            planner_defaults=self.defaults,
        )
        self.assertEqual(packet["raw_user_input"], raw)

    def test_legacy_runtime_policy_is_diagnosed_without_affecting_plan(self) -> None:
        packet = _canonicalize_response_payload(
            "explain a concept",
            {
                "planner_retrieval_plan": {
                    "selection_policy": {"max_chunks": 1},
                    "claim_policy": {"coverage_claims_allowed": True},
                }
            },
            planner_defaults=self.defaults,
        )
        self.assertNotIn("selection_policy", packet["planner_retrieval_plan"])
        self.assertNotIn("claim_policy", packet["planner_retrieval_plan"])
        self.assertEqual(
            [item["field"] for item in packet["planner_diagnostics"]["retired_planner_fields"]],
            ["claim_policy", "selection_policy"],
        )


if __name__ == "__main__":
    unittest.main()
