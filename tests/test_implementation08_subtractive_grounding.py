from __future__ import annotations

import json
import unittest
from pathlib import Path

from semantic_traversal.config import load_runtime_config
from semantic_traversal.retrieval_resolver import validate_plan_completeness
from semantic_traversal.retrieval_plan import build_default_retrieval_plan, canonicalize_retrieval_plan
from semantic_traversal.runtime import _canonicalize_compiler_packet, _repair_incomplete_compiler_plan
from semantic_traversal.semantic_compiler import SemanticCompilerResponse


REPO_ROOT = Path(__file__).resolve().parents[1]


class _RepairBackend:
    mode_name = "fake"

    def __init__(self, response: SemanticCompilerResponse) -> None:
        self.response = response
        self.calls: list[dict[str, object]] = []

    def compile_turn(self, packet: dict[str, object]) -> SemanticCompilerResponse:
        self.calls.append(packet)
        return self.response


class SubtractiveGroundingProductionSeamTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_runtime_config(repo_root=REPO_ROOT)

    def test_query_level_context_reaches_runtime_canonical_grounding(self) -> None:
        packet = {
            "concepts": ["query context"],
            "resolved_referents": [],
            "literal_terms": [],
            "semantic_queries": ["query context"],
            "lexical_queries": ["query context"],
            "graph_seeds": [],
            "evidence_requirements": ["chronology"],
            "retrieval_layers": [{"operator": "temporal_retrieve", "required": True}],
        }
        fallback = build_default_retrieval_plan(
            raw_user_input="query context chronology", query="query context", concepts=["query context"],
            graph_seeds=[], resolved_referents=[], planner_defaults=self.config.retrieval_planner_defaults,
        )
        canonical, _ = canonicalize_retrieval_plan(
            packet, fallback=fallback, planner_defaults=self.config.retrieval_planner_defaults,
            raw_user_input="query context chronology",
        )
        self.assertEqual(validate_plan_completeness(planner_retrieval_plan=canonical, config=self.config)["status"], "complete")

    def test_malformed_exact_contract_uses_existing_one_shot_repair(self) -> None:
        initial_payload = {
            "raw_user_input": "find target phrase", "intent": "exact_search", "query": "target phrase",
            "entities": [], "relations": [], "resolved_referents": [], "limitations": [],
            "planner_retrieval_plan": {
                "intent_type": "exact_search", "concepts": [], "resolved_referents": [],
                "literal_terms": ["target phrase"], "evidence_requirements": ["literal_exhaustive"],
                "semantic_queries": [], "lexical_queries": [], "graph_seeds": [],
                "retrieval_layers": [{"operator": "exact_chunk_search", "required": True}],
            },
        }
        repaired_payload = json.loads(json.dumps(initial_payload))
        repaired_payload["planner_retrieval_plan"]["literal_terms"] = [{
            "term": "target phrase", "match": "case_insensitive_substring", "required": True,
        }]
        repaired_payload["planner_retrieval_plan"]["retrieval_layers"] = [{
            "operator": "exact_chunk_search", "required": True, "return_total_count": True,
        }]
        initial_response = SemanticCompilerResponse(initial_payload, json.dumps(initial_payload), {}, {}, "parsed")
        repaired_response = SemanticCompilerResponse(repaired_payload, json.dumps(repaired_payload), {}, {}, "parsed")
        backend = _RepairBackend(repaired_response)
        packet, _ = _canonicalize_compiler_packet(
            raw_user_input="find target phrase", prior_thread_state={}, active_focus={}, recent_semantic_turns=[],
            payload=initial_payload, config=self.config,
        ), "parsed"
        repaired_packet, status, _, record = _repair_incomplete_compiler_plan(
            raw_user_input="find target phrase", compiler_request={"instruction": "compile"},
            compiler_backend=backend, compiler_response=initial_response, semantic_compiler_packet=packet,
            prior_thread_state={}, active_focus={}, recent_semantic_turns=[], config=self.config,
            resource_inventory_summary={},
        )
        self.assertEqual(len(backend.calls), 1)
        self.assertEqual(status, "parsed")
        self.assertEqual(record["outcome"], "complete")
        self.assertEqual(repaired_packet["planner_retrieval_plan"]["literal_terms"][0]["required"], True)
        self.assertNotIn("raw_type", repaired_packet["planner_retrieval_plan"]["literal_terms"][0])


if __name__ == "__main__":
    unittest.main()
