from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from semantic_traversal.config import load_runtime_config
from semantic_traversal.semantic_compiler import SemanticCompilerResponse
from tools.implementation08_baseline import (
    ControlledPlannerBackend,
    ScenarioTurn,
    _case_dir,
    _case_path,
    run_baseline,
)


REPO_ROOT = Path(__file__).resolve().parents[1]


class ContextAwareBackend:
    instances: list["ContextAwareBackend"] = []

    def __init__(self) -> None:
        self.__class__.instances.append(self)

    def compile_turn(self, packet: dict[str, object]) -> SemanticCompilerResponse:
        raw = str(packet["raw_user_input"])
        prior = packet["prior_thread_state"]
        if raw == "Set up alpha and beta.":
            concepts = ["alpha", "beta"]
            referents: list[str] = []
        elif raw in {"Which one is broader?", "Which of those two is earlier?"}:
            self.assert_actual_setup_state(prior)
            concepts = ["alpha", "beta"]
            referents = ["alpha", "beta"]
        elif raw == "Continue with that concept after planner restart.":
            self.assert_actual_setup_state(prior)
            concepts = ["alpha"]
            referents = ["alpha"]
        elif raw == "What is the boiling point of water?":
            concepts = ["boiling point", "water"]
            referents = []
        elif raw == "Tell me more about it.":
            self.assert_actual_setup_state(prior)
            concepts = ["it"]
            referents = []
        else:
            concepts = ["alpha"]
            referents = []
        payload = {
            "raw_user_input": raw,
            "intent": "synthetic context-aware plan",
            "query": raw,
            "entities": [],
            "relations": [],
            "resolved_referents": referents,
            "planner_retrieval_plan": {
                "intent_type": "semantic_traversal",
                "scope_requests": [],
                "concepts": concepts,
                "resolved_referents": referents,
                "literal_terms": [],
                "evidence_requirements": ["chronology"] if "earlier" in raw else [],
                "semantic_queries": [raw],
                "lexical_queries": [],
                "graph_seeds": [],
                "retrieval_layers": ([{"operator": "temporal_retrieve", "required": True, "mode": "earliest"}] if "earlier" in raw else []),
            },
            "limitations": [],
        }
        return SemanticCompilerResponse(payload, json.dumps(payload), {"backend_mode": "controlled_context"}, {}, "parsed")

    @staticmethod
    def assert_actual_setup_state(prior: object) -> None:
        assert isinstance(prior, dict)
        assert prior.get("latest_user_input") in {"Set up alpha and beta.", "Remember concept alpha for the next turn."}
        messages = prior.get("recent_messages")
        assert isinstance(messages, list)
        assert any(message.get("content") in {"Set up alpha and beta.", "Remember concept alpha for the next turn."} for message in messages if isinstance(message, dict))


class Implementation08BaselineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = load_runtime_config(repo_root=REPO_ROOT)

    def test_expectations_are_separate_from_observed_plan_fields(self) -> None:
        turns = (ScenarioTurn("separation", "1", "Explain concept alpha.", "traverse", ("expected subject",), ("graph_expand",), ("graph_relation",), "separation"),)
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "records.jsonl"
            run_baseline(output_path=output, config=self.config, backend_factory=lambda _turn: ContextAwareBackend(), turns=turns)
            record = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(record["expected_operators"], ["graph_expand"])
            self.assertEqual(record["requested_operators"], [])
            self.assertEqual(record["expected_evidence_requirements"], ["graph_relation"])
            self.assertEqual(record["evidence_requirements"], [])
            self.assertEqual(record["plan_completeness"]["status"], "complete")
            self.assertIn(record["plan_executability"]["status"], {"complete", "non_executable"})

    def test_real_multiturn_state_is_consumed_and_carry_is_only_labeled_when_observed(self) -> None:
        ContextAwareBackend.instances = []
        turns = (
            ScenarioTurn("continuation", "1", "Set up alpha and beta.", "traverse", ("alpha", "beta"), thread_id="thread-one", setup_turn=True),
            ScenarioTurn("continuation", "2", "Which one is broader?", "traverse", ("alpha", "beta"), expected_referents=("alpha", "beta"), thread_id="thread-one"),
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "records.jsonl"
            run_baseline(output_path=output, config=self.config, backend_factory=lambda _turn: ContextAwareBackend(), turns=turns)
            records = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(records[1]["prior_thread_state"]["latest_user_input"], "Set up alpha and beta.")
            self.assertEqual(records[1]["prior_message_history"][0]["content"], "Set up alpha and beta.")
            self.assertEqual(records[1]["resolved_referents"], ["alpha", "beta"])
            self.assertEqual(records[1]["carry_source"]["values"], ["alpha", "beta"])
            self.assertEqual(len(ContextAwareBackend.instances), 2)

    def test_topic_reset_and_ambiguous_reference_do_not_fabricate_carry(self) -> None:
        turns = (
            ScenarioTurn("topic-reset", "1", "Set up alpha and beta.", "traverse", ("alpha", "beta"), thread_id="topic-reset", setup_turn=True),
            ScenarioTurn("topic-reset", "2", "What is the boiling point of water?", "direct", ("boiling point", "water"), thread_id="topic-reset"),
            ScenarioTurn("ambiguous-reference", "1", "Set up alpha and beta.", "traverse", ("alpha", "beta"), thread_id="ambiguous-reference", setup_turn=True),
            ScenarioTurn("ambiguous-reference", "2", "Tell me more about it.", "traverse", (), thread_id="ambiguous-reference"),
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "records.jsonl"
            run_baseline(output_path=output, config=self.config, backend_factory=lambda _turn: ContextAwareBackend(), turns=turns)
            records = {json.loads(line)["case_id"]: json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()}
            reset = records["topic-reset:2"]
            ambiguous = records["ambiguous-reference:2"]
            self.assertEqual(reset["carry_source"], None)
            self.assertEqual(reset["topic_reset_stale_subjects_emitted"], [])
            self.assertEqual(reset["resolved_referents"], [])
            self.assertTrue(ambiguous["ambiguity_preserved"])
            self.assertEqual(ambiguous["resolved_referents"], [])
            self.assertIsNone(ambiguous["carry_source"])

    def test_restart_reloads_persisted_state_with_new_backend_instance(self) -> None:
        ContextAwareBackend.instances = []
        turns = (
            ScenarioTurn("restart-recovery", "1", "Remember concept alpha for the next turn.", "traverse", ("alpha",), thread_id="restart-recovery", setup_turn=True),
            ScenarioTurn("restart-recovery", "2", "Continue with that concept after planner restart.", "traverse", ("alpha",), expected_referents=("alpha",), thread_id="restart-recovery"),
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "records.jsonl"
            run_baseline(output_path=output, config=self.config, backend_factory=lambda _turn: ContextAwareBackend(), turns=turns)
            records = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(ContextAwareBackend.instances), 2)
            self.assertTrue(records[1]["restart_backend_instance"])
            self.assertEqual(records[1]["prior_thread_state"]["latest_user_input"], "Remember concept alpha for the next turn.")
            self.assertEqual(records[1]["resolved_referents"], ["alpha"])

    def test_truncated_case_checkpoint_is_quarantined_and_only_damaged_case_reruns(self) -> None:
        turns = (
            ScenarioTurn("case-a", "1", "Explain alpha.", "traverse", ("alpha",), thread_id="case-a"),
            ScenarioTurn("case-b", "1", "Explain beta.", "traverse", ("beta",), thread_id="case-b"),
        )
        calls: list[str] = []

        def factory(turn: ScenarioTurn) -> ControlledPlannerBackend:
            calls.append(turn.scenario_id)
            return ControlledPlannerBackend()

        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "records.jsonl"
            first = run_baseline(output_path=output, config=self.config, backend_factory=factory, turns=turns)
            self.assertEqual(first["authoritative_case_id_count"], 2)
            damaged = _case_path(_case_dir(output), "case-b:1")
            damaged.write_text('{"case_id":"case-b:1"', encoding="utf-8")
            calls.clear()
            recovered = run_baseline(output_path=output, config=self.config, backend_factory=factory, turns=turns)
            self.assertEqual(calls, ["case-b"])
            self.assertEqual(recovered["authoritative_case_id_count"], 2)
            self.assertTrue(recovered["quarantined_checkpoints"])
            final_records = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(sorted(record["case_id"] for record in final_records), ["case-a:1", "case-b:1"])
            calls.clear()
            clean = run_baseline(output_path=output, config=self.config, backend_factory=factory, turns=turns)
            self.assertEqual(calls, [])
            self.assertEqual(clean["authoritative_case_id_count"], 2)

    def test_controlled_failures_have_distinct_terminal_planner_statuses(self) -> None:
        turns = (
            ScenarioTurn("ok", "1", "Explain concept alpha.", "traverse", ("concept alpha",), thread_id="ok"),
            ScenarioTurn("unavailable", "1", "Unavailable planner.", "traverse", (), thread_id="unavailable"),
            ScenarioTurn("timeout", "1", "Timeout planner.", "traverse", (), thread_id="timeout"),
            ScenarioTurn("malformed", "1", "Malformed planner.", "traverse", (), thread_id="malformed"),
        )

        def factory(turn: ScenarioTurn) -> ControlledPlannerBackend:
            return ControlledPlannerBackend({"unavailable": "unavailable", "timeout": "timeout", "malformed": "malformed"}.get(turn.scenario_id, "parsed"))

        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "records.jsonl"
            summary = run_baseline(output_path=output, config=self.config, backend_factory=factory, timeout_seconds=0.01, turns=turns)
            self.assertEqual(summary["case_count"], 4)
            self.assertEqual(summary["terminal_counts"]["completed"], 1)
            self.assertEqual(summary["terminal_counts"]["unavailable"], 1)
            self.assertEqual(summary["terminal_counts"]["timed_out"], 1)
            self.assertEqual(summary["terminal_counts"]["malformed"], 1)
            self.assertEqual(summary["authoritative_case_id_count"], 4)


if __name__ == "__main__":
    unittest.main()
