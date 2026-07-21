from __future__ import annotations

import json
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

    def test_explicit_empty_planner_lists_are_not_replaced(self) -> None:
        plan, diagnostics = self.canonicalize({
            "scope_requests": [],
            "concepts": [],
            "resolved_referents": [],
            "literal_terms": [],
            "semantic_queries": [],
            "lexical_queries": [],
            "graph_seeds": [],
            "retrieval_layers": [],
        })
        for field in ("scope_requests", "concepts", "resolved_referents", "literal_terms", "semantic_queries", "lexical_queries", "graph_seeds", "retrieval_layers"):
            self.assertEqual(plan[field], [], field)
        self.assertEqual(diagnostics["explicit_empty_fields"], [
            "scope_requests", "concepts", "resolved_referents", "literal_terms",
            "semantic_queries", "lexical_queries", "graph_seeds", "retrieval_layers",
        ])

    def test_missing_lists_use_fallback_and_are_diagnosed(self) -> None:
        plan, diagnostics = self.canonicalize({"intent_type": "semantic_traversal"})
        self.assertTrue(plan["semantic_queries"])
        self.assertTrue(plan["lexical_queries"])
        self.assertTrue(plan["retrieval_layers"])
        self.assertIn("semantic_queries", diagnostics["defaulted_missing_fields"])

    def test_invalid_list_types_are_diagnosed_and_fall_back(self) -> None:
        plan, diagnostics = self.canonicalize({
            "semantic_queries": "origin",
            "lexical_queries": {"term": "concept"},
            "graph_seeds": 7,
            "retrieval_layers": "vector_search",
        })
        self.assertTrue(plan["semantic_queries"])
        self.assertTrue(plan["lexical_queries"])
        self.assertTrue(plan["graph_seeds"])
        self.assertEqual({item["field"] for item in diagnostics["invalid_planner_fields"]}, {
            "semantic_queries", "lexical_queries", "graph_seeds", "retrieval_layers",
        })

    def test_subject_bearing_fallback_uses_model_concepts_before_query_tokens(self) -> None:
        packet = _canonicalize_response_payload(
            "Where did concept X come from?",
            {
                "query": "origin_of_idea",
                "planner_retrieval_plan": {
                    "concepts": ["concept X"],
                    "semantic_queries": [],
                    "lexical_queries": [],
                    "graph_seeds": [],
                    "retrieval_layers": [{"operator": "temporal_retrieve", "required": True, "mode": "ordered"}],
                },
            },
            planner_defaults=self.defaults,
        )
        self.assertIn("concept X", packet["query"])
        self.assertEqual(packet["planner_retrieval_plan"]["semantic_queries"], [])
        self.assertEqual(packet["planner_retrieval_plan"]["lexical_queries"], [])
        self.assertEqual(packet["planner_retrieval_plan"]["graph_seeds"], [])
        self.assertEqual(packet["planner_diagnostics"]["subject_preservation_status"]["status"], "incomplete")
        self.assertEqual(packet["planner_diagnostics"]["fallback_query_sources"]["subject_candidates"], "planner_concepts")

    def test_non_subject_intent_queries_receive_structural_subject_context(self) -> None:
        packet = _canonicalize_response_payload(
            "Explain concept X development",
            {
                "query": "origin_of_idea",
                "planner_retrieval_plan": {
                    "concepts": ["concept X"],
                    "semantic_queries": ["origin", "development"],
                    "lexical_queries": ["precursor"],
                    "graph_seeds": ["origin_of_idea"],
                    "retrieval_layers": [{"operator": "graph_expand"}],
                },
            },
            planner_defaults=self.defaults,
        )
        plan = packet["planner_retrieval_plan"]
        self.assertTrue(all("concept X" in value for value in plan["semantic_queries"] + plan["lexical_queries"] + plan["graph_seeds"]))
        self.assertEqual(packet["planner_diagnostics"]["subject_preservation_status"]["status"], "subject_preserved")
        self.assertTrue(packet["planner_diagnostics"]["subject_query_adjustments"])

    def test_empty_graph_seeds_do_not_manufacture_intent_seed(self) -> None:
        packet = _canonicalize_response_payload(
            "Explain concept X origin",
            {
                "query": "origin_of_idea",
                "planner_retrieval_plan": {
                    "concepts": ["concept X"],
                    "graph_seeds": [],
                    "retrieval_layers": [{"operator": "graph_expand"}],
                },
            },
            planner_defaults=self.defaults,
        )
        self.assertEqual(packet["planner_retrieval_plan"]["graph_seeds"], [])
        self.assertNotIn("origin_of_idea", packet["planner_retrieval_plan"]["graph_seeds"])

    def test_overlapping_subject_candidates_preserve_valid_queries_and_related_seed(self) -> None:
        packet = _canonicalize_response_payload(
            "Explain concept X development",
            {
                "query": "origin of concept X",
                "planner_retrieval_plan": {
                    "concepts": ["concept X", "precursors of concept X"],
                    "semantic_queries": ["origin of concept X", "development of concept X"],
                    "lexical_queries": ["concept X", "precursor concepts"],
                    "graph_seeds": ["Framework Y"],
                    "retrieval_layers": [{"operator": "graph_expand"}],
                },
            },
            planner_defaults=self.defaults,
        )
        plan = packet["planner_retrieval_plan"]
        self.assertEqual(plan["semantic_queries"], ["origin of concept X", "development of concept X"])
        self.assertEqual(plan["lexical_queries"], ["concept X", "precursor concepts regarding concept X"])
        self.assertEqual(plan["graph_seeds"], ["Framework Y"])
        diagnostics = packet["planner_diagnostics"]
        self.assertEqual(diagnostics["subject_candidates"], ["concept X", "precursors of concept X"])
        self.assertEqual(diagnostics["minimal_subject_basis"], ["concept X"])
        self.assertEqual(diagnostics["preserved_graph_seeds"], ["Framework Y"])
        self.assertEqual(diagnostics["overlapping_subject_candidates"][0]["shorter"], "concept X")

    def test_already_subject_bearing_queries_are_preserved_byte_for_byte(self) -> None:
        semantic = "Concept X early formulation"
        lexical = "precursors of concept X"
        packet = _canonicalize_response_payload(
            "Explain concept X",
            {
                "query": "development of concept X",
                "planner_retrieval_plan": {
                    "concepts": ["concept X", "precursors of concept X"],
                    "semantic_queries": [semantic],
                    "lexical_queries": [lexical],
                    "graph_seeds": ["Source Title"],
                },
            },
            planner_defaults=self.defaults,
        )
        self.assertEqual(packet["planner_retrieval_plan"]["semantic_queries"], [semantic])
        self.assertEqual(packet["planner_retrieval_plan"]["lexical_queries"], [lexical])
        self.assertEqual(packet["planner_retrieval_plan"]["graph_seeds"], ["Source Title"])
        self.assertEqual(packet["planner_diagnostics"]["repaired_subjectless_fields"], [])

    def test_subjectless_queries_use_minimal_basis_once_and_are_idempotent(self) -> None:
        payload = {
            "query": "origin_of_idea",
            "planner_retrieval_plan": {
                "concepts": ["concept X", "precursors of concept X"],
                "semantic_queries": ["origin"],
                "lexical_queries": ["precursor concepts"],
                "graph_seeds": ["origin_of_idea"],
                "retrieval_layers": [{"operator": "graph_expand"}],
            },
        }
        first = _canonicalize_response_payload("Explain concept X origin", payload, planner_defaults=self.defaults)
        second = _canonicalize_response_payload("Explain concept X origin", first, planner_defaults=self.defaults)
        first_plan = first["planner_retrieval_plan"]
        self.assertEqual(first_plan["semantic_queries"], ["origin regarding concept X"])
        self.assertEqual(first_plan["lexical_queries"], ["precursor concepts regarding concept X"])
        self.assertEqual(first_plan["graph_seeds"], ["concept X"])
        self.assertNotIn("regarding concept X regarding", json.dumps(first_plan))
        self.assertEqual(second["planner_retrieval_plan"], first_plan)

    def test_graph_seed_empty_remains_empty_but_intent_seed_is_repaired(self) -> None:
        empty = _canonicalize_response_payload(
            "Explain concept X",
            {"planner_retrieval_plan": {"concepts": ["concept X"], "graph_seeds": []}},
            planner_defaults=self.defaults,
        )
        self.assertEqual(empty["planner_retrieval_plan"]["graph_seeds"], [])
        repaired = _canonicalize_response_payload(
            "Explain concept X",
            {"planner_retrieval_plan": {"concepts": ["concept X"], "graph_seeds": ["graph_relation"]}},
            planner_defaults=self.defaults,
        )
        self.assertEqual(repaired["planner_retrieval_plan"]["graph_seeds"], ["concept X"])
        self.assertEqual(repaired["planner_diagnostics"]["rejected_or_repaired_graph_seeds"][0]["action"], "repaired_graph_seed")

    def test_independent_relation_subjects_remain_represented(self) -> None:
        packet = _canonicalize_response_payload(
            "Compare concept X and concept Y",
            {
                "intent": "comparison",
                "query": "relation between concept X and concept Y",
                "planner_retrieval_plan": {
                    "concepts": ["concept X", "concept Y"],
                    "semantic_queries": ["relation between concept X and concept Y"],
                    "lexical_queries": ["concept X and concept Y"],
                },
            },
            planner_defaults=self.defaults,
        )
        plan = packet["planner_retrieval_plan"]
        self.assertEqual(plan["semantic_queries"], ["relation between concept X and concept Y"])
        self.assertEqual(plan["lexical_queries"], ["concept X and concept Y"])
        self.assertEqual(packet["planner_diagnostics"]["minimal_subject_basis"], ["concept X", "concept Y"])

    def test_prompt_contract_mentions_subject_preservation_and_rejects_identifiers(self) -> None:
        from semantic_traversal.semantic_compiler import _render_ollama_prompt
        prompt = _render_ollama_prompt(
            packet={"raw_user_input": "subject probe", "resource_inventory_summary": {}},
            template=self.config.semantic_compiler_prompt_template,
            planner_defaults=self.defaults,
        )
        self.assertIn("human-readable retrieval text", prompt)
        self.assertIn("preserves the principal subject", prompt)
        self.assertIn("origin_of_idea", prompt)
        self.assertIn("concept X origin", prompt)
        self.assertIn("evidence_requirements", prompt)
        self.assertIn("request_binding", prompt)
        self.assertIn("raw_user_input_sha256", prompt)
        self.assertIn("chronology", prompt)

    def test_evidence_requirements_are_canonical_schema_fields(self) -> None:
        packet = _canonicalize_response_payload(
            "Where did concept X develop?",
            {
                "query": "development of concept X",
                "planner_retrieval_plan": {
                    "concepts": ["concept X"],
                    "evidence_requirements": ["chronology", "chronology"],
                    "semantic_queries": ["development of concept X"],
                    "lexical_queries": ["development of concept X"],
                    "retrieval_layers": [{"operator": "temporal_retrieve", "required": True, "mode": "ordered"}],
                },
            },
            planner_defaults=self.defaults,
        )
        plan = packet["planner_retrieval_plan"]
        self.assertEqual(plan["evidence_requirements"], ["chronology"])
        self.assertNotIn("selection_policy", plan)
        self.assertNotIn("claim_policy", plan)

    def test_unknown_evidence_requirement_is_structurally_diagnosed(self) -> None:
        packet = _canonicalize_response_payload(
            "Explain concept X",
            {"planner_retrieval_plan": {"evidence_requirements": ["topic_specific_causality"]}},
            planner_defaults=self.defaults,
        )
        self.assertTrue(any(item["reason"] == "unsupported_requirement_enum" for item in packet["planner_diagnostics"]["invalid_planner_fields"]))


if __name__ == "__main__":
    unittest.main()
