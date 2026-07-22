from __future__ import annotations

import hashlib
import inspect
import tempfile
import unittest
from pathlib import Path

from semantic_traversal.config import load_runtime_config
from semantic_traversal.hashing import sha256_text
from semantic_traversal.retrieval_plan import build_default_retrieval_plan
from semantic_traversal.runtime import (
    _apply_retrieval_candidate_hygiene,
    _build_visible_transcript_tail,
    _graph_match_note_nodes,
    _merge_candidates,
    _update_thread_state,
)
from semantic_traversal.semantic_compiler import _render_ollama_prompt


REPO_ROOT = Path(__file__).resolve().parent.parent
PROMPT_BASELINE_SHA256 = "d20b1973517cdd9b4705522c636160497972faf1c411d0ac0dd894e3880dc50f"
FRONTIER_PROMPT_BASELINE_SHA256 = "fdac281e48e765af09578be02e53ad65a443d49914fac53d017ade5391a18776"


class ControlSurfaceRelocationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_runtime_config(repo_root=REPO_ROOT)

    def test_prompt_and_frontier_baselines_and_schema_are_preserved(self) -> None:
        packet = {
            "raw_user_input": "baseline deterministic fixture",
            "active_focus": {"literal": "coherence"},
            "nested": {"value": "unchanged"},
        }
        prompt = _render_ollama_prompt(
            packet=packet,
            template=self.config.semantic_compiler_prompt_template,
            planner_defaults=self.config.retrieval_planner_defaults,
        )
        self.assertEqual(hashlib.sha256(prompt.encode()).hexdigest(), PROMPT_BASELINE_SHA256)
        self.assertEqual(sha256_text(self.config.frontier_synthesis_instructions), FRONTIER_PROMPT_BASELINE_SHA256)
        for field in (
            "raw_user_input",
            "intent",
            "query",
            "entities",
            "relations",
            "resolved_referents",
            "planner_retrieval_plan",
            "limitations",
            "retrieval_layers",
        ):
            self.assertIn(f'"{field}"', prompt)
        self.assertNotIn('"selection_policy"', prompt)
        self.assertNotIn('"claim_policy"', prompt)
        self.assertNotIn("semantic_compiler_exact_budget", prompt)

    def test_planner_defaults_drive_plan_and_prompt_from_one_yaml_section(self) -> None:
        defaults = self.config.retrieval_planner_defaults
        semantic_plan = build_default_retrieval_plan(
            raw_user_input="explain coherence",
            query="explain coherence",
            concepts=["explain", "coherence"],
            scope_requests=[],
            graph_seeds=["explain coherence"],
            resolved_referents=[],
            planner_defaults=defaults,
        )
        exact_plan = build_default_retrieval_plan(
            raw_user_input="find exact coherence",
            query="find exact coherence",
            concepts=["coherence"],
            scope_requests=[],
            graph_seeds=["find exact coherence"],
            resolved_referents=[],
            planner_defaults=defaults,
        )
        self.assertNotIn("selection_policy", semantic_plan)
        self.assertNotIn("claim_policy", semantic_plan)
        self.assertEqual(exact_plan["retrieval_layers"][0]["limit"], 200)
        self.assertEqual(exact_plan["retrieval_layers"][0]["return_total_count"], True)
        self.assertEqual(exact_plan["retrieval_layers"][1]["limit"], 50)
        self.assertEqual(exact_plan["retrieval_layers"][2]["limit"], 24)
        self.assertEqual(exact_plan["retrieval_layers"][3]["depth"], 1)
        self.assertNotIn("selection_policy", exact_plan)
        self.assertNotIn("claim_policy", exact_plan)

        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "semantic_traversal.runtime.yaml"
            text = (REPO_ROOT / "semantic_traversal.runtime.yaml").read_text(encoding="utf-8")
            config_path.write_text(text.replace("    vector_limit: 24", "    vector_limit: 17"), encoding="utf-8")
            mutated = load_runtime_config(repo_root=REPO_ROOT, config_path=str(config_path))
            mutated_plan = build_default_retrieval_plan(
                raw_user_input="explain coherence",
                query="explain coherence",
                concepts=["explain", "coherence"],
                scope_requests=[],
                graph_seeds=["explain coherence"],
                resolved_referents=[],
                planner_defaults=mutated.retrieval_planner_defaults,
            )
            mutated_prompt = _render_ollama_prompt(
                packet={"raw_user_input": "probe"},
                template=mutated.semantic_compiler_prompt_template,
                planner_defaults=mutated.retrieval_planner_defaults,
            )
            self.assertEqual(mutated_plan["retrieval_layers"][1]["limit"], 17)
            self.assertIn('"limit": 17', mutated_prompt)
            self.assertNotIn("prompt_example", mutated.raw)

    def test_prompt_marker_replacement_is_packet_safe(self) -> None:
        markers = " ".join(
            [
                "{semantic_compiler_lexical_limit}",
                "{semantic_compiler_vector_limit}",
                "{semantic_compiler_graph_depth}",
                "{semantic_compiler_max_chunks}",
            ]
        )
        prompt = _render_ollama_prompt(
            packet={"raw_user_input": markers, "nested": {"value": markers}},
            template=self.config.semantic_compiler_prompt_template,
            planner_defaults=self.config.retrieval_planner_defaults,
        )
        self.assertIn(markers, prompt)
        self.assertIn('"limit": 50', prompt)
        self.assertIn('"limit": 24', prompt)
        self.assertNotIn("{semantic_compiler_lexical_limit}", prompt.split('"raw_user_input":', 1)[0])

    def test_runtime_helpers_require_explicit_config(self) -> None:
        for helper in (_graph_match_note_nodes, _apply_retrieval_candidate_hygiene, _merge_candidates):
            self.assertIs(inspect.signature(helper).parameters["config"].default, inspect.Parameter.empty)
            self.assertNotIn("Path.cwd()", inspect.getsource(helper))

    def test_message_limits_are_independent_yaml_controls(self) -> None:
        self.assertEqual(self.config.runtime_conversation["recent_message_limit"], 6)
        self.assertEqual(self.config.runtime_conversation["visible_transcript_tail_limit"], 6)
        messages = [{"role": "user", "content": str(index)} for index in range(8)]
        self.assertEqual(_build_visible_transcript_tail(messages, limit=6), messages[-6:])
        self.assertEqual(_build_visible_transcript_tail(messages, limit=3), messages[-3:])

        def update(config):
            return _update_thread_state(
                prior_thread_state={"recent_messages": messages, "recent_semantic_turns": []},
                thread_id="thread",
                turn_id=1,
                raw_user_input="new message",
                assistant_response="response",
                semantic_compiler_packet={"planner_retrieval_plan": {}},
                retrieval_packet={"selected_chunks": []},
                created_at="2026-01-01T00:00:00Z",
                config=config,
            )

        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "semantic_traversal.runtime.yaml"
            text = (REPO_ROOT / "semantic_traversal.runtime.yaml").read_text(encoding="utf-8")
            config_path.write_text(text.replace("  recent_message_limit: 6", "  recent_message_limit: 3"), encoding="utf-8")
            recent_mutated = load_runtime_config(repo_root=REPO_ROOT, config_path=str(config_path))
            self.assertEqual(len(update(recent_mutated)["recent_messages"]), 3)
            self.assertEqual(len(_build_visible_transcript_tail(messages, limit=recent_mutated.runtime_conversation["visible_transcript_tail_limit"])), 6)

            config_path.write_text(text.replace("  visible_transcript_tail_limit: 6", "  visible_transcript_tail_limit: 2"), encoding="utf-8")
            visible_mutated = load_runtime_config(repo_root=REPO_ROOT, config_path=str(config_path))
            self.assertEqual(len(update(visible_mutated)["recent_messages"]), 6)
            self.assertEqual(len(_build_visible_transcript_tail(messages, limit=visible_mutated.runtime_conversation["visible_transcript_tail_limit"])), 2)


if __name__ == "__main__":
    unittest.main()
