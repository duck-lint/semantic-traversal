from __future__ import annotations

import json
import math
import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config import RuntimeConfig, load_runtime_config
from .embeddings import EmbeddingBackend, resolve_embedding_backend
from .hashing import sha256_json, sha256_text
from .llm import LLMBackend
from .resource_inventory import build_resource_inventory
from .retrieval_plan import build_default_retrieval_plan, canonicalize_retrieval_plan, coerce_string_list, is_comparison_intent, retrieval_plan_layer, scope_requests_from_text, _focus_carry_terms
from .retrieval_resolver import bind_retrieval_plan
from .text_filters import is_low_signal_apparatus_text
from .semantic_compiler import (
    INTERNAL_COMPILER_ECHO_FIELDS,
    SemanticCompilerBackend,
    SemanticCompilerResponse,
    resolve_semantic_compiler_backend,
)
from .storage import append_ledger_record, create_thread_paths, load_json, write_json


QUERY_TOKEN_RE = re.compile(r"[A-Za-z0-9']+")
LAYER_TO_SOURCE = {"exact_chunk_search": "exact", "lexical_chunk_search": "lexical", "vector_search": "vector", "graph_expand": "graph", "graph_lookup": "graph", "graph_paths": "graph", "graph_neighbors": "graph", "graph_from_results": "graph"}
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
        "scope_requests": [],
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
                "scope_requests": _coerce_string_list(entry.get("scope_requests")),
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
    focus["scope_requests"] = _coerce_string_list(value.get("scope_requests"))
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
        "scope_requests": _coerce_string_list(planner_retrieval_plan.get("scope_requests")),
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
        "scope_requests": _coerce_string_list(planner_retrieval_plan.get("scope_requests")),
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
        "scope_aliases": resource_inventory_summary.get("scope_aliases", {}),
        "instruction": "Compile a soft retrieval plan, not an answer. Emit scope requests and concepts only; do not emit note_type, path_contains, source_label, planner_diagnostics, or any other executable filters.",
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
    scope_requests = ["journal"] if any(term in concepts for term in ("journal", "journaled", "daily", "dailies", "entries", "entry")) else []
    resolved_referents: list[str] = []
    carry_focus_terms = _is_referential_user_input(raw_user_input, config=config) or is_comparison_intent(raw_user_input)
    if carry_focus_terms:
        focus_terms = _focus_carry_terms(active_focus=active_focus, recent_semantic_turns=recent_semantic_turns)
        resolved_referents = list(dict.fromkeys(term for term in focus_terms if term))
        if resolved_referents:
            concepts = list(dict.fromkeys([*concepts, *resolved_referents]))
            query = " ".join([query, *resolved_referents[:8]]).strip() or query
    graph_seeds: list[str] = [query] if query else []
    if prior_thread_state.get("latest_user_input"):
        graph_seeds.append(str(prior_thread_state["latest_user_input"]).strip())
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
        scope_requests=scope_requests,
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
    focus_terms: list[str] = []
    carry_focus_terms = _is_referential_user_input(raw_user_input, config=config) or is_comparison_intent(raw_user_input)
    if carry_focus_terms:
        focus_terms = _focus_carry_terms(active_focus=active_focus, recent_semantic_turns=recent_semantic_turns)
    if carry_focus_terms and focus_terms:
        packet["resolved_referents"] = list(dict.fromkeys([*packet["resolved_referents"], *focus_terms]))
    fallback_plan = build_default_retrieval_plan(
        raw_user_input=raw_user_input,
        query=packet["query"],
        concepts=_extract_terms(packet["query"], config=config),
        scope_requests=scope_requests_from_text(packet["query"]),
        graph_seeds=[packet["query"]] if packet["query"].strip() else [],
        resolved_referents=list(packet["resolved_referents"]),
        planner_defaults=config.retrieval_planner_defaults,
    )
    canonical_plan, planner_diagnostics = canonicalize_retrieval_plan(payload.get("planner_retrieval_plan"), fallback=fallback_plan, planner_defaults=config.retrieval_planner_defaults, raw_user_input=raw_user_input)
    if carry_focus_terms and focus_terms:
        canonical_plan["resolved_referents"] = list(dict.fromkeys([*canonical_plan.get("resolved_referents", []), *packet["resolved_referents"]]))
        comparison_query = " ".join([packet["query"], *focus_terms[:6]]).strip()
        if comparison_query and comparison_query not in canonical_plan["semantic_queries"]:
            canonical_plan["semantic_queries"].append(comparison_query)
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


def _semantic_compiler_diagnostic_packet(
    *,
    response: SemanticCompilerResponse,
    semantic_compiler_status: str,
    config: RuntimeConfig,
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
    rows = connection.execute(
        """
        SELECT
            chunk_id,
            note_id,
            source_root_label,
            source_root_path,
            relative_path,
            note_title,
            frontmatter_semantics_json,
            section_label,
            paragraph_text,
            chunk_hash
        FROM chunks
        """
    ).fetchall()
    return [dict(row) for row in rows]


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
    preferred_scope_filters: dict[str, Any],
) -> list[dict[str, Any]]:
    """Expose scope evidence without turning preferred scope into admission."""
    annotated: list[dict[str, Any]] = []
    preferred_scope_active = bool(
        str(preferred_scope_filters.get("source_label") or "").strip()
        or _scope_values(preferred_scope_filters, "note_type")
        or _scope_values(preferred_scope_filters, "path_contains")
    )
    for candidate in candidates:
        next_candidate = dict(candidate)
        if "scope_match" not in next_candidate:
            next_candidate["scope_match"] = hard_scope_filters
        next_candidate["preferred_scope_match"] = (
            preferred_scope_active
            and _chunk_matches_scope(next_candidate, preferred_scope_filters)
        )
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
    return [source for source in ("exact", "lexical", "vector", "graph") if source in observed]


def _count_selected_by_layer(selected_candidates: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"exact": 0, "lexical": 0, "vector": 0, "graph": 0}
    for candidate in selected_candidates:
        for source in _candidate_source_layers(candidate):
            if source in counts:
                counts[source] += 1
    return counts


def _exact_candidates(
    chunk_rows: list[dict[str, Any]],
    literal_terms: list[dict[str, Any]],
    *,
    scope_filters: dict[str, Any],
    limit: int,
    return_total_count: bool = False,
    config: RuntimeConfig,
) -> tuple[list[dict[str, Any]], list[str], dict[str, Any]]:
    fields = ("note_title", "section_label", "relative_path", "paragraph_text")
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
            term_occurrences: list[tuple[str, int, int]] = []
            for field in fields:
                value = str(row.get(field) or "")
                for start, end in occurrences(value, term, entry["match"]):
                    term_occurrences.append((field, start, end))
            if not term_occurrences:
                continue
            matched_terms.append(term)
            result = per_term[term]
            result["status"] = "completed_with_matches"
            result["matching_chunk_count"] += 1
            result["matching_note_count"] = None
            result["total_occurrence_count"] += len(term_occurrences)
            total_occurrence_count += len(term_occurrences)
            for field, start, end in term_occurrences:
                representative = context(str(row.get(field) or ""), start, end)
                representative["field"] = field
                evidence.append({
                    "term": term,
                    "match": entry["match"],
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
            occurrences(str(row.get(field) or ""), term, next(entry["match"] for entry in term_entries if entry["term"] == term))
            for field in fields
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
    prior_thread_state: dict[str, Any],
    config: RuntimeConfig,
) -> list[tuple[str, str]]:
    seeds: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    active_focus = _normalize_active_focus(prior_thread_state.get("active_focus"))
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
    if "active_focus" in configured_sources:
        for value in (
            active_focus.get("query"),
            active_focus.get("concepts"),
            active_focus.get("scope_requests"),
            active_focus.get("resolved_referents"),
            active_focus.get("semantic_queries"),
            active_focus.get("lexical_queries"),
            active_focus.get("graph_seeds"),
            active_focus.get("selected_note_titles"),
            active_focus.get("selected_section_labels"),
        ):
            for item in _coerce_string_list(value):
                seed = ("active_focus", item)
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
        "effective_query": None,
        "requested_mode": mode,
        "effective_mode": None,
        "fts_available": None,
        "candidate_count": 0,
    }
    def finish() -> tuple[list[dict[str, Any]], list[str]] | tuple[list[dict[str, Any]], list[str], dict[str, Any]]:
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
    if selected_mode == "exact_phrase":
        fts_query = '"' + " ".join(terms).replace('"', '""') + '"'
    else:
        tokens = [token for term in terms for token in QUERY_TOKEN_RE.findall(term.lower())]
        if selected_mode == "prefix":
            tokens = [f"{token}*" for token in tokens]
        joiner = " AND " if selected_mode == "all_tokens" else " OR "
        fts_query = joiner.join(tokens)
    if not fts_query:
        diagnostics["status"] = "skipped_no_input"
        return finish()
    diagnostics["effective_query"] = fts_query
    try:
        match_rows = connection.execute(
            "SELECT chunk_id, bm25(chunks_fts) AS fts_rank FROM chunks_fts WHERE chunks_fts MATCH ? ORDER BY fts_rank ASC, chunk_id ASC",
            (fts_query,),
        ).fetchall()
    except sqlite3.OperationalError as exc:
        diagnostics["status"] = "unavailable" if "no such table" in str(exc).lower() else "failed"
        diagnostics["fts_available"] = False
        message = [f"lexical search unavailable: FTS5 index error: {exc}"]
        return ([], message, diagnostics) if return_diagnostics else ([], message)
    diagnostics["fts_available"] = True
    rows_by_id = {str(row.get("chunk_id")): row for row in _scoped_chunk_rows(chunk_rows, scope_filters or {})}
    for match_row in match_rows:
        row = rows_by_id.get(str(match_row["chunk_id"]))
        if row is None or (limit is not None and len(candidates) >= limit):
            continue
        rank = float(match_row["fts_rank"] or 0.0)
        candidates.append({
            **row,
            "selection_reason": f"FTS5 {selected_mode} match: {', '.join(terms)}",
            "score": (1.0 / (1.0 + abs(rank))) + float(config.retrieval_scoring["lexical_bonus"]),
            "selection_source": "lexical",
            "match_reason": f"FTS5 {selected_mode} query {fts_query}",
            "lexical_mode": selected_mode,
            "lexical_query": " ".join(terms),
            "fts_rank": rank,
        })
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
    return_diagnostics: bool = False,
) -> tuple[list[dict[str, Any]], list[str]] | tuple[list[dict[str, Any]], list[str], dict[str, Any]]:
    notes: list[str] = []
    diagnostics: dict[str, Any] = {
        "requested_queries": [],
        "query_results": [],
        "candidate_count": 0,
    }
    def finish() -> tuple[list[dict[str, Any]], list[str]] | tuple[list[dict[str, Any]], list[str], dict[str, Any]]:
        diagnostics["candidate_count"] = len(candidates)
        return (candidates, notes, diagnostics) if return_diagnostics else (candidates, notes)

    queries = [str(query).strip() for query in (vector_queries or [vector_query]) if str(query).strip()]
    diagnostics["requested_queries"] = list(queries)
    if not queries:
        diagnostics["status"] = "skipped_no_input"
        candidates: list[dict[str, Any]] = []
        return finish()
    if getattr(embedding_backend, "mode_name", "unavailable") == "unavailable":
        diagnostics["status"] = "unavailable"
        candidates = []
        return finish()

    query_vectors: list[tuple[str, list[float]]] = []
    query_failures: list[str] = []
    for query in queries:
        response = embedding_backend.embed_query_text(query)
        if response.status == "embedded" and response.vectors:
            query_vectors.append((query, response.vectors[0]))
            diagnostics["query_results"].append({"query": query, "backend_status": response.status, "embedding_succeeded": True, "candidate_count_before_merge": 0})
        else:
            query_failures.append(f"{query}: {response.status}")
            diagnostics["query_results"].append({"query": query, "backend_status": response.status, "embedding_succeeded": False, "candidate_count_before_merge": 0, "diagnostic": "embedding failed"})
    if not query_vectors:
        diagnostics["status"] = "failed" if query_failures else "unavailable"
        candidates = []
        return ([], [f"vector search unavailable: {'; '.join(query_failures)}"], diagnostics) if return_diagnostics else ([], [f"vector search unavailable: {'; '.join(query_failures)}"])
    try:
        rows = connection.execute(
            f"SELECT chunk_id, vector_json FROM {config.vector_table}"
        ).fetchall()
    except sqlite3.OperationalError as exc:
        diagnostics["status"] = "unavailable"
        candidates = []
        message = [f"vector search unavailable: {exc}"]
        return ([], message, diagnostics) if return_diagnostics else ([], message)
    if not rows:
        diagnostics["status"] = "partial_failure" if query_failures else "completed_no_candidates"
        candidates = []
        return finish()

    chunk_rows = {row["chunk_id"]: row for row in _load_chunk_rows(connection)}
    candidates: list[dict[str, Any]] = []
    query_hit_counts = {query: 0 for query, _ in query_vectors}
    for row in rows:
        chunk_id = str(row["chunk_id"])
        chunk_row = chunk_rows.get(chunk_id)
        if chunk_row is None or not _chunk_matches_scope(chunk_row, scope_filters or {}):
            continue
        try:
            vector = json.loads(str(row["vector_json"]))
        except json.JSONDecodeError:
            continue
        if not isinstance(vector, list) or not all(isinstance(value, (int, float)) for value in vector):
            continue
        similarities = [
            (query, _cosine_similarity(query_vector, [float(value) for value in vector]))
            for query, query_vector in query_vectors
        ]
        query, similarity = max(similarities, key=lambda item: (item[1], item[0]))
        if similarity <= 0.0:
            continue
        matching_queries = [query_name for query_name, score in similarities if score > 0.0]
        for query_name in matching_queries:
            query_hit_counts[query_name] += 1
        candidates.append(
            {
                **chunk_row,
                "selection_reason": f"vector similarity {similarity:.3f}",
                "score": similarity + float(config.retrieval_scoring["vector_bonus"]),
                "selection_source": "vector",
                "match_reason": f"vector query similarity {similarity:.3f}",
                "semantic_query_provenance": matching_queries,
            }
        )
    candidates = sorted(candidates, key=lambda candidate: -float(candidate.get("score") or 0.0))
    if limit is not None:
        candidates = candidates[:limit]
    if candidates:
        notes.append(f"vector search matched {len(candidates)} chunk(s) across {len(query_vectors)} semantic quer{'y' if len(query_vectors) == 1 else 'ies'}")
    else:
        notes.append("vector search produced no matches")
    if query_failures:
        notes.append(f"vector queries failed: {'; '.join(query_failures)}")
    for result in diagnostics["query_results"]:
        result["candidate_count_before_merge"] = query_hit_counts.get(result["query"], 0)
    diagnostics["status"] = "partial_failure" if query_failures else ("completed_with_candidates" if candidates else "completed_no_candidates")
    return finish()


def _graph_candidates(
    *,
    connection: sqlite3.Connection,
    config: RuntimeConfig,
    planner_retrieval_plan: dict[str, Any],
    prior_thread_state: dict[str, Any],
    graph_depth: dict[str, Any] | None = None,
    scope_filters: dict[str, Any] | None = None,
    limit: int | None = None,
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
            f"SELECT source_node_id, target_node_id, edge_type FROM {config.graph_edges_table}"
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
        prior_thread_state=prior_thread_state,
        config=config,
    )
    if not seed_values:
        return [], ["graph search skipped: no graph seeds"], {
            "status": "skipped_no_input",
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

    submitted_seeds: list[dict[str, str]] = []
    for source_name, seed in seed_values:
        item = {"source": str(source_name), "value": str(seed)}
        if item not in submitted_seeds:
            submitted_seeds.append(item)

    outgoing: dict[str, list[tuple[str, str, str]]] = {}
    incoming: dict[str, list[tuple[str, str, str]]] = {}
    for row in edge_rows:
        source_node_id = str(row["source_node_id"])
        target_node_id = str(row["target_node_id"])
        edge_type = str(row["edge_type"])
        outgoing.setdefault(source_node_id, []).append((target_node_id, edge_type, "outbound"))
        incoming.setdefault(target_node_id, []).append((source_node_id, edge_type, "inbound"))

    selected_note_ids: list[str] = []
    note_reasons: dict[str, list[str]] = {}
    note_hops: dict[str, list[dict[str, Any]]] = {}
    matched_seed_note_ids: list[str] = []
    matched_seed_count = 0
    expanded_note_count = 0
    edge_types_used: list[str] = []
    queue: list[tuple[str, int]] = []
    visited_notes: set[str] = set()

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
        traversals: list[tuple[str, str, str]] = []
        if config.graph_traversal_direction in {"outbound", "both"}:
            traversals.extend(sorted(outgoing.get(current_node_id, []), key=lambda item: (item[1], item[0])))
        if config.graph_traversal_direction in {"inbound", "both"}:
            traversals.extend(sorted(incoming.get(current_node_id, []), key=lambda item: (item[1], item[0])))
        for target_node_id, edge_type, edge_direction in traversals:
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
        "expanded_note_count": expanded_note_count,
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
    for key in ("entities", "relations", "resolved_referents", "graph_seeds", "concepts", "scope_requests", "literal_terms", "semantic_queries", "lexical_queries"):
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


def _merge_candidates(
    lexical_candidates: list[dict[str, Any]],
    vector_candidates: list[dict[str, Any]],
    graph_candidates: list[dict[str, Any]],
    config: RuntimeConfig,
    exact_candidates: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    source_priority = {str(key): int(value) for key, value in config.retrieval_scoring["source_priority"].items()}

    def merge_list(existing: dict[str, Any], candidate: dict[str, Any], field: str) -> None:
        values = [*existing.get(field, []), *candidate.get(field, [])]
        if values:
            unique: list[Any] = []
            for value in values:
                if value not in unique:
                    unique.append(value)
            existing[field] = unique

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
        existing["sources"] = list(dict.fromkeys(existing.get("sources", []) + candidate_layers))
        existing["source_layers"] = list(dict.fromkeys(existing.get("source_layers", []) + candidate_layers))
        merge_list(existing, candidate, "exact_term_provenance")
        merge_list(existing, candidate, "exact_match_evidence")
        merge_list(existing, candidate, "graph_hop_provenance")
        if existing_graph_provenance or candidate_graph_provenance:
            existing["graph_provenance"] = list(dict.fromkeys([*existing_graph_provenance, *candidate_graph_provenance]))
        if existing.get("graph_direction") is None and candidate.get("graph_direction") is not None:
            existing["graph_direction"] = candidate.get("graph_direction")
        existing_score = float(existing.get("score") or 0.0)
        candidate_score = float(candidate.get("score") or 0.0)
        if candidate_score > existing_score or (
            candidate_score == existing_score
            and source_priority.get(source, 0) > source_priority.get(str(existing.get("selection_source") or ""), 0)
        ):
            merged[chunk_id] = dict(candidate)
            merged[chunk_id]["graph_provenance"] = list(dict.fromkeys([*existing_graph_provenance, *candidate_graph_provenance]))
            merged[chunk_id]["graph_hop_provenance"] = []
            merge_list(merged[chunk_id], existing, "graph_hop_provenance")
            merge_list(merged[chunk_id], candidate, "graph_hop_provenance")
            if merged[chunk_id].get("graph_direction") is None:
                merged[chunk_id]["graph_direction"] = existing.get("graph_direction")
            merged[chunk_id]["sources"] = list(dict.fromkeys(existing.get("sources", []) + candidate_layers))
            merged[chunk_id]["source_layers"] = list(dict.fromkeys(existing.get("source_layers", []) + candidate_layers))
            merged[chunk_id]["semantic_query_provenance"] = list(dict.fromkeys([*existing_queries, *candidate_queries]))
            merged[chunk_id]["exact_term_provenance"] = list(dict.fromkeys([*_coerce_string_list(existing.get("exact_term_provenance")), *_coerce_string_list(candidate.get("exact_term_provenance"))]))
            evidence = [*existing.get("exact_match_evidence", []), *candidate.get("exact_match_evidence", [])]
            merged[chunk_id]["exact_match_evidence"] = []
            for item in evidence:
                if item not in merged[chunk_id]["exact_match_evidence"]:
                    merged[chunk_id]["exact_match_evidence"].append(item)
        else:
            existing["score"] = max(existing_score, candidate_score)
            existing["_retrieval_demoted"] = bool(existing.get("_retrieval_demoted")) or bool(candidate.get("_retrieval_demoted"))
            existing["selection_reason"] = ", ".join(
                part for part in [existing.get("selection_reason"), candidate.get("selection_reason")] if part
            )

    for candidate in list(exact_candidates or []) + lexical_candidates + vector_candidates + graph_candidates:
        absorb(candidate)

    ranked = sorted(
        merged.values(),
        key=lambda candidate: (
            bool(candidate.get("_retrieval_demoted")),
            not bool(candidate.get("preferred_scope_match")),
            -source_priority.get(str((candidate.get("selection_source") or "lexical")), 0),
            -float(candidate.get("score") or 0.0),
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

    budgets = selection_policy.get("budgets") if isinstance(selection_policy, dict) else None
    preserve_required_layers = bool(selection_policy.get("preserve_required_layers")) if isinstance(selection_policy, dict) else False
    # Reserve exact-backed representatives only for required terms with
    # positive matches. The configured exact budget and overall packet limit
    # remain hard bounds; inability to reserve is diagnosed after selection.
    required_exact_terms = set(required_exact_terms or set())
    exact_budget = None
    if isinstance(budgets, dict):
        try:
            exact_budget = max(0, int(budgets.get("exact", 0)))
        except (TypeError, ValueError):
            exact_budget = 0
    reserved_terms: set[str] = set()
    if required_exact_terms and (exact_budget is None or exact_budget > 0):
        for candidate in merged_candidates:
            candidate_terms = set(_coerce_string_list(candidate.get("exact_term_provenance")))
            if "exact" not in _candidate_source_layers(candidate) or not candidate_terms.intersection(required_exact_terms - reserved_terms):
                continue
            if exact_budget is not None and sum("exact" in _candidate_source_layers(item) for item in selected) >= exact_budget:
                break
            if absorb(candidate):
                reserved_terms.update(candidate_terms.intersection(required_exact_terms))

    required_sources = set(required_sources or set())
    reservation_diagnostics = selection_diagnostics if selection_diagnostics is not None else {}
    reservation_diagnostics.setdefault("required_sources", sorted(required_sources))
    reservation_diagnostics.setdefault("preserved_sources", [])
    reservation_diagnostics.setdefault("inadequacies", [])
    if preserve_required_layers and required_sources:
        def source_budget_available(candidate: dict[str, Any]) -> bool:
            if not isinstance(budgets, dict):
                return True
            selected_source_counts = {
                source: sum(source in _candidate_source_layers(item) for item in selected)
                for source in required_sources
            }
            for source in required_sources.intersection(_candidate_source_layers(candidate)):
                try:
                    budget = max(0, int(budgets.get(source, 0)))
                except (TypeError, ValueError):
                    budget = 0
                if selected_source_counts.get(source, 0) >= budget:
                    return False
            return True

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
                and source_budget_available(candidate)
            ]
            if not eligible:
                for source in sorted(unsatisfied):
                    reason = "no candidate survived executor limits"
                    if isinstance(budgets, dict) and int(budgets.get(source, 0) or 0) <= 0:
                        reason = "source budget is zero"
                    elif len(selected) >= max(0, max_chunks):
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
            reservation_diagnostics["preserved_sources"] = sorted(
                set(reservation_diagnostics["preserved_sources"]).union(_candidate_source_layers(best))
            )

    if isinstance(budgets, dict):
        for source in ("exact", "lexical", "vector", "graph"):
            try:
                budget = max(0, int(budgets.get(source, 0)))
            except (TypeError, ValueError):
                budget = 0
            taken = sum(source in _candidate_source_layers(item) for item in selected)
            for candidate in merged_candidates:
                if taken >= budget:
                    break
                if source not in _candidate_source_layers(candidate):
                    continue
                if absorb(candidate):
                    taken += 1

    for candidate in merged_candidates:
        if len(selected) >= max(0, max_chunks):
            break
        absorb(candidate)
    return selected


def _semantic_traversal(
    *,
    connection: sqlite3.Connection,
    config: RuntimeConfig,
    semantic_compiler_packet: dict[str, Any],
    prior_thread_state: dict[str, Any],
    embedding_backend: EmbeddingBackend,
) -> tuple[dict[str, Any], dict[str, Any]]:
    planner_retrieval_plan = semantic_compiler_packet.get("planner_retrieval_plan") if isinstance(semantic_compiler_packet.get("planner_retrieval_plan"), dict) else {}
    if not planner_retrieval_plan:
        planner_retrieval_plan = build_default_retrieval_plan(
            raw_user_input=str(semantic_compiler_packet.get("raw_user_input") or ""),
            query=str(semantic_compiler_packet.get("query") or ""),
            concepts=_coerce_string_list(semantic_compiler_packet.get("concepts")),
            scope_requests=_coerce_string_list(semantic_compiler_packet.get("scope_requests")),
            graph_seeds=_coerce_string_list(semantic_compiler_packet.get("graph_seeds")),
            resolved_referents=_coerce_string_list(semantic_compiler_packet.get("resolved_referents")),
            planner_defaults=config.retrieval_planner_defaults,
        )

    resource_inventory_summary = build_resource_inventory(connection=connection, config=config)
    bound_retrieval_plan, resolver_adjustments, resource_inventory_summary = bind_retrieval_plan(
        planner_retrieval_plan=planner_retrieval_plan,
        inventory_summary=resource_inventory_summary,
        config=config,
        raw_user_input=str(semantic_compiler_packet.get("raw_user_input") or ""),
    )

    scope_filters = bound_retrieval_plan.get("scope_filters") if isinstance(bound_retrieval_plan.get("scope_filters"), dict) else {}
    literal_terms = [entry for entry in bound_retrieval_plan.get("literal_terms", []) if isinstance(entry, dict)]
    semantic_queries = _coerce_string_list(bound_retrieval_plan.get("semantic_queries"))
    lexical_queries = _coerce_string_list(bound_retrieval_plan.get("lexical_queries")) or semantic_queries
    graph_layer = retrieval_plan_layer(bound_retrieval_plan, "graph_expand")
    exact_layer = retrieval_plan_layer(bound_retrieval_plan, "exact_chunk_search")
    lexical_layer = retrieval_plan_layer(bound_retrieval_plan, "lexical_chunk_search")
    vector_layer = retrieval_plan_layer(bound_retrieval_plan, "vector_search")

    chunk_rows = _load_chunk_rows(connection)
    layer_manifests: dict[str, Any] = {}
    execution = {"layers_executed": [], "layers_skipped": []}
    unsupported_layer_requests: list[dict[str, Any]] = []
    supported_operators = {"exact_chunk_search", "lexical_chunk_search", "vector_search", "graph_expand"}
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
    if graph_layer is not None:
        execution["layers_executed"].append(str(graph_layer.get("operator") or "graph_expand"))
        graph_candidates, graph_notes, graph_traversal_info = _graph_candidates(
            connection=connection,
            config=config,
            planner_retrieval_plan=bound_retrieval_plan,
            prior_thread_state=prior_thread_state,
            graph_depth=graph_layer,
            scope_filters=scope_filters,
            limit=_layer_limit(graph_layer, config.retrieval_graph_max_candidates),
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

    preferred_scope_filters = bound_retrieval_plan.get("preferred_scope_filters") if isinstance(bound_retrieval_plan.get("preferred_scope_filters"), dict) else {}
    exact_candidates = _annotate_scope_matches(
        exact_candidates,
        hard_scope_filters=scope_filters,
        preferred_scope_filters=preferred_scope_filters,
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
    lexical_candidates = _annotate_scope_matches(
        lexical_candidates,
        hard_scope_filters=scope_filters,
        preferred_scope_filters=preferred_scope_filters,
    )
    vector_candidates = _annotate_scope_matches(
        vector_candidates,
        hard_scope_filters=scope_filters,
        preferred_scope_filters=preferred_scope_filters,
    )
    graph_candidates = _annotate_scope_matches(
        graph_candidates,
        hard_scope_filters=scope_filters,
        preferred_scope_filters=preferred_scope_filters,
    )

    merged_candidates = _merge_candidates(lexical_candidates, vector_candidates, graph_candidates, config=config, exact_candidates=exact_candidates)
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
    for source, manifest in (("lexical", layer_manifests.get("lexical")), ("vector", layer_manifests.get("vector")), ("graph", layer_manifests.get("graph"))):
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
    exact_info = layer_manifests.get("exact", {}) if isinstance(layer_manifests.get("exact"), dict) else {}
    exact_search_performed = exact_layer is not None
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
            "candidate_count": int(manifest.get("candidate_count") or len({"exact": exact_candidates, "lexical": lexical_candidates, "vector": vector_candidates, "graph": graph_candidates}.get(source, []))),
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
        "coverage_claims_allowed": bool(exact_info.get("search_exhaustive")) and exact_info.get("status") in {"completed_no_matches", "completed_with_matches"},
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
        "resource_inventory_summary": resource_inventory_summary,
        "execution": execution,
        "candidate_counts": {
            "exact": len(exact_candidates),
            "lexical": len(lexical_candidates),
            "vector": len(vector_candidates),
            "graph": len(graph_candidates),
        },
        "selected_counts": selected_counts,
        "selected_chunk_ids": [str(candidate["chunk_id"]) for candidate in selected_candidates],
        "layer_manifests": layer_manifests,
        "unsupported_layer_requests": unsupported_layer_requests,
        "coverage": coverage,
        "limits": limits,
        "graph_traversal": graph_traversal_info,
        "scope_resolution": {
            "hard": scope_filters,
            "preferred": preferred_scope_filters,
            "bound_requests": bound_retrieval_plan.get("scope_resolution", {}),
        },
        "selection_notes": [*exact_notes, *lexical_notes, *vector_notes, *graph_notes],
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
                "paragraph_text": str(candidate["paragraph_text"]),
                "chunk_hash": str(candidate["chunk_hash"]),
                "source_layers": _candidate_source_layers(candidate),
                "selection_source": str(candidate.get("selection_source") or ""),
                "exact_match_evidence": candidate.get("exact_match_evidence", []),
                "semantic_query_provenance": _coerce_string_list(candidate.get("semantic_query_provenance")),
                "graph_direction": candidate.get("graph_direction"),
                "graph_provenance": candidate.get("graph_provenance", []),
                "graph_hop_provenance": candidate.get("graph_hop_provenance", []),
                "match_reason": str(candidate.get("match_reason") or candidate.get("selection_reason") or ""),
                "scope_match": candidate.get("scope_match"),
                "preferred_scope_match": bool(candidate.get("preferred_scope_match")),
                "selection_reason": str(candidate.get("selection_reason") or ""),
            }
            for candidate in selected_candidates
        ],
        "matched_chunk_count": len(selected_candidates),
        "retrieval_observation": "matched_chunks" if selected_candidates else "no_matches",
        "assembled_from_traversal_manifest": True,
    }

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
    bound_plan = traversal_manifest.get("bound_retrieval_plan") if isinstance(traversal_manifest.get("bound_retrieval_plan"), dict) else {}
    graph_seeds = list(bound_plan.get("graph_seeds") or [])
    semantic_queries = list(bound_plan.get("semantic_queries") or [])
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
        and traversal_manifest["coverage"].get("exact_status") == "completed_no_matches"
        and traversal_manifest["coverage"].get("negative_claims_allowed") is not None
        and all(
            isinstance(result, dict) and result.get("status") == "completed_no_matches"
            for result in (layer_manifests.get("exact", {}).get("term_results", []) if isinstance(layer_manifests.get("exact"), dict) else [])
        )
    )
    if selected_count == 0 and (semantic_queries or graph_seeds) and not exact_absence_is_valid:
        blocking_reasons.append("retrieval required but no chunks were selected")
    selection_notes = traversal_manifest.get("selection_notes") if isinstance(traversal_manifest, dict) else []
    if isinstance(selection_notes, list) and any("ingestion database unavailable" in str(note).lower() for note in selection_notes):
        blocking_reasons.append("ingestion database unavailable; run ingest before asking corpus questions")
    return {
        "decision": "approved" if not blocking_reasons and (selected_count > 0 or not (semantic_queries or graph_seeds)) else "blocked",
        "blocking_reasons": blocking_reasons,
        "semantic_compiler_status": semantic_compiler_status,
        "semantic_compiler_response_status": semantic_compiler_diagnostic.get("semantic_compiler_response_status"),
        "semantic_compiler_diagnostic_hash": sha256_json(semantic_compiler_diagnostic),
        "semantic_compiler_packet_hash": sha256_json(semantic_compiler_packet) if compiler_valid else None,
        "semantic_traversal_manifest_hash": sha256_json(traversal_manifest),
        "retrieval_packet_hash": sha256_json(retrieval_packet),
        "selected_chunk_count": selected_count,
    }


def _blocked_turn_response(*, blocking_reasons: list[str]) -> str:
    """Return an honest assistant message for a turn that never reached synthesis."""
    reasons = " ".join(str(reason).strip() for reason in blocking_reasons if str(reason).strip()).lower()
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


def _build_synthesis_context_packet(
    *,
    thread_id: str,
    turn_id: int,
    raw_user_input: str,
    prior_thread_state: dict[str, Any],
    visible_transcript_tail: list[dict[str, Any]],
    semantic_compiler_packet: dict[str, Any],
    semantic_traversal_manifest: dict[str, Any],
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
        "semantic_traversal_manifest": semantic_traversal_manifest,
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
    resource_inventory_summary: dict[str, Any] = {"configured_source_labels": [resolved_config.vault_source_label], "observed_source_labels": [], "frontmatter_facets": {"note_type": []}, "path_topology": {"top_level": [], "second_level": []}, "graph_capabilities": {"nodes_table_present": False, "edges_table_present": False, "node_count": 0, "edge_count": 0}, "scope_aliases": resolved_config.retrieval_scope_aliases}
    if database_path.exists():
        try:
            with sqlite3.connect(database_path) as inventory_connection:
                resource_inventory_summary = build_resource_inventory(connection=inventory_connection, config=resolved_config)
        except sqlite3.Error:
            resource_inventory_summary = build_resource_inventory(connection=None, config=resolved_config)

    compiler_request = _compiler_request_packet(
        raw_user_input=user_input,
        prior_thread_state=prior_thread_state,
        recent_messages=recent_messages,
        recent_semantic_turns=recent_semantic_turns,
        active_focus=active_focus,
        resource_inventory_summary=resource_inventory_summary,
    )
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
    semantic_compiler_diagnostic = _semantic_compiler_diagnostic_packet(
        response=compiler_response,
        semantic_compiler_status=semantic_compiler_status,
        config=resolved_config,
    )
    planner_retrieval_plan = semantic_compiler_packet.get("planner_retrieval_plan") if isinstance(semantic_compiler_packet.get("planner_retrieval_plan"), dict) else {}
    if not planner_retrieval_plan:
        planner_retrieval_plan = build_default_retrieval_plan(
            raw_user_input=user_input,
            query=str(semantic_compiler_packet.get("query") or ""),
            concepts=_coerce_string_list(semantic_compiler_packet.get("concepts")),
            scope_requests=_coerce_string_list(semantic_compiler_packet.get("scope_requests")),
            graph_seeds=_coerce_string_list(semantic_compiler_packet.get("graph_seeds")),
            resolved_referents=_coerce_string_list(semantic_compiler_packet.get("resolved_referents")),
            planner_defaults=resolved_config.retrieval_planner_defaults,
        )
    # The database-backed path binds inside _semantic_traversal, where the same
    # live inventory is already loaded. Only bind here for the no-database
    # diagnostic path so resolver work is not performed twice per turn.
    bound_retrieval_plan = planner_retrieval_plan
    resolver_adjustments: list[dict[str, Any]] = []

    if database_path.exists():
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
        semantic_traversal_manifest=semantic_traversal_manifest,
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
                semantic_traversal_manifest=semantic_traversal_manifest,
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
