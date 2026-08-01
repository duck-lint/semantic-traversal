from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import yaml

from semantic_traversal.semantic_compiler import SemanticCompilerResponse
from tools.implementation08_private_uat import (
    FixtureError,
    PrivateUATUnavailable,
    RecordingSemanticCompilerBackend,
    _assert_private_path,
    _coverage_metrics,
    _coverage_scope_matches,
    _compare_required,
    _final_response_attempt,
    _plan_source_attempt,
    _validate_attempt_topology,
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
            "intent_type": "fact", "concepts": [], "resolved_referents": [],
            "literal_terms": [], "evidence_requirements": [], "semantic_queries": ["synthetic query"],
            "lexical_queries": ["synthetic query"], "graph_seeds": [],
            "retrieval_layers": [{"operator": "lexical_chunk_search", "required": False}],
        },
    }


class Implementation08PrivateUATTests(unittest.TestCase):
    def test_unrestricted_runtime_scope_matches_complete_eligible_corpus(self) -> None:
        self.assertTrue(_coverage_scope_matches({"source_label": None, "note_type": [], "path_contains": []}, "complete_eligible_corpus"))
        self.assertTrue(_coverage_scope_matches("complete_eligible_corpus", "complete_eligible_corpus"))

    def test_restricted_runtime_scopes_do_not_match_complete_eligible_corpus(self) -> None:
        for scope in (
            {"source_label": "vault", "note_type": [], "path_contains": []},
            {"source_label": None, "note_type": ["journal_entry"], "path_contains": []},
            {"source_label": None, "note_type": [], "path_contains": ["journal"]},
        ):
            self.assertFalse(_coverage_scope_matches(scope, "complete_eligible_corpus"))
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

    def test_missing_raw_response_is_contract_unavailable(self) -> None:
        response = SemanticCompilerResponse(None, None, {}, {}, "unavailable")
        result = evaluate_compiler_contract(packet={"raw_user_input": "synthetic input"}, response=response, canonical_packet=canonical())
        self.assertEqual(result["raw_json_status"], "unavailable")
        self.assertEqual(result["contract_status"], "unavailable")

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

    def test_redacted_repair_attempts_expose_safe_statuses_without_private_content(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = Path(temp_dir) / "run"
            (run_dir / "raw").mkdir(parents=True)
            (run_dir / "run.json").write_text(json.dumps({"suite_id": "synthetic", "fixture_sha256": "fixture", "run_sha256": "run", "report_schema_version": 5, "evaluator_contract_version": 4}), encoding="utf-8")
            raw = {"case_id": "private-case-id", "turns": [{"turn_id": 1, "metrics": {"runtime_status": "completed", "evaluation_status": "review_required", "compiler_contract": {"raw_json_status": "object", "contract_status": "valid"}, "compiler_attempt_count": 2, "repair_attempted": True, "repair_outcome": "complete", "initial_contract_status": "invalid", "repair_contract_status": "valid", "authoritative_attempt_role": "repair", "authoritative_contract_status": "valid", "expectations": {"current_turn_subjects": {"status": "mismatch"}, "resolved_referents": {"status": "match"}, "required_operators": {"status": "match", "additional": ["vector_search"]}}, "coverage": {"coverage_decision": "approved", "claim_authorized": False}}, "compiler_attempts": [{"request": {"repair_context": {"private": "repair secret"}}, "raw_response": "private response"}]}]}
            (run_dir / "raw" / "private.json").write_text(json.dumps(raw), encoding="utf-8")
            output = Path(temp_dir) / "report.json"
            report = export_redacted(run_dir=run_dir, output_path=output)
            exported = output.read_text(encoding="utf-8")
            self.assertEqual(report["evaluator_contract_version"], 5)
            self.assertEqual(report["cases"][0]["turns"][0]["compiler_attempt_count"], 2)
            self.assertTrue(report["cases"][0]["turns"][0]["repair_attempted"])
            for value in ("private-case-id", "repair secret", "private response"):
                self.assertNotIn(value, exported)

    def test_different_evaluator_versions_cannot_resume(self) -> None:
        from tools.implementation08_private_uat import run_private_uat
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            fixture = root / "agent_harness/private/fixture.yaml"
            fixture.parent.mkdir(parents=True)
            fixture.write_text(yaml.safe_dump(self.fixture), encoding="utf-8")
            output = root / "agent_harness/private/run"
            output.mkdir(parents=True)
            (output / "run.json").write_text(json.dumps({"fixture_sha256": __import__("hashlib").sha256(fixture.read_bytes()).hexdigest(), "report_schema_version": 2, "evaluator_contract_version": 1}), encoding="utf-8")
            with self.assertRaisesRegex(PrivateUATUnavailable, "different evaluator/report contract"):
                run_private_uat(fixture_path=fixture, repo_root=root, output_dir=output)

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
            semantic_compiler_diagnostic=self._diagnostic(raw=response.raw_response),
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
            semantic_compiler_diagnostic=self._diagnostic(raw=response.raw_response),
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

    @staticmethod
    def _observation(*, repair: bool = False, raw: str | None = None, status: str = "parsed") -> dict:
        payload = canonical()
        raw = raw if raw is not None else (None if status in {"unavailable", "timed_out"} else json.dumps(payload))
        response = SemanticCompilerResponse(None if raw is None else payload, raw, {"semantic_compiler_prompt_hash": "synthetic-prompt"}, {}, status)
        packet = {"raw_user_input": "synthetic input", "instruction": "Repair the compiler retrieval plan exactly once." if repair else "Compile a retrieval request."}
        if repair:
            packet["repair_context"] = {"synthetic": True}
        return {"packet": packet, "response": response}

    @staticmethod
    def _diagnostic(*, raw: str | None, attempted: bool = False, outcome: str = "not_needed", status: str | None = None, response_status: str | None = None) -> dict:
        from semantic_traversal.hashing import sha256_text
        effective_status = status or ("parsed" if raw is not None else "unavailable")
        return {"semantic_compiler_response_status": effective_status, "raw_response_hash": sha256_text(raw) if raw is not None else None, "plan_repair": {"attempted": attempted, "outcome": outcome, "response_status": response_status or effective_status}}

    def test_one_initial_attempt_is_accepted(self) -> None:
        observation = self._observation()
        attempts = _validate_attempt_topology(observations=[observation], diagnostic=self._diagnostic(raw=observation["response"].raw_response))
        self.assertEqual([attempt["attempt_role"] for attempt in attempts], ["initial"])

    def test_initial_plus_valid_repair_is_accepted(self) -> None:
        initial = self._observation(raw=json.dumps({"initial": True}))
        repair = self._observation(repair=True)
        attempts = _validate_attempt_topology(observations=[initial, repair], diagnostic=self._diagnostic(raw=repair["response"].raw_response, attempted=True, outcome="complete"))
        self.assertEqual([attempt["attempt_role"] for attempt in attempts], ["initial", "repair"])

    def test_zero_attempts_rejected(self) -> None:
        with self.assertRaises(PrivateUATUnavailable):
            _validate_attempt_topology(observations=[], diagnostic={})

    def test_more_than_two_attempts_rejected(self) -> None:
        observations = [self._observation(), self._observation(repair=True), self._observation(repair=True)]
        with self.assertRaises(PrivateUATUnavailable):
            _validate_attempt_topology(observations=observations, diagnostic=self._diagnostic(raw=observations[-1]["response"].raw_response, attempted=True, outcome="complete"))

    def test_duplicate_initial_roles_rejected(self) -> None:
        observations = [self._observation(), self._observation()]
        with self.assertRaises(PrivateUATUnavailable):
            _validate_attempt_topology(observations=observations, diagnostic=self._diagnostic(raw=observations[-1]["response"].raw_response, attempted=True, outcome="complete"))

    def test_repair_before_initial_rejected(self) -> None:
        observations = [self._observation(repair=True), self._observation()]
        with self.assertRaises(PrivateUATUnavailable):
            _validate_attempt_topology(observations=observations, diagnostic=self._diagnostic(raw=observations[-1]["response"].raw_response, attempted=True, outcome="complete"))

    def test_repair_without_runtime_diagnostic_rejected(self) -> None:
        observations = [self._observation(), self._observation(repair=True)]
        with self.assertRaises(PrivateUATUnavailable):
            _validate_attempt_topology(observations=observations, diagnostic=self._diagnostic(raw=observations[-1]["response"].raw_response))

    def test_runtime_repair_without_observation_rejected(self) -> None:
        observation = self._observation()
        with self.assertRaises(PrivateUATUnavailable):
            _validate_attempt_topology(observations=[observation], diagnostic=self._diagnostic(raw=observation["response"].raw_response, attempted=True, outcome="complete"))

    def test_authoritative_repaired_response_is_hash_matched(self) -> None:
        initial = self._observation(raw=json.dumps({"initial": True}))
        repair = self._observation(repair=True)
        attempts = _validate_attempt_topology(observations=[initial, repair], diagnostic=self._diagnostic(raw=repair["response"].raw_response, attempted=True, outcome="complete"))
        self.assertEqual(_final_response_attempt(attempts=attempts, diagnostic=self._diagnostic(raw=repair["response"].raw_response, attempted=True, outcome="complete"))["attempt_role"], "repair")

    def test_timeout_repair_is_accepted_without_repair_hash(self) -> None:
        initial = self._observation(raw=json.dumps(canonical(), sort_keys=True))
        repair = self._observation(repair=True, raw=None, status="unavailable")
        diagnostic = self._diagnostic(raw=None, attempted=True, outcome="incomplete", status="unavailable")
        attempts = _validate_attempt_topology(observations=[initial, repair], diagnostic=diagnostic)
        final = _final_response_attempt(attempts=attempts, diagnostic=diagnostic)
        self.assertEqual(final["attempt_role"], "repair")
        self.assertIsNone(final["raw_response_sha256"])
        self.assertEqual(evaluate_compiler_contract(packet=repair["packet"], response=repair["response"], canonical_packet=canonical())["contract_status"], "unavailable")

    def test_unavailable_repair_is_contract_unavailable_not_invalid(self) -> None:
        initial = self._observation()
        repair = self._observation(repair=True, raw=None, status="unavailable")
        diagnostic = self._diagnostic(raw=None, attempted=True, outcome="incomplete", status="unavailable")
        attempts = _validate_attempt_topology(observations=[initial, repair], diagnostic=diagnostic)
        self.assertEqual(_final_response_attempt(attempts=attempts, diagnostic=diagnostic)["backend_status"], "unavailable")
        self.assertEqual(evaluate_compiler_contract(packet=repair["packet"], response=repair["response"], canonical_packet=canonical())["contract_status"], "unavailable")

    def test_timeout_repair_records_repair_as_final_and_initial_as_plan_source(self) -> None:
        initial = self._observation()
        repair = self._observation(repair=True, raw=None, status="unavailable")
        expected = copy.deepcopy(self.fixture["cases"][0]["turns"][0]["expected"])
        payload = canonical()
        payload["planner_diagnostics"] = {"plan_completeness": {"status": "incomplete"}, "plan_executability": {"status": "blocked"}}
        result = SimpleNamespace(
            semantic_compiler_packet=payload,
            semantic_compiler_diagnostic=self._diagnostic(raw=None, attempted=True, outcome="incomplete", status="unavailable"),
            semantic_traversal_manifest={"execution": {"layers_executed": []}},
            retrieval_packet={"selected_chunks": []}, runtime_outcome="blocked", coverage_report={"decision": "blocked"},
        )
        metrics = _turn_metrics(result=result, expected=expected, observation=[initial, repair])
        self.assertEqual(metrics["final_response_attempt_role"], "repair")
        self.assertEqual(metrics["final_response_status"], "unavailable")
        self.assertEqual(metrics["plan_source_attempt_role"], "initial")
        self.assertEqual(metrics["plan_source_contract_status"], "valid")
        self.assertEqual(metrics["evaluation_status"], "failed")

    def test_successful_repair_records_repair_as_final_and_plan_source(self) -> None:
        initial = self._observation(raw=json.dumps({"planner_intent": "off contract"}))
        repair = self._observation(repair=True)
        payload = canonical()
        payload["planner_diagnostics"] = {"plan_completeness": {"status": "complete"}, "plan_executability": {"status": "executable"}}
        result = SimpleNamespace(
            semantic_compiler_packet=payload,
            semantic_compiler_diagnostic=self._diagnostic(raw=repair["response"].raw_response, attempted=True, outcome="complete"),
            semantic_traversal_manifest={"execution": {"layers_executed": []}},
            retrieval_packet={"selected_chunks": [{"synthetic": True}]}, runtime_outcome="completed", coverage_report={"decision": "approved"},
        )
        expected = copy.deepcopy(self.fixture["cases"][0]["turns"][0]["expected"])
        metrics = _turn_metrics(result=result, expected=expected, observation=[initial, repair])
        self.assertEqual(metrics["final_response_attempt_role"], "repair")
        self.assertEqual(metrics["plan_source_attempt_role"], "repair")

    def test_successful_repair_with_initial_packet_is_plan_source_incomplete(self) -> None:
        initial = self._observation(raw=json.dumps(canonical(), sort_keys=True))
        repair = self._observation(repair=True)
        payload = canonical()
        payload["planner_diagnostics"] = {"plan_completeness": {"status": "complete"}, "plan_executability": {"status": "executable"}}
        result = SimpleNamespace(
            semantic_compiler_packet=payload,
            semantic_compiler_diagnostic=self._diagnostic(raw=repair["response"].raw_response, attempted=True, outcome="complete"),
            semantic_traversal_manifest={"execution": {"layers_executed": []}},
            retrieval_packet={"selected_chunks": [{"synthetic": True}]}, runtime_outcome="completed", coverage_report={"decision": "approved"},
        )
        expected = copy.deepcopy(self.fixture["cases"][0]["turns"][0]["expected"])
        # Make the retained packet equal only to the initial parsed payload by
        # giving the repair a distinct parsed plan while its raw response still
        # remains a valid observed response.
        repair["response"] = SemanticCompilerResponse({**canonical(), "query": "different synthetic query"}, repair["response"].raw_response, repair["response"].metadata, repair["response"].diagnostics, repair["response"].status)
        metrics = _turn_metrics(result=result, expected=expected, observation=[initial, repair])
        self.assertIsNone(metrics["plan_source_attempt_role"])
        self.assertIn(metrics["evaluation_status"], {"failed", "incomplete"})

    def test_timeout_topology_with_inconsistent_diagnostics_is_rejected(self) -> None:
        initial = self._observation()
        repair = self._observation(repair=True, raw=None, status="unavailable")
        diagnostic = self._diagnostic(raw=None, attempted=True, outcome="incomplete", status="parsed")
        with self.assertRaises(PrivateUATUnavailable):
            _validate_attempt_topology(observations=[initial, repair], diagnostic=diagnostic)

    def test_raw_final_response_without_diagnostic_hash_is_rejected(self) -> None:
        observation = self._observation()
        attempts = _validate_attempt_topology(observations=[observation], diagnostic=self._diagnostic(raw=None, status="parsed"))
        with self.assertRaises(PrivateUATUnavailable):
            _final_response_attempt(attempts=attempts, diagnostic=self._diagnostic(raw=None, status="parsed"))

    def test_failed_structured_case_is_written_and_later_case_continues(self) -> None:
        from tools import implementation08_private_uat as evaluator

        fixture = copy.deepcopy(self.fixture)
        fixture["cases"] = fixture["cases"][:2]

        def make_result(*, blocked: bool) -> SimpleNamespace:
            payload = canonical()
            payload["planner_diagnostics"] = {"plan_completeness": {"status": "incomplete" if blocked else "complete"}, "plan_executability": {"status": "blocked" if blocked else "executable"}}
            return SimpleNamespace(
                assistant_response="synthetic", runtime_outcome="blocked" if blocked else "completed",
                semantic_compiler_packet=payload,
                semantic_compiler_diagnostic=self._diagnostic(raw=None if blocked else json.dumps(canonical()), attempted=blocked, outcome="incomplete" if blocked else "not_needed", status="unavailable" if blocked else "parsed"),
                semantic_traversal_manifest={"execution": {"layers_executed": []}}, retrieval_packet={"selected_chunks": []}, coverage_report={"decision": "blocked" if blocked else "approved"}, llm_metadata={},
            )

        calls = []
        def fake_turn(**kwargs):
            calls.append(kwargs["user_input"])
            initial = self._observation()
            kwargs["semantic_compiler_backend"].observations.append(initial)
            if len(calls) == 1:
                repair = self._observation(repair=True, raw=None, status="unavailable")
                kwargs["semantic_compiler_backend"].observations.append(repair)
                return make_result(blocked=True)
            return make_result(blocked=False)

        with tempfile.TemporaryDirectory() as temp_dir, patch.object(evaluator, "load_runtime_config") as load_config, patch.object(evaluator, "resolve_llm_backend", return_value=SimpleNamespace(unavailable_reason=None)), patch.object(evaluator, "resolve_semantic_compiler_backend", return_value=SimpleNamespace(mode_name="synthetic")), patch.object(evaluator, "run_thread_turn", side_effect=fake_turn):
            root = Path(temp_dir)
            fixture_path = root / "agent_harness/private/fixture.yaml"
            fixture_path.parent.mkdir(parents=True)
            fixture_path.write_text(yaml.safe_dump(fixture), encoding="utf-8")
            data_root = root / "data"
            database = data_root / "ingestion.db"
            database.parent.mkdir(parents=True)
            database.write_text("", encoding="utf-8")
            load_config.return_value = SimpleNamespace(data_root=data_root, storage_ingestion_root=Path("."), storage_ingestion_database_filename="ingestion.db", vault_root=root)
            report = evaluator.run_private_uat(fixture_path=fixture_path, repo_root=root)
            self.assertEqual(report["case_count"], 2)
            records = sorted((root / "agent_harness/private/runs" / fixture["suite_id"] / "raw").glob("*.json"))
            self.assertEqual(len(records), 2)
            self.assertEqual(len(json.loads(records[0].read_text(encoding="utf-8"))["turns"]), 1)
            self.assertEqual(len(calls), 2)

    def test_genuine_production_exception_is_not_swallowed(self) -> None:
        from tools import implementation08_private_uat as evaluator
        fixture = copy.deepcopy(self.fixture)
        fixture["cases"] = fixture["cases"][:1]
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            fixture_path = root / "agent_harness/private/fixture.yaml"
            fixture_path.parent.mkdir(parents=True)
            fixture_path.write_text(yaml.safe_dump(fixture), encoding="utf-8")
            data_root = root / "data"
            database = data_root / "ingestion.db"
            database.parent.mkdir(parents=True)
            database.write_text("", encoding="utf-8")
            config = SimpleNamespace(data_root=data_root, storage_ingestion_root=Path("."), storage_ingestion_database_filename="ingestion.db", vault_root=root)
            with patch.object(evaluator, "load_runtime_config", return_value=config), patch.object(evaluator, "resolve_llm_backend", return_value=SimpleNamespace(unavailable_reason=None)), patch.object(evaluator, "resolve_semantic_compiler_backend", return_value=SimpleNamespace(mode_name="synthetic")), patch.object(evaluator, "run_thread_turn", side_effect=RuntimeError("synthetic production failure")):
                with self.assertRaises(RuntimeError):
                    evaluator.run_private_uat(fixture_path=fixture_path, repo_root=root)

    def test_mismatched_authoritative_response_rejected(self) -> None:
        observation = self._observation()
        attempts = _validate_attempt_topology(observations=[observation], diagnostic=self._diagnostic(raw=observation["response"].raw_response))
        with self.assertRaises(PrivateUATUnavailable):
            _final_response_attempt(attempts=attempts, diagnostic={"semantic_compiler_response_status": "parsed", "raw_response_hash": "wrong", "plan_repair": {"attempted": False}})

    def test_required_operator_plus_additional_operator_matches(self) -> None:
        result = _compare_required(["temporal_retrieve"], ["temporal_retrieve", "vector_search"])
        self.assertEqual(result["status"], "match")
        self.assertEqual(result["missing"], [])
        self.assertEqual(result["additional"], ["vector_search"])

    def test_missing_required_operator_mismatches(self) -> None:
        result = _compare_required(["temporal_retrieve"], ["vector_search"])
        self.assertEqual(result["status"], "mismatch")
        self.assertEqual(result["missing"], ["temporal_retrieve"])

    def test_successful_repair_preserves_initial_contract_status(self) -> None:
        initial = self._observation(raw=json.dumps({"planner_intent": "off contract"}))
        repair = self._observation(repair=True)
        result = SimpleNamespace(
            semantic_compiler_packet=canonical(),
            semantic_compiler_diagnostic=self._diagnostic(raw=repair["response"].raw_response, attempted=True, outcome="complete"),
            semantic_traversal_manifest={"execution": {"layers_executed": []}},
            retrieval_packet={"selected_chunks": []},
            runtime_outcome="completed",
            coverage_report={"decision": "approved"},
        )
        expected = copy.deepcopy(self.fixture["cases"][0]["turns"][0]["expected"])
        metrics = _turn_metrics(result=result, expected=expected, observation=[initial, repair])
        self.assertEqual(metrics["initial_contract_status"], "invalid")
        self.assertEqual(metrics["repair_contract_status"], "valid")
        self.assertTrue(metrics["repair_attempted"])
        self.assertEqual(metrics["repair_outcome"], "complete")

    def test_surface_subject_mismatch_is_review_not_failure(self) -> None:
        payload = canonical()
        payload["planner_retrieval_plan"]["concepts"] = ["different surface"]
        response = SemanticCompilerResponse(payload, json.dumps(payload), {}, {}, "parsed")
        payload["planner_diagnostics"] = {"plan_completeness": {"status": "complete"}, "plan_executability": {"status": "executable"}}
        result = SimpleNamespace(semantic_compiler_packet=payload, semantic_compiler_diagnostic=self._diagnostic(raw=response.raw_response), semantic_traversal_manifest={"execution": {"layers_executed": []}}, retrieval_packet={"selected_chunks": [{"synthetic": True}]}, runtime_outcome="completed", coverage_report={"decision": "approved"})
        expected = copy.deepcopy(self.fixture["cases"][0]["turns"][0]["expected"])
        expected["current_turn_subjects"] = ["expected surface"]
        expected["required_operators"] = []
        expected["evidence_requirements"] = []
        metrics = _turn_metrics(result=result, expected=expected, observation=[{"packet": {"raw_user_input": "synthetic input"}, "response": response}])
        self.assertEqual(metrics["expectations"]["current_turn_subjects"]["status"], "mismatch")
        self.assertEqual(metrics["evaluation_status"], "review_required")

    def test_referent_surface_mismatch_is_review_not_failure(self) -> None:
        payload = canonical()
        payload["resolved_referents"] = ["different referent"]
        response = SemanticCompilerResponse(payload, json.dumps(payload), {}, {}, "parsed")
        payload["planner_diagnostics"] = {"plan_completeness": {"status": "complete"}, "plan_executability": {"status": "executable"}}
        result = SimpleNamespace(semantic_compiler_packet=payload, semantic_compiler_diagnostic=self._diagnostic(raw=response.raw_response), semantic_traversal_manifest={"execution": {"layers_executed": []}}, retrieval_packet={"selected_chunks": [{"synthetic": True}]}, runtime_outcome="completed", coverage_report={"decision": "approved"})
        expected = copy.deepcopy(self.fixture["cases"][0]["turns"][0]["expected"])
        expected["resolved_referents"] = ["expected referent"]
        expected["required_operators"] = []
        expected["evidence_requirements"] = []
        metrics = _turn_metrics(result=result, expected=expected, observation=[{"packet": {"raw_user_input": "synthetic input"}, "response": response}])
        self.assertEqual(metrics["expectations"]["resolved_referents"]["status"], "mismatch")
        self.assertEqual(metrics["evaluation_status"], "review_required")

    def test_hard_contract_failure_remains_failed_even_with_surface_mismatch(self) -> None:
        response = SemanticCompilerResponse({"planner_intent": "off contract"}, json.dumps({"planner_intent": "off contract"}), {}, {}, "parsed")
        result = SimpleNamespace(semantic_compiler_packet=canonical(), semantic_compiler_diagnostic=self._diagnostic(raw=response.raw_response), semantic_traversal_manifest={"execution": {"layers_executed": []}}, retrieval_packet={"selected_chunks": [{"synthetic": True}]}, runtime_outcome="completed", coverage_report={"decision": "approved"})
        expected = copy.deepcopy(self.fixture["cases"][0]["turns"][0]["expected"])
        expected["current_turn_subjects"] = ["different surface"]
        expected["required_operators"] = []
        expected["evidence_requirements"] = []
        metrics = _turn_metrics(result=result, expected=expected, observation=[{"packet": {"raw_user_input": "synthetic input"}, "response": response}])
        self.assertEqual(metrics["evaluation_status"], "failed")


if __name__ == "__main__":
    unittest.main()
