from __future__ import annotations

import inspect
import unittest
from pathlib import Path

from semantic_traversal.config import load_runtime_config
from semantic_traversal.runtime import (
    _canonicalize_compiler_packet,
    _deterministic_semantic_packet,
    _graph_seed_values,
)
from semantic_traversal.semantic_compiler import _canonicalize_response_payload


REPO_ROOT = Path(__file__).resolve().parents[1]


class RetrievalContextBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = load_runtime_config(repo_root=REPO_ROOT)

    def test_graph_seed_helper_accepts_only_current_plan_and_config(self) -> None:
        parameters = inspect.signature(_graph_seed_values).parameters
        self.assertEqual(set(parameters), {"planner_retrieval_plan", "config"})
        seeds = _graph_seed_values(
            planner_retrieval_plan={
                "graph_seeds": ["current graph target"],
                "semantic_queries": ["current semantic target"],
            },
            config=self.config,
        )
        self.assertEqual(
            seeds,
            [
                ("graph_seeds", "current graph target"),
                ("semantic_queries", "current semantic target"),
            ],
        )

    def test_self_contained_fallback_does_not_use_previous_user_input(self) -> None:
        packet = _deterministic_semantic_packet(
            raw_user_input="What is concept beta?",
            prior_thread_state={"latest_user_input": "old concept alpha", "active_focus": {"query": "old concept alpha"}},
            active_focus={"query": "old concept alpha", "graph_seeds": ["old concept alpha"]},
            recent_semantic_turns=[],
            config=self.config,
        )
        plan = packet["planner_retrieval_plan"]
        self.assertEqual(plan["graph_seeds"], ["what concept beta"])
        self.assertNotIn("old concept alpha", str(plan))

    def test_runtime_canonicalization_does_not_append_prior_focus(self) -> None:
        payload = {
            "intent": "current request",
            "query": "current concept beta",
            "entities": [],
            "relations": [],
            "resolved_referents": ["current concept beta"],
            "planner_retrieval_plan": {
                "intent_type": "semantic_traversal",
                "scope_requests": [],
                "concepts": ["current concept beta"],
                "resolved_referents": ["current concept beta"],
                "literal_terms": [],
                "semantic_queries": ["current concept beta development"],
                "lexical_queries": [],
                "graph_seeds": ["current concept beta"],
                "retrieval_layers": [],
            },
        }
        packet = _canonicalize_compiler_packet(
            raw_user_input="What is concept beta?",
            prior_thread_state={"active_focus": {"concepts": ["old concept alpha"]}},
            active_focus={"concepts": ["old concept alpha"]},
            recent_semantic_turns=[{"concepts": ["old concept alpha"]}],
            payload=payload,
            config=self.config,
        )
        plan = packet["planner_retrieval_plan"]
        self.assertEqual(plan["resolved_referents"], ["current concept beta"])
        self.assertEqual(plan["semantic_queries"], ["current concept beta development"])
        self.assertNotIn("old concept alpha", str(plan))

    def test_semantic_compiler_canonicalization_does_not_append_prior_focus(self) -> None:
        payload = {
            "intent": "current request",
            "query": "current concept beta",
            "entities": [],
            "relations": [],
            "resolved_referents": ["current concept beta"],
            "planner_retrieval_plan": {
                "intent_type": "semantic_traversal",
                "scope_requests": [],
                "concepts": ["current concept beta"],
                "resolved_referents": ["current concept beta"],
                "literal_terms": [],
                "semantic_queries": ["current concept beta development"],
                "lexical_queries": [],
                "graph_seeds": ["current concept beta"],
                "retrieval_layers": [],
            },
        }
        result = _canonicalize_response_payload(
            "What is concept beta?",
            payload,
            planner_defaults=self.config.retrieval_planner_defaults,
            packet={
                "active_focus": {"concepts": ["old concept alpha"]},
                "recent_semantic_turns": [{"concepts": ["old concept alpha"]}],
            },
        )
        plan = result["planner_retrieval_plan"]
        self.assertEqual(plan["resolved_referents"], ["current concept beta"])
        self.assertEqual(plan["semantic_queries"], ["current concept beta development"])
        self.assertNotIn("old concept alpha", str(result))


if __name__ == "__main__":
    unittest.main()
