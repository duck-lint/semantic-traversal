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
from .retrieval_plan import build_default_retrieval_plan, canonicalize_retrieval_plan, coerce_string_list, retrieval_plan_layer, scope_requests_from_text
from .retrieval_resolver import bind_retrieval_plan
from .semantic_compiler import (
    SemanticCompilerBackend,
    SemanticCompilerResponse,
    resolve_semantic_compiler_backend,
)
from .storage import append_ledger_record, create_thread_paths, load_json, write_json


QUERY_TOKEN_RE = re.compile(r"[A-Za-z0-9']+")
STOP_WORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "but",
    "by",
    "about",
    "do",
    "for",
    "from",
    "how",
    "i",
    "if",
    "in",
    "into",
    "is",
    "it",
    "my",
    "of",
    "on",
    "or",
    "our",
    "retrieve",
    "retrieved",
    "retrieves",
    "retrieving",
    "note",
    "notes",
    "regarding",
    "search",
    "the",
    "find",
    "found",
    "locate",
    "mention",
    "mentions",
    "show",
    "to",
    "with",
    "you",
    "your",
}
REFERENTIAL_SURFACE_WORDS = {"it", "that", "this", "those", "they", "them"}
RECENT_SEMANTIC_TURN_LIMIT = 6
ASSISTANT_SNIPPET_LIMIT = 120
LAYER_TO_SOURCE = {"exact_chunk_search": "exact", "lexical_chunk_search": "lexical", "vector_search": "vector", "graph_expand": "graph", "graph_lookup": "graph", "graph_paths": "graph", "graph_neighbors": "graph", "graph_from_results": "graph"}


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


def _extract_terms(text: str) -> list[str]:
    terms: list[str] = []
    seen: set[str] = set()
    for token in QUERY_TOKEN_RE.findall(text.lower()):
        if len(token) < 3 or token in STOP_WORDS or token.isdigit():
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


def _ensure_recent_semantic_turns(value: Any) -> list[dict[str, Any]]:
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
    return turns[-RECENT_SEMANTIC_TURN_LIMIT:]


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


def _focus_terms(focus: dict[str, Any]) -> list[str]:
    terms: list[str] = []
    for value in (
        focus.get("concepts"),
        focus.get("scope_requests"),
        focus.get("resolved_referents"),
        focus.get("literal_terms"),
        focus.get("semantic_queries"),
        focus.get("lexical_queries"),
        focus.get("graph_seeds"),
        focus.get("selected_note_titles"),
        focus.get("selected_section_labels"),
        [focus.get("query")],
    ):
        for item in _coerce_string_list(value):
            for term in _extract_terms(item):
                if term not in terms:
                    terms.append(term)
            if item not in terms:
                terms.append(item)
    return terms


def _is_referential_user_input(text: str) -> bool:
    lowered = f" {text.lower()} "
    return any(f" {surface} " in lowered for surface in REFERENTIAL_SURFACE_WORDS)


def _snippet(text: str, limit: int = ASSISTANT_SNIPPET_LIMIT) -> str:
    cleaned = re.sub(r"\s+", " ", text).strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1].rstrip() + "…"


def _semantic_turn_focus_terms(turn: dict[str, Any]) -> list[str]:
    terms: list[str] = []
    for value in (
        turn.get("query"),
        turn.get("concepts"),
        turn.get("scope_requests"),
        turn.get("resolved_referents"),
        turn.get("literal_terms"),
        turn.get("semantic_queries"),
        turn.get("lexical_queries"),
        turn.get("graph_seeds"),
        turn.get("selected_note_titles"),
        turn.get("selected_section_labels"),
        turn.get("entities"),
        turn.get("relations"),
    ):
        for item in _coerce_string_list(value):
            for term in _extract_terms(item):
                if term not in terms:
                    terms.append(term)
            if item not in terms:
                terms.append(item)
    return terms


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


def _recent_semantic_turns_from_state(value: Any) -> list[dict[str, Any]]:
    return _ensure_recent_semantic_turns(value)


def _build_recent_semantic_turn(
    *,
    turn_id: int,
    raw_user_input: str,
    assistant_response: str | None,
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
        "turn_id": turn_id,
        "raw_user_input": raw_user_input,
        "assistant_response_snippet": _snippet(assistant_response or ""),
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
        "instruction": "Compile a soft retrieval plan, not an answer. Emit scope requests and concepts only; do not emit note_type, path_contains, source_label, or any other executable filters.",
    }


def _deterministic_semantic_packet(
    *,
    raw_user_input: str,
    prior_thread_state: dict[str, Any],
    active_focus: dict[str, Any],
    recent_semantic_turns: list[dict[str, Any]],
    limitations: list[str] | None = None,
) -> dict[str, Any]:
    concepts = _extract_terms(raw_user_input)
    query = " ".join(concepts[:8]).strip() or raw_user_input.strip()
    scope_requests = ["journal"] if any(term in concepts for term in ("journal", "journaled", "daily", "dailies", "entries", "entry")) else []
    resolved_referents: list[str] = []
    if _is_referential_user_input(raw_user_input):
        focus_terms = _focus_terms(active_focus)
        for turn in recent_semantic_turns[-2:]:
            focus_terms.extend(_semantic_turn_focus_terms(turn))
        resolved_referents = list(dict.fromkeys(term for term in focus_terms if term))
        if resolved_referents:
            concepts = list(dict.fromkeys([*concepts, *resolved_referents]))
            query = " ".join(resolved_referents[:8]).strip() or query
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
    fallback_limitations: list[str] | None = None,
) -> dict[str, Any]:
    fallback_packet = _deterministic_semantic_packet(
        raw_user_input=raw_user_input,
        prior_thread_state=prior_thread_state,
        active_focus=active_focus,
        recent_semantic_turns=recent_semantic_turns,
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
    packet["limitations"] = _coerce_string_list(payload.get("limitations")) or packet["limitations"]
    focus_terms: list[str] = []
    if _is_referential_user_input(raw_user_input):
        focus_terms = _focus_terms(active_focus)
        for turn in recent_semantic_turns[-2:]:
            focus_terms.extend(_semantic_turn_focus_terms(turn))
        focus_terms = list(dict.fromkeys(term for term in focus_terms if term))
    if _is_referential_user_input(raw_user_input) and focus_terms:
        packet["resolved_referents"] = list(dict.fromkeys([*packet["resolved_referents"], *focus_terms]))
    fallback_plan = build_default_retrieval_plan(
        raw_user_input=raw_user_input,
        query=packet["query"],
        concepts=_extract_terms(packet["query"]),
        scope_requests=scope_requests_from_text(packet["query"]),
        graph_seeds=[packet["query"]] if packet["query"].strip() else [],
        resolved_referents=list(packet["resolved_referents"]),
    )
    canonical_plan, planner_diagnostics = canonicalize_retrieval_plan(payload.get("planner_retrieval_plan"), fallback=fallback_plan)
    if _is_referential_user_input(raw_user_input):
        canonical_plan["resolved_referents"] = list(dict.fromkeys([*canonical_plan.get("resolved_referents", []), *packet["resolved_referents"]]))
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
            limitations=["semantic compiler backend unavailable; deterministic lexical fallback used"],
        ),
        "fallback",
    )


def _semantic_compiler_diagnostic_packet(
    *,
    response: SemanticCompilerResponse,
    semantic_compiler_status: str,
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
        "raw_response_preview": _snippet(raw_response, limit=1200) if raw_response else None,
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


def _layer_enabled(plan: dict[str, Any], operator: str) -> bool:
    return retrieval_plan_layer(plan, operator) is not None


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
    title = str(row.get("note_title") or "").lower()
    frontmatter_semantics = ""
    try:
        frontmatter_semantics = json.dumps(json.loads(str(row.get("frontmatter_semantics_json") or "{}")), ensure_ascii=True).lower()
    except json.JSONDecodeError:
        frontmatter_semantics = str(row.get("frontmatter_semantics_json") or "").lower()
    scope_haystack = f"{source_root_path} {relative_path} {title} {frontmatter_semantics}"
    # Journal/daily scopes are often encoded in path/title/frontmatter-derived filenames rather than a chunk column.
    required_terms = list(dict.fromkeys([*path_terms, *note_type_terms]))
    return any(term in scope_haystack for term in required_terms)


def _scoped_chunk_rows(chunk_rows: list[dict[str, Any]], scope_filters: dict[str, Any]) -> list[dict[str, Any]]:
    return [row for row in chunk_rows if _chunk_matches_scope(row, scope_filters)]


def _candidate_source_layers(candidate: dict[str, Any]) -> list[str]:
    sources = candidate.get("sources") or [candidate.get("selection_source") or "lexical"]
    normalized: list[str] = []
    for source in _coerce_string_list(sources):
        if source == "graph_expanded":
            source = "graph"
        if source and source not in normalized:
            normalized.append(source)
    return normalized


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
) -> tuple[list[dict[str, Any]], list[str], dict[str, Any]]:
    notes: list[str] = []
    terms = [str(entry.get("term") or "").strip() for entry in literal_terms if isinstance(entry, dict) and str(entry.get("term") or "").strip()]
    if not terms:
        return [], ["exact search skipped: no literal terms"], {
            "operator": "exact_chunk_search",
            "literal_terms": [],
            "scope": scope_filters,
            "total_match_count": 0,
            "matching_note_count": 0,
            "returned_count": 0,
        }

    scoped_rows = _scoped_chunk_rows(chunk_rows, scope_filters)
    candidates: list[dict[str, Any]] = []
    matching_note_ids: set[str] = set()
    total_match_count = 0
    lowered_terms = [term.lower() for term in terms]
    for row in scoped_rows:
        haystack = "\n".join(
            str(row.get(field) or "")
            for field in ("note_title", "section_label", "relative_path", "paragraph_text")
        ).lower()
        matched_terms = [term for term, lowered_term in zip(terms, lowered_terms, strict=True) if lowered_term in haystack]
        if not matched_terms:
            continue
        total_match_count += 1
        matching_note_ids.add(str(row.get("note_id") or ""))
        if len(candidates) >= limit:
            continue
        candidates.append(
            {
                **row,
                "selection_reason": f"exact literal match: {', '.join(matched_terms)}",
                "score": len(matched_terms) + 4.0,
                "selection_source": "exact",
                "match_reason": f"literal term {', '.join(matched_terms)}",
                "scope_match": scope_filters,
            }
        )
    if candidates:
        notes.append(f"exact search matched {total_match_count} chunk(s); returned {len(candidates)}")
    else:
        notes.append("exact search produced no matches")
    return candidates, notes, {
        "operator": "exact_chunk_search",
        "literal_terms": terms,
        "scope": scope_filters,
        "total_match_count": total_match_count,
        "matching_note_count": len([note_id for note_id in matching_note_ids if note_id]),
        "returned_count": len(candidates),
    }


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


def _graph_token_set(value: str) -> set[str]:
    return set(_extract_terms(value))


def _graph_match_note_nodes(
    *,
    seed: str,
    node_rows: list[dict[str, Any]],
    node_type_allowlist: set[str],
    match_mode: str,
    min_token_overlap: int,
) -> list[dict[str, Any]]:
    exact_matches: list[dict[str, Any]] = []
    overlap_matches: list[dict[str, Any]] = []
    seed_normalized = _normalize_text(seed)
    seed_tokens = _graph_token_set(seed)
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
        node_tokens = _graph_token_set(f"{label} {ref_id}")
        if len(seed_tokens.intersection(node_tokens)) >= min_token_overlap:
            overlap_matches.append(dict(row))
    return exact_matches or overlap_matches


def _lexical_candidates(
    chunk_rows: list[dict[str, Any]],
    query_terms: list[str],
    *,
    scope_filters: dict[str, Any] | None = None,
    limit: int | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    candidates: list[dict[str, Any]] = []
    notes: list[str] = []
    if not query_terms:
        return candidates, notes
    scoped_rows = _scoped_chunk_rows(chunk_rows, scope_filters or {})
    lowered_terms = [str(term).lower() for term in query_terms if str(term).strip()]
    for row in scoped_rows:
        haystack = " ".join(
            str(row.get(field) or "")
            for field in ("note_title", "section_label", "relative_path", "paragraph_text")
        ).lower()
        matched_terms = [term for term in lowered_terms if term in haystack]
        if not matched_terms:
            continue
        if limit is not None and len(candidates) >= limit:
            continue
        candidates.append(
            {
                **row,
                "selection_reason": f"lexical match: {', '.join(matched_terms)}",
                "score": len(matched_terms) + 2.0,
                "selection_source": "lexical",
                "match_reason": f"lexical term {', '.join(matched_terms)}",
            }
        )
    if candidates:
        notes.append(f"lexical search matched {len(candidates)} chunk(s)")
    else:
        notes.append("lexical search produced no matches")
    return candidates, notes


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
    scope_filters: dict[str, Any] | None = None,
    limit: int | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    notes: list[str] = []
    if not vector_query.strip():
        return [], ["vector search skipped: empty query"]
    if getattr(embedding_backend, "mode_name", "unavailable") == "unavailable":
        return [], ["vector search unavailable"]

    response = embedding_backend.embed_query_text(vector_query)
    if response.status != "embedded" or not response.vectors:
        return [], [f"vector search unavailable: {response.status}"]

    query_vector = response.vectors[0]
    rows = connection.execute(
        f"SELECT chunk_id, vector_json FROM {config.vector_table}"
    ).fetchall()
    if not rows:
        return [], ["vector search found no indexed vectors"]

    chunk_rows = {row["chunk_id"]: row for row in _load_chunk_rows(connection)}
    candidates: list[dict[str, Any]] = []
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
        similarity = _cosine_similarity(query_vector, [float(value) for value in vector])
        if similarity <= 0.0:
            continue
        candidates.append(
            {
                **chunk_row,
                "selection_reason": f"vector similarity {similarity:.3f}",
                "score": similarity + 1.0,
                "selection_source": "vector",
                "match_reason": f"vector query similarity {similarity:.3f}",
            }
        )
    candidates = sorted(candidates, key=lambda candidate: -float(candidate.get("score") or 0.0))
    if limit is not None:
        candidates = candidates[:limit]
    if candidates:
        notes.append(f"vector search matched {len(candidates)} chunk(s)")
    else:
        notes.append("vector search produced no matches")
    return candidates, notes


def _graph_candidates(
    *,
    connection: sqlite3.Connection,
    config: RuntimeConfig,
    planner_retrieval_plan: dict[str, Any],
    prior_thread_state: dict[str, Any],
    scope_filters: dict[str, Any] | None = None,
    limit: int | None = None,
) -> tuple[list[dict[str, Any]], list[str], dict[str, Any]]:
    notes: list[str] = []
    if not config.graph_traversal_enabled:
        return [], ["graph traversal disabled"], {
            "enabled": False,
            "hop_limit": config.graph_traversal_hop_limit,
            "seed_sources": list(config.graph_traversal_seed_sources),
            "matched_seed_count": 0,
            "expanded_note_count": 0,
            "edge_types_used": [],
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
            "enabled": config.graph_traversal_enabled,
            "hop_limit": config.graph_traversal_hop_limit,
            "seed_sources": list(config.graph_traversal_seed_sources),
            "matched_seed_count": 0,
            "expanded_note_count": 0,
            "edge_types_used": [],
        }

    chunk_rows = {row["chunk_id"]: row for row in _load_chunk_rows(connection)}
    nodes_by_id = {str(row["node_id"]): dict(row) for row in node_rows}
    note_nodes = [dict(row) for row in node_rows if str(row["node_type"]) in set(config.graph_traversal_node_type_allowlist)]
    edge_type_allowlist = set(config.graph_traversal_edge_type_allowlist)
    node_type_allowlist = set(config.graph_traversal_node_type_allowlist)
    seed_values = _graph_seed_values(
        planner_retrieval_plan=planner_retrieval_plan,
        prior_thread_state=prior_thread_state,
        config=config,
    )
    if not seed_values:
        return [], ["graph search skipped: no graph seeds"], {
            "enabled": config.graph_traversal_enabled,
            "hop_limit": config.graph_traversal_hop_limit,
            "seed_sources": list(config.graph_traversal_seed_sources),
            "matched_seed_count": 0,
            "expanded_note_count": 0,
            "edge_types_used": [],
        }

    outgoing: dict[str, list[tuple[str, str]]] = {}
    for row in edge_rows:
        outgoing.setdefault(str(row["source_node_id"]), []).append((str(row["target_node_id"]), str(row["edge_type"])))

    selected_note_ids: list[str] = []
    note_reasons: dict[str, list[str]] = {}
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
            if note_id not in visited_notes:
                visited_notes.add(note_id)
                selected_note_ids.append(note_id)
                queue.append((note_id, 0))

    while queue:
        current_note_id, hop = queue.pop(0)
        if hop >= config.graph_traversal_hop_limit:
            continue
        # Avoid relying on helper placement below; note node ids are canonical.
        current_node_id = f"note::{current_note_id}"
        current_node = nodes_by_id.get(current_node_id)
        if current_node is None:
            continue
        current_label = str(current_node.get("label") or current_note_id)
        for target_node_id, edge_type in outgoing.get(current_node_id, []):
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
            note_reasons.setdefault(target_note_id, []).append(f"wikilink hop {hop + 1}: {current_label} -> {target_label}")
            if target_note_id not in visited_notes:
                visited_notes.add(target_note_id)
                selected_note_ids.append(target_note_id)
                queue.append((target_note_id, hop + 1))
                expanded_note_count += 1

    max_graph_candidates = limit if limit is not None else config.graph_traversal_max_candidates
    selected_chunk_ids: list[str] = []
    for note_id in selected_note_ids:
        for chunk_row in chunk_rows.values():
            if str(chunk_row["note_id"]) != note_id or not _chunk_matches_scope(chunk_row, scope_filters or {}):
                continue
            chunk_id = str(chunk_row["chunk_id"])
            if chunk_id in selected_chunk_ids:
                continue
            selected_chunk_ids.append(chunk_id)
            if len(selected_chunk_ids) >= max_graph_candidates:
                break
        if len(selected_chunk_ids) >= max_graph_candidates:
            break

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
                "score": 1.5,
                "selection_source": "graph",
                "match_reason": "graph expansion",
            }
        )
    if candidates:
        notes.append(f"graph search matched {len(candidates)} chunk(s)")
    else:
        notes.append("graph search produced no matches")
    notes.append(
        f"graph traversal enabled={config.graph_traversal_enabled}, hop_limit={config.graph_traversal_hop_limit}, matched_seed_count={matched_seed_count}, expanded_note_count={expanded_note_count}"
    )
    return candidates, notes, {
        "enabled": config.graph_traversal_enabled,
        "hop_limit": config.graph_traversal_hop_limit,
        "seed_sources": list(config.graph_traversal_seed_sources),
        "matched_seed_count": matched_seed_count,
        "expanded_note_count": expanded_note_count,
        "edge_types_used": edge_types_used,
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
) -> list[dict[str, Any]]:
    if _is_template_or_schema_query(semantic_compiler_packet):
        return candidates
    adjusted: list[dict[str, Any]] = []
    for candidate in candidates:
        next_candidate = dict(candidate)
        if _is_template_boilerplate_candidate(next_candidate):
            next_candidate["score"] = float(next_candidate.get("score") or 0.0) - 10.0
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
    exact_candidates: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    source_priority = {"exact": 4, "lexical": 3, "vector": 2, "graph": 1}

    def absorb(candidate: dict[str, Any]) -> None:
        chunk_id = str(candidate["chunk_id"])
        existing = merged.get(chunk_id)
        source = str(candidate.get("selection_source") or "lexical")
        if existing is None:
            merged[chunk_id] = dict(candidate)
            merged[chunk_id]["sources"] = [source]
            merged[chunk_id]["source_layers"] = [source]
            return
        existing["sources"] = list(dict.fromkeys(existing.get("sources", []) + [source]))
        existing["source_layers"] = list(dict.fromkeys(existing.get("source_layers", []) + [source]))
        existing_score = float(existing.get("score") or 0.0)
        candidate_score = float(candidate.get("score") or 0.0)
        if candidate_score > existing_score or (
            candidate_score == existing_score
            and source_priority.get(source, 0) > source_priority.get(str(existing.get("selection_source") or ""), 0)
        ):
            merged[chunk_id] = dict(candidate)
            merged[chunk_id]["sources"] = list(dict.fromkeys(existing.get("sources", []) + [source]))
            merged[chunk_id]["source_layers"] = list(dict.fromkeys(existing.get("source_layers", []) + [source]))
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
            -source_priority.get(str((candidate.get("selection_source") or "lexical")), 0),
            -float(candidate.get("score") or 0.0),
            str(candidate["chunk_id"]),
        ),
    )
    for candidate in ranked:
        sources = candidate.get("sources") or [candidate.get("selection_source") or "lexical"]
        candidate["selection_reason"] = ", ".join(
            part for part in [candidate.get("selection_reason"), f"sources: {', '.join(str(source) for source in sources)}"] if part
        )
    return ranked


def _select_retrieval_chunks(
    *,
    merged_candidates: list[dict[str, Any]],
    max_chunks: int,
    selection_policy: dict[str, Any] | None = None,
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
    if isinstance(budgets, dict):
        for source in ("exact", "lexical", "vector", "graph"):
            try:
                budget = max(0, int(budgets.get(source, 0)))
            except (TypeError, ValueError):
                budget = 0
            taken = 0
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

    exact_candidates: list[dict[str, Any]] = []
    exact_notes: list[str] = []
    exact_info = {
        "operator": "exact_chunk_search",
        "literal_terms": [],
        "scope": scope_filters,
        "total_match_count": 0,
        "matching_note_count": 0,
        "returned_count": 0,
    }
    if exact_layer is not None:
        execution["layers_executed"].append("exact_chunk_search")
        exact_candidates, exact_notes, exact_info = _exact_candidates(
            chunk_rows,
            literal_terms,
            scope_filters=scope_filters,
            limit=_layer_limit(exact_layer, config.retrieval_exact_max_matches),
        )
    else:
        execution["layers_skipped"].append({"layer": "exact_chunk_search", "reason": "not requested by bound retrieval plan"})
        exact_notes = ["exact search skipped: not requested by bound retrieval plan"]
    layer_manifests["exact"] = exact_info

    lexical_candidates: list[dict[str, Any]] = []
    lexical_notes: list[str] = []
    if lexical_layer is not None:
        execution["layers_executed"].append("lexical_chunk_search")
        lexical_candidates, lexical_notes = _lexical_candidates(
            chunk_rows,
            lexical_queries,
            scope_filters=scope_filters,
            limit=_layer_limit(lexical_layer, config.retrieval_lexical_max_candidates),
        )
    else:
        execution["layers_skipped"].append({"layer": "lexical_chunk_search", "reason": "not requested by bound retrieval plan"})
        lexical_notes = ["lexical search skipped: not requested by bound retrieval plan"]

    vector_candidates: list[dict[str, Any]] = []
    vector_notes: list[str] = []
    if vector_layer is not None:
        execution["layers_executed"].append("vector_search")
        vector_candidates, vector_notes = _vector_candidates(
            connection=connection,
            config=config,
            embedding_backend=embedding_backend,
            vector_query=semantic_queries[0] if semantic_queries else (lexical_queries[0] if lexical_queries else str(bound_retrieval_plan.get("intent_type") or "")),
            scope_filters=scope_filters,
            limit=_layer_limit(vector_layer, config.retrieval_vector_max_candidates),
        )
    else:
        execution["layers_skipped"].append({"layer": "vector_search", "reason": "not requested by bound retrieval plan"})
        vector_notes = ["vector search skipped: not requested by bound retrieval plan"]

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
            scope_filters=scope_filters,
            limit=_layer_limit(graph_layer, config.retrieval_graph_max_candidates),
        )
    else:
        execution["layers_skipped"].append({"layer": "graph_expand", "reason": "not requested by bound retrieval plan"})
        graph_candidates, graph_notes, graph_traversal_info = [], ["graph search skipped: not requested by bound retrieval plan"], {
            "enabled": config.graph_traversal_enabled,
            "hop_limit": config.graph_traversal_hop_limit,
            "seed_sources": list(config.graph_traversal_seed_sources),
            "matched_seed_count": 0,
            "expanded_note_count": 0,
            "edge_types_used": [],
        }

    exact_candidates = _apply_retrieval_candidate_hygiene(
        candidates=exact_candidates,
        semantic_compiler_packet=semantic_compiler_packet,
    )
    lexical_candidates = _apply_retrieval_candidate_hygiene(
        candidates=lexical_candidates,
        semantic_compiler_packet=semantic_compiler_packet,
    )
    vector_candidates = _apply_retrieval_candidate_hygiene(
        candidates=vector_candidates,
        semantic_compiler_packet=semantic_compiler_packet,
    )
    graph_candidates = _apply_retrieval_candidate_hygiene(
        candidates=graph_candidates,
        semantic_compiler_packet=semantic_compiler_packet,
    )

    merged_candidates = _merge_candidates(lexical_candidates, vector_candidates, graph_candidates, exact_candidates=exact_candidates)
    selected_candidates = _select_retrieval_chunks(
        merged_candidates=merged_candidates,
        max_chunks=int(bound_retrieval_plan.get("selection_policy", {}).get("max_chunks") or config.max_retrieval_chunks),
        selection_policy=bound_retrieval_plan.get("selection_policy") if isinstance(bound_retrieval_plan.get("selection_policy"), dict) else None,
    )
    selected_counts = _count_selected_by_layer(selected_candidates)
    exact_info = layer_manifests.get("exact", {}) if isinstance(layer_manifests.get("exact"), dict) else {}
    exact_search_performed = exact_layer is not None
    total_exact_matches = int(exact_info.get("total_match_count") or 0)
    matching_note_count = int(exact_info.get("matching_note_count") or 0)
    claim_policy = bound_retrieval_plan.get("claim_policy") if isinstance(bound_retrieval_plan.get("claim_policy"), dict) else {}
    coverage = {
        "exact_search_performed": exact_search_performed,
        "scope": scope_filters,
        "literal_terms": [str(entry.get("term") or "") for entry in literal_terms if str(entry.get("term") or "")],
        "total_exact_matches": total_exact_matches,
        "matching_note_count": matching_note_count,
        "coverage_claims_allowed": bool(claim_policy.get("coverage_claims_allowed")) and exact_search_performed,
        "negative_claims_allowed": exact_search_performed and total_exact_matches == 0,
    }
    limits: list[str] = [f"Only {config.max_retrieval_chunks} chunk(s) may be selected for frontier synthesis."]
    if exact_search_performed:
        limits.append(f"Exact layer found {total_exact_matches} match(es) across {matching_note_count} note(s); synthesis uses a representative subset.")
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
        "coverage": coverage,
        "limits": limits,
        "graph_traversal": graph_traversal_info,
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
                "match_reason": str(candidate.get("match_reason") or candidate.get("selection_reason") or ""),
                "scope_match": candidate.get("scope_match"),
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
    if selected_count == 0 and (semantic_queries or graph_seeds):
        blocking_reasons.append("retrieval required but no chunks were selected")
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


def _build_visible_transcript_tail(messages: list[dict[str, Any]], limit: int = 6) -> list[dict[str, Any]]:
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
    return {
        "thread_id": thread_id,
        "turn_id": turn_id,
        "runtime_outcome": runtime_outcome,
        "blocking_reasons": blocking_reasons,
        "llm_mode": llm_metadata.get("mode"),
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
) -> dict[str, Any]:
    planner_retrieval_plan = semantic_compiler_packet.get("planner_retrieval_plan") if isinstance(semantic_compiler_packet.get("planner_retrieval_plan"), dict) else {}
    recent_messages = _ensure_message_list(prior_thread_state.get("recent_messages"))
    _append_turn_message(messages=recent_messages, role="user", content=raw_user_input, turn_id=turn_id)
    if assistant_response is not None:
        _append_turn_message(messages=recent_messages, role="assistant", content=assistant_response, turn_id=turn_id)
    recent_messages = recent_messages[-6:]
    recent_semantic_turns = _recent_semantic_turns_from_state(prior_thread_state.get("recent_semantic_turns"))
    recent_semantic_turns.append(
        _build_recent_semantic_turn(
            turn_id=turn_id,
            raw_user_input=raw_user_input,
            assistant_response=assistant_response,
            planner_retrieval_plan=planner_retrieval_plan,
            retrieval_packet=retrieval_packet,
        )
    )
    recent_semantic_turns = recent_semantic_turns[-RECENT_SEMANTIC_TURN_LIMIT:]
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
    recent_semantic_turns = _recent_semantic_turns_from_state(prior_thread_state.get("recent_semantic_turns"))
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
    )
    semantic_compiler_diagnostic = _semantic_compiler_diagnostic_packet(
        response=compiler_response,
        semantic_compiler_status=semantic_compiler_status,
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
        )
    bound_retrieval_plan, resolver_adjustments, resource_inventory_summary = bind_retrieval_plan(
        planner_retrieval_plan=planner_retrieval_plan,
        inventory_summary=resource_inventory_summary,
        config=resolved_config,
        raw_user_input=user_input,
    )

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
    visible_transcript_tail = _build_visible_transcript_tail(_ensure_message_list(conversation_thread.get("messages")))
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
    if runtime_outcome == "completed":
        llm_response = llm_backend.generate(synthesis_context_packet)
        assistant_response_text = llm_response.assistant_response
        llm_metadata = dict(llm_response.metadata)

    thread_state = _update_thread_state(
        prior_thread_state=prior_thread_state,
        thread_id=thread_id_value,
        turn_id=turn_id,
        raw_user_input=user_input,
        assistant_response=assistant_response_text,
        semantic_compiler_packet=semantic_compiler_packet,
        retrieval_packet=retrieval_packet,
        created_at=created_at,
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
