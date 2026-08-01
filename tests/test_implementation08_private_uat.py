from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import yaml

from semantic_traversal.semantic_compiler import SemanticCompilerResponse
from tools.implementation08_private_uat import (
    FixtureError,
    PrivateUATUnavailable,
    RecordingSemanticCompilerBackend,
    _assert_private_path,
    _coverage_metrics,
    _turn_metrics,
    evaluate_compiler_contract,
    export_redacted,
    validate_fixture,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = REPO_ROOT / "agent_harness/implementation-projects/active/implementation-08-private-uat.example.yaml"


def canonical(raw_input: str = "synthetic input") -> dict:
    return {
        "raw_user_input": raw_input, "intent": "synthetic", "query": "synthetic query",
        "entities": [], "relations": [], "resolved_referents": [], "limitations": [],
        "planner_retrieval_plan": {
            "intent_type": "fact", "scope_requests": [], "concepts": [], "resolved_referents": [],
            "literal_terms": [], "evidence_requirements": [], "semantic_queries": ["synthetic query"],
            "lexical_queries": ["synthetic query"], "graph_seeds": [],
            "retrieval_layers": [{"operator": "lexical_chunk_search", "required": False}],
        },
    }


class Implementation08PrivateUATTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))

    def test_valid_schema_v2_fixture_and_five_cases(self) -> None:
        validated = validate_fixture(self.fixture)
        self.assertEqual(validated["schema_version"], 2)
        self.assertEqual(len(validated["cases"]), 5)
        self.assertTrue(any(len(case["turns"]) == 2 for case in validated["cases"]))

    def test_schema_v1_rejected_with_migration_message(self) -> None:
        old = copy.deepcopy(self.fixture)
        old["schema_version"] = 1
        with self.assertRaisesRegex(FixtureError, "moved from the case level"):
            validate_fixture(old)

    def test_case_level_expected_rejected(self) -> None:
        malformed = copy.deepcopy(self.fixture)
        malformed["cases"][0]["expected"] = {}
        with self.assertRaises(FixtureError):
            validate_fixture(malformed)

    def test_different_turn_expectations_are_preserved(self) -> None:
        turns = validate_fixture(self.fixture)["cases"][2]["turns"]
        self.assertNotEqual(turns[0]["expected"]["current_turn_subjects"], turns[1]["expected"]["current_turn_subjects"])
        self.assertEqual(turns[1]["expected"]["resolved_referents"], ["synthetic experience", "later choice"])

    def test_unknown_and_malformed_structural_fields_rejected(self) -> None:
        malformed = copy.deepcopy(self.fixture)
        malformed["cases"][0]["turns"][0]["expected"]["unexpected"] = True
        with self.assertRaises(FixtureError):
            validate_fixture(malformed)

    def test_negative_claim_requires_explicit_coverage(self) -> None:
        malformed = copy.deepcopy(self.fixture)
        malformed["cases"][0]["turns"][0]["expected"]["negative_claim"]["permitted"] = True
        malformed["cases"][0]["turns"][0]["expected"]["negative_claim"]["required_coverage"] = None
        with self.assertRaises(FixtureError):
            validate_fixture(malformed)

    def test_valid_canonical_raw_output_is_contract_valid(self) -> None:
        raw = json.dumps(canonical())
        response = SemanticCompilerResponse(canonical(), raw, {"semantic_compiler_prompt_hash": "prompt"}, {}, "parsed")
        result = evaluate_compiler_contract(packet={"raw_user_input": "synthetic input"}, response=response, canonical_packet=canonical())
        self.assertEqual(result["raw_json_status"], "object")
        self.assertEqual(result["contract_status"], "valid")

    def test_off_contract_object_is_contract_invalid(self) -> None:
        raw = json.dumps({"planner_intent": "synthetic", "retrieval_layer": "lexical"})
        response = SemanticCompilerResponse(canonical(), raw, {}, {}, "parsed")
        result = evaluate_compiler_contract(packet={"raw_user_input": "synthetic input"}, response=response, canonical_packet=canonical())
        self.assertEqual(result["raw_json_status"], "object")
        self.assertEqual(result["contract_status"], "invalid")
        self.assertIn("planner_retrieval_plan", result["missing_top_level_fields"])

    def test_invalid_json_distinguished_from_invalid_contract(self) -> None:
        response = SemanticCompilerResponse(canonical(), "not json", {}, {}, "invalid_json")
        result = evaluate_compiler_contract(packet={"raw_user_input": "synthetic input"}, response=response, canonical_packet=canonical())
        self.assertEqual(result["raw_json_status"], "invalid")
        self.assertEqual(result["contract_status"], "invalid")

    def test_missing_raw_planner_with_canonical_plan_is_fallback(self) -> None:
        response = SemanticCompilerResponse(canonical(), json.dumps({"raw_user_input": "synthetic input"}), {}, {}, "parsed")
        result = evaluate_compiler_contract(packet={"raw_user_input": "synthetic input"}, response=response, canonical_packet=canonical())
        self.assertTrue(result["fallback_plan_used"])

    def test_raw_input_preservation_is_contract_requirement(self) -> None:
        payload = canonical("other input")
        response = SemanticCompilerResponse(payload, json.dumps(payload), {}, {}, "parsed")
        result = evaluate_compiler_contract(packet={"raw_user_input": "synthetic input"}, response=response, canonical_packet=payload)
        self.assertEqual(result["contract_status"], "invalid")
        self.assertFalse(result["raw_user_input_preserved"])

    def test_missing_and_unexpected_fields_are_recorded(self) -> None:
        payload = canonical()
        payload.pop("limitations")
        payload["invented"] = "synthetic"
        response = SemanticCompilerResponse(canonical(), json.dumps(payload), {}, {}, "parsed")
        result = evaluate_compiler_contract(packet={"raw_user_input": "synthetic input"}, response=response, canonical_packet=canonical())
        self.assertIn("limitations", result["missing_top_level_fields"])
        self.assertEqual(result["unexpected_top_level_fields"], ["invented"])

    def test_ad_hoc_compiler_fields_are_rejected(self) -> None:
        raw = json.dumps({"raw_user_input": "synthetic input", "planner_intent": "x", "retrieval_layer": "x"})
        response = SemanticCompilerResponse(canonical(), raw, {}, {}, "parsed")
        result = evaluate_compiler_contract(packet={"raw_user_input": "synthetic input"}, response=response, canonical_packet=canonical())
        self.assertEqual(result["contract_status"], "invalid")
        self.assertIn("planner_intent", result["unexpected_top_level_fields"])

    def _coverage_result(self, *, allowed: bool, exact_status: str = "completed_no_matches", executed: bool = True) -> SimpleNamespace:
        manifest = {
            "coverage": {"negative_claims_allowed": allowed, "exact_search_performed": executed, "exact_status": exact_status, "scope": "complete_eligible_corpus", "required_exact_terms": ["synthetic"], "inadequate_required_exact_terms": [], "total_exact_matches": 0},
            "execution": {"layers_executed": ["exact_chunk_search"] if executed else [], "layers_skipped": [] if executed else [{"layer": "exact_chunk_search"}]},
        }
        packet = {"planner_retrieval_plan": {"retrieval_layers": [{"operator": "exact_chunk_search", "required": True}]}}
        expected = {"negative_claim": {"permitted": True, "required_coverage": {"layer": "exact_chunk_search", "exhaustive": True, "scope": "complete_eligible_corpus"}}}
        return SimpleNamespace(coverage_report={"decision": "approved"}, semantic_traversal_manifest=manifest, semantic_compiler_packet=packet, retrieval_packet={}), expected

    def test_generic_approval_does_not_authorize_negative_claim(self) -> None:
        result, expected = self._coverage_result(allowed=False)
        metrics = _coverage_metrics(result=result, expected=expected)
        self.assertFalse(metrics["claim_authorized"])

    def test_exhaustive_exact_authorization_is_runtime_owned(self) -> None:
        result, expected = self._coverage_result(allowed=True)
        metrics = _coverage_metrics(result=result, expected=expected)
        self.assertTrue(metrics["claim_authorized"])
        self.assertTrue(metrics["required_coverage_satisfied"])

    def test_skipped_exact_layer_is_coverage_mismatch(self) -> None:
        result, expected = self._coverage_result(allowed=False, executed=False, exact_status="not_requested")
        metrics = _coverage_metrics(result=result, expected=expected)
        self.assertFalse(metrics["required_coverage_satisfied"])
        self.assertFalse(metrics["claim_authorized"])

    def test_private_output_boundary_rejects_paths_outside_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with self.assertRaises(ValueError):
                _assert_private_path(root / "outside.json", root / "agent_harness/private", "output")

    def test_redaction_exposes_statuses_but_no_private_material_or_arbitrary_keys(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = Path(temp_dir) / "run"
            (run_dir / "raw").mkdir(parents=True)
            (run_dir / "run.json").write_text(json.dumps({"suite_id": "synthetic", "fixture_sha256": "fixture", "run_sha256": "run"}), encoding="utf-8")
            raw = {"case_id": "safe-id", "description": "private question", "turns": [{"turn_id": 1, "input": "private question", "expected": {"answer": "private answer"}, "metrics": {"runtime_status": "completed", "evaluation_status": "failed", "expectations": {"response_mode": {"status": "mismatch"}}, "compiler_contract": {"raw_json_status": "object", "contract_status": "invalid", "fallback_plan_used": True}, "coverage": {"coverage_decision": "approved", "claim_authorized": False}, "requested_operators": ["exact_chunk_search"], "executed_operators": [], "final_answer_correctness": {"status": "unavailable"}, "synthesis_support": {"status": "unavailable"}}, "result": {"question": "private question", "answer": "private answer", "uuid": "private-uuid", "path": "private/path", "chunk": "private chunk", "assistant": "private response", "unexpected_private_key": "private value"}}]}
            (run_dir / "raw" / "safe-id.json").write_text(json.dumps(raw), encoding="utf-8")
            output = Path(temp_dir) / "report.json"
            report = export_redacted(run_dir=run_dir, output_path=output)
            exported = output.read_text(encoding="utf-8")
            for value in ("private question", "private answer", "private-uuid", "private/path", "private chunk", "private response", "unexpected_private_key", "private value"):
                self.assertNotIn(value, exported)
            self.assertEqual(report["cases"][0]["turns"][0]["compiler_contract_status"], "invalid")

    def test_redacted_export_is_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = Path(temp_dir) / "run"
            (run_dir / "raw").mkdir(parents=True)
            (run_dir / "run.json").write_text(json.dumps({"suite_id": "synthetic", "fixture_sha256": "fixture", "run_sha256": "run"}), encoding="utf-8")
            (run_dir / "raw" / "safe.json").write_text(json.dumps({"case_id": "safe", "turns": []}), encoding="utf-8")
            first = export_redacted(run_dir=run_dir, output_path=Path(temp_dir) / "one.json")
            second = export_redacted(run_dir=run_dir, output_path=Path(temp_dir) / "two.json")
            self.assertEqual(first, second)

    def test_recording_proxy_delegates_once_and_returns_same_object(self) -> None:
        response = SemanticCompilerResponse(canonical(), "{}", {}, {}, "parsed")
        class Backend:
            mode_name = "synthetic"
            def __init__(self): self.calls = 0
            def compile_turn(self, packet):
                self.calls += 1
                return response
        backend = Backend()
        proxy = RecordingSemanticCompilerBackend(backend)
        returned = proxy.compile_turn({"raw_user_input": "synthetic"})
        self.assertIs(returned, response)
        self.assertEqual(backend.calls, 1)
        self.assertEqual(len(proxy.observations), 1)

    def test_completed_runtime_plus_contract_failure_is_evaluation_failed(self) -> None:
        expected = self.fixture["cases"][0]["turns"][0]["expected"]
        response = SemanticCompilerResponse(canonical(), json.dumps({"planner_intent": "off contract"}), {}, {}, "parsed")
        result = SimpleNamespace(
            semantic_compiler_packet=canonical(), semantic_traversal_manifest={"execution": {}, "candidate_counts": {}},
            retrieval_packet={"selected_chunks": []}, runtime_outcome="completed", coverage_report={"decision": "approved"},
        )
        metrics = _turn_metrics(result=result, expected=expected, observation={"packet": {"raw_user_input": "synthetic input"}, "response": response})
        self.assertEqual(metrics["evaluation_status"], "failed")
        self.assertEqual(metrics["runtime_status"], "completed")

    def test_deterministic_success_without_answer_oracle_is_review_required(self) -> None:
        expected = copy.deepcopy(self.fixture["cases"][0]["turns"][0]["expected"])
        expected["current_turn_subjects"] = []
        expected["required_operators"] = ["lexical_chunk_search"]
        expected["evidence_requirements"] = []
        payload = canonical()
        payload["planner_retrieval_plan"]["concepts"] = []
        payload["planner_retrieval_plan"]["evidence_requirements"] = []
        response = SemanticCompilerResponse(payload, json.dumps(payload), {"semantic_compiler_prompt_hash": "prompt"}, {}, "parsed")
        result = SimpleNamespace(
            semantic_compiler_packet={**payload, "planner_diagnostics": {"plan_completeness": {"status": "complete"}, "plan_executability": {"status": "executable"}}},
            semantic_traversal_manifest={"execution": {"layers_executed": ["lexical_chunk_search"]}, "candidate_counts": {}, "coverage": {"negative_claims_allowed": False}},
            retrieval_packet={"selected_chunks": [{"synthetic": True}]}, runtime_outcome="completed", coverage_report={"decision": "approved"},
        )
        metrics = _turn_metrics(result=result, expected=expected, observation={"packet": {"raw_user_input": "synthetic input"}, "response": response})
        self.assertEqual(metrics["evaluation_status"], "review_required")

    def test_authoritative_different_run_is_not_overwritten(self) -> None:
        from tools.implementation08_private_uat import run_private_uat
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            fixture = root / "agent_harness/private/fixture.yaml"
            fixture.parent.mkdir(parents=True)
            fixture.write_text(yaml.safe_dump(self.fixture), encoding="utf-8")
            output = root / "agent_harness/private/run"
            output.mkdir(parents=True)
            (output / "run.json").write_text(json.dumps({"fixture_sha256": "different"}), encoding="utf-8")
            with self.assertRaises(PrivateUATUnavailable):
                run_private_uat(fixture_path=fixture, repo_root=root, output_dir=output)


if __name__ == "__main__":
    unittest.main()
