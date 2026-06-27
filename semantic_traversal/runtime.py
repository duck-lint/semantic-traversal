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
from .semantic_extraction import (
    SemanticExtractionResponse,
    SemanticExtractorBackend,
    extract_terms as extract_semantic_terms,
    resolve_semantic_extractor_backend,
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
    assistant_response: str | None
    llm_metadata: dict[str, Any]
    runtime_outcome: str
    blocking_reasons: list[str]
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


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


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


def _dedupe_reasons(reasons: list[str]) -> list[str]:
    deduped: list[str] = []
    for reason in reasons:
        if reason not in deduped:
            deduped.append(reason)
    return deduped


def _extract_lexical_query_terms(user_input: str) -> list[str]:
    terms: list[str] = []
    seen: set[str] = set()
    for token in QUERY_TOKEN_RE.findall(user_input.lower()):
        if len(token) < 3 or token in STOP_WORDS or token.isdigit():
            continue
        if token not in seen:
            seen.add(token)
            terms.append(token)
    return terms


def _collect_string_terms(value: Any) -> list[str]:
    if isinstance(value, str):
        return extract_semantic_terms(value)
    if isinstance(value, list):
        terms: list[str] = []
        for item in value:
            if isinstance(item, str):
                for term in extract_semantic_terms(item):
                    if term not in terms:
                        terms.append(term)
        return terms
    return []


def _collect_extraction_hint_terms(
    parsed_payload: dict[str, Any] | None,
    *,
    extraction_mode: str,
) -> tuple[list[str], dict[str, list[str]]]:
    if not parsed_payload:
        return [], {}

    allowed_fields: list[tuple[str, Any]] = []
    if extraction_mode == "isolated":
        allowed_fields = [
            ("isolated.candidate_targets", parsed_payload.get("candidate_targets")),
            ("isolated.candidate_relations", parsed_payload.get("candidate_relations")),
            ("isolated.terms_or_phrases_not_to_discard", parsed_payload.get("terms_or_phrases_not_to_discard")),
        ]
    elif extraction_mode == "contextual":
        activation_hints = parsed_payload.get("activation_hints")
        if isinstance(activation_hints, dict):
            allowed_fields = [
                ("contextual.activation_hints.lexical_terms", activation_hints.get("lexical_terms")),
                ("contextual.activation_hints.phrases", activation_hints.get("phrases")),
                ("contextual.activation_hints.entity_hints", activation_hints.get("entity_hints")),
                ("contextual.activation_hints.relation_hints", activation_hints.get("relation_hints")),
            ]

    terms: list[str] = []
    sources: dict[str, list[str]] = {}
    for source_label, value in allowed_fields:
        for term in _collect_string_terms(value):
            if term not in terms:
                terms.append(term)
            if source_label not in sources.setdefault(term, []):
                sources[term].append(source_label)
    return terms, sources


def _extract_semantic_outputs(
    *,
    isolated_packet: dict[str, Any],
    contextual_packet: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    isolated_payload = isolated_packet.get("parsed_payload") if isinstance(isolated_packet.get("parsed_payload"), dict) else {}
    contextual_payload = contextual_packet.get("parsed_payload") if isinstance(contextual_packet.get("parsed_payload"), dict) else {}
    semantic_payload = contextual_payload or isolated_payload
    semantic_coverage_target = semantic_payload.get("semantic_coverage_target") or semantic_payload.get("coverage_target")
    return (
        semantic_payload if isinstance(semantic_payload, dict) else {},
        semantic_coverage_target if isinstance(semantic_coverage_target, dict) else None,
    )


def _validate_semantic_context_payload(payload: dict[str, Any], *, raw_user_input: str) -> list[str]:
    reasons: list[str] = []
    if payload.get("raw_user_input") != raw_user_input:
        reasons.append("semantic extraction did not preserve raw_user_input")
    required_fields = {
        "perturbation_nodes": list,
        "contextual_salt_nodes": list,
        "perturbation_semantic_graph": dict,
        "semantic_coverage_target": dict,
        "activation_hints": dict,
        "limitations": list,
    }
    for field_name, expected_type in required_fields.items():
        value = payload.get(field_name)
        if not isinstance(value, expected_type):
            reasons.append(f"{field_name} missing")
    return reasons


def _normalize_coverage_text(value: Any) -> str:
    normalized = re.sub(r"[^a-z0-9]+", " ", str(value).lower())
    return re.sub(r"\s+", " ", normalized).strip()


def _coverage_target_tokens(value: Any) -> list[str]:
    tokens: list[str] = []
    seen: set[str] = set()
    for token in QUERY_TOKEN_RE.findall(str(value).lower()):
        if len(token) < 3 or token in STOP_WORDS or token.isdigit():
            continue
        if token not in seen:
            seen.add(token)
            tokens.append(token)
    return tokens


def _chunk_evidence_fields(chunk: dict[str, Any]) -> list[tuple[str, str]]:
    return [
        ("paragraph_text", str(chunk.get("paragraph_text") or "")),
        ("note_title", str(chunk.get("note_title") or "")),
        ("section_label", str(chunk.get("section_label") or "")),
        ("relative_path", str(chunk.get("relative_path") or "")),
        ("note_path", str(chunk.get("note_path") or "")),
        ("section_path", " / ".join(str(item) for item in list(chunk.get("section_path") or []))),
        ("frontmatter", json.dumps(chunk.get("frontmatter") or {}, sort_keys=True, ensure_ascii=True)),
    ]


def _build_evidence_excerpt(text: str, target: str, *, max_length: int = 200) -> str:
    raw_text = str(text)
    if not raw_text:
        return ""
    lowered = raw_text.lower()
    target_lower = str(target).lower().strip()
    if target_lower:
        index = lowered.find(target_lower)
        if index >= 0:
            start = max(0, index - 60)
            end = min(len(raw_text), index + len(target_lower) + 120)
            return raw_text[start:end].strip()
    return raw_text[:max_length].strip()


def _match_target_to_evidence_fields(target: str, chunk: dict[str, Any]) -> dict[str, Any] | None:
    normalized_target = _normalize_coverage_text(target)
    target_tokens = _coverage_target_tokens(target)
    if not normalized_target and not target_tokens:
        return None
    for field_name, field_text in _chunk_evidence_fields(chunk):
        normalized_field = _normalize_coverage_text(field_text)
        if not normalized_field:
            continue
        if normalized_target and normalized_target in normalized_field:
            return {
                "field": field_name,
                "match_type": "normalized_phrase",
                "excerpt": _build_evidence_excerpt(field_text, target),
            }
        if target_tokens:
            field_tokens = set(_coverage_target_tokens(field_text))
            if all(token in field_tokens for token in target_tokens):
                return {
                    "field": field_name,
                    "match_type": "token_set_same_chunk",
                    "excerpt": _build_evidence_excerpt(field_text, target),
                }
    return None


def _evaluate_semantic_target_coverage(
    *,
    semantic_context_packet: dict[str, Any],
    semantic_traversal_manifest: dict[str, Any],
    retrieval_packet: dict[str, Any],
) -> dict[str, Any]:
    semantic_coverage_target = semantic_context_packet.get("semantic_coverage_target")
    selected_chunks = list(retrieval_packet.get("selected_chunks") or [])
    target_present = isinstance(semantic_coverage_target, dict)
    target_valid = target_present
    must_preserve_results: list[dict[str, Any]] = []
    should_include_results: list[dict[str, Any]] = []
    avoid_results: list[dict[str, Any]] = []
    missing_must_preserve: list[str] = []
    missing_should_include: list[str] = []
    present_avoid_satisfying_with: list[str] = []
    limits = [
        "deterministic lexical/metadata evidence only",
        "does not claim full semantic entailment",
    ]
    if not target_present:
        return {
            "target_present": False,
            "target_valid": False,
            "target_hash": None,
            "evaluation_mode": "deterministic_retrieved_evidence_match",
            "must_preserve": [],
            "should_include": [],
            "avoid_satisfying_with": [],
            "missing_must_preserve": [],
            "missing_should_include": [],
            "present_avoid_satisfying_with": [],
            "covered": False,
            "limits": limits,
        }

    try:
        target_hash = sha256_json(semantic_coverage_target)
    except Exception:
        target_hash = None
        target_valid = False
    if not isinstance(semantic_coverage_target.get("must_preserve"), list):
        target_valid = False
    if not isinstance(semantic_coverage_target.get("should_include"), list):
        target_valid = False
    if not isinstance(semantic_coverage_target.get("avoid_satisfying_with"), list):
        target_valid = False
    if "query_text" not in semantic_coverage_target or semantic_coverage_target.get("query_text") is None:
        target_valid = False
    elif not isinstance(semantic_coverage_target.get("query_text"), str):
        target_valid = False
    if "allow_no_retrieval_needed" not in semantic_coverage_target or semantic_coverage_target.get("allow_no_retrieval_needed") is None:
        target_valid = False
    elif not isinstance(semantic_coverage_target.get("allow_no_retrieval_needed"), bool):
        target_valid = False

    for target_value in list(semantic_coverage_target.get("must_preserve") or []):
        target_text = str(target_value)
        match = None
        for chunk in selected_chunks:
            match = _match_target_to_evidence_fields(target_text, chunk)
            if match is not None:
                must_preserve_results.append(
                    {
                        "target": target_text,
                        "covered": True,
                        "match_type": match["match_type"],
                        "evidence": [
                            {
                                "chunk_id": chunk.get("chunk_id"),
                                "field": match["field"],
                                "excerpt": match["excerpt"],
                            }
                        ],
                    }
                )
                break
        if match is None:
            missing_must_preserve.append(target_text)
            must_preserve_results.append(
                {
                    "target": target_text,
                    "covered": False,
                    "match_type": None,
                    "evidence": [],
                }
            )

    for target_value in list(semantic_coverage_target.get("should_include") or []):
        target_text = str(target_value)
        match = None
        for chunk in selected_chunks:
            match = _match_target_to_evidence_fields(target_text, chunk)
            if match is not None:
                should_include_results.append(
                    {
                        "target": target_text,
                        "covered": True,
                        "match_type": match["match_type"],
                        "evidence": [
                            {
                                "chunk_id": chunk.get("chunk_id"),
                                "field": match["field"],
                                "excerpt": match["excerpt"],
                            }
                        ],
                    }
                )
                break
        if match is None:
            missing_should_include.append(target_text)
            should_include_results.append(
                {
                    "target": target_text,
                    "covered": False,
                    "match_type": None,
                    "evidence": [],
                }
            )

    for target_value in list(semantic_coverage_target.get("avoid_satisfying_with") or []):
        target_text = str(target_value)
        match = None
        for chunk in selected_chunks:
            match = _match_target_to_evidence_fields(target_text, chunk)
            if match is not None:
                present_avoid_satisfying_with.append(target_text)
                avoid_results.append(
                    {
                        "target": target_text,
                        "present": True,
                        "match_type": match["match_type"],
                        "evidence": [
                            {
                                "chunk_id": chunk.get("chunk_id"),
                                "field": match["field"],
                                "excerpt": match["excerpt"],
                            }
                        ],
                    }
                )
                break
        if match is None:
            avoid_results.append(
                {
                    "target": target_text,
                    "present": False,
                    "match_type": None,
                    "evidence": [],
                }
            )

    covered = target_valid and not missing_must_preserve and not present_avoid_satisfying_with
    return {
        "target_present": True,
        "target_valid": target_valid,
        "target_hash": target_hash,
        "evaluation_mode": "deterministic_retrieved_evidence_match",
        "must_preserve": must_preserve_results,
        "should_include": should_include_results,
        "avoid_satisfying_with": avoid_results,
        "missing_must_preserve": missing_must_preserve,
        "missing_should_include": missing_should_include,
        "present_avoid_satisfying_with": present_avoid_satisfying_with,
        "covered": covered,
        "limits": limits,
    }


def _build_retrieval_preparation(
    *,
    user_input: str,
    isolated_packet: dict[str, Any],
    contextual_packet: dict[str, Any],
    semantic_coverage_target: dict[str, Any] | None,
) -> dict[str, Any]:
    raw_lexical_terms = _extract_lexical_query_terms(user_input)
    candidate_term_sources: dict[str, list[str]] = {}
    for term in raw_lexical_terms:
        candidate_term_sources.setdefault(term, []).append("raw_user_input")

    isolated_hint_terms, isolated_sources = _collect_extraction_hint_terms(
        isolated_packet.get("parsed_payload"),
        extraction_mode="isolated",
    )
    contextual_hint_terms, contextual_sources = _collect_extraction_hint_terms(
        contextual_packet.get("parsed_payload"),
        extraction_mode="contextual",
    )
    coverage_target_terms: list[str] = []
    if semantic_coverage_target:
        for key in ("must_preserve", "should_include"):
            for term in _collect_string_terms(semantic_coverage_target.get(key)):
                if term not in coverage_target_terms:
                    coverage_target_terms.append(term)
                if "semantic_coverage_target" not in candidate_term_sources.setdefault(term, []):
                    candidate_term_sources[term].append("semantic_coverage_target")
        query_text = semantic_coverage_target.get("query_text")
        for term in _collect_string_terms(query_text):
            if term not in coverage_target_terms:
                coverage_target_terms.append(term)
            if "semantic_coverage_target.query_text" not in candidate_term_sources.setdefault(term, []):
                candidate_term_sources[term].append("semantic_coverage_target.query_text")

    extraction_hint_terms: list[str] = []
    for term in isolated_hint_terms:
        if term not in extraction_hint_terms:
            extraction_hint_terms.append(term)
        for source_label in isolated_sources.get(term, []):
            if source_label not in candidate_term_sources.setdefault(term, []):
                candidate_term_sources[term].append(source_label)
    for term in contextual_hint_terms:
        if term not in extraction_hint_terms:
            extraction_hint_terms.append(term)
        for source_label in contextual_sources.get(term, []):
            if source_label not in candidate_term_sources.setdefault(term, []):
                candidate_term_sources[term].append(source_label)

    combined_candidate_terms: list[str] = []
    for term in raw_lexical_terms + extraction_hint_terms + coverage_target_terms:
        if term not in combined_candidate_terms:
            combined_candidate_terms.append(term)

    model_proposed_only_terms = [
        term
        for term in extraction_hint_terms + coverage_target_terms
        if "raw_user_input" not in candidate_term_sources.get(term, [])
    ]
    return {
        "raw_lexical_terms": raw_lexical_terms,
        "extraction_hint_terms": extraction_hint_terms,
        "coverage_target_terms": coverage_target_terms,
        "combined_candidate_terms": combined_candidate_terms,
        "candidate_term_sources": candidate_term_sources,
        "model_proposed_only_terms": model_proposed_only_terms,
        "used_additively_for_retrieval": True,
    }


def _build_semantic_context_packet(
    *,
    thread_document: dict[str, Any],
    prior_thread_state: dict[str, Any],
    user_input: str,
    turn_id: int,
    semantic_extraction: SemanticExtractionArtifacts,
) -> dict[str, Any]:
    semantic_payload, semantic_coverage_target = _extract_semantic_outputs(
        isolated_packet=semantic_extraction.isolated_packet,
        contextual_packet=semantic_extraction.contextual_packet,
    )
    contract_reasons = _validate_semantic_context_payload(semantic_payload, raw_user_input=user_input) if semantic_payload else [
        "semantic extraction parsed but failed contract validation",
    ]
    retrieval_preparation = _build_retrieval_preparation(
        user_input=user_input,
        isolated_packet=semantic_extraction.isolated_packet,
        contextual_packet=semantic_extraction.contextual_packet,
        semantic_coverage_target=semantic_coverage_target,
    )
    return {
        "thread_id": thread_document["thread_id"],
        "turn_id": turn_id,
        "user_input": user_input,
        "raw_user_input": user_input,
        "perturbation_nodes": list(semantic_payload.get("perturbation_nodes") or []),
        "contextual_salt_nodes": list(semantic_payload.get("contextual_salt_nodes") or []),
        "perturbation_semantic_graph": semantic_payload.get("perturbation_semantic_graph"),
        "semantic_coverage_target": semantic_coverage_target,
        "activation_hints": dict(semantic_payload.get("activation_hints") or {}),
        "limitations": list(semantic_payload.get("limitations") or []),
        "extracted_lexical_query_terms": list(retrieval_preparation["raw_lexical_terms"]),
        "retrieval_preparation": retrieval_preparation,
        "semantic_contract_validation": {
            "valid": not contract_reasons,
            "reasons": contract_reasons,
        },
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
            "Return JSON only. "
            "Do not answer the user. Preserve the raw message. "
            "Produce raw_user_input, perturbation_nodes, contextual_salt_nodes, perturbation_semantic_graph, "
            "semantic_coverage_target, activation_hints, and limitations."
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
        "diagnostics": response.diagnostics,
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


def _database_path(config: RuntimeConfig, data_root: Path) -> Path:
    raw_root = config.storage_ingestion_root
    ingest_root = raw_root if raw_root.is_absolute() else (data_root / raw_root)
    return (ingest_root / config.storage_ingestion_database_filename).resolve()


def _load_chunk_rows(connection: sqlite3.Connection) -> list[sqlite3.Row]:
    return connection.execute(
        """
        SELECT
            chunks.chunk_id,
            chunks.note_id,
            chunks.source_root_label,
            chunks.relative_path,
            chunks.note_path,
            chunks.note_title,
            chunks.section_id,
            chunks.section_label,
            chunks.section_path_json,
            chunks.paragraph_ordinal,
            chunks.paragraph_text,
            chunks.chunk_hash,
            notes.frontmatter_json
        FROM chunks
        JOIN notes ON notes.note_id = chunks.note_id
        """
    ).fetchall()


def _score_fields_for_terms(fields: dict[str, str], query_terms: list[str]) -> tuple[list[str], list[str]]:
    matched_terms: list[str] = []
    source_fields: list[str] = []
    for field_name, raw_value in fields.items():
        lowered_value = raw_value.lower()
        field_matched = False
        for term in query_terms:
            if term in lowered_value:
                if term not in matched_terms:
                    matched_terms.append(term)
                field_matched = True
        if field_matched:
            source_fields.append(field_name)
    return matched_terms, source_fields


def _base_candidate_from_row(
    row: sqlite3.Row,
    *,
    surface: str,
    matched_terms: list[str],
    source_fields: list[str],
    score: float,
    reason: str,
    graph_path: list[str] | None = None,
    hop_count: int | None = None,
    edge_types: list[str] | None = None,
) -> dict[str, Any]:
    section_path = json.loads(row["section_path_json"]) if row["section_path_json"] else []
    candidate = {
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
        "surface": surface,
        "surface_score": score,
        "matched_terms": matched_terms,
        "source_fields_matched": source_fields,
        "selection_reason": reason,
    }
    if graph_path is not None:
        candidate["graph_path"] = graph_path
    if hop_count is not None:
        candidate["hop_count"] = hop_count
    if edge_types is not None:
        candidate["edge_types"] = edge_types
    return candidate


def _query_lexical_candidates(connection: sqlite3.Connection, *, query_terms: list[str]) -> list[dict[str, Any]]:
    if not query_terms:
        return []
    candidates: list[dict[str, Any]] = []
    for row in _load_chunk_rows(connection):
        matched_terms, source_fields = _score_fields_for_terms(
            {
                "paragraph_text": str(row["paragraph_text"]),
                "note_title": str(row["note_title"]),
                "section_label": str(row["section_label"]),
                "relative_path": str(row["relative_path"]),
                "note_path": str(row["note_path"]),
            },
            query_terms,
        )
        if not matched_terms:
            continue
        reason = f"lexical index matched {', '.join(matched_terms)}"
        candidates.append(
            _base_candidate_from_row(
                row,
                surface="lexical_index_surface",
                matched_terms=matched_terms,
                source_fields=source_fields,
                score=float(len(matched_terms)) + (0.1 * len(source_fields)),
                reason=reason,
            )
        )
    candidates.sort(key=lambda item: (-item["surface_score"], item["chunk_id"]))
    return candidates


def _query_primary_corpus_candidates(connection: sqlite3.Connection, *, query_terms: list[str]) -> list[dict[str, Any]]:
    if not query_terms:
        return []
    candidates: list[dict[str, Any]] = []
    for row in _load_chunk_rows(connection):
        matched_terms, source_fields = _score_fields_for_terms(
            {
                "note_title": str(row["note_title"]),
                "section_label": str(row["section_label"]),
                "relative_path": str(row["relative_path"]),
                "note_path": str(row["note_path"]),
                "source_root_label": str(row["source_root_label"]),
                "frontmatter_json": str(row["frontmatter_json"]),
            },
            query_terms,
        )
        if not matched_terms:
            continue
        reason = f"primary corpus metadata matched {', '.join(matched_terms)}"
        candidates.append(
            _base_candidate_from_row(
                row,
                surface="primary_corpus",
                matched_terms=matched_terms,
                source_fields=source_fields,
                score=float(len(matched_terms)) + 0.25,
                reason=reason,
            )
        )
    candidates.sort(key=lambda item: (-item["surface_score"], item["chunk_id"]))
    return candidates


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    if len(left) != len(right) or not left:
        return 0.0
    numerator = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return numerator / (left_norm * right_norm)


def _build_query_embedding_text(semantic_context_packet: dict[str, Any]) -> str:
    semantic_coverage_target = semantic_context_packet.get("semantic_coverage_target") or {}
    parts = [
        str(semantic_coverage_target.get("query_text") or ""),
        " ".join(str(item) for item in list(semantic_coverage_target.get("must_preserve") or [])),
        " ".join(str(item) for item in list(semantic_coverage_target.get("should_include") or [])),
        str(semantic_context_packet.get("user_input") or ""),
    ]
    return " ".join(part for part in parts if part).strip()


def _query_vector_candidates(
    connection: sqlite3.Connection,
    *,
    query_text: str,
    embedding_backend: EmbeddingBackend,
    config: RuntimeConfig,
) -> tuple[list[dict[str, Any]], str]:
    if not query_text:
        return [], "no_query_text"
    query_embedder = getattr(embedding_backend, "embed_query_text", None)
    if callable(query_embedder):
        response = query_embedder(query_text)
    else:
        response = embedding_backend.embed_texts([query_text])
    if response.status != "embedded" or not response.vectors:
        return [], response.status if response.status != "embedded" else "embedding_unavailable"
    vector_table = config.vector_table
    rows = connection.execute(
        f"""
        SELECT
            {vector_table}.chunk_id,
            {vector_table}.vector_json,
            chunks.note_id,
            chunks.source_root_label,
            chunks.relative_path,
            chunks.note_path,
            chunks.note_title,
            chunks.section_id,
            chunks.section_label,
            chunks.section_path_json,
            chunks.paragraph_ordinal,
            chunks.paragraph_text,
            chunks.chunk_hash
        FROM {vector_table}
        JOIN chunks ON chunks.chunk_id = {vector_table}.chunk_id
        """
    ).fetchall()
    if not rows:
        return [], "no_indexed_vectors"
    query_vector = response.vectors[0]
    candidates: list[dict[str, Any]] = []
    valid_vector_rows = 0
    for row in rows:
        try:
            stored_vector = json.loads(str(row["vector_json"]))
        except json.JSONDecodeError:
            continue
        if not isinstance(stored_vector, list) or not all(isinstance(value, (int, float)) for value in stored_vector):
            continue
        stored_vector_floats = [float(value) for value in stored_vector]
        if len(stored_vector_floats) != len(query_vector):
            continue
        valid_vector_rows += 1
        score = _cosine_similarity(query_vector, stored_vector_floats)
        if score <= 0:
            continue
        candidates.append(
            _base_candidate_from_row(
                row,
                surface="vector_index_surface",
                matched_terms=[],
                source_fields=["vector_similarity"],
                score=score,
                reason=f"vector similarity score {score:.4f}",
            )
        )
    candidates.sort(key=lambda item: (-item["surface_score"], item["chunk_id"]))
    if candidates:
        return candidates[: config.max_retrieval_chunks], "activated"
    if valid_vector_rows == 0:
        return [], "no_valid_vectors"
    return [], "no_vector_matches"


def _query_graph_candidates(
    connection: sqlite3.Connection,
    *,
    seed_chunk_ids: list[str],
    config: RuntimeConfig,
) -> list[dict[str, Any]]:
    if not seed_chunk_ids:
        return []
    graph_edges_table = config.graph_edges_table
    hop_limit = config.coverage_graph_expansion_hop_limit
    placeholders = ",".join("?" for _ in seed_chunk_ids)
    seed_rows = connection.execute(
        f"SELECT chunk_id, note_id FROM chunks WHERE chunk_id IN ({placeholders})",
        tuple(seed_chunk_ids),
    ).fetchall()
    seed_chunk_to_note_id = {str(row["chunk_id"]): str(row["note_id"]) for row in seed_rows}
    seed_note_ids = {str(row["note_id"]) for row in seed_rows}
    seed_note_to_chunk_id: dict[str, str] = {}
    for chunk_id in seed_chunk_ids:
        note_id = seed_chunk_to_note_id.get(chunk_id)
        if note_id is not None and note_id not in seed_note_to_chunk_id:
            seed_note_to_chunk_id[note_id] = chunk_id
    candidates_by_chunk: dict[str, dict[str, Any]] = {}

    if seed_note_ids:
        note_placeholders = ",".join("?" for _ in seed_note_ids)
        sibling_rows = connection.execute(
            f"""
            SELECT
                chunks.chunk_id,
                chunks.note_id,
                chunks.source_root_label,
                chunks.relative_path,
                chunks.note_path,
                chunks.note_title,
                chunks.section_id,
                chunks.section_label,
                chunks.section_path_json,
                chunks.paragraph_ordinal,
                chunks.paragraph_text,
                chunks.chunk_hash
            FROM chunks
            WHERE chunks.note_id IN ({note_placeholders})
            """,
            tuple(seed_note_ids),
        ).fetchall()
        for row in sibling_rows:
            chunk_id = str(row["chunk_id"])
            if chunk_id in seed_chunk_ids:
                continue
            seed_chunk_id = seed_note_to_chunk_id.get(str(row["note_id"]))
            candidates_by_chunk[chunk_id] = _base_candidate_from_row(
                row,
                surface="graph_layer",
                matched_terms=[],
                source_fields=["note_contains_chunk"],
                score=2.0,
                reason="graph expansion reached sibling chunk through note_contains_chunk",
                graph_path=[seed_chunk_id, chunk_id] if seed_chunk_id else None,
                hop_count=1,
                edge_types=["sibling"],
            )

    if hop_limit >= 2 and seed_note_ids:
        note_node_ids = [f"note::{note_id}" for note_id in seed_note_ids]
        node_placeholders = ",".join("?" for _ in note_node_ids)
        wikilink_edges = connection.execute(
            f"""
            SELECT source_node_id, target_node_id
            FROM {graph_edges_table}
            WHERE edge_type = 'wikilink' AND source_node_id IN ({node_placeholders})
            """,
            tuple(note_node_ids),
        ).fetchall()
        target_note_ids = {
            str(row["target_node_id"]).split("note::", 1)[1]
            for row in wikilink_edges
            if str(row["target_node_id"]).startswith("note::")
        }
        if target_note_ids:
            source_note_to_target_note_ids: dict[str, set[str]] = {}
            for row in wikilink_edges:
                source_node_id = str(row["source_node_id"])
                target_node_id = str(row["target_node_id"])
                if not source_node_id.startswith("note::") or not target_node_id.startswith("note::"):
                    continue
                source_note_id = source_node_id.split("note::", 1)[1]
                target_note_id = target_node_id.split("note::", 1)[1]
                source_note_to_target_note_ids.setdefault(source_note_id, set()).add(target_note_id)
            target_placeholders = ",".join("?" for _ in target_note_ids)
            linked_rows = connection.execute(
                f"""
                SELECT
                    chunks.chunk_id,
                    chunks.note_id,
                    chunks.source_root_label,
                    chunks.relative_path,
                    chunks.note_path,
                    chunks.note_title,
                    chunks.section_id,
                    chunks.section_label,
                    chunks.section_path_json,
                    chunks.paragraph_ordinal,
                    chunks.paragraph_text,
                    chunks.chunk_hash
                FROM chunks
                WHERE chunks.note_id IN ({target_placeholders})
                """,
                tuple(target_note_ids),
            ).fetchall()
            for row in linked_rows:
                chunk_id = str(row["chunk_id"])
                source_note_id = None
                for candidate_source_note_id, target_ids in source_note_to_target_note_ids.items():
                    if str(row["note_id"]) in target_ids:
                        source_note_id = candidate_source_note_id
                        break
                seed_chunk_id = seed_note_to_chunk_id.get(source_note_id or "")
                candidates_by_chunk.setdefault(
                    chunk_id,
                    _base_candidate_from_row(
                        row,
                        surface="graph_layer",
                        matched_terms=[],
                        source_fields=["wikilink"],
                        score=1.5,
                        reason="graph expansion reached chunk through wikilink edge",
                        graph_path=[seed_chunk_id, f"note::{row['note_id']}", chunk_id] if seed_chunk_id else None,
                        hop_count=1,
                        edge_types=["wikilink"],
                    ),
                )

    candidates = list(candidates_by_chunk.values())
    candidates.sort(key=lambda item: (-item["surface_score"], item["chunk_id"]))
    return candidates


def _query_synthetic_candidates(
    connection: sqlite3.Connection,
    *,
    query_terms: list[str],
    config: RuntimeConfig,
) -> list[dict[str, Any]]:
    if not query_terms:
        return []
    synthetic_root = config.synthetic_nodes_root
    candidates: list[dict[str, Any]] = []
    for row in _load_chunk_rows(connection):
        note_path = Path(str(row["note_path"])).resolve()
        try:
            note_path.relative_to(synthetic_root)
        except ValueError:
            continue
        matched_terms, source_fields = _score_fields_for_terms(
            {
                "paragraph_text": str(row["paragraph_text"]),
                "section_label": str(row["section_label"]),
                "relative_path": str(row["relative_path"]),
            },
            query_terms,
        )
        if not matched_terms:
            continue
        candidates.append(
            _base_candidate_from_row(
                row,
                surface="synthetic_nodes",
                matched_terms=matched_terms,
                source_fields=source_fields,
                score=float(len(matched_terms)) + 0.1,
                reason=f"synthetic node matched {', '.join(matched_terms)}",
            )
        )
    candidates.sort(key=lambda item: (-item["surface_score"], item["chunk_id"]))
    return candidates


def _build_latent_space_activation(
    *,
    repo_root: Path,
    data_root: Path,
    config: RuntimeConfig,
    semantic_context_packet: dict[str, Any],
    embedding_backend: EmbeddingBackend,
) -> dict[str, Any]:
    database_path = _database_path(config, data_root)
    retrieval_preparation = dict(semantic_context_packet.get("retrieval_preparation") or {})
    query_terms = list(retrieval_preparation.get("combined_candidate_terms") or [])
    activation_surfaces: list[dict[str, Any]] = []
    candidate_regions: dict[str, list[dict[str, Any]]] = {
        "lexical_index_surface": [],
        "primary_corpus": [],
        "vector_index_surface": [],
        "graph_layer": [],
        "synthetic_nodes": [],
    }
    blocked_surfaces: list[dict[str, Any]] = []
    if not database_path.exists():
        for surface in candidate_regions:
            activation_surfaces.append(
                {
                    "surface": surface,
                    "status": "blocked",
                    "candidate_count": 0,
                    "reason": "ingestion SQLite database not found",
                }
            )
        activated_regions = {
            "thread_id": semantic_context_packet["thread_id"],
            "turn_id": semantic_context_packet["turn_id"],
            "database_path": str(database_path),
            "query_terms": query_terms,
            "activation_surfaces": activation_surfaces,
            "candidate_regions": candidate_regions,
            "blocked_surfaces": [{"surface": surface, "reason": "ingestion SQLite database not found"} for surface in candidate_regions],
            "repo_root": str(repo_root),
        }
        activated_regions["activated_region_hash"] = sha256_json(activated_regions)
        return activated_regions

    connection = sqlite3.connect(database_path)
    try:
        connection.row_factory = sqlite3.Row
        lexical_candidates = _query_lexical_candidates(connection, query_terms=query_terms)
        candidate_regions["lexical_index_surface"] = lexical_candidates[: config.max_retrieval_chunks]
        activation_surfaces.append(
            {
                "surface": "lexical_index_surface",
                "status": "activated" if lexical_candidates else ("no_query_terms" if not query_terms else "no_matches"),
                "candidate_count": len(lexical_candidates),
                "reason": None if lexical_candidates else ("no query terms available" if not query_terms else "no lexical matches"),
            }
        )

        primary_candidates = _query_primary_corpus_candidates(connection, query_terms=query_terms)
        candidate_regions["primary_corpus"] = primary_candidates[: config.max_retrieval_chunks]
        activation_surfaces.append(
            {
                "surface": "primary_corpus",
                "status": "activated" if primary_candidates else ("no_query_terms" if not query_terms else "no_matches"),
                "candidate_count": len(primary_candidates),
                "reason": None if primary_candidates else ("no query terms available" if not query_terms else "no primary corpus metadata matches"),
            }
        )

        query_text = _build_query_embedding_text(semantic_context_packet)
        vector_candidates: list[dict[str, Any]] = []
        vector_status = "unavailable"
        if getattr(embedding_backend, "mode_name", "unavailable") == "unavailable":
            vector_status = "unavailable"
        else:
            vector_candidates, vector_status = _query_vector_candidates(
                connection,
                query_text=query_text,
                embedding_backend=embedding_backend,
                config=config,
            )
        candidate_regions["vector_index_surface"] = vector_candidates
        activation_surfaces.append(
            {
                "surface": "vector_index_surface",
                "status": "activated" if vector_candidates else vector_status,
                "candidate_count": len(vector_candidates),
                "reason": None if vector_candidates else vector_status,
            }
        )

        seed_chunk_ids = [
            candidate["chunk_id"]
            for surface_name in ("lexical_index_surface", "primary_corpus", "vector_index_surface")
            for candidate in candidate_regions[surface_name][: config.max_retrieval_chunks]
        ]
        deduped_seed_chunk_ids: list[str] = []
        for chunk_id in seed_chunk_ids:
            if chunk_id not in deduped_seed_chunk_ids:
                deduped_seed_chunk_ids.append(chunk_id)
        graph_candidates = _query_graph_candidates(connection, seed_chunk_ids=deduped_seed_chunk_ids, config=config)
        candidate_regions["graph_layer"] = graph_candidates[: config.max_retrieval_chunks]
        activation_surfaces.append(
            {
                "surface": "graph_layer",
                "status": "activated" if graph_candidates else ("no_seed_candidates" if not deduped_seed_chunk_ids else "no_expansion_matches"),
                "candidate_count": len(graph_candidates),
                "reason": None if graph_candidates else ("no seed candidates available for graph expansion" if not deduped_seed_chunk_ids else "graph expansion produced no additional chunk candidates"),
            }
        )

        synthetic_candidates = _query_synthetic_candidates(connection, query_terms=query_terms, config=config)
        candidate_regions["synthetic_nodes"] = synthetic_candidates[: config.max_retrieval_chunks]
        activation_surfaces.append(
            {
                "surface": "synthetic_nodes",
                "status": "activated" if synthetic_candidates else ("no_query_terms" if not query_terms else "no_matches"),
                "candidate_count": len(synthetic_candidates),
                "reason": None if synthetic_candidates else ("no query terms available" if not query_terms else "no synthetic node matches"),
            }
        )
    finally:
        connection.close()

    for surface in activation_surfaces:
        if surface["status"] not in {"activated", "no_matches", "no_query_terms", "no_expansion_matches", "no_seed_candidates"}:
            blocked_surfaces.append({"surface": surface["surface"], "reason": surface["reason"]})
    activated_regions = {
        "thread_id": semantic_context_packet["thread_id"],
        "turn_id": semantic_context_packet["turn_id"],
        "database_path": str(database_path),
        "query_terms": query_terms,
        "activation_surfaces": activation_surfaces,
        "candidate_regions": candidate_regions,
        "blocked_surfaces": blocked_surfaces,
        "repo_root": str(repo_root),
    }
    activated_regions["activated_region_hash"] = sha256_json(activated_regions)
    return activated_regions


def _build_semantic_traversal_manifest(
    *,
    semantic_context_packet: dict[str, Any],
    activated_semantic_regions: dict[str, Any],
    config: RuntimeConfig,
) -> dict[str, Any]:
    candidate_regions = dict(activated_semantic_regions.get("candidate_regions") or {})
    aggregate: dict[str, dict[str, Any]] = {}
    for surface_name, candidates in candidate_regions.items():
        for candidate in list(candidates or []):
            chunk_id = str(candidate["chunk_id"])
            entry = aggregate.setdefault(
                chunk_id,
                {
                    "chunk_id": chunk_id,
                    "total_score": 0.0,
                    "surface_contributions": [],
                    "selection_reasons": [],
                    "matched_terms": [],
                },
            )
            entry["total_score"] += float(candidate["surface_score"])
            if surface_name not in entry["surface_contributions"]:
                entry["surface_contributions"].append(surface_name)
            selection_reason = str(candidate["selection_reason"])
            if selection_reason not in entry["selection_reasons"]:
                entry["selection_reasons"].append(selection_reason)
            for term in list(candidate.get("matched_terms") or []):
                if term not in entry["matched_terms"]:
                    entry["matched_terms"].append(term)
            if surface_name == "graph_layer":
                if candidate.get("graph_path") is not None and "graph_path" not in entry:
                    entry["graph_path"] = list(candidate.get("graph_path") or [])
                if candidate.get("hop_count") is not None and "hop_count" not in entry:
                    entry["hop_count"] = int(candidate.get("hop_count"))
                if candidate.get("edge_types") is not None and "edge_types" not in entry:
                    entry["edge_types"] = list(candidate.get("edge_types") or [])

    ranked_candidates = sorted(
        aggregate.values(),
        key=lambda item: (-float(item["total_score"]), str(item["chunk_id"])),
    )
    selected = ranked_candidates[: config.max_retrieval_chunks]
    selected_chunk_ids = [str(item["chunk_id"]) for item in selected]
    selection_reasons = [reason for item in selected for reason in item["selection_reasons"]]
    if not selection_reasons:
        if not list(activated_semantic_regions.get("query_terms") or []):
            selection_reasons = ["no lexical or additive extraction candidate terms were available"]
        elif any(str(surface.get("reason") or "") == "ingestion SQLite database not found" for surface in list(activated_semantic_regions.get("activation_surfaces") or [])):
            selection_reasons = ["ingestion SQLite database not found"]
        elif not ranked_candidates:
            selection_reasons = ["no chunk text or metadata matched the activated candidate terms"]
    surface_contributions = {
        surface_name: any(surface_name in list(item["surface_contributions"]) for item in selected)
        for surface_name in ("lexical_index_surface", "primary_corpus", "vector_index_surface", "graph_layer", "synthetic_nodes")
    }
    manifest_validity_reasons: list[str] = []
    if not semantic_context_packet.get("semantic_contract_validation", {}).get("valid"):
        manifest_validity_reasons.append("semantic extraction parsed but failed contract validation")
    if semantic_context_packet.get("perturbation_semantic_graph") is None:
        manifest_validity_reasons.append("perturbation_semantic_graph missing")
    if semantic_context_packet.get("semantic_coverage_target") is None:
        manifest_validity_reasons.append("semantic_coverage_target missing")
    if not ranked_candidates:
        manifest_validity_reasons.append("semantic traversal produced no candidate regions")
    manifest = {
        "thread_id": semantic_context_packet["thread_id"],
        "turn_id": semantic_context_packet["turn_id"],
        "semantic_coverage_target_hash": (
            sha256_json(semantic_context_packet["semantic_coverage_target"])
            if isinstance(semantic_context_packet.get("semantic_coverage_target"), dict)
            else None
        ),
        "activated_region_hash": activated_semantic_regions.get("activated_region_hash"),
        "activation_surfaces": list(activated_semantic_regions.get("activation_surfaces") or []),
        "candidate_regions": {
            surface_name: list(candidates or [])
            for surface_name, candidates in candidate_regions.items()
        },
        "query_terms_available": bool(list(activated_semantic_regions.get("query_terms") or [])),
        "selected_chunk_ids": selected_chunk_ids,
        "selection_reasons": selection_reasons,
        "surface_contributions": surface_contributions,
        "ranking_inputs": {
            "query_terms": list(activated_semantic_regions.get("query_terms") or []),
            "max_selected_chunks": config.max_retrieval_chunks,
        },
        "limits": {
            "max_selected_chunks": config.max_retrieval_chunks,
            "candidate_count": len(ranked_candidates),
        },
        "blocked_surfaces": list(activated_semantic_regions.get("blocked_surfaces") or []),
        "selected_candidates": selected,
        "manifest_validity": {
            "valid": not manifest_validity_reasons,
            "reasons": manifest_validity_reasons,
        },
    }
    return manifest


def _assemble_retrieval_packet(
    *,
    data_root: Path,
    config: RuntimeConfig,
    semantic_traversal_manifest: dict[str, Any],
) -> dict[str, Any]:
    database_path = _database_path(config, data_root)
    selected_chunk_ids = list(semantic_traversal_manifest.get("selected_chunk_ids") or [])
    retrieval_packet: dict[str, Any] = {
        "thread_id": semantic_traversal_manifest["thread_id"],
        "turn_id": semantic_traversal_manifest["turn_id"],
        "database_path": str(database_path),
        "assembled_from_traversal_manifest": True,
        "selected_chunk_ids_from_manifest": selected_chunk_ids,
        "selected_chunks": [],
        "graph_context_available": False,
        "matched_chunk_count": 0,
        "retrieval_observation": "not_attempted",
    }
    if not database_path.exists() or not selected_chunk_ids:
        if not database_path.exists():
            retrieval_packet["retrieval_observation"] = "index_missing"
        elif not semantic_traversal_manifest.get("query_terms_available"):
            retrieval_packet["retrieval_observation"] = "no_query_terms"
        else:
            retrieval_packet["retrieval_observation"] = "no_matches"
        return retrieval_packet

    connection = sqlite3.connect(database_path)
    try:
        connection.row_factory = sqlite3.Row
        placeholders = ",".join("?" for _ in selected_chunk_ids)
        rows = connection.execute(
            f"""
            SELECT
                chunks.chunk_id,
                chunks.note_id,
                chunks.source_root_label,
                chunks.relative_path,
                chunks.note_path,
                chunks.note_title,
                chunks.section_id,
                chunks.section_label,
                chunks.section_path_json,
                chunks.paragraph_ordinal,
                chunks.paragraph_text,
                chunks.chunk_hash,
                notes.frontmatter_json
            FROM chunks
            JOIN notes ON notes.note_id = chunks.note_id
            WHERE chunks.chunk_id IN ({placeholders})
            """,
            tuple(selected_chunk_ids),
        ).fetchall()
    finally:
        connection.close()

    row_map = {str(row["chunk_id"]): row for row in rows}
    selected_candidates = {
        str(item["chunk_id"]): item
        for item in list(semantic_traversal_manifest.get("selected_candidates") or [])
    }
    selected_chunks: list[dict[str, Any]] = []
    for chunk_id in selected_chunk_ids:
        row = row_map.get(chunk_id)
        if row is None:
            continue
        selected_candidate = selected_candidates.get(chunk_id, {})
        selected_chunks.append(
            {
                "chunk_id": row["chunk_id"],
                "note_id": row["note_id"],
                "source_root_label": row["source_root_label"],
                "relative_path": row["relative_path"],
                "note_path": row["note_path"],
                "note_title": row["note_title"],
                "section_id": row["section_id"],
                "section_label": row["section_label"],
                "section_path": json.loads(row["section_path_json"]) if row["section_path_json"] else [],
                "paragraph_ordinal": row["paragraph_ordinal"],
                "paragraph_text": row["paragraph_text"],
                "chunk_hash": row["chunk_hash"],
                "frontmatter": json.loads(str(row["frontmatter_json"])) if row["frontmatter_json"] else {},
                "surface_contributions": list(selected_candidate.get("surface_contributions") or []),
                "selection_reasons": list(selected_candidate.get("selection_reasons") or []),
                "graph_path": list(selected_candidate.get("graph_path") or []) if selected_candidate.get("graph_path") is not None else None,
                "hop_count": selected_candidate.get("hop_count"),
                "edge_types": list(selected_candidate.get("edge_types") or []) if selected_candidate.get("edge_types") is not None else None,
                "vector_score": next(
                    (
                        candidate["surface_score"]
                        for candidate in list(semantic_traversal_manifest.get("candidate_regions", {}).get("vector_index_surface") or [])
                        if candidate["chunk_id"] == chunk_id
                    ),
                    None,
                ),
                "lexical_match_info": next(
                    (
                        {
                            "matched_terms": list(candidate.get("matched_terms") or []),
                            "source_fields_matched": list(candidate.get("source_fields_matched") or []),
                        }
                        for candidate in list(semantic_traversal_manifest.get("candidate_regions", {}).get("lexical_index_surface") or [])
                        if candidate["chunk_id"] == chunk_id
                    ),
                    None,
                ),
            }
        )
    retrieval_packet["selected_chunks"] = selected_chunks
    retrieval_packet["matched_chunk_count"] = len(selected_chunks)
    retrieval_packet["retrieval_observation"] = "matched_chunks" if selected_chunks else "no_matches"
    return retrieval_packet


def _evaluate_retrieval_coverage(
    *,
    semantic_context_packet: dict[str, Any],
    activated_semantic_regions: dict[str, Any],
    semantic_traversal_manifest: dict[str, Any],
    retrieval_packet: dict[str, Any],
    config: RuntimeConfig,
    semantic_traversal_manifest_hash: str,
    retrieval_packet_hash: str,
) -> dict[str, Any]:
    statuses = dict(semantic_context_packet.get("semantic_extraction", {}).get("statuses") or {})
    backend_mode = str(statuses.get("backend_mode") or "unknown")
    isolated_status = str(statuses.get("isolated_status") or "unknown")
    contextual_status = str(statuses.get("contextual_status") or "unknown")
    reasons: list[str] = []

    if backend_mode in {"disabled", "stub", "unavailable"}:
        reasons.append(f"semantic_context_extraction backend `{backend_mode}` is not valid for the normal runtime")
    if isolated_status != "parsed":
        reasons.append(f"isolated semantic extraction did not produce a valid parsed result: {isolated_status}")
    if contextual_status != "parsed":
        reasons.append(f"contextual semantic extraction did not produce a valid parsed result: {contextual_status}")
    contract_validation = dict(semantic_context_packet.get("semantic_contract_validation") or {})
    if not contract_validation.get("valid"):
        reasons.extend(list(contract_validation.get("reasons") or []))
        reasons.append("semantic extraction parsed but failed contract validation")

    surface_statuses = {
        str(surface["surface"]): str(surface["status"])
        for surface in list(activated_semantic_regions.get("activation_surfaces") or [])
    }
    required_surface_contributions = config.coverage_require_surface_contributions
    if required_surface_contributions.get("lexical_index_surface") and surface_statuses.get("lexical_index_surface") == "blocked":
        reasons.append("lexical_index_surface unavailable")
    if required_surface_contributions.get("primary_corpus") and surface_statuses.get("primary_corpus") == "blocked":
        reasons.append("primary_corpus unavailable")
    if required_surface_contributions.get("vector_index_surface") and surface_statuses.get("vector_index_surface") != "activated":
        reasons.append("vector_index_surface unavailable or missing configured embeddings")
    if required_surface_contributions.get("graph_layer") and surface_statuses.get("graph_layer") == "blocked":
        reasons.append("graph_layer unavailable")
    if required_surface_contributions.get("synthetic_nodes") and surface_statuses.get("synthetic_nodes") != "activated":
        reasons.append("synthetic_nodes surface required but unavailable")

    manifest_validity = dict(semantic_traversal_manifest.get("manifest_validity") or {})
    if not manifest_validity.get("valid"):
        reasons.extend(list(manifest_validity.get("reasons") or []))
        reasons.append("semantic_traversal_manifest invalid")

    selected_chunk_ids = list(semantic_traversal_manifest.get("selected_chunk_ids") or [])
    selected_chunks = list(retrieval_packet.get("selected_chunks") or [])
    if not retrieval_packet.get("assembled_from_traversal_manifest"):
        reasons.append("retrieval_packet was not assembled from traversal manifest")
    if [str(chunk["chunk_id"]) for chunk in selected_chunks] != selected_chunk_ids:
        reasons.append("retrieval_packet selected chunks do not exactly match traversal selected IDs")

    selected_chunk_count = len(selected_chunks)
    min_selected_chunks = config.coverage_min_selected_chunks
    max_selected_chunks = config.coverage_max_selected_chunks
    allow_no_retrieval_needed = config.coverage_allow_no_retrieval_needed
    target_allows_no_retrieval = bool(
        isinstance(semantic_context_packet.get("semantic_coverage_target"), dict)
        and semantic_context_packet["semantic_coverage_target"].get("allow_no_retrieval_needed")
    )
    semantic_target_coverage = _evaluate_semantic_target_coverage(
        semantic_context_packet=semantic_context_packet,
        semantic_traversal_manifest=semantic_traversal_manifest,
        retrieval_packet=retrieval_packet,
    )
    if not semantic_target_coverage["target_present"] or not semantic_target_coverage["target_valid"]:
        reasons.append("semantic_coverage_target missing or invalid")
    for target in list(semantic_target_coverage.get("missing_must_preserve") or []):
        reasons.append(f"semantic coverage target missing required evidence: {target}")
    for target in list(semantic_target_coverage.get("present_avoid_satisfying_with") or []):
        reasons.append(f"semantic coverage target matched avoided evidence: {target}")
    if selected_chunk_count < min_selected_chunks and not (allow_no_retrieval_needed and target_allows_no_retrieval):
        reasons.append(f"selected chunk count below configured minimum: {selected_chunk_count} < {min_selected_chunks}")
    if selected_chunk_count > max_selected_chunks:
        reasons.append(f"selected chunk count exceeds configured maximum: {selected_chunk_count} > {max_selected_chunks}")

    actual_surface_contributions = dict(semantic_traversal_manifest.get("surface_contributions") or {})
    for surface_name, required in required_surface_contributions.items():
        if required and not actual_surface_contributions.get(surface_name):
            reasons.append(f"required surface contribution missing: {surface_name}")

    decision = "approved" if not reasons else "blocked"
    semantic_coverage_target = semantic_context_packet.get("semantic_coverage_target")
    return {
        "decision": decision,
        "semantic_coverage_target_hash": semantic_target_coverage.get("target_hash"),
        "semantic_traversal_manifest_hash": semantic_traversal_manifest_hash,
        "retrieval_packet_hash": retrieval_packet_hash,
        "evaluated_activation_surfaces": list(activated_semantic_regions.get("activation_surfaces") or []),
        "semantic_target_coverage": semantic_target_coverage,
        "blocking_reasons": _dedupe_reasons(reasons),
        "limits": {
            "selection_limit": config.max_retrieval_chunks,
            "min_selected_chunks": min_selected_chunks,
            "max_selected_chunks": max_selected_chunks,
            "selected_chunk_count": selected_chunk_count,
            "diagnostic_retrieval_observation": retrieval_packet.get("retrieval_observation"),
        },
        "surface_contributions": actual_surface_contributions,
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
    coverage_decision = str(coverage_report.get("decision") or "blocked")
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
        "approved_retrieval_packet": retrieval_packet if coverage_decision == "approved" else None,
        "coverage_report": coverage_report,
        "runtime_outcome": "completed" if coverage_decision == "approved" else "blocked",
        "blocking_reasons": list(coverage_report.get("blocking_reasons") or []),
        "output_requirements": [
            "Respond directly to the latest raw user input.",
            "Preserve continuity with the prior thread state.",
            "Do not emit a user-facing answer when the runtime outcome is blocked.",
            "Use retrieved material only when it has been approved for synthesis.",
            "Do not invent retrieval results or claim thesis-valid traversal when blocked.",
        ],
    }


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
    assistant_response: str | None,
    turn_id: int,
    timestamp: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    prior_recent_messages = list(prior_thread_state.get("recent_messages") or [])
    updated_recent_messages = prior_recent_messages + [{"role": "user", "content": user_input}]
    if assistant_response is not None:
        updated_recent_messages.append({"role": "assistant", "content": assistant_response})
    updated_recent_messages = updated_recent_messages[-6:]
    next_thread_state = {
        "thread_id": thread_id,
        "latest_turn_id": turn_id,
        "conversation_summary": assistant_response or str(prior_thread_state.get("conversation_summary") or ""),
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
    config: RuntimeConfig | None = None,
    semantic_extractor_backend: SemanticExtractorBackend | None = None,
    embedding_backend: EmbeddingBackend | None = None,
) -> TurnExecutionResult:
    resolved_config = config or load_runtime_config(repo_root=repo_root)
    timestamp = _utc_now()
    paths = create_thread_paths(data_root=data_root, config=resolved_config, thread_id=thread_id)

    thread_document = load_json(paths.conversation_thread_path) or _default_thread_document(paths.thread_id, timestamp)
    prior_thread_state = load_json(paths.thread_state_path) or _default_thread_state(paths.thread_id, timestamp)
    ledger_records = read_ledger(paths.thread_ledger_path)
    turn_id = len(ledger_records) + 1
    parent_perturbation_hash = ledger_records[-1]["state_perturbation_hash"] if ledger_records else None
    prior_thread_state_hash = prior_thread_state.get("latest_thread_state_hash")
    turn_root = paths.turn_root(turn_id)
    extractor_backend = semantic_extractor_backend or resolve_semantic_extractor_backend(repo_root=repo_root, config=resolved_config)
    resolved_embedding_backend = embedding_backend or resolve_embedding_backend(resolved_config)

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
    activated_semantic_regions = _build_latent_space_activation(
        repo_root=repo_root,
        data_root=data_root,
        config=resolved_config,
        semantic_context_packet=semantic_context_packet,
        embedding_backend=resolved_embedding_backend,
    )
    semantic_traversal_manifest = _build_semantic_traversal_manifest(
        semantic_context_packet=semantic_context_packet,
        activated_semantic_regions=activated_semantic_regions,
        config=resolved_config,
    )
    retrieval_packet = _assemble_retrieval_packet(
        data_root=data_root,
        config=resolved_config,
        semantic_traversal_manifest=semantic_traversal_manifest,
    )
    semantic_traversal_manifest_hash = sha256_json(semantic_traversal_manifest)
    retrieval_packet_hash = sha256_json(retrieval_packet)
    coverage_report = _evaluate_retrieval_coverage(
        semantic_context_packet=semantic_context_packet,
        activated_semantic_regions=activated_semantic_regions,
        semantic_traversal_manifest=semantic_traversal_manifest,
        retrieval_packet=retrieval_packet,
        config=resolved_config,
        semantic_traversal_manifest_hash=semantic_traversal_manifest_hash,
        retrieval_packet_hash=retrieval_packet_hash,
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
    coverage_report_hash = sha256_json(coverage_report)
    synthesis_context_packet_hash = sha256_json(synthesis_context_packet)
    isolated_semantic_extraction_packet_hash = sha256_json(semantic_extraction.isolated_packet)
    isolated_semantic_extraction_raw_hash = sha256_json(semantic_extraction.isolated_raw_artifact)
    contextual_semantic_extraction_packet_hash = sha256_json(semantic_extraction.contextual_packet)
    contextual_semantic_extraction_raw_hash = sha256_json(semantic_extraction.contextual_raw_artifact)
    coverage_decision = str(coverage_report.get("decision") or "blocked")
    runtime_outcome = "completed" if coverage_decision == "approved" else "blocked"
    blocking_reasons = list(coverage_report.get("blocking_reasons") or [])
    assistant_response: str | None = None
    llm_metadata: dict[str, Any] = {
        "mode": "not_called",
        "blocked": True,
        "reason": "runtime blocked before llm_call_boundary",
    }
    if runtime_outcome == "completed":
        llm_response = llm_backend.generate(synthesis_context_packet)
        assistant_response = llm_response.assistant_response
        llm_metadata = llm_response.metadata

    next_thread_state, state_delta = _project_next_thread_state(
        thread_id=paths.thread_id,
        prior_thread_state=prior_thread_state,
        user_input=user_input,
        assistant_response=assistant_response,
        turn_id=turn_id,
        timestamp=timestamp,
    )
    state_delta["runtime_outcome"] = runtime_outcome
    state_delta["blocking_reasons"] = blocking_reasons

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
    thread_document["messages"] = list(thread_document.get("messages") or []) + [user_message]
    if assistant_response is not None:
        assistant_message = {
            "role": "assistant",
            "content": assistant_response,
            "turn_id": turn_id,
            "timestamp": timestamp,
        }
        thread_document["messages"].append(assistant_message)
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
        "assistant_response_hash": sha256_text(assistant_response) if assistant_response is not None else None,
        "state_delta_hash": sha256_json(state_delta),
        "next_thread_state_hash": next_thread_state["latest_thread_state_hash"],
        "llm_call_metadata": llm_metadata,
        "runtime_outcome": runtime_outcome,
        "blocking_reasons": blocking_reasons,
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
        assistant_response=assistant_response,
        llm_metadata=llm_metadata,
        runtime_outcome=runtime_outcome,
        blocking_reasons=blocking_reasons,
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
