from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Iterable


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from semantic_traversal.config import RuntimeConfig, load_runtime_config
from semantic_traversal.hashing import sha256_json
from semantic_traversal.retrieval_plan import coerce_string_list
from semantic_traversal.retrieval_resolver import validate_plan_completeness, validate_plan_executability
from semantic_traversal.runtime import (
    _build_conversation_thread,
    _compiler_request_packet,
    _compiler_response_to_packet,
    _default_conversation_thread,
    _default_thread_state,
    _ensure_message_list,
    _recent_semantic_turns_from_state,
    _thread_state_hash,
    _update_thread_state,
)
from semantic_traversal.semantic_compiler import SemanticCompilerResponse, resolve_semantic_compiler_backend


TERMINAL_STATUSES = {
    "completed", "timed_out", "unavailable", "malformed", "blocked",
    "unsupported_baseline_capability", "harness_error",
}
SUPPORTED_SURFACES = ("exact", "lexical", "vector", "graph", "temporal")
MULTI_TURN_SCENARIOS = {
    "referential-continuation", "comparative-continuation", "ambiguous-reference",
    "topic-reset", "restart-recovery",
}


@dataclass(frozen=True)
class ScenarioTurn:
    scenario_id: str
    turn_id: str
    user_input: str
    expected_response_mode: str
    expected_subjects: tuple[str, ...]
    expected_operators: tuple[str, ...] = ()
    expected_evidence_requirements: tuple[str, ...] = ()
    thread_id: str = ""
    setup_turn: bool = False
    expected_referents: tuple[str, ...] = ()


def generic_scenario_turns() -> tuple[ScenarioTurn, ...]:
    single_turns = (
        ("direct-casual", "Hello there.", "direct", ("conversation",)),
        ("direct-explanation", "Explain gravity in plain language.", "direct", ("gravity",)),
        ("direct-rewrite", "Rewrite this sentence more clearly: The process is hard to understand.", "direct", ("rewriting",)),
        ("user-traversal", "Trace the relationship between concept alpha and concept beta.", "traverse", ("concept alpha", "concept beta"), ("graph_expand",), ("graph_relation",)),
        ("narrow-fact", "Find the narrow fact about concept alpha.", "traverse", ("concept alpha",), ("vector_search",), ("semantic_similarity",)),
        ("exact-phrase", "Find the exact phrase 'concept alpha'.", "traverse", ("concept alpha",), ("exact_chunk_search",), ("literal_exhaustive",)),
        ("exact-count", "How many times does concept alpha occur?", "traverse", ("concept alpha",), ("exact_chunk_search",), ("literal_exhaustive",)),
        ("exact-absence", "Is the exact phrase concept omega absent?", "traverse", ("concept omega",), ("exact_chunk_search",), ("literal_exhaustive",)),
        ("lexical-punctuation", "Search for I'm, author's concept, and OR (literal punctuation).", "traverse", ("I'm", "author's concept"), ("lexical_chunk_search",), ("lexical_relevance",)),
        ("broad-synthesis", "Give a broad synthesis of concept alpha.", "traverse", ("concept alpha",), ("vector_search", "lexical_chunk_search"), ("semantic_similarity", "lexical_relevance")),
        ("temporal-origin", "What is the origin of concept alpha?", "traverse", ("concept alpha",), ("temporal_retrieve",), ("chronology",)),
        ("temporal-order", "Order the development of concept alpha.", "traverse", ("concept alpha",), ("temporal_retrieve",), ("chronology",)),
        ("graph-relation", "Which notes connect concept alpha and concept beta?", "traverse", ("concept alpha", "concept beta"), ("graph_expand",), ("graph_relation",)),
        ("cross-note", "Compare concept alpha with concept beta.", "traverse", ("concept alpha", "concept beta"), ("vector_search",), ("semantic_similarity",)),
        ("unsupported-private", "What private fact is stored about an unavailable person?", "traverse", ("unavailable person",), ("exact_chunk_search",), ("literal_exhaustive",)),
        ("planner-unavailable", "Find evidence for concept alpha with an unavailable planner.", "traverse", ("concept alpha",)),
        ("planner-timeout", "Find evidence for concept alpha with a timed-out planner.", "traverse", ("concept alpha",)),
        ("planner-malformed", "Find evidence for concept alpha with malformed planner output.", "traverse", ("concept alpha",)),
        ("zero-evidence", "Find evidence for concept with zero retrieval results.", "traverse", ("zero evidence",)),
    )
    turns: list[ScenarioTurn] = []
    for entry in single_turns:
        scenario_id, text, mode, subjects, *rest = entry
        operators = tuple(rest[0]) if rest else ()
        evidence = tuple(rest[1]) if len(rest) > 1 else ()
        turns.append(ScenarioTurn(scenario_id, "1", text, mode, tuple(subjects), operators, evidence, scenario_id))

    turns.extend((
        ScenarioTurn("referential-continuation", "1", "Compare concept alpha and concept beta.", "traverse", ("concept alpha", "concept beta"), thread_id="referential-continuation", setup_turn=True),
        ScenarioTurn("referential-continuation", "2", "Which of those two is earlier?", "traverse", ("concept alpha", "concept beta"), ("temporal_retrieve",), ("chronology",), "referential-continuation", expected_referents=("concept alpha", "concept beta")),
        ScenarioTurn("comparative-continuation", "1", "Set up a comparison of concept alpha and concept beta.", "traverse", ("concept alpha", "concept beta"), thread_id="comparative-continuation", setup_turn=True),
        ScenarioTurn("comparative-continuation", "2", "Which one is broader?", "traverse", ("concept alpha", "concept beta"), ("vector_search",), ("semantic_similarity",), "comparative-continuation", expected_referents=("concept alpha", "concept beta")),
        ScenarioTurn("topic-reset", "1", "Discuss concept alpha.", "traverse", ("concept alpha",), thread_id="topic-reset", setup_turn=True),
        ScenarioTurn("topic-reset", "2", "What is the boiling point of water?", "direct", ("boiling point of water",), thread_id="topic-reset", expected_referents=()),
        ScenarioTurn("ambiguous-reference", "1", "Introduce concept alpha and concept beta.", "traverse", ("concept alpha", "concept beta"), thread_id="ambiguous-reference", setup_turn=True),
        ScenarioTurn("ambiguous-reference", "2", "Tell me more about it.", "traverse", (), thread_id="ambiguous-reference", expected_referents=()),
        ScenarioTurn("restart-recovery", "1", "Remember concept alpha for the next turn.", "traverse", ("concept alpha",), thread_id="restart-recovery", setup_turn=True),
        ScenarioTurn("restart-recovery", "2", "Continue with that concept after planner restart.", "traverse", ("concept alpha",), thread_id="restart-recovery", expected_referents=("concept alpha",)),
    ))
    return tuple(turns)


class ControlledPlannerBackend:
    """Deterministic backend doubles used only for failure-injection cases."""

    def __init__(self, status: str = "parsed") -> None:
        self.status = status

    def compile_turn(self, packet: dict[str, Any]) -> SemanticCompilerResponse:
        if self.status == "timeout":
            time.sleep(60)
        if self.status == "unavailable":
            return SemanticCompilerResponse(None, None, {"backend_mode": "controlled_unavailable"}, {}, "unavailable")
        if self.status == "malformed":
            return SemanticCompilerResponse(None, "not-json", {"backend_mode": "controlled_malformed"}, {}, "invalid_json")
        payload = {
            "raw_user_input": packet.get("raw_user_input", ""),
            "intent": "synthetic evaluation plan",
            "query": packet.get("raw_user_input", ""),
            "entities": [], "relations": [], "resolved_referents": [],
            "planner_retrieval_plan": {
                "intent_type": "semantic_traversal",
                "scope_requests": [], "concepts": [], "resolved_referents": [], "literal_terms": [],
                "evidence_requirements": [], "semantic_queries": [packet.get("raw_user_input", "")],
                "lexical_queries": [packet.get("raw_user_input", "")], "graph_seeds": [],
                "retrieval_layers": [],
            },
            "limitations": [],
        }
        return SemanticCompilerResponse(payload, json.dumps(payload), {"backend_mode": "controlled"}, {}, "parsed")


def _call_with_timeout(function: Callable[[], SemanticCompilerResponse], timeout_seconds: float) -> tuple[SemanticCompilerResponse | None, str | None, float]:
    result: list[SemanticCompilerResponse] = []
    failure: list[BaseException] = []
    started = time.perf_counter()

    def invoke() -> None:
        try:
            result.append(function())
        except BaseException as exc:  # noqa: BLE001
            failure.append(exc)

    worker = threading.Thread(target=invoke, daemon=True)
    worker.start()
    worker.join(timeout_seconds)
    latency_ms = (time.perf_counter() - started) * 1000
    if worker.is_alive():
        return None, "timed_out", latency_ms
    if failure:
        return None, f"harness_error: {type(failure[0]).__name__}: {failure[0]}", latency_ms
    return result[0], None, latency_ms


def _case_dir(output_path: Path) -> Path:
    return output_path.with_name(output_path.stem + "-cases")


def _case_path(case_dir: Path, case_id: str) -> Path:
    return case_dir / (hashlib.sha256(case_id.encode("utf-8")).hexdigest() + ".json")


def _atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}-{threading.get_ident()}")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=True, sort_keys=True, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _read_case_records(case_dir: Path) -> tuple[dict[str, dict[str, Any]], list[str]]:
    records: dict[str, dict[str, Any]] = {}
    quarantined: list[str] = []
    if not case_dir.exists():
        return records, quarantined
    for path in sorted(case_dir.glob("*.json")):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            case_id = str(record["case_id"])
            if record.get("terminal_status") not in TERMINAL_STATUSES:
                raise ValueError("case record is not terminal")
        except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
            quarantine = path.with_name(f"{path.name}.corrupt-{time.time_ns()}")
            os.replace(path, quarantine)
            quarantined.append(f"{path.name}: {type(exc).__name__}: {exc}")
            continue
        if case_id in records:
            raise RuntimeError(f"duplicate authoritative case ID in checkpoint directory: {case_id}")
        records[case_id] = record
    return records, quarantined


def _write_summary(output_path: Path, records: dict[str, dict[str, Any]]) -> None:
    temporary = output_path.with_name(f".{output_path.name}.tmp-{os.getpid()}-{threading.get_ident()}")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        for case_id in sorted(records):
            handle.write(json.dumps(records[case_id], ensure_ascii=True, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, output_path)


def _summary(output_path: Path, records: dict[str, dict[str, Any]], quarantined: list[str]) -> dict[str, Any]:
    counts = {status: 0 for status in sorted(TERMINAL_STATUSES)}
    planner_counts: dict[str, int] = {}
    for record in records.values():
        counts[str(record["terminal_status"])] = counts.get(str(record["terminal_status"]), 0) + 1
        status = str(record.get("planner_call_status") or "unknown")
        planner_counts[status] = planner_counts.get(status, 0) + 1
    return {
        "case_count": len(records),
        "authoritative_case_id_count": len(set(records)),
        "terminal_counts": counts,
        "planner_call_status_counts": planner_counts,
        "quarantined_checkpoints": quarantined,
        "records_path": str(output_path),
        "case_ids": sorted(records),
    }


def _unique(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def _observed_subjects(packet: dict[str, Any]) -> list[str]:
    plan = packet.get("planner_retrieval_plan") if isinstance(packet.get("planner_retrieval_plan"), dict) else {}
    return _unique([
        *coerce_string_list(packet.get("entities")),
        *coerce_string_list(packet.get("concepts")),
        *coerce_string_list(plan.get("concepts")),
        *coerce_string_list(plan.get("resolved_referents")),
    ])


def _comparison(expected: Iterable[str], observed: Iterable[str], *, unavailable_reason: str | None = None) -> dict[str, Any]:
    if unavailable_reason:
        return {"status": "unavailable", "reason": unavailable_reason}
    expected_values = set(expected)
    observed_values = set(observed)
    return {
        "status": "match" if expected_values == observed_values else "mismatch",
        "expected": sorted(expected_values),
        "observed": sorted(observed_values),
        "missing": sorted(expected_values - observed_values),
        "unexpected": sorted(observed_values - expected_values),
    }


def _load_scenario_state(state_dir: Path, turn: ScenarioTurn, records: dict[str, dict[str, Any]], config: RuntimeConfig) -> dict[str, Any]:
    path = state_dir / f"{turn.scenario_id}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    if turn.turn_id != "1":
        previous = records.get(f"{turn.scenario_id}:1")
        if previous and isinstance(previous.get("next_thread_state"), dict):
            return {
                "thread_state": previous["next_thread_state"],
                "conversation_thread": previous.get("next_conversation_thread") or _default_conversation_thread(turn.thread_id or turn.scenario_id, datetime.now(UTC).isoformat()),
                "turn_history": [previous.get("state_turn_record", {})],
            }
        raise RuntimeError(f"missing persisted setup state for multi-turn scenario {turn.scenario_id}")
    thread_id = turn.thread_id or turn.scenario_id
    created_at = datetime.now(UTC).isoformat()
    return {"thread_state": _default_thread_state(thread_id, created_at), "conversation_thread": _default_conversation_thread(thread_id, created_at), "turn_history": []}


def _persist_scenario_state(state_dir: Path, scenario_id: str, state: dict[str, Any]) -> None:
    _atomic_write_json(state_dir / f"{scenario_id}.json", state)


def _default_live_factory(config: RuntimeConfig, turn: ScenarioTurn) -> Any:
    controlled_status = {"planner-unavailable": "unavailable", "planner-timeout": "timeout", "planner-malformed": "malformed"}.get(turn.scenario_id)
    if controlled_status:
        return ControlledPlannerBackend(controlled_status)
    return resolve_semantic_compiler_backend(config=config, model_override="qwen3:8b")


def run_baseline(
    *,
    output_path: Path,
    config: RuntimeConfig,
    backend_factory: Callable[[ScenarioTurn], Any] | None = None,
    timeout_seconds: float = 20.0,
    turns: Iterable[ScenarioTurn] | None = None,
) -> dict[str, Any]:
    cases = tuple(turns or generic_scenario_turns())
    case_dir = _case_dir(output_path)
    state_dir = output_path.with_name(output_path.stem + "-state")
    records, quarantined = _read_case_records(case_dir)
    factory = backend_factory or (lambda turn: _default_live_factory(config, turn))
    for turn in cases:
        case_id = f"{turn.scenario_id}:{turn.turn_id}"
        if case_id in records:
            continue
        scenario_state = _load_scenario_state(state_dir, turn, records, config)
        prior_thread_state = scenario_state["thread_state"]
        prior_conversation_thread = scenario_state["conversation_thread"]
        recent_messages = _ensure_message_list(prior_thread_state.get("recent_messages"))
        recent_semantic_turns = _recent_semantic_turns_from_state(prior_thread_state.get("recent_semantic_turns"), config=config)
        active_focus = prior_thread_state.get("active_focus") if isinstance(prior_thread_state.get("active_focus"), dict) else {}
        resource_inventory_summary = {"scope_aliases": config.retrieval_scope_aliases, "inventory_diagnostics": {"status": "unavailable", "reason": "no persisted inventory available"}}
        compiler_request = _compiler_request_packet(
            raw_user_input=turn.user_input, prior_thread_state=prior_thread_state,
            recent_messages=recent_messages, recent_semantic_turns=recent_semantic_turns,
            active_focus=active_focus, resource_inventory_summary=resource_inventory_summary,
        )
        prior_values = _unique([
            *coerce_string_list(active_focus.get("resolved_referents")),
            *coerce_string_list(active_focus.get("concepts")),
            *[str(value) for item in recent_semantic_turns for value in coerce_string_list(item.get("resolved_referents"))],
        ])
        backend = factory(turn)
        backend_instance = f"{type(backend).__name__}:{id(backend)}"
        started = time.perf_counter()
        response, failure, planner_latency_ms = _call_with_timeout(lambda: backend.compile_turn(compiler_request), timeout_seconds)
        if failure == "timed_out":
            response_for_packet = SemanticCompilerResponse(None, None, {"backend_mode": getattr(backend, "mode_name", type(backend).__name__)}, {}, "timeout")
            planner_call_status = "timed_out"
        elif failure:
            response_for_packet = SemanticCompilerResponse(None, None, {"backend_mode": getattr(backend, "mode_name", type(backend).__name__), "error": failure}, {}, "harness_error")
            planner_call_status = "harness_error"
        else:
            response_for_packet = response or SemanticCompilerResponse(None, None, {}, {}, "unavailable")
            planner_call_status = str(response_for_packet.status)
        canonical_packet, compiler_status = _compiler_response_to_packet(
            raw_user_input=turn.user_input, prior_thread_state=prior_thread_state,
            active_focus=active_focus, recent_semantic_turns=recent_semantic_turns,
            config=config, response=response_for_packet,
        )
        plan = canonical_packet.get("planner_retrieval_plan") if isinstance(canonical_packet.get("planner_retrieval_plan"), dict) else {}
        completeness = validate_plan_completeness(planner_retrieval_plan=plan, config=config)
        executability = validate_plan_executability(planner_retrieval_plan=plan, config=config)
        observed_operators = _unique(str(layer.get("operator") or "") for layer in plan.get("retrieval_layers", []) if isinstance(layer, dict))
        observed_evidence = _unique(coerce_string_list(plan.get("evidence_requirements")))
        observed_referents = _unique([
            *coerce_string_list(canonical_packet.get("resolved_referents")),
            *coerce_string_list(plan.get("resolved_referents")),
        ])
        observed_subjects = _observed_subjects(canonical_packet)
        carried_values = [value for value in prior_values if value in observed_referents or value in observed_subjects]
        carry_source = None
        if turn.turn_id != "1" and carried_values:
            carry_source = {"source": "thread_state.active_focus_or_recent_semantic_turns", "values": carried_values}
        stale_subjects = sorted(set(prior_values).intersection(observed_subjects + observed_referents)) if turn.scenario_id == "topic-reset" and turn.turn_id != "1" else []
        component_scores = {
            "current_turn_target_correctness": _comparison(turn.expected_subjects, observed_subjects),
            "referent_correctness": _comparison(turn.expected_referents, observed_referents),
            "operator_correctness": _comparison(turn.expected_operators, observed_operators),
            "evidence_requirement_correctness": _comparison(turn.expected_evidence_requirements, observed_evidence),
            "plan_completeness": {"status": completeness.get("status"), "observed": completeness},
            "plan_executability": {"status": executability.get("status"), "observed": executability},
        }
        empty_retrieval = {"selected_chunks": [], "matched_chunk_count": 0, "retrieval_observation": "not_executed", "coverage": {"status": "not_evaluated"}}
        next_thread_state = _update_thread_state(
            prior_thread_state=prior_thread_state, thread_id=turn.thread_id or turn.scenario_id,
            turn_id=int(turn.turn_id), raw_user_input=turn.user_input, assistant_response=None,
            semantic_compiler_packet=canonical_packet, retrieval_packet=empty_retrieval,
            created_at=datetime.now(UTC).isoformat(), config=config,
        )
        perturbation_hash = sha256_json({"case_id": case_id, "compiler_status": planner_call_status, "packet": canonical_packet})
        next_conversation_thread = _build_conversation_thread(
            prior_thread_document=prior_conversation_thread, turn_id=int(turn.turn_id),
            raw_user_input=turn.user_input, assistant_response=None,
            thread_state_hash=next_thread_state["latest_thread_state_hash"], perturbation_hash=perturbation_hash,
            created_at=datetime.now(UTC).isoformat(),
        )
        state_turn_record = {
            "turn_id": turn.turn_id, "user_input": turn.user_input, "compiler_status": planner_call_status,
            "compiler_response_status": response_for_packet.status, "raw_model_output": response_for_packet.raw_response,
            "canonical_packet": canonical_packet, "resolved_referents": observed_referents,
            "subjects": observed_subjects, "prior_message_history": recent_messages,
        }
        record: dict[str, Any] = {
            "case_id": case_id, "scenario_id": turn.scenario_id, "turn_id": turn.turn_id,
            "thread_id": turn.thread_id or turn.scenario_id, "input": turn.user_input,
            "expected_response_mode": turn.expected_response_mode, "emitted_response_mode": None,
            "baseline_capability_gap": "production_response_mode_schema_missing",
            "current_routing_assumption": "always_traverse",
            "expected_current_turn_subjects": list(turn.expected_subjects), "expected_referents": list(turn.expected_referents),
            "expected_operators": list(turn.expected_operators), "expected_evidence_requirements": list(turn.expected_evidence_requirements),
            "emitted_current_turn_subjects": observed_subjects, "resolved_referents": observed_referents,
            "carry_source": carry_source, "requested_operators": observed_operators, "executed_operators": [],
            "evidence_requirements": observed_evidence, "requiredness": {str(layer.get("operator")): bool(layer.get("required")) for layer in plan.get("retrieval_layers", []) if isinstance(layer, dict)},
            "plan_completeness": completeness, "plan_executability": executability,
            "component_scores": component_scores, "topic_reset_stale_subjects_emitted": stale_subjects,
            "ambiguity_preserved": turn.scenario_id != "ambiguous-reference" or turn.turn_id != "2" or not observed_referents,
            "execution_outcome": None, "blocking_reason": None,
            "candidate_count_by_surface": {surface: 0 for surface in SUPPORTED_SURFACES},
            "answer_bearing_candidate_present": False, "selected_evidence_present": False,
            "irrelevant_selected_evidence_count": 0, "malformed_status": None,
            "planner_latency_ms": round(planner_latency_ms, 3), "retrieval_latency_ms": None, "synthesis_latency_ms": None,
            "available_planner_input_tokens": None, "available_planner_output_tokens": None, "available_planner_cost": None,
            "available_synthesis_tokens": None, "available_synthesis_cost": None, "final_support_grade": None,
            "metric_availability": {
                "candidate_recall": {"status": "unavailable", "reason": "retrieval not executed; persisted inventory unavailable"},
                "selection_precision": {"status": "unavailable", "reason": "retrieval and selection not executed"},
                "synthesis_support": {"status": "unavailable", "reason": "frontier synthesis not executed"},
                "final_answer_quality": {"status": "unavailable", "reason": "frontier synthesis not executed"},
            },
            "retrieval_results": [], "synthesis_called": False, "setup_turn": turn.setup_turn,
            "restart_backend_instance": turn.scenario_id == "restart-recovery" and turn.turn_id == "2",
            "backend_instance": backend_instance, "planner_call_status": planner_call_status,
            "compiler_status": compiler_status, "raw_model_output": response_for_packet.raw_response,
            "canonical_plan": plan, "canonical_packet": canonical_packet,
            "backend_metadata": response_for_packet.metadata,
            "prior_thread_state": prior_thread_state, "prior_message_history": recent_messages,
            "persisted_state_path": str(state_dir / f"{turn.scenario_id}.json"),
            "state_turn_record": state_turn_record, "next_thread_state": next_thread_state,
            "next_conversation_thread": next_conversation_thread,
        }
        if failure == "timed_out":
            record.update(terminal_status="timed_out", execution_outcome="blocked_control", blocking_reason="planner timeout")
        elif failure:
            record.update(terminal_status="harness_error", execution_outcome="blocked_control", blocking_reason=failure)
        elif response_for_packet.status == "unavailable":
            record.update(terminal_status="unavailable", execution_outcome="blocked_control", blocking_reason="planner unavailable")
        elif response_for_packet.status == "invalid_json":
            record.update(terminal_status="malformed", malformed_status="planner_malformed", execution_outcome="blocked_control", blocking_reason="planner output was not valid JSON")
        elif turn.scenario_id in {"zero-evidence", "unsupported-private"}:
            record.update(terminal_status="blocked", execution_outcome="blocked_zero_evidence", blocking_reason="retrieval was not executed; scenario requires zero-evidence or private-evidence handling")
        elif turn.expected_response_mode == "direct":
            record.update(terminal_status="unsupported_baseline_capability", execution_outcome="unsupported", blocking_reason="current production runtime has no accepted direct route")
        else:
            record.update(terminal_status="completed", execution_outcome="blocked_retrieval", blocking_reason="planner parsed; retrieval and synthesis were not executed because persisted inventory was unavailable")
        record["case_duration_ms"] = round((time.perf_counter() - started) * 1000, 3)
        _atomic_write_json(_case_path(case_dir, case_id), record)
        _persist_scenario_state(state_dir, turn.scenario_id, {"thread_state": next_thread_state, "conversation_thread": next_conversation_thread, "turn_history": [*scenario_state.get("turn_history", []), state_turn_record]})
        records[case_id] = record
        _write_summary(output_path, records)
    _write_summary(output_path, records)
    return _summary(output_path, records, quarantined)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the generic Implementation-08 Seam-0 planner baseline with atomic per-case checkpoints.")
    parser.add_argument("--output", type=Path, default=REPO_ROOT / "agent_harness/implementation-projects/active/implementation-08-baseline-records.jsonl")
    parser.add_argument("--timeout-seconds", type=float, default=20.0)
    args = parser.parse_args(argv)
    config = load_runtime_config(repo_root=REPO_ROOT)
    print(json.dumps(run_baseline(output_path=args.output, config=config, timeout_seconds=args.timeout_seconds), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
