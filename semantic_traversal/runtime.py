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
from .hashing import sha256_json
from .llm import LLMBackend, LLMResponse
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


def _default_thread_state(thread_id: str, created_at: str) -> dict[str, Any]:
    return {
        "thread_id": thread_id,
        "latest_turn_id": 0,
        "conversation_summary": "",
        "recent_messages": [],
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
) -> dict[str, Any]:
    return {
        "raw_user_input": raw_user_input,
        "prior_thread_state": prior_thread_state,
        "recent_messages": recent_messages,
        "instruction": "Compile a minimal semantic target for traversal. Do not answer the user.",
    }


def _deterministic_semantic_packet(
    *,
    raw_user_input: str,
    prior_thread_state: dict[str, Any],
    recent_messages: list[dict[str, Any]],
    limitations: list[str] | None = None,
) -> dict[str, Any]:
    retrieval_terms = _extract_terms(raw_user_input)
    query = raw_user_input.strip()
    graph_seeds: list[str] = []
    if retrieval_terms:
        graph_seeds.append(query)
        if prior_thread_state.get("latest_user_input"):
            graph_seeds.append(str(prior_thread_state["latest_user_input"]).strip())
    if recent_messages and any(surface in raw_user_input.lower() for surface in REFERENTIAL_SURFACE_WORDS):
        last_message = recent_messages[-1].get("content")
        if isinstance(last_message, str) and last_message.strip():
            graph_seeds.append(last_message.strip())
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
    recent_messages: list[dict[str, Any]],
    payload: dict[str, Any] | None,
    fallback_limitations: list[str] | None = None,
) -> dict[str, Any]:
    fallback_packet = _deterministic_semantic_packet(
        raw_user_input=raw_user_input,
        prior_thread_state=prior_thread_state,
        recent_messages=recent_messages,
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
    recent_messages: list[dict[str, Any]],
    response: SemanticCompilerResponse,
) -> tuple[dict[str, Any], str]:
    payload = response.parsed_payload if isinstance(response.parsed_payload, dict) else None
    if response.status in {"parsed", "stub"} and payload is not None:
        return (
            _canonicalize_compiler_packet(
                raw_user_input=raw_user_input,
                prior_thread_state=prior_thread_state,
                recent_messages=recent_messages,
                payload=payload,
            ),
            response.status,
        )
    return (
        _deterministic_semantic_packet(
            raw_user_input=raw_user_input,
            prior_thread_state=prior_thread_state,
            recent_messages=recent_messages,
            limitations=["semantic compiler backend unavailable; deterministic lexical fallback used"],
        ),
        "fallback",
    )


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
    graph_seeds: list[str],
) -> tuple[list[dict[str, Any]], list[str]]:
    notes: list[str] = []
    if not graph_seeds:
        return [], ["graph search skipped: no graph seeds"]

    try:
        node_rows = connection.execute(
            f"SELECT node_id, node_type, label, ref_id, metadata_json FROM {config.graph_nodes_table}"
        ).fetchall()
        edge_rows = connection.execute(
            f"SELECT source_node_id, target_node_id, edge_type FROM {config.graph_edges_table}"
        ).fetchall()
    except sqlite3.OperationalError:
        return [], ["graph search unavailable"]

    chunk_rows = {row["chunk_id"]: row for row in _load_chunk_rows(connection)}
    nodes_by_id = {str(row["node_id"]): row for row in node_rows}
    nodes_by_key: dict[str, list[dict[str, Any]]] = {}
    for row in node_rows:
        for key in (row["label"], row["ref_id"]):
            normalized = _normalize_text(key)
            if normalized:
                nodes_by_key.setdefault(normalized, []).append(row)

    outgoing: dict[str, list[tuple[str, str]]] = {}
    for row in edge_rows:
        outgoing.setdefault(str(row["source_node_id"]), []).append((str(row["target_node_id"]), str(row["edge_type"])))

    selected_chunk_ids: set[str] = set()
    selection_notes: list[str] = []

    def add_note_chunk(note_id: str, reason: str) -> None:
        for chunk_row in chunk_rows.values():
            if str(chunk_row["note_id"]) == note_id and str(chunk_row["chunk_id"]) not in selected_chunk_ids:
                selected_chunk_ids.add(str(chunk_row["chunk_id"]))
                selection_notes.append(reason)

    for seed in graph_seeds:
        normalized = _normalize_text(seed)
        matched_nodes = nodes_by_key.get(normalized, [])
        if not matched_nodes:
            continue
        for node_row in matched_nodes:
            node_id = str(node_row["node_id"])
            node_type = str(node_row["node_type"])
            node_label = str(node_row["label"])
            if node_type == "chunk":
                chunk_row = chunk_rows.get(str(node_row["ref_id"] or ""))
                if chunk_row is not None:
                    selected_chunk_ids.add(str(chunk_row["chunk_id"]))
                    selection_notes.append(f"graph seed matched chunk node {node_label}")
            elif node_type == "note":
                note_id = str(node_row["ref_id"] or "")
                if note_id:
                    add_note_chunk(note_id, f"graph seed matched note node {node_label}")
                for target_id, edge_type in outgoing.get(node_id, []):
                    target_node = nodes_by_id.get(target_id)
                    if target_node is None:
                        continue
                    if str(target_node["node_type"]) == "note" and edge_type == "note_links_note":
                        target_note_id = str(target_node["ref_id"] or "")
                        if target_note_id:
                            add_note_chunk(target_note_id, f"graph hop via {node_label} -> {target_node['label']}")

    candidates = [dict(chunk_rows[chunk_id], selection_reason="graph expansion", score=1.5, selection_source="graph") for chunk_id in selected_chunk_ids if chunk_id in chunk_rows]
    if candidates:
        notes.append(f"graph search matched {len(candidates)} chunk(s)")
        notes.extend(selection_notes[:3])
    else:
        notes.append("graph search produced no matches")
    return candidates, notes


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
    embedding_backend: EmbeddingBackend,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    query_terms = list(semantic_compiler_packet.get("retrieval_terms") or [])
    vector_query = str(semantic_compiler_packet.get("vector_query") or "")
    graph_seeds = list(semantic_compiler_packet.get("graph_seeds") or [])

    chunk_rows = _load_chunk_rows(connection)
    lexical_candidates, lexical_notes = _lexical_candidates(chunk_rows, query_terms)
    vector_candidates, vector_notes = _vector_candidates(
        connection=connection,
        config=config,
        embedding_backend=embedding_backend,
        vector_query=vector_query,
    )
    graph_candidates, graph_notes = _graph_candidates(connection=connection, config=config, graph_seeds=graph_seeds)

    merged_candidates = _merge_candidates(lexical_candidates, vector_candidates, graph_candidates)
    selected_candidates = _select_retrieval_chunks(merged_candidates=merged_candidates, max_chunks=config.max_retrieval_chunks)

    traversal_manifest = {
        "query_terms": query_terms,
        "vector_query": vector_query,
        "graph_seeds": graph_seeds,
        "candidate_counts": {
            "lexical": len(lexical_candidates),
            "vector": len(vector_candidates),
            "graph": len(graph_candidates),
        },
        "selected_chunk_ids": [str(candidate["chunk_id"]) for candidate in selected_candidates],
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

    return traversal_manifest, retrieval_packet, {
        "lexical_candidates": lexical_candidates,
        "vector_candidates": vector_candidates,
        "graph_candidates": graph_candidates,
    }


def _coverage_report(
    *,
    semantic_compiler_packet: Any,
    traversal_manifest: dict[str, Any],
    retrieval_packet: dict[str, Any],
) -> dict[str, Any]:
    compiler_valid = _is_compiler_packet_valid(semantic_compiler_packet)
    selected_count = int(retrieval_packet.get("matched_chunk_count") or 0)
    blocking_reasons: list[str] = []
    if not compiler_valid:
        blocking_reasons.append("semantic compiler packet is missing or malformed")
    query_terms = list(traversal_manifest.get("query_terms") or [])
    graph_seeds = list(traversal_manifest.get("graph_seeds") or [])
    if selected_count == 0 and (query_terms or graph_seeds):
        blocking_reasons.append("retrieval required but no chunks were selected")
    return {
        "decision": "approved" if not blocking_reasons and (selected_count > 0 or not (query_terms or graph_seeds)) else "blocked",
        "blocking_reasons": blocking_reasons,
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
    created_at: str,
) -> dict[str, Any]:
    recent_messages = _ensure_message_list(prior_thread_state.get("recent_messages"))
    _append_turn_message(messages=recent_messages, role="user", content=raw_user_input, turn_id=turn_id)
    if assistant_response is not None:
        _append_turn_message(messages=recent_messages, role="assistant", content=assistant_response, turn_id=turn_id)
    recent_messages = recent_messages[-6:]
    thread_state = {
        "thread_id": thread_id,
        "latest_turn_id": turn_id,
        "conversation_summary": str(prior_thread_state.get("conversation_summary") or ""),
        "recent_messages": recent_messages,
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
    thread_id_value = str(conversation_thread.get("thread_id") or prior_thread_state.get("thread_id") or thread_state_path.parent.name)
    parent_perturbation_hash = conversation_thread.get("latest_perturbation_hash")
    turn_id = int(prior_thread_state.get("latest_turn_id") or 0) + 1
    created_at = _utc_now()
    turn_paths = create_thread_paths(resolved_data_root, config=resolved_config, thread_id=thread_id_value)
    turn_root = turn_paths.turn_root(turn_id)
    turn_root.mkdir(parents=True, exist_ok=True)

    recent_messages = _ensure_message_list(prior_thread_state.get("recent_messages"))
    compiler_request = _compiler_request_packet(
        raw_user_input=user_input,
        prior_thread_state=prior_thread_state,
        recent_messages=recent_messages,
    )
    compiler_backend = semantic_compiler_backend or resolve_semantic_compiler_backend(repo_root=resolved_repo_root, config=resolved_config)
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
        recent_messages=recent_messages,
        response=compiler_response,
    )

    database_path = _load_ingestion_database_path(data_root=resolved_data_root, config=resolved_config)
    if database_path.exists():
        if embedding_backend is None:
            embedding_backend = resolve_embedding_backend(resolved_config)
        connection = sqlite3.connect(database_path)
        try:
            connection.row_factory = sqlite3.Row
            semantic_traversal_manifest, retrieval_packet, _ = _semantic_traversal(
                connection=connection,
                config=resolved_config,
                semantic_compiler_packet=semantic_compiler_packet,
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
        traversal_manifest=semantic_traversal_manifest,
        retrieval_packet=retrieval_packet,
    )

    runtime_outcome = "completed" if coverage_report["decision"] == "approved" else "blocked"
    blocking_reasons = list(coverage_report.get("blocking_reasons") or [])
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
    semantic_traversal_manifest_path = turn_root / "semantic_traversal_manifest.json"
    retrieval_packet_path = turn_root / "retrieval_packet.json"
    coverage_report_path = turn_root / "coverage_report.json"
    synthesis_context_packet_path = turn_root / "synthesis_context_packet.json"
    state_delta_path = turn_root / "state_delta.json"

    write_json(semantic_compiler_packet_path, semantic_compiler_packet)
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
        semantic_traversal_manifest=semantic_traversal_manifest,
        retrieval_packet=retrieval_packet,
        coverage_report=coverage_report,
        synthesis_context_packet=synthesis_context_packet,
        state_delta=state_delta,
    )
