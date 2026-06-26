from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .hashing import sha256_json, sha256_text
from .llm import LLMBackend
from .semantic_extraction import (
    DisabledSemanticExtractorBackend,
    SemanticExtractionResponse,
    SemanticExtractorBackend,
    extract_terms as extract_semantic_terms,
)
from .storage import append_ledger_record, create_thread_paths, load_json, read_ledger, write_json


@dataclass(frozen=True)
class TurnExecutionResult:
    thread_id: str
    turn_id: int
    thread_root: Path
    turn_root: Path
    conversation_thread_path: Path
    thread_state_path: Path
    thread_ledger_path: Path
    semantic_context_packet_path: Path
    semantic_traversal_manifest_path: Path
    retrieval_packet_path: Path
    coverage_report_path: Path
    synthesis_context_packet_path: Path
    state_delta_path: Path
    isolated_semantic_extraction_packet_path: Path
    isolated_semantic_extraction_raw_path: Path
    contextual_semantic_extraction_packet_path: Path
    contextual_semantic_extraction_raw_path: Path
    assistant_response: str
    llm_metadata: dict[str, Any]
    prior_thread_state: dict[str, Any]
    next_thread_state: dict[str, Any]
    ledger_record: dict[str, Any]
    semantic_context_packet: dict[str, Any]
    semantic_traversal_manifest: dict[str, Any]
    retrieval_packet: dict[str, Any]
    coverage_report: dict[str, Any]
    synthesis_context_packet: dict[str, Any]
    isolated_semantic_extraction_packet: dict[str, Any]
    contextual_semantic_extraction_packet: dict[str, Any]


@dataclass(frozen=True)
class SemanticExtractionArtifacts:
    isolated_packet: dict[str, Any]
    isolated_raw_artifact: dict[str, Any]
    contextual_packet: dict[str, Any]
    contextual_raw_artifact: dict[str, Any]


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


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
RETRIEVAL_LIMIT = 6


def _default_thread_document(thread_id: str, created_at: str) -> dict[str, Any]:
    return {
        "thread_id": thread_id,
        "created_at": created_at,
        "updated_at": created_at,
        "turn_count": 0,
        "latest_thread_state_hash": None,
        "latest_perturbation_hash": None,
        "ledger_record_count": 0,
        "messages": [],
    }


def _default_thread_state(thread_id: str, created_at: str) -> dict[str, Any]:
    return {
        "thread_id": thread_id,
        "latest_turn_id": 0,
        "conversation_summary": "",
        "recent_messages": [],
        "current_user_goals": [],
        "open_questions": [],
        "active_constraints": [],
        "recent_semantic_trajectory": [],
        "latest_user_input": None,
        "latest_assistant_response": None,
        "updated_at": created_at,
        "latest_thread_state_hash": None,
    }


def _build_synthesis_context_packet(
    thread_document: dict[str, Any],
    prior_thread_state: dict[str, Any],
    user_input: str,
    turn_id: int,
    semantic_context_packet: dict[str, Any],
    semantic_traversal_manifest: dict[str, Any],
    retrieval_packet: dict[str, Any],
    coverage_report: dict[str, Any],
) -> dict[str, Any]:
    return {
        "thread_id": thread_document["thread_id"],
        "turn_id": turn_id,
        "user_input": user_input,
        "raw_user_input": user_input,
        "prior_thread_state": prior_thread_state,
        "visible_transcript_tail": thread_document["messages"][-6:],
        "semantic_extraction": semantic_context_packet["semantic_extraction"],
        "semantic_context_packet": semantic_context_packet,
        "semantic_traversal_manifest": semantic_traversal_manifest,
        "retrieval_packet": retrieval_packet,
        "approved_retrieval_packet": retrieval_packet if coverage_report.get("retrieval_approved_for_synthesis") else None,
        "coverage_report": coverage_report,
        "output_requirements": [
            "Respond directly to the latest raw user input.",
            "Preserve continuity with the prior thread state.",
            "Use semantic extraction as non-authoritative additive context only.",
            "Use retrieved material only when present and relevant.",
            "Do not invent retrieval results.",
            "Preserve the raw user intent where extraction is uncertain.",
            "State retrieval limits if the retrieval packet is empty or partial.",
        ],
    }


def _build_semantic_context_packet(
    *,
    thread_document: dict[str, Any],
    prior_thread_state: dict[str, Any],
    user_input: str,
    turn_id: int,
    semantic_extraction: SemanticExtractionArtifacts,
) -> dict[str, Any]:
    retrieval_preparation = _build_retrieval_preparation(
        user_input=user_input,
        isolated_packet=semantic_extraction.isolated_packet,
        contextual_packet=semantic_extraction.contextual_packet,
    )
    return {
        "thread_id": thread_document["thread_id"],
        "turn_id": turn_id,
        "user_input": user_input,
        "raw_user_input": user_input,
        "extracted_lexical_query_terms": list(retrieval_preparation["raw_lexical_terms"]),
        "retrieval_preparation": retrieval_preparation,
        "semantic_extraction": {
            "isolated": semantic_extraction.isolated_packet,
            "contextual": semantic_extraction.contextual_packet,
            "statuses": {
                "backend_mode": semantic_extraction.isolated_packet["backend_mode"],
                "isolated_status": semantic_extraction.isolated_packet["status"],
                "contextual_status": semantic_extraction.contextual_packet["status"],
            },
        },
        "prior_thread_state_context": {
            "latest_turn_id": prior_thread_state.get("latest_turn_id", 0),
            "conversation_summary": prior_thread_state.get("conversation_summary", ""),
            "recent_semantic_trajectory": list(prior_thread_state.get("recent_semantic_trajectory") or []),
            "recent_messages": list(prior_thread_state.get("recent_messages") or [])[-4:],
        },
        "explicit_limitation": "raw user input remains authoritative; semantic extraction is additive only",
    }


def _build_retrieval_preparation(
    *,
    user_input: str,
    isolated_packet: dict[str, Any],
    contextual_packet: dict[str, Any],
) -> dict[str, Any]:
    raw_lexical_terms = _extract_lexical_query_terms(user_input)
    candidate_term_sources: dict[str, list[str]] = {}
    for term in raw_lexical_terms:
        candidate_term_sources.setdefault(term, []).append("raw_user_input")

    isolated_hint_terms = _collect_extraction_hint_terms(isolated_packet.get("parsed_payload"))
    contextual_hint_terms = _collect_extraction_hint_terms(contextual_packet.get("parsed_payload"))
    extraction_hint_terms: list[str] = []
    for term in isolated_hint_terms:
        if term not in extraction_hint_terms:
            extraction_hint_terms.append(term)
            candidate_term_sources.setdefault(term, []).append("isolated_semantic_extraction")
    for term in contextual_hint_terms:
        if term not in extraction_hint_terms:
            extraction_hint_terms.append(term)
        candidate_term_sources.setdefault(term, []).append("contextual_activation_hints")

    combined_candidate_terms: list[str] = []
    for term in raw_lexical_terms + extraction_hint_terms:
        if term not in combined_candidate_terms:
            combined_candidate_terms.append(term)

    model_proposed_only_terms = [
        term
        for term in extraction_hint_terms
        if "raw_user_input" not in candidate_term_sources.get(term, [])
    ]
    return {
        "raw_lexical_terms": raw_lexical_terms,
        "extraction_hint_terms": extraction_hint_terms,
        "combined_candidate_terms": combined_candidate_terms,
        "candidate_term_sources": candidate_term_sources,
        "model_proposed_only_terms": model_proposed_only_terms,
        "used_additively_for_retrieval": True,
    }


def _collect_extraction_hint_terms(parsed_payload: dict[str, Any] | None) -> list[str]:
    if not parsed_payload:
        return []
    collected_values: list[str] = []

    def extend_from(value: Any) -> None:
        if isinstance(value, str):
            collected_values.append(value)
        elif isinstance(value, list):
            for item in value:
                extend_from(item)
        elif isinstance(value, dict):
            for item in value.values():
                extend_from(item)

    for key in ("candidate_targets", "candidate_relations", "terms_or_phrases_not_to_discard"):
        extend_from(parsed_payload.get(key))
    activation_hints = parsed_payload.get("activation_hints")
    if isinstance(activation_hints, dict):
        extend_from(activation_hints)
    coverage_target = parsed_payload.get("coverage_target")
    if isinstance(coverage_target, dict):
        extend_from(coverage_target)

    terms: list[str] = []
    seen: set[str] = set()
    for value in collected_values:
        for term in extract_semantic_terms(value):
            if term not in seen:
                seen.add(term)
                terms.append(term)
    return terms


def _build_lexical_retrieval_artifacts(
    *,
    repo_root: Path,
    data_root: Path,
    semantic_context_packet: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    retrieval_preparation = dict(semantic_context_packet.get("retrieval_preparation") or {})
    raw_lexical_terms = list(retrieval_preparation.get("raw_lexical_terms") or [])
    extraction_hint_terms = list(retrieval_preparation.get("extraction_hint_terms") or [])
    query_terms = list(retrieval_preparation.get("combined_candidate_terms") or [])
    database_path = (data_root / "ingestion" / "latent_space.sqlite3").resolve()
    traversal_manifest: dict[str, Any] = {
        "thread_id": semantic_context_packet["thread_id"],
        "turn_id": semantic_context_packet["turn_id"],
        "query_terms": query_terms,
        "query_terms_available": bool(query_terms),
        "retrieval_mode": "lexical_sqlite",
        "selected_chunk_ids": [],
        "candidate_count": 0,
        "selection_reasons": [],
        "limitations": [
            "no vector search",
            "no graph expansion",
            "no coverage loop",
            "lexical_sqlite only",
            "semantic extraction is additive only and not authoritative",
        ],
        "database_path": str(database_path),
        "repo_root": str(repo_root),
        "retrieval_preparation": retrieval_preparation,
    }
    retrieval_packet: dict[str, Any] = {
        "thread_id": semantic_context_packet["thread_id"],
        "turn_id": semantic_context_packet["turn_id"],
        "query_terms": query_terms,
        "raw_lexical_terms": raw_lexical_terms,
        "extraction_hint_terms": extraction_hint_terms,
        "combined_candidate_terms": query_terms,
        "candidate_term_sources": dict(retrieval_preparation.get("candidate_term_sources") or {}),
        "model_proposed_only_terms": list(retrieval_preparation.get("model_proposed_only_terms") or []),
        "query_terms_available": bool(query_terms),
        "retrieval_mode": "lexical_sqlite",
        "selection_limit": RETRIEVAL_LIMIT,
        "candidate_count": 0,
        "matched_chunk_count": 0,
        "approved_for_synthesis": False,
        "retrieval_status": "not_attempted",
        "selected_chunks": [],
        "database_path": str(database_path),
    }
    coverage_report: dict[str, Any] = {
        "status": "not_attempted",
        "matched_chunk_count": 0,
        "candidate_count": 0,
        "query_terms_used": query_terms,
        "raw_lexical_terms": raw_lexical_terms,
        "extraction_hint_terms": extraction_hint_terms,
        "limits": {
            "selection_limit": RETRIEVAL_LIMIT,
        },
        "semantic_extraction": {
            "backend_mode": semantic_context_packet["semantic_extraction"]["statuses"]["backend_mode"],
            "isolated_status": semantic_context_packet["semantic_extraction"]["statuses"]["isolated_status"],
            "contextual_status": semantic_context_packet["semantic_extraction"]["statuses"]["contextual_status"],
            "used_additively_for_retrieval": True,
            "limitations": [
                "semantic extraction is additive only",
                "semantic extraction is not authoritative",
            ],
        },
        "retrieval_approved_for_synthesis": False,
    }

    if not database_path.exists():
        traversal_manifest["selection_reasons"].append("ingestion SQLite database not found")
        coverage_report["status"] = "no_index"
        retrieval_packet["retrieval_status"] = "no_index"
        return traversal_manifest, retrieval_packet, coverage_report, _chunkless_retrieval_hashes(
            traversal_manifest, retrieval_packet, coverage_report
        )

    if not query_terms:
        traversal_manifest["selection_reasons"].append("no lexical or additive extraction candidate terms were available")
        coverage_report["status"] = "no_query_terms"
        retrieval_packet["retrieval_status"] = "no_query_terms"
        return traversal_manifest, retrieval_packet, coverage_report, _chunkless_retrieval_hashes(
            traversal_manifest, retrieval_packet, coverage_report
        )

    connection = sqlite3.connect(database_path)
    try:
        connection.row_factory = sqlite3.Row
        candidates = _query_lexical_candidates(connection, query_terms=query_terms)
    finally:
        connection.close()

    traversal_manifest["candidate_count"] = len(candidates)
    retrieval_packet["candidate_count"] = len(candidates)
    coverage_report["candidate_count"] = len(candidates)

    if not candidates:
        traversal_manifest["selection_reasons"].append("no chunk text or metadata matched the lexical candidate terms")
        coverage_report["status"] = "no_matches"
        retrieval_packet["retrieval_status"] = "no_matches"
        return traversal_manifest, retrieval_packet, coverage_report, _chunkless_retrieval_hashes(
            traversal_manifest, retrieval_packet, coverage_report
        )

    selected_chunks = candidates[:RETRIEVAL_LIMIT]
    traversal_manifest["selected_chunk_ids"] = [chunk["chunk_id"] for chunk in selected_chunks]
    traversal_manifest["selection_reasons"] = [chunk["selection_reason"] for chunk in selected_chunks]
    coverage_report["status"] = "minimal_pass"
    coverage_report["matched_chunk_count"] = len(selected_chunks)
    coverage_report["retrieval_approved_for_synthesis"] = True
    retrieval_packet["matched_chunk_count"] = len(selected_chunks)
    retrieval_packet["approved_for_synthesis"] = True
    retrieval_packet["retrieval_status"] = "minimal_pass"
    retrieval_packet["selected_chunks"] = selected_chunks
    return traversal_manifest, retrieval_packet, coverage_report, _chunkless_retrieval_hashes(
        traversal_manifest, retrieval_packet, coverage_report
    )


def _chunkless_retrieval_hashes(
    traversal_manifest: dict[str, Any],
    retrieval_packet: dict[str, Any],
    coverage_report: dict[str, Any],
) -> dict[str, str]:
    return {
        "semantic_traversal_manifest_hash": sha256_json(traversal_manifest),
        "retrieval_packet_hash": sha256_json(retrieval_packet),
        "coverage_report_hash": sha256_json(coverage_report),
    }


def _query_lexical_candidates(connection: sqlite3.Connection, *, query_terms: list[str]) -> list[dict[str, Any]]:
    clauses: list[str] = []
    parameters: list[str] = []
    for term in query_terms:
        like_term = f"%{term}%"
        term_clause = []
        for field in ("paragraph_text", "note_title", "section_label", "relative_path", "note_path"):
            term_clause.append(f"lower({field}) LIKE ?")
            parameters.append(like_term)
        clauses.append("(" + " OR ".join(term_clause) + ")")

    sql = (
        "SELECT chunk_id, note_id, source_root_label, relative_path, note_path, note_title, "
        "section_id, section_label, section_path_json, paragraph_ordinal, paragraph_text, chunk_hash "
        "FROM chunks WHERE "
        + " OR ".join(clauses)
    )
    rows = connection.execute(sql, tuple(parameters)).fetchall()
    candidates: list[dict[str, Any]] = []
    for row in rows:
        searchable_text = " ".join(
            [
                str(row["note_title"]),
                str(row["section_label"]),
                str(row["relative_path"]),
                str(row["paragraph_text"]),
            ]
        ).lower()
        matched_terms = [term for term in query_terms if term in searchable_text]
        selection_reason = f"matched {len(matched_terms)} candidate term(s): {', '.join(matched_terms) if matched_terms else 'none'}"
        section_path = json.loads(row["section_path_json"]) if row["section_path_json"] else []
        candidates.append(
            {
                "chunk_id": row["chunk_id"],
                "note_id": row["note_id"],
                "note_title": row["note_title"],
                "relative_path": row["relative_path"],
                "section_id": row["section_id"],
                "section_label": row["section_label"],
                "section_path": section_path,
                "paragraph_ordinal": row["paragraph_ordinal"],
                "paragraph_text": row["paragraph_text"],
                "chunk_hash": row["chunk_hash"],
                "source_root_label": row["source_root_label"],
                "selection_score": len(matched_terms),
                "matched_terms": matched_terms,
                "selection_reason": selection_reason,
            }
        )
    candidates.sort(key=lambda item: (-item["selection_score"], item["chunk_id"]))
    return candidates


def _extract_lexical_query_terms(user_input: str) -> list[str]:
    terms: list[str] = []
    seen: set[str] = set()
    for token in QUERY_TOKEN_RE.findall(user_input.lower()):
        if len(token) < 3 or token in STOP_WORDS:
            continue
        if token.isdigit():
            continue
        if token not in seen:
            seen.add(token)
            terms.append(token)
    return terms


def _build_isolated_extraction_request(user_input: str) -> dict[str, Any]:
    return {
        "mode": "isolated",
        "raw_user_input": user_input,
        "instruction": (
            "Extract additive semantic structure from the raw user message. "
            "Do not answer the user. Do not remove or rewrite the input. Preserve uncertainty."
        ),
    }


def _build_contextual_extraction_request(
    *,
    user_input: str,
    prior_thread_state: dict[str, Any],
    isolated_semantic_extraction: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "mode": "contextual",
        "raw_user_input": user_input,
        "prior_thread_state": prior_thread_state,
        "isolated_semantic_extraction": isolated_semantic_extraction or {},
        "instruction": (
            "Hydrate the isolated extraction with conversation context. "
            "Do not answer the user. Preserve the raw message. "
            "Record what changed between isolated and contextual readings."
        ),
    }


def _build_extraction_packet(
    *,
    thread_id: str,
    turn_id: int,
    backend_mode: str,
    request_packet: dict[str, Any],
    response: SemanticExtractionResponse,
) -> dict[str, Any]:
    limitations = []
    if isinstance(response.parsed_payload, dict):
        limitations = list(response.parsed_payload.get("limitations") or [])
    if not limitations:
        limitations = [
            "model-generated extraction",
            "additive only",
            "not authoritative",
        ]
    return {
        "thread_id": thread_id,
        "turn_id": turn_id,
        "mode": request_packet["mode"],
        "backend_mode": backend_mode,
        "raw_user_input": request_packet["raw_user_input"],
        "request_packet": request_packet,
        "status": response.status,
        "parsed_payload": response.parsed_payload,
        "metadata": response.metadata,
        "limitations": limitations,
    }


def _build_raw_response_artifact(
    *,
    thread_id: str,
    turn_id: int,
    mode: str,
    backend_mode: str,
    response: SemanticExtractionResponse,
) -> dict[str, Any]:
    if response.raw_response is None:
        note = f"{backend_mode} backend did not emit a raw model response"
    else:
        note = "raw model response persisted"
    return {
        "thread_id": thread_id,
        "turn_id": turn_id,
        "mode": mode,
        "backend_mode": backend_mode,
        "status": response.status,
        "raw_response_available": response.raw_response is not None,
        "raw_response": response.raw_response,
        "metadata": response.metadata,
        "note": note,
    }


def _run_semantic_extraction(
    *,
    thread_id: str,
    turn_id: int,
    user_input: str,
    prior_thread_state: dict[str, Any],
    backend: SemanticExtractorBackend,
) -> SemanticExtractionArtifacts:
    isolated_request = _build_isolated_extraction_request(user_input)
    isolated_response = backend.extract_isolated(isolated_request)
    isolated_packet = _build_extraction_packet(
        thread_id=thread_id,
        turn_id=turn_id,
        backend_mode=backend.mode_name,
        request_packet=isolated_request,
        response=isolated_response,
    )
    isolated_raw_artifact = _build_raw_response_artifact(
        thread_id=thread_id,
        turn_id=turn_id,
        mode="isolated",
        backend_mode=backend.mode_name,
        response=isolated_response,
    )

    contextual_request = _build_contextual_extraction_request(
        user_input=user_input,
        prior_thread_state=prior_thread_state,
        isolated_semantic_extraction=isolated_response.parsed_payload,
    )
    contextual_response = backend.extract_contextual(contextual_request)
    contextual_packet = _build_extraction_packet(
        thread_id=thread_id,
        turn_id=turn_id,
        backend_mode=backend.mode_name,
        request_packet=contextual_request,
        response=contextual_response,
    )
    contextual_raw_artifact = _build_raw_response_artifact(
        thread_id=thread_id,
        turn_id=turn_id,
        mode="contextual",
        backend_mode=backend.mode_name,
        response=contextual_response,
    )
    return SemanticExtractionArtifacts(
        isolated_packet=isolated_packet,
        isolated_raw_artifact=isolated_raw_artifact,
        contextual_packet=contextual_packet,
        contextual_raw_artifact=contextual_raw_artifact,
    )


def _persist_turn_artifacts(
    *,
    turn_root: Path,
    semantic_context_packet: dict[str, Any],
    semantic_traversal_manifest: dict[str, Any],
    retrieval_packet: dict[str, Any],
    coverage_report: dict[str, Any],
    synthesis_context_packet: dict[str, Any],
    state_delta: dict[str, Any],
    semantic_extraction: SemanticExtractionArtifacts,
) -> dict[str, Path]:
    turn_root.mkdir(parents=True, exist_ok=True)
    semantic_context_packet_path = turn_root / "semantic_context_packet.json"
    semantic_traversal_manifest_path = turn_root / "semantic_traversal_manifest.json"
    retrieval_packet_path = turn_root / "retrieval_packet.json"
    coverage_report_path = turn_root / "coverage_report.json"
    synthesis_context_packet_path = turn_root / "synthesis_context_packet.json"
    state_delta_path = turn_root / "state_delta.json"
    isolated_semantic_extraction_packet_path = turn_root / "isolated_semantic_extraction_packet.json"
    isolated_semantic_extraction_raw_path = turn_root / "isolated_semantic_extraction_raw.json"
    contextual_semantic_extraction_packet_path = turn_root / "contextual_semantic_extraction_packet.json"
    contextual_semantic_extraction_raw_path = turn_root / "contextual_semantic_extraction_raw.json"
    write_json(semantic_context_packet_path, semantic_context_packet)
    write_json(semantic_traversal_manifest_path, semantic_traversal_manifest)
    write_json(retrieval_packet_path, retrieval_packet)
    write_json(coverage_report_path, coverage_report)
    write_json(synthesis_context_packet_path, synthesis_context_packet)
    write_json(state_delta_path, state_delta)
    write_json(isolated_semantic_extraction_packet_path, semantic_extraction.isolated_packet)
    write_json(isolated_semantic_extraction_raw_path, semantic_extraction.isolated_raw_artifact)
    write_json(contextual_semantic_extraction_packet_path, semantic_extraction.contextual_packet)
    write_json(contextual_semantic_extraction_raw_path, semantic_extraction.contextual_raw_artifact)
    return {
        "semantic_context_packet_path": semantic_context_packet_path,
        "semantic_traversal_manifest_path": semantic_traversal_manifest_path,
        "retrieval_packet_path": retrieval_packet_path,
        "coverage_report_path": coverage_report_path,
        "synthesis_context_packet_path": synthesis_context_packet_path,
        "state_delta_path": state_delta_path,
        "isolated_semantic_extraction_packet_path": isolated_semantic_extraction_packet_path,
        "isolated_semantic_extraction_raw_path": isolated_semantic_extraction_raw_path,
        "contextual_semantic_extraction_packet_path": contextual_semantic_extraction_packet_path,
        "contextual_semantic_extraction_raw_path": contextual_semantic_extraction_raw_path,
    }


def _project_next_thread_state(
    thread_id: str,
    prior_thread_state: dict[str, Any],
    user_input: str,
    assistant_response: str,
    turn_id: int,
    timestamp: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    prior_recent_messages = list(prior_thread_state.get("recent_messages") or [])
    updated_recent_messages = (
        prior_recent_messages
        + [
            {"role": "user", "content": user_input},
            {"role": "assistant", "content": assistant_response},
        ]
    )[-6:]
    next_thread_state = {
        "thread_id": thread_id,
        "latest_turn_id": turn_id,
        "conversation_summary": assistant_response,
        "recent_messages": updated_recent_messages,
        "current_user_goals": [user_input],
        "open_questions": list(prior_thread_state.get("open_questions") or []),
        "active_constraints": list(prior_thread_state.get("active_constraints") or []),
        "recent_semantic_trajectory": [message["content"] for message in updated_recent_messages[-4:]],
        "latest_user_input": user_input,
        "latest_assistant_response": assistant_response,
        "updated_at": timestamp,
    }
    next_thread_state_hash = sha256_json(next_thread_state)
    next_thread_state["latest_thread_state_hash"] = next_thread_state_hash
    state_delta = {
        "from_turn_id": prior_thread_state.get("latest_turn_id", 0),
        "to_turn_id": turn_id,
        "latest_user_input": user_input,
        "latest_assistant_response": assistant_response,
        "latest_thread_state_hash": next_thread_state_hash,
    }
    return next_thread_state, state_delta


def run_thread_turn(
    *,
    repo_root: Path,
    data_root: Path,
    user_input: str,
    llm_backend: LLMBackend,
    thread_id: str | None = None,
    semantic_extractor_backend: SemanticExtractorBackend | None = None,
) -> TurnExecutionResult:
    timestamp = _utc_now()
    paths = create_thread_paths(data_root=data_root, thread_id=thread_id)

    thread_document = load_json(paths.conversation_thread_path) or _default_thread_document(paths.thread_id, timestamp)
    prior_thread_state = load_json(paths.thread_state_path) or _default_thread_state(paths.thread_id, timestamp)
    ledger_records = read_ledger(paths.thread_ledger_path)
    turn_id = len(ledger_records) + 1
    parent_perturbation_hash = ledger_records[-1]["state_perturbation_hash"] if ledger_records else None
    prior_thread_state_hash = prior_thread_state.get("latest_thread_state_hash")
    turn_root = paths.turn_root(turn_id)
    extractor_backend = semantic_extractor_backend or DisabledSemanticExtractorBackend()

    semantic_extraction = _run_semantic_extraction(
        thread_id=paths.thread_id,
        turn_id=turn_id,
        user_input=user_input,
        prior_thread_state=prior_thread_state,
        backend=extractor_backend,
    )
    semantic_context_packet = _build_semantic_context_packet(
        thread_document=thread_document,
        prior_thread_state=prior_thread_state,
        user_input=user_input,
        turn_id=turn_id,
        semantic_extraction=semantic_extraction,
    )
    semantic_traversal_manifest, retrieval_packet, coverage_report, retrieval_hashes = _build_lexical_retrieval_artifacts(
        repo_root=repo_root,
        data_root=data_root,
        semantic_context_packet=semantic_context_packet,
    )

    synthesis_context_packet = _build_synthesis_context_packet(
        thread_document=thread_document,
        prior_thread_state=prior_thread_state,
        user_input=user_input,
        turn_id=turn_id,
        semantic_context_packet=semantic_context_packet,
        semantic_traversal_manifest=semantic_traversal_manifest,
        retrieval_packet=retrieval_packet,
        coverage_report=coverage_report,
    )
    semantic_context_packet_hash = sha256_json(semantic_context_packet)
    semantic_traversal_manifest_hash = retrieval_hashes["semantic_traversal_manifest_hash"]
    retrieval_packet_hash = retrieval_hashes["retrieval_packet_hash"]
    coverage_report_hash = retrieval_hashes["coverage_report_hash"]
    synthesis_context_packet_hash = sha256_json(synthesis_context_packet)
    isolated_semantic_extraction_packet_hash = sha256_json(semantic_extraction.isolated_packet)
    isolated_semantic_extraction_raw_hash = sha256_json(semantic_extraction.isolated_raw_artifact)
    contextual_semantic_extraction_packet_hash = sha256_json(semantic_extraction.contextual_packet)
    contextual_semantic_extraction_raw_hash = sha256_json(semantic_extraction.contextual_raw_artifact)
    llm_response = llm_backend.generate(synthesis_context_packet)

    next_thread_state, state_delta = _project_next_thread_state(
        thread_id=paths.thread_id,
        prior_thread_state=prior_thread_state,
        user_input=user_input,
        assistant_response=llm_response.assistant_response,
        turn_id=turn_id,
        timestamp=timestamp,
    )

    artifact_paths = _persist_turn_artifacts(
        turn_root=turn_root,
        semantic_context_packet=semantic_context_packet,
        semantic_traversal_manifest=semantic_traversal_manifest,
        retrieval_packet=retrieval_packet,
        coverage_report=coverage_report,
        synthesis_context_packet=synthesis_context_packet,
        state_delta=state_delta,
        semantic_extraction=semantic_extraction,
    )

    user_message = {"role": "user", "content": user_input, "turn_id": turn_id, "timestamp": timestamp}
    assistant_message = {
        "role": "assistant",
        "content": llm_response.assistant_response,
        "turn_id": turn_id,
        "timestamp": timestamp,
    }
    thread_document["messages"] = list(thread_document.get("messages") or []) + [user_message, assistant_message]
    thread_document["turn_count"] = turn_id
    thread_document["updated_at"] = timestamp
    thread_document["latest_thread_state_hash"] = next_thread_state["latest_thread_state_hash"]

    ledger_record_base = {
        "thread_id": paths.thread_id,
        "turn_id": turn_id,
        "timestamp": timestamp,
        "parent_perturbation_hash": parent_perturbation_hash,
        "prior_thread_state_hash": prior_thread_state_hash,
        "user_input_hash": sha256_text(user_input),
        "isolated_semantic_extraction_packet_hash": isolated_semantic_extraction_packet_hash,
        "isolated_semantic_extraction_raw_hash": isolated_semantic_extraction_raw_hash,
        "contextual_semantic_extraction_packet_hash": contextual_semantic_extraction_packet_hash,
        "contextual_semantic_extraction_raw_hash": contextual_semantic_extraction_raw_hash,
        "semantic_context_packet_hash": semantic_context_packet_hash,
        "semantic_traversal_manifest_hash": semantic_traversal_manifest_hash,
        "retrieval_packet_hash": retrieval_packet_hash,
        "coverage_report_hash": coverage_report_hash,
        "synthesis_context_packet_hash": synthesis_context_packet_hash,
        "assistant_response_hash": sha256_text(llm_response.assistant_response),
        "state_delta_hash": sha256_json(state_delta),
        "next_thread_state_hash": next_thread_state["latest_thread_state_hash"],
        "llm_call_metadata": llm_response.metadata,
    }
    state_perturbation_hash = sha256_json(ledger_record_base)
    ledger_record = dict(ledger_record_base)
    ledger_record["state_perturbation_hash"] = state_perturbation_hash

    append_ledger_record(paths.thread_ledger_path, ledger_record)

    thread_document["latest_perturbation_hash"] = state_perturbation_hash
    thread_document["ledger_record_count"] = turn_id

    write_json(paths.thread_state_path, next_thread_state)
    write_json(paths.conversation_thread_path, thread_document)

    return TurnExecutionResult(
        thread_id=paths.thread_id,
        turn_id=turn_id,
        thread_root=paths.thread_root,
        turn_root=turn_root,
        conversation_thread_path=paths.conversation_thread_path,
        thread_state_path=paths.thread_state_path,
        thread_ledger_path=paths.thread_ledger_path,
        semantic_context_packet_path=artifact_paths["semantic_context_packet_path"],
        semantic_traversal_manifest_path=artifact_paths["semantic_traversal_manifest_path"],
        retrieval_packet_path=artifact_paths["retrieval_packet_path"],
        coverage_report_path=artifact_paths["coverage_report_path"],
        synthesis_context_packet_path=artifact_paths["synthesis_context_packet_path"],
        state_delta_path=artifact_paths["state_delta_path"],
        isolated_semantic_extraction_packet_path=artifact_paths["isolated_semantic_extraction_packet_path"],
        isolated_semantic_extraction_raw_path=artifact_paths["isolated_semantic_extraction_raw_path"],
        contextual_semantic_extraction_packet_path=artifact_paths["contextual_semantic_extraction_packet_path"],
        contextual_semantic_extraction_raw_path=artifact_paths["contextual_semantic_extraction_raw_path"],
        assistant_response=llm_response.assistant_response,
        llm_metadata=llm_response.metadata,
        prior_thread_state=prior_thread_state,
        next_thread_state=next_thread_state,
        ledger_record=ledger_record,
        semantic_context_packet=semantic_context_packet,
        semantic_traversal_manifest=semantic_traversal_manifest,
        retrieval_packet=retrieval_packet,
        coverage_report=coverage_report,
        synthesis_context_packet=synthesis_context_packet,
        isolated_semantic_extraction_packet=semantic_extraction.isolated_packet,
        contextual_semantic_extraction_packet=semantic_extraction.contextual_packet,
    )
