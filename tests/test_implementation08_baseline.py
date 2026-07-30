from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path

from semantic_traversal.config import load_runtime_config
from tools.implementation08_baseline import ControlledPlannerBackend, ScenarioTurn, run_baseline


REPO_ROOT = Path(__file__).resolve().parents[1]


class Implementation08BaselineTests(unittest.TestCase):
    def test_controlled_failures_persist_terminal_records_and_resume(self) -> None:
        config = load_runtime_config(repo_root=REPO_ROOT)
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
            summary = run_baseline(output_path=output, config=config, backend_factory=factory, timeout_seconds=0.01, turns=turns)
            self.assertEqual(summary["case_count"], 4)
            self.assertEqual(summary["terminal_counts"]["completed"], 1)
            self.assertEqual(summary["terminal_counts"]["unavailable"], 1)
            self.assertEqual(summary["terminal_counts"]["timed_out"], 1)
            self.assertEqual(summary["terminal_counts"]["malformed"], 1)
            first_content = output.read_text(encoding="utf-8")
            time.sleep(0.01)
            resumed = run_baseline(output_path=output, config=config, backend_factory=factory, timeout_seconds=0.01, turns=turns)
            self.assertEqual(resumed, summary)
            self.assertEqual(output.read_text(encoding="utf-8"), first_content)
            records = [json.loads(line) for line in first_content.splitlines()]
            self.assertTrue(all(record["terminal_status"] for record in records))
            self.assertEqual(records[0]["raw_model_output"] is not None, True)

    def test_multi_turn_cases_share_thread_id_and_carry_source(self) -> None:
        config = load_runtime_config(repo_root=REPO_ROOT)
        turns = (
            ScenarioTurn("continuation", "1", "Set up alpha and beta.", "traverse", ("alpha", "beta"), thread_id="thread-one", setup_turn=True),
            ScenarioTurn("continuation", "2", "Which is earlier?", "traverse", ("alpha", "beta"), ("temporal_retrieve",), ("chronology",), "thread-one"),
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "records.jsonl"
            run_baseline(output_path=output, config=config, backend_factory=lambda _turn: ControlledPlannerBackend(), turns=turns)
            records = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
            self.assertEqual([record["thread_id"] for record in records], ["thread-one", "thread-one"])
            self.assertEqual(records[1]["carry_source"], "persisted_thread_state")
            self.assertEqual(records[1]["turn_id"], "2")


if __name__ == "__main__":
    unittest.main()
