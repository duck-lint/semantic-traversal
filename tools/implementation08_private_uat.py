"""Run the operator-supplied Implementation-08 private UAT.

The fixture and raw records are deliberately private. The runner delegates
each turn to the existing production runtime and only derives metrics from
artifacts that runtime actually returns; unavailable measurements stay
explicitly unavailable.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
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
from semantic_traversal.runtime import run_thread_turn
from semantic_traversal.semantic_compiler import resolve_semantic_compiler_backend


PRIVATE_RELATIVE_ROOT = Path("agent_harness/private")
SUPPORTED_RESPONSE_MODES = {"direct", "traverse"}
REQUIRED_CASE_FIELDS = {"id", "description", "turns", "expected"}
REQUIRED_EXPECTED_FIELDS = {
    "response_mode", "current_turn_subjects", "resolved_referents",
    "required_operators", "evidence_requirements", "answer",
    "evidence_boundary", "negative_claim",
}


class FixtureError(ValueError):
    """A malformed or unsupported private-UAT fixture."""


class PrivateUATUnavailable(RuntimeError):
    """The configured real runtime cannot execute the private baseline."""


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise FixtureError(f"{label} must be a mapping")
    return value


def _string_list(value: Any, label: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise FixtureError(f"{label} must be a list of non-empty strings")
    return [item.strip() for item in value]


def validate_fixture(document: Any) -> dict[str, Any]:
    root = _mapping(document, "fixture")
    if root.get("schema_version") != 1:
        raise FixtureError("schema_version must be 1")
    suite_id = root.get("suite_id")
    if not isinstance(suite_id, str) or not suite_id.strip():
        raise FixtureError("suite_id must be a non-empty string")
    cases = root.get("cases")
    if not isinstance(cases, list) or not cases:
        raise FixtureError("cases must be a non-empty list")
    seen_ids: set[str] = set()
    normalized_cases: list[dict[str, Any]] = []
    for index, raw_case in enumerate(cases):
        case = _mapping(raw_case, f"cases[{index}]")
        missing = REQUIRED_CASE_FIELDS - set(case)
        if missing:
            raise FixtureError(f"cases[{index}] missing fields: {', '.join(sorted(missing))}")
        case_id = case["id"]
        if not isinstance(case_id, str) or not case_id.strip() or case_id in seen_ids:
            raise FixtureError(f"cases[{index}].id must be unique and non-empty")
        seen_ids.add(case_id)
        if not isinstance(case["description"], str) or not case["description"].strip():
            raise FixtureError(f"cases[{index}].description must be a non-empty string")
        turns = case["turns"]
        if not isinstance(turns, list) or not turns:
            raise FixtureError(f"cases[{index}].turns must be a non-empty list")
        normalized_turns = []
        for turn_index, raw_turn in enumerate(turns):
            turn = _mapping(raw_turn, f"cases[{index}].turns[{turn_index}]")
            if set(turn) != {"input"}:
                raise FixtureError(f"cases[{index}].turns[{turn_index}] must contain only input")
            if not isinstance(turn["input"], str) or not turn["input"].strip():
                raise FixtureError(f"cases[{index}].turns[{turn_index}].input must be non-empty")
            normalized_turns.append({"input": turn["input"]})
        expected = _mapping(case["expected"], f"cases[{index}].expected")
        missing_expected = REQUIRED_EXPECTED_FIELDS - set(expected)
        if missing_expected:
            raise FixtureError(f"cases[{index}].expected missing fields: {', '.join(sorted(missing_expected))}")
        if expected["response_mode"] not in SUPPORTED_RESPONSE_MODES:
            raise FixtureError(f"cases[{index}].expected.response_mode must be direct or traverse")
        for field in ("current_turn_subjects", "resolved_referents", "required_operators", "evidence_requirements"):
            _string_list(expected[field], f"cases[{index}].expected.{field}")
        answer = _mapping(expected["answer"], f"cases[{index}].expected.answer")
        if set(answer) != {"exact_value", "acceptable_values", "resolution"}:
            raise FixtureError(f"cases[{index}].expected.answer has an unsupported shape")
        _string_list(answer["acceptable_values"], f"cases[{index}].expected.answer.acceptable_values")
        if answer["resolution"] is not None and not isinstance(answer["resolution"], str):
            raise FixtureError(f"cases[{index}].expected.answer.resolution must be a string or null")
        boundary = _mapping(expected["evidence_boundary"], f"cases[{index}].expected.evidence_boundary")
        for field in ("required_note_uuids", "acceptable_note_uuids", "answer_bearing_chunk_ids", "forbidden_note_uuids", "forbidden_inferences"):
            _string_list(boundary.get(field), f"cases[{index}].expected.evidence_boundary.{field}")
        negative = _mapping(expected["negative_claim"], f"cases[{index}].expected.negative_claim")
        if set(negative) != {"permitted", "required_coverage"} or not isinstance(negative["permitted"], bool):
            raise FixtureError(f"cases[{index}].expected.negative_claim must contain permitted and required_coverage")
        if negative["permitted"] and not isinstance(negative["required_coverage"], dict):
            raise FixtureError(f"cases[{index}] permitted negative claims require required_coverage")
        if not negative["permitted"] and negative["required_coverage"] is not None:
            raise FixtureError(f"cases[{index}] non-negative cases must set required_coverage to null")
        normalized_cases.append({"id": case_id, "description": case["description"], "turns": normalized_turns, "expected": case["expected"]})
    return {"schema_version": 1, "suite_id": suite_id.strip(), "cases": normalized_cases}


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


def _unavailable(reason: str) -> dict[str, Any]:
    return {"status": "unavailable", "reason": reason}


def _turn_metrics(result: Any, expected: dict[str, Any]) -> dict[str, Any]:
    packet = result.semantic_compiler_packet
    plan = packet.get("planner_retrieval_plan") if isinstance(packet.get("planner_retrieval_plan"), dict) else {}
    manifest = result.semantic_traversal_manifest if isinstance(result.semantic_traversal_manifest, dict) else {}
    retrieval = result.retrieval_packet if isinstance(result.retrieval_packet, dict) else {}
    selected = retrieval.get("selected_chunks") if isinstance(retrieval.get("selected_chunks"), list) else []
    execution = manifest.get("execution") if isinstance(manifest.get("execution"), dict) else {}
    return {
        "expected_current_turn_subjects": expected["current_turn_subjects"],
        "emitted_current_turn_subjects": plan.get("concepts") or packet.get("entities") or [],
        "expected_referents": expected["resolved_referents"],
        "resolved_referents": packet.get("resolved_referents") or plan.get("resolved_referents") or [],
        "expected_operators": expected["required_operators"],
        "requested_operators": [str(layer.get("operator")) for layer in plan.get("retrieval_layers", []) if isinstance(layer, dict) and layer.get("operator")],
        "executed_operators": execution.get("layers_executed", []),
        "expected_evidence_requirements": expected["evidence_requirements"],
        "observed_evidence_requirements": plan.get("evidence_requirements", []),
        "plan_completeness": packet.get("planner_diagnostics", {}).get("plan_completeness", _unavailable("runtime did not expose plan completeness")),
        "plan_executability": packet.get("planner_diagnostics", {}).get("plan_executability", _unavailable("runtime did not expose plan executability")),
        "execution_outcome": result.runtime_outcome,
        "blocking_reason": "runtime_blocked" if result.runtime_outcome != "completed" else None,
        "candidate_counts_by_surface": manifest.get("candidate_counts", _unavailable("runtime did not expose candidate counts")),
        "answer_bearing_candidate_presence": _unavailable("answer-bearing status requires an operator answer oracle"),
        "selected_evidence_presence": bool(selected),
        "selected_evidence_precision": _unavailable("precision requires an evidence adjudication oracle"),
        "irrelevant_selected_evidence_count": _unavailable("irrelevance requires an evidence adjudication oracle"),
        "final_support_grade": _unavailable("support grade requires evidence adjudication"),
        "final_answer_correctness": _unavailable("correctness requires operator inspection"),
        "planner_latency": _unavailable("production runtime does not expose planner latency in TurnExecutionResult"),
        "retrieval_latency": _unavailable("production runtime does not expose retrieval latency in TurnExecutionResult"),
        "synthesis_latency": _unavailable("production runtime does not expose synthesis latency in TurnExecutionResult"),
        "terminal_status": result.runtime_outcome,
        "negative_claim": {
            "permitted": bool(expected["negative_claim"]["permitted"]),
            "required_coverage": expected["negative_claim"]["required_coverage"],
            "coverage_report": result.coverage_report.get("decision") if isinstance(result.coverage_report, dict) else "unavailable",
            "claim_authorized": bool(expected["negative_claim"]["permitted"]) and bool(isinstance(result.coverage_report, dict) and result.coverage_report.get("decision") == "approved"),
        },
    }


def _load_raw_records(run_dir: Path) -> list[dict[str, Any]]:
    records = []
    for path in sorted((run_dir / "raw").glob("*.json")):
        records.append(json.loads(path.read_text(encoding="utf-8")))
    return records


def export_redacted(*, run_dir: Path, output_path: Path) -> dict[str, Any]:
    manifest = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    raw_records = _load_raw_records(run_dir)
    cases = []
    for record in raw_records:
        statuses = [turn.get("metrics", {}).get("terminal_status") for turn in record.get("turns", [])]
        cases.append({
            "case_id": record["case_id"],
            "status": "completed" if all(status == "completed" for status in statuses) else ("blocked" if any(status == "blocked" for status in statuses) else "unavailable"),
            "component_statuses": {"plan": (record.get("turns", [{}])[0].get("metrics", {}).get("plan_completeness", {}).get("status", "unavailable")), "candidate_recall": "unavailable", "selection_precision": "unavailable", "synthesis_support": "unavailable", "final_answer_correctness": "unavailable"},
            "operator_names": sorted({operator for turn in record.get("turns", []) for operator in turn.get("metrics", {}).get("requested_operators", [])}),
            "turn_count": len(record.get("turns", [])),
            "coverage_status": [turn.get("metrics", {}).get("negative_claim", {}).get("coverage_report") for turn in record.get("turns", [])],
            "support_grades": [turn.get("metrics", {}).get("final_support_grade", {}).get("status") for turn in record.get("turns", [])],
        })
    report = {
        "suite_id": manifest["suite_id"],
        "schema_version": 1,
        "case_count": len(cases),
        "cases": cases,
        "fixture_sha256": manifest["fixture_sha256"],
        "raw_run_sha256": sha256_text(json.dumps(raw_records, ensure_ascii=True, sort_keys=True, separators=(",", ":"))),
    }
    _atomic_json(output_path, report)
    return report


def run_private_uat(*, fixture_path: Path, repo_root: Path, output_dir: Path | None = None, replace: bool = False, validate_only: bool = False, export_path: Path | None = None) -> dict[str, Any]:
    private_root = _private_root(repo_root)
    fixture_path = _assert_private_path(fixture_path, private_root, "fixture")
    fixture = validate_fixture(yaml.safe_load(fixture_path.read_text(encoding="utf-8")))
    fixture_sha = sha256_text(fixture_path.read_text(encoding="utf-8"))
    if validate_only:
        return {"status": "validated", "suite_id": fixture["suite_id"], "case_count": len(fixture["cases"]), "fixture_sha256": fixture_sha}
    run_dir = _assert_private_path(output_dir or (private_root / "runs" / fixture["suite_id"]), private_root, "output directory")
    run_manifest_path = run_dir / "run.json"
    if run_manifest_path.exists() and not replace:
        existing = json.loads(run_manifest_path.read_text(encoding="utf-8"))
        if existing.get("fixture_sha256") != fixture_sha:
            raise PrivateUATUnavailable("output contains an authoritative run for a different fixture; use --replace explicitly")
    if replace and run_manifest_path.exists():
        raw_dir = run_dir / "raw"
        for existing_record in raw_dir.glob("*.json"):
            existing_record.unlink()
    config = load_runtime_config(repo_root=repo_root)
    database_path = (config.data_root / config.storage_ingestion_root / config.storage_ingestion_database_filename).resolve()
    if not config.vault_root.exists():
        raise PrivateUATUnavailable(f"configured vault is unavailable: {config.vault_root}")
    if not database_path.exists():
        raise PrivateUATUnavailable(f"persisted ingestion database is unavailable: {database_path}")
    llm_backend = resolve_llm_backend(repo_root=repo_root, config=config, llm_mode="auto")
    if getattr(llm_backend, "unavailable_reason", None):
        raise PrivateUATUnavailable(str(llm_backend.unavailable_reason))
    compiler_backend = resolve_semantic_compiler_backend(config=config)
    run_dir.mkdir(parents=True, exist_ok=True)
    _atomic_json(run_manifest_path, {"schema_version": 1, "suite_id": fixture["suite_id"], "fixture_sha256": fixture_sha, "run_sha256": None})
    raw_dir = run_dir / "raw"
    completed = {record.get("case_id") for record in _load_raw_records(run_dir)}
    for case in fixture["cases"]:
        if case["id"] in completed:
            continue
        thread_id = "private-uat-" + hashlib.sha256(f"{fixture['suite_id']}:{case['id']}".encode()).hexdigest()[:24]
        turn_records = []
        for turn_index, turn in enumerate(case["turns"], start=1):
            result = run_thread_turn(repo_root=repo_root, data_root=config.data_root, user_input=turn["input"], thread_id=thread_id, config=config, llm_backend=llm_backend, semantic_compiler_backend=compiler_backend)
            turn_records.append({"turn_id": turn_index, "input": turn["input"], "metrics": _turn_metrics(result, case["expected"]), "result": {"assistant_response": result.assistant_response, "runtime_outcome": result.runtime_outcome, "blocking_reasons": ["runtime_blocked"] if result.runtime_outcome != "completed" else [], "semantic_compiler_packet": result.semantic_compiler_packet, "semantic_compiler_diagnostic": result.semantic_compiler_diagnostic, "semantic_traversal_manifest": result.semantic_traversal_manifest, "retrieval_packet": result.retrieval_packet, "coverage_report": result.coverage_report, "llm_metadata": result.llm_metadata}})
        _atomic_json(raw_dir / f"{case['id']}.json", {"case_id": case["id"], "description": case["description"], "expected": case["expected"], "turns": turn_records})
    records = _load_raw_records(run_dir)
    run_sha = sha256_text(json.dumps(records, ensure_ascii=True, sort_keys=True, separators=(",", ":")))
    _atomic_json(run_manifest_path, {"schema_version": 1, "suite_id": fixture["suite_id"], "fixture_sha256": fixture_sha, "run_sha256": run_sha, "case_count": len(records)})
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
    parser.add_argument("--validate-only", action="store_true", help="Validate a sanitized fixture without requiring a vault, inventory, or model backend.")
    args = parser.parse_args(argv)
    try:
        print(json.dumps(run_private_uat(fixture_path=args.fixture, repo_root=args.repo_root.resolve(), output_dir=args.output_dir, replace=args.replace, validate_only=args.validate_only, export_path=args.export_redacted), indent=2, sort_keys=True))
        return 0
    except (FixtureError, FileNotFoundError, OSError, PrivateUATUnavailable, ValueError) as exc:
        print(f"private UAT unavailable or invalid: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
