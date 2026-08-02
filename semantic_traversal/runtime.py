from __future__ import annotations

import json
import math
import re
import sqlite3
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config import RuntimeConfig, load_runtime_config
from .embeddings import (
    EmbeddingBackend,
    embedding_identity_from_response,
    embedding_identity_hash,
    resolve_embedding_backend,
)
from .hashing import sha256_json, sha256_text
from .llm import LLMBackend
from .resource_inventory import build_compiler_inventory_projection, build_resource_inventory, load_persisted_inventory
from .retrieval_plan import build_default_retrieval_plan, canonicalize_retrieval_plan, coerce_string_list, is_comparison_intent, retrieval_plan_layer, _focus_carry_terms
from .retrieval_resolver import bind_retrieval_plan, validate_plan_completeness, validate_plan_executability
from .semantic_grounding import classify_candidate, merge_grounding_assessments, build_grounding_spec
from .text_filters import is_low_signal_apparatus_text
from .temporal import parse_temporal_value, relation_for_anchor
from .semantic_compiler import (
    INTERNAL_COMPILER_ECHO_FIELDS,
    SemanticCompilerBackend,
    SemanticCompilerResponse,
    resolve_semantic_compiler_backend,
)
from .storage import append_ledger_record, create_thread_paths, load_json, write_json


# FTS5 grammar is separate from SQL grammar.  These tokens are only used to
# identify literal search atoms; `_serialize_fts5_atom` performs the grammar
# quoting before the complete expression is passed as a bound MATCH value.
QUERY_TOKEN_RE = re.compile(r"[^\W_]+(?:['’][^\W_]+)*", re.UNICODE)
LAYER_TO_SOURCE = {"exact_chunk_search": "exact", "lexical_chunk_search": "lexical", "vector_search": "vector", "graph_expand": "graph", "graph_lookup": "graph", "graph_paths": "graph", "graph_neighbors": "graph", "graph_from_results": "graph", "temporal_retrieve": "temporal"}
EXACT_SEARCH_STATUSES = {
    "not_requested",
    "skipped_no_terms",
    "unavailable",
    "failed",
    "completed_no_matches",
    "completed_with_matches",
}


def _default_active_focus() -> dict[str, Any]:
    return {
        "query": None,
        "entities": [],
        "relations": [],
        "concepts": [],
        "resolved_referents": [],
        "literal_terms": [],
        "semantic_queries": [],
        "lexical_queries": [],
        "graph_seeds": [],
        "retrieval_layers": [],
        "selection_policy": {},
        "claim_policy": {},
        "selected_chunk_ids": [],
        "selected_note_titles": [],
        "selected_section_labels": [],
    }


@dataclass(frozen=True)
class TurnExecutionResult:
    thread_id: str
    turn_id: int
    thread_root: Path
    turn_root: Path
    conversation_thread_path: Path
    thread_state_path: Path
    thread_ledger_path: Path
    semantic_compiler_packet_path: Path
    semantic_compiler_diagnostic_path: Path
    semantic_traversal_manifest_path: Path
    retrieval_packet_path: Path
    coverage_report_path: Path
    synthesis_context_packet_path: Path
    state_delta_path: Path
    assistant_response: str | None
    llm_metadata: dict[str, Any]
    runtime_outcome: str
    blocking_reasons: list[str]
    prior_thread_state: dict[str, Any]
    next_thread_state: dict[str, Any]
    ledger_record: dict[str, Any]
    semantic_compiler_status: str
    semantic_compiler_packet: dict[str, Any]
    semantic_compiler_diagnostic: dict[str, Any]
    semantic_traversal_manifest: dict[str, Any]
    retrieval_packet: dict[str, Any]
    coverage_report: dict[str, Any]
    synthesis_context_packet: dict[str, Any]
    state_delta: dict[str, Any]


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _resolve_data_path(data_root: Path, raw_path: Path) -> Path:
    if raw_path.is_absolute():
        return raw_path.resolve()
    return (data_root / raw_path).resolve()


def _normalize_text(value: Any) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", str(value).lower())).strip()


def _extract_terms(text: str, *, config: RuntimeConfig) -> list[str]:
    terms: list[str] = []
    seen: set[str] = set()
    for token in QUERY_TOKEN_RE.findall(text.lower()):
        if len(token) < 3 or token in set(config.runtime_conversation["stop_words"]) or token.isdigit():
            continue
        if token not in seen:
            seen.add(token)
            terms.append(token)
    return terms


def _coerce_string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        cleaned = value.strip()
        return [cleaned] if cleaned else []
    if not isinstance(value, list):
        return [str(value).strip()] if str(value).strip() else []
    items: list[str] = []
    for entry in value:
        candidate: Any = entry
        if isinstance(entry, dict):
            candidate = entry.get("term") or entry.get("query") or entry.get("label") or entry.get("resolved_to") or entry.get("surface_form") or entry.get("value")
        if candidate is None:
            continue
        cleaned = str(candidate).strip()
        if cleaned and cleaned not in items:
            items.append(cleaned)
    return items


def _ensure_message_list(messages: Any) -> list[dict[str, Any]]:
    if not isinstance(messages, list):
        return []
    normalized: list[dict[str, Any]] = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "").strip().lower()
        content = str(message.get("content") or "").strip()
        if role and content:
            normalized.append(
                {
                    "role": role,
                    "content": content,
                    "turn_id": message.get("turn_id"),
                    "created_at": message.get("created_at"),
                }
            )
    return normalized


def _ensure_recent_semantic_turns(value: Any, *, config: RuntimeConfig) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    turns: list[dict[str, Any]] = []
    for entry in value:
        if not isinstance(entry, dict):
            continue
        turn_id = entry.get("turn_id")
        raw_user_input = str(entry.get("raw_user_input") or "").strip()
        query = str(entry.get("query") or "").strip()
        if turn_id is None or not raw_user_input:
            continue
        turns.append(
            {
                "turn_id": turn_id,
                "raw_user_input": raw_user_input,
                "assistant_response_snippet": str(entry.get("assistant_response_snippet") or "").strip(),
                "query": query,
                "entities": _coerce_string_list(entry.get("entities")),
                "relations": _coerce_string_list(entry.get("relations")),
                "concepts": _coerce_string_list(entry.get("concepts")),
                "resolved_referents": _coerce_string_list(entry.get("resolved_referents")),
                "literal_terms": _coerce_string_list(entry.get("literal_terms")),
                "semantic_queries": _coerce_string_list(entry.get("semantic_queries")),
                "lexical_queries": _coerce_string_list(entry.get("lexical_queries")),
                "graph_seeds": _coerce_string_list(entry.get("graph_seeds")),
                "retrieval_layers": entry.get("retrieval_layers") if isinstance(entry.get("retrieval_layers"), list) else [],
                "selection_policy": entry.get("selection_policy") if isinstance(entry.get("selection_policy"), dict) else {},
                "claim_policy": entry.get("claim_policy") if isinstance(entry.get("claim_policy"), dict) else {},
                "selected_chunk_ids": _coerce_string_list(entry.get("selected_chunk_ids")),
                "selected_note_titles": _coerce_string_list(entry.get("selected_note_titles")),
                "selected_section_labels": _coerce_string_list(entry.get("selected_section_labels")),
            }
        )
    return turns[-int(config.runtime_conversation["recent_semantic_turn_limit"]):]


def _normalize_active_focus(value: Any) -> dict[str, Any]:
    focus = _default_active_focus()
    if not isinstance(value, dict):
        return focus
    focus["query"] = str(value.get("query") or "").strip() or None
    focus["entities"] = _coerce_string_list(value.get("entities"))
    focus["relations"] = _coerce_string_list(value.get("relations"))
    focus["concepts"] = _coerce_string_list(value.get("concepts"))
    focus["resolved_referents"] = _coerce_string_list(value.get("resolved_referents"))
    focus["graph_seeds"] = _coerce_string_list(value.get("graph_seeds"))
    focus["literal_terms"] = _coerce_string_list(value.get("literal_terms"))
    focus["semantic_queries"] = _coerce_string_list(value.get("semantic_queries"))
    focus["lexical_queries"] = _coerce_string_list(value.get("lexical_queries"))
    focus["retrieval_layers"] = value.get("retrieval_layers") if isinstance(value.get("retrieval_layers"), list) else []
    focus["selection_policy"] = value.get("selection_policy") if isinstance(value.get("selection_policy"), dict) else {}
    focus["claim_policy"] = value.get("claim_policy") if isinstance(value.get("claim_policy"), dict) else {}
    focus["selected_chunk_ids"] = _coerce_string_list(value.get("selected_chunk_ids"))
    focus["selected_note_titles"] = _coerce_string_list(value.get("selected_note_titles"))
    focus["selected_section_labels"] = _coerce_string_list(value.get("selected_section_labels"))
    return focus


def _is_referential_user_input(text: str, *, config: RuntimeConfig) -> bool:
    lowered = f" {text.lower()} "
    return any(f" {surface} " in lowered for surface in config.runtime_conversation["referential_surface_words"])


def _snippet(text: str, *, limit: int) -> str:
    cleaned = re.sub(r"\s+", " ", text).strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1].rstrip() + "…"


def _compact_active_focus(
    *,
    planner_retrieval_plan: dict[str, Any],
    retrieval_packet: dict[str, Any],
) -> dict[str, Any]:
    selected_chunks = retrieval_packet.get("selected_chunks")
    selected_chunk_ids: list[str] = []
    selected_note_titles: list[str] = []
    selected_section_labels: list[str] = []
    if isinstance(selected_chunks, list):
        for chunk in selected_chunks:
            if not isinstance(chunk, dict):
                continue
            chunk_id = str(chunk.get("chunk_id") or "").strip()
            note_title = str(chunk.get("note_title") or "").strip()
            section_label = str(chunk.get("section_label") or "").strip()
            if chunk_id and chunk_id not in selected_chunk_ids:
                selected_chunk_ids.append(chunk_id)
            if note_title and note_title not in selected_note_titles:
                selected_note_titles.append(note_title)
            if section_label and section_label not in selected_section_labels:
                selected_section_labels.append(section_label)
    semantic_queries = _coerce_string_list(planner_retrieval_plan.get("semantic_queries"))
    return {
        "query": semantic_queries[0] if semantic_queries else None,
        "entities": [],
        "relations": [],
        "concepts": _coerce_string_list(planner_retrieval_plan.get("concepts")),
        "resolved_referents": _coerce_string_list(planner_retrieval_plan.get("resolved_referents")),
        "literal_terms": _coerce_string_list(planner_retrieval_plan.get("literal_terms")),
        "semantic_queries": semantic_queries,
        "lexical_queries": _coerce_string_list(planner_retrieval_plan.get("lexical_queries")),
        "graph_seeds": _coerce_string_list(planner_retrieval_plan.get("graph_seeds")),
        "retrieval_layers": planner_retrieval_plan.get("retrieval_layers") if isinstance(planner_retrieval_plan.get("retrieval_layers"), list) else [],
        "selection_policy": planner_retrieval_plan.get("selection_policy") if isinstance(planner_retrieval_plan.get("selection_policy"), dict) else {},
        "claim_policy": planner_retrieval_plan.get("claim_policy") if isinstance(planner_retrieval_plan.get("claim_policy"), dict) else {},
        "selected_chunk_ids": selected_chunk_ids,
        "selected_note_titles": selected_note_titles,
        "selected_section_labels": selected_section_labels,
    }


def _recent_semantic_turns_from_state(value: Any, *, config: RuntimeConfig) -> list[dict[str, Any]]:
    return _ensure_recent_semantic_turns(value, config=config)


def _build_recent_semantic_turn(
    *,
    turn_id: int,
    raw_user_input: str,
    assistant_response: str | None,
    planner_retrieval_plan: dict[str, Any],
    retrieval_packet: dict[str, Any],
    config: RuntimeConfig,
) -> dict[str, Any]:
    selected_chunks = retrieval_packet.get("selected_chunks")
    selected_chunk_ids: list[str] = []
    selected_note_titles: list[str] = []
    selected_section_labels: list[str] = []
    if isinstance(selected_chunks, list):
        for chunk in selected_chunks:
            if not isinstance(chunk, dict):
                continue
            chunk_id = str(chunk.get("chunk_id") or "").strip()
            note_title = str(chunk.get("note_title") or "").strip()
            section_label = str(chunk.get("section_label") or "").strip()
            if chunk_id and chunk_id not in selected_chunk_ids:
                selected_chunk_ids.append(chunk_id)
            if note_title and note_title not in selected_note_titles:
                selected_note_titles.append(note_title)
            if section_label and section_label not in selected_section_labels:
                selected_section_labels.append(section_label)
    semantic_queries = _coerce_string_list(planner_retrieval_plan.get("semantic_queries"))
    return {
        "turn_id": turn_id,
        "raw_user_input": raw_user_input,
        "assistant_response_snippet": _snippet(assistant_response or "", limit=int(config.runtime_conversation["assistant_snippet_limit"])),
        "query": semantic_queries[0] if semantic_queries else "",
        "entities": [],
        "relations": [],
        "concepts": _coerce_string_list(planner_retrieval_plan.get("concepts")),
        "resolved_referents": _coerce_string_list(planner_retrieval_plan.get("resolved_referents")),
        "literal_terms": _coerce_string_list(planner_retrieval_plan.get("literal_terms")),
        "semantic_queries": semantic_queries,
        "lexical_queries": _coerce_string_list(planner_retrieval_plan.get("lexical_queries")),
        "graph_seeds": _coerce_string_list(planner_retrieval_plan.get("graph_seeds")),
        "retrieval_layers": planner_retrieval_plan.get("retrieval_layers") if isinstance(planner_retrieval_plan.get("retrieval_layers"), list) else [],
        "selection_policy": planner_retrieval_plan.get("selection_policy") if isinstance(planner_retrieval_plan.get("selection_policy"), dict) else {},
        "claim_policy": planner_retrieval_plan.get("claim_policy") if isinstance(planner_retrieval_plan.get("claim_policy"), dict) else {},
        "selected_chunk_ids": selected_chunk_ids,
        "selected_note_titles": selected_note_titles,
        "selected_section_labels": selected_section_labels,
    }


def _default_thread_state(thread_id: str, created_at: str) -> dict[str, Any]:
    return {
        "thread_id": thread_id,
        "latest_turn_id": 0,
        "conversation_summary": "",
        "recent_messages": [],
        "recent_semantic_turns": [],
        "active_focus": _default_active_focus(),
        "latest_user_input": None,
        "latest_assistant_response": None,
        "updated_at": created_at,
        "latest_thread_state_hash": None,
    }


def _default_conversation_thread(thread_id: str, created_at: str) -> dict[str, Any]:
    return {
        "thread_id": thread_id,
        "created_at": created_at,
        "updated_at": created_at,
        "turn_count": 0,
        "latest_turn_id": 0,
        "latest_thread_state_hash": None,
        "latest_perturbation_hash": None,
        "messages": [],
    }


def _thread_state_hash(thread_state: dict[str, Any]) -> str:
    payload = dict(thread_state)
    payload["latest_thread_state_hash"] = None
    return sha256_json(payload)


def _compiler_request_packet(
    *,
    raw_user_input: str,
    prior_thread_state: dict[str, Any],
    recent_messages: list[dict[str, Any]],
    recent_semantic_turns: list[dict[str, Any]],
    active_focus: dict[str, Any],
    resource_inventory_summary: dict[str, Any],
) -> dict[str, Any]:
    return {
        "raw_user_input": raw_user_input,
        "prior_thread_state": prior_thread_state,
        "recent_messages": recent_messages,
        "recent_semantic_turns": recent_semantic_turns,
        "active_focus": active_focus,
        "resource_inventory_summary": resource_inventory_summary,
        "instruction": "Compile a retrieval request, not an answer. Emit supported planner intent and retrieval-layer fields only. Do not emit database or metadata filters, planner_diagnostics, or runtime selection/claim policy.",
    }


def _deterministic_semantic_packet(
    *,
    raw_user_input: str,
    prior_thread_state: dict[str, Any],
    active_focus: dict[str, Any],
    recent_semantic_turns: list[dict[str, Any]],
    config: RuntimeConfig,
    limitations: list[str] | None = None,
) -> dict[str, Any]:
    concepts = _extract_terms(raw_user_input, config=config)
    query = " ".join(concepts[:8]).strip() or raw_user_input.strip()
    resolved_referents: list[str] = []
    carry_focus_terms = _is_referential_user_input(raw_user_input, config=config) or is_comparison_intent(raw_user_input)
    if carry_focus_terms:
        focus_terms = _focus_carry_terms(active_focus=active_focus, recent_semantic_turns=recent_semantic_turns)
        resolved_referents = list(dict.fromkeys(term for term in focus_terms if term))
        if resolved_referents:
            concepts = list(dict.fromkeys([*concepts, *resolved_referents]))
            query = " ".join([query, *resolved_referents[:8]]).strip() or query
    graph_seeds: list[str] = [query] if query else []
    graph_seeds = list(dict.fromkeys(seed for seed in graph_seeds if seed))
    packet = {
        "raw_user_input": raw_user_input,
        "intent": "deterministic retrieval planning fallback",
        "query": query,
        "entities": [],
        "relations": [],
        "resolved_referents": resolved_referents,
        "limitations": list(limitations or ["semantic compiler backend unavailable; deterministic retrieval-planning fallback used"]),
    }
    packet["planner_retrieval_plan"] = build_default_retrieval_plan(
        raw_user_input=raw_user_input,
        query=query,
        concepts=concepts,
        graph_seeds=graph_seeds,
        resolved_referents=resolved_referents,
        planner_defaults=config.retrieval_planner_defaults,
    )
    packet["planner_diagnostics"] = {"ignored_planner_fields": []}
    return packet


def _canonicalize_compiler_packet(
    *,
    raw_user_input: str,
    prior_thread_state: dict[str, Any],
    active_focus: dict[str, Any],
    recent_semantic_turns: list[dict[str, Any]],
    payload: dict[str, Any] | None,
    config: RuntimeConfig,
    fallback_limitations: list[str] | None = None,
) -> dict[str, Any]:
    fallback_packet = _deterministic_semantic_packet(
        raw_user_input=raw_user_input,
        prior_thread_state=prior_thread_state,
        active_focus=active_focus,
        recent_semantic_turns=recent_semantic_turns,
        config=config,
        limitations=fallback_limitations,
    )
    if not isinstance(payload, dict):
        return fallback_packet

    packet = dict(fallback_packet)
    packet["intent"] = str(payload.get("intent") or packet["intent"]).strip() or packet["intent"]
    packet["query"] = str(payload.get("query") or packet["query"]).strip() or packet["query"]
    packet["entities"] = _coerce_string_list(payload.get("entities")) or packet["entities"]
    packet["relations"] = _coerce_string_list(payload.get("relations")) or packet["relations"]
    packet["resolved_referents"] = _coerce_string_list(payload.get("resolved_referents")) or packet["resolved_referents"]
    packet["limitations"] = _coerce_string_list(payload.get("limitations")) if isinstance(payload.get("limitations"), list) else []
    fallback_plan = build_default_retrieval_plan(
        raw_user_input=raw_user_input,
        query=packet["query"],
        concepts=_extract_terms(packet["query"], config=config),
        graph_seeds=[packet["query"]] if packet["query"].strip() else [],
        resolved_referents=list(packet["resolved_referents"]),
        planner_defaults=config.retrieval_planner_defaults,
    )
    canonical_plan, planner_diagnostics = canonicalize_retrieval_plan(payload.get("planner_retrieval_plan"), fallback=fallback_plan, planner_defaults=config.retrieval_planner_defaults, raw_user_input=raw_user_input)
    packet["planner_retrieval_plan"] = canonical_plan
    packet["planner_diagnostics"] = {
        "ignored_planner_fields": sorted(
            set(planner_diagnostics.get("ignored_planner_fields") or [])
            | {
                key
                for key in payload
                if key
                not in {
                    "raw_user_input",
                    "intent",
                    "query",
                    "entities",
                    "relations",
                    "resolved_referents",
                    "planner_retrieval_plan",
                    "limitations",
                }
                and key not in INTERNAL_COMPILER_ECHO_FIELDS
            }
        )
    }
    if planner_diagnostics.get("literal_contract") is not None:
        packet["planner_diagnostics"]["literal_contract"] = dict(planner_diagnostics["literal_contract"])
    return packet


def _compiler_response_to_packet(
    *,
    raw_user_input: str,
    prior_thread_state: dict[str, Any],
    active_focus: dict[str, Any],
    recent_semantic_turns: list[dict[str, Any]],
    config: RuntimeConfig,
    response: SemanticCompilerResponse,
) -> tuple[dict[str, Any], str]:
    payload = response.parsed_payload if isinstance(response.parsed_payload, dict) else None
    if response.status == "parsed" and payload is not None:
        return (
            _canonicalize_compiler_packet(
                raw_user_input=raw_user_input,
                prior_thread_state=prior_thread_state,
                active_focus=active_focus,
                recent_semantic_turns=recent_semantic_turns,
                config=config,
                payload=payload,
            ),
            response.status,
        )
    return (
        _deterministic_semantic_packet(
            raw_user_input=raw_user_input,
            prior_thread_state=prior_thread_state,
            active_focus=active_focus,
            recent_semantic_turns=recent_semantic_turns,
            config=config,
            limitations=["semantic compiler backend unavailable; deterministic lexical fallback used"],
        ),
        "fallback",
    )


def _repair_incomplete_compiler_plan(
    *,
    raw_user_input: str,
    compiler_request: dict[str, Any],
    compiler_backend: SemanticCompilerBackend,
    compiler_response: SemanticCompilerResponse,
    semantic_compiler_packet: dict[str, Any],
    prior_thread_state: dict[str, Any],
    active_focus: dict[str, Any],
    recent_semantic_turns: list[dict[str, Any]],
    config: RuntimeConfig,
    resource_inventory_summary: dict[str, Any],
) -> tuple[dict[str, Any], str, SemanticCompilerResponse, dict[str, Any]]:
    planner = semantic_compiler_packet.get("planner_retrieval_plan") if isinstance(semantic_compiler_packet.get("planner_retrieval_plan"), dict) else {}
    initial_completeness = validate_plan_completeness(planner_retrieval_plan=planner, config=config)
    if initial_completeness.get("status") == "complete":
        semantic_compiler_packet.setdefault("planner_diagnostics", {})["plan_completeness"] = initial_completeness
        return semantic_compiler_packet, "parsed", compiler_response, {"attempted": False, "outcome": "not_needed", "latency_ms": 0}

    repair_request = dict(compiler_request)
    repair_request["instruction"] = (
        "Repair the compiler retrieval plan exactly once. Preserve raw_user_input, valid semantic fields, and valid layers. "
        "For each declared evidence requirement, add its mapped supported operator or explicitly remove/revise the requirement "
        "if the interpretation was wrong. Return JSON only; "
        "do not answer the user, emit runtime policy, or make topic-specific inferences."
    )
    repair_request["repair_context"] = {
        "original_compiler_payload": compiler_response.parsed_payload,
        "canonical_plan": planner,
        "completeness_diagnostics": initial_completeness,
        "supported_evidence_requirements": list(config.retrieval_evidence_requirement_operators),
        "requirement_operator_mapping": config.retrieval_evidence_requirement_operators,
    }
    started = time.perf_counter()
    try:
        repair_response = compiler_backend.compile_turn(repair_request)
    except Exception as exc:  # noqa: BLE001
        repair_response = SemanticCompilerResponse(
            parsed_payload=None,
            raw_response=None,
            metadata={"backend_mode": getattr(compiler_backend, "mode_name", "unknown"), "error": str(exc)},
            diagnostics={},
            status="unavailable",
        )
    latency_ms = round((time.perf_counter() - started) * 1000, 3)
    if repair_response.status == "parsed" and isinstance(repair_response.parsed_payload, dict):
        repaired_packet, repaired_status = _compiler_response_to_packet(
            raw_user_input=raw_user_input,
            prior_thread_state=prior_thread_state,
            active_focus=active_focus,
            recent_semantic_turns=recent_semantic_turns,
            config=config,
            response=repair_response,
        )
    else:
        repaired_packet, repaired_status = semantic_compiler_packet, "repair_failed"
    repaired_plan = repaired_packet.get("planner_retrieval_plan") if isinstance(repaired_packet.get("planner_retrieval_plan"), dict) else {}
    repaired_completeness = validate_plan_completeness(planner_retrieval_plan=repaired_plan, config=config)
    repair_record = {
        "attempted": True,
        "latency_ms": latency_ms,
        "outcome": "complete" if repaired_completeness.get("status") == "complete" else "incomplete",
        "response_status": repair_response.status,
    }
    repaired_completeness["repair_attempted"] = True
    repaired_completeness["repair_result"] = repair_record["outcome"]
    if isinstance(repaired_completeness.get("literal_contract"), dict):
        repaired_completeness["literal_contract"]["repair_triggered"] = True
    repaired_packet.setdefault("planner_diagnostics", {})["plan_completeness"] = repaired_completeness
    repaired_packet["planner_diagnostics"]["plan_repair"] = repair_record
    return repaired_packet, repaired_status, repair_response, repair_record


def _incomplete_plan_artifacts(
    *,
    semantic_compiler_packet: dict[str, Any],
    config: RuntimeConfig,
    resource_inventory_summary: dict[str, Any],
    prior_thread_state: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    planner = semantic_compiler_packet.get("planner_retrieval_plan") if isinstance(semantic_compiler_packet.get("planner_retrieval_plan"), dict) else {}
    bound, adjustments, inventory = bind_retrieval_plan(
        planner_retrieval_plan=planner,
        inventory_summary=resource_inventory_summary,
        config=config,
        raw_user_input=str(semantic_compiler_packet.get("raw_user_input") or ""),
    )
    completeness = validate_plan_completeness(planner_retrieval_plan=planner, config=config)
    plan_executability = semantic_compiler_packet.get("planner_diagnostics", {}).get("plan_executability", {})
    note = "retrieval blocked: compiler-declared evidence requirements are incomplete"
    manifest = {
        "planner_retrieval_plan": planner,
        "bound_retrieval_plan": bound,
        "resolver_adjustments": adjustments,
        "resource_inventory_summary": inventory,
        "inventory_diagnostics": inventory.get("inventory_diagnostics", {}),
        "execution": {"layers_executed": [], "layers_skipped": [{"reason": "incomplete plan"}]},
        "candidate_counts": {"exact": 0, "lexical": 0, "vector": 0, "graph": 0, "temporal": 0},
        "selected_counts": {"exact": 0, "lexical": 0, "vector": 0, "graph": 0, "temporal": 0},
        "selected_chunk_ids": [],
        "layer_manifests": {},
        "unsupported_layer_requests": [
            {
                "operator": str(layer.get("operator") or ""),
                "required": bool(layer.get("required")),
                "supplied_fields": dict(layer),
                "reason": "retrieval operator is not supported by this runtime seam",
            }
            for layer in bound.get("retrieval_layers", [])
            if isinstance(layer, dict) and str(layer.get("operator") or "") not in {"exact_chunk_search", "lexical_chunk_search", "vector_search", "graph_expand", "temporal_retrieve"}
        ],
        "plan_completeness": completeness,
        "plan_executability": plan_executability,
        "coverage": {"exact_search_performed": False, "exact_status": "not_requested", "scope": bound.get("scope_filters", {}), "literal_terms": [], "coverage_claims_allowed": False, "negative_claims_allowed": False, "required_layer_results": [], "plan_completeness": completeness, "plan_executability": plan_executability},
        "limits": ["Retrieval was not executed because the compiler plan was incomplete."],
        "graph_traversal": {"status": "not_executed", "direction": config.graph_traversal_direction, "candidate_note_ids": []},
        "selection_notes": [note],
    }
    packet = {"bound_retrieval_plan": bound, "coverage": manifest["coverage"], "limits": manifest["limits"], "selected_chunks": [], "matched_chunk_count": 0, "retrieval_observation": "blocked_incomplete_plan", "assembled_from_traversal_manifest": True}
    return manifest, packet


def _non_executable_plan_artifacts(
    *,
    semantic_compiler_packet: dict[str, Any],
    config: RuntimeConfig,
    resource_inventory_summary: dict[str, Any],
    plan_executability: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Persist a blocked plan without opening the database or binding inputs."""
    planner = semantic_compiler_packet.get("planner_retrieval_plan") if isinstance(semantic_compiler_packet.get("planner_retrieval_plan"), dict) else {}
    completeness = semantic_compiler_packet.get("planner_diagnostics", {}).get("plan_completeness", {})
    note = "retrieval blocked: semantic compiler produced a non-executable retrieval plan"
    coverage = {
        "exact_search_performed": False,
        "exact_status": "not_requested",
        "scope": {},
        "literal_terms": [],
        "coverage_claims_allowed": False,
        "negative_claims_allowed": False,
        "required_layer_results": [],
        "plan_completeness": completeness,
        "plan_executability": plan_executability,
    }
    manifest = {
        "planner_retrieval_plan": planner,
        "bound_retrieval_plan": planner,
        "resolver_adjustments": [],
        "resource_inventory_summary": resource_inventory_summary,
        "inventory_diagnostics": resource_inventory_summary.get("inventory_diagnostics", {}),
        "execution": {"layers_executed": [], "layers_skipped": [{"reason": "non-executable plan"}]},
        "candidate_counts": {key: 0 for key in ("exact", "lexical", "vector", "graph", "temporal")},
        "selected_counts": {key: 0 for key in ("exact", "lexical", "vector", "graph", "temporal")},
        "selected_chunk_ids": [],
        "layer_manifests": {},
        "unsupported_layer_requests": [
            {
                "operator": str(layer.get("operator") or ""),
                "required": bool(layer.get("required")),
                "supplied_fields": dict(layer),
                "reason": "retrieval operator is not supported by this runtime seam",
            }
            for layer in planner.get("retrieval_layers", [])
            if isinstance(layer, dict) and str(layer.get("operator") or "") not in {"exact_chunk_search", "lexical_chunk_search", "vector_search", "graph_expand", "temporal_retrieve"}
        ],
        "plan_completeness": completeness,
        "plan_executability": plan_executability,
        "coverage": coverage,
        "limits": ["Retrieval was not executed because the compiler plan was not executable."],
        "graph_traversal": {"status": "not_executed", "direction": config.graph_traversal_direction, "candidate_note_ids": []},
        "selection_notes": [note],
    }
    packet = {
        "bound_retrieval_plan": planner,
        "coverage": coverage,
        "limits": manifest["limits"],
        "selected_chunks": [],
        "matched_chunk_count": 0,
        "retrieval_observation": "blocked_non_executable_plan",
        "assembled_from_traversal_manifest": True,
    }
    return manifest, packet


def _semantic_compiler_diagnostic_packet(
    *,
    response: SemanticCompilerResponse,
    semantic_compiler_status: str,
    config: RuntimeConfig,
    inventory_projection_diagnostics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    raw_response = response.raw_response if isinstance(response.raw_response, str) else None
    return {
        "semantic_compiler_response_status": response.status,
        "canonical_semantic_compiler_status": semantic_compiler_status,
        "metadata": response.metadata,
        "diagnostics": response.diagnostics,
        "parsed_payload_available": isinstance(response.parsed_payload, dict),
        "raw_response_available": bool(raw_response),
        "raw_response_hash": sha256_text(raw_response) if raw_response else None,
        "raw_response_preview": _snippet(raw_response, limit=int(config.runtime_conversation["raw_response_preview_limit"])) if raw_response else None,
        "inventory_projection": dict(inventory_projection_diagnostics or {}),
    }


def _is_compiler_packet_valid(packet: Any) -> bool:
    if not isinstance(packet, dict):
        return False
    required_keys = {
        "raw_user_input",
        "intent",
        "query",
        "entities",
        "relations",
        "resolved_referents",
        "planner_retrieval_plan",
        "limitations",
    }
    if not required_keys.issubset(packet):
        return False
    if not isinstance(packet["raw_user_input"], str) or not isinstance(packet["query"], str):
        return False
    for key in ("entities", "relations", "resolved_referents", "limitations"):
        if not isinstance(packet.get(key), list):
            return False
    if not isinstance(packet.get("planner_retrieval_plan"), dict):
        return False
    return True


def _load_chunk_rows(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    # Test doubles and pre-manifest databases may omit section_path_json. The
    # canonical ingest schema supplies it; retaining this narrow read fallback
    # keeps executor diagnostics usable for those intentionally minimal stores.
    rows = connection.execute("SELECT * FROM chunks").fetchall()
    result: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        try:
            item["section_path"] = " > ".join(json.loads(str(item.get("section_path_json") or "[]")))
        except (TypeError, ValueError, json.JSONDecodeError):
            item["section_path"] = str(item.get("section_label") or "")
        result.append(item)
    return result


def _flatten_semantic_leaves(value: Any, path: str = "frontmatter_semantics") -> list[tuple[str, str]]:
    leaves: list[tuple[str, str]] = []
    if isinstance(value, dict):
        for key in sorted(value, key=str):
            leaves.extend(_flatten_semantic_leaves(value[key], f"{path}.{key}"))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            leaves.extend(_flatten_semantic_leaves(item, f"{path}[{index}]"))
    elif value is not None:
        leaves.append((path, "true" if value is True else "false" if value is False else str(value)))
    return [(path, text) for path, text in leaves if text]


def _parse_frontmatter_semantics(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(str(value or "{}"))
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _layer_limit(layer: dict[str, Any] | None, fallback: int) -> int:
    if not isinstance(layer, dict) or "limit" not in layer:
        return fallback
    try:
        return max(0, int(layer["limit"]))
    except (TypeError, ValueError):
        return fallback


def _non_exact_layer_manifest(
    *,
    operator: str,
    layer: dict[str, Any] | None,
    status: str,
    candidate_count: int,
    diagnostics: dict[str, Any] | None = None,
    selected_contribution_count: int = 0,
    selected_chunk_ids: list[str] | None = None,
    adequate_contribution: bool = False,
) -> dict[str, Any]:
    layer = layer if isinstance(layer, dict) else {}
    return {
        "operator": operator,
        "requested": bool(layer),
        "required": bool(layer.get("required")),
        "status": status,
        "requested_limit": layer.get("requested_limit"),
        "default_limit": layer.get("default_limit"),
        "maximum_limit": layer.get("maximum_limit"),
        "effective_limit": layer.get("effective_limit", layer.get("limit")),
        "limit_adjustment": layer.get("limit_adjustment", "none"),
        "requested_depth": layer.get("requested_depth"),
        "effective_depth": layer.get("effective_depth"),
        "depth_adjustment": layer.get("depth_adjustment", "none"),
        "source": layer.get("source"),
        "automatic": bool(layer.get("automatic")),
        "support_reason": layer.get("support_reason"),
        "candidate_count": int(candidate_count),
        "selected_contribution_count": int(selected_contribution_count),
        "selected_chunk_ids": list(selected_chunk_ids or []),
        "adequate_contribution": bool(adequate_contribution),
        "diagnostics": diagnostics or [],
    }
def _scope_values(scope_filters: dict[str, Any], key: str) -> list[str]:
    return [str(value).strip().lower() for value in _coerce_string_list(scope_filters.get(key)) if str(value).strip()]


def _chunk_matches_scope(row: dict[str, Any], scope_filters: dict[str, Any]) -> bool:
    if not isinstance(scope_filters, dict):
        return True
    source_label = str(scope_filters.get("source_label") or "").strip().lower()
    if source_label and str(row.get("source_root_label") or "").strip().lower() != source_label:
        return False
    path_terms = _scope_values(scope_filters, "path_contains")
    note_type_terms = _scope_values(scope_filters, "note_type")
    if not path_terms and not note_type_terms:
        return True
    relative_path = str(row.get("relative_path") or "").lower().replace("\\", "/")
    source_root_path = str(row.get("source_root_path") or "").lower().replace("\\", "/")
    frontmatter_values: dict[str, Any] = {}
    try:
        parsed_frontmatter = json.loads(str(row.get("frontmatter_semantics_json") or "{}"))
        if isinstance(parsed_frontmatter, dict):
            frontmatter_values = parsed_frontmatter
    except json.JSONDecodeError:
        frontmatter_values = {}

    # A dimension is an OR set; different dimensions are conjunctive. Note type
    # is read from parsed frontmatter so a value such as `journal` cannot match
    # an unrelated serialized field or a substring of another facet.
    if note_type_terms:
        raw_note_type = frontmatter_values.get("note_type")
        note_types = _coerce_string_list(raw_note_type)
        normalized_note_types = {value.strip().lower() for value in note_types if value.strip()}
        if not normalized_note_types.intersection(note_type_terms):
            return False
    if path_terms:
        path_haystack = f"{source_root_path} {relative_path}".lower()
        if not any(term in path_haystack for term in path_terms):
            return False
    return True


def _scoped_chunk_rows(chunk_rows: list[dict[str, Any]], scope_filters: dict[str, Any]) -> list[dict[str, Any]]:
    return [row for row in chunk_rows if _chunk_matches_scope(row, scope_filters)]


def _annotate_scope_matches(
    candidates: list[dict[str, Any]],
    *,
    hard_scope_filters: dict[str, Any],
) -> list[dict[str, Any]]:
    """Expose the concrete execution scope used by the existing APIs."""
    annotated: list[dict[str, Any]] = []
    for candidate in candidates:
        next_candidate = dict(candidate)
        if "scope_match" not in next_candidate:
            next_candidate["scope_match"] = hard_scope_filters
        annotated.append(next_candidate)
    return annotated


def _candidate_source_layers(candidate: dict[str, Any]) -> list[str]:
    # `source_layers` is materialized before merge-only fields are removed. It
    # is the durable provenance surface; `sources` is only the merge scratch
    # field and selection_source is only the winning representation.
    sources = candidate.get("source_layers") or candidate.get("sources") or [candidate.get("selection_source") or "lexical"]
    observed: set[str] = set()
    for source in _coerce_string_list(sources):
        if source == "graph_expanded":
            source = "graph"
        if source:
            observed.add(source)
    # Keep packet and manifest ordering stable even when candidates arrive from
    # different executors or contain duplicate support entries.
    return [source for source in ("exact", "lexical", "vector", "graph", "temporal") if source in observed]


def _count_selected_by_layer(selected_candidates: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"exact": 0, "lexical": 0, "vector": 0, "graph": 0}
    if any("temporal" in _candidate_source_layers(candidate) for candidate in selected_candidates):
        counts["temporal"] = 0
    for candidate in selected_candidates:
        for source in _candidate_source_layers(candidate):
            if source in counts:
                counts[source] += 1
    return counts


def _resolved_wikilink_promotions(
    *, connection: sqlite3.Connection, candidate: dict[str, Any], plan: dict[str, Any],
) -> list[dict[str, Any]]:
    """Promote exact link-target matches without rematerializing note text."""
    source_note_id = str(candidate.get("note_id") or "").strip()
    if not source_note_id:
        return []
    referents = _coerce_string_list(plan.get("resolved_referents"))
    if not referents:
        return []
    try:
        rows = connection.execute(
            f"SELECT metadata_json FROM {plan.get('_graph_edges_table', 'graph_edges')} WHERE source_node_id = ? AND edge_type = 'note_links_note'",
            (f"note::{source_note_id}",),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    promotions: list[dict[str, Any]] = []
    for row in rows:
        try:
            metadata = json.loads(str(row["metadata_json"] or "{}"))
        except (TypeError, json.JSONDecodeError):
            continue
        occurrences = metadata.get("provenance") if isinstance(metadata.get("provenance"), list) else [metadata]
        for occurrence in occurrences:
            if not isinstance(occurrence, dict) or str(occurrence.get("resolution_status") or ("resolved" if occurrence.get("resolved") else "unresolved")) != "resolved":
                continue
            target_note_id = str(occurrence.get("resolved_note_id") or occurrence.get("target_note_id") or "").strip()
            if not target_note_id:
                continue
            surfaces = [occurrence.get("target_base"), occurrence.get("target_note"), occurrence.get("target_text"), occurrence.get("alias"), occurrence.get("display_text")]
            normalized_surfaces = [_normalize_text(value) for value in surfaces if str(value or "").strip()]
            for index, referent in enumerate(referents):
                normalized_referent = _normalize_text(referent)
                if not normalized_referent:
                    continue
                matched_surface = next((surface for surface in normalized_surfaces if re.search(rf"(?<!\w){re.escape(normalized_referent)}(?!\w)", surface)), None)
                if matched_surface is None:
                    continue
                item = {
                    "subject_id": f"subject-{index}",
                    "referent": referent,
                    "target_note_id": target_note_id,
                    "target_graph_node_id": str(occurrence.get("target_graph_node_id") or f"note::{target_note_id}"),
                    "matched_surface": matched_surface,
                    "occurrence": occurrence,
                }
                if item not in promotions:
                    promotions.append(item)
    return promotions


def _grounding_summary(*, specification: dict[str, Any], candidates: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize the final canonical candidate assessments, not route scratch."""
    bundles = specification.get("bundles", []) if isinstance(specification, dict) else []
    atoms = [
        atom
        for bundle in bundles if isinstance(bundle, dict)
        for key in ("referent_atoms", "subject_bearing_atoms", "subject_predicate_atoms", "shared_predicate_atoms", "query_level_atoms")
        for atom in ((bundle.get(key) or []) if isinstance(bundle.get(key), list) else [])
    ]
    atom_keys: set[str] = set()
    unique_atoms: list[dict[str, Any]] = []
    for atom in atoms:
        key = json.dumps(atom, sort_keys=True, ensure_ascii=True)
        if key not in atom_keys:
            atom_keys.add(key)
            unique_atoms.append(atom)
    atoms = unique_atoms
    unique_candidates = {str(item.get("chunk_id") or ""): item for item in candidates if str(item.get("chunk_id") or "")}
    evidence_units = {
        chunk_id for chunk_id, candidate in unique_candidates.items()
        if ((candidate.get("grounding") or {}).get("evidence_unit") or {}).get("subjects")
    }
    proposition_units = {
        chunk_id for chunk_id, candidate in unique_candidates.items()
        if ((candidate.get("grounding") or {}).get("relation_proposition") or {}).get("eligible")
    }
    identity_notes: set[str] = set()
    promotions: set[tuple[str, str, str]] = set()
    body_promotions = 0
    frontmatter_promotions = 0
    unresolved = 0
    authorized_seeds: set[str] = set()
    typed_relation_count = 0
    typed_relation_temporal_count = 0
    rejection_reason_counts: Counter[str] = Counter()
    per_subject: dict[str, dict[str, int]] = defaultdict(lambda: {"evidence_grounded": 0, "predicate_grounded": 0, "proposition_grounded": 0})
    for candidate in unique_candidates.values():
        grounding = candidate.get("grounding") or {}
        typed_matches = [
            entry
            for evidence in (grounding.get("predicate_evidence") or {}).values()
            if isinstance(evidence, list)
            for entry in evidence
            if isinstance(entry, dict) and entry.get("match") == "typed_relation_provenance"
        ]
        typed_relation_count += len(typed_matches)
        if typed_matches and "temporal" in _candidate_source_layers(candidate):
            typed_relation_temporal_count += len(typed_matches)
        for reason in _coerce_string_list(grounding.get("rejection_reasons")):
            rejection_reason_counts[reason] += 1
        for subject_id in _coerce_string_list((grounding.get("evidence_unit") or {}).get("subjects")):
            per_subject[subject_id]["evidence_grounded"] += 1
        for subject_id in (grounding.get("predicate_evidence") or {}).keys():
            per_subject[subject_id]["predicate_grounded"] += 1
        for subject_id in _coerce_string_list((grounding.get("relation_proposition") or {}).get("subjects")):
            per_subject[subject_id]["proposition_grounded"] += 1
        for subject in (grounding.get("object_identity") or {}).get("subjects", []):
            target_ids = candidate.get("identity_seed_note_ids") or [candidate.get("note_id")]
            identity_notes.update(str(value) for value in target_ids if str(value or ""))
        for promotion in candidate.get("wikilink_identity_promotions", []):
            if not isinstance(promotion, dict):
                continue
            occurrence = promotion.get("occurrence") or {}
            target_id = str(promotion.get("target_note_id") or "")
            key = (str(candidate.get("chunk_id") or ""), target_id, str(promotion.get("subject_id") or ""))
            if target_id:
                promotions.add(key)
                authorized_seeds.add(target_id)
                if occurrence.get("source_surface") == "body":
                    body_promotions += 1
                elif occurrence.get("source_surface") == "admitted_frontmatter":
                    frontmatter_promotions += 1
        for occurrence in candidate.get("wikilink_occurrences", []):
            if str(occurrence.get("resolution_status") or "") != "resolved":
                unresolved += 1
    diagnostics = {
        "version": 1,
        "subject_bundle_count": len(bundles) if specification.get("named_subjects") else 0,
        "query_level_bundle_count": 0 if specification.get("named_subjects") else len(bundles),
        "subject_only_atom_count": sum(atom.get("role") == "subject_only" for atom in atoms),
        "predicate_only_atom_count": sum(atom.get("role") == "predicate_only" for atom in atoms),
        "subject_and_predicate_atom_count": sum(atom.get("role") == "subject_and_predicate" for atom in atoms),
        "query_level_atom_count": sum(atom.get("role") == "query_level_context" for atom in atoms),
        "nonempty_predicate_residual_count": sum(bool(atom.get("predicate_residual")) for atom in atoms),
        "canonical_object_identity_note_count": len(identity_notes),
        "descriptive_subject_count": sum(not bool((candidate.get("grounding") or {}).get("object_identity", {}).get("subjects")) and bool((candidate.get("grounding") or {}).get("evidence_unit", {}).get("subjects")) for candidate in unique_candidates.values()),
        "evidence_grounded_unit_count": len(evidence_units),
        "proposition_grounded_unit_count": len(proposition_units),
        "multi_subject_proposition_unit_count": sum(len(((candidate.get("grounding") or {}).get("relation_proposition") or {}).get("subjects", [])) > 1 for candidate in unique_candidates.values()),
        "resolved_wikilink_target_promotion_count": len(promotions),
        "resolved_wikilink_occurrence_count": len(promotions),
        "unresolved_wikilink_occurrence_count": unresolved,
        "body_link_promotion_count": body_promotions,
        "frontmatter_link_promotion_count": frontmatter_promotions,
        "identity_authorized_graph_seed_count": len(authorized_seeds),
        "evidence_only_note_count": len({str(candidate.get("note_id") or "") for candidate in unique_candidates.values() if not ((candidate.get("grounding") or {}).get("object_identity") or {}).get("subjects")}),
        "grounding_assessment_merge_count": sum(1 for candidate in candidates if isinstance(candidate.get("grounding"), dict) and len(candidate.get("source_layers", [])) > 1),
        "query_level_evidence_grounded_unit_count": sum(1 for candidate in unique_candidates.values() if "query-0" in _coerce_string_list(((candidate.get("grounding") or {}).get("evidence_unit") or {}).get("subjects"))),
        "query_level_proposition_grounded_unit_count": sum(1 for candidate in unique_candidates.values() if "query-0" in _coerce_string_list(((candidate.get("grounding") or {}).get("relation_proposition") or {}).get("subjects"))),
        "query_level_missing_subject_grounding_count": sum(1 for candidate in unique_candidates.values() if not specification.get("named_subjects") and "missing_subject_grounding" in _coerce_string_list((candidate.get("grounding") or {}).get("rejection_reasons"))),
        "typed_relation_evidence_count": typed_relation_count,
        "typed_relation_temporal_candidate_count": typed_relation_temporal_count,
        "typed_relation_rejection_reason_counts": dict(sorted(rejection_reason_counts.items())),
        "per_subject_grounding": {key: per_subject[key] for key in sorted(per_subject)},
    }
    diagnostics["proposition_implies_evidence"] = diagnostics["proposition_grounded_unit_count"] <= diagnostics["evidence_grounded_unit_count"]
    diagnostics["invariant_status"] = "valid" if diagnostics["proposition_implies_evidence"] else "invalid_proposition_without_evidence"
    if diagnostics["invariant_status"] == "invalid_proposition_without_evidence":
        diagnostics["invariant_diagnostic"] = "proposition-grounded unit count exceeds evidence-grounded unit count"
    return diagnostics


def _annotate_context_candidates(
    candidates: list[dict[str, Any]], *, plan: dict[str, Any], config: RuntimeConfig, connection: sqlite3.Connection | None = None,
) -> list[dict[str, Any]]:
    """Attach explicit grounding authority without turning referents into filters."""
    specification = build_grounding_spec(plan)
    annotated: list[dict[str, Any]] = []
    for candidate in candidates:
        next_candidate = dict(candidate)
        if connection is not None:
            next_candidate["wikilink_identity_promotions"] = _resolved_wikilink_promotions(
                connection=connection, candidate=next_candidate, plan={**plan, "_graph_edges_table": config.graph_edges_table},
            )
        assessment = classify_candidate(next_candidate, specification)
        existing = next_candidate.get("grounding")
        if isinstance(existing, dict):
            assessment = merge_grounding_assessments(existing, assessment)
        next_candidate["grounding"] = assessment
        bundle_referents = {
            str(bundle.get("subject_id")): str(bundle.get("referent") or "")
            for bundle in specification.as_dict().get("bundles", [])
        }
        matched_subjects = list(dict.fromkeys(
            str(value) for value in (assessment.get("relation_proposition") or {}).get("subject_referents", []) if str(value).strip()
        ))
        authorized_subject_ids = _coerce_string_list((assessment.get("graph_authority") or {}).get("may_propagate_subjects"))
        authorized_subjects = [bundle_referents.get(value, value) for value in authorized_subject_ids]
        identity_seed_note_ids = [
            str(item.get("target_note_id") or "") for item in next_candidate.get("wikilink_identity_promotions", [])
            if str(item.get("target_note_id") or "")
        ]
        if identity_seed_note_ids:
            next_candidate["identity_seed_note_ids"] = list(dict.fromkeys(identity_seed_note_ids))
            (assessment.setdefault("graph_authority", {}))["identity_seed_note_ids"] = list(dict.fromkeys(identity_seed_note_ids))
        next_candidate["context_subjects"] = list(dict.fromkeys([*_coerce_string_list(candidate.get("context_subjects")), *matched_subjects]))
        next_candidate["authorized_subjects"] = list(dict.fromkeys([
            *_coerce_string_list(candidate.get("authorized_subjects")),
            *authorized_subjects,
        ]))
        next_candidate["context_probe_provenance"] = list(dict.fromkeys([
            *(_coerce_string_list(candidate.get("context_probe_provenance"))),
            *[str(item.get("value") or "") for item in (assessment.get("evidence_unit") or {}).get("context_atoms", []) if isinstance(item, dict)],
        ]))
        # Compatibility only: this means proposition/evidence context was
        # grounded, never that the note is the subject object.
        next_candidate["context_grounded"] = bool((assessment.get("evidence_unit") or {}).get("subjects")) or bool(candidate.get("context_grounded"))
        next_candidate["context_route"] = str(candidate.get("selection_source") or "unknown")
        annotated.append(next_candidate)
    return annotated


def _enforce_canonical_subject_authority(
    candidates: list[dict[str, Any]], *, canonical_referents: set[str], named_subjects: bool,
) -> list[dict[str, Any]]:
    """Prevent descriptive matches from competing with discovered canonical objects."""
    if not named_subjects or not canonical_referents:
        return candidates
    adjusted: list[dict[str, Any]] = []
    for candidate in candidates:
        next_candidate = dict(candidate)
        grounding = dict(next_candidate.get("grounding") or {})
        relation = dict(grounding.get("relation_proposition") or {})
        has_identity = bool((grounding.get("object_identity") or {}).get("subjects"))
        has_authorized_route = bool(next_candidate.get("authorized_subjects"))
        if not has_identity and not has_authorized_route:
            subject_ids = list(relation.get("subjects", []))
            subject_refs = list(relation.get("subject_referents", []))
            retained = [index for index, value in enumerate(subject_refs) if value not in canonical_referents]
            relation["subject_referents"] = [subject_refs[index] for index in retained]
            relation["subjects"] = [subject_ids[index] for index in retained if index < len(subject_ids)]
            relation["eligible"] = bool(relation["subjects"])
            grounding["relation_proposition"] = relation
            next_candidate["grounding"] = grounding
            next_candidate["context_subjects"] = [value for value in _coerce_string_list(next_candidate.get("context_subjects")) if value not in canonical_referents]
        adjusted.append(next_candidate)
    return adjusted


def _exact_candidates(
    chunk_rows: list[dict[str, Any]],
    literal_terms: list[dict[str, Any]],
    *,
    scope_filters: dict[str, Any],
    limit: int,
    return_total_count: bool = False,
    config: RuntimeConfig,
) -> tuple[list[dict[str, Any]], list[str], dict[str, Any]]:
    fields = ("note_title", "relative_path", "section_path", "paragraph_text")
    notes: list[str] = []
    term_entries = []
    for entry in literal_terms:
        if not isinstance(entry, dict):
            continue
        term = str(entry.get("term") or "").strip()
        if term:
            term_entries.append({
                "term": term,
                "match": str(entry.get("match") or "case_insensitive_substring").strip(),
                "required": bool(entry.get("required")),
                "automatic": bool(entry.get("automatic")),
                "source_atom": entry.get("source_atom"),
                "exhaustive": bool(entry.get("exhaustive", not entry.get("automatic"))),
            })
    terms = [entry["term"] for entry in term_entries]
    base_info = {
        "operator": "exact_chunk_search",
        "literal_terms": terms,
        "scope": scope_filters,
        "search_exhaustive": False,
        "return_total_count_requested": bool(return_total_count),
        "count_status": "not_requested" if not return_total_count else "completed",
        "count_unit": "matching_chunks",
        "total_match_count": None if not return_total_count else 0,
        "total_occurrence_count": None if not return_total_count else 0,
        "matching_note_count": None if not return_total_count else 0,
        "returned_candidate_count": 0,
        "returned_count": 0,
        "term_results": [],
    }
    if not terms:
        base_info.update(status="skipped_no_terms", search_exhaustive=False, count_status="not_applicable")
        return [], ["exact search skipped: no literal terms"], base_info

    def occurrences(value: str, term: str, match_mode: str) -> list[tuple[int, int]]:
        if match_mode not in {"case_sensitive_substring", "case_insensitive_substring"}:
            return []
        if not term:
            return []
        if match_mode == "case_sensitive_substring":
            matcher = re.compile(re.escape(term))
        else:
            # Search the original text so regex offsets remain offsets into
            # the canonical field even when Unicode case handling expands a
            # code point during comparison.
            matcher = re.compile(re.escape(term), re.IGNORECASE)
        found: list[tuple[int, int]] = []
        # Count deterministic non-overlapping occurrences. This mirrors the
        # first-match context contract and avoids double-counting overlaps.
        for match in matcher.finditer(value):
            found.append((match.start(), match.end()))
        return found

    def context(value: str, start: int, end: int) -> dict[str, Any]:
        radius = max(0, int(config.retrieval_exact_context_chars))
        return {
            "field": "",
            "match_start": start,
            "match_end": end,
            "excerpt": value[max(0, start - radius): min(len(value), end + radius)],
        }

    scoped_rows = _scoped_chunk_rows(chunk_rows, scope_filters)
    candidates: list[dict[str, Any]] = []
    per_term: dict[str, dict[str, Any]] = {
        entry["term"]: {
            "term": entry["term"], "required": entry["required"], "match": entry["match"],
            "automatic": entry["automatic"], "source_atom": entry["source_atom"], "exhaustive": entry["exhaustive"],
            "status": "failed" if entry["match"] not in {"case_sensitive_substring", "case_insensitive_substring"} else "completed_no_matches",
            "matching_chunk_count": 0, "matching_note_count": 0, "total_occurrence_count": 0,
            "returned_candidate_count": 0, "selected_exact_chunk_count": 0, "selected_chunk_ids": [],
            "adequate_contribution": not entry["required"],
        }
        for entry in term_entries
    }
    matching_note_ids: set[str] = set()
    total_match_count = 0
    total_occurrence_count = 0
    for row in scoped_rows:
        evidence: list[dict[str, Any]] = []
        matched_terms: list[str] = []
        for entry in term_entries:
            term = entry["term"]
            term_occurrences: list[tuple[str, int, int, str]] = []
            searchable_fields = (("note_id",) + fields) if entry["automatic"] else fields
            searchable_values = [(field, str(row.get(field) or "")) for field in searchable_fields]
            for path, value in _flatten_semantic_leaves(_parse_frontmatter_semantics(row.get("frontmatter_semantics_json"))):
                searchable_values.extend(((path, path), (path, value)))
            for field, value in searchable_values:
                for start, end in occurrences(value, term, entry["match"]):
                    term_occurrences.append((field, start, end, value))
            if not term_occurrences:
                continue
            matched_terms.append(term)
            result = per_term[term]
            result["status"] = "completed_with_matches"
            result["matching_chunk_count"] += 1
            result["matching_note_count"] = None
            result["total_occurrence_count"] += len(term_occurrences)
            total_occurrence_count += len(term_occurrences)
            for field, start, end, matched_value in term_occurrences:
                representative = context(matched_value, start, end)
                representative["field"] = field
                evidence.append({
                    "term": term,
                    "match": entry["match"],
                    "automatic": entry["automatic"],
                    "source_atom": entry["source_atom"],
                    "matched_fields": [field],
                    "occurrence_count_in_chunk": len([item for item in term_occurrences if item[0] == field]),
                    "representative_context": representative,
                })
                break
        if not matched_terms:
            continue
        total_match_count += 1
        matching_note_ids.add(str(row.get("note_id") or ""))
        for term in matched_terms:
            per_term[term]["matching_note_count"] = None
        if len(candidates) < max(0, limit):
            candidates.append({
                **row,
                "selection_reason": f"exact literal match: {', '.join(matched_terms)}",
                "score": len(matched_terms) + float(config.retrieval_scoring["exact_bonus"]),
                "selection_source": "exact",
                "match_reason": f"literal term {', '.join(matched_terms)}",
                "scope_match": scope_filters,
                "exact_term_provenance": list(matched_terms),
                "exact_match_evidence": evidence,
            })
            for term in matched_terms:
                per_term[term]["returned_candidate_count"] += 1

    for term, result in per_term.items():
        result["matching_note_count"] = len({str(row.get("note_id") or "") for row in scoped_rows if any(
            occurrences(value, term, next(entry["match"] for entry in term_entries if entry["term"] == term))
            for _, value in [(field, str(row.get(field) or "")) for field in fields] + [item for path, value in _flatten_semantic_leaves(_parse_frontmatter_semantics(row.get("frontmatter_semantics_json"))) for item in ((path, path), (path, value))]
        )})
    for result in per_term.values():
        if result["status"] == "completed_no_matches":
            result["adequate_contribution"] = True
        if not return_total_count:
            # Status still comes from the exhaustive scan, but exhaustive
            # count reporting was not requested and must not leak as a total.
            result["matching_chunk_count"] = None
            result["matching_note_count"] = None
            result["total_occurrence_count"] = None
    base_info.update({
        "status": "failed" if any(result["status"] == "failed" for result in per_term.values()) else ("completed_with_matches" if total_match_count else "completed_no_matches"),
        "search_exhaustive": True,
        "count_status": "completed" if return_total_count else "not_requested",
        "total_match_count": total_match_count if return_total_count else None,
        "total_occurrence_count": total_occurrence_count if return_total_count else None,
        "matching_note_count": len([note_id for note_id in matching_note_ids if note_id]) if return_total_count else None,
        "returned_candidate_count": len(candidates),
        "returned_count": len(candidates),
        "term_results": list(per_term.values()),
    })
    notes.append(
        f"exact search matched {total_match_count} chunk(s); returned {len(candidates)}"
        if total_match_count else "exact search produced no matches"
    )
    return candidates, notes, base_info


def _graph_seed_values(
    *,
    planner_retrieval_plan: dict[str, Any],
    config: RuntimeConfig,
) -> list[tuple[str, str]]:
    seeds: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    configured_sources = set(config.graph_traversal_seed_sources)
    if "graph_seeds" in configured_sources:
        for value in _coerce_string_list(planner_retrieval_plan.get("graph_seeds")):
            seed = ("graph_seeds", value)
            if seed not in seen:
                seen.add(seed)
                seeds.append(seed)
    if "semantic_queries" in configured_sources:
        for value in _coerce_string_list(planner_retrieval_plan.get("semantic_queries")):
            seed = ("semantic_queries", value)
            if seed not in seen:
                seen.add(seed)
                seeds.append(seed)
    return [(source, seed) for source, seed in seeds if seed]


def _graph_token_set(value: str, *, config: RuntimeConfig) -> set[str]:
    return set(_extract_terms(value, config=config))


def _graph_match_note_nodes(
    *,
    seed: str,
    node_rows: list[dict[str, Any]],
    node_type_allowlist: set[str],
    match_mode: str,
    min_token_overlap: int,
    config: RuntimeConfig,
) -> list[dict[str, Any]]:
    exact_matches: list[dict[str, Any]] = []
    overlap_matches: list[dict[str, Any]] = []
    seed_normalized = _normalize_text(seed)
    seed_tokens = _graph_token_set(seed, config=config)
    for row in node_rows:
        node_type = str(row.get("node_type") or "")
        if node_type not in node_type_allowlist:
            continue
        label = str(row.get("label") or "")
        ref_id = str(row.get("ref_id") or "")
        normalized_label = _normalize_text(label)
        normalized_ref = _normalize_text(ref_id)
        if seed_normalized and seed_normalized in {normalized_label, normalized_ref}:
            exact_matches.append(dict(row))
            continue
        if match_mode != "exact_or_token_overlap":
            continue
        node_tokens = _graph_token_set(f"{label} {ref_id}", config=config)
        if len(seed_tokens.intersection(node_tokens)) >= min_token_overlap:
            overlap_matches.append(dict(row))
    return exact_matches or overlap_matches


def _serialize_fts5_atom(value: str) -> str:
    """Quote one literal FTS5 value without granting it operator syntax."""
    return '"' + str(value).replace('"', '""') + '"'


def _serialize_fts5_query_atoms(tokens: list[str], *, mode: str) -> str:
    """Serialize literal atoms while keeping the accepted mode grammar fixed."""
    atoms = [_serialize_fts5_atom(token) for token in tokens if token]
    if mode == "prefix":
        # The wildcard belongs outside the quoted literal, where FTS5 treats
        # it as prefix grammar rather than as user-controlled text.
        atoms = [f"{atom}*" for atom in atoms]
    if not atoms:
        return ""
    joiner = " AND " if mode == "all_tokens" else " OR "
    return joiner.join(atoms)


def _lexical_candidates(
    chunk_rows: list[dict[str, Any]],
    query_terms: list[str],
    *,
    connection: sqlite3.Connection,
    scope_filters: dict[str, Any] | None = None,
    limit: int | None = None,
    config: RuntimeConfig,
    mode: str | None = None,
    return_diagnostics: bool = False,
) -> tuple[list[dict[str, Any]], list[str]] | tuple[list[dict[str, Any]], list[str], dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    notes: list[str] = []
    diagnostics: dict[str, Any] = {
        "requested_queries": list(query_terms),
        "original_query": list(query_terms),
        "effective_query": None,
        "query_results": [],
        "requested_mode": mode,
        "effective_mode": None,
        "fts_available": None,
        "index_row_count": None,
        "raw_match_count": None,
        "scoped_match_count": None,
        "returned_candidate_count": 0,
        "candidate_count": 0,
    }
    def finish() -> tuple[list[dict[str, Any]], list[str]] | tuple[list[dict[str, Any]], list[str], dict[str, Any]]:
        diagnostics["returned_candidate_count"] = len(candidates)
        diagnostics["candidate_count"] = len(candidates)
        return (candidates, notes, diagnostics) if return_diagnostics else (candidates, notes)

    # Kept as a keyword-only compatibility escape hatch for runtime manifests.
    # Existing focused executor tests retain the historical two-value return.
    if not query_terms:
        diagnostics["status"] = "skipped_no_input"
        return finish()
    selected_mode = str(mode or config.retrieval_lexical_default_mode).strip()
    diagnostics["effective_mode"] = selected_mode
    supported_modes = {"exact_phrase", "all_tokens", "any_tokens", "prefix", "ranked_fts"}
    if selected_mode not in supported_modes:
        diagnostics["status"] = "unsupported"
        message = [f"lexical search unavailable: unsupported mode {selected_mode}"]
        return ([], message, diagnostics) if return_diagnostics else ([], message)
    terms = [str(term).strip() for term in query_terms if str(term).strip()]
    if not terms:
        diagnostics["status"] = "skipped_no_input"
        return finish()
    rows_by_id = {str(row.get("chunk_id")): row for row in _scoped_chunk_rows(chunk_rows, scope_filters or {})}
    merged: dict[str, dict[str, Any]] = {}
    raw_match_count = 0
    scoped_match_count = 0
    for query in terms:
        tokens = QUERY_TOKEN_RE.findall(query.casefold())
        if not tokens:
            continue
        if selected_mode == "exact_phrase":
            fts_query = _serialize_fts5_atom(" ".join(query.split()))
        else:
            fts_query = _serialize_fts5_query_atoms(tokens, mode=selected_mode)
        if not fts_query:
            continue
        if diagnostics["effective_query"] is None:
            diagnostics["effective_query"] = fts_query
        try:
            match_rows = connection.execute(
                "SELECT chunk_id, bm25(chunks_fts) AS fts_rank FROM chunks_fts WHERE chunks_fts MATCH ? ORDER BY fts_rank ASC, chunk_id ASC",
                (fts_query,),
            ).fetchall()
        except sqlite3.Error as exc:
            diagnostics["status"] = "unavailable" if "no such table" in str(exc).lower() else "failed"
            diagnostics["fts_available"] = False
            message = [f"lexical search unavailable: FTS5 index error: {exc}"]
            return ([], message, diagnostics) if return_diagnostics else ([], message)
        diagnostics["query_results"].append({"query": query, "fts_query": fts_query, "raw_match_count": len(match_rows)})
        raw_match_count += len(match_rows)
        scoped_match_count += sum(1 for match_row in match_rows if str(match_row["chunk_id"]) in rows_by_id)
        for match_row in match_rows:
            chunk_id = str(match_row["chunk_id"])
            row = rows_by_id.get(chunk_id)
            if row is None:
                continue
            rank = float(match_row["fts_rank"] or 0.0)
            existing = merged.get(chunk_id)
            if existing is None or rank < float(existing["fts_rank"]):
                merged[chunk_id] = {
                    **row,
                    "selection_reason": f"FTS5 {selected_mode} match: {query}",
                    "score": (1.0 / (1.0 + abs(rank))) + float(config.retrieval_scoring["lexical_bonus"]),
                    "selection_source": "lexical",
                    "match_reason": f"FTS5 {selected_mode} query {fts_query}",
                    "lexical_mode": selected_mode,
                    "lexical_query": query,
                    "lexical_query_provenance": [query],
                    "fts_rank": rank,
                }
            elif query not in existing["lexical_query_provenance"]:
                existing["lexical_query_provenance"].append(query)
    ordered = sorted(merged.values(), key=lambda item: (float(item["fts_rank"]), str(item["chunk_id"])))
    candidates = ordered[: max(0, int(limit))] if limit is not None else ordered
    if not diagnostics["query_results"]:
        diagnostics["status"] = "skipped_no_input"
        return finish()
    diagnostics["fts_available"] = True
    diagnostics["index_row_count"] = int(connection.execute("SELECT COUNT(*) FROM chunks_fts").fetchone()[0])
    diagnostics["raw_match_count"] = raw_match_count
    diagnostics["scoped_match_count"] = scoped_match_count
    if candidates:
        notes.append(f"lexical FTS5 search mode={selected_mode} matched {len(candidates)} chunk(s)")
    else:
        notes.append("lexical search produced no matches")
    diagnostics["status"] = "completed_with_candidates" if candidates else "completed_no_candidates"
    return finish()


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    numerator = sum(x * y for x, y in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return numerator / (left_norm * right_norm)


def _vector_candidates(
    *,
    connection: sqlite3.Connection,
    config: RuntimeConfig,
    embedding_backend: EmbeddingBackend,
    vector_query: str,
    vector_queries: list[str] | None = None,
    scope_filters: dict[str, Any] | None = None,
    limit: int | None = None,
    eligible_chunk_ids: set[str] | None = None,
    return_diagnostics: bool = False,
) -> tuple[list[dict[str, Any]], list[str]] | tuple[list[dict[str, Any]], list[str], dict[str, Any]]:
    notes: list[str] = []
    candidates: list[dict[str, Any]] = []
    diagnostics: dict[str, Any] = {
        "requested_queries": [],
        "effective_queries": [],
        "query_results": [],
        "candidate_count": 0,
        "effective_limit": 0,
        "configured_min_similarity": config.retrieval_vector_min_similarity,
        "effective_min_similarity": config.retrieval_vector_min_similarity,
        "configured_per_query_max_candidates": config.retrieval_vector_per_query_max_candidates,
        "configured_max_chunks_per_note": config.retrieval_vector_max_chunks_per_note,
        "stored_vector_row_count": 0,
        "compatible_vector_count": 0,
        "invalid_json_count": 0,
        "invalid_numeric_count": 0,
        "empty_vector_count": 0,
        "zero_norm_count": 0,
        "dimension_mismatch_count": 0,
        "identity_mismatch_count": 0,
        "mixed_identity_count": 0,
        "below_threshold_count": 0,
        "at_or_above_threshold_count": 0,
        "note_cap_attrition_count": 0,
        "per_query_cap_attrition_count": 0,
        "query_order_policy": "canonical_lexicographic",
    }

    def finish() -> tuple[list[dict[str, Any]], list[str]] | tuple[list[dict[str, Any]], list[str], dict[str, Any]]:
        diagnostics["candidate_count"] = len(candidates)
        diagnostics["returned_candidate_count"] = len(candidates)
        return (candidates, notes, diagnostics) if return_diagnostics else (candidates, notes)

    requested_queries = [str(query).strip() for query in (vector_queries or [vector_query]) if str(query).strip()]
    queries = sorted(set(requested_queries))
    diagnostics["requested_queries"] = requested_queries
    diagnostics["effective_queries"] = queries
    effective_limit = min(config.retrieval_vector_max_candidates, max(0, int(limit if limit is not None else config.retrieval_vector_max_candidates)))
    diagnostics["effective_limit"] = effective_limit
    if not queries:
        diagnostics["status"] = "skipped_no_input"
        return finish()
    if getattr(embedding_backend, "mode_name", "unavailable") == "unavailable":
        diagnostics["status"] = "unavailable"
        diagnostics["failure_reason"] = "embedding backend unavailable"
        return finish()

    query_vectors: list[tuple[str, list[float], dict[str, Any]]] = []
    query_failures: list[str] = []
    for query in queries:
        response = embedding_backend.embed_query_text(query)
        query_result: dict[str, Any] = {
            "query": query,
            "backend_status": response.status,
            "embedding_succeeded": False,
            "vector_rows_considered": 0,
            "scope_admissible_rows": 0,
            "rows_at_or_above_threshold": 0,
            "candidates_before_per_query_cap": 0,
            "candidates_after_per_query_cap": 0,
            "candidates_represented_after_merged_allocation": 0,
        }
        if response.status == "embedded" and response.vectors and isinstance(response.vectors[0], list):
            query_vector = response.vectors[0]
            if query_vector and all(isinstance(value, (int, float)) for value in query_vector):
                query_identity = embedding_identity_from_response(response, config=config, vector=[float(value) for value in query_vector])
                if not isinstance(response.metadata, dict) or not response.metadata.get("backend_mode"):
                    query_identity["provider"] = str(getattr(embedding_backend, "mode_name", "unknown"))
                query_norm = math.sqrt(sum(float(value) * float(value) for value in query_vector))
                if query_norm > 0.0:
                    query_vectors.append((query, [float(value) for value in query_vector], query_identity))
                    query_result.update(
                        {
                            "embedding_succeeded": True,
                            "query_identity": query_identity,
                            "query_dimensions": len(query_vector),
                        }
                    )
                else:
                    query_result["diagnostic"] = "query vector has zero norm"
            else:
                query_result["diagnostic"] = "query vector is empty or nonnumeric"
        else:
            query_result["diagnostic"] = "query embedding failed"
        if not query_result["embedding_succeeded"]:
            query_failures.append(f"{query}: {response.status}")
        diagnostics["query_results"].append(query_result)
    if not query_vectors:
        diagnostics["status"] = "failed" if query_failures else "unavailable"
        return finish()

    try:
        rows = connection.execute(
            f"SELECT chunk_id, vector_json, vector_dimensions, embedding_provider, embedding_model, embedding_identity_json, embedding_identity_hash FROM {config.vector_table} ORDER BY chunk_id"
        ).fetchall()
    except sqlite3.OperationalError as exc:
        diagnostics["status"] = "partial_failure" if query_failures else "unavailable"
        diagnostics["failure_reason"] = f"vector identity columns unavailable: {exc}"
        return finish()
    diagnostics["stored_vector_row_count"] = len(rows)
    chunk_rows = {str(row["chunk_id"]): row for row in _load_chunk_rows(connection)}
    valid_rows: list[tuple[Any, dict[str, Any], list[float], dict[str, Any]]] = []
    observed_identity_keys: set[str] = set()
    for row in rows:
        chunk_id = str(row["chunk_id"])
        chunk_row = chunk_rows.get(chunk_id)
        if chunk_row is None:
            diagnostics["identity_mismatch_count"] += 1
            continue
        try:
            identity = json.loads(str(row["embedding_identity_json"]))
            vector = json.loads(str(row["vector_json"]))
        except (TypeError, ValueError, json.JSONDecodeError):
            diagnostics["invalid_json_count"] += 1
            continue
        if not isinstance(identity, dict) or not {"provider", "model", "dimensions", "normalize_embeddings", "encoding_strategy"}.issubset(identity):
            diagnostics["identity_mismatch_count"] += 1
            continue
        observed_identity_keys.add(json.dumps(identity, sort_keys=True, separators=(",", ":")))
        try:
            columns_match = (
                str(row["embedding_provider"]) == str(identity["provider"])
                and str(row["embedding_model"]) == str(identity["model"])
                and int(row["vector_dimensions"]) == int(identity["dimensions"])
            )
        except (TypeError, ValueError):
            columns_match = False
        if not columns_match:
            diagnostics["identity_mismatch_count"] += 1
            continue
        if str(row["embedding_identity_hash"] or "") != embedding_identity_hash(identity):
            diagnostics["identity_mismatch_count"] += 1
            continue
        if not isinstance(vector, list):
            diagnostics["invalid_numeric_count"] += 1
            continue
        if not vector:
            diagnostics["empty_vector_count"] += 1
            continue
        if not all(isinstance(value, (int, float)) for value in vector):
            diagnostics["invalid_numeric_count"] += 1
            continue
        if len(vector) != int(identity["dimensions"]) or len(vector) != int(row["vector_dimensions"]):
            diagnostics["dimension_mismatch_count"] += 1
            continue
        if math.sqrt(sum(float(value) * float(value) for value in vector)) == 0.0:
            diagnostics["zero_norm_count"] += 1
            continue
        diagnostics["compatible_vector_count"] += 1
        valid_rows.append((row, chunk_row, [float(value) for value in vector], identity))
    diagnostics["observed_identity_count"] = len(observed_identity_keys)
    diagnostics["mixed_identity_count"] = max(0, len(observed_identity_keys) - 1)

    per_query_lists: dict[str, list[dict[str, Any]]] = {}
    merged_by_id: dict[str, dict[str, Any]] = {}
    for query, query_vector, query_identity in query_vectors:
        query_result = next(result for result in diagnostics["query_results"] if result["query"] == query)
        query_candidates: list[dict[str, Any]] = []
        query_result["vector_rows_considered"] = len(rows)
        for row, chunk_row, vector, stored_identity in valid_rows:
            if eligible_chunk_ids is not None and str(row["chunk_id"]) not in eligible_chunk_ids:
                continue
            if not _chunk_matches_scope(chunk_row, scope_filters or {}):
                continue
            query_result["scope_admissible_rows"] += 1
            if any(stored_identity.get(field) != query_identity.get(field) for field in ("provider", "model", "dimensions", "normalize_embeddings", "encoding_strategy")):
                diagnostics["identity_mismatch_count"] += 1
                continue
            similarity = _cosine_similarity(query_vector, vector)
            if similarity < config.retrieval_vector_min_similarity:
                diagnostics["below_threshold_count"] += 1
                continue
            diagnostics["at_or_above_threshold_count"] += 1
            query_result["rows_at_or_above_threshold"] += 1
            query_candidates.append({"chunk_id": str(row["chunk_id"]), "query": query, "similarity": similarity, "chunk_row": chunk_row})
        query_candidates.sort(key=lambda item: (-float(item["similarity"]), str(item["chunk_id"])))
        query_result["candidates_before_per_query_cap"] = len(query_candidates)
        if len(query_candidates) > config.retrieval_vector_per_query_max_candidates:
            diagnostics["per_query_cap_attrition_count"] += len(query_candidates) - config.retrieval_vector_per_query_max_candidates
        query_candidates = query_candidates[: config.retrieval_vector_per_query_max_candidates]
        query_result["candidates_after_per_query_cap"] = len(query_candidates)
        per_query_lists[query] = query_candidates
        for item in query_candidates:
            candidate = merged_by_id.setdefault(item["chunk_id"], {"chunk_row": item["chunk_row"], "scores": {}})
            candidate["scores"][query] = float(item["similarity"])

    selected_ids: set[str] = set()
    selected_order: list[str] = []
    note_counts: dict[str, int] = {}
    query_indexes = {query: 0 for query in queries if query in per_query_lists}
    while len(selected_ids) < effective_limit and any(query_indexes[query] < len(per_query_lists[query]) for query in query_indexes):
        progressed = False
        for query in queries:
            if query not in query_indexes or query_indexes[query] >= len(per_query_lists[query]) or len(selected_ids) >= effective_limit:
                continue
            item = per_query_lists[query][query_indexes[query]]
            query_indexes[query] += 1
            progressed = True
            chunk_id = item["chunk_id"]
            if chunk_id in selected_ids:
                continue
            note_id = str(item["chunk_row"].get("note_id") or "")
            if note_counts.get(note_id, 0) >= config.retrieval_vector_max_chunks_per_note:
                diagnostics["note_cap_attrition_count"] += 1
                continue
            selected_ids.add(chunk_id)
            selected_order.append(chunk_id)
            note_counts[note_id] = note_counts.get(note_id, 0) + 1
        if not progressed:
            break

    for chunk_id in selected_order:
        merged = merged_by_id[chunk_id]
        scores = {query: merged["scores"][query] for query in sorted(merged["scores"])}
        best_query = sorted(scores, key=lambda query: (-scores[query], query))[0]
        best_similarity = scores[best_query]
        candidates.append(
            {
                **merged["chunk_row"],
                "selection_reason": f"vector similarity {best_similarity:.3f} from {best_query}",
                "score": best_similarity + float(config.retrieval_scoring["vector_bonus"]),
                "selection_source": "vector",
                "match_reason": f"vector query similarity {best_similarity:.3f}",
                "semantic_query_provenance": list(scores),
                "vector_query_scores": [{"query": query, "similarity": scores[query]} for query in scores],
                "vector_best_query": best_query,
                "vector_similarity": best_similarity,
            }
        )
    for result in diagnostics["query_results"]:
        result["candidates_represented_after_merged_allocation"] = sum(1 for candidate in candidates if result["query"] in candidate.get("semantic_query_provenance", []))
    if candidates:
        notes.append(f"vector search matched {len(candidates)} chunk(s) across {len(query_vectors)} semantic quer{'y' if len(query_vectors) == 1 else 'ies'}")
    else:
        notes.append("vector search produced no matches")
    if query_failures:
        notes.append(f"vector queries failed: {'; '.join(query_failures)}")
    degraded = any(diagnostics[key] for key in ("invalid_json_count", "invalid_numeric_count", "empty_vector_count", "zero_norm_count", "dimension_mismatch_count", "identity_mismatch_count", "mixed_identity_count")) and diagnostics["compatible_vector_count"] > 0
    diagnostics["status"] = "partial_failure" if query_failures or degraded else ("completed_with_candidates" if candidates else "completed_no_candidates")
    return finish()


def _graph_candidates(
    *,
    connection: sqlite3.Connection,
    config: RuntimeConfig,
    planner_retrieval_plan: dict[str, Any],
    graph_depth: dict[str, Any] | None = None,
    scope_filters: dict[str, Any] | None = None,
    limit: int | None = None,
    context_candidates: list[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], list[str], dict[str, Any]]:
    notes: list[str] = []
    if not config.graph_traversal_enabled:
        return [], ["graph traversal disabled"], {
            "status": "disabled",
            "enabled": False,
            "hop_limit": 0,
            "seed_sources": list(config.graph_traversal_seed_sources),
            "submitted_seeds": [],
            "matched_seed_count": 0,
            "matched_seed_note_ids": [],
            "expanded_note_count": 0,
            "selected_note_ids": [],
            "edge_types_used": [],
            "direction": config.graph_traversal_direction,
            "candidate_note_ids": [],
            "candidate_unique_note_ids": [],
            "traversal_hops": [],
        }

    try:
        node_rows = connection.execute(
            f"SELECT node_id, node_type, label, ref_id, metadata_json FROM {config.graph_nodes_table}"
        ).fetchall()
        edge_rows = connection.execute(
            f"SELECT source_node_id, target_node_id, edge_type, metadata_json FROM {config.graph_edges_table}"
        ).fetchall()
    except sqlite3.OperationalError:
        return [], ["graph search unavailable"], {
            "status": "unavailable",
            "enabled": config.graph_traversal_enabled,
            "hop_limit": int((graph_depth or {}).get("effective_depth", 0)),
            "seed_sources": list(config.graph_traversal_seed_sources),
            "submitted_seeds": [],
            "matched_seed_count": 0,
            "matched_seed_note_ids": [],
            "expanded_note_count": 0,
            "selected_note_ids": [],
            "edge_types_used": [],
            "direction": config.graph_traversal_direction,
            "candidate_note_ids": [],
            "candidate_unique_note_ids": [],
            "traversal_hops": [],
        }

    chunk_rows = {row["chunk_id"]: row for row in _load_chunk_rows(connection)}
    nodes_by_id = {str(row["node_id"]): dict(row) for row in node_rows}
    note_nodes = sorted(
        [dict(row) for row in node_rows if str(row["node_type"]) in set(config.graph_traversal_node_type_allowlist)],
        key=lambda row: str(row.get("node_id") or ""),
    )
    edge_type_allowlist = set(config.graph_traversal_edge_type_allowlist)
    node_type_allowlist = set(config.graph_traversal_node_type_allowlist)
    seed_values = _graph_seed_values(
        planner_retrieval_plan=planner_retrieval_plan,
        config=config,
    )
    # Preserve canonical note identity directly. Known notes must not be
    # serialized to title/path text and fuzzy-rematched.
    grounded_context_candidates = [
        candidate for candidate in (context_candidates or [])
        if bool(((candidate.get("grounding") or {}).get("graph_authority") or {}).get("may_seed_subject_graph"))
    ]
    canonical_seed_note_ids = list(dict.fromkeys(
        seed_note_id
        for candidate in grounded_context_candidates
        for seed_note_id in [*candidate.get("identity_seed_note_ids", []), *(
            [str(candidate.get("note_id") or "").strip()]
            if not candidate.get("identity_seed_note_ids") else []
        )]
        if str(seed_note_id).strip()
    ))
    if not seed_values and not canonical_seed_note_ids:
        return [], ["graph search skipped: no graph seeds"], {
            "status": "skipped_no_input",
            "enabled": config.graph_traversal_enabled,
            "hop_limit": int((graph_depth or {}).get("effective_depth", 0)),
            "seed_sources": list(config.graph_traversal_seed_sources),
            "submitted_seeds": [],
            "matched_seed_count": 0,
            "matched_seed_note_ids": [],
            "seed_note_ids": [],
            "hydrated_seed_note_count": 0,
            "expanded_note_count": 0,
            "traversed_note_count": 0,
            "selected_note_ids": [],
            "edge_types_used": [],
            "direction": config.graph_traversal_direction,
            "candidate_note_ids": [],
            "candidate_unique_note_ids": [],
            "traversal_hops": [],
        }

    submitted_seeds: list[dict[str, str]] = []
    for source_name, seed in seed_values:
        item = {"source": str(source_name), "value": str(seed)}
        if item not in submitted_seeds:
            submitted_seeds.append(item)

    outgoing: dict[str, list[tuple[str, str, str, dict[str, Any]]]] = {}
    incoming: dict[str, list[tuple[str, str, str, dict[str, Any]]]] = {}
    for row in edge_rows:
        source_node_id = str(row["source_node_id"])
        target_node_id = str(row["target_node_id"])
        edge_type = str(row["edge_type"])
        try:
            metadata = json.loads(str(row["metadata_json"] or "{}"))
        except (TypeError, json.JSONDecodeError):
            metadata = {}
        outgoing.setdefault(source_node_id, []).append((target_node_id, edge_type, "outbound", metadata))
        incoming.setdefault(target_node_id, []).append((source_node_id, edge_type, "inbound", metadata))

    selected_note_ids: list[str] = []
    note_reasons: dict[str, list[str]] = {}
    note_hops: dict[str, list[dict[str, Any]]] = {}
    matched_seed_note_ids: list[str] = []
    matched_seed_count = 0
    expanded_note_count = 0
    hydrated_seed_note_count = 0
    context_subjects_by_note: dict[str, list[str]] = {}
    authorized_subjects_by_note: dict[str, list[str]] = {}
    for candidate in grounded_context_candidates:
        note_id = str(candidate.get("note_id") or "")
        authorized = _coerce_string_list(candidate.get("authorized_subjects"))
        authorized_subjects_by_note[note_id] = list(dict.fromkeys([
            *authorized_subjects_by_note.get(note_id, []), *authorized,
        ]))
        context_subjects_by_note[note_id] = list(dict.fromkeys([
            *context_subjects_by_note.get(note_id, []), *authorized,
        ]))
        for promotion in candidate.get("wikilink_identity_promotions", []):
            if not isinstance(promotion, dict):
                continue
            target_note_id = str(promotion.get("target_note_id") or "")
            subject_id = str(promotion.get("subject_id") or "")
            if target_note_id and subject_id:
                authorized_subjects_by_note[target_note_id] = list(dict.fromkeys([
                    *authorized_subjects_by_note.get(target_note_id, []), subject_id,
                ]))
                context_subjects_by_note[target_note_id] = list(dict.fromkeys([
                    *context_subjects_by_note.get(target_note_id, []), subject_id,
                ]))
    edge_types_used: list[str] = []
    queue: list[tuple[str, int]] = []
    visited_notes: set[str] = set()

    for note_id in canonical_seed_note_ids:
        visited_notes.add(note_id)
        selected_note_ids.append(note_id)
        matched_seed_note_ids.append(note_id)
        hydrated_seed_note_count += 1
        queue.append((note_id, 0))

    for source_name, seed in seed_values:
        matched_nodes = _graph_match_note_nodes(
            seed=seed,
            node_rows=note_nodes,
            node_type_allowlist=node_type_allowlist,
            match_mode=config.graph_traversal_match_mode,
            min_token_overlap=config.graph_traversal_min_token_overlap,
            config=config,
        )
        if not matched_nodes:
            continue
        matched_seed_count += 1
        for node_row in matched_nodes:
            note_id = str(node_row.get("ref_id") or "")
            if not note_id:
                continue
            referents = _coerce_string_list(planner_retrieval_plan.get("resolved_referents"))
            normalized_seed = _normalize_text(seed)
            explicit_subjects = [
                referent for referent in referents
                if _normalize_text(referent) == normalized_seed
            ]
            if not explicit_subjects and len(referents) == 1 and source_name == "graph_seeds":
                explicit_subjects = list(referents)
            authorized_subjects_by_note[note_id] = list(dict.fromkeys([
                *authorized_subjects_by_note.get(note_id, []), *explicit_subjects,
            ]))
            context_subjects_by_note[note_id] = list(dict.fromkeys([
                *context_subjects_by_note.get(note_id, []), *explicit_subjects,
            ]))
            node_label = str(node_row.get("label") or note_id)
            note_reasons.setdefault(note_id, []).append(f"{source_name} graph seed matched note: {node_label}")
            if note_id not in matched_seed_note_ids:
                matched_seed_note_ids.append(note_id)
            if note_id not in visited_notes:
                visited_notes.add(note_id)
                selected_note_ids.append(note_id)
                queue.append((note_id, 0))

    while queue:
        current_note_id, hop = queue.pop(0)
        effective_depth = int((graph_depth or {}).get("effective_depth", 0))
        if hop >= effective_depth:
            continue
        # Avoid relying on helper placement below; note node ids are canonical.
        current_node_id = f"note::{current_note_id}"
        current_node = nodes_by_id.get(current_node_id)
        if current_node is None:
            continue
        current_label = str(current_node.get("label") or current_note_id)
        traversals: list[tuple[str, str, str, dict[str, Any]]] = []
        if config.graph_traversal_direction in {"outbound", "both"}:
            traversals.extend(sorted(outgoing.get(current_node_id, []), key=lambda item: (item[1], item[0])))
        if config.graph_traversal_direction in {"inbound", "both"}:
            traversals.extend(sorted(incoming.get(current_node_id, []), key=lambda item: (item[1], item[0])))
        for target_node_id, edge_type, edge_direction, edge_metadata in traversals:
            if edge_type not in edge_type_allowlist:
                continue
            if edge_type not in edge_types_used:
                edge_types_used.append(edge_type)
            target_node = nodes_by_id.get(target_node_id)
            if target_node is None:
                continue
            if str(target_node.get("node_type") or "") not in node_type_allowlist:
                continue
            target_note_id = str(target_node.get("ref_id") or "")
            if not target_note_id:
                continue
            propagated_subjects = authorized_subjects_by_note.get(current_note_id, [])
            authorized_subjects_by_note[target_note_id] = list(dict.fromkeys([
                *authorized_subjects_by_note.get(target_note_id, []), *propagated_subjects,
            ]))
            context_subjects_by_note[target_note_id] = list(dict.fromkeys([
                *context_subjects_by_note.get(target_note_id, []), *propagated_subjects,
            ]))
            target_label = str(target_node.get("label") or target_note_id)
            note_reasons.setdefault(target_note_id, []).append(f"wikilink hop {hop + 1} ({edge_direction}): {current_label} -> {target_label}")
            edge_source_note_id = str(nodes_by_id.get(
                current_node_id if edge_direction == "outbound" else target_node_id,
                {},
            ).get("ref_id") or "")
            edge_target_note_id = str(nodes_by_id.get(
                target_node_id if edge_direction == "outbound" else current_node_id,
                {},
            ).get("ref_id") or "")
            hop_evidence = {
                "hop": hop + 1,
                "traversal_direction": edge_direction,
                "edge_type": edge_type,
                "edge_source_note_id": edge_source_note_id,
                "edge_target_note_id": edge_target_note_id,
                "from_note_id": current_note_id,
                "to_note_id": target_note_id,
                "source_grounding_role": "object_identity" if authorized_subjects_by_note.get(current_note_id) else "evidence_unit",
                "subject_propagation_authorized": bool(propagated_subjects),
                "propagated_subjects": list(propagated_subjects),
                "edge_provenance": edge_metadata,
            }
            target_hops = note_hops.setdefault(target_note_id, [])
            if hop_evidence not in target_hops and len(target_hops) < 8:
                target_hops.append(hop_evidence)
            if target_note_id not in visited_notes:
                visited_notes.add(target_note_id)
                selected_note_ids.append(target_note_id)
                queue.append((target_note_id, hop + 1))
                expanded_note_count += 1

    max_graph_candidates = limit if limit is not None else config.graph_traversal_max_candidates
    eligible_rows_by_note: list[list[dict[str, Any]]] = []
    for note_id in selected_note_ids:
        note_chunk_rows = sorted(
            [chunk_row for chunk_row in chunk_rows.values() if str(chunk_row["note_id"]) == note_id and _chunk_matches_scope(chunk_row, scope_filters or {})],
            key=lambda row: str(row.get("chunk_id") or ""),
        )
        if not note_chunk_rows:
            continue
        preferred_rows = [
            row
            for row in note_chunk_rows
            if not (
                config.chunking_low_signal_apparatus_skip_during_graph_representatives
                and is_low_signal_apparatus_text(
                    str(row.get("paragraph_text") or ""),
                    config=config,
                    section_label=str(row.get("section_label") or ""),
                    note_title=str(row.get("note_title") or ""),
                    relative_path=str(row.get("relative_path") or ""),
                )
            )
        ]
        eligible_rows_by_note.append(preferred_rows or note_chunk_rows)

    # Materialize one representative per traversed note per round. This keeps
    # a large seed note from consuming the graph pool before neighbors appear.
    selected_chunk_ids: list[str] = []
    round_index = 0
    while len(selected_chunk_ids) < max_graph_candidates:
        added_this_round = False
        for candidate_rows in eligible_rows_by_note:
            if round_index >= len(candidate_rows):
                continue
            chunk_id = str(candidate_rows[round_index]["chunk_id"])
            if chunk_id in selected_chunk_ids:
                continue
            selected_chunk_ids.append(chunk_id)
            added_this_round = True
            if len(selected_chunk_ids) >= max_graph_candidates:
                break
        if not added_this_round:
            break
        round_index += 1

    candidates = []
    for chunk_id in selected_chunk_ids:
        chunk_row = chunk_rows.get(chunk_id)
        if chunk_row is None:
            continue
        reason = "; ".join(dict.fromkeys(note_reasons.get(str(chunk_row["note_id"]), ["graph traversal selected note"])))
        candidates.append(
            {
                **chunk_row,
                "selection_reason": reason,
                "score": float(config.retrieval_scoring["graph_bonus"]),
                "selection_source": "graph",
                "match_reason": "graph expansion",
                "graph_direction": config.graph_traversal_direction,
                "graph_provenance": note_reasons.get(str(chunk_row["note_id"]), []),
                "graph_hop_provenance": note_hops.get(str(chunk_row["note_id"]), []),
                "context_subjects": context_subjects_by_note.get(str(chunk_row["note_id"]), []),
                "authorized_subjects": authorized_subjects_by_note.get(str(chunk_row["note_id"]), []),
                "context_grounded": bool(context_subjects_by_note.get(str(chunk_row["note_id"]))),
            }
        )
    if candidates:
        notes.append(f"graph search matched {len(candidates)} chunk(s)")
    else:
        notes.append("graph search produced no matches")
    notes.append(
        f"graph traversal enabled={config.graph_traversal_enabled}, effective_depth={int((graph_depth or {}).get('effective_depth', 0))}, matched_seed_count={matched_seed_count}, expanded_note_count={expanded_note_count}"
    )
    return candidates, notes, {
        "status": "completed_with_candidates" if candidates else "completed_no_candidates",
        "enabled": config.graph_traversal_enabled,
        "hop_limit": int((graph_depth or {}).get("effective_depth", 0)),
        **(graph_depth or {}),
        "seed_sources": list(config.graph_traversal_seed_sources),
        "submitted_seeds": submitted_seeds,
        "matched_seed_count": matched_seed_count,
        "matched_seed_note_ids": matched_seed_note_ids,
        "seed_note_ids": canonical_seed_note_ids,
        "hydrated_seed_note_count": hydrated_seed_note_count,
        "expanded_note_count": expanded_note_count,
        "traversed_note_count": expanded_note_count,
        "selected_note_ids": list(selected_note_ids),
        "edge_types_used": edge_types_used,
        "direction": config.graph_traversal_direction,
        "candidate_note_ids": [str(candidate.get("note_id") or "") for candidate in candidates],
        "candidate_unique_note_ids": list(dict.fromkeys(str(candidate.get("note_id") or "") for candidate in candidates)),
        "traversal_hops": [hop for note_id in selected_note_ids for hop in note_hops.get(note_id, [])],
    }


def _is_template_or_schema_query(semantic_compiler_packet: dict[str, Any]) -> bool:
    values: list[str] = []
    for key in ("raw_user_input", "intent", "query"):
        value = semantic_compiler_packet.get(key)
        if isinstance(value, str):
            values.append(value)
    for key in ("entities", "relations", "resolved_referents", "graph_seeds", "concepts", "literal_terms", "semantic_queries", "lexical_queries"):
        values.extend(_coerce_string_list(semantic_compiler_packet.get(key)))
    haystack = _normalize_text(" ".join(values))
    template_terms = {
        "applicability",
        "boilerplate",
        "frontmatter",
        "schema",
        "template",
        "templates",
        "vault design",
        "yaml",
    }
    return any(term in haystack for term in template_terms)


def _is_template_boilerplate_candidate(candidate: dict[str, Any]) -> bool:
    relative_path = str(candidate.get("relative_path") or "").lower()
    note_title = str(candidate.get("note_title") or "").lower()
    paragraph_text = str(candidate.get("paragraph_text") or "").strip().lower()
    section_label = str(candidate.get("section_label") or "").lower()
    template_surface = "template" in relative_path or "template" in note_title or "vault design" in relative_path
    if not template_surface:
        return False
    placeholder_surfaces = (
        "[[...]]",
        "bridge_",
        "[]",
        "scope:",
        "speculation_quarantine",
        "revision_triggers",
        "stop_rule",
        "applicability",
        "preserves",
        "breaks",
        "conditions",
        "method",
    )
    if any(surface in paragraph_text for surface in placeholder_surfaces):
        return True
    return "template" in section_label


def _apply_retrieval_candidate_hygiene(
    *,
    candidates: list[dict[str, Any]],
    semantic_compiler_packet: dict[str, Any],
    config: RuntimeConfig,
) -> list[dict[str, Any]]:
    if _is_template_or_schema_query(semantic_compiler_packet):
        return candidates
    adjusted: list[dict[str, Any]] = []
    for candidate in candidates:
        next_candidate = dict(candidate)
        if _is_template_boilerplate_candidate(next_candidate):
            next_candidate["score"] = float(next_candidate.get("score") or 0.0) - float(config.retrieval_scoring["demotion_penalty"])
            next_candidate["_retrieval_demoted"] = True
            next_candidate["selection_reason"] = "; ".join(
                part
                for part in [
                    str(next_candidate.get("selection_reason") or "").strip(),
                    "template boilerplate demoted for non-template query",
                ]
                if part
            )
        adjusted.append(next_candidate)
    return adjusted


def _temporal_anchor_order_key(anchor: dict[str, Any]) -> tuple[str, str, str]:
    """Return a stable interval key without comparing raw cross-surface scores."""
    return (
        str(anchor.get("canonical_start") or ""),
        str(anchor.get("canonical_end") or ""),
        str(anchor.get("anchor_id") or ""),
    )


def _choose_temporal_governing_anchor(
    anchors: list[dict[str, Any]],
    *,
    mode: str,
    direction: str,
) -> dict[str, Any]:
    """Choose the mode-specific anchor while retaining the complete set."""
    if not anchors:
        raise ValueError("temporal governing anchor requires at least one anchor")
    if mode == "latest" or (mode == "ordered" and direction == "descending"):
        return max(anchors, key=_temporal_anchor_order_key)
    if mode == "before":
        return max(
            anchors,
            key=lambda anchor: (
                str(anchor.get("canonical_end") or anchor.get("canonical_start") or ""),
                str(anchor.get("canonical_start") or ""),
                str(anchor.get("anchor_id") or ""),
            ),
        )
    if mode == "after":
        return min(
            anchors,
            key=lambda anchor: (
                str(anchor.get("canonical_start") or anchor.get("canonical_end") or ""),
                str(anchor.get("canonical_end") or ""),
                str(anchor.get("anchor_id") or ""),
            ),
        )
    # earliest, ordered ascending, and between use the earliest qualifying
    # interval as their governing temporal position.
    return min(anchors, key=_temporal_anchor_order_key)


def _temporal_candidate_position(candidate: dict[str, Any], field: str) -> str:
    return str(candidate.get(field) or "")


def _order_temporal_candidates(
    candidates: list[dict[str, Any]],
    *,
    mode: str,
    direction: str,
) -> list[dict[str, Any]]:
    """Apply the accepted temporal tuple with relevance in the right place."""
    ordered = list(candidates)
    # Stable passes make the tuple explicit while keeping identity as the last
    # tie-breaker. Relation modes intentionally apply relevance last, making it
    # primary after relation admission; date is only their secondary tie-break.
    ordered.sort(key=lambda candidate: str(candidate.get("chunk_id") or ""))
    if mode in {"earliest", "latest", "ordered"}:
        descending = mode == "latest" or (mode == "ordered" and direction == "descending")
        ordered.sort(
            key=lambda candidate: -float(candidate.get("internal_ordinal_relevance") or 0.0)
        )
        ordered.sort(
            key=lambda candidate: _temporal_candidate_position(
                candidate, "temporal_governing_canonical_start"
            ),
            reverse=descending,
        )
        return ordered

    if mode == "before":
        ordered.sort(
            key=lambda candidate: (
                _temporal_candidate_position(candidate, "temporal_governing_canonical_end")
                or _temporal_candidate_position(candidate, "temporal_governing_canonical_start"),
            ),
            reverse=True,
        )
    elif mode == "after":
        ordered.sort(
            key=lambda candidate: (
                _temporal_candidate_position(candidate, "temporal_governing_canonical_start")
                or _temporal_candidate_position(candidate, "temporal_governing_canonical_end"),
            )
        )
    elif mode == "between":
        ordered.sort(
            key=lambda candidate: (
                _temporal_candidate_position(candidate, "temporal_governing_canonical_start"),
                _temporal_candidate_position(candidate, "temporal_governing_canonical_end"),
            )
        )
    ordered.sort(key=lambda candidate: -float(candidate.get("internal_ordinal_relevance") or 0.0))
    return ordered


def _temporal_candidates_global_legacy(
    *,
    connection: sqlite3.Connection,
    config: RuntimeConfig,
    chunk_rows: list[dict[str, Any]],
    layer: dict[str, Any],
    lexical_queries: list[str],
    semantic_queries: list[str],
    literal_terms: list[dict[str, Any]],
    scope_filters: dict[str, Any],
    embedding_backend: EmbeddingBackend,
) -> tuple[list[dict[str, Any]], list[str], dict[str, Any]]:
    mode = str(layer.get("mode") or config.retrieval_temporal_default_mode)
    diagnostics: dict[str, Any] = {
        "operator": "temporal_retrieve",
        "status": "not_requested",
        "mode": mode,
        "candidate_count": 0,
        "matched_anchor_count": 0,
        "matched_note_count": 0,
        "internal_surface_counts": {"lexical": 0, "vector": 0},
        "searches_full_temporal_projection": True,
        "relation_certainty_counts": {},
        "anchor_types": list(layer.get("anchor_types") or config.retrieval_temporal_default_anchor_types),
        "authorities": list(layer.get("authorities") or config.retrieval_temporal_allowed_authorities),
        "include_unresolved": bool(layer.get("include_unresolved", config.retrieval_temporal_include_conflicted_by_default)),
    }
    if not config.retrieval_temporal_enabled:
        diagnostics.update({"status": "disabled", "failure_reason": "temporal retrieval disabled by runtime YAML"})
        return [], ["temporal retrieval disabled"], diagnostics
    if mode not in config.retrieval_temporal_allowed_modes:
        diagnostics.update({"status": "unsupported", "failure_reason": "temporal mode is not YAML-allowed"})
        return [], ["temporal retrieval mode unsupported"], diagnostics
    try:
        boundary_start = parse_temporal_value(layer["before"])[0] if layer.get("before") is not None else parse_temporal_value(layer["start"])[0] if layer.get("start") is not None else None
        boundary_end = parse_temporal_value(layer["after"])[1] if layer.get("after") is not None else parse_temporal_value(layer["end"])[1] if layer.get("end") is not None else None
        if mode == "between" and (boundary_start is None or boundary_end is None):
            raise ValueError("between requires start and end")
        if boundary_start and boundary_end and boundary_start > boundary_end:
            raise ValueError("temporal boundaries are contradictory")
    except (TypeError, ValueError) as exc:
        diagnostics.update({"status": "failed", "failure_reason": str(exc)})
        return [], [f"temporal boundary failure: {exc}"], diagnostics

    anchor_rows = connection.execute(
        "SELECT anchor_id, note_id, chunk_id, anchor_type, canonical_start, canonical_end, precision, source_field, original_source_value, authority, parsing_status, conflict_group, unresolved, diagnostic_reason FROM temporal_anchors ORDER BY canonical_start, anchor_id"
    ).fetchall()
    allowed_types = set(diagnostics["anchor_types"])
    allowed_authorities = set(diagnostics["authorities"])
    eligible_anchors: list[dict[str, Any]] = []
    for row in anchor_rows:
        anchor = dict(row)
        if anchor["anchor_type"] not in allowed_types or anchor["authority"] not in allowed_authorities:
            continue
        relation = relation_for_anchor(anchor, mode=mode, boundary_start=boundary_start, boundary_end=boundary_end, include_unresolved=diagnostics["include_unresolved"])
        if relation is None:
            continue
        anchor["relation_certainty"] = relation
        eligible_anchors.append(anchor)
    diagnostics["matched_anchor_count"] = len(eligible_anchors)
    if not eligible_anchors:
        diagnostics.update({"status": "completed_no_candidates", "relation_certainty_counts": {}})
        return [], ["temporal retrieval found no qualifying anchors"], diagnostics

    combined_lexical_queries = list(dict.fromkeys([*lexical_queries, *[str(item.get("term")) for item in literal_terms if item.get("term")]]))
    lexical, lexical_notes, lexical_diag = _lexical_candidates(
        chunk_rows, combined_lexical_queries, connection=connection, scope_filters=scope_filters,
        limit=None, config=config, mode=config.retrieval_lexical_default_mode, return_diagnostics=True,
    )
    eligible_note_ids = {str(anchor["note_id"]) for anchor in eligible_anchors}
    eligible_chunk_ids = {str(row["chunk_id"]) for row in chunk_rows if str(row.get("note_id")) in eligible_note_ids and _chunk_matches_scope(row, scope_filters)}
    lexical = [candidate for candidate in lexical if str(candidate.get("chunk_id")) in eligible_chunk_ids]
    vector, vector_notes, vector_diag = _vector_candidates(
        connection=connection, config=config, embedding_backend=embedding_backend,
        vector_query=semantic_queries[0] if semantic_queries else "", vector_queries=semantic_queries,
        scope_filters=scope_filters, limit=config.retrieval_temporal_max_candidates,
        eligible_chunk_ids=eligible_chunk_ids, return_diagnostics=True,
    )
    lexical_by_id = {str(candidate["chunk_id"]): (index, candidate) for index, candidate in enumerate(lexical, start=1)}
    vector_by_id = {str(candidate["chunk_id"]): (index, candidate) for index, candidate in enumerate(vector, start=1)}
    diagnostics["internal_surface_counts"] = {"lexical": len(lexical), "vector": len(vector)}
    rows_by_id = {str(row["chunk_id"]): row for row in chunk_rows}
    anchor_by_note: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for anchor in eligible_anchors:
        anchor_by_note[str(anchor["note_id"])].append(anchor)
    temporal: list[dict[str, Any]] = []
    for chunk_id in sorted(set(lexical_by_id) | set(vector_by_id)):
        row = rows_by_id.get(chunk_id)
        if row is None:
            continue
        anchors = anchor_by_note.get(str(row.get("note_id")), [])
        if not anchors:
            continue
        direction = str(layer.get("direction") or "ascending")
        anchor = _choose_temporal_governing_anchor(anchors, mode=mode, direction=direction)
        lexical_rank = lexical_by_id.get(chunk_id, (None, {}))[0]
        vector_rank = vector_by_id.get(chunk_id, (None, {}))[0]
        ranks = [rank for rank in (lexical_rank, vector_rank) if rank is not None]
        ordinal = sum(1.0 / rank for rank in ranks) if ranks else 0.0
        provenance = [{"anchor_id": item["anchor_id"], "anchor_type": item["anchor_type"], "canonical_start": item["canonical_start"], "canonical_end": item["canonical_end"], "precision": item["precision"], "authority": item["authority"], "source_field": item["source_field"], "relation": mode, "relation_certainty": item["relation_certainty"]} for item in anchors]
        ordered_anchors = sorted(anchors, key=_temporal_anchor_order_key)
        temporal.append({
            **row,
            "selection_source": "temporal",
            "source_layers": ["temporal"],
            "score": ordinal,
            "internal_lexical_rank": lexical_rank,
            "internal_vector_rank": vector_rank,
            "internal_ordinal_relevance": ordinal,
            "temporal_anchor_ids": [item["anchor_id"] for item in ordered_anchors],
            "temporal_provenance": [
                item for item in sorted(provenance, key=lambda value: (str(value.get("canonical_start") or ""), str(value.get("canonical_end") or ""), str(value.get("anchor_id") or "")))
            ],
            "temporal_governing_anchor_id": anchor["anchor_id"],
            "temporal_governing_anchor_type": anchor["anchor_type"],
            "temporal_governing_canonical_start": anchor["canonical_start"],
            "temporal_governing_canonical_end": anchor["canonical_end"],
            "temporal_mode": mode,
            "relation_status": anchor["relation_certainty"],
            "selection_reason": f"temporal {mode} relevance admission",
            "match_reason": f"temporal {mode} relation with ordinal relevance",
        })
    temporal = _order_temporal_candidates(
        temporal,
        mode=mode,
        direction=str(layer.get("direction") or "ascending"),
    )
    temporal = temporal[: min(config.retrieval_temporal_max_candidates, max(0, int(layer.get("effective_limit", layer.get("limit", config.retrieval_temporal_default_limit)))))]
    diagnostics.update({"status": "completed_with_candidates" if temporal else "completed_no_candidates", "candidate_count": len(temporal), "matched_note_count": len({str(item["note_id"]) for item in temporal}), "relation_certainty_counts": dict(sorted(Counter(str(item.get("relation_status")) for item in temporal).items()))})
    return temporal, [*lexical_notes, *vector_notes], diagnostics


def _temporal_candidates_from_closure(
    *, connection: sqlite3.Connection, config: RuntimeConfig, chunk_rows: list[dict[str, Any]],
    layer: dict[str, Any], closure_candidates: list[dict[str, Any]], resolved_referents: list[str],
) -> tuple[list[dict[str, Any]], list[str], dict[str, Any]]:
    """Evaluate chronology over already-grounded closure candidates."""
    mode = str(layer.get("mode") or config.retrieval_temporal_default_mode)
    subjects = list(dict.fromkeys(str(value).strip() for value in resolved_referents if str(value).strip()))
    anchor_rows = connection.execute(
        "SELECT anchor_id, note_id, chunk_id, anchor_type, canonical_start, canonical_end, precision, source_field, original_source_value, authority, parsing_status, conflict_group, unresolved, diagnostic_reason FROM temporal_anchors ORDER BY canonical_start, anchor_id"
    ).fetchall()
    try:
        boundary_start = parse_temporal_value(layer["before"])[0] if layer.get("before") is not None else parse_temporal_value(layer["start"])[0] if layer.get("start") is not None else None
        boundary_end = parse_temporal_value(layer["after"])[1] if layer.get("after") is not None else parse_temporal_value(layer["end"])[1] if layer.get("end") is not None else None
    except (TypeError, ValueError) as exc:
        return [], [f"temporal boundary failure: {exc}"], {"operator": "temporal_retrieve", "status": "failed", "candidate_count": 0, "failure_reason": str(exc)}
    allowed_types = set(layer.get("anchor_types") or config.retrieval_temporal_default_anchor_types)
    allowed_authorities = set(layer.get("authorities") or config.retrieval_temporal_allowed_authorities)
    anchors_by_note: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in anchor_rows:
        anchor = dict(row)
        if anchor["anchor_type"] not in allowed_types or anchor["authority"] not in allowed_authorities:
            continue
        relation = relation_for_anchor(anchor, mode=mode, boundary_start=boundary_start, boundary_end=boundary_end, include_unresolved=bool(layer.get("include_unresolved", config.retrieval_temporal_include_conflicted_by_default)))
        if relation is not None:
            anchor["relation_certainty"] = relation
            anchors_by_note[str(anchor["note_id"])].append(anchor)
    rows_by_id = {str(row["chunk_id"]): row for row in chunk_rows}
    by_subject: list[list[dict[str, Any]]] = []
    diagnostics: dict[str, Any] = {
        "operator": "temporal_retrieve", "status": "not_requested", "mode": mode,
        "searches_full_temporal_projection": False, "context_source": "semantic_closure",
        "subject_count": len(subjects), "query_context_count": 0 if subjects else 1,
        "context_mode": "named_subjects" if subjects else "query", "per_subject": [],
        "temporal_candidates_before_proposition_filter": 0,
        "temporal_candidates_after_proposition_filter": 0,
        "rejection_reason_counts": {},
    }
    for subject in subjects or [""]:
        relevant: list[dict[str, Any]] = []
        for candidate in closure_candidates:
            chunk_id = str(candidate.get("chunk_id") or "")
            row = rows_by_id.get(chunk_id)
            if row is None or str(row.get("note_id") or "") not in anchors_by_note:
                continue
            diagnostics["temporal_candidates_before_proposition_filter"] += 1
            grounding = candidate.get("grounding") if isinstance(candidate.get("grounding"), dict) else {}
            proposition = grounding.get("relation_proposition") if isinstance(grounding.get("relation_proposition"), dict) else {}
            proposition_subjects_for_candidate = _coerce_string_list(proposition.get("subject_referents")) or _coerce_string_list(proposition.get("subjects"))
            if not bool(proposition.get("eligible")):
                for reason in _coerce_string_list(grounding.get("rejection_reasons")) or ["not_proposition_grounded"]:
                    diagnostics["rejection_reason_counts"][reason] = diagnostics["rejection_reason_counts"].get(reason, 0) + 1
                continue
            candidate_subjects = _coerce_string_list(candidate.get("context_subjects"))
            if subjects:
                if subject not in proposition_subjects_for_candidate or subject not in candidate_subjects:
                    continue
            elif not candidate.get("context_grounded"):
                continue
            relevant.append(candidate)
            diagnostics["temporal_candidates_after_proposition_filter"] += 1
        # Preserve convergence while preventing one route from multiplying
        # the same semantic unit in the selected packet.
        unique: dict[str, dict[str, Any]] = {}
        for candidate in relevant:
            unique.setdefault(str(candidate.get("chunk_id") or ""), candidate)
        temporal: list[dict[str, Any]] = []
        for chunk_id, candidate in sorted(unique.items()):
            row = rows_by_id[chunk_id]
            anchors = anchors_by_note[str(row["note_id"])]
            governing = _choose_temporal_governing_anchor(anchors, mode=mode, direction=str(layer.get("direction") or "ascending"))
            temporal.append({
                **row, **candidate, "selection_source": "temporal", "source_layers": [*candidate.get("source_layers", []), "temporal"],
                "temporal_subject": subject, "temporal_subjects": [subject] if subject else [],
                "temporal_mode": mode, "temporal_governing_anchor_id": governing["anchor_id"],
                "temporal_governing_anchor_type": governing["anchor_type"],
                "temporal_governing_canonical_start": governing["canonical_start"],
                "temporal_governing_canonical_end": governing["canonical_end"],
                "temporal_anchor_ids": [item["anchor_id"] for item in sorted(anchors, key=_temporal_anchor_order_key)],
                "temporal_provenance": [{"anchor_id": item["anchor_id"], "canonical_start": item["canonical_start"], "canonical_end": item["canonical_end"], "authority": item["authority"], "subject": subject or None, "relation": mode, "relation_certainty": item["relation_certainty"]} for item in sorted(anchors, key=_temporal_anchor_order_key)],
                "relation_status": governing["relation_certainty"],
                "selection_reason": f"temporal {mode} evaluation over contextual closure",
                "match_reason": f"temporal {mode} relation over grounded semantic unit",
            })
        ordered = _order_temporal_candidates(temporal, mode=mode, direction=str(layer.get("direction") or "ascending"))
        by_subject.append(ordered)
        diagnostics["per_subject"].append({"subject": subject or None, "context_type": "subject" if subject else "query", "closure_candidate_count": len(unique), "temporal_candidate_count": len(ordered), "governing_anchor_count": len(ordered)})
    requested_limit = max(0, int(layer.get("effective_limit", layer.get("limit", config.retrieval_temporal_default_limit))))
    effective_limit = min(config.retrieval_temporal_max_candidates, max(requested_limit, len(subjects)))
    temporal: list[dict[str, Any]] = []
    for index in range(max((len(items) for items in by_subject), default=0)):
        for items in by_subject:
            if index < len(items) and len(temporal) < effective_limit:
                temporal.append(items[index])
    satisfied = {str(item.get("temporal_subject") or "") for item in temporal if str(item.get("temporal_subject") or "") in subjects}
    diagnostics.update({"status": "completed_with_candidates" if temporal else "completed_no_candidates", "candidate_count": len(temporal), "effective_limit": effective_limit, "requested_limit": requested_limit, "limit_adjustment": "raised_to_subject_count" if effective_limit != requested_limit else "none", "satisfied_subject_count": len(satisfied), "missing_subject_count": len(set(subjects) - satisfied), "one_per_subject_reservation_satisfied": bool(subjects) and all(subject in satisfied for subject in subjects)})
    return temporal, ["temporal relation evaluated over contextual semantic closure"], diagnostics


def _temporal_candidates(
    *, connection: sqlite3.Connection, config: RuntimeConfig, chunk_rows: list[dict[str, Any]],
    layer: dict[str, Any], lexical_queries: list[str], semantic_queries: list[str],
    literal_terms: list[dict[str, Any]], scope_filters: dict[str, Any],
    embedding_backend: EmbeddingBackend, resolved_referents: list[str] | None = None,
    closure_candidates: list[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], list[str], dict[str, Any]]:
    """Admit relevance separately for each existing subject, then apply chronology."""
    named_subjects = list(dict.fromkeys(str(value).strip() for value in (resolved_referents or []) if str(value).strip()))
    if closure_candidates is not None:
        return _temporal_candidates_from_closure(
            connection=connection, config=config, chunk_rows=chunk_rows, layer=layer,
            closure_candidates=closure_candidates, resolved_referents=named_subjects,
        )
    # An empty referent list is a genuine query-level context. Keep it
    # distinct from named-subject coverage so an anonymous execution cannot
    # satisfy a semantic subject reservation.
    subjects = named_subjects or [""]
    # The legacy implementation performs the unchanged anchor parsing and
    # relation checks.  Its candidate construction is reused per subject so
    # chronology can never select a global early note before relevance.
    anchor_rows = connection.execute(
        "SELECT anchor_id, note_id, chunk_id, anchor_type, canonical_start, canonical_end, precision, source_field, original_source_value, authority, parsing_status, conflict_group, unresolved, diagnostic_reason FROM temporal_anchors ORDER BY canonical_start, anchor_id"
    ).fetchall()
    mode = str(layer.get("mode") or config.retrieval_temporal_default_mode)
    diagnostics: dict[str, Any] = {"operator": "temporal_retrieve", "status": "not_requested", "mode": mode, "searches_full_temporal_projection": True, "subject_count": len(named_subjects), "query_context_count": 1 if not named_subjects else 0, "context_mode": "named_subjects" if named_subjects else "query", "per_subject": []}
    if not config.retrieval_temporal_enabled or mode not in config.retrieval_temporal_allowed_modes:
        diagnostics.update({"status": "unsupported", "candidate_count": 0})
        return [], ["temporal retrieval unavailable"], diagnostics
    try:
        boundary_start = parse_temporal_value(layer["before"])[0] if layer.get("before") is not None else parse_temporal_value(layer["start"])[0] if layer.get("start") is not None else None
        boundary_end = parse_temporal_value(layer["after"])[1] if layer.get("after") is not None else parse_temporal_value(layer["end"])[1] if layer.get("end") is not None else None
        if mode == "between" and (boundary_start is None or boundary_end is None):
            raise ValueError("between requires start and end")
        if boundary_start and boundary_end and boundary_start > boundary_end:
            raise ValueError("temporal boundaries are contradictory")
    except (TypeError, ValueError) as exc:
        diagnostics.update({"status": "failed", "failure_reason": str(exc), "candidate_count": 0})
        return [], [f"temporal boundary failure: {exc}"], diagnostics
    allowed_types = set(layer.get("anchor_types") or config.retrieval_temporal_default_anchor_types)
    allowed_authorities = set(layer.get("authorities") or config.retrieval_temporal_allowed_authorities)
    anchors_by_note: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in anchor_rows:
        anchor = dict(row)
        if anchor["anchor_type"] not in allowed_types or anchor["authority"] not in allowed_authorities:
            continue
        relation = relation_for_anchor(anchor, mode=mode, boundary_start=boundary_start, boundary_end=boundary_end, include_unresolved=bool(layer.get("include_unresolved", config.retrieval_temporal_include_conflicted_by_default)))
        if relation is not None:
            anchor["relation_certainty"] = relation
            anchors_by_note[str(anchor["note_id"])].append(anchor)
    eligible_chunk_ids = {str(row["chunk_id"]) for row in chunk_rows if str(row.get("note_id")) in anchors_by_note and _chunk_matches_scope(row, scope_filters)}
    rows_by_id = {str(row["chunk_id"]): row for row in chunk_rows}
    by_subject: list[list[dict[str, Any]]] = []
    notes: list[str] = []
    lexical_total = 0
    vector_total = 0
    for subject in subjects:
        lexical = [query for query in lexical_queries if not subject or subject.lower() in query.lower()] or ([subject] if subject else lexical_queries)
        semantic = [query for query in semantic_queries if not subject or subject.lower() in query.lower()] or ([subject] if subject else semantic_queries)
        literal = [entry for entry in literal_terms if not subject or subject.lower() in str(entry.get("term") or "").lower()]
        combined_queries = list(dict.fromkeys(list(lexical) + [str(item.get("term")) for item in literal if item.get("term")]))
        lexical_candidates, lexical_notes, lexical_diag = _lexical_candidates(chunk_rows, combined_queries, connection=connection, scope_filters=scope_filters, limit=None, config=config, mode=config.retrieval_lexical_default_mode, return_diagnostics=True)
        lexical_candidates = [item for item in lexical_candidates if str(item.get("chunk_id")) in eligible_chunk_ids]
        vector_candidates, vector_notes, vector_diag = _vector_candidates(connection=connection, config=config, embedding_backend=embedding_backend, vector_query=semantic[0] if semantic else subject, vector_queries=semantic or ([subject] if subject else []), scope_filters=scope_filters, limit=config.retrieval_temporal_max_candidates, eligible_chunk_ids=eligible_chunk_ids, return_diagnostics=True)
        lexical_total += len(lexical_candidates)
        vector_total += len(vector_candidates)
        notes.extend([*lexical_notes, *vector_notes])
        lexical_by_id = {str(item["chunk_id"]): index for index, item in enumerate(lexical_candidates, 1)}
        vector_by_id = {str(item["chunk_id"]): index for index, item in enumerate(vector_candidates, 1)}
        candidates: list[dict[str, Any]] = []
        for chunk_id in sorted(set(lexical_by_id) | set(vector_by_id)):
            row = rows_by_id.get(chunk_id); anchors = anchors_by_note.get(str(row.get("note_id"))) if row else []
            if not row or not anchors: continue
            governing = _choose_temporal_governing_anchor(anchors, mode=mode, direction=str(layer.get("direction") or "ascending"))
            ranks = [rank for rank in (lexical_by_id.get(chunk_id), vector_by_id.get(chunk_id)) if rank]
            relevance = sum(1.0 / rank for rank in ranks)
            candidates.append({**row, "selection_source": "temporal", "source_layers": ["temporal"], "score": relevance, "internal_ordinal_relevance": relevance, "temporal_subject": subject, "temporal_subjects": [subject] if subject else [], "temporal_mode": mode, "temporal_governing_anchor_id": governing["anchor_id"], "temporal_governing_anchor_type": governing["anchor_type"], "temporal_governing_canonical_start": governing["canonical_start"], "temporal_governing_canonical_end": governing["canonical_end"], "temporal_anchor_ids": [item["anchor_id"] for item in sorted(anchors, key=_temporal_anchor_order_key)], "temporal_provenance": [{"anchor_id": item["anchor_id"], "canonical_start": item["canonical_start"], "canonical_end": item["canonical_end"], "authority": item["authority"], "subject": subject or None, "relation": mode, "relation_certainty": item["relation_certainty"]} for item in sorted(anchors, key=_temporal_anchor_order_key)], "relation_status": governing["relation_certainty"], "semantic_query_provenance": semantic, "selection_reason": f"temporal {mode} relevance admission", "match_reason": f"temporal {mode} relation with ordinal relevance"})
        for candidate in candidates:
            candidate["internal_lexical_rank"] = lexical_by_id.get(str(candidate.get("chunk_id") or ""))
            candidate["internal_vector_rank"] = vector_by_id.get(str(candidate.get("chunk_id") or ""))
        by_subject.append(_order_temporal_candidates(candidates, mode=mode, direction=str(layer.get("direction") or "ascending")))
        diagnostics["per_subject"].append({"subject": subject or None, "context_type": "subject" if subject else "query", "lexical_candidate_count": len(lexical_candidates), "vector_candidate_count": len(vector_candidates), "temporal_candidate_count": len(candidates), "governing_anchor_count": len(candidates)})
    requested_limit = max(0, int(layer.get("effective_limit", layer.get("limit", config.retrieval_temporal_default_limit))))
    limit = min(config.retrieval_temporal_max_candidates, max(requested_limit, len(subjects)))
    temporal: list[dict[str, Any]] = []
    for index in range(max((len(items) for items in by_subject), default=0)):
        for items in by_subject:
            if index < len(items) and len(temporal) < limit:
                temporal.append(items[index])
    diagnostics["internal_surface_counts"] = {"lexical": lexical_total, "vector": vector_total}
    diagnostics["requested_limit"] = requested_limit
    diagnostics["effective_limit"] = limit
    diagnostics["limit_adjustment"] = "raised_to_subject_count" if limit != requested_limit else "none"
    satisfied_subjects = {str(item.get("temporal_subject") or "") for item in temporal if str(item.get("temporal_subject") or "") in named_subjects}
    diagnostics.update({"status": "completed_with_candidates" if temporal else "completed_no_candidates", "candidate_count": len(temporal), "satisfied_subject_count": len(satisfied_subjects), "missing_subject_count": len(set(named_subjects) - satisfied_subjects), "one_per_subject_reservation_satisfied": bool(named_subjects) and all(subject in satisfied_subjects for subject in named_subjects)})
    if config.retrieval_temporal_max_candidates < len(subjects):
        diagnostics["status"] = "incomplete_subject_capacity"
        diagnostics["limit_adjustment"] = "runtime_max_below_subject_count"
    return temporal, notes, diagnostics


_FUSION_SURFACE_ORDER = ("exact", "lexical", "vector", "graph", "temporal")


def _deduplicate_surface_candidates(
    candidates: list[dict[str, Any]],
    *,
    surface: str,
) -> list[dict[str, Any]]:
    """Keep the first executor-local representation for each chunk."""
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for candidate in candidates:
        chunk_id = str(candidate.get("chunk_id") or "")
        if not chunk_id or chunk_id in seen:
            if surface == "temporal" and chunk_id:
                existing = next((item for item in unique if str(item.get("chunk_id") or "") == chunk_id), None)
                if existing is not None:
                    for field in ("temporal_subjects", "temporal_anchor_ids", "temporal_provenance"):
                        values = [*existing.get(field, []), *candidate.get(field, [])]
                        merged_values: list[Any] = []
                        for value in values:
                            if value not in merged_values:
                                merged_values.append(value)
                        existing[field] = merged_values
            continue
        seen.add(chunk_id)
        unique.append(candidate)
    return unique


def _surface_rank_maps(
    surface_candidates: dict[str, list[dict[str, Any]]],
) -> tuple[dict[str, dict[str, int]], dict[str, dict[str, float]]]:
    ranks: dict[str, dict[str, int]] = {}
    raw_scores: dict[str, dict[str, float]] = {}
    for surface in _FUSION_SURFACE_ORDER:
        ranks[surface] = {}
        raw_scores[surface] = {}
        for ordinal, candidate in enumerate(surface_candidates.get(surface, []), start=1):
            chunk_id = str(candidate.get("chunk_id") or "")
            if not chunk_id:
                continue
            ranks[surface][chunk_id] = ordinal
            raw_scores[surface][chunk_id] = float(candidate.get("score") or 0.0)
    return ranks, raw_scores


def _apply_ordinal_fusion_metadata(
    candidate: dict[str, Any],
    *,
    surface_ranks: dict[str, int],
    surface_raw_scores: dict[str, float],
) -> None:
    ordered_ranks = {
        surface: surface_ranks[surface]
        for surface in _FUSION_SURFACE_ORDER
        if surface in surface_ranks
    }
    ordered_scores = {
        surface: surface_raw_scores[surface]
        for surface in _FUSION_SURFACE_ORDER
        if surface in surface_raw_scores
    }
    candidate["surface_ranks"] = ordered_ranks
    candidate["surface_raw_scores"] = ordered_scores
    candidate["ordinal_fusion_score"] = sum(1.0 / rank for rank in ordered_ranks.values())
    candidate["best_surface_rank"] = min(ordered_ranks.values()) if ordered_ranks else None
    candidate["surface_rank_sum"] = sum(ordered_ranks.values())


def _merge_candidates(
    lexical_candidates: list[dict[str, Any]],
    vector_candidates: list[dict[str, Any]],
    graph_candidates: list[dict[str, Any]],
    config: RuntimeConfig,
    exact_candidates: list[dict[str, Any]] | None = None,
    temporal_candidates: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    surface_candidates = {
        "exact": _deduplicate_surface_candidates(list(exact_candidates or []), surface="exact"),
        "lexical": _deduplicate_surface_candidates(lexical_candidates, surface="lexical"),
        "vector": _deduplicate_surface_candidates(vector_candidates, surface="vector"),
        "graph": _deduplicate_surface_candidates(graph_candidates, surface="graph"),
        "temporal": _deduplicate_surface_candidates(list(temporal_candidates or []), surface="temporal"),
    }
    surface_ranks, surface_raw_scores = _surface_rank_maps(surface_candidates)
    surface_representations = {
        surface: {str(candidate.get("chunk_id") or ""): candidate for candidate in candidates}
        for surface, candidates in surface_candidates.items()
    }

    def merge_list(existing: dict[str, Any], candidate: dict[str, Any], field: str) -> None:
        values = [*existing.get(field, []), *candidate.get(field, [])]
        if values:
            unique: list[Any] = []
            for value in values:
                if value not in unique:
                    unique.append(value)
            existing[field] = unique

    def merge_vector_provenance(existing: dict[str, Any], candidate: dict[str, Any]) -> None:
        scores: dict[str, float] = {}
        for item in [*existing.get("vector_query_scores", []), *candidate.get("vector_query_scores", [])]:
            if isinstance(item, dict) and str(item.get("query") or ""):
                scores[str(item["query"])] = max(scores.get(str(item["query"]), float("-inf")), float(item.get("similarity") or 0.0))
        if not scores:
            return
        ordered_scores = {query: scores[query] for query in sorted(scores)}
        best_query = sorted(ordered_scores, key=lambda query: (-ordered_scores[query], query))[0]
        existing["vector_query_scores"] = [{"query": query, "similarity": ordered_scores[query]} for query in ordered_scores]
        existing["semantic_query_provenance"] = list(ordered_scores)
        existing["vector_best_query"] = best_query
        existing["vector_similarity"] = ordered_scores[best_query]

    def absorb(candidate: dict[str, Any]) -> None:
        chunk_id = str(candidate["chunk_id"])
        existing = merged.get(chunk_id)
        source = str(candidate.get("selection_source") or "lexical")
        candidate_layers = _candidate_source_layers(candidate)
        if existing is None:
            merged[chunk_id] = dict(candidate)
            merged[chunk_id]["sources"] = list(candidate_layers)
            merged[chunk_id]["source_layers"] = list(candidate_layers)
            merged[chunk_id]["semantic_query_provenance"] = list(dict.fromkeys(_coerce_string_list(candidate.get("semantic_query_provenance"))))
            merged[chunk_id]["exact_term_provenance"] = list(dict.fromkeys(_coerce_string_list(candidate.get("exact_term_provenance"))))
            return
        existing_queries = _coerce_string_list(existing.get("semantic_query_provenance"))
        candidate_queries = _coerce_string_list(candidate.get("semantic_query_provenance"))
        existing_graph_provenance = _coerce_string_list(existing.get("graph_provenance"))
        candidate_graph_provenance = _coerce_string_list(candidate.get("graph_provenance"))
        existing["semantic_query_provenance"] = list(dict.fromkeys([*existing_queries, *candidate_queries]))
        merge_vector_provenance(existing, candidate)
        existing["sources"] = list(dict.fromkeys(existing.get("sources", []) + candidate_layers))
        existing["source_layers"] = list(dict.fromkeys(existing.get("source_layers", []) + candidate_layers))
        merge_list(existing, candidate, "context_subjects")
        merge_list(existing, candidate, "temporal_subjects")
        merge_list(existing, candidate, "exact_term_provenance")
        merge_list(existing, candidate, "exact_match_evidence")
        merge_list(existing, candidate, "graph_hop_provenance")
        merge_list(existing, candidate, "temporal_provenance")
        merge_list(existing, candidate, "temporal_anchor_ids")
        if isinstance(existing.get("grounding"), dict) and isinstance(candidate.get("grounding"), dict):
            existing["grounding"] = merge_grounding_assessments(existing["grounding"], candidate["grounding"])
        elif isinstance(candidate.get("grounding"), dict):
            existing["grounding"] = candidate["grounding"]
        merge_list(existing, candidate, "authorized_subjects")
        merge_list(existing, candidate, "identity_seed_note_ids")
        merge_list(existing, candidate, "wikilink_identity_promotions")
        merge_list(existing, candidate, "wikilink_occurrences")
        if existing.get("temporal_governing_anchor_id") is None and candidate.get("temporal_governing_anchor_id") is not None:
            existing["temporal_governing_anchor_id"] = candidate["temporal_governing_anchor_id"]
            existing["temporal_governing_anchor_type"] = candidate.get("temporal_governing_anchor_type")
            existing["temporal_governing_canonical_start"] = candidate.get("temporal_governing_canonical_start")
            existing["temporal_governing_canonical_end"] = candidate.get("temporal_governing_canonical_end")
        if existing_graph_provenance or candidate_graph_provenance:
            existing["graph_provenance"] = list(dict.fromkeys([*existing_graph_provenance, *candidate_graph_provenance]))
        if existing.get("graph_direction") is None and candidate.get("graph_direction") is not None:
            existing["graph_direction"] = candidate.get("graph_direction")
        existing["_retrieval_demoted"] = bool(existing.get("_retrieval_demoted")) or bool(candidate.get("_retrieval_demoted"))
        existing["selection_reason"] = ", ".join(
            part for part in [existing.get("selection_reason"), candidate.get("selection_reason")] if part
        )
        merge_vector_provenance(existing, candidate)

    for surface in _FUSION_SURFACE_ORDER:
        for candidate in surface_candidates[surface]:
            absorb(candidate)

    for chunk_id, candidate in merged.items():
        supporting_ranks = {
            surface: ranks[chunk_id]
            for surface, ranks in surface_ranks.items()
            if chunk_id in ranks
        }
        supporting_scores = {
            surface: raw_scores[chunk_id]
            for surface, raw_scores in surface_raw_scores.items()
            if chunk_id in raw_scores
        }
        _apply_ordinal_fusion_metadata(
            candidate,
            surface_ranks=supporting_ranks,
            surface_raw_scores=supporting_scores,
        )
        best_surface = min(
            supporting_ranks,
            key=lambda surface: (supporting_ranks[surface], _FUSION_SURFACE_ORDER.index(surface)),
        )
        best_representation = surface_representations[best_surface][chunk_id]
        preserved_fields = {
            "source_layers",
            "sources",
            "exact_term_provenance",
            "exact_match_evidence",
            "graph_provenance",
            "graph_hop_provenance",
            "graph_direction",
            "vector_query_scores",
            "semantic_query_provenance",
            "vector_best_query",
            "vector_similarity",
            "temporal_provenance",
            "temporal_subjects",
            "context_subjects",
            "temporal_anchor_ids",
            "temporal_governing_anchor_id",
            "temporal_governing_anchor_type",
            "temporal_governing_canonical_start",
            "temporal_governing_canonical_end",
            "selection_reason",
            "grounding",
            "authorized_subjects",
            "surface_ranks",
            "surface_raw_scores",
            "ordinal_fusion_score",
            "best_surface_rank",
            "surface_rank_sum",
        }
        merged_fields = {key: candidate[key] for key in preserved_fields if key in candidate}
        candidate.update({key: value for key, value in best_representation.items() if key not in preserved_fields})
        candidate.update(merged_fields)
        candidate["selection_source"] = best_surface

    ranked = sorted(
        merged.values(),
        key=lambda candidate: (
            bool(candidate.get("_retrieval_demoted")),
            -float(candidate.get("ordinal_fusion_score") or 0.0),
            int(candidate.get("best_surface_rank") or 10**9),
            int(candidate.get("surface_rank_sum") or 10**9),
            tuple(
                candidate.get("surface_ranks", {}).get(surface, 10**9)
                for surface in _FUSION_SURFACE_ORDER
            ),
            str(candidate["chunk_id"]),
        ),
    )
    for candidate in ranked:
        sources = _candidate_source_layers(candidate)
        candidate["selection_reason"] = ", ".join(
            part for part in [candidate.get("selection_reason"), f"sources: {', '.join(str(source) for source in sources)}"] if part
        )
    return ranked


def _select_retrieval_chunks(
    *,
    merged_candidates: list[dict[str, Any]],
    max_chunks: int,
    selection_policy: dict[str, Any] | None = None,
    required_exact_terms: set[str] | None = None,
    required_sources: set[str] | None = None,
    selection_diagnostics: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    seen_content_hashes: set[str] = set()
    seen_chunk_ids: set[str] = set()

    def absorb(candidate: dict[str, Any]) -> bool:
        if len(selected) >= max(0, max_chunks):
            return False
        chunk_id = str(candidate.get("chunk_id") or "")
        if chunk_id and chunk_id in seen_chunk_ids:
            return False
        chunk_hash = str(candidate.get("chunk_hash") or "").strip()
        if chunk_hash and chunk_hash in seen_content_hashes:
            return False
        if chunk_hash:
            seen_content_hashes.add(chunk_hash)
        if chunk_id:
            seen_chunk_ids.add(chunk_id)
        selected_candidate = dict(candidate)
        selected_candidate["source_layers"] = _candidate_source_layers(selected_candidate)
        selected_candidate.pop("sources", None)
        selected_candidate.pop("_retrieval_demoted", None)
        selected.append(selected_candidate)
        return True

    preserve_required_layers = bool(selection_policy.get("preserve_required_layers")) if isinstance(selection_policy, dict) else False
    # Reserve exact-backed representatives only for required terms with
    # positive matches. Ordinary retrieval-layer budgets are retired; exact
    # term bounds, executor limits, and the global packet maximum remain hard.
    required_exact_terms = set(required_exact_terms or set())
    reserved_terms: set[str] = set()
    if required_exact_terms:
        for candidate in merged_candidates:
            candidate_terms = set(_coerce_string_list(candidate.get("exact_term_provenance")))
            if "exact" not in _candidate_source_layers(candidate) or not candidate_terms.intersection(required_exact_terms - reserved_terms):
                continue
            if absorb(candidate):
                reserved_terms.update(candidate_terms.intersection(required_exact_terms))
                selected[-1]["_fusion_selection_stage"] = "required_exact_reservation"
                selected[-1]["_fusion_required_reservation"] = True

    required_sources = set(required_sources or set())
    reservation_diagnostics = selection_diagnostics if selection_diagnostics is not None else {}
    reservation_diagnostics.setdefault("required_sources", sorted(required_sources))
    reservation_diagnostics.setdefault("preserved_sources", [])
    reservation_diagnostics.setdefault("inadequacies", [])
    if preserve_required_layers and required_sources:
        while True:
            satisfied = {
                source for source in required_sources
                if any(source in _candidate_source_layers(item) for item in selected)
            }
            unsatisfied = required_sources - satisfied
            if not unsatisfied:
                break
            eligible = [
                candidate for candidate in merged_candidates
                if set(_candidate_source_layers(candidate)).intersection(unsatisfied)
            ]
            if not eligible:
                for source in sorted(unsatisfied):
                    reason = "no candidate survived executor limits"
                    if len(selected) >= max(0, max_chunks):
                        reason = "packet maximum is exhausted"
                    reservation_diagnostics["inadequacies"].append({"source": source, "reason": reason})
                break
            best = max(
                eligible,
                key=lambda candidate: len(set(_candidate_source_layers(candidate)).intersection(unsatisfied)),
            )
            if not absorb(best):
                reason = "packet maximum is exhausted" if len(selected) >= max(0, max_chunks) else "deduplication removed the only representative"
                reservation_diagnostics["inadequacies"].append({"source": sorted(unsatisfied)[0], "reason": reason})
                break
            selected[-1]["_fusion_selection_stage"] = "required_layer_reservation"
            selected[-1]["_fusion_required_reservation"] = True
            reservation_diagnostics["preserved_sources"] = sorted(
                set(reservation_diagnostics["preserved_sources"]).union(_candidate_source_layers(best))
            )

    # Ordinary selection is breadth-before-depth across evidence sources
    # identified by note_id. Required reservations already count as depth.
    selected_ids = {str(item.get("chunk_id") or "") for item in selected}
    selected_hashes = {str(item.get("chunk_hash") or "") for item in selected if str(item.get("chunk_hash") or "")}
    note_candidates: dict[str, list[dict[str, Any]]] = {}
    note_first_position: dict[str, int] = {}
    for position, candidate in enumerate(merged_candidates):
        chunk_id = str(candidate.get("chunk_id") or "")
        if chunk_id in selected_ids:
            continue
        note_id = str(candidate.get("note_id") or "")
        note_candidates.setdefault(note_id, []).append(candidate)
        note_first_position.setdefault(note_id, position)
    note_order = sorted(note_candidates, key=lambda note_id: (note_first_position[note_id], note_id))
    note_counts = {
        note_id: sum(str(item.get("note_id") or "") == note_id for item in selected)
        for note_id in note_order
    }
    note_offsets = {note_id: 0 for note_id in note_order}
    round_number = 0
    while len(selected) < max(0, max_chunks):
        eligible_notes = [
            note_id for note_id in note_order
            if note_offsets[note_id] < len(note_candidates[note_id])
        ]
        if not eligible_notes:
            break
        minimum_depth = min(note_counts[note_id] for note_id in eligible_notes)
        round_number += 1
        for note_id in note_order:
            if len(selected) >= max(0, max_chunks):
                break
            if note_id not in eligible_notes or note_counts[note_id] != minimum_depth:
                continue
            candidate = note_candidates[note_id][note_offsets[note_id]]
            note_offsets[note_id] += 1
            chunk_id = str(candidate.get("chunk_id") or "")
            chunk_hash = str(candidate.get("chunk_hash") or "")
            before_depth = note_counts[note_id]
            if chunk_id in selected_ids:
                reservation_diagnostics.setdefault("duplicate_rejections", []).append({"chunk_id": chunk_id, "reason": "duplicate_chunk_id"})
                continue
            if chunk_hash and chunk_hash in selected_hashes:
                reservation_diagnostics.setdefault("duplicate_rejections", []).append({"chunk_id": chunk_id, "reason": "duplicate_content_hash"})
                continue
            selected_candidate = dict(candidate)
            selected_candidate["_fusion_selection_stage"] = "ordinary_note_breadth"
            selected_candidate["_fusion_round"] = round_number
            selected_candidate["_fusion_note_count_before"] = before_depth
            selected_candidate["_fusion_note_count_after"] = before_depth + 1
            if absorb(selected_candidate):
                selected_ids.add(chunk_id)
                if chunk_hash:
                    selected_hashes.add(chunk_hash)
                note_counts[note_id] += 1
    return selected


def _fusion_diagnostics(
    *,
    merged_candidates: list[dict[str, Any]],
    selected_candidates: list[dict[str, Any]],
    selection_diagnostics: dict[str, Any],
    retrieval_packet: dict[str, Any],
) -> dict[str, Any]:
    selected_ids = {str(candidate.get("chunk_id") or "") for candidate in selected_candidates}
    selected_by_id = {str(candidate.get("chunk_id") or ""): candidate for candidate in selected_candidates}
    selected_hashes = {str(candidate.get("chunk_hash") or "") for candidate in selected_candidates if str(candidate.get("chunk_hash") or "")}
    selected_note_counts = Counter(str(candidate.get("note_id") or "") for candidate in selected_candidates)
    candidate_records: list[dict[str, Any]] = []
    for candidate in merged_candidates:
        chunk_id = str(candidate.get("chunk_id") or "")
        selected = chunk_id in selected_ids
        selected_record = selected_by_id.get(chunk_id, candidate)
        rejection_reason = None
        if not selected:
            chunk_hash = str(candidate.get("chunk_hash") or "")
            if chunk_hash and chunk_hash in selected_hashes:
                rejection_reason = "duplicate_content_hash"
            else:
                rejection_reason = "not_selected_after_note_breadth"
        candidate_records.append({
            "chunk_id": chunk_id,
            "note_id": str(candidate.get("note_id") or ""),
            "retrieval_demoted": bool(candidate.get("_retrieval_demoted")),
            "source_layers": _candidate_source_layers(candidate),
            "surface_ranks": dict(candidate.get("surface_ranks") or {}),
            "surface_raw_scores": dict(candidate.get("surface_raw_scores") or {}),
            "ordinal_fusion_score": candidate.get("ordinal_fusion_score"),
            "best_surface_rank": candidate.get("best_surface_rank"),
            "surface_rank_sum": candidate.get("surface_rank_sum"),
            "selection_source": candidate.get("selection_source"),
            "status": "selected" if selected else "rejected",
            "selection_stage": selected_record.get("_fusion_selection_stage"),
            "selection_round": selected_record.get("_fusion_round"),
            "note_selected_count_before": selected_record.get("_fusion_note_count_before"),
            "note_selected_count_after": selected_record.get("_fusion_note_count_after"),
            "required_reservation": bool(selected_record.get("_fusion_required_reservation")),
            "rejection_reason": rejection_reason,
        })
    packet_bytes = len(json.dumps(retrieval_packet, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    return {
        "selector": "ordinal_note_breadth",
        "ordinal_formula": "sum(1 / surface_rank) over supporting surfaces",
        "surface_order": list(_FUSION_SURFACE_ORDER),
        "ordinary_layer_budgets": "retired",
        "budget_diagnostic": "selection_policy.budgets retired for ordinal-note-breadth selector",
        "candidate_count": len(merged_candidates),
        "unique_candidate_note_count": len({str(candidate.get("note_id") or "") for candidate in merged_candidates}),
        "selected_count": len(selected_candidates),
        "selected_unique_note_count": len(selected_note_counts),
        "selected_chunks_per_note": dict(sorted(selected_note_counts.items())),
        "max_selected_chunks_per_note": max(selected_note_counts.values(), default=0),
        "selected_counts_by_supporting_surface": _count_selected_by_layer(selected_candidates),
        "selected_counts_by_selection_source": dict(sorted(Counter(str(candidate.get("selection_source") or "") for candidate in selected_candidates).items())),
        "required_reservation": dict(selection_diagnostics),
        "retrieval_packet_bytes": packet_bytes,
        "synthesis_context_bytes": None,
        "candidates": candidate_records,
    }


def _semantic_traversal(
    *,
    connection: sqlite3.Connection,
    config: RuntimeConfig,
    semantic_compiler_packet: dict[str, Any],
    prior_thread_state: dict[str, Any],
    embedding_backend: EmbeddingBackend,
    resource_inventory_summary: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    planner_retrieval_plan = semantic_compiler_packet.get("planner_retrieval_plan") if isinstance(semantic_compiler_packet.get("planner_retrieval_plan"), dict) else {}
    if not planner_retrieval_plan:
        planner_retrieval_plan = build_default_retrieval_plan(
            raw_user_input=str(semantic_compiler_packet.get("raw_user_input") or ""),
            query=str(semantic_compiler_packet.get("query") or ""),
            concepts=_coerce_string_list(semantic_compiler_packet.get("concepts")),
            graph_seeds=_coerce_string_list(semantic_compiler_packet.get("graph_seeds")),
            resolved_referents=_coerce_string_list(semantic_compiler_packet.get("resolved_referents")),
            planner_defaults=config.retrieval_planner_defaults,
        )

    if resource_inventory_summary is None:
        resource_inventory_summary = build_resource_inventory(connection=connection, config=config)
    bound_retrieval_plan, resolver_adjustments, resource_inventory_summary = bind_retrieval_plan(
        planner_retrieval_plan=planner_retrieval_plan,
        inventory_summary=resource_inventory_summary,
        config=config,
        raw_user_input=str(semantic_compiler_packet.get("raw_user_input") or ""),
    )
    # Execute the deterministic runtime closure while retaining the compiler's
    # requested layer list separately for diagnostics and requiredness.
    requested_layers = list(bound_retrieval_plan.get("retrieval_layers") or [])
    expanded_layers = list(bound_retrieval_plan.get("runtime_expanded_layers") or requested_layers)
    bound_retrieval_plan["requested_retrieval_layers"] = requested_layers
    bound_retrieval_plan["retrieval_layers"] = expanded_layers
    planner_resolved_referents = coerce_string_list(planner_retrieval_plan.get("resolved_referents"))
    bound_resolved_referents = coerce_string_list(bound_retrieval_plan.get("resolved_referents"))
    referent_propagation = {
        "status": "preserved" if planner_resolved_referents == bound_resolved_referents else "failed",
        "planner_count": len(planner_resolved_referents),
        "bound_count": len(bound_resolved_referents),
        "order_preserved": planner_resolved_referents == bound_resolved_referents,
    }
    plan_completeness = validate_plan_completeness(planner_retrieval_plan=planner_retrieval_plan, config=config)
    plan_executability = semantic_compiler_packet.get("planner_diagnostics", {}).get("plan_executability", {})
    plan_executability = dict(plan_executability) if isinstance(plan_executability, dict) else {}
    if referent_propagation["status"] == "failed":
        plan_executability = {
            **plan_executability,
            "status": "non_executable",
            "blocking_reasons": [
                *coerce_string_list(plan_executability.get("blocking_reasons")),
                "resolved_referents propagation mismatch between planner and bound plan",
            ],
        }

    scope_filters = bound_retrieval_plan.get("scope_filters") if isinstance(bound_retrieval_plan.get("scope_filters"), dict) else {}
    explicit_literal_terms = [entry for entry in bound_retrieval_plan.get("literal_terms", []) if isinstance(entry, dict)]
    runtime_contextual_terms = [entry for entry in bound_retrieval_plan.get("runtime_contextual_exact_terms", []) if isinstance(entry, dict)]
    literal_terms = [*explicit_literal_terms, *runtime_contextual_terms]
    semantic_queries = _coerce_string_list(bound_retrieval_plan.get("semantic_queries"))
    lexical_queries = _coerce_string_list(bound_retrieval_plan.get("lexical_queries")) or semantic_queries
    graph_layer = retrieval_plan_layer(bound_retrieval_plan, "graph_expand")
    exact_layer = retrieval_plan_layer(bound_retrieval_plan, "exact_chunk_search")
    lexical_layer = retrieval_plan_layer(bound_retrieval_plan, "lexical_chunk_search")
    vector_layer = retrieval_plan_layer(bound_retrieval_plan, "vector_search")
    temporal_layer = retrieval_plan_layer(bound_retrieval_plan, "temporal_retrieve")

    chunk_rows = _load_chunk_rows(connection)
    layer_manifests: dict[str, Any] = {}
    execution = {"layers_executed": [], "layers_skipped": []}
    unsupported_layer_requests: list[dict[str, Any]] = []
    supported_operators = {"exact_chunk_search", "lexical_chunk_search", "vector_search", "graph_expand", "temporal_retrieve"}
    for layer in bound_retrieval_plan.get("retrieval_layers", []):
        if not isinstance(layer, dict) or str(layer.get("operator") or "") in supported_operators:
            continue
        unsupported_layer_requests.append({
            "operator": str(layer.get("operator") or ""),
            "required": bool(layer.get("required")),
            "supplied_fields": dict(layer),
            "reason": "retrieval operator is not supported by this runtime seam",
        })
        execution["layers_skipped"].append({"layer": str(layer.get("operator") or ""), "reason": "unsupported operator"})

    exact_candidates: list[dict[str, Any]] = []
    exact_notes: list[str] = []
    exact_info = {
        "operator": "exact_chunk_search",
        "status": "not_requested",
        "literal_terms": [],
        "scope": scope_filters,
        "search_exhaustive": False,
        "return_total_count_requested": False,
        "count_status": "not_requested",
        "count_unit": "matching_chunks",
        "total_match_count": None,
        "total_occurrence_count": None,
        "matching_note_count": None,
        "returned_candidate_count": 0,
        "returned_count": 0,
        "term_results": [],
    }
    if exact_layer is not None:
        execution["layers_executed"].append("exact_chunk_search")
        exact_candidates, exact_notes, exact_info = _exact_candidates(
            chunk_rows,
            literal_terms,
            scope_filters=scope_filters,
            limit=_layer_limit(exact_layer, config.retrieval_exact_max_matches),
            return_total_count=bool(exact_layer.get("return_total_count")),
            config=config,
        )
    else:
        execution["layers_skipped"].append({"layer": "exact_chunk_search", "reason": "not requested by bound retrieval plan"})
        exact_notes = ["exact search skipped: not requested by bound retrieval plan"]
    layer_manifests["exact"] = exact_info

    lexical_candidates: list[dict[str, Any]] = []
    lexical_notes: list[str] = []
    lexical_diagnostics: dict[str, Any] = {}
    if lexical_layer is not None:
        execution["layers_executed"].append("lexical_chunk_search")
        lexical_candidates, lexical_notes, lexical_diagnostics = _lexical_candidates(
            chunk_rows,
            lexical_queries,
            connection=connection,
            scope_filters=scope_filters,
            limit=_layer_limit(lexical_layer, config.retrieval_lexical_max_candidates),
            config=config,
            mode=str(lexical_layer.get("mode") or config.retrieval_lexical_default_mode),
            return_diagnostics=True,
        )
    else:
        execution["layers_skipped"].append({"layer": "lexical_chunk_search", "reason": "not requested by bound retrieval plan"})
        lexical_notes = ["lexical search skipped: not requested by bound retrieval plan"]
        lexical_diagnostics = {"status": "not_requested", "requested_queries": [], "candidate_count": 0}

    vector_candidates: list[dict[str, Any]] = []
    vector_notes: list[str] = []
    vector_diagnostics: dict[str, Any] = {}
    if vector_layer is not None:
        execution["layers_executed"].append("vector_search")
        vector_candidates, vector_notes, vector_diagnostics = _vector_candidates(
            connection=connection,
            config=config,
            embedding_backend=embedding_backend,
            vector_query=semantic_queries[0] if semantic_queries else "",
            vector_queries=semantic_queries,
            scope_filters=scope_filters,
            limit=_layer_limit(vector_layer, config.retrieval_vector_max_candidates),
            return_diagnostics=True,
        )
    else:
        execution["layers_skipped"].append({"layer": "vector_search", "reason": "not requested by bound retrieval plan"})
        vector_notes = ["vector search skipped: not requested by bound retrieval plan"]
        vector_diagnostics = {"status": "not_requested", "requested_queries": [], "query_results": [], "candidate_count": 0}

    graph_candidates: list[dict[str, Any]] = []
    graph_notes: list[str] = []
    graph_traversal_info: dict[str, Any]
    # Contextual support surfaces are allowed to ground graph traversal even
    # when graph_expand was omitted from the compiler's requested list.
    direct_context_candidates = _annotate_context_candidates(
        [*exact_candidates, *lexical_candidates, *vector_candidates],
        plan={**bound_retrieval_plan, "concepts": semantic_compiler_packet.get("concepts", [])},
        config=config,
        connection=connection,
    )
    grounding_bundles = (bound_retrieval_plan.get("grounding_specification") or {}).get("bundles", [])
    subject_id_to_referent = {
        str(bundle.get("subject_id")): str(bundle.get("referent") or "")
        for bundle in grounding_bundles if isinstance(bundle, dict) and bundle.get("referent")
    }
    canonical_referents = {
        subject_id_to_referent.get(str(subject_id), "")
        for candidate in direct_context_candidates
        for subject_id in ((candidate.get("grounding") or {}).get("object_identity") or {}).get("subjects", [])
        if subject_id_to_referent.get(str(subject_id), "")
    }
    canonical_referents.update(
        referent for referent in planner_resolved_referents
        if any(_normalize_text(seed) == _normalize_text(referent) for seed in _coerce_string_list(bound_retrieval_plan.get("graph_seeds")))
    )
    direct_context_candidates = _enforce_canonical_subject_authority(
        direct_context_candidates,
        canonical_referents=canonical_referents,
        named_subjects=bool(planner_resolved_referents),
    )
    if graph_layer is not None:
        execution["layers_executed"].append(str(graph_layer.get("operator") or "graph_expand"))
        graph_candidates, graph_notes, graph_traversal_info = _graph_candidates(
            connection=connection,
            config=config,
            planner_retrieval_plan=bound_retrieval_plan,
            graph_depth=graph_layer,
            scope_filters=scope_filters,
            limit=_layer_limit(graph_layer, config.retrieval_graph_max_candidates),
            context_candidates=direct_context_candidates,
        )
    else:
        execution["layers_skipped"].append({"layer": "graph_expand", "reason": "not requested by bound retrieval plan"})
        graph_candidates, graph_notes, graph_traversal_info = [], ["graph search skipped: not requested by bound retrieval plan"], {
            "status": "not_requested",
            "enabled": config.graph_traversal_enabled,
            "hop_limit": 0,
            "seed_sources": list(config.graph_traversal_seed_sources),
            "submitted_seeds": [],
            "matched_seed_count": 0,
            "matched_seed_note_ids": [],
            "expanded_note_count": 0,
            "selected_note_ids": [],
            "edge_types_used": [],
            "direction": config.graph_traversal_direction,
            "candidate_note_ids": [],
            "candidate_unique_note_ids": [],
            "traversal_hops": [],
        }
    graph_candidates = _annotate_context_candidates(
        graph_candidates,
        plan={**bound_retrieval_plan, "concepts": semantic_compiler_packet.get("concepts", [])},
        config=config,
        connection=connection,
    )
    graph_candidates = _enforce_canonical_subject_authority(
        graph_candidates,
        canonical_referents=canonical_referents,
        named_subjects=bool(planner_resolved_referents),
    )

    temporal_candidates: list[dict[str, Any]] = []
    temporal_notes: list[str] = []
    temporal_info: dict[str, Any] = {"operator": "temporal_retrieve", "status": "not_requested", "candidate_count": 0}
    if temporal_layer is not None:
        execution["layers_executed"].append("temporal_retrieve")
        try:
            if referent_propagation["status"] == "failed":
                temporal_candidates, temporal_notes, temporal_info = [], ["temporal retrieval blocked: resolved_referents propagation failed"], {
                    "operator": "temporal_retrieve",
                    "status": "non_executable",
                    "candidate_count": 0,
                    "subject_count": len(planner_resolved_referents),
                    "satisfied_subject_count": 0,
                    "missing_subject_count": len(planner_resolved_referents),
                    "one_per_subject_reservation_satisfied": False,
                    "failure_reason": "resolved_referents propagation mismatch between planner and bound plan",
                }
            else:
                temporal_candidates, temporal_notes, temporal_info = _temporal_candidates(
                    connection=connection, config=config, chunk_rows=chunk_rows,
                    layer=temporal_layer, lexical_queries=lexical_queries,
                    semantic_queries=semantic_queries, literal_terms=literal_terms,
                    scope_filters=scope_filters, embedding_backend=embedding_backend,
                    resolved_referents=bound_resolved_referents,
                    closure_candidates=[*direct_context_candidates, *graph_candidates],
                )
        except sqlite3.OperationalError as exc:
            temporal_info = {"operator": "temporal_retrieve", "status": "unavailable", "candidate_count": 0, "failure_reason": str(exc)}
            temporal_notes = [f"temporal retrieval unavailable: {exc}"]
    else:
        execution["layers_skipped"].append({"layer": "temporal_retrieve", "reason": "not requested by bound retrieval plan"})

    exact_candidates = _apply_retrieval_candidate_hygiene(
        candidates=exact_candidates,
        semantic_compiler_packet=semantic_compiler_packet,
        config=config,
    )
    lexical_candidates = _apply_retrieval_candidate_hygiene(
        candidates=lexical_candidates,
        semantic_compiler_packet=semantic_compiler_packet,
        config=config,
    )
    vector_candidates = _apply_retrieval_candidate_hygiene(
        candidates=vector_candidates,
        semantic_compiler_packet=semantic_compiler_packet,
        config=config,
    )
    graph_candidates = _apply_retrieval_candidate_hygiene(
        candidates=graph_candidates,
        semantic_compiler_packet=semantic_compiler_packet,
        config=config,
    )
    temporal_candidates = _apply_retrieval_candidate_hygiene(
        candidates=temporal_candidates,
        semantic_compiler_packet=semantic_compiler_packet,
        config=config,
    )

    exact_candidates = _annotate_scope_matches(
        exact_candidates,
        hard_scope_filters=scope_filters,
    )

    layer_manifests["lexical"] = _non_exact_layer_manifest(
        operator="lexical_chunk_search",
        layer=lexical_layer,
        status=str(lexical_diagnostics.get("status") or ("completed_with_candidates" if lexical_candidates else "completed_no_candidates")),
        candidate_count=len(lexical_candidates),
        diagnostics=lexical_diagnostics,
    )
    layer_manifests["vector"] = _non_exact_layer_manifest(
        operator="vector_search",
        layer=vector_layer,
        status=str(vector_diagnostics.get("status") or ("completed_with_candidates" if vector_candidates else "completed_no_candidates")),
        candidate_count=len(vector_candidates),
        diagnostics=vector_diagnostics,
    )
    graph_status = str(graph_traversal_info.get("status") or ("completed_with_candidates" if graph_candidates else "completed_no_candidates"))
    layer_manifests["graph"] = _non_exact_layer_manifest(
        operator="graph_expand",
        layer=graph_layer,
        status=graph_status,
        candidate_count=len(graph_candidates),
        diagnostics=graph_traversal_info,
    )
    layer_manifests["temporal"] = _non_exact_layer_manifest(
        operator="temporal_retrieve",
        layer=temporal_layer,
        status=str(temporal_info.get("status") or ("completed_with_candidates" if temporal_candidates else "completed_no_candidates")),
        candidate_count=len(temporal_candidates),
        diagnostics=temporal_info,
    )
    lexical_candidates = _annotate_scope_matches(
        lexical_candidates,
        hard_scope_filters=scope_filters,
    )
    vector_candidates = _annotate_scope_matches(
        vector_candidates,
        hard_scope_filters=scope_filters,
    )
    graph_candidates = _annotate_scope_matches(
        graph_candidates,
        hard_scope_filters=scope_filters,
    )
    temporal_candidates = _annotate_scope_matches(
        temporal_candidates,
        hard_scope_filters=scope_filters,
    )

    merged_candidates = _merge_candidates(lexical_candidates, vector_candidates, graph_candidates, config=config, exact_candidates=exact_candidates, temporal_candidates=temporal_candidates)
    required_exact_terms = {
        str(result.get("term"))
        for result in (layer_manifests.get("exact", {}).get("term_results", []) if isinstance(layer_manifests.get("exact"), dict) else [])
        if isinstance(result, dict) and bool(result.get("required")) and result.get("status") == "completed_with_matches"
    }
    required_sources = {
        LAYER_TO_SOURCE.get(str(layer.get("operator") or ""))
        for layer in bound_retrieval_plan.get("retrieval_layers", [])
        if isinstance(layer, dict) and bool(layer.get("required")) and LAYER_TO_SOURCE.get(str(layer.get("operator") or ""))
    }
    selection_diagnostics: dict[str, Any] = {}
    effective_max_chunks = bound_retrieval_plan.get("selection_policy", {}).get("max_chunks")
    if effective_max_chunks is None:
        effective_max_chunks = config.max_retrieval_chunks
    selected_candidates = _select_retrieval_chunks(
        merged_candidates=merged_candidates,
        max_chunks=int(effective_max_chunks),
        selection_policy=bound_retrieval_plan.get("selection_policy") if isinstance(bound_retrieval_plan.get("selection_policy"), dict) else None,
        required_exact_terms=required_exact_terms,
        required_sources=required_sources,
        selection_diagnostics=selection_diagnostics,
    )
    selected_counts = _count_selected_by_layer(selected_candidates)
    for source, manifest in (("lexical", layer_manifests.get("lexical")), ("vector", layer_manifests.get("vector")), ("graph", layer_manifests.get("graph")), ("temporal", layer_manifests.get("temporal"))):
        if not isinstance(manifest, dict):
            continue
        selected_for_source = [candidate for candidate in selected_candidates if source in _candidate_source_layers(candidate)]
        manifest["selected_contribution_count"] = len(selected_for_source)
        manifest["selected_chunk_ids"] = [str(candidate.get("chunk_id") or "") for candidate in selected_for_source]
        manifest["adequate_contribution"] = manifest.get("status") == "completed_with_candidates" and bool(selected_for_source)
        if bool(manifest.get("required")) and not manifest["adequate_contribution"]:
            manifest["inadequacy_reason"] = (
                "required layer did not produce candidates"
                if manifest.get("status") != "completed_with_candidates"
                else "required layer produced candidates but no selected chunk retained its provenance"
            )
        if source == "temporal" and bool(manifest.get("required")) and int((temporal_info or {}).get("missing_subject_count") or 0) > 0:
            manifest["adequate_contribution"] = False
            manifest["inadequacy_reason"] = "required temporal subject context is incomplete"
    exact_info = layer_manifests.get("exact", {}) if isinstance(layer_manifests.get("exact"), dict) else {}
    exact_search_performed = exact_layer is not None and not bool(exact_layer.get("automatic"))
    total_exact_matches = exact_info.get("total_match_count")
    matching_note_count = exact_info.get("matching_note_count")
    for term_result in exact_info.get("term_results", []) if isinstance(exact_info.get("term_results"), list) else []:
        if not isinstance(term_result, dict):
            continue
        selected_for_term = [
            candidate for candidate in selected_candidates
            if "exact" in _candidate_source_layers(candidate)
            and str(term_result.get("term")) in _coerce_string_list(candidate.get("exact_term_provenance"))
        ]
        term_result["selected_exact_chunk_count"] = len(selected_for_term)
        term_result["selected_chunk_ids"] = [str(candidate.get("chunk_id") or "") for candidate in selected_for_term]
        term_result["adequate_contribution"] = (
            term_result.get("status") == "completed_no_matches"
            or not bool(term_result.get("required"))
            or len(selected_for_term) > 0
        )
    runtime_claim_policy = config.retrieval_planner_defaults.get("claim_policy", {})
    negative_policy_requires_exact = runtime_claim_policy.get("negative_claims_require_exact_layer")
    negative_policy_supported = isinstance(negative_policy_requires_exact, bool) and negative_policy_requires_exact
    all_terms_completed = all(
        isinstance(result, dict) and result.get("status") in {"completed_no_matches", "completed_with_matches"}
        for result in exact_info.get("term_results", [])
    ) if exact_info.get("term_results") else False
    no_matches = exact_info.get("status") == "completed_no_matches"
    negative_allowed = bool(
        negative_policy_supported
        and exact_search_performed
        and no_matches
        and bool(exact_info.get("search_exhaustive"))
        and all_terms_completed
        and not any(result.get("status") == "completed_with_matches" for result in exact_info.get("term_results", []) if isinstance(result, dict))
    )
    required_layer_results: list[dict[str, Any]] = []
    for layer in bound_retrieval_plan.get("retrieval_layers", []):
        if not isinstance(layer, dict) or not bool(layer.get("required")):
            continue
        operator = str(layer.get("operator") or "")
        source = LAYER_TO_SOURCE.get(operator)
        if source is None:
            required_layer_results.append({
                "operator": operator,
                "source": None,
                "status": "unsupported",
                "candidate_count": 0,
                "selected_contribution_count": 0,
                "adequate_contribution": False,
                "blocking_reason": "required retrieval operator is unsupported",
            })
            continue
        manifest = exact_info if source == "exact" else layer_manifests.get(source, {})
        status = str(manifest.get("status") or "not_requested")
        if source == "exact":
            adequate = status in {"completed_no_matches", "completed_with_matches"} and not any(
                isinstance(result, dict)
                and bool(result.get("required"))
                and result.get("status") == "completed_with_matches"
                and not result.get("adequate_contribution")
                for result in exact_info.get("term_results", [])
            )
            selected_contribution = sum(
                1 for candidate in selected_candidates if "exact" in _candidate_source_layers(candidate)
            )
        else:
            adequate = bool(manifest.get("adequate_contribution"))
            selected_contribution = int(manifest.get("selected_contribution_count") or 0)
        result = {
            "operator": operator,
            "source": source,
            "status": status,
            "candidate_count": int(manifest.get("candidate_count") or len({"exact": exact_candidates, "lexical": lexical_candidates, "vector": vector_candidates, "graph": graph_candidates, "temporal": temporal_candidates}.get(source, []))),
            "selected_contribution_count": selected_contribution,
            "adequate_contribution": adequate,
            "blocking_reason": None,
        }
        if not adequate:
            result["blocking_reason"] = str(manifest.get("inadequacy_reason") or f"required {source} layer is {status}")
        required_layer_results.append(result)
    coverage = {
        "exact_search_performed": exact_search_performed,
        "exact_status": str(exact_info.get("status") or ("completed_with_matches" if total_exact_matches else "completed_no_matches" if exact_search_performed else "not_requested")),
        "scope": scope_filters,
        "literal_terms": [str(entry.get("term") or "") for entry in literal_terms if str(entry.get("term") or "")],
        "total_exact_matches": total_exact_matches,
        "matching_note_count": matching_note_count,
        "total_occurrence_count": exact_info.get("total_occurrence_count"),
        "count_status": exact_info.get("count_status"),
        "coverage_claims_allowed": bool(exact_search_performed and exact_info.get("search_exhaustive")) and exact_info.get("status") in {"completed_no_matches", "completed_with_matches"},
        "negative_claims_allowed": negative_allowed,
        "negative_claims_require_exact_layer": negative_policy_requires_exact,
        "negative_claim_policy_diagnostic": None if negative_policy_supported else "runtime YAML negative-claim policy is unsupported or unavailable; permission remains false",
        "required_exact_terms": sorted(required_exact_terms),
        "inadequate_required_exact_terms": sorted(
            str(result.get("term")) for result in exact_info.get("term_results", [])
            if isinstance(result, dict) and bool(result.get("required")) and result.get("status") == "completed_with_matches" and not result.get("adequate_contribution")
        ),
        "required_layer_results": required_layer_results,
        "selection_reservation": selection_diagnostics,
    }
    limits: list[str] = [f"Only {config.max_retrieval_chunks} chunk(s) may be selected for frontier synthesis."]
    if exact_search_performed and exact_info.get("return_total_count_requested"):
        limits.append(f"Exact layer found {total_exact_matches} matching chunk(s) across {matching_note_count} note(s); synthesis uses a representative subset.")
    elif exact_search_performed:
        limits.append(f"Exact layer returned {exact_info.get('returned_candidate_count', 0)} candidate(s); exhaustive total reporting was not requested.")
    if not exact_search_performed:
        limits.append("No corpus-wide positive or negative exact-match claim is allowed because exact search did not run.")

    traversal_manifest = {
        "planner_retrieval_plan": planner_retrieval_plan,
        "bound_retrieval_plan": bound_retrieval_plan,
        "resolver_adjustments": resolver_adjustments,
        "resolved_referent_propagation": referent_propagation,
        "resource_inventory_summary": resource_inventory_summary,
        "inventory_diagnostics": resource_inventory_summary.get("inventory_diagnostics", {}),
        "execution": execution,
        "candidate_counts": {
            "exact": len(exact_candidates),
            "lexical": len(lexical_candidates),
            "vector": len(vector_candidates),
            "graph": len(graph_candidates),
            "temporal": len(temporal_candidates),
        },
        "selected_counts": selected_counts,
        "selected_chunk_ids": [str(candidate["chunk_id"]) for candidate in selected_candidates],
        "layer_manifests": layer_manifests,
        "unsupported_layer_requests": unsupported_layer_requests,
        "plan_completeness": plan_completeness,
        "plan_executability": plan_executability,
        "coverage": coverage,
        "limits": limits,
        "graph_traversal": graph_traversal_info,
        "selection_notes": [*exact_notes, *lexical_notes, *vector_notes, *graph_notes, *temporal_notes],
        "semantic_closure": {
            "execution_model": "contextual_surface_closure_then_relation_evaluation",
            "requested_surfaces": [str(layer.get("operator") or "") for layer in requested_layers if isinstance(layer, dict)],
            "expanded_support_surfaces": [str(layer.get("operator") or "") for layer in bound_retrieval_plan.get("expanded_support_surfaces", []) if isinstance(layer, dict)],
            "direct_surface_attempt_counts": {"exact": 1 if exact_layer is not None else 0, "lexical": 1 if lexical_layer is not None else 0, "vector": 1 if vector_layer is not None else 0, "graph": 1 if graph_layer is not None else 0},
            "semantic_units_discovered": len({str(candidate.get("chunk_id") or "") for candidate in [*exact_candidates, *lexical_candidates, *vector_candidates, *graph_candidates] if candidate.get("chunk_id")}),
            "semantic_objects_discovered": len({str(candidate.get("note_id") or "") for candidate in [*exact_candidates, *lexical_candidates, *vector_candidates, *graph_candidates] if candidate.get("note_id")}),
            "graph_seeds_derived_from_grounded_objects": list(graph_traversal_info.get("submitted_seeds", [])),
            "temporal_anchors_attached": len({str(anchor_id) for candidate in temporal_candidates for anchor_id in candidate.get("temporal_anchor_ids", [])}),
            "termination_reason": "finite_transition_stages_completed",
            "manifest_version": (resource_inventory_summary.get("retrieval_surface_manifest") or {}).get("manifest_version"),
            "manifest_hash": sha256_json(resource_inventory_summary.get("retrieval_surface_manifest") or {}),
        },
        "semantic_grounding": {
            **_grounding_summary(
                specification=bound_retrieval_plan.get("grounding_specification", {}),
                candidates=merged_candidates,
            ),
            "named_subjects": bool((bound_retrieval_plan.get("grounding_specification") or {}).get("named_subjects")),
            "identity_authorized_graph_seed_count": int(graph_traversal_info.get("hydrated_seed_note_count") or 0),
            "evidence_only_graph_seed_count": max(0, int(graph_traversal_info.get("matched_seed_count") or 0) - int(graph_traversal_info.get("hydrated_seed_note_count") or 0)),
            "graph_hops_with_subject_authority": sum(1 for hop in graph_traversal_info.get("traversal_hops", []) if hop.get("subject_propagation_authorized")),
            "graph_hops_without_subject_authority": sum(1 for hop in graph_traversal_info.get("traversal_hops", []) if not hop.get("subject_propagation_authorized")),
            "temporal_candidates_before_proposition_filter": int(temporal_info.get("temporal_candidates_before_proposition_filter") or 0),
            "temporal_candidates_after_proposition_filter": int(temporal_info.get("temporal_candidates_after_proposition_filter") or 0),
            "grounding_specification": bound_retrieval_plan.get("grounding_specification", {}),
        },
    }

    retrieval_packet = {
        "bound_retrieval_plan": bound_retrieval_plan,
        "coverage": coverage,
        "limits": limits,
        "selected_chunks": [
            {
                "chunk_id": str(candidate["chunk_id"]),
                "note_id": str(candidate["note_id"]),
                "source_root_label": str(candidate["source_root_label"]),
                "relative_path": str(candidate["relative_path"]),
                "note_title": str(candidate["note_title"]),
                "section_label": str(candidate["section_label"]),
                "frontmatter_semantics": _parse_frontmatter_semantics(candidate.get("frontmatter_semantics_json")),
                "paragraph_text": str(candidate["paragraph_text"]),
                "chunk_hash": str(candidate["chunk_hash"]),
                "source_layers": _candidate_source_layers(candidate),
                "selection_source": str(candidate.get("selection_source") or ""),
                "exact_match_evidence": candidate.get("exact_match_evidence", []),
                "semantic_query_provenance": _coerce_string_list(candidate.get("semantic_query_provenance")),
                "vector_query_scores": candidate.get("vector_query_scores", []),
                "vector_best_query": candidate.get("vector_best_query"),
                "vector_similarity": candidate.get("vector_similarity"),
                "graph_direction": candidate.get("graph_direction"),
                "graph_provenance": candidate.get("graph_provenance", []),
                "graph_hop_provenance": candidate.get("graph_hop_provenance", []),
                "wikilink_identity_promotions": candidate.get("wikilink_identity_promotions", []),
                "identity_seed_note_ids": candidate.get("identity_seed_note_ids", []),
                "temporal_mode": candidate.get("temporal_mode"),
                "temporal_anchor_ids": candidate.get("temporal_anchor_ids", []),
                "temporal_provenance": candidate.get("temporal_provenance", []),
                "temporal_subjects": candidate.get("temporal_subjects", []),
                "temporal_governing_anchor_id": candidate.get("temporal_governing_anchor_id"),
                "relation_status": candidate.get("relation_status"),
                "match_reason": str(candidate.get("match_reason") or candidate.get("selection_reason") or ""),
                "scope_match": candidate.get("scope_match"),
                "selection_reason": str(candidate.get("selection_reason") or ""),
                "grounding": candidate.get("grounding", {}),
                "authorized_subjects": candidate.get("authorized_subjects", []),
            }
            for candidate in selected_candidates
        ],
        "matched_chunk_count": len(selected_candidates),
        "retrieval_observation": "matched_chunks" if selected_candidates else "no_matches",
        "assembled_from_traversal_manifest": True,
    }
    traversal_manifest["fusion"] = _fusion_diagnostics(
        merged_candidates=merged_candidates,
        selected_candidates=selected_candidates,
        selection_diagnostics=selection_diagnostics,
        retrieval_packet=retrieval_packet,
    )

    return traversal_manifest, retrieval_packet


def _coverage_report(
    *,
    semantic_compiler_packet: Any,
    semantic_compiler_status: str,
    semantic_compiler_diagnostic: dict[str, Any],
    traversal_manifest: dict[str, Any],
    retrieval_packet: dict[str, Any],
) -> dict[str, Any]:
    compiler_valid = _is_compiler_packet_valid(semantic_compiler_packet)
    selected_count = int(retrieval_packet.get("matched_chunk_count") or 0)
    blocking_reasons: list[str] = []
    if semantic_compiler_status != "parsed":
        blocking_reasons.append(f"semantic compiler status is {semantic_compiler_status}; parsed compiler output is required")
    if not compiler_valid:
        blocking_reasons.append("semantic compiler packet is missing or malformed")
    planner_diagnostics = semantic_compiler_packet.get("planner_diagnostics") if isinstance(semantic_compiler_packet, dict) else {}
    plan_completeness = planner_diagnostics.get("plan_completeness") if isinstance(planner_diagnostics, dict) else None
    if isinstance(plan_completeness, dict) and plan_completeness.get("status") != "complete":
        blocking_reasons.append("compiler-declared evidence requirements are incomplete")
    plan_executability = planner_diagnostics.get("plan_executability") if isinstance(planner_diagnostics, dict) else None
    if isinstance(plan_executability, dict) and plan_executability.get("status") != "executable":
        blocking_reasons.append("semantic compiler produced a non-executable retrieval plan")
        blocking_reasons.extend(str(reason) for reason in plan_executability.get("blocking_reasons") or [] if str(reason).strip())
    bound_plan = traversal_manifest.get("bound_retrieval_plan") if isinstance(traversal_manifest.get("bound_retrieval_plan"), dict) else {}
    layer_manifests = traversal_manifest.get("layer_manifests") if isinstance(traversal_manifest.get("layer_manifests"), dict) else {}
    candidate_counts = traversal_manifest.get("candidate_counts") if isinstance(traversal_manifest.get("candidate_counts"), dict) else {}
    required_layer_failures: list[str] = []
    structured_required_results = traversal_manifest.get("coverage", {}).get("required_layer_results") if isinstance(traversal_manifest.get("coverage"), dict) else None
    if isinstance(structured_required_results, list):
        for result in structured_required_results:
            if isinstance(result, dict) and not bool(result.get("adequate_contribution")):
                required_layer_failures.append(
                    f"required {result.get('source') or result.get('operator')} layer is inadequate: {result.get('blocking_reason') or result.get('status')}"
                )
    else:
        for layer in bound_plan.get("retrieval_layers") or []:
            if not isinstance(layer, dict) or not bool(layer.get("required")):
                continue
            operator = str(layer.get("operator") or "")
            source = LAYER_TO_SOURCE.get(operator)
            if source == "exact":
                exact_status = str((layer_manifests.get("exact") or {}).get("status") or "not_requested")
                if exact_status not in {"completed_no_matches", "completed_with_matches"}:
                    required_layer_failures.append(f"required exact layer is {exact_status}")
            elif source and int(candidate_counts.get(source) or 0) == 0:
                required_layer_failures.append(f"required {source} layer produced no candidates")
    exact_manifest = layer_manifests.get("exact") if isinstance(layer_manifests.get("exact"), dict) else {}
    for term_result in exact_manifest.get("term_results", []) if isinstance(exact_manifest.get("term_results"), list) else []:
        if not isinstance(term_result, dict) or not bool(term_result.get("required")):
            continue
        status = str(term_result.get("status") or "failed")
        if status not in {"completed_no_matches", "completed_with_matches"}:
            required_layer_failures.append(f"required exact literal term {term_result.get('term')!r} was not executed adequately: {status}")
        elif status == "completed_with_matches" and not bool(term_result.get("adequate_contribution")):
            required_layer_failures.append(f"required exact literal term {term_result.get('term')!r} has inadequate selected contribution")
    blocking_reasons.extend(required_layer_failures)
    exact_absence_is_valid = bool(
        isinstance(traversal_manifest.get("coverage"), dict)
        and bool(traversal_manifest["coverage"].get("exact_search_performed"))
        and traversal_manifest["coverage"].get("exact_status") == "completed_no_matches"
        and traversal_manifest["coverage"].get("negative_claims_allowed") is True
        and not required_layer_failures
        and all(
            isinstance(result, dict) and result.get("status") == "completed_no_matches"
            for result in (layer_manifests.get("exact", {}).get("term_results", []) if isinstance(layer_manifests.get("exact"), dict) else [])
        )
    )
    has_retrieval_intent = bool(plan_executability.get("has_retrieval_intent")) if isinstance(plan_executability, dict) else bool(bound_plan.get("retrieval_layers"))
    if selected_count == 0 and has_retrieval_intent and not exact_absence_is_valid:
        blocking_reasons.append("retrieval required but no chunks were selected")
    selection_notes = traversal_manifest.get("selection_notes") if isinstance(traversal_manifest, dict) else []
    if isinstance(selection_notes, list) and any("ingestion database unavailable" in str(note).lower() for note in selection_notes):
        blocking_reasons.append("ingestion database unavailable; run ingest before asking corpus questions")
    return {
        # All coverage and stop-gate policy has already been reduced to this
        # list above.  Do not re-derive intent from retired local names here:
        # doing so both duplicates authority and can crash on zero evidence.
        "decision": "approved" if not blocking_reasons else "blocked",
        "blocking_reasons": blocking_reasons,
        "semantic_compiler_status": semantic_compiler_status,
        "semantic_compiler_response_status": semantic_compiler_diagnostic.get("semantic_compiler_response_status"),
        "semantic_compiler_diagnostic_hash": sha256_json(semantic_compiler_diagnostic),
        "semantic_compiler_packet_hash": sha256_json(semantic_compiler_packet) if compiler_valid else None,
        "semantic_traversal_manifest_hash": sha256_json(traversal_manifest),
        "retrieval_packet_hash": sha256_json(retrieval_packet),
        "selected_chunk_count": selected_count,
        "plan_completeness": plan_completeness,
        "plan_executability": plan_executability,
    }


def _blocked_turn_response(*, blocking_reasons: list[str]) -> str:
    """Return an honest assistant message for a turn that never reached synthesis."""
    reasons = " ".join(str(reason).strip() for reason in blocking_reasons if str(reason).strip()).lower()
    if "non-executable retrieval plan" in reasons:
        return "I couldn't search the vault because the retrieval plan did not contain usable search inputs."
    if "semantic compiler" in reasons:
        return "I couldn't interpret that turn because the semantic compiler is unavailable or returned invalid output."
    if "ingestion database" in reasons:
        return "I couldn't search the vault because the ingestion index is unavailable. Run ingestion, then retry."
    if "no chunks were selected" in reasons:
        return "I couldn't find matching material in the indexed vault. Try rephrasing the request or narrowing its scope."
    if "llm backend unavailable" in reasons or "frontier llm" in reasons:
        return "I prepared the turn, but the frontier agent was unavailable. Please retry when the frontier provider is available."
    return "I couldn't complete that turn. Please retry after checking the runtime diagnostics."


def _build_visible_transcript_tail(messages: list[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
    return messages[-limit:]


def _build_synthesis_traversal_summary(traversal_manifest: dict[str, Any]) -> dict[str, Any]:
    """Project only bounded execution metadata into the frontier packet."""
    layer_manifests = traversal_manifest.get("layer_manifests") if isinstance(traversal_manifest.get("layer_manifests"), dict) else {}
    layer_statuses: dict[str, Any] = {}
    for name in ("exact", "lexical", "vector", "graph", "temporal"):
        layer = layer_manifests.get(name) if isinstance(layer_manifests.get(name), dict) else {}
        if name == "exact":
            layer_statuses[name] = {
                "status": str(layer.get("status") or "not_requested"),
                "returned_candidate_count": int(layer.get("returned_candidate_count") or 0),
                "total_match_count": layer.get("total_match_count"),
                "matching_note_count": layer.get("matching_note_count"),
            }
        else:
            layer_statuses[name] = {
                "status": str(layer.get("status") or "not_requested"),
                "candidate_count": int(layer.get("candidate_count") or 0),
            }
    fusion = traversal_manifest.get("fusion") if isinstance(traversal_manifest.get("fusion"), dict) else {}
    reservation = fusion.get("required_reservation") if isinstance(fusion.get("required_reservation"), dict) else {}
    full_coverage = traversal_manifest.get("coverage") if isinstance(traversal_manifest.get("coverage"), dict) else {}
    coverage = {
        key: full_coverage.get(key)
        for key in (
            "exact_search_performed", "exact_status", "total_exact_matches", "matching_note_count",
            "total_occurrence_count", "count_status", "coverage_claims_allowed",
            "negative_claims_allowed", "negative_claims_require_exact_layer", "required_layer_results",
        )
        if key in full_coverage
    }
    return {
        "execution": {
            "layers_executed": list((traversal_manifest.get("execution") or {}).get("layers_executed") or []),
            "layers_skipped": list((traversal_manifest.get("execution") or {}).get("layers_skipped") or []),
        },
        "candidate_counts": dict(traversal_manifest.get("candidate_counts") or {}),
        "selected_counts": dict(traversal_manifest.get("selected_counts") or {}),
        "coverage": coverage,
        "limits": list(traversal_manifest.get("limits") or []),
        "plan_completeness": dict(traversal_manifest.get("plan_completeness") or {}),
        "plan_executability": dict(traversal_manifest.get("plan_executability") or {}),
        "unsupported_layer_requests": [
            {
                "operator": str(item.get("operator") or ""),
                "required": bool(item.get("required")),
                "reason": str(item.get("reason") or ""),
            }
            for item in traversal_manifest.get("unsupported_layer_requests") or []
            if isinstance(item, dict)
        ],
        "layer_statuses": layer_statuses,
        "fusion_summary": {
            "selector": str(fusion.get("selector") or ""),
            "candidate_count": int(fusion.get("candidate_count") or 0),
            "selected_count": int(fusion.get("selected_count") or 0),
            "selected_unique_note_count": int(fusion.get("selected_unique_note_count") or 0),
            "max_selected_chunks_per_note": int(fusion.get("max_selected_chunks_per_note") or 0),
            "required_reservation": reservation,
        },
    }


def _build_synthesis_context_packet(
    *,
    thread_id: str,
    turn_id: int,
    raw_user_input: str,
    prior_thread_state: dict[str, Any],
    visible_transcript_tail: list[dict[str, Any]],
    semantic_compiler_packet: dict[str, Any],
    synthesis_traversal_summary: dict[str, Any],
    approved_retrieval_packet: dict[str, Any] | None,
    coverage_report: dict[str, Any],
    runtime_outcome: str,
    blocking_reasons: list[str],
) -> dict[str, Any]:
    return {
        "thread_id": thread_id,
        "turn_id": turn_id,
        "raw_user_input": raw_user_input,
        "prior_thread_state": prior_thread_state,
        "visible_transcript_tail": visible_transcript_tail,
        "semantic_compiler_packet": semantic_compiler_packet,
        "synthesis_traversal_summary": synthesis_traversal_summary,
        "approved_retrieval_packet": approved_retrieval_packet,
        "coverage_report": coverage_report,
        "runtime_outcome": runtime_outcome,
        "blocking_reasons": blocking_reasons,
        "output_requirements": [
            "Answer directly. Use only the coverage-approved retrieval packet when coverage is approved.",
            "Do not describe retrieved notes as independently approved, verified, or authoritative.",
            "Do not make corpus-wide negative claims unless retrieval coverage says negative_claims_allowed is true.",
        ],
    }


def _append_turn_message(
    *,
    messages: list[dict[str, Any]],
    role: str,
    content: str,
    turn_id: int,
) -> None:
    messages.append(
        {
            "role": role,
            "content": content,
            "turn_id": turn_id,
            "created_at": _utc_now(),
        }
    )


def _build_state_delta(
    *,
    thread_id: str,
    turn_id: int,
    raw_user_input: str,
    assistant_response: str | None,
    runtime_outcome: str,
    blocking_reasons: list[str],
    semantic_compiler_status: str,
    coverage_report: dict[str, Any],
    prior_thread_state_hash: str | None,
    next_thread_state_hash: str,
) -> dict[str, Any]:
    return {
        "thread_id": thread_id,
        "turn_id": turn_id,
        "raw_user_input": raw_user_input,
        "assistant_response": assistant_response,
        "runtime_outcome": runtime_outcome,
        "blocking_reasons": blocking_reasons,
        "semantic_compiler_status": semantic_compiler_status,
        "coverage_decision": coverage_report.get("decision"),
        "prior_thread_state_hash": prior_thread_state_hash,
        "next_thread_state_hash": next_thread_state_hash,
    }


def _build_ledger_record(
    *,
    thread_id: str,
    turn_id: int,
    runtime_outcome: str,
    blocking_reasons: list[str],
    llm_metadata: dict[str, Any],
    llm_synthesis_attempted: bool,
    semantic_compiler_status: str,
    semantic_compiler_packet: dict[str, Any],
    semantic_compiler_diagnostic: dict[str, Any],
    semantic_traversal_manifest: dict[str, Any],
    retrieval_packet: dict[str, Any],
    coverage_report: dict[str, Any],
    synthesis_context_packet: dict[str, Any],
    state_delta: dict[str, Any],
    conversation_thread: dict[str, Any],
    thread_state: dict[str, Any],
    parent_perturbation_hash: str | None,
) -> dict[str, Any]:
    llm_call_metadata = {
        # A blocked coverage turn has no frontier synthesis to account for.
        # Keep that distinct from a synthesis attempt that reached a provider
        # and failed before it could return usage telemetry.
        "synthesis_status": (
            "failed"
            if llm_synthesis_attempted and llm_metadata.get("error")
            else "completed"
            if llm_synthesis_attempted
            else "not_attempted"
        ),
        "provider": llm_metadata.get("provider"),
        "model": llm_metadata.get("model"),
        "response_id": llm_metadata.get("response_id"),
        "reasoning_effort": llm_metadata.get("reasoning_effort"),
        "usage": llm_metadata.get("usage"),
        "error": llm_metadata.get("error"),
    }
    return {
        "thread_id": thread_id,
        "turn_id": turn_id,
        "runtime_outcome": runtime_outcome,
        "blocking_reasons": blocking_reasons,
        "llm_mode": llm_metadata.get("mode"),
        "llm_call_metadata": llm_call_metadata,
        "semantic_compiler_status": semantic_compiler_status,
        "parent_perturbation_hash": parent_perturbation_hash,
        "state_perturbation_hash": sha256_json(state_delta),
        "semantic_compiler_packet_hash": sha256_json(semantic_compiler_packet),
        "semantic_compiler_diagnostic_hash": sha256_json(semantic_compiler_diagnostic),
        "semantic_traversal_manifest_hash": sha256_json(semantic_traversal_manifest),
        "retrieval_packet_hash": sha256_json(retrieval_packet),
        "coverage_report_hash": sha256_json(coverage_report),
        "synthesis_context_packet_hash": sha256_json(synthesis_context_packet),
        "state_delta_hash": sha256_json(state_delta),
        "conversation_thread_hash": sha256_json(conversation_thread),
        "thread_state_hash": thread_state["latest_thread_state_hash"],
    }


def _update_thread_state(
    *,
    prior_thread_state: dict[str, Any],
    thread_id: str,
    turn_id: int,
    raw_user_input: str,
    assistant_response: str | None,
    semantic_compiler_packet: dict[str, Any],
    retrieval_packet: dict[str, Any],
    created_at: str,
    config: RuntimeConfig,
) -> dict[str, Any]:
    planner_retrieval_plan = semantic_compiler_packet.get("planner_retrieval_plan") if isinstance(semantic_compiler_packet.get("planner_retrieval_plan"), dict) else {}
    recent_messages = _ensure_message_list(prior_thread_state.get("recent_messages"))
    _append_turn_message(messages=recent_messages, role="user", content=raw_user_input, turn_id=turn_id)
    if assistant_response is not None:
        _append_turn_message(messages=recent_messages, role="assistant", content=assistant_response, turn_id=turn_id)
    recent_messages = recent_messages[-int(config.runtime_conversation["recent_message_limit"]):]
    recent_semantic_turns = _recent_semantic_turns_from_state(prior_thread_state.get("recent_semantic_turns"), config=config)
    recent_semantic_turns.append(
        _build_recent_semantic_turn(
            turn_id=turn_id,
            raw_user_input=raw_user_input,
            assistant_response=assistant_response,
            planner_retrieval_plan=planner_retrieval_plan,
            retrieval_packet=retrieval_packet,
            config=config,
        )
    )
    recent_semantic_turns = recent_semantic_turns[-int(config.runtime_conversation["recent_semantic_turn_limit"]):]
    active_focus = _compact_active_focus(
        planner_retrieval_plan=planner_retrieval_plan,
        retrieval_packet=retrieval_packet,
    )
    thread_state = {
        "thread_id": thread_id,
        "latest_turn_id": turn_id,
        "conversation_summary": str(prior_thread_state.get("conversation_summary") or ""),
        "recent_messages": recent_messages,
        "recent_semantic_turns": recent_semantic_turns,
        "active_focus": active_focus,
        "latest_user_input": raw_user_input,
        "latest_assistant_response": assistant_response,
        "updated_at": created_at,
        "latest_thread_state_hash": None,
    }
    thread_state["latest_thread_state_hash"] = _thread_state_hash(thread_state)
    return thread_state


def _build_conversation_thread(
    *,
    prior_thread_document: dict[str, Any],
    turn_id: int,
    raw_user_input: str,
    assistant_response: str | None,
    thread_state_hash: str,
    perturbation_hash: str,
    created_at: str,
) -> dict[str, Any]:
    messages = _ensure_message_list(prior_thread_document.get("messages"))
    _append_turn_message(messages=messages, role="user", content=raw_user_input, turn_id=turn_id)
    if assistant_response is not None:
        _append_turn_message(messages=messages, role="assistant", content=assistant_response, turn_id=turn_id)
    return {
        "thread_id": str(prior_thread_document.get("thread_id") or ""),
        "created_at": str(prior_thread_document.get("created_at") or created_at),
        "updated_at": created_at,
        "turn_count": turn_id,
        "latest_turn_id": turn_id,
        "latest_thread_state_hash": thread_state_hash,
        "latest_perturbation_hash": perturbation_hash,
        "messages": messages,
    }


def _resolve_thread_state(
    *,
    thread_id: str | None,
    data_root: Path,
    config: RuntimeConfig,
) -> tuple[dict[str, Any], dict[str, Any], Path, Path, Path]:
    thread_paths = create_thread_paths(data_root, config=config, thread_id=thread_id)
    created_at = _utc_now()
    conversation_thread_path = thread_paths.conversation_thread_path
    thread_state_path = thread_paths.thread_state_path
    thread_ledger_path = thread_paths.thread_ledger_path

    conversation_thread = load_json(conversation_thread_path) or _default_conversation_thread(thread_paths.thread_id, created_at)
    thread_state = load_json(thread_state_path) or _default_thread_state(thread_paths.thread_id, created_at)
    return conversation_thread, thread_state, conversation_thread_path, thread_state_path, thread_ledger_path


def _load_ingestion_database_path(*, data_root: Path, config: RuntimeConfig) -> Path:
    return _resolve_data_path(data_root, config.storage_ingestion_root) / config.storage_ingestion_database_filename


def run_thread_turn(
    *,
    repo_root: Path,
    data_root: Path,
    user_input: str,
    llm_backend: LLMBackend,
    thread_id: str | None = None,
    config: RuntimeConfig | None = None,
    semantic_compiler_backend: SemanticCompilerBackend | None = None,
    embedding_backend: EmbeddingBackend | None = None,
) -> TurnExecutionResult:
    resolved_repo_root = repo_root.resolve()
    resolved_config = config or load_runtime_config(repo_root=resolved_repo_root)
    resolved_data_root = data_root.resolve()

    conversation_thread, prior_thread_state, conversation_thread_path, thread_state_path, thread_ledger_path = _resolve_thread_state(
        thread_id=thread_id,
        data_root=resolved_data_root,
        config=resolved_config,
    )
    recent_messages = _ensure_message_list(prior_thread_state.get("recent_messages"))
    recent_semantic_turns = _recent_semantic_turns_from_state(prior_thread_state.get("recent_semantic_turns"), config=resolved_config)
    active_focus = _normalize_active_focus(prior_thread_state.get("active_focus"))
    prior_thread_state = dict(prior_thread_state)
    prior_thread_state["recent_messages"] = recent_messages
    prior_thread_state["recent_semantic_turns"] = recent_semantic_turns
    prior_thread_state["active_focus"] = active_focus
    thread_id_value = str(conversation_thread.get("thread_id") or prior_thread_state.get("thread_id") or thread_state_path.parent.name)
    parent_perturbation_hash = conversation_thread.get("latest_perturbation_hash")
    turn_id = int(prior_thread_state.get("latest_turn_id") or 0) + 1
    created_at = _utc_now()
    turn_paths = create_thread_paths(resolved_data_root, config=resolved_config, thread_id=thread_id_value)
    turn_root = turn_paths.turn_root(turn_id)
    turn_root.mkdir(parents=True, exist_ok=True)

    database_path = _load_ingestion_database_path(data_root=resolved_data_root, config=resolved_config)
    resource_inventory_summary: dict[str, Any] = {"configured_source_labels": [resolved_config.vault_source_label], "observed_source_labels": [], "frontmatter_facets": {"note_type": []}, "path_topology": {"top_level": [], "second_level": []}, "graph_capabilities": {"nodes_table_present": False, "edges_table_present": False, "node_count": 0, "edge_count": 0}, "inventory_diagnostics": {"source": "config_only_fallback", "status": "unavailable", "snapshot_load_count": 0, "full_inventory_rebuilds": 0}}
    if database_path.exists():
        try:
            with sqlite3.connect(database_path) as inventory_connection:
                resource_inventory_summary, _ = load_persisted_inventory(connection=inventory_connection, config=resolved_config)
        except sqlite3.Error:
            resource_inventory_summary = build_resource_inventory(connection=None, config=resolved_config)

    full_resource_inventory_summary = resource_inventory_summary
    compiler_inventory_projection, inventory_projection_diagnostics = build_compiler_inventory_projection(
        inventory_summary=full_resource_inventory_summary,
        config=resolved_config,
    )
    compiler_request = _compiler_request_packet(
        raw_user_input=user_input,
        prior_thread_state=prior_thread_state,
        recent_messages=recent_messages,
        recent_semantic_turns=recent_semantic_turns,
        active_focus=active_focus,
        resource_inventory_summary=compiler_inventory_projection,
    )
    compiler_request["inventory_projection_diagnostics"] = inventory_projection_diagnostics
    compiler_backend = semantic_compiler_backend or resolve_semantic_compiler_backend(config=resolved_config)
    try:
        compiler_response = compiler_backend.compile_turn(compiler_request)
    except Exception as exc:  # noqa: BLE001
        compiler_response = SemanticCompilerResponse(
            parsed_payload=None,
            raw_response=None,
            metadata={"backend_mode": getattr(compiler_backend, "mode_name", "unknown"), "error": str(exc)},
            diagnostics={},
            status="unavailable",
        )

    semantic_compiler_packet, semantic_compiler_status = _compiler_response_to_packet(
        raw_user_input=user_input,
        prior_thread_state=prior_thread_state,
        active_focus=active_focus,
        recent_semantic_turns=recent_semantic_turns,
        response=compiler_response,
        config=resolved_config,
    )
    planner_for_completeness = semantic_compiler_packet.get("planner_retrieval_plan") if isinstance(semantic_compiler_packet.get("planner_retrieval_plan"), dict) else {}
    initial_completeness = validate_plan_completeness(planner_retrieval_plan=planner_for_completeness, config=resolved_config)
    semantic_compiler_packet.setdefault("planner_diagnostics", {})["plan_completeness"] = initial_completeness
    repair_record = {"attempted": False, "outcome": "not_needed", "latency_ms": 0}
    if initial_completeness.get("status") != "complete" and compiler_response.status == "parsed":
        semantic_compiler_packet, semantic_compiler_status, compiler_response, repair_record = _repair_incomplete_compiler_plan(
            raw_user_input=user_input,
            compiler_request=compiler_request,
            compiler_backend=compiler_backend,
            compiler_response=compiler_response,
            semantic_compiler_packet=semantic_compiler_packet,
            prior_thread_state=prior_thread_state,
            active_focus=active_focus,
            recent_semantic_turns=recent_semantic_turns,
            config=resolved_config,
            resource_inventory_summary=full_resource_inventory_summary,
        )
    semantic_compiler_diagnostic = _semantic_compiler_diagnostic_packet(
        response=compiler_response,
        semantic_compiler_status=semantic_compiler_status,
        config=resolved_config,
        inventory_projection_diagnostics=inventory_projection_diagnostics,
    )
    semantic_compiler_diagnostic["plan_repair"] = repair_record
    planner_retrieval_plan = semantic_compiler_packet.get("planner_retrieval_plan") if isinstance(semantic_compiler_packet.get("planner_retrieval_plan"), dict) else {}
    if not planner_retrieval_plan:
        planner_retrieval_plan = build_default_retrieval_plan(
            raw_user_input=user_input,
            query=str(semantic_compiler_packet.get("query") or ""),
            concepts=_coerce_string_list(semantic_compiler_packet.get("concepts")),
            graph_seeds=_coerce_string_list(semantic_compiler_packet.get("graph_seeds")),
            resolved_referents=_coerce_string_list(semantic_compiler_packet.get("resolved_referents")),
            planner_defaults=resolved_config.retrieval_planner_defaults,
        )
    plan_executability = validate_plan_executability(planner_retrieval_plan=planner_retrieval_plan, config=resolved_config)
    semantic_compiler_packet.setdefault("planner_diagnostics", {})["plan_executability"] = plan_executability
    # The database-backed path binds inside _semantic_traversal, where the same
    # live inventory is already loaded. Only bind here for the no-database
    # diagnostic path so resolver work is not performed twice per turn.
    bound_retrieval_plan = planner_retrieval_plan
    resolver_adjustments: list[dict[str, Any]] = []

    final_completeness = semantic_compiler_packet.get("planner_diagnostics", {}).get("plan_completeness", {})
    if final_completeness.get("status") != "complete":
        semantic_traversal_manifest, retrieval_packet = _incomplete_plan_artifacts(
            semantic_compiler_packet=semantic_compiler_packet,
            config=resolved_config,
            resource_inventory_summary=resource_inventory_summary,
            prior_thread_state=prior_thread_state,
        )
    elif plan_executability.get("status") != "executable":
        semantic_traversal_manifest, retrieval_packet = _non_executable_plan_artifacts(
            semantic_compiler_packet=semantic_compiler_packet,
            config=resolved_config,
            resource_inventory_summary=resource_inventory_summary,
            plan_executability=plan_executability,
        )
    elif database_path.exists():
        if embedding_backend is None:
            embedding_backend = resolve_embedding_backend(resolved_config)
        connection = sqlite3.connect(database_path)
        try:
            connection.row_factory = sqlite3.Row
            semantic_traversal_manifest, retrieval_packet = _semantic_traversal(
                connection=connection,
                config=resolved_config,
                semantic_compiler_packet=semantic_compiler_packet,
                prior_thread_state=prior_thread_state,
                embedding_backend=embedding_backend,
                resource_inventory_summary=resource_inventory_summary,
            )
        finally:
            connection.close()
    else:
        bound_retrieval_plan, resolver_adjustments, resource_inventory_summary = bind_retrieval_plan(
            planner_retrieval_plan=planner_retrieval_plan,
            inventory_summary=resource_inventory_summary,
            config=resolved_config,
            raw_user_input=user_input,
        )
        semantic_traversal_manifest = {
            "planner_retrieval_plan": planner_retrieval_plan,
            "bound_retrieval_plan": bound_retrieval_plan,
            "resolver_adjustments": resolver_adjustments,
            "resource_inventory_summary": resource_inventory_summary,
            "inventory_diagnostics": resource_inventory_summary.get("inventory_diagnostics", {}),
            "execution": {"layers_executed": [], "layers_skipped": []},
            "candidate_counts": {"exact": 0, "lexical": 0, "vector": 0, "graph": 0},
            "selected_counts": {"exact": 0, "lexical": 0, "vector": 0, "graph": 0},
            "selected_chunk_ids": [],
            "selection_notes": ["ingestion database unavailable"],
            "coverage": {
                "exact_search_performed": False,
                "scope": {},
                "literal_terms": [],
                "total_exact_matches": 0,
                "matching_note_count": 0,
                "coverage_claims_allowed": False,
                "negative_claims_allowed": False,
            },
            "limits": ["No ingestion database available."],
        }
        retrieval_packet = {
            "bound_retrieval_plan": bound_retrieval_plan,
            "coverage": semantic_traversal_manifest["coverage"],
            "limits": semantic_traversal_manifest["limits"],
            "selected_chunks": [],
            "matched_chunk_count": 0,
            "retrieval_observation": "no_matches",
            "assembled_from_traversal_manifest": True,
        }

    coverage_report = _coverage_report(
        semantic_compiler_packet=semantic_compiler_packet,
        semantic_compiler_status=semantic_compiler_status,
        semantic_compiler_diagnostic=semantic_compiler_diagnostic,
        traversal_manifest=semantic_traversal_manifest,
        retrieval_packet=retrieval_packet,
    )

    blocking_reasons = list(coverage_report.get("blocking_reasons") or [])
    llm_unavailable_reason = getattr(llm_backend, "unavailable_reason", None)
    if coverage_report["decision"] == "approved" and llm_unavailable_reason:
        blocking_reasons.append(f"LLM backend unavailable: {llm_unavailable_reason}")
    runtime_outcome = "completed" if coverage_report["decision"] == "approved" and not blocking_reasons else "blocked"
    approved_retrieval_packet = retrieval_packet if runtime_outcome == "completed" else None
    visible_transcript_tail = _build_visible_transcript_tail(
        _ensure_message_list(conversation_thread.get("messages")),
        limit=int(resolved_config.runtime_conversation["visible_transcript_tail_limit"]),
    )
    synthesis_context_packet = _build_synthesis_context_packet(
        thread_id=thread_id_value,
        turn_id=turn_id,
        raw_user_input=user_input,
        prior_thread_state=prior_thread_state,
        visible_transcript_tail=visible_transcript_tail,
        semantic_compiler_packet=semantic_compiler_packet,
        synthesis_traversal_summary=_build_synthesis_traversal_summary(semantic_traversal_manifest),
        approved_retrieval_packet=approved_retrieval_packet,
        coverage_report=coverage_report,
        runtime_outcome=runtime_outcome,
        blocking_reasons=blocking_reasons,
    )
    assistant_response_text: str | None = None
    llm_metadata: dict[str, Any] = {
        "mode": getattr(llm_backend, "mode_name", "unknown"),
    }
    llm_synthesis_attempted = False
    if runtime_outcome == "completed":
        try:
            llm_synthesis_attempted = True
            llm_response = llm_backend.generate(synthesis_context_packet)
            assistant_response_text = llm_response.assistant_response
            llm_metadata = dict(llm_response.metadata)
        except Exception as exc:  # noqa: BLE001
            # Persist the submitted turn and its diagnostics even when the
            # frontier provider fails. The caller can retry the same thread.
            blocking_reasons.append(f"frontier LLM failed: {type(exc).__name__}: {exc}")
            runtime_outcome = "blocked"
            assistant_response_text = _blocked_turn_response(blocking_reasons=blocking_reasons)
            describe_call = getattr(llm_backend, "describe_call", None)
            llm_metadata = dict(describe_call()) if callable(describe_call) else {
                "mode": getattr(llm_backend, "mode_name", "unknown"),
            }
            llm_metadata["error"] = f"{type(exc).__name__}: {exc}"
            approved_retrieval_packet = None
            synthesis_context_packet = _build_synthesis_context_packet(
                thread_id=thread_id_value,
                turn_id=turn_id,
                raw_user_input=user_input,
                prior_thread_state=prior_thread_state,
                visible_transcript_tail=visible_transcript_tail,
                semantic_compiler_packet=semantic_compiler_packet,
                synthesis_traversal_summary=_build_synthesis_traversal_summary(semantic_traversal_manifest),
                approved_retrieval_packet=None,
                coverage_report=coverage_report,
                runtime_outcome=runtime_outcome,
                blocking_reasons=blocking_reasons,
            )
    else:
        assistant_response_text = _blocked_turn_response(blocking_reasons=blocking_reasons)

    thread_state = _update_thread_state(
        prior_thread_state=prior_thread_state,
        thread_id=thread_id_value,
        turn_id=turn_id,
        raw_user_input=user_input,
        assistant_response=assistant_response_text,
        semantic_compiler_packet=semantic_compiler_packet,
        retrieval_packet=retrieval_packet,
        created_at=created_at,
        config=resolved_config,
    )

    conversation_thread = _build_conversation_thread(
        prior_thread_document=conversation_thread,
        turn_id=turn_id,
        raw_user_input=user_input,
        assistant_response=assistant_response_text,
        thread_state_hash=thread_state["latest_thread_state_hash"],
        perturbation_hash="",
        created_at=created_at,
    )
    state_delta = _build_state_delta(
        thread_id=thread_id_value,
        turn_id=turn_id,
        raw_user_input=user_input,
        assistant_response=assistant_response_text,
        runtime_outcome=runtime_outcome,
        blocking_reasons=blocking_reasons,
        semantic_compiler_status=semantic_compiler_status,
        coverage_report=coverage_report,
        prior_thread_state_hash=prior_thread_state.get("latest_thread_state_hash"),
        next_thread_state_hash=thread_state["latest_thread_state_hash"],
    )
    state_delta_hash = sha256_json(state_delta)
    conversation_thread["latest_perturbation_hash"] = state_delta_hash
    conversation_thread["latest_thread_state_hash"] = thread_state["latest_thread_state_hash"]

    ledger_record = _build_ledger_record(
        thread_id=thread_id_value,
        turn_id=turn_id,
        runtime_outcome=runtime_outcome,
        blocking_reasons=blocking_reasons,
        llm_metadata=llm_metadata,
        llm_synthesis_attempted=llm_synthesis_attempted,
        semantic_compiler_status=semantic_compiler_status,
        semantic_compiler_packet=semantic_compiler_packet,
        semantic_compiler_diagnostic=semantic_compiler_diagnostic,
        semantic_traversal_manifest=semantic_traversal_manifest,
        retrieval_packet=retrieval_packet,
        coverage_report=coverage_report,
        synthesis_context_packet=synthesis_context_packet,
        state_delta=state_delta,
        conversation_thread=conversation_thread,
        thread_state=thread_state,
        parent_perturbation_hash=parent_perturbation_hash,
    )
    ledger_record["state_perturbation_hash"] = state_delta_hash
    ledger_record["state_delta_hash"] = state_delta_hash

    semantic_compiler_packet_path = turn_root / "semantic_compiler_packet.json"
    semantic_compiler_diagnostic_path = turn_root / "semantic_compiler_diagnostic.json"
    semantic_traversal_manifest_path = turn_root / "semantic_traversal_manifest.json"
    retrieval_packet_path = turn_root / "retrieval_packet.json"
    coverage_report_path = turn_root / "coverage_report.json"
    synthesis_context_packet_path = turn_root / "synthesis_context_packet.json"
    state_delta_path = turn_root / "state_delta.json"

    write_json(semantic_compiler_packet_path, semantic_compiler_packet)
    write_json(semantic_compiler_diagnostic_path, semantic_compiler_diagnostic)
    write_json(semantic_traversal_manifest_path, semantic_traversal_manifest)
    write_json(retrieval_packet_path, retrieval_packet)
    write_json(coverage_report_path, coverage_report)
    write_json(synthesis_context_packet_path, synthesis_context_packet)
    write_json(state_delta_path, state_delta)

    write_json(conversation_thread_path, conversation_thread)
    write_json(thread_state_path, thread_state)
    append_ledger_record(thread_ledger_path, ledger_record)

    return TurnExecutionResult(
        thread_id=thread_id_value,
        turn_id=turn_id,
        thread_root=turn_paths.thread_root,
        turn_root=turn_root,
        conversation_thread_path=conversation_thread_path,
        thread_state_path=thread_state_path,
        thread_ledger_path=thread_ledger_path,
        semantic_compiler_packet_path=semantic_compiler_packet_path,
        semantic_compiler_diagnostic_path=semantic_compiler_diagnostic_path,
        semantic_traversal_manifest_path=semantic_traversal_manifest_path,
        retrieval_packet_path=retrieval_packet_path,
        coverage_report_path=coverage_report_path,
        synthesis_context_packet_path=synthesis_context_packet_path,
        state_delta_path=state_delta_path,
        assistant_response=assistant_response_text,
        llm_metadata=llm_metadata,
        runtime_outcome=runtime_outcome,
        blocking_reasons=blocking_reasons,
        prior_thread_state=prior_thread_state,
        next_thread_state=thread_state,
        ledger_record=ledger_record,
        semantic_compiler_status=semantic_compiler_status,
        semantic_compiler_packet=semantic_compiler_packet,
        semantic_compiler_diagnostic=semantic_compiler_diagnostic,
        semantic_traversal_manifest=semantic_traversal_manifest,
        retrieval_packet=retrieval_packet,
        coverage_report=coverage_report,
        synthesis_context_packet=synthesis_context_packet,
        state_delta=state_delta,
    )
