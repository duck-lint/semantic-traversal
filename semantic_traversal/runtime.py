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
    "the",
    "to",
    "with",
    "you",
    "your",
}
REFERENTIAL_SURFACE_WORDS = {"it", "that", "this", "those", "they", "them"}
RECENT_SEMANTIC_TURN_LIMIT = 6
ASSISTANT_SNIPPET_LIMIT = 120


def _default_active_focus() -> dict[str, Any]:
    return {
        "query": None,
        "entities": [],
        "relations": [],
        "retrieval_terms": [],
        "vector_query": None,
        "graph_seeds": [],
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
            candidate = entry.get("label") or entry.get("resolved_to") or entry.get("surface_form") or entry.get("value")
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
                "retrieval_terms": _coerce_string_list(entry.get("retrieval_terms")),
                "vector_query": str(entry.get("vector_query") or "").strip(),
                "graph_seeds": _coerce_string_list(entry.get("graph_seeds")),
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
    focus["retrieval_terms"] = _coerce_string_list(value.get("retrieval_terms"))
    focus["vector_query"] = str(value.get("vector_query") or "").strip() or None
    focus["graph_seeds"] = _coerce_string_list(value.get("graph_seeds"))
    focus["selected_chunk_ids"] = _coerce_string_list(value.get("selected_chunk_ids"))
    focus["selected_note_titles"] = _coerce_string_list(value.get("selected_note_titles"))
    focus["selected_section_labels"] = _coerce_string_list(value.get("selected_section_labels"))
    return focus


def _focus_terms(focus: dict[str, Any]) -> list[str]:
    terms: list[str] = []
    for value in (
        focus.get("retrieval_terms"),
        focus.get("graph_seeds"),
        focus.get("selected_note_titles"),
        focus.get("selected_section_labels"),
        [focus.get("query")],
        [focus.get("vector_query")],
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
        turn.get("retrieval_terms"),
        turn.get("vector_query"),
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
    semantic_compiler_packet: dict[str, Any],
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
    return {
        "query": str(semantic_compiler_packet.get("query") or "").strip() or None,
        "entities": _coerce_string_list(semantic_compiler_packet.get("entities")),
        "relations": _coerce_string_list(semantic_compiler_packet.get("relations")),
        "retrieval_terms": _coerce_string_list(semantic_compiler_packet.get("retrieval_terms")),
        "vector_query": str(semantic_compiler_packet.get("vector_query") or "").strip() or None,
        "graph_seeds": _coerce_string_list(semantic_compiler_packet.get("graph_seeds")),
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
    semantic_compiler_packet: dict[str, Any],
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
    return {
        "turn_id": turn_id,
        "raw_user_input": raw_user_input,
        "assistant_response_snippet": _snippet(assistant_response or ""),
        "query": str(semantic_compiler_packet.get("query") or "").strip(),
        "entities": _coerce_string_list(semantic_compiler_packet.get("entities")),
        "relations": _coerce_string_list(semantic_compiler_packet.get("relations")),
        "retrieval_terms": _coerce_string_list(semantic_compiler_packet.get("retrieval_terms")),
        "vector_query": str(semantic_compiler_packet.get("vector_query") or "").strip(),
        "graph_seeds": _coerce_string_list(semantic_compiler_packet.get("graph_seeds")),
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
) -> dict[str, Any]:
    return {
        "raw_user_input": raw_user_input,
        "prior_thread_state": prior_thread_state,
        "recent_messages": recent_messages,
        "recent_semantic_turns": recent_semantic_turns,
        "active_focus": active_focus,
        "instruction": "Compile a minimal semantic target for traversal. Do not answer the user.",
    }


def _deterministic_semantic_packet(
    *,
    raw_user_input: str,
    prior_thread_state: dict[str, Any],
    active_focus: dict[str, Any],
    recent_semantic_turns: list[dict[str, Any]],
    limitations: list[str] | None = None,
) -> dict[str, Any]:
    retrieval_terms = _extract_terms(raw_user_input)
    query = raw_user_input.strip()
    focus_terms: list[str] = []
    if _is_referential_user_input(raw_user_input) and len(retrieval_terms) < 4:
        focus_terms = _focus_terms(active_focus)
        for turn in recent_semantic_turns[-2:]:
            focus_terms.extend(_semantic_turn_focus_terms(turn))
        focus_terms = list(dict.fromkeys(term for term in focus_terms if term))
        retrieval_terms = list(dict.fromkeys([*retrieval_terms, *focus_terms]))
        if focus_terms:
            query = f"{query} {' '.join(focus_terms[:8])}".strip()
    graph_seeds: list[str] = []
    if retrieval_terms:
        graph_seeds.append(query)
        if prior_thread_state.get("latest_user_input"):
            graph_seeds.append(str(prior_thread_state["latest_user_input"]).strip())
    return {
        "raw_user_input": raw_user_input,
        "intent": "deterministic lexical fallback",
        "query": query,
        "entities": [],
        "relations": [],
        "resolved_referents": [],
        "retrieval_terms": retrieval_terms,
        "vector_query": query,
        "graph_seeds": list(dict.fromkeys(seed for seed in graph_seeds if seed)),
        "limitations": list(limitations or ["semantic compiler backend unavailable; deterministic lexical fallback used"]),
    }


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
    packet["retrieval_terms"] = _coerce_string_list(payload.get("retrieval_terms")) or packet["retrieval_terms"]
    packet["vector_query"] = str(payload.get("vector_query") or packet["query"]).strip() or packet["query"]
    packet["graph_seeds"] = _coerce_string_list(payload.get("graph_seeds")) or packet["graph_seeds"]
    packet["limitations"] = _coerce_string_list(payload.get("limitations")) or packet["limitations"]
    if not packet["retrieval_terms"]:
        packet["retrieval_terms"] = _extract_terms(packet["query"])
    if not packet["graph_seeds"] and packet["retrieval_terms"]:
        packet["graph_seeds"] = [packet["query"]]
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
        "retrieval_terms",
        "vector_query",
        "graph_seeds",
        "limitations",
    }
    if not required_keys.issubset(packet):
        return False
    if not isinstance(packet["raw_user_input"], str) or not isinstance(packet["query"], str):
        return False
    for key in ("entities", "relations", "resolved_referents", "retrieval_terms", "graph_seeds", "limitations"):
        if not isinstance(packet.get(key), list):
            return False
    if not isinstance(packet.get("vector_query"), str):
        return False
    return True


def _load_chunk_rows(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT
            chunk_id,
            note_id,
            source_root_label,
            relative_path,
            note_title,
            section_label,
            paragraph_text,
            chunk_hash
        FROM chunks
        """
    ).fetchall()
    return [dict(row) for row in rows]


def _graph_seed_values(
    *,
    semantic_compiler_packet: dict[str, Any],
    prior_thread_state: dict[str, Any],
    config: RuntimeConfig,
) -> list[tuple[str, str]]:
    seeds: list[tuple[str, str]] = []
    active_focus = _normalize_active_focus(prior_thread_state.get("active_focus"))
    configured_sources = set(config.graph_traversal_seed_sources)
    if "graph_seeds" in configured_sources:
        for value in _coerce_string_list(semantic_compiler_packet.get("graph_seeds")):
            seeds.append(("graph_seeds", value))
    if "retrieval_terms" in configured_sources:
        for value in _coerce_string_list(semantic_compiler_packet.get("retrieval_terms")):
            seeds.append(("retrieval_terms", value))
    if "active_focus" in configured_sources:
        for value in (
            active_focus.get("query"),
            active_focus.get("vector_query"),
            active_focus.get("retrieval_terms"),
            active_focus.get("graph_seeds"),
            active_focus.get("selected_note_titles"),
            active_focus.get("selected_section_labels"),
        ):
            for item in _coerce_string_list(value):
                seeds.append(("active_focus", item))
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


def _lexical_candidates(chunk_rows: list[dict[str, Any]], query_terms: list[str]) -> tuple[list[dict[str, Any]], list[str]]:
    candidates: list[dict[str, Any]] = []
    notes: list[str] = []
    if not query_terms:
        return candidates, notes
    for row in chunk_rows:
        haystack = " ".join(
            str(row.get(field) or "")
            for field in ("note_title", "section_label", "relative_path", "paragraph_text")
        ).lower()
        matched_terms = [term for term in query_terms if term in haystack]
        if not matched_terms:
            continue
        candidates.append(
            {
                **row,
                "selection_reason": f"lexical match: {', '.join(matched_terms)}",
                "score": len(matched_terms) + 2.0,
                "selection_source": "lexical",
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
        if chunk_row is None:
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
            }
        )
    if candidates:
        notes.append(f"vector search matched {len(candidates)} chunk(s)")
    else:
        notes.append("vector search produced no matches")
    return candidates, notes


def _graph_candidates(
    *,
    connection: sqlite3.Connection,
    config: RuntimeConfig,
    semantic_compiler_packet: dict[str, Any],
    prior_thread_state: dict[str, Any],
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
        semantic_compiler_packet=semantic_compiler_packet,
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

    selected_chunk_ids: list[str] = []
    for note_id in selected_note_ids:
        for chunk_row in chunk_rows.values():
            if str(chunk_row["note_id"]) != note_id:
                continue
            chunk_id = str(chunk_row["chunk_id"])
            if chunk_id in selected_chunk_ids:
                continue
            selected_chunk_ids.append(chunk_id)
            if len(selected_chunk_ids) >= config.graph_traversal_max_candidates:
                break
        if len(selected_chunk_ids) >= config.graph_traversal_max_candidates:
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


def _merge_candidates(
    lexical_candidates: list[dict[str, Any]],
    vector_candidates: list[dict[str, Any]],
    graph_candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    source_priority = {"lexical": 3, "vector": 2, "graph": 1}

    def absorb(candidate: dict[str, Any]) -> None:
        chunk_id = str(candidate["chunk_id"])
        existing = merged.get(chunk_id)
        source = str(candidate.get("selection_source") or "lexical")
        if existing is None:
            merged[chunk_id] = dict(candidate)
            merged[chunk_id]["sources"] = [source]
            return
        existing["sources"] = list(dict.fromkeys(existing.get("sources", []) + [source]))
        existing_score = float(existing.get("score") or 0.0)
        candidate_score = float(candidate.get("score") or 0.0)
        if candidate_score > existing_score or (
            candidate_score == existing_score
            and source_priority.get(source, 0) > source_priority.get(str(existing.get("selection_source") or ""), 0)
        ):
            merged[chunk_id] = dict(candidate)
            merged[chunk_id]["sources"] = list(dict.fromkeys(existing.get("sources", []) + [source]))
        else:
            existing["score"] = max(existing_score, candidate_score)
            existing["selection_reason"] = ", ".join(
                part for part in [existing.get("selection_reason"), candidate.get("selection_reason")] if part
            )

    for candidate in lexical_candidates + vector_candidates + graph_candidates:
        absorb(candidate)

    ranked = sorted(
        merged.values(),
        key=lambda candidate: (
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
) -> list[dict[str, Any]]:
    selected = merged_candidates[: max(0, max_chunks)]
    for candidate in selected:
        candidate.pop("sources", None)
    return selected


def _semantic_traversal(
    *,
    connection: sqlite3.Connection,
    config: RuntimeConfig,
    semantic_compiler_packet: dict[str, Any],
    prior_thread_state: dict[str, Any],
    embedding_backend: EmbeddingBackend,
) -> tuple[dict[str, Any], dict[str, Any]]:
    query_terms = list(semantic_compiler_packet.get("retrieval_terms") or [])
    vector_query = str(semantic_compiler_packet.get("vector_query") or "")

    chunk_rows = _load_chunk_rows(connection)
    lexical_candidates, lexical_notes = _lexical_candidates(chunk_rows, query_terms)
    vector_candidates, vector_notes = _vector_candidates(
        connection=connection,
        config=config,
        embedding_backend=embedding_backend,
        vector_query=vector_query,
    )
    graph_candidates, graph_notes, graph_traversal_info = _graph_candidates(
        connection=connection,
        config=config,
        semantic_compiler_packet=semantic_compiler_packet,
        prior_thread_state=prior_thread_state,
    )

    merged_candidates = _merge_candidates(lexical_candidates, vector_candidates, graph_candidates)
    selected_candidates = _select_retrieval_chunks(merged_candidates=merged_candidates, max_chunks=config.max_retrieval_chunks)

    traversal_manifest = {
        "query_terms": query_terms,
        "vector_query": vector_query,
        "graph_seeds": list(semantic_compiler_packet.get("graph_seeds") or []),
        "candidate_counts": {
            "lexical": len(lexical_candidates),
            "vector": len(vector_candidates),
            "graph": len(graph_candidates),
        },
        "selected_chunk_ids": [str(candidate["chunk_id"]) for candidate in selected_candidates],
        "graph_traversal": graph_traversal_info,
        "selection_notes": [*lexical_notes, *vector_notes, *graph_notes],
    }

    retrieval_packet = {
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
    query_terms = list(traversal_manifest.get("query_terms") or [])
    graph_seeds = list(traversal_manifest.get("graph_seeds") or [])
    if selected_count == 0 and (query_terms or graph_seeds):
        blocking_reasons.append("retrieval required but no chunks were selected")
    return {
        "decision": "approved" if not blocking_reasons and (selected_count > 0 or not (query_terms or graph_seeds)) else "blocked",
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
            "Answer directly and use only the approved retrieval packet if coverage is approved.",
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
            semantic_compiler_packet=semantic_compiler_packet,
            retrieval_packet=retrieval_packet,
        )
    )
    recent_semantic_turns = recent_semantic_turns[-RECENT_SEMANTIC_TURN_LIMIT:]
    active_focus = _compact_active_focus(
        semantic_compiler_packet=semantic_compiler_packet,
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

    compiler_request = _compiler_request_packet(
        raw_user_input=user_input,
        prior_thread_state=prior_thread_state,
        recent_messages=recent_messages,
        recent_semantic_turns=recent_semantic_turns,
        active_focus=active_focus,
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

    database_path = _load_ingestion_database_path(data_root=resolved_data_root, config=resolved_config)
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
            "query_terms": list(semantic_compiler_packet.get("retrieval_terms") or []),
            "vector_query": str(semantic_compiler_packet.get("vector_query") or ""),
            "graph_seeds": list(semantic_compiler_packet.get("graph_seeds") or []),
            "candidate_counts": {"lexical": 0, "vector": 0, "graph": 0},
            "selected_chunk_ids": [],
            "selection_notes": ["ingestion database unavailable"],
        }
        retrieval_packet = {
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
