from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

import yaml

from tools.implementation08_private_uat import (
    FixtureError,
    PrivateUATUnavailable,
    _assert_private_path,
    export_redacted,
    validate_fixture,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = REPO_ROOT / "agent_harness/implementation-projects/active/implementation-08-private-uat.example.yaml"


class Implementation08PrivateUATTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))

    def test_sanitized_fixture_is_valid_and_preserves_multiturn_shape(self) -> None:
        validated = validate_fixture(self.fixture)
        self.assertEqual(validated["schema_version"], 1)
        self.assertTrue(any(len(case["turns"]) == 2 for case in validated["cases"]))

    def test_malformed_fixture_is_rejected(self) -> None:
        malformed = copy.deepcopy(self.fixture)
        malformed["cases"][0]["expected"]["response_mode"] = "answer"
        with self.assertRaises(FixtureError):
            validate_fixture(malformed)

    def test_negative_claim_permission_requires_exhaustive_boundary(self) -> None:
        malformed = copy.deepcopy(self.fixture)
        negative = malformed["cases"][0]["expected"]["negative_claim"]
        negative["permitted"] = True
        negative["required_coverage"] = None
        with self.assertRaises(FixtureError):
            validate_fixture(malformed)

    def test_raw_output_boundary_rejects_paths_outside_private_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            private = root / "agent_harness/private"
            with self.assertRaises(ValueError):
                _assert_private_path(root / "outside.json", private, "output")

    def test_redacted_export_is_allowlisted_and_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = Path(temp_dir) / "run"
            (run_dir / "raw").mkdir(parents=True)
            (run_dir / "run.json").write_text(json.dumps({"suite_id": "synthetic", "fixture_sha256": "fixture-hash"}), encoding="utf-8")
            raw = {
                "case_id": "case-a",
                "description": "private question must not escape",
                "expected": {"answer": {"exact_value": "private answer"}},
                "turns": [{"metrics": {"terminal_status": "blocked", "requested_operators": ["exact_chunk_search"], "negative_claim": {"coverage_report": "not_approved"}, "final_support_grade": {"status": "unavailable"}}, "input": "private question", "result": {"note_uuid": "private-uuid", "path": "private/path", "chunk_text": "private chunk"}}],
            }
            (run_dir / "raw/case-a.json").write_text(json.dumps(raw), encoding="utf-8")
            first_path = Path(temp_dir) / "redacted-1.json"
            second_path = Path(temp_dir) / "redacted-2.json"
            first = export_redacted(run_dir=run_dir, output_path=first_path)
            second = export_redacted(run_dir=run_dir, output_path=second_path)
            self.assertEqual(first, second)
            exported = first_path.read_text(encoding="utf-8")
            for private_value in ("private question", "private answer", "private-uuid", "private/path", "private chunk"):
                self.assertNotIn(private_value, exported)
            self.assertEqual(first["cases"][0]["status"], "blocked")

    def test_existing_run_for_different_fixture_requires_explicit_replacement(self) -> None:
        # The preflight check is intentionally before vault access, so this
        # safety gate remains testable without a private corpus.
        from tools.implementation08_private_uat import run_private_uat

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            private_fixture = root / "agent_harness/private/fixture.yaml"
            private_fixture.parent.mkdir(parents=True)
            private_fixture.write_text(yaml.safe_dump(self.fixture), encoding="utf-8")
            output = root / "agent_harness/private/run"
            output.mkdir(parents=True)
            (output / "run.json").write_text(json.dumps({"fixture_sha256": "different"}), encoding="utf-8")
            with self.assertRaises(PrivateUATUnavailable):
                run_private_uat(fixture_path=private_fixture, repo_root=root, output_dir=output)


if __name__ == "__main__":
    unittest.main()
