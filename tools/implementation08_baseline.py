from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from semantic_traversal.config import RuntimeConfig, load_runtime_config
from semantic_traversal.runtime import _compiler_request_packet
from semantic_traversal.semantic_compiler import SemanticCompilerResponse, resolve_semantic_compiler_backend


TERMINAL_STATUSES = {
    "completed", "timed_out", "unavailable", "malformed", "blocked",
    "unsupported_baseline_capability", "harness_error",
}


@dataclass(frozen=True)
class ScenarioTurn:
    scenario_id: str
    turn_id: str
    user_input: str
    expected_response_mode: str
    expected_subjects: tuple[str, ...]
    requested_operators: tuple[str, ...] = ()
    evidence_requirements: tuple[str, ...] = ()
    thread_id: str = ""
    setup_turn: bool = False


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
        requirements = tuple(rest[1]) if len(rest) > 1 else ()
        turns.append(ScenarioTurn(scenario_id, "1", text, mode, tuple(subjects), operators, requirements, scenario_id))

    turns.extend((
        ScenarioTurn("referential-continuation", "1", "Compare concept alpha and concept beta.", "traverse", ("concept alpha", "concept beta"), thread_id="referential-continuation", setup_turn=True),
        ScenarioTurn("referential-continuation", "2", "Which of those two is earlier?", "traverse", ("concept alpha", "concept beta"), ("temporal_retrieve",), ("chronology",), "referential-continuation"),
        ScenarioTurn("comparative-continuation", "1", "Set up a comparison of concept alpha and concept beta.", "traverse", ("concept alpha", "concept beta"), thread_id="comparative-continuation", setup_turn=True),
        ScenarioTurn("comparative-continuation", "2", "Which one is broader?", "traverse", ("concept alpha", "concept beta"), ("vector_search",), ("semantic_similarity",), "comparative-continuation"),
        ScenarioTurn("topic-reset", "1", "Discuss concept alpha.", "traverse", ("concept alpha",), thread_id="topic-reset", setup_turn=True),
        ScenarioTurn("topic-reset", "2", "What is the boiling point of water?", "direct", ("boiling point of water",), thread_id="topic-reset"),
        ScenarioTurn("ambiguous-reference", "1", "Introduce concept alpha and concept beta.", "traverse", ("concept alpha", "concept beta"), thread_id="ambiguous-reference", setup_turn=True),
        ScenarioTurn("ambiguous-reference", "2", "Tell me more about it.", "traverse", (), thread_id="ambiguous-reference"),
        ScenarioTurn("restart-recovery", "1", "Remember concept alpha for the next turn.", "traverse", ("concept alpha",), thread_id="restart-recovery", setup_turn=True),
        ScenarioTurn("restart-recovery", "2", "Continue with that concept after planner restart.", "traverse", ("concept alpha",), thread_id="restart-recovery"),
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


def _load_completed(path: Path) -> set[str]:
    if not path.exists():
        return set()
    completed: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if record.get("terminal_status") in TERMINAL_STATUSES:
            completed.add(str(record.get("case_id")))
    return completed


def _append_checkpoint(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(record, ensure_ascii=True, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _summary(path: Path) -> dict[str, Any]:
    counts = {status: 0 for status in sorted(TERMINAL_STATUSES)}
    records: list[dict[str, Any]] = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                record = json.loads(line)
                records.append(record)
                counts[str(record.get("terminal_status"))] = counts.get(str(record.get("terminal_status")), 0) + 1
    return {"case_count": len(records), "terminal_counts": counts, "records_path": str(path), "case_ids": sorted(str(record.get("case_id")) for record in records)}


def run_baseline(
    *,
    output_path: Path,
    config: RuntimeConfig,
    backend_factory: Callable[[ScenarioTurn], Any] | None = None,
    timeout_seconds: float = 20.0,
    turns: Iterable[ScenarioTurn] | None = None,
) -> dict[str, Any]:
    cases = tuple(turns or generic_scenario_turns())
    completed = _load_completed(output_path)
    if backend_factory is None:
        def live_factory(turn: ScenarioTurn) -> Any:
            controlled_status = {
                "planner-unavailable": "unavailable",
                "planner-timeout": "timeout",
                "planner-malformed": "malformed",
            }.get(turn.scenario_id)
            if controlled_status:
                return ControlledPlannerBackend(controlled_status)
            return resolve_semantic_compiler_backend(config=config, model_override="qwen3:8b")
    else:
        live_factory = backend_factory
    for turn in cases:
        case_id = f"{turn.scenario_id}:{turn.turn_id}"
        if case_id in completed:
            continue
        prior_messages = [] if turn.turn_id == "1" else [{"role": "user", "content": "Synthetic setup turn persisted for this scenario."}]
        prior_state = {"thread_id": turn.thread_id or turn.scenario_id, "recent_messages": prior_messages, "recent_semantic_turns": [], "active_focus": {}}
        packet = _compiler_request_packet(
            raw_user_input=turn.user_input, prior_thread_state=prior_state,
            recent_messages=prior_messages, recent_semantic_turns=[], active_focus={},
            resource_inventory_summary={"inventory_diagnostics": {"status": "unavailable"}},
        )
        started = time.perf_counter()
        backend = live_factory(turn)
        response, failure, planner_latency_ms = _call_with_timeout(lambda: backend.compile_turn(packet), timeout_seconds)
        record: dict[str, Any] = {
            "case_id": case_id, "scenario_id": turn.scenario_id, "turn_id": turn.turn_id,
            "thread_id": turn.thread_id or turn.scenario_id, "input": turn.user_input,
            "expected_response_mode": turn.expected_response_mode, "emitted_response_mode": None,
            "baseline_capability_gap": "production_response_mode_schema_missing",
            "current_routing_assumption": "always_traverse",
            "expected_current_turn_subjects": list(turn.expected_subjects), "emitted_current_turn_subjects": [],
            "resolved_referents": [], "carry_source": "persisted_thread_state" if turn.turn_id != "1" else None,
            "requested_operators": list(turn.requested_operators), "executed_operators": [],
            "evidence_requirements": list(turn.evidence_requirements), "requiredness": {},
            "plan_completeness": None, "plan_executability": None, "execution_outcome": None,
            "blocking_reason": None, "candidate_count_by_surface": {surface: 0 for surface in ("exact", "lexical", "vector", "graph", "temporal")},
            "answer_bearing_candidate_present": False, "selected_evidence_present": False,
            "irrelevant_selected_evidence_count": 0, "malformed_status": None,
            "planner_latency_ms": round(planner_latency_ms, 3), "retrieval_latency_ms": None, "synthesis_latency_ms": None,
            "available_planner_input_tokens": None, "available_planner_output_tokens": None, "available_planner_cost": None,
            "available_synthesis_tokens": None, "available_synthesis_cost": None, "final_support_grade": None,
            "retrieval_results": [], "synthesis_called": False, "setup_turn": turn.setup_turn,
            "raw_model_output": response.raw_response if response else None,
            "canonical_plan": response.parsed_payload.get("planner_retrieval_plan") if response and response.parsed_payload else None,
            "backend_metadata": response.metadata if response else {},
        }
        if failure == "timed_out":
            record.update(terminal_status="timed_out", execution_outcome="blocked_control", blocking_reason="planner timeout")
        elif failure:
            record.update(terminal_status="harness_error", execution_outcome="blocked_control", blocking_reason=failure)
        elif response is None or response.status == "unavailable":
            record.update(terminal_status="unavailable", execution_outcome="blocked_control", blocking_reason="planner unavailable")
        elif response.status == "invalid_json":
            record.update(terminal_status="malformed", malformed_status="planner_malformed", execution_outcome="blocked_control", blocking_reason="planner output was not valid JSON")
        elif turn.scenario_id == "planner-malformed":
            record.update(terminal_status="malformed", malformed_status="planner_malformed", execution_outcome="blocked_control", blocking_reason="controlled malformed planner output")
        elif turn.scenario_id in {"zero-evidence", "unsupported-private"}:
            record.update(terminal_status="blocked", execution_outcome="blocked_zero_evidence", blocking_reason="retrieval returned zero evidence")
        elif turn.expected_response_mode == "direct":
            record.update(terminal_status="unsupported_baseline_capability", execution_outcome="unsupported", blocking_reason="current production runtime has no accepted direct route")
        else:
            record.update(terminal_status="completed", execution_outcome="blocked_retrieval", blocking_reason="planner baseline did not execute retrieval; persisted inventory unavailable")
        record["case_duration_ms"] = round((time.perf_counter() - started) * 1000, 3)
        _append_checkpoint(output_path, record)
        completed.add(case_id)
    return _summary(output_path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the generic Implementation-08 Seam-0 planner baseline with crash-safe checkpoints.")
    parser.add_argument("--output", type=Path, default=REPO_ROOT / "agent_harness/implementation-projects/active/implementation-08-baseline-records.jsonl")
    parser.add_argument("--timeout-seconds", type=float, default=20.0)
    args = parser.parse_args(argv)
    config = load_runtime_config(repo_root=REPO_ROOT)
    print(json.dumps(run_baseline(output_path=args.output, config=config, timeout_seconds=args.timeout_seconds), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
