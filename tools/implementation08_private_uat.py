"""Private-UAT evaluator for the unchanged Implementation-08 runtime.

The runner records raw material only below ``agent_harness/private``.  All
evaluation is deterministic and local: the compiler proxy delegates exactly
once per executed turn and observes the response without changing it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from semantic_traversal.config import load_runtime_config
from semantic_traversal.hashing import sha256_text
from semantic_traversal.llm import resolve_llm_backend
from semantic_traversal.resource_inventory import (
    COMPILER_INVENTORY_PROJECTION_VERSION,
    RETRIEVAL_SURFACE_MANIFEST_VERSION,
    INVENTORY_SCHEMA_VERSION,
    validate_inventory_snapshot,
    load_persisted_inventory,
    build_compiler_inventory_projection,
)
from semantic_traversal.runtime import run_thread_turn
from semantic_traversal.semantic_compiler import SemanticCompilerResponse, resolve_semantic_compiler_backend
from semantic_traversal.storage import inspect_thread_state


PRIVATE_RELATIVE_ROOT = Path("agent_harness/private")
FIXTURE_SCHEMA_VERSION = 2
REPORT_SCHEMA_VERSION = 6
EVALUATOR_CONTRACT_VERSION = 5
SUPPORTED_RESPONSE_MODES = {"direct", "traverse"}
ROOT_FIELDS = {"schema_version", "suite_id", "cases"}
CASE_FIELDS = {"id", "description", "turns"}
TURN_FIELDS = {"input", "expected"}
EXPECTED_FIELDS = {
    "response_mode", "current_turn_subjects", "resolved_referents",
    "required_operators", "evidence_requirements", "answer",
    "evidence_boundary", "negative_claim",
}
ANSWER_FIELDS = {"exact_value", "acceptable_values", "resolution"}
BOUNDARY_FIELDS = {
    "required_note_uuids", "acceptable_note_uuids", "answer_bearing_chunk_ids",
    "forbidden_note_uuids", "forbidden_inferences",
}
NEGATIVE_FIELDS = {"permitted", "required_coverage"}
COVERAGE_FIELDS = {"layer", "exhaustive", "scope"}
TOP_LEVEL_COMPILER_FIELDS = {
    "raw_user_input", "intent", "query", "entities", "relations",
    "resolved_referents", "planner_retrieval_plan", "limitations",
}
PLANNER_FIELDS = {
    "intent_type", "concepts", "resolved_referents",
    "literal_terms", "evidence_requirements", "semantic_queries",
    "lexical_queries", "graph_seeds", "retrieval_layers",
}
SAFE_OPERATORS = {"exact_chunk_search", "lexical_chunk_search", "vector_search", "graph_expand", "temporal_retrieve"}


class FixtureError(ValueError):
    """A malformed or unsupported private-UAT fixture."""


class PrivateUATUnavailable(RuntimeError):
    """The configured real runtime cannot execute the private baseline."""


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise FixtureError(f"{label} must be a mapping")
    return value


def _string_list(value: Any, label: str, *, allow_empty: bool = True) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise FixtureError(f"{label} must be a list of non-empty strings")
    if not allow_empty and not value:
        raise FixtureError(f"{label} must not be empty")
    return [item.strip() for item in value]


def _strict_fields(value: dict[str, Any], expected: set[str], label: str) -> None:
    unknown = set(value) - expected
    if unknown:
        raise FixtureError(f"{label} contains unknown fields: {', '.join(sorted(unknown))}")


def _validate_expected(expected: Any, label: str) -> dict[str, Any]:
    expected = _mapping(expected, label)
    _strict_fields(expected, EXPECTED_FIELDS, label)
    missing = EXPECTED_FIELDS - set(expected)
    if missing:
        raise FixtureError(f"{label} missing fields: {', '.join(sorted(missing))}")
    if expected["response_mode"] not in SUPPORTED_RESPONSE_MODES:
        raise FixtureError(f"{label}.response_mode must be direct or traverse")
    for field in ("current_turn_subjects", "resolved_referents", "required_operators", "evidence_requirements"):
        _string_list(expected[field], f"{label}.{field}")

    answer = _mapping(expected["answer"], f"{label}.answer")
    _strict_fields(answer, ANSWER_FIELDS, f"{label}.answer")
    if set(answer) != ANSWER_FIELDS:
        raise FixtureError(f"{label}.answer must contain exact_value, acceptable_values, and resolution")
    if answer["exact_value"] is not None and not isinstance(answer["exact_value"], str):
        raise FixtureError(f"{label}.answer.exact_value must be a string or null")
    _string_list(answer["acceptable_values"], f"{label}.answer.acceptable_values")
    if not isinstance(answer["resolution"], str) or not answer["resolution"].strip():
        raise FixtureError(f"{label}.answer.resolution must be a non-empty string")

    boundary = _mapping(expected["evidence_boundary"], f"{label}.evidence_boundary")
    _strict_fields(boundary, BOUNDARY_FIELDS, f"{label}.evidence_boundary")
    if set(boundary) != BOUNDARY_FIELDS:
        raise FixtureError(f"{label}.evidence_boundary is incomplete")
    for field in BOUNDARY_FIELDS:
        _string_list(boundary[field], f"{label}.evidence_boundary.{field}")

    negative = _mapping(expected["negative_claim"], f"{label}.negative_claim")
    _strict_fields(negative, NEGATIVE_FIELDS, f"{label}.negative_claim")
    if set(negative) != NEGATIVE_FIELDS or not isinstance(negative["permitted"], bool):
        raise FixtureError(f"{label}.negative_claim must contain permitted and required_coverage")
    coverage = negative["required_coverage"]
    if negative["permitted"]:
        coverage = _mapping(coverage, f"{label}.negative_claim.required_coverage")
        _strict_fields(coverage, COVERAGE_FIELDS, f"{label}.negative_claim.required_coverage")
        if set(coverage) != COVERAGE_FIELDS:
            raise FixtureError(f"{label}.negative_claim.required_coverage is incomplete")
        if coverage["layer"] != "exact_chunk_search" or coverage["exhaustive"] is not True:
            raise FixtureError(f"{label}.negative_claim.required_coverage must require exhaustive exact_chunk_search")
        if not isinstance(coverage["scope"], str) or not coverage["scope"].strip():
            raise FixtureError(f"{label}.negative_claim.required_coverage.scope must be non-empty")
    elif coverage is not None:
        raise FixtureError(f"{label} cannot require coverage when negative claims are not permitted")
    return expected


def validate_fixture(document: Any) -> dict[str, Any]:
    root = _mapping(document, "fixture")
    _strict_fields(root, ROOT_FIELDS, "fixture")
    if root.get("schema_version") != FIXTURE_SCHEMA_VERSION:
        if root.get("schema_version") == 1:
            raise FixtureError("schema_version 1 is unsupported; expectations moved from the case level to each individual turn in schema version 2")
        raise FixtureError(f"schema_version must be {FIXTURE_SCHEMA_VERSION}")
    suite_id = root.get("suite_id")
    if not isinstance(suite_id, str) or not suite_id.strip():
        raise FixtureError("suite_id must be a non-empty string")
    cases = root.get("cases")
    if not isinstance(cases, list) or not cases:
        raise FixtureError("cases must be a non-empty list")
    seen: set[str] = set()
    normalized_cases: list[dict[str, Any]] = []
    for case_index, raw_case in enumerate(cases):
        label = f"cases[{case_index}]"
        case = _mapping(raw_case, label)
        _strict_fields(case, CASE_FIELDS, label)
        missing = CASE_FIELDS - set(case)
        if missing:
            raise FixtureError(f"{label} missing fields: {', '.join(sorted(missing))}")
        case_id = case["id"]
        if not isinstance(case_id, str) or not case_id.strip() or case_id in seen:
            raise FixtureError(f"{label}.id must be unique and non-empty")
        seen.add(case_id)
        if not isinstance(case["description"], str) or not case["description"].strip():
            raise FixtureError(f"{label}.description must be a non-empty string")
        turns = case["turns"]
        if not isinstance(turns, list) or not turns:
            raise FixtureError(f"{label}.turns must be a non-empty list")
        normalized_turns: list[dict[str, Any]] = []
        for turn_index, raw_turn in enumerate(turns):
            turn_label = f"{label}.turns[{turn_index}]"
            turn = _mapping(raw_turn, turn_label)
            _strict_fields(turn, TURN_FIELDS, turn_label)
            if set(turn) != TURN_FIELDS:
                raise FixtureError(f"{turn_label} must contain input and expected")
            if not isinstance(turn["input"], str) or not turn["input"].strip():
                raise FixtureError(f"{turn_label}.input must be non-empty")
            normalized_turns.append({"input": turn["input"], "expected": _validate_expected(turn["expected"], f"{turn_label}.expected")})
        normalized_cases.append({"id": case_id.strip(), "description": case["description"].strip(), "turns": normalized_turns})
    return {"schema_version": FIXTURE_SCHEMA_VERSION, "suite_id": suite_id.strip(), "cases": normalized_cases}


def _private_root(repo_root: Path) -> Path:
    return (repo_root / PRIVATE_RELATIVE_ROOT).resolve()


def _assert_private_path(path: Path, private_root: Path, label: str) -> Path:
    resolved = path.resolve()
    try:
        resolved.relative_to(private_root)
    except ValueError as exc:
        raise ValueError(f"{label} must remain beneath {private_root}") from exc
    return resolved


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    temporary.write_text(json.dumps(payload, ensure_ascii=True, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _norm(value: Any) -> str:
    return " ".join(str(value or "").casefold().split())


def _norm_list(value: Any) -> list[str]:
    return [_norm(item) for item in value] if isinstance(value, list) else []


def _compare_lists(expected: Any, observed: Any) -> dict[str, Any]:
    expected_values = list(dict.fromkeys(_norm_list(expected)))
    observed_values = list(dict.fromkeys(_norm_list(observed)))
    missing = [item for item in expected_values if item not in observed_values]
    unexpected = [item for item in observed_values if item not in expected_values]
    return {"expected": expected, "observed": observed, "missing": missing, "unexpected": unexpected, "status": "match" if not missing and not unexpected else "mismatch"}


def _compare_required(expected: Any, observed: Any) -> dict[str, Any]:
    """Compare a required list as subset containment while retaining extras."""
    expected_values = list(dict.fromkeys(_norm_list(expected)))
    observed_values = list(dict.fromkeys(_norm_list(observed)))
    missing = [item for item in expected_values if item not in observed_values]
    additional = [item for item in observed_values if item not in expected_values]
    return {
        "expected": expected,
        "observed": observed,
        "missing": missing,
        "unexpected": additional,
        "additional": additional,
        "status": "match" if not missing else "mismatch",
    }


def _status(value: Any, default: str = "unavailable") -> str:
    return str(value.get("status") or default) if isinstance(value, dict) else default


def _raw_json_status(raw_response: str | None) -> tuple[str, Any]:
    if not isinstance(raw_response, str):
        return "unavailable", None
    try:
        value = json.loads(raw_response)
    except json.JSONDecodeError:
        return "invalid", None
    return ("object", value) if isinstance(value, dict) else ("non_object", value)


def evaluate_compiler_contract(*, packet: dict[str, Any], response: SemanticCompilerResponse, canonical_packet: dict[str, Any]) -> dict[str, Any]:
    raw_status, raw_value = _raw_json_status(response.raw_response)
    missing_top: list[str] = []
    unexpected_top: list[str] = []
    invalid_types: list[str] = []
    missing_planner: list[str] = []
    invalid_planner: list[str] = []
    raw_planner_present = isinstance(raw_value, dict) and isinstance(raw_value.get("planner_retrieval_plan"), dict)
    if isinstance(raw_value, dict):
        missing_top = sorted(TOP_LEVEL_COMPILER_FIELDS - set(raw_value))
        unexpected_top = sorted(set(raw_value) - TOP_LEVEL_COMPILER_FIELDS)
        if raw_value.get("raw_user_input") != packet.get("raw_user_input"):
            invalid_types.append("raw_user_input_preservation")
        for field in ("raw_user_input", "intent", "query"):
            if not isinstance(raw_value.get(field), str):
                invalid_types.append(field)
        for field in ("entities", "relations", "resolved_referents", "limitations"):
            if not isinstance(raw_value.get(field), list):
                invalid_types.append(field)
        planner = raw_value.get("planner_retrieval_plan")
        if not isinstance(planner, dict):
            invalid_types.append("planner_retrieval_plan")
        else:
            missing_planner = sorted(PLANNER_FIELDS - set(planner))
            invalid_planner = sorted(set(planner) - PLANNER_FIELDS)
            for field in ("intent_type",):
                if not isinstance(planner.get(field), str): invalid_planner.append(field)
            for field in ("concepts", "resolved_referents", "evidence_requirements", "semantic_queries", "lexical_queries", "graph_seeds", "retrieval_layers", "literal_terms"):
                if not isinstance(planner.get(field), list): invalid_planner.append(field)
            for index, layer in enumerate(planner.get("retrieval_layers", []) if isinstance(planner.get("retrieval_layers"), list) else []):
                if not isinstance(layer, dict) or not isinstance(layer.get("operator"), str) or not layer.get("operator"):
                    invalid_planner.append(f"retrieval_layers[{index}]")
    if raw_status == "unavailable":
        contract_status = "unavailable"
    else:
        contract_status = "valid" if raw_status == "object" and not missing_top and not unexpected_top and not invalid_types and not missing_planner and not invalid_planner else "invalid"
    canonical_plan_present = isinstance(canonical_packet.get("planner_retrieval_plan"), dict)
    fallback_used = bool(canonical_plan_present and not raw_planner_present)
    return {
        "backend_status": response.status,
        "raw_json_status": raw_status,
        "contract_status": contract_status,
        "raw_user_input_preserved": "raw_user_input_preservation" not in invalid_types,
        "missing_top_level_fields": missing_top,
        "unexpected_top_level_fields": unexpected_top,
        "invalid_field_types": sorted(set(invalid_types)),
        "missing_planner_fields": missing_planner,
        "invalid_planner_fields": sorted(set(invalid_planner)),
        "raw_planner_present": raw_planner_present,
        "canonicalization_required": contract_status != "valid" or response.status != "parsed",
        "fallback_plan_used": fallback_used,
        "raw_response_sha256": sha256_text(response.raw_response) if isinstance(response.raw_response, str) else None,
        "semantic_compiler_prompt_hash": response.metadata.get("semantic_compiler_prompt_hash"),
        "evaluator_contract_version": EVALUATOR_CONTRACT_VERSION,
    }


def _attempt_role(packet: Any) -> str:
    """Classify only from the production request structure."""
    if not isinstance(packet, dict):
        raise PrivateUATUnavailable("semantic compiler attempt request was not a mapping")
    has_repair_context = "repair_context" in packet
    if has_repair_context:
        if not isinstance(packet.get("repair_context"), dict):
            raise PrivateUATUnavailable("semantic compiler repair request had malformed repair_context")
        return "repair"
    return "initial"


def _attempt_record(observation: dict[str, Any], *, attempt_index: int) -> dict[str, Any]:
    response = observation["response"]
    request_payload = observation["packet"]
    role = _attempt_role(request_payload)
    request_json = json.dumps(request_payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    raw_response = response.raw_response
    return {
        "attempt_index": attempt_index,
        "attempt_role": role,
        "request_sha256": sha256_text(request_json),
        "raw_response_sha256": sha256_text(raw_response) if isinstance(raw_response, str) else None,
        "backend_status": response.status,
        "contract_status": None,
        "request": request_payload,
        "raw_response": raw_response,
        "response_metadata": response.metadata,
        "response": response,
    }


def _validate_attempt_topology(*, observations: list[dict[str, Any]], diagnostic: dict[str, Any]) -> list[dict[str, Any]]:
    if not 1 <= len(observations) <= 2:
        raise PrivateUATUnavailable(f"semantic compiler attempt count was {len(observations)}; expected one initial attempt plus at most one repair attempt")
    attempts = [_attempt_record(observation, attempt_index=index) for index, observation in enumerate(observations, start=1)]
    roles = [attempt["attempt_role"] for attempt in attempts]
    if roles[0] != "initial" or roles.count("initial") != 1 or roles.count("repair") > 1:
        raise PrivateUATUnavailable("semantic compiler attempt topology was not initial followed by at most one repair")
    if len(roles) == 2 and roles[1] != "repair":
        raise PrivateUATUnavailable("semantic compiler repair observation did not follow the initial observation")
    repair = diagnostic.get("plan_repair") if isinstance(diagnostic.get("plan_repair"), dict) else {}
    repair_attempted = bool(repair.get("attempted"))
    if len(roles) == 2 and not repair_attempted:
        raise PrivateUATUnavailable("semantic compiler repair observation exists but runtime diagnostics say repair was not attempted")
    if len(roles) == 1 and repair_attempted:
        raise PrivateUATUnavailable("runtime diagnostics claim repair occurred but no repair observation exists")
    if repair_attempted:
        if repair.get("response_status") != attempts[-1]["backend_status"]:
            raise PrivateUATUnavailable("runtime repair diagnostics disagree with the observed repair response status")
        if repair.get("outcome") not in {"complete", "incomplete", "failed"}:
            raise PrivateUATUnavailable("runtime repair diagnostics reported an unknown repair outcome")
    diagnostic_status = diagnostic.get("semantic_compiler_response_status")
    if diagnostic_status != attempts[-1]["backend_status"]:
        raise PrivateUATUnavailable("runtime semantic compiler status disagrees with the final observed response")
    return attempts


def _final_response_attempt(*, attempts: list[dict[str, Any]], diagnostic: dict[str, Any]) -> dict[str, Any]:
    """Return production's final compiler call, even when it returned nothing."""
    authoritative_hash = diagnostic.get("raw_response_hash")
    final = attempts[-1]
    if not isinstance(final.get("raw_response"), str):
        if authoritative_hash is not None:
            raise PrivateUATUnavailable("runtime diagnostic supplied a response hash for a final attempt with no raw response")
        if final.get("backend_status") not in {"unavailable", "timed_out"}:
            raise PrivateUATUnavailable("final compiler response was absent without an unavailable or timed_out backend status")
        return final
    if not isinstance(authoritative_hash, str) or not authoritative_hash:
        raise PrivateUATUnavailable("runtime semantic compiler diagnostic did not identify an authoritative response hash")
    matches = [attempt for attempt in attempts if attempt.get("raw_response_sha256") == authoritative_hash]
    if len(matches) != 1:
        raise PrivateUATUnavailable("final compiler response could not be matched to exactly one authoritative observation")
    return matches[0]


def _packet_projection(packet: Any) -> dict[str, Any] | None:
    if not isinstance(packet, dict) or not isinstance(packet.get("planner_retrieval_plan"), dict):
        return None
    fields = ("raw_user_input", "intent", "query", "entities", "relations", "resolved_referents", "limitations", "planner_retrieval_plan")
    return {field: packet.get(field) for field in fields}


def _plan_source_attempt(*, attempts: list[dict[str, Any]], packet: dict[str, Any], final_attempt: dict[str, Any]) -> dict[str, Any] | None:
    """Find the observed response whose parsed packet equals the retained packet."""
    retained = _packet_projection(packet)
    if retained is None:
        return None
    matches = []
    for attempt in attempts:
        parsed = _packet_projection(attempt["response"].parsed_payload)
        if parsed is not None and parsed == retained:
            matches.append(attempt)
    if isinstance(final_attempt.get("raw_response"), str):
        return final_attempt if final_attempt in matches else None
    if len(matches) == 1:
        return matches[0]
    # A successful final response is still required to prove packet identity;
    # call order alone is not authority. A failed repair may legitimately
    # preserve the initial packet, which is covered by the same comparison.
    return None


class RecordingSemanticCompilerBackend:
    """Transparent proxy: one production call, unchanged response object."""

    def __init__(self, backend: Any) -> None:
        self.backend = backend
        self.mode_name = getattr(backend, "mode_name", "unknown")
        self.observations: list[dict[str, Any]] = []

    def compile_turn(self, packet: dict[str, Any]) -> SemanticCompilerResponse:
        response = self.backend.compile_turn(packet)
        if not isinstance(response, SemanticCompilerResponse):
            raise PrivateUATUnavailable("semantic compiler proxy received an unexpected response object")
        self.observations.append({"packet": dict(packet), "response": response})
        return response


def _coverage_metrics(*, result: Any, expected: dict[str, Any]) -> dict[str, Any]:
    coverage = result.coverage_report if isinstance(result.coverage_report, dict) else {}
    manifest = result.semantic_traversal_manifest if isinstance(result.semantic_traversal_manifest, dict) else {}
    manifest_coverage = manifest.get("coverage") if isinstance(manifest.get("coverage"), dict) else {}
    plan = result.semantic_compiler_packet.get("planner_retrieval_plan", {}) if isinstance(result.semantic_compiler_packet, dict) else {}
    layers = plan.get("retrieval_layers", []) if isinstance(plan, dict) else []
    requested = any(isinstance(layer, dict) and layer.get("operator") == "exact_chunk_search" for layer in layers)
    execution = manifest.get("execution") if isinstance(manifest.get("execution"), dict) else {}
    executed_names = execution.get("layers_executed", []) if isinstance(execution.get("layers_executed"), list) else []
    skipped = execution.get("layers_skipped", []) if isinstance(execution.get("layers_skipped"), list) else []
    exact_executed = "exact_chunk_search" in executed_names
    exact_skipped = any((item.get("layer") if isinstance(item, dict) else item) == "exact_chunk_search" for item in skipped)
    runtime_allowed = bool(manifest_coverage.get("negative_claims_allowed", coverage.get("negative_claims_allowed", False)))
    required = expected["negative_claim"].get("required_coverage")
    required_satisfied = True
    reason = "not_required"
    if expected["negative_claim"]["permitted"]:
        scope_ok = _coverage_scope_matches(manifest_coverage.get("scope"), required.get("scope") if isinstance(required, dict) else None)
        exact_status = manifest_coverage.get("exact_status")
        inadequate = manifest_coverage.get("inadequate_required_exact_terms") or []
        required_satisfied = requested and exact_executed and not exact_skipped and exact_status in {"completed_no_matches", "completed_with_matches"} and not inadequate and scope_ok
        reason = "authorized" if runtime_allowed and required_satisfied else "exact_coverage_not_authorized"
    return {
        "fixture_permits_negative_claim": bool(expected["negative_claim"]["permitted"]),
        "fixture_required_coverage": required,
        "exact_layer_requested": requested,
        "exact_layer_executed": exact_executed,
        "exact_search_performed": bool(manifest_coverage.get("exact_search_performed", False)),
        "exact_status": manifest_coverage.get("exact_status", "unavailable"),
        "required_exact_terms": manifest_coverage.get("required_exact_terms", []),
        "inadequate_required_exact_terms": manifest_coverage.get("inadequate_required_exact_terms", []),
        "total_exact_matches": manifest_coverage.get("total_exact_matches"),
        "scope": manifest_coverage.get("scope"),
        "runtime_negative_claims_allowed": runtime_allowed,
        "required_coverage_satisfied": required_satisfied,
        "claim_authorized": runtime_allowed,
        "authorization_reason": reason,
        "coverage_decision": coverage.get("decision", "unavailable"),
    }


def _coverage_scope_matches(runtime_scope: Any, required_scope: Any) -> bool:
    """Compare semantic coverage scope without equating aliases to filters."""
    if required_scope != "complete_eligible_corpus":
        return runtime_scope == required_scope
    if runtime_scope == "complete_eligible_corpus":
        return True
    if not isinstance(runtime_scope, dict):
        return False
    return (
        runtime_scope.get("source_label") in (None, "")
        and runtime_scope.get("note_type") in (None, [], "")
        and runtime_scope.get("path_contains") in (None, [], "")
    )


def _turn_metrics(*, result: Any, expected: dict[str, Any], observation: dict[str, Any] | list[dict[str, Any]]) -> dict[str, Any]:
    packet = result.semantic_compiler_packet if isinstance(result.semantic_compiler_packet, dict) else {}
    plan = packet.get("planner_retrieval_plan") if isinstance(packet.get("planner_retrieval_plan"), dict) else {}
    manifest = result.semantic_traversal_manifest if isinstance(result.semantic_traversal_manifest, dict) else {}
    retrieval = result.retrieval_packet if isinstance(result.retrieval_packet, dict) else {}
    selected = retrieval.get("selected_chunks") if isinstance(retrieval.get("selected_chunks"), list) else []
    execution = manifest.get("execution") if isinstance(manifest.get("execution"), dict) else {}
    requested_operators = [str(layer.get("operator")) for layer in plan.get("retrieval_layers", []) if isinstance(layer, dict) and layer.get("operator")]
    observed_subjects = plan.get("concepts") or packet.get("entities") or []
    observed_referents = packet.get("resolved_referents") or plan.get("resolved_referents") or []
    observed_evidence = plan.get("evidence_requirements", [])
    components = {
        "response_mode": _compare_lists([expected["response_mode"]], ["traverse"]),
        "current_turn_subjects": _compare_lists(expected["current_turn_subjects"], observed_subjects),
        "resolved_referents": _compare_lists(expected["resolved_referents"], observed_referents),
        "required_operators": _compare_required(expected["required_operators"], requested_operators),
        "evidence_requirements": _compare_required(expected["evidence_requirements"], observed_evidence),
    }
    observations = observation if isinstance(observation, list) else [observation]
    diagnostic = result.semantic_compiler_diagnostic if isinstance(result.semantic_compiler_diagnostic, dict) else {}
    attempts = _validate_attempt_topology(observations=observations, diagnostic=diagnostic)
    final_response_attempt = _final_response_attempt(attempts=attempts, diagnostic=diagnostic)
    plan_source_attempt = _plan_source_attempt(
        attempts=attempts,
        packet=packet,
        final_attempt=final_response_attempt,
    )
    compiler_contracts = []
    for attempt in attempts:
        contract = evaluate_compiler_contract(packet=attempt["request"], response=attempt["response"], canonical_packet=packet)
        attempt["contract_status"] = contract["contract_status"]
        compiler_contracts.append(contract)
    compiler_contract = compiler_contracts[final_response_attempt["attempt_index"] - 1]
    repair = diagnostic.get("plan_repair") if isinstance(diagnostic.get("plan_repair"), dict) else {}
    coverage = _coverage_metrics(result=result, expected=expected)
    plan_complete = packet.get("planner_diagnostics", {}).get("plan_completeness", {"status": "unavailable"})
    plan_executable = packet.get("planner_diagnostics", {}).get("plan_executability", {"status": "unavailable"})
    components["semantic_compiler_contract"] = {"status": compiler_contract["contract_status"]}
    components["plan_completeness"] = {"status": _status(plan_complete)}
    components["plan_executability"] = {"status": _status(plan_executable)}
    components["negative_claim_coverage"] = {"status": "pass" if coverage["required_coverage_satisfied"] else ("not_required" if not coverage["fixture_permits_negative_claim"] else "mismatch")}
    components["runtime_terminal_outcome"] = {"status": "pass" if result.runtime_outcome == "completed" else result.runtime_outcome}
    hard_components = {
        "response_mode", "required_operators", "evidence_requirements",
        "semantic_compiler_contract", "plan_completeness", "plan_executability",
        "negative_claim_coverage", "runtime_terminal_outcome",
    }
    deterministic = list(components.values())
    failed = any(components[name].get("status") in {"mismatch", "invalid", "blocked"} for name in hard_components)
    unavailable = any(item.get("status") == "unavailable" for item in deterministic)
    if plan_source_attempt is None:
        unavailable = True
    if failed:
        evaluation_status = "failed"
    elif unavailable:
        evaluation_status = "incomplete"
    else:
        evaluation_status = "review_required"
    return {
        "expectations": components,
        "compiler_contract": compiler_contract,
        "compiler_attempts": [
            {
                "attempt_index": attempt["attempt_index"],
                "attempt_role": attempt["attempt_role"],
                "request_sha256": attempt["request_sha256"],
                "raw_response_sha256": attempt["raw_response_sha256"],
                "backend_status": attempt["backend_status"],
                "contract_status": attempt["contract_status"],
            }
            for attempt in attempts
        ],
        "compiler_attempt_count": len(attempts),
        "repair_attempted": bool(repair.get("attempted")),
        "repair_outcome": repair.get("outcome", "not_needed"),
        "initial_backend_status": attempts[0]["backend_status"],
        "initial_contract_status": compiler_contracts[0]["contract_status"],
        "repair_backend_status": attempts[1]["backend_status"] if len(attempts) == 2 else None,
        "repair_contract_status": compiler_contracts[1]["contract_status"] if len(attempts) == 2 else None,
        "final_response_attempt_role": final_response_attempt["attempt_role"],
        "final_response_status": compiler_contracts[final_response_attempt["attempt_index"] - 1]["contract_status"],
        "plan_source_attempt_role": plan_source_attempt["attempt_role"] if plan_source_attempt else None,
        "plan_source_contract_status": compiler_contracts[plan_source_attempt["attempt_index"] - 1]["contract_status"] if plan_source_attempt else "unavailable",
        "plan_completeness": plan_complete,
        "plan_executability": plan_executable,
        "coverage": coverage,
        "candidate_counts_by_surface": manifest.get("candidate_counts", {"status": "unavailable"}),
        "selected_evidence_presence": bool(selected),
        "selected_evidence_precision": {"status": "unavailable", "reason": "requires independent evidence adjudication"},
        "synthesis_support": {"status": "unavailable", "reason": "requires independent support adjudication"},
        "final_answer_correctness": {"status": "unavailable", "reason": "requires operator adjudication"},
        "requested_operators": requested_operators,
        "executed_operators": execution.get("layers_executed", []),
        "runtime_status": result.runtime_outcome,
        "evaluation_status": evaluation_status,
    }


def _load_raw_records(run_dir: Path) -> list[dict[str, Any]]:
    return [json.loads(path.read_text(encoding="utf-8")) for path in sorted((run_dir / "raw").glob("*.json"))]


def _thread_id_for_case(*, suite_id: str, case_id: str) -> str:
    return "private-uat-" + hashlib.sha256(f"{suite_id}:{case_id}".encode()).hexdigest()[:24]


def _preflight_pending_threads(*, fixture: dict[str, Any], completed: set[str], data_root: Path, config: Any) -> dict[str, Any]:
    pending = [case for case in fixture["cases"] if case["id"] not in completed]
    clean_count = 0
    contaminated_count = 0
    for case in pending:
        status = inspect_thread_state(
            data_root,
            config=config,
            thread_id=_thread_id_for_case(suite_id=fixture["suite_id"], case_id=case["id"]),
        )
        if status.get("nonempty"):
            contaminated_count += 1
        else:
            clean_count += 1
    result = {
        "status": "clean" if contaminated_count == 0 else "contaminated",
        "pending_case_count": len(pending),
        "clean_thread_count": clean_count,
        "contaminated_thread_count": contaminated_count,
        "model_calls_began": False,
    }
    if contaminated_count:
        raise PrivateUATUnavailable(
            "private UAT thread preflight failed: pending deterministic threads are not clean "
            f"(pending={len(pending)}, clean={clean_count}, contaminated={contaminated_count}); use a new suite_id"
        )
    return result


def _redacted_turn(turn: dict[str, Any]) -> dict[str, Any]:
    metrics = turn.get("metrics", {})
    expectations = metrics.get("expectations", {})
    contract = metrics.get("compiler_contract", {})
    coverage = metrics.get("coverage", {})
    def component(name: str) -> str:
        return str(expectations.get(name, {}).get("status", "unavailable"))
    required_operator = expectations.get("required_operators", {})
    subjects = expectations.get("current_turn_subjects", {})
    referents = expectations.get("resolved_referents", {})
    return {
        "turn_id": turn.get("turn_id"),
        "runtime_status": metrics.get("runtime_status", "unavailable"),
        "evaluation_status": metrics.get("evaluation_status", "incomplete"),
        "compiler_json_status": contract.get("raw_json_status", "unavailable"),
        "compiler_contract_status": contract.get("contract_status", "unavailable"),
        "fallback_plan_used": bool(contract.get("fallback_plan_used", False)),
        "compiler_attempt_count": metrics.get("compiler_attempt_count", 0),
        "repair_attempted": bool(metrics.get("repair_attempted", False)),
        "repair_outcome": metrics.get("repair_outcome", "unavailable"),
        "initial_backend_status": metrics.get("initial_backend_status", "unavailable"),
        "initial_contract_status": metrics.get("initial_contract_status", "unavailable"),
        "repair_backend_status": metrics.get("repair_backend_status"),
        "repair_contract_status": metrics.get("repair_contract_status"),
        "final_response_attempt_role": metrics.get("final_response_attempt_role", "unavailable"),
        "final_response_status": metrics.get("final_response_status", "unavailable"),
        "plan_source_attempt_role": metrics.get("plan_source_attempt_role", "unavailable"),
        "plan_source_contract_status": metrics.get("plan_source_contract_status", "unavailable"),
        "response_mode_expectation": component("response_mode"),
        "subject_expectation": component("current_turn_subjects"),
        "referent_expectation": component("resolved_referents"),
        "operator_expectation": component("required_operators"),
        "required_operator_status": required_operator.get("status", "unavailable"),
        "additional_operator_count": len(required_operator.get("additional", required_operator.get("unexpected", [])) or []),
        "subject_surface_status": subjects.get("status", "unavailable"),
        "referent_surface_status": referents.get("status", "unavailable"),
        "subject_surface_expected_count": len(subjects.get("expected", []) or []),
        "subject_surface_observed_count": len(subjects.get("observed", []) or []),
        "referent_surface_expected_count": len(referents.get("expected", []) or []),
        "referent_surface_observed_count": len(referents.get("observed", []) or []),
        "evidence_requirement_expectation": component("evidence_requirements"),
        "plan_completeness": component("plan_completeness"),
        "plan_executability": component("plan_executability"),
        "coverage_decision": coverage.get("coverage_decision", "unavailable"),
        "negative_claim_authorized": bool(coverage.get("claim_authorized", False)),
        "final_answer_correctness": metrics.get("final_answer_correctness", {}).get("status", "unavailable"),
        "synthesis_support": metrics.get("synthesis_support", {}).get("status", "unavailable"),
        "requested_operators": [name for name in metrics.get("requested_operators", []) if name in SAFE_OPERATORS],
        "executed_operators": [name for name in metrics.get("executed_operators", []) if name in SAFE_OPERATORS],
    }


def export_redacted(*, run_dir: Path, output_path: Path) -> dict[str, Any]:
    manifest = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    cases = []
    for case_index, record in enumerate(_load_raw_records(run_dir), start=1):
        turns = [_redacted_turn(turn) for turn in record.get("turns", [])]
        statuses = [turn["evaluation_status"] for turn in turns]
        case_status = "failed" if "failed" in statuses else "incomplete" if "incomplete" in statuses else "review_required"
        cases.append({"case_id": f"case-{case_index}", "runtime_status": "completed" if all(turn["runtime_status"] == "completed" for turn in turns) else "blocked", "evaluation_status": case_status, "turn_count": len(turns), "turns": turns})
    report = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "evaluator_contract_version": EVALUATOR_CONTRACT_VERSION,
        "suite_id": manifest["suite_id"],
        "case_count": len(cases),
        "cases": cases,
        "fixture_sha256": manifest.get("fixture_sha256"),
        "raw_run_sha256": manifest.get("run_sha256"),
        "adjudication": {"final_answer_correctness": "unavailable", "synthesis_support": "unavailable"},
    }
    _atomic_json(output_path, report)
    return report


def run_private_uat(*, fixture_path: Path, repo_root: Path, output_dir: Path | None = None, replace: bool = False, validate_only: bool = False, export_path: Path | None = None) -> dict[str, Any]:
    private_root = _private_root(repo_root)
    resolved_fixture = fixture_path.resolve()
    if validate_only:
        try:
            resolved_fixture.relative_to(repo_root.resolve())
        except ValueError as exc:
            raise ValueError("validation-only fixture must remain beneath the repository root") from exc
        fixture_path = resolved_fixture
    else:
        fixture_path = _assert_private_path(resolved_fixture, private_root, "fixture")
    fixture_text = fixture_path.read_text(encoding="utf-8")
    fixture = validate_fixture(yaml.safe_load(fixture_text))
    fixture_sha = sha256_text(fixture_text)
    if validate_only:
        return {"status": "validated", "schema_version": FIXTURE_SCHEMA_VERSION, "suite_id": fixture["suite_id"], "case_count": len(fixture["cases"]), "fixture_sha256": fixture_sha}
    run_dir = _assert_private_path(output_dir or (private_root / "runs" / fixture["suite_id"]), private_root, "output directory")
    run_manifest_path = run_dir / "run.json"
    if run_manifest_path.exists() and not replace:
        existing = json.loads(run_manifest_path.read_text(encoding="utf-8"))
        if existing.get("evaluator_contract_version") != EVALUATOR_CONTRACT_VERSION or existing.get("report_schema_version") != REPORT_SCHEMA_VERSION:
            raise PrivateUATUnavailable("output was produced under a different evaluator/report contract; use a fresh suite_id")
        if existing.get("fixture_sha256") != fixture_sha:
            raise PrivateUATUnavailable("output contains an authoritative run for a different fixture; use --replace explicitly")
    if replace and run_manifest_path.exists():
        for path in (run_dir / "raw").glob("*.json"):
            path.unlink()
    config = load_runtime_config(repo_root=repo_root)
    completed = {record.get("case_id") for record in _load_raw_records(run_dir)}
    database_path = (config.data_root / config.storage_ingestion_root / config.storage_ingestion_database_filename).resolve()
    if not config.vault_root.exists():
        raise PrivateUATUnavailable(f"configured vault is unavailable: {config.vault_root}")
    if not database_path.exists():
        raise PrivateUATUnavailable(f"persisted ingestion database is unavailable: {database_path}")
    # Refuse to begin model calls against stale or reconstructed substrate.
    # This is intentionally a safe preflight: it exposes only statuses,
    # versions, and hashes, never corpus content.
    with sqlite3.connect(database_path) as connection:
        validation = validate_inventory_snapshot(connection=connection, config=config)
        if validation.get("status") != "valid":
            raise PrivateUATUnavailable("persisted inventory preflight failed: inventory status is not valid")
        summary, diagnostics = load_persisted_inventory(connection=connection, config=config)
        manifest = summary.get("retrieval_surface_manifest") if isinstance(summary.get("retrieval_surface_manifest"), dict) else {}
        projection, projection_diagnostics = build_compiler_inventory_projection(inventory_summary=summary, config=config)
        if diagnostics.get("source") != "persisted" or diagnostics.get("status") != "valid":
            raise PrivateUATUnavailable("persisted inventory preflight failed: inventory source is not persisted")
        if int(summary.get("inventory_diagnostics", {}).get("status", 0) == "valid") != 1:
            raise PrivateUATUnavailable("persisted inventory preflight failed: inventory status is not valid")
        if int(manifest.get("manifest_version", 0)) != RETRIEVAL_SURFACE_MANIFEST_VERSION:
            raise PrivateUATUnavailable("persisted inventory preflight failed: retrieval manifest version mismatch")
        if int(projection.get("projection_version", 0)) != COMPILER_INVENTORY_PROJECTION_VERSION:
            raise PrivateUATUnavailable("persisted inventory preflight failed: compiler projection version mismatch")
        capabilities = summary.get("capabilities") if isinstance(summary.get("capabilities"), dict) else {}
        exact_ok = capabilities.get("exact_fts", {}).get("projection_status") == "valid"
        vector_ok = capabilities.get("vector", {}).get("validation_status") == "valid"
        graph_ok = all(capabilities.get("graph", {}).get(key, {}).get("table_present") for key in ("nodes", "edges"))
        temporal_ok = bool(capabilities.get("temporal", {}).get("table_present"))
        if not all((exact_ok, vector_ok, graph_ok, temporal_ok)):
            raise PrivateUATUnavailable("persisted inventory preflight failed: required index is invalid")
    thread_preflight = _preflight_pending_threads(
        fixture=fixture,
        completed=completed,
        data_root=config.data_root,
        config=config,
    )
    llm_backend = resolve_llm_backend(repo_root=repo_root, config=config, llm_mode="auto")
    if getattr(llm_backend, "unavailable_reason", None):
        raise PrivateUATUnavailable(str(llm_backend.unavailable_reason))
    compiler = RecordingSemanticCompilerBackend(resolve_semantic_compiler_backend(config=config))
    run_dir.mkdir(parents=True, exist_ok=True)
    _atomic_json(run_manifest_path, {"report_schema_version": REPORT_SCHEMA_VERSION, "evaluator_contract_version": EVALUATOR_CONTRACT_VERSION, "fixture_schema_version": FIXTURE_SCHEMA_VERSION, "suite_id": fixture["suite_id"], "fixture_sha256": fixture_sha, "run_sha256": None, "thread_preflight": thread_preflight})
    executed_turn_count = 0
    for case in fixture["cases"]:
        if case["id"] in completed:
            continue
        thread_id = _thread_id_for_case(suite_id=fixture["suite_id"], case_id=case["id"])
        turn_records = []
        for turn_index, turn in enumerate(case["turns"], start=1):
            executed_turn_count += 1
            before = len(compiler.observations)
            result = run_thread_turn(repo_root=repo_root, data_root=config.data_root, user_input=turn["input"], thread_id=thread_id, config=config, llm_backend=llm_backend, semantic_compiler_backend=compiler)
            observed = compiler.observations[before:]
            diagnostic = result.semantic_compiler_diagnostic if isinstance(result.semantic_compiler_diagnostic, dict) else {}
            attempts = _validate_attempt_topology(observations=observed, diagnostic=diagnostic)
            metrics = _turn_metrics(result=result, expected=turn["expected"], observation=observed)
            final_attempt_index = next(attempt["attempt_index"] for attempt in attempts if attempt["attempt_role"] == metrics["final_response_attempt_role"] and attempt["attempt_index"] == len(attempts))
            plan_source_index = next((attempt["attempt_index"] for attempt in attempts if attempt["attempt_role"] == metrics["plan_source_attempt_role"]), None)
            turn_records.append({"turn_id": turn_index, "input": turn["input"], "expected": turn["expected"], "metrics": metrics, "result": {"assistant_response": result.assistant_response, "runtime_outcome": result.runtime_outcome, "semantic_compiler_packet": result.semantic_compiler_packet, "semantic_compiler_diagnostic": result.semantic_compiler_diagnostic, "semantic_traversal_manifest": result.semantic_traversal_manifest, "retrieval_packet": result.retrieval_packet, "coverage_report": result.coverage_report, "llm_metadata": result.llm_metadata}, "compiler_attempts": [{"attempt_index": attempt["attempt_index"], "attempt_role": attempt["attempt_role"], "request_sha256": attempt["request_sha256"], "raw_response_sha256": attempt["raw_response_sha256"], "backend_status": attempt["backend_status"], "contract_status": metrics["compiler_attempts"][attempt["attempt_index"] - 1]["contract_status"], "request": attempt["request"], "raw_response": attempt["raw_response"], "metadata": attempt["response_metadata"]} for attempt in attempts], "final_response_attempt_index": final_attempt_index, "plan_source_attempt_index": plan_source_index})
            # A structured blocked result is a valid failed observation, but
            # later turns in the same thread would not have valid setup state.
            if result.runtime_outcome != "completed":
                break
        _atomic_json(run_dir / "raw" / f"{case['id']}.json", {"case_id": case["id"], "description": case["description"], "turns": turn_records})
    if not executed_turn_count <= len(compiler.observations) <= executed_turn_count * 2:
        raise PrivateUATUnavailable("observed semantic compiler call count was outside the permitted initial-plus-repair range")
    records = _load_raw_records(run_dir)
    run_sha = sha256_text(json.dumps(records, ensure_ascii=True, sort_keys=True, separators=(",", ":")))
    thread_preflight["model_calls_began"] = bool(executed_turn_count)
    _atomic_json(run_manifest_path, {"report_schema_version": REPORT_SCHEMA_VERSION, "evaluator_contract_version": EVALUATOR_CONTRACT_VERSION, "fixture_schema_version": FIXTURE_SCHEMA_VERSION, "suite_id": fixture["suite_id"], "fixture_sha256": fixture_sha, "run_sha256": run_sha, "case_count": len(records), "thread_preflight": thread_preflight})
    report = {"status": "completed", "suite_id": fixture["suite_id"], "case_count": len(records), "run_dir": str(run_dir)}
    if export_path:
        export_path = _assert_private_path(export_path, private_root, "redacted output")
        report["redacted_report"] = str(export_path)
        export_redacted(run_dir=run_dir, output_path=export_path)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the private Implementation-08 baseline against the configured real corpus.", allow_abbrev=False)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--export-redacted", type=Path)
    parser.add_argument("--replace", action="store_true")
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args(argv)
    try:
        print(json.dumps(run_private_uat(fixture_path=args.fixture, repo_root=args.repo_root.resolve(), output_dir=args.output_dir, replace=args.replace, validate_only=args.validate_only, export_path=args.export_redacted), indent=2, sort_keys=True))
        return 0
    except (FixtureError, FileNotFoundError, OSError, PrivateUATUnavailable, ValueError) as exc:
        print(f"private UAT unavailable or invalid: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
