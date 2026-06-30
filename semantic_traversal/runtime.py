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
    FOLLOWUP_EXPLETIVE_PATTERNS,
    SemanticCompilerResponse,
    SemanticCompilerBackend,
    _detect_followup_signals,
    _resolve_followup_referents,
    extract_terms as extract_semantic_terms,
    resolve_semantic_compiler_backend,
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
    semantic_compiler_packet_path: Path
    turn_compilation_packet_path: Path
    semantic_traversal_manifest_path: Path
    retrieval_packet_path: Path
    coverage_report_path: Path
    synthesis_context_packet_path: Path
    state_delta_path: Path
    isolated_semantic_compiler_packet_path: Path
    isolated_semantic_compiler_raw_path: Path
    contextual_semantic_compiler_packet_path: Path
    contextual_semantic_compiler_raw_path: Path
    assistant_response: str | None
    llm_metadata: dict[str, Any]
    runtime_outcome: str
    blocking_reasons: list[str]
    prior_thread_state: dict[str, Any]
    next_thread_state: dict[str, Any]
    ledger_record: dict[str, Any]
    semantic_compiler_packet: dict[str, Any]
    turn_compilation_packet: dict[str, Any]
    semantic_traversal_manifest: dict[str, Any]
    retrieval_packet: dict[str, Any]
    coverage_report: dict[str, Any]
    synthesis_context_packet: dict[str, Any]
    isolated_semantic_compiler_packet: dict[str, Any]
    contextual_semantic_compiler_packet: dict[str, Any]


@dataclass(frozen=True)
class SemanticCompilerArtifacts:
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

GENERIC_RELATION_WORDS = {
    "about",
    "analysis",
    "argument",
    "assessment",
    "attitude",
    "between",
    "category",
    "compare",
    "comparison",
    "concept",
    "concrete",
    "consumption",
    "effect",
    "emotional",
    "emotion",
    "feel",
    "feeling",
    "feelings",
    "for",
    "general",
    "impact",
    "influence",
    "objective",
    "of",
    "on",
    "opinion",
    "perspective",
    "predicate",
    "question",
    "questioning",
    "relation",
    "relationship",
    "response",
    "shell",
    "specific",
    "specificity",
    "stance",
    "subjective",
    "think",
    "thought",
    "thoughts",
    "timing",
    "topic",
    "view",
}

CAUSAL_DISAMBIGUATION_PATTERNS = (
    (re.compile(r"\bcoming from\b", re.IGNORECASE), "coming from"),
    (re.compile(r"\bcaused by\b", re.IGNORECASE), "caused by"),
    (re.compile(r"\bcome from\b", re.IGNORECASE), "come from"),
    (re.compile(r"\bdue to\b", re.IGNORECASE), "due to"),
    (re.compile(r"\bfrom\b", re.IGNORECASE), "from"),
    (re.compile(r"\bmake(?:s|d)?\b", re.IGNORECASE), "make"),
    (re.compile(r"\bcause(?:s|d)?\b", re.IGNORECASE), "cause"),
    (re.compile(r"\btrigger(?:s|ed)?\b", re.IGNORECASE), "trigger"),
    (re.compile(r"\baffect(?:s|ed)?\b", re.IGNORECASE), "affect"),
    (re.compile(r"\bbecause\b", re.IGNORECASE), "because"),
)

COMPARISON_DISAMBIGUATION_PATTERNS = (
    (re.compile(r"\bcloser to\b", re.IGNORECASE), "closer to"),
    (re.compile(r"\bmore likely\b", re.IGNORECASE), "more likely"),
    (re.compile(r"\bcompare\b", re.IGNORECASE), "compare"),
    (re.compile(r"\bversus\b", re.IGNORECASE), "versus"),
    (re.compile(r"\bvs\.?\b", re.IGNORECASE), "vs"),
    (re.compile(r"\bwhich is\b", re.IGNORECASE), "which is"),
    (re.compile(r"\bwhich\b", re.IGNORECASE), "which"),
)

RAW_TOPIC_FRAME_PATTERNS = (
    re.compile(r"^(?:what|how)\s+do\s+(?:i|you|we|they)\s+(?:think|feel|view|see|assess)\s+about\s+(.+)$", re.IGNORECASE),
    re.compile(r"^(?:what(?:'s| is)\s+)?(?:my|your|our|their)\s+(?:opinion|view|perspective)\s+(?:about|on|of)\s+(.+)$", re.IGNORECASE),
    re.compile(r"^(?:what(?:'s| is)\s+)?(?:my|your|our|their)\s+thoughts?\s+(?:about|on)\s+(.+)$", re.IGNORECASE),
    re.compile(r"^(?:thoughts?|opinions?|views?)\s+(?:about|on)\s+(.+)$", re.IGNORECASE),
)


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
    semantic_target = semantic_payload.get("semantic_target") or semantic_payload.get("coverage_target")
    return (
        semantic_payload if isinstance(semantic_payload, dict) else {},
        semantic_target if isinstance(semantic_target, dict) else None,
    )


def _expected_type_name(expected_type: type[Any]) -> str:
    return getattr(expected_type, "__name__", str(expected_type))


def _list_or_empty(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _dict_or_empty(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _normalize_semantic_text(value: Any) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", str(value).lower())).strip()


def _semantic_target_includes_anchor(semantic_target: Any, anchor: str) -> bool:
    if not isinstance(semantic_target, dict):
        return False
    normalized_anchor = _normalize_semantic_text(anchor)
    if not normalized_anchor:
        return False
    for candidate in list(semantic_target.get("must_preserve") or []):
        candidate_text = _normalize_semantic_text(candidate)
        if candidate_text and normalized_anchor in candidate_text:
            return True
    return False


def _normalize_resolved_referent_value(value: Any) -> str:
    return _normalize_semantic_text(value)


def _collect_resolved_referent_targets(resolved_referents: Any) -> list[str]:
    targets: list[str] = []
    if not isinstance(resolved_referents, list):
        return targets
    for resolved_referent in resolved_referents:
        if isinstance(resolved_referent, dict):
            resolved_to = str(resolved_referent.get("resolved_to") or "").strip()
            if resolved_to:
                normalized = _normalize_resolved_referent_value(resolved_to)
                if normalized and normalized not in targets:
                    targets.append(normalized)
    return targets


def _repair_resolved_referents_from_deterministic_candidates(
    *,
    user_input: str,
    prior_thread_state: dict[str, Any],
    semantic_payload: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    diagnostics = {
        "repaired": False,
        "repair_type": None,
        "repairs": [],
        "reason": "",
    }
    if not isinstance(semantic_payload, dict):
        diagnostics["reason"] = "semantic payload missing or invalid"
        return {}, diagnostics

    repaired_payload = dict(semantic_payload)
    followup_detection = _detect_followup_signals(user_input, prior_thread_state)
    if not followup_detection.get("requires_referent_resolution"):
        diagnostics["reason"] = "referent repair not applied for non-referential turn"
        return repaired_payload, diagnostics

    resolved_referents = repaired_payload.get("resolved_referents")
    if not isinstance(resolved_referents, list):
        diagnostics["reason"] = "resolved_referents missing or invalid"
        return repaired_payload, diagnostics

    deterministic_referents = _resolve_followup_referents(
        raw_user_input=user_input,
        prior_thread_state=prior_thread_state,
        followup_detection=followup_detection,
    )
    deterministic_by_target: dict[str, dict[str, Any]] = {}
    for candidate in deterministic_referents:
        if not isinstance(candidate, dict):
            continue
        normalized_target = _normalize_resolved_referent_value(candidate.get("resolved_to"))
        if not normalized_target:
            continue
        deterministic_by_target[normalized_target] = candidate

    if not deterministic_by_target:
        diagnostics["reason"] = "no deterministic referent candidates available"
        return repaired_payload, diagnostics

    repaired_resolved_referents: list[Any] = []
    repairs: list[dict[str, Any]] = []
    explicit_conflicts: list[str] = []
    for index, resolved_referent in enumerate(resolved_referents):
        if not isinstance(resolved_referent, dict):
            repaired_resolved_referents.append(resolved_referent)
            continue
        updated_referent = dict(resolved_referent)
        normalized_target = _normalize_resolved_referent_value(updated_referent.get("resolved_to"))
        deterministic_candidate = deterministic_by_target.get(normalized_target)
        required_for_target = updated_referent.get("required_for_target")
        if deterministic_candidate and deterministic_candidate.get("required_for_target") is True:
            if required_for_target is None:
                updated_referent["required_for_target"] = True
                repairs.append(
                    {
                        "index": index,
                        "resolved_to": str(updated_referent.get("resolved_to") or ""),
                        "surface_form": str(updated_referent.get("surface_form") or ""),
                        "source": str(updated_referent.get("source") or ""),
                    }
                )
            elif required_for_target is False:
                explicit_conflicts.append(str(updated_referent.get("resolved_to") or ""))
        repaired_resolved_referents.append(updated_referent)

    repaired_payload["resolved_referents"] = repaired_resolved_referents
    if repairs:
        diagnostics["repaired"] = True
        diagnostics["repair_type"] = "filled_required_for_target_from_deterministic_candidate"
        diagnostics["repairs"] = repairs
        diagnostics["reason"] = "filled missing required_for_target from agreeing deterministic referent candidate"
    elif explicit_conflicts:
        diagnostics["reason"] = (
            "explicit model required_for_target=false contradicts deterministic required target: "
            + ", ".join(explicit_conflicts)
        )
    else:
        diagnostics["reason"] = "no referent metadata repair needed"
    return repaired_payload, diagnostics


def _normalize_compiler_question_type(
    *,
    user_input: str,
    entity_labels: list[str],
    followup_detection: dict[str, Any],
) -> str:
    classification = classify_semantic_question_shape(
        raw_user_input=user_input,
        prior_thread_state={},
        candidate_entities=list(entity_labels),
        deterministic_followup_detection=followup_detection,
    )
    return str(classification.get("question_type") or "open_inquiry")


def classify_semantic_question_shape(
    *,
    raw_user_input: str,
    prior_thread_state: dict[str, Any],
    candidate_entities: list[str],
    deterministic_followup_detection: dict[str, Any],
) -> dict[str, Any]:
    lowered = raw_user_input.lower()
    has_recent_context = bool(
        prior_thread_state.get("recent_messages")
        or prior_thread_state.get("recent_user_messages")
        or prior_thread_state.get("recent_semantic_trajectory")
    )
    raw_topic_terms = {
        term
        for term in _extract_lexical_query_terms(raw_user_input)
        if term not in GENERIC_RELATION_WORDS
    }
    concrete_candidates: list[str] = []
    for candidate in candidate_entities:
        if not isinstance(candidate, str):
            continue
        cleaned = candidate.strip()
        if not cleaned or _is_generic_relation_shell(cleaned, raw_topic_terms=raw_topic_terms):
            continue
        if cleaned not in concrete_candidates:
            concrete_candidates.append(cleaned)

    signals: list[dict[str, Any]] = []
    limits: list[str] = []
    question_type = "open_inquiry"
    confidence = "low"
    requires_prior_referent = False
    disambiguation_basis = "default_open_inquiry"

    def add_signal(kind: str, surface: str) -> None:
        if not any(signal.get("kind") == kind and signal.get("surface") == surface for signal in signals):
            signals.append({"kind": kind, "surface": surface})

    causal_signals = [surface for pattern, surface in CAUSAL_DISAMBIGUATION_PATTERNS if pattern.search(lowered)]
    comparison_signals = [surface for pattern, surface in COMPARISON_DISAMBIGUATION_PATTERNS if pattern.search(lowered)]
    for surface in causal_signals:
        add_signal("causal_probe", surface)
    for surface in comparison_signals:
        add_signal("comparison_probe", surface)

    explicit_concrete_candidates = bool(concrete_candidates)
    contrastive_or = bool(re.search(r"\bor\b", lowered)) and len(concrete_candidates) >= 2
    if contrastive_or:
        add_signal("contrastive_or", "or")
    has_explicit_disambiguation = bool(causal_signals or comparison_signals or contrastive_or)
    expletive_it_pattern = any(pattern.search(lowered) for pattern in FOLLOWUP_EXPLETIVE_PATTERNS)
    has_deterministic_referential_followup = bool(deterministic_followup_detection.get("requires_referent_resolution"))

    if has_deterministic_referential_followup and has_recent_context and not expletive_it_pattern and not (
        comparison_signals or contrastive_or
    ):
        question_type = "referential_followup"
        confidence = "high" if deterministic_followup_detection.get("referential_signals") else "medium"
        requires_prior_referent = True
        disambiguation_basis = "deictic_followup_requires_prior_anchor"
        for signal in deterministic_followup_detection.get("referential_signals") or []:
            add_signal("referential_followup", str(signal))
    elif causal_signals and len(concrete_candidates) >= 2:
        question_type = "causal_disambiguation"
        confidence = "high" if len(concrete_candidates) >= 2 else "medium"
        disambiguation_basis = "explicit_contrastive_causal_query"
    elif (comparison_signals or contrastive_or) and len(concrete_candidates) >= 2:
        question_type = "comparison_disambiguation"
        confidence = "high" if len(concrete_candidates) >= 2 else "medium"
        disambiguation_basis = "explicit_comparison_query"
    elif lowered.startswith(
        (
            "what",
            "how",
            "why",
            "which",
            "who",
            "where",
            "when",
            "is",
            "are",
            "do",
            "does",
            "did",
            "can",
            "could",
            "would",
            "should",
            "will",
            "tell me about",
            "tell me ",
        )
    ):
        question_type = "focused_inquiry" if explicit_concrete_candidates else "open_inquiry"
        confidence = "medium" if explicit_concrete_candidates else "low"
        disambiguation_basis = "focused_current_turn_inquiry" if explicit_concrete_candidates else "broad_open_inquiry"
    else:
        confidence = "medium" if explicit_concrete_candidates else "low"
        if has_explicit_disambiguation and not explicit_concrete_candidates:
            limits.append("explicit disambiguation cues present but no concrete current-turn anchors were available")

    if has_deterministic_referential_followup and question_type != "referential_followup":
        limits.append("deterministic referential follow-up downgraded in favor of explicit current-turn disambiguation")
    if expletive_it_pattern and has_recent_context:
        limits.append("expletive it phrase treated as non-referential")

    return {
        "question_type": question_type,
        "confidence": confidence,
        "signals": signals,
        "requires_prior_referent": requires_prior_referent,
        "disambiguation_basis": disambiguation_basis,
        "limits": limits,
    }


def _infer_entity_role(label: str, *, nodes_by_label: dict[str, dict[str, Any]]) -> str:
    normalized = _normalize_semantic_text(label)
    node = nodes_by_label.get(normalized, {})
    kind = str(node.get("kind") or "").strip().lower()
    if kind in {"topic", "factor", "effect", "constraint", "context"}:
        return kind
    if any(token in normalized for token in ("feel", "emotion", "mood", "charged", "anxiety", "melancholy")):
        return "effect"
    if any(token in normalized for token in ("sleep", "food", "bed", "quality", "thing", "ate")):
        return "factor"
    return "topic" if len(_coverage_target_tokens(label)) >= 2 else "unknown"


def _build_compiler_entity_lookup(
    *,
    entities: list[dict[str, Any]],
    graph_nodes: list[dict[str, Any]],
    candidate_entities: list[str],
) -> dict[str, str]:
    lookup: dict[str, str] = {}

    def register(key: Any, entity_id: str) -> None:
        normalized = _normalize_semantic_text(key)
        if normalized and normalized not in lookup:
            lookup[normalized] = entity_id

    candidate_map = {
        _normalize_semantic_text(label): entity["id"]
        for label, entity in zip(candidate_entities, entities)
        if _normalize_semantic_text(label)
    }
    for entity in entities:
        entity_id = str(entity.get("id") or "")
        register(entity_id, entity_id)
        register(entity.get("label"), entity_id)

    for node in graph_nodes:
        if not isinstance(node, dict):
            continue
        node_id = str(node.get("id") or "").strip()
        node_label = str(node.get("label") or "").strip()
        canonical_id = (
            candidate_map.get(_normalize_semantic_text(node_label))
            or candidate_map.get(_normalize_semantic_text(node_id))
            or lookup.get(_normalize_semantic_text(node_label))
            or lookup.get(_normalize_semantic_text(node_id))
        )
        if canonical_id:
            register(node_id, canonical_id)
            register(node_label, canonical_id)

    return lookup


def _resolve_compiler_relation_endpoint(
    endpoint: Any,
    *,
    entity_lookup: dict[str, str],
) -> tuple[str, bool]:
    endpoint_text = str(endpoint or "").strip()
    normalized = _normalize_semantic_text(endpoint_text)
    canonical_id = entity_lookup.get(normalized) if normalized else None
    if canonical_id:
        return canonical_id, True
    return endpoint_text, False


def _normalize_compiler_relations(
    *,
    graph: dict[str, Any],
    isolated_payload: dict[str, Any],
    entities: list[dict[str, Any]],
    candidate_entities: list[str],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    relations: list[dict[str, Any]] = []
    diagnostics = {
        "normalized_count": 0,
        "partial_count": 0,
        "unresolved_count": 0,
    }
    graph_nodes = [node for node in _list_or_empty(graph.get("nodes")) if isinstance(node, dict)]
    entity_lookup = _build_compiler_entity_lookup(
        entities=entities,
        graph_nodes=graph_nodes,
        candidate_entities=candidate_entities,
    )

    for edge in _list_or_empty(graph.get("edges")):
        if not isinstance(edge, dict):
            continue
        source_entity, source_normalized = _resolve_compiler_relation_endpoint(
            edge.get("source"),
            entity_lookup=entity_lookup,
        )
        target_entity, target_normalized = _resolve_compiler_relation_endpoint(
            edge.get("target"),
            entity_lookup=entity_lookup,
        )
        if source_normalized and target_normalized:
            endpoint_normalization = "normalized"
            diagnostics["normalized_count"] += 1
        elif source_normalized or target_normalized:
            endpoint_normalization = "partial"
            diagnostics["partial_count"] += 1
        else:
            endpoint_normalization = "unresolved"
            diagnostics["unresolved_count"] += 1
        relations.append(
            {
                "source_entity": source_entity,
                "relation": str(edge.get("kind") or ""),
                "target_entity": target_entity,
                "confidence": "medium",
                "source": "model_inference",
                "original_source": str(edge.get("source") or ""),
                "original_target": str(edge.get("target") or ""),
                "endpoint_normalization": endpoint_normalization,
            }
        )

    if relations:
        return relations, diagnostics

    for relation in _list_or_empty(isolated_payload.get("candidate_relations")):
        if isinstance(relation, str) and relation.strip():
            relations.append(
                {
                    "source_entity": entities[0]["id"] if entities else "entity:unknown",
                    "relation": relation.strip(),
                    "target_entity": entities[1]["id"] if len(entities) > 1 else "entity:unknown",
                    "confidence": "low",
                    "source": "model_inference",
                    "endpoint_normalization": "normalized" if len(entities) >= 2 else "unresolved",
                }
            )
            if len(entities) >= 2:
                diagnostics["normalized_count"] += 1
            else:
                diagnostics["unresolved_count"] += 1
    return relations, diagnostics


def _build_semantic_compiler_packet(
    *,
    user_input: str,
    prior_thread_state: dict[str, Any],
    semantic_payload: dict[str, Any],
    isolated_packet: dict[str, Any],
    contextual_packet: dict[str, Any],
    semantic_target: dict[str, Any] | None,
) -> tuple[dict[str, Any], list[str]]:
    compiler_reasons: list[str] = []
    followup_detection = _detect_followup_signals(user_input, prior_thread_state)
    isolated_payload = _dict_or_empty(isolated_packet.get("parsed_payload"))
    contextual_payload = _dict_or_empty(contextual_packet.get("parsed_payload"))

    candidate_entities: list[str] = []
    seen_entities: set[str] = set()
    for source in (
        _list_or_empty(isolated_payload.get("candidate_targets")),
        _list_or_empty(isolated_payload.get("terms_or_phrases_not_to_discard")),
        _list_or_empty(contextual_payload.get("candidate_targets")),
        _list_or_empty(_dict_or_empty(contextual_payload.get("semantic_target")).get("must_preserve")),
        _list_or_empty(_dict_or_empty(contextual_payload.get("activation_hints")).get("entity_hints")),
    ):
        for item in source:
            if not isinstance(item, str):
                continue
            cleaned = item.strip()
            normalized = _normalize_semantic_text(cleaned)
            if not cleaned or not normalized or normalized in seen_entities:
                continue
            if len(_coverage_target_tokens(cleaned)) == 0:
                continue
            seen_entities.add(normalized)
            candidate_entities.append(cleaned)

    concrete_candidates = _collect_concrete_anchor_candidates(
        user_input=user_input,
        isolated_packet=isolated_packet,
        contextual_packet=contextual_packet,
        semantic_payload=semantic_payload,
        semantic_target=semantic_target,
    )
    for candidate in concrete_candidates:
        normalized = _normalize_semantic_text(candidate)
        if normalized and normalized not in seen_entities:
            seen_entities.add(normalized)
            candidate_entities.append(candidate)

    question_shape_classification = classify_semantic_question_shape(
        raw_user_input=user_input,
        prior_thread_state=prior_thread_state,
        candidate_entities=candidate_entities,
        deterministic_followup_detection=followup_detection,
    )

    referent_diagnostics = _validate_resolved_referents(
        payload=semantic_payload,
        raw_user_input=user_input,
        prior_thread_state=prior_thread_state,
    )[1] if semantic_payload else {
        "deterministic_followup_detection": followup_detection,
        "deterministic_referent_targets": [],
        "model_referent_targets": [],
        "disagreements": [],
        "requires_referent_resolution": False,
    }

    nodes_by_label: dict[str, dict[str, Any]] = {}
    for node in _list_or_empty(semantic_payload.get("perturbation_nodes")):
        if isinstance(node, dict):
            label = str(node.get("label") or "").strip()
            normalized = _normalize_semantic_text(label)
            if normalized:
                nodes_by_label[normalized] = node

    entities: list[dict[str, Any]] = []
    for index, label in enumerate(candidate_entities, start=1):
        normalized = _normalize_semantic_text(label)
        source = "raw_user_input" if normalized and normalized in _normalize_semantic_text(user_input) else "model_inference"
        if normalized in {str(target) for target in _list_or_empty(referent_diagnostics.get("deterministic_referent_targets"))}:
            source = "prior_thread_state"
        entities.append(
            {
                "id": f"entity:{index}",
                "label": label,
                "role": _infer_entity_role(label, nodes_by_label=nodes_by_label),
                "source": source,
            }
        )

    graph = _dict_or_empty(semantic_payload.get("perturbation_semantic_graph"))
    relations, relation_endpoint_normalization = _normalize_compiler_relations(
        graph=graph,
        isolated_payload=isolated_payload,
        entities=entities,
        candidate_entities=candidate_entities,
    )

    disambiguation_options: list[dict[str, Any]] = []
    if question_shape_classification["question_type"] in {"causal_disambiguation", "comparison_disambiguation"} and entities:
        for entity in entities[:3]:
            if _is_generic_relation_shell(str(entity.get("label") or ""), raw_topic_terms={term for term in _extract_lexical_query_terms(user_input) if term not in GENERIC_RELATION_WORDS}):
                continue
            disambiguation_options.append(
                {
                    "label": entity["label"],
                    "description": f"Interpret the query with emphasis on {entity['label']}",
                    "entities": [entity["id"]],
                }
            )

    required_anchors: list[dict[str, Any]] = []
    raw_topic_terms = {term for term in _extract_lexical_query_terms(user_input) if term not in GENERIC_RELATION_WORDS}
    if question_shape_classification["question_type"] == "referential_followup":
        for target in _list_or_empty(referent_diagnostics.get("deterministic_referent_targets")):
            if isinstance(target, str) and target.strip():
                required_anchors.append(
                    {
                        "label": target,
                        "source": "prior_thread_state",
                        "coverage_role": "must_touch",
                        "anchor_kind": "prior_thread_referent",
                    }
                )
    if not required_anchors:
        non_generic_must_preserve = []
        if isinstance(semantic_target, dict):
            for candidate in _list_or_empty(semantic_target.get("must_preserve")):
                if isinstance(candidate, str) and candidate.strip() and not _is_generic_relation_shell(candidate, raw_topic_terms=raw_topic_terms):
                    non_generic_must_preserve.append(candidate.strip())
        filtered_candidate_entities = [
            candidate
            for candidate in candidate_entities
            if isinstance(candidate, str)
            and candidate.strip()
            and not _is_generic_relation_shell(candidate, raw_topic_terms=raw_topic_terms)
        ]
        anchor_seed_candidates = non_generic_must_preserve or concrete_candidates or filtered_candidate_entities
        selected_anchor_kind = "current_turn_entity" if question_shape_classification["question_type"] in {"causal_disambiguation", "comparison_disambiguation"} else "current_turn_phrase"
        for candidate in anchor_seed_candidates[:3]:
            required_anchors.append(
                {
                    "label": candidate,
                    "source": "raw_user_input",
                    "coverage_role": "must_touch",
                    "anchor_kind": selected_anchor_kind,
                }
            )

    lexical_terms = _extract_lexical_query_terms(user_input)
    entity_terms = [entity["label"] for entity in entities]
    relation_terms = [str(relation.get("relation") or "") for relation in relations if str(relation.get("relation") or "").strip()]
    avoid_terms = []
    if isinstance(semantic_target, dict):
        avoid_terms = [str(item) for item in _list_or_empty(semantic_target.get("avoid_satisfying_with")) if str(item).strip()]

    semantic_target = {
        "intent": str(
            contextual_payload.get("contextual_user_intent")
            or isolated_payload.get("probable_user_intent")
            or user_input
        ),
        "question_type": str(question_shape_classification["question_type"]),
        "canonical_query": str(semantic_target.get("query_text") if isinstance(semantic_target, dict) else user_input) or user_input,
        "entities": entities,
        "relations": relations,
        "disambiguation_options": disambiguation_options,
        "required_anchors": required_anchors,
        "uncertainties": [
            str(item)
            for item in _list_or_empty(isolated_payload.get("ambiguities")) + _list_or_empty(contextual_payload.get("ambiguities"))
            if isinstance(item, str) and item.strip()
        ],
    }
    retrieval_plan = {
        "lexical_terms": lexical_terms,
        "entity_terms": entity_terms,
        "relation_terms": relation_terms,
        "vector_query": semantic_target["canonical_query"],
        "graph_seeds": entity_terms[:4],
        "avoid_terms": avoid_terms,
    }
    coverage_policy = {
        "requires_retrieval": not bool(isinstance(semantic_target, dict) and semantic_target.get("allow_no_retrieval_needed")),
        "required_anchor_policy": "touch_any" if semantic_target["question_type"] in {"causal_disambiguation", "comparison_disambiguation"} else "touch_all",
        "coverage_mode": "provenance_alignment",
        "block_on_missing_exact_phrase": False,
    }
    compiler_packet = {
        "raw_user_input": user_input,
        "semantic_target": semantic_target,
        "retrieval_plan": retrieval_plan,
        "coverage_policy": coverage_policy,
        "limitations": [
            "model-generated semantic compiler output",
            "raw user input remains authoritative",
            "retrieval/provenance remain runtime-owned",
        ],
        "compiler_diagnostics": {
            "question_shape_classification": question_shape_classification,
            "deterministic_followup_detection": followup_detection,
            "model_followup_detection": _dict_or_empty(semantic_payload.get("followup_detection")),
            "referent_resolution_diagnostics": referent_diagnostics,
            "relation_endpoint_normalization": relation_endpoint_normalization,
        },
    }
    if not semantic_target["entities"]:
        compiler_reasons.append("semantic compiler packet missing entities")
    if not semantic_target["required_anchors"]:
        compiler_reasons.append("semantic compiler packet missing required anchors")
    if not retrieval_plan["lexical_terms"] and not retrieval_plan["entity_terms"]:
        compiler_reasons.append("semantic compiler packet missing retrieval terms")
    return compiler_packet, compiler_reasons


def _phrase_token_count(value: str) -> int:
    return len(_coverage_target_tokens(value))


def _strip_question_framing(raw_user_input: str) -> str:
    cleaned = str(raw_user_input or "").strip().strip("?.! ")
    if not cleaned:
        return ""
    for pattern in RAW_TOPIC_FRAME_PATTERNS:
        match = pattern.match(cleaned)
        if match:
            return str(match.group(1) or "").strip().strip("?.! ")
    return ""


def _anchor_matches(target: str, candidate: str) -> bool:
    normalized_target = _normalize_semantic_text(target)
    normalized_candidate = _normalize_semantic_text(candidate)
    if not normalized_target or not normalized_candidate:
        return False
    return (
        normalized_target == normalized_candidate
        or normalized_target in normalized_candidate
        or normalized_candidate in normalized_target
    )


def _is_generic_relation_shell(target: str, *, raw_topic_terms: set[str]) -> bool:
    target_tokens = _coverage_target_tokens(target)
    if not target_tokens:
        return True
    nongeneric_tokens = [token for token in target_tokens if token not in GENERIC_RELATION_WORDS]
    topical_overlap = [token for token in nongeneric_tokens if token in raw_topic_terms]
    if topical_overlap:
        return False
    if not nongeneric_tokens:
        return True
    if len(target_tokens) <= 3 and len(nongeneric_tokens) <= 1:
        return True
    if len(nongeneric_tokens) * 2 <= len(target_tokens):
        return True
    normalized_target = _normalize_semantic_text(target)
    for suffix in (" about", " on", " of", " between", " for"):
        if normalized_target.endswith(suffix) and len(nongeneric_tokens) <= 1:
            return True
    return False


def _collect_concrete_anchor_candidates(
    *,
    user_input: str,
    isolated_packet: dict[str, Any],
    contextual_packet: dict[str, Any],
    semantic_payload: dict[str, Any],
    semantic_target: dict[str, Any] | None,
) -> list[str]:
    raw_topic_terms = {term for term in _extract_lexical_query_terms(user_input) if term not in GENERIC_RELATION_WORDS}
    scored_candidates: list[tuple[int, str]] = []
    seen: set[str] = set()

    def add_candidate(value: Any, *, weight: int) -> None:
        if not isinstance(value, str):
            return
        candidate = value.strip()
        normalized_candidate = _normalize_semantic_text(candidate)
        if not candidate or not normalized_candidate or normalized_candidate in seen:
            return
        if _is_generic_relation_shell(candidate, raw_topic_terms=raw_topic_terms):
            return
        token_count = _phrase_token_count(candidate)
        if token_count == 0:
            return
        topical_overlap = len(set(_coverage_target_tokens(candidate)).intersection(raw_topic_terms))
        score = weight + (token_count * 10) + (topical_overlap * 5)
        seen.add(normalized_candidate)
        scored_candidates.append((score, candidate))

    stripped_topic = _strip_question_framing(user_input)
    add_candidate(stripped_topic, weight=60)

    isolated_payload = _dict_or_empty(isolated_packet.get("parsed_payload"))
    for candidate in _list_or_empty(isolated_payload.get("candidate_targets")):
        add_candidate(candidate, weight=50)
    for candidate in _list_or_empty(isolated_payload.get("terms_or_phrases_not_to_discard")):
        add_candidate(candidate, weight=45)

    for node in _list_or_empty(semantic_payload.get("contextual_salt_nodes")):
        if isinstance(node, dict):
            add_candidate(node.get("label"), weight=25)

    activation_hints = _dict_or_empty(semantic_payload.get("activation_hints"))
    for candidate in _list_or_empty(activation_hints.get("entity_hints")):
        add_candidate(candidate, weight=30)

    if isinstance(semantic_target, dict):
        for candidate in _list_or_empty(semantic_target.get("should_include")):
            add_candidate(candidate, weight=35)

    contextual_payload = _dict_or_empty(contextual_packet.get("parsed_payload"))
    contextual_activation_hints = _dict_or_empty(contextual_payload.get("activation_hints"))
    for candidate in _list_or_empty(contextual_activation_hints.get("entity_hints")):
        add_candidate(candidate, weight=20)

    scored_candidates.sort(key=lambda item: (-item[0], -_phrase_token_count(item[1]), item[1]))
    return [candidate for _, candidate in scored_candidates]


def _coverage_target_has_concrete_anchor(
    semantic_target: dict[str, Any] | None,
    *,
    concrete_anchor_candidates: list[str],
    raw_topic_terms: set[str],
) -> bool:
    if not isinstance(semantic_target, dict):
        return False
    must_preserve = _list_or_empty(semantic_target.get("must_preserve"))
    for target in must_preserve:
        if not isinstance(target, str):
            continue
        if any(_anchor_matches(target, candidate) for candidate in concrete_anchor_candidates):
            return True
        if not _is_generic_relation_shell(target, raw_topic_terms=raw_topic_terms):
            return True
    return False


def _repair_semantic_target_anchors(
    *,
    user_input: str,
    prior_thread_state: dict[str, Any],
    isolated_packet: dict[str, Any],
    contextual_packet: dict[str, Any],
    semantic_payload: dict[str, Any],
    semantic_target: dict[str, Any] | None,
) -> tuple[dict[str, Any] | None, dict[str, Any], list[str]]:
    diagnostics = {
        "repaired": False,
        "repair_type": None,
        "added_must_preserve": [],
        "original_must_preserve": [],
        "concrete_anchor_candidates": [],
        "generic_must_preserve_candidates": [],
        "reason": "",
    }
    if not isinstance(semantic_target, dict):
        diagnostics["reason"] = "legacy semantic coverage target missing or invalid"
        return semantic_target, diagnostics, []

    followup_detection = _detect_followup_signals(user_input, prior_thread_state)
    if followup_detection.get("requires_referent_resolution"):
        diagnostics["reason"] = "referential follow-up target repair not applied"
        diagnostics["original_must_preserve"] = list(semantic_target.get("must_preserve") or [])
        return semantic_target, diagnostics, []

    must_preserve = [str(item) for item in _list_or_empty(semantic_target.get("must_preserve"))]
    diagnostics["original_must_preserve"] = must_preserve
    raw_topic_terms = {term for term in _extract_lexical_query_terms(user_input) if term not in GENERIC_RELATION_WORDS}
    concrete_anchor_candidates = _collect_concrete_anchor_candidates(
        user_input=user_input,
        isolated_packet=isolated_packet,
        contextual_packet=contextual_packet,
        semantic_payload=semantic_payload,
        semantic_target=semantic_target,
    )
    diagnostics["concrete_anchor_candidates"] = concrete_anchor_candidates
    diagnostics["generic_must_preserve_candidates"] = [
        candidate
        for candidate in must_preserve
        if _is_generic_relation_shell(candidate, raw_topic_terms=raw_topic_terms)
    ]

    if _coverage_target_has_concrete_anchor(
        semantic_target,
        concrete_anchor_candidates=concrete_anchor_candidates,
        raw_topic_terms=raw_topic_terms,
    ):
        diagnostics["reason"] = "must_preserve already contains a concrete anchor"
        return semantic_target, diagnostics, []

    if concrete_anchor_candidates:
        strongest_anchor = concrete_anchor_candidates[0]
        updated_target = dict(semantic_target)
        updated_target["must_preserve"] = [strongest_anchor] + must_preserve
        diagnostics["repaired"] = True
        diagnostics["repair_type"] = "added_concrete_anchor_to_must_preserve"
        diagnostics["added_must_preserve"] = [strongest_anchor]
        diagnostics["reason"] = "must_preserve contained only generic relation shells; added strongest concrete anchor"
        return updated_target, diagnostics, []

    diagnostics["reason"] = "semantic_target must_preserve lacks concrete coverage anchor and no concrete anchor candidates were available"
    return semantic_target, diagnostics, [diagnostics["reason"]]


def _validate_resolved_referents(
    *,
    payload: dict[str, Any],
    raw_user_input: str,
    prior_thread_state: dict[str, Any],
) -> tuple[list[str], dict[str, Any]]:
    reasons: list[str] = []
    followup_detection = _detect_followup_signals(raw_user_input, prior_thread_state)
    requires_resolution = bool(followup_detection.get("requires_referent_resolution"))
    resolved_referents = payload.get("resolved_referents")
    deterministic_referents = _resolve_followup_referents(
        raw_user_input=raw_user_input,
        prior_thread_state=prior_thread_state,
        followup_detection=followup_detection,
    )
    deterministic_targets = _collect_resolved_referent_targets(deterministic_referents)
    model_targets = _collect_resolved_referent_targets(resolved_referents)
    disagreements: list[str] = []

    if resolved_referents is not None:
        if not isinstance(resolved_referents, list):
            actual_type = type(resolved_referents).__name__ if resolved_referents is not None else "missing"
            reasons.append(f"resolved_referents expected list, got {actual_type}")
        else:
            for index, resolved_referent in enumerate(resolved_referents):
                if not isinstance(resolved_referent, dict):
                    reasons.append(f"resolved_referents[{index}] expected dict, got {type(resolved_referent).__name__}")
                    continue
                required_fields = ("surface_form", "resolved_to", "source", "confidence", "required_for_target")
                for field_name in required_fields:
                    if field_name not in resolved_referent:
                        reasons.append(f"resolved_referents[{index}] missing {field_name}")
                surface_form = resolved_referent.get("surface_form")
                resolved_to = resolved_referent.get("resolved_to")
                source = resolved_referent.get("source")
                confidence = resolved_referent.get("confidence")
                required_for_target = resolved_referent.get("required_for_target")
                if not isinstance(surface_form, str):
                    reasons.append(
                        f"resolved_referents[{index}].surface_form expected str, got {type(surface_form).__name__}"
                    )
                if not isinstance(resolved_to, str):
                    reasons.append(
                        f"resolved_referents[{index}].resolved_to expected str, got {type(resolved_to).__name__}"
                    )
                if not isinstance(source, str):
                    reasons.append(f"resolved_referents[{index}].source expected str, got {type(source).__name__}")
                if confidence not in {"high", "medium", "low"}:
                    actual_type = type(confidence).__name__ if confidence is not None else "missing"
                    reasons.append(
                        f"resolved_referents[{index}].confidence expected one of high, medium, low, got {actual_type}"
                    )
                if not isinstance(required_for_target, bool):
                    reasons.append(
                        f"resolved_referents[{index}].required_for_target expected bool, got {type(required_for_target).__name__}"
                    )
                if required_for_target is True and isinstance(resolved_to, str) and not resolved_to.strip():
                    reasons.append(
                        f"resolved_referents[{index}] required_for_target=true but resolved_to is empty"
                    )

    if requires_resolution:
        if not isinstance(resolved_referents, list):
            reasons.append("follow-up semantic target missing resolved referent")
            reasons.append("follow-up semantic target missing required resolved referent")
        else:
            required_referents = [
                resolved_referent
                for resolved_referent in resolved_referents
                if isinstance(resolved_referent, dict)
                and resolved_referent.get("required_for_target") is True
                and isinstance(resolved_referent.get("resolved_to"), str)
                and str(resolved_referent.get("resolved_to")).strip()
            ]
            if not required_referents:
                reasons.append("follow-up semantic target missing resolved referent")
                reasons.append("follow-up semantic target missing required resolved referent")
            for resolved_referent in required_referents:
                resolved_to = str(resolved_referent.get("resolved_to") or "").strip()
                if resolved_to and not _semantic_target_includes_anchor(payload.get("semantic_target"), resolved_to):
                    reasons.append(f"semantic_target must_preserve does not include required resolved referent: {resolved_to}")

    if deterministic_targets and model_targets:
        missing_from_model = [target for target in deterministic_targets if target not in model_targets]
        missing_from_deterministic = [target for target in model_targets if target not in deterministic_targets]
        if missing_from_model or missing_from_deterministic:
            disagreements.extend(
                [
                    f"deterministic resolved referent candidate missing from model output: {target}"
                    for target in missing_from_model
                ]
            )
            disagreements.extend(
                [
                    f"model resolved referent candidate not predicted deterministically: {target}"
                    for target in missing_from_deterministic
                ]
            )

    diagnostics = {
        "deterministic_followup_detection": followup_detection,
        "deterministic_referent_targets": deterministic_targets,
        "model_referent_targets": model_targets,
        "disagreements": disagreements,
        "requires_referent_resolution": requires_resolution,
    }
    return reasons, diagnostics


def _validate_semantic_context_payload(
    payload: dict[str, Any],
    *,
    raw_user_input: str,
    prior_thread_state: dict[str, Any],
) -> list[str]:
    reasons: list[str] = []
    if payload.get("raw_user_input") != raw_user_input:
        reasons.append("semantic compiler payload did not preserve raw_user_input")
    required_fields = {
        "perturbation_nodes": list,
        "contextual_salt_nodes": list,
        "perturbation_semantic_graph": dict,
        "semantic_target": dict,
        "activation_hints": dict,
        "limitations": list,
    }
    for field_name, expected_type in required_fields.items():
        value = payload.get(field_name)
        if not isinstance(value, expected_type):
            actual_type = type(value).__name__ if value is not None else "missing"
            reasons.append(f"{field_name} expected {_expected_type_name(expected_type)}, got {actual_type}")

    followup_detection = payload.get("followup_detection")
    if followup_detection is not None and not isinstance(followup_detection, dict):
        reasons.append(f"followup_detection expected dict, got {type(followup_detection).__name__}")

    resolved_referent_reasons, _ = _validate_resolved_referents(
        payload=payload,
        raw_user_input=raw_user_input,
        prior_thread_state=prior_thread_state,
    )
    reasons.extend(resolved_referent_reasons)
    return reasons


def _validate_semantic_compiler_packet(
    packet: dict[str, Any],
    *,
    raw_user_input: str,
) -> list[str]:
    reasons: list[str] = []
    if packet.get("raw_user_input") != raw_user_input:
        reasons.append("semantic_compiler_packet did not preserve raw_user_input")

    semantic_target = packet.get("semantic_target")
    retrieval_plan = packet.get("retrieval_plan")
    coverage_policy = packet.get("coverage_policy")
    if not isinstance(semantic_target, dict):
        reasons.append("semantic_compiler_packet.semantic_target missing or invalid")
    if not isinstance(retrieval_plan, dict):
        reasons.append("semantic_compiler_packet.retrieval_plan missing or invalid")
    if not isinstance(coverage_policy, dict):
        reasons.append("semantic_compiler_packet.coverage_policy missing or invalid")
    if reasons:
        return reasons

    if not isinstance(semantic_target.get("entities"), list):
        reasons.append("semantic_compiler_packet.semantic_target.entities missing or invalid")
    if not isinstance(semantic_target.get("required_anchors"), list):
        reasons.append("semantic_compiler_packet.semantic_target.required_anchors missing or invalid")
    if coverage_policy.get("coverage_mode") != "provenance_alignment":
        reasons.append("semantic_compiler_packet.coverage_policy.coverage_mode must be provenance_alignment")
    if coverage_policy.get("block_on_missing_exact_phrase") is not False:
        reasons.append("semantic_compiler_packet.coverage_policy.block_on_missing_exact_phrase must be false")

    requires_retrieval = bool(coverage_policy.get("requires_retrieval", True))
    useful_query_surfaces = [
        *(_collect_string_terms(retrieval_plan.get("lexical_terms"))),
        *(_collect_string_terms(retrieval_plan.get("entity_terms"))),
        *(_collect_string_terms(retrieval_plan.get("relation_terms"))),
        *(_collect_string_terms(retrieval_plan.get("graph_seeds"))),
        *(_collect_string_terms(retrieval_plan.get("vector_query"))),
        *(_collect_string_terms(semantic_target.get("canonical_query"))),
    ]
    if requires_retrieval and not useful_query_surfaces:
        reasons.append("semantic_compiler_packet missing retrieval terms")
    return reasons


def _build_legacy_compatibility_target_from_compiler(
    semantic_compiler_packet: dict[str, Any],
) -> dict[str, Any] | None:
    if not isinstance(semantic_compiler_packet, dict):
        return None
    semantic_target = _dict_or_empty(semantic_compiler_packet.get("semantic_target"))
    retrieval_plan = _dict_or_empty(semantic_compiler_packet.get("retrieval_plan"))
    coverage_policy = _dict_or_empty(semantic_compiler_packet.get("coverage_policy"))
    if not semantic_target or not retrieval_plan or not coverage_policy:
        return None

    must_preserve: list[str] = []
    for anchor in _list_or_empty(semantic_target.get("required_anchors")):
        if isinstance(anchor, dict):
            label = str(anchor.get("label") or "").strip()
            if label and label not in must_preserve:
                must_preserve.append(label)
    if not must_preserve:
        for entity in _list_or_empty(semantic_target.get("entities"))[:2]:
            if isinstance(entity, dict):
                label = str(entity.get("label") or "").strip()
                if label and label not in must_preserve:
                    must_preserve.append(label)

    should_include: list[str] = []
    for term in _collect_string_terms(retrieval_plan.get("relation_terms")):
        if term not in should_include:
            should_include.append(term)

    avoid_terms = [
        str(item)
        for item in _list_or_empty(retrieval_plan.get("avoid_terms"))
        if isinstance(item, str) and item.strip()
    ]
    return {
        "must_preserve": must_preserve,
        "should_include": should_include,
        "avoid_satisfying_with": avoid_terms,
        "query_text": str(semantic_target.get("canonical_query") or semantic_compiler_packet.get("raw_user_input") or ""),
        "allow_no_retrieval_needed": not bool(coverage_policy.get("requires_retrieval", True)),
    }


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
    for field_name, field_text in _chunk_evidence_fields(chunk):
        normalized_field = _normalize_coverage_text(field_text)
        if not normalized_field:
            continue
        if target_tokens:
            field_tokens = set(_coverage_target_tokens(field_text))
            if all(token in field_tokens for token in target_tokens):
                return {
                    "field": field_name,
                    "match_type": "token_set_same_chunk",
                    "excerpt": _build_evidence_excerpt(field_text, target),
                }
    return None


def _match_target_to_resolved_referent_anchor(
    target: str,
    *,
    turn_compilation_packet: dict[str, Any],
) -> dict[str, Any] | None:
    normalized_target = _normalize_resolved_referent_value(target)
    if not normalized_target:
        return None

    referent_diagnostics = _dict_or_empty(turn_compilation_packet.get("referent_resolution_diagnostics"))
    disagreements = _list_or_empty(referent_diagnostics.get("disagreements"))
    if disagreements:
        return None

    deterministic_targets = set(_list_or_empty(referent_diagnostics.get("deterministic_referent_targets")))
    model_targets = set(_list_or_empty(referent_diagnostics.get("model_referent_targets")))
    diagnostics_available = bool(referent_diagnostics)

    for resolved_referent in _list_or_empty(turn_compilation_packet.get("resolved_referents")):
        if not isinstance(resolved_referent, dict):
            continue
        if resolved_referent.get("required_for_target") is not True:
            continue
        resolved_to = str(resolved_referent.get("resolved_to") or "").strip()
        if not resolved_to:
            continue
        if _normalize_resolved_referent_value(resolved_to) != normalized_target:
            continue

        evidence = {
            "resolved_to": resolved_to,
            "surface_form": str(resolved_referent.get("surface_form") or ""),
            "source": str(resolved_referent.get("source") or ""),
            "confidence": str(resolved_referent.get("confidence") or ""),
            "required_for_target": True,
        }
        if diagnostics_available:
            evidence["deterministic_model_agreement"] = (
                normalized_target in deterministic_targets and normalized_target in model_targets
            )
        return evidence
    return None


def _is_deemphasized_generic_must_preserve_target(target: str, *, turn_compilation_packet: dict[str, Any]) -> bool:
    diagnostics = _dict_or_empty(turn_compilation_packet.get("semantic_target_diagnostics"))
    if not diagnostics.get("repaired"):
        return False
    generic_candidates = [str(item) for item in _list_or_empty(diagnostics.get("generic_must_preserve_candidates"))]
    added_anchors = _list_or_empty(diagnostics.get("added_must_preserve"))
    return bool(added_anchors) and target in generic_candidates


def _evaluate_semantic_compiler_alignment(
    *,
    turn_compilation_packet: dict[str, Any],
    semantic_traversal_manifest: dict[str, Any],
    retrieval_packet: dict[str, Any],
    config: RuntimeConfig,
) -> dict[str, Any]:
    compiler_packet = _dict_or_empty(turn_compilation_packet.get("semantic_compiler_packet"))
    legacy_turn_compilation_packet = dict(turn_compilation_packet)
    legacy_diagnostics = _dict_or_empty(turn_compilation_packet.get("legacy_semantic_compiler_diagnostics"))
    if isinstance(legacy_diagnostics.get("legacy_semantic_target"), dict):
        legacy_turn_compilation_packet["semantic_target"] = legacy_diagnostics["legacy_semantic_target"]
    legacy_target = _evaluate_semantic_target_coverage(
        turn_compilation_packet=legacy_turn_compilation_packet,
        semantic_traversal_manifest=semantic_traversal_manifest,
        retrieval_packet=retrieval_packet,
    )
    semantic_target = _dict_or_empty(compiler_packet.get("semantic_target"))
    retrieval_plan = _dict_or_empty(compiler_packet.get("retrieval_plan"))
    coverage_policy = _dict_or_empty(compiler_packet.get("coverage_policy"))
    selected_chunks = list(retrieval_packet.get("selected_chunks") or [])
    target_present = bool(compiler_packet)
    target_valid = bool(semantic_target and retrieval_plan and coverage_policy)
    if not dict(turn_compilation_packet.get("semantic_contract_validation") or {}).get("valid"):
        target_valid = False

    selected_chunk_provenance = [
        {
            "chunk_id": str(chunk.get("chunk_id") or ""),
            "note_id": str(chunk.get("note_id") or ""),
            "source_root_label": str(chunk.get("source_root_label") or ""),
            "relative_path": str(chunk.get("relative_path") or ""),
            "has_provenance": bool(chunk.get("chunk_id") and chunk.get("note_id") and chunk.get("relative_path")),
        }
        for chunk in selected_chunks
    ]
    surface_statuses = {
        str(surface_name): ("activated" if bool(is_activated) else "blocked")
        for surface_name, is_activated in dict(semantic_traversal_manifest.get("surface_contributions") or {}).items()
    }
    selected_text = " ".join(
        " ".join(
            [
                str(chunk.get("paragraph_text") or ""),
                str(chunk.get("note_title") or ""),
                str(chunk.get("section_label") or ""),
                str(chunk.get("relative_path") or ""),
            ]
        )
        for chunk in selected_chunks
    )
    selected_terms = set(_coverage_target_tokens(selected_text))
    compiler_query_terms = set(
        _collect_string_terms(retrieval_plan.get("lexical_terms"))
        + _collect_string_terms(retrieval_plan.get("entity_terms"))
        + _collect_string_terms(retrieval_plan.get("relation_terms"))
    )

    referent_diagnostics = _dict_or_empty(turn_compilation_packet.get("referent_resolution_diagnostics"))
    deterministic_targets = set(_list_or_empty(referent_diagnostics.get("deterministic_referent_targets")))
    resolved_referents = _list_or_empty(turn_compilation_packet.get("resolved_referents"))
    required_anchor_alignment: list[dict[str, Any]] = []
    legacy_must_preserve: list[dict[str, Any]] = []
    diagnostic_gaps: list[str] = []
    blocking_gaps: list[str] = []
    missing_required_anchors: list[str] = []
    for anchor in _list_or_empty(semantic_target.get("required_anchors")):
        if not isinstance(anchor, dict):
            continue
        label = str(anchor.get("label") or "").strip()
        anchor_source = str(anchor.get("source") or "raw_user_input")
        anchor_tokens = set(_coverage_target_tokens(label))
        aligned = False
        evidence: list[dict[str, Any]] = []
        if anchor_source == "prior_thread_state":
            normalized_anchor = _normalize_resolved_referent_value(label)
            if normalized_anchor in deterministic_targets or any(
                _normalize_resolved_referent_value(referent.get("resolved_to")) == normalized_anchor
                for referent in resolved_referents
                if isinstance(referent, dict)
            ):
                aligned = True
                evidence.append(
                    {
                        "alignment_type": "discourse_anchor",
                        "label": label,
                    }
                )
        if not aligned and anchor_tokens:
            for chunk in selected_chunks:
                match = _match_target_to_evidence_fields(label, chunk)
                if match is not None:
                    aligned = True
                    evidence.append(
                        {
                            "alignment_type": "retrieved_term_overlap",
                            "matched_terms": sorted(anchor_tokens.intersection(set(_coverage_target_tokens(str(chunk.get("paragraph_text") or ""))))),
                            "chunk_id": str(chunk.get("chunk_id") or ""),
                            "field": match["field"],
                            "excerpt": match["excerpt"],
                        }
                    )
                    break
        if not aligned:
            missing_required_anchors.append(label)
            diagnostic_gaps.append(f"required anchor not aligned: {label}")
            legacy_must_preserve.append(
                {
                    "target": label,
                    "covered": False,
                    "match_type": None,
                    "evidence": [],
                }
            )
        else:
            legacy_evidence = []
            if evidence and evidence[0].get("alignment_type") == "discourse_anchor":
                legacy_evidence = [evidence[0]]
                match_type = "resolved_referent_discourse_anchor"
            else:
                match_type = "provenance_alignment_term_overlap"
                if evidence and evidence[0].get("chunk_id"):
                    legacy_evidence = [
                        {
                            "chunk_id": evidence[0]["chunk_id"],
                            "field": evidence[0].get("field", "paragraph_text"),
                            "excerpt": evidence[0].get("excerpt", ""),
                        }
                    ]
            legacy_must_preserve.append(
                {
                    "target": label,
                    "covered": True,
                    "match_type": match_type,
                    "evidence": legacy_evidence,
                }
            )
        required_anchor_alignment.append(
            {
                "label": label,
                "source": anchor_source,
                "coverage_role": str(anchor.get("coverage_role") or "must_touch"),
                "aligned": aligned,
                "evidence": evidence,
            }
        )

    retrieval_plan_alignment = {
        "lexical_term_overlap": sorted(set(_collect_string_terms(retrieval_plan.get("lexical_terms"))).intersection(selected_terms)),
        "entity_term_overlap": sorted(set(_collect_string_terms(retrieval_plan.get("entity_terms"))).intersection(selected_terms)),
        "relation_term_overlap": sorted(set(_collect_string_terms(retrieval_plan.get("relation_terms"))).intersection(selected_terms)),
        "query_term_overlap_count": len(compiler_query_terms.intersection(selected_terms)),
    }
    relation_terms = [str(term) for term in _list_or_empty(retrieval_plan.get("relation_terms")) if str(term).strip()]
    should_include = list(legacy_target.get("should_include") or [])
    if not should_include:
        should_include = [
            {
                "target": term,
                "covered": term.lower() in {item.lower() for item in retrieval_plan_alignment["relation_term_overlap"]},
                "match_type": "provenance_alignment_term_overlap"
                if term.lower() in {item.lower() for item in retrieval_plan_alignment["relation_term_overlap"]}
                else None,
                "evidence": [],
            }
            for term in relation_terms
        ]
    missing_should_include = list(legacy_target.get("missing_should_include") or [])
    if not missing_should_include:
        missing_should_include = [item["target"] for item in should_include if not item["covered"]]

    avoid_violations: list[str] = []
    avoid_results: list[dict[str, Any]] = []
    for avoid_term in _list_or_empty(retrieval_plan.get("avoid_terms")):
        if isinstance(avoid_term, str) and _normalize_semantic_text(avoid_term) in _normalize_semantic_text(selected_text):
            avoid_violations.append(avoid_term)
            diagnostic_gaps.append(f"avoid term present in retrieved evidence: {avoid_term}")
            avoid_results.append({"target": avoid_term, "present": True, "match_type": "normalized_phrase", "evidence": []})
        elif isinstance(avoid_term, str):
            avoid_results.append({"target": avoid_term, "present": False, "match_type": None, "evidence": []})

    compiler_required_surfaces = [str(item) for item in _list_or_empty(coverage_policy.get("required_surfaces")) if str(item).strip()]
    if compiler_required_surfaces:
        required_surface_names = compiler_required_surfaces
    else:
        required_surface_names = [
            surface_name
            for surface_name, required in config.coverage_require_surface_contributions.items()
            if required and surface_name not in {"primary_corpus", "graph_layer"}
        ]
    required_surface_names = [
        surface_name
        for surface_name in required_surface_names
        if surface_name not in {"primary_corpus", "graph_layer"}
    ]
    compiler_optional_surfaces = [str(item) for item in _list_or_empty(coverage_policy.get("optional_surfaces")) if str(item).strip()]
    if compiler_optional_surfaces:
        optional_surface_names = compiler_optional_surfaces
    else:
        optional_surface_names = [
            surface_name
            for surface_name, required in config.coverage_require_surface_contributions.items()
            if not required or surface_name in {"primary_corpus", "graph_layer"}
        ]
    for surface_name in ("primary_corpus", "graph_layer"):
        if surface_name not in optional_surface_names:
            optional_surface_names = list(optional_surface_names) + [surface_name]
    available_surfaces = sorted(surface_name for surface_name, status in surface_statuses.items() if status == "activated")
    missing_required_surfaces = [surface_name for surface_name in required_surface_names if surface_statuses.get(surface_name) != "activated"]
    missing_optional_surfaces = [surface_name for surface_name in optional_surface_names if surface_statuses.get(surface_name) != "activated"]
    for surface_name in missing_required_surfaces:
        diagnostic_gaps.append(f"required surface unavailable: {surface_name}")
        blocking_gaps.append(f"required surface unavailable: {surface_name}")
    required_policy = str(coverage_policy.get("required_anchor_policy") or "touch_all")
    aligned_required_anchors = [item for item in required_anchor_alignment if item.get("aligned")]
    missing_required_anchor_labels = [str(item.get("label") or "") for item in required_anchor_alignment if not item.get("aligned")]
    if required_anchor_alignment:
        if required_policy == "touch_all":
            anchor_alignment_satisfied = all(item.get("aligned") for item in required_anchor_alignment)
        else:
            anchor_alignment_satisfied = any(item.get("aligned") for item in required_anchor_alignment)
    else:
        anchor_alignment_satisfied = False
    anchor_alignment = {
        "policy": required_policy,
        "required_anchors": [item.get("label") for item in required_anchor_alignment],
        "aligned_anchors": [item.get("label") for item in aligned_required_anchors],
        "missing_anchors": [label for label in missing_required_anchor_labels if label],
    }
    surface_alignment = {
        "required_surfaces": required_surface_names,
        "optional_surfaces": optional_surface_names,
        "available_surfaces": available_surfaces,
        "missing_required_surfaces": missing_required_surfaces,
        "missing_optional_surfaces": missing_optional_surfaces,
    }
    requires_retrieval = bool(coverage_policy.get("requires_retrieval", True))
    if not required_anchor_alignment and requires_retrieval:
        diagnostic_gaps.append("no required anchors compiled for coverage")
        blocking_gaps.append("no required anchors compiled for coverage")
    if required_policy == "touch_all":
        for label in missing_required_anchor_labels:
            if label:
                diagnostic_gaps.append(f"required anchor not aligned: {label}")
                blocking_gaps.append(f"required anchor not aligned: {label}")
    elif required_anchor_alignment and not anchor_alignment_satisfied:
        blocking_gaps.append("no required anchors aligned under touch_any policy")
        for label in missing_required_anchor_labels:
            if label:
                diagnostic_gaps.append(f"required anchor not aligned: {label}")
    provenance_present = all(item["has_provenance"] for item in selected_chunk_provenance) if selected_chunk_provenance else False
    covered = (
        target_present
        and target_valid
        and (not requires_retrieval or bool(selected_chunks))
        and (not requires_retrieval or provenance_present)
        and anchor_alignment_satisfied
        and not avoid_violations
    )
    return {
        "target_present": target_present,
        "target_valid": target_valid,
        "coverage_mode": "provenance_alignment",
        "anchor_alignment": anchor_alignment,
        "surface_alignment": surface_alignment,
        "required_anchor_alignment": required_anchor_alignment,
        "retrieval_plan_alignment": retrieval_plan_alignment,
        "selected_chunk_provenance": selected_chunk_provenance,
        "missing_required_surfaces": missing_required_surfaces,
        "missing_required_anchors": missing_required_anchors,
        "diagnostic_gaps": diagnostic_gaps,
        "blocking_gaps": blocking_gaps,
        "avoid_violations": avoid_violations,
        "must_preserve": legacy_must_preserve,
        "should_include": should_include,
        "avoid_satisfying_with": avoid_results,
        "missing_must_preserve": missing_required_anchors,
        "missing_should_include": missing_should_include,
        "present_avoid_satisfying_with": avoid_violations,
        "covered": covered,
    }


def _evaluate_semantic_target_coverage(
    *,
    turn_compilation_packet: dict[str, Any],
    semantic_traversal_manifest: dict[str, Any],
    retrieval_packet: dict[str, Any],
) -> dict[str, Any]:
    semantic_target = turn_compilation_packet.get("semantic_target")
    selected_chunks = list(retrieval_packet.get("selected_chunks") or [])
    target_present = isinstance(semantic_target, dict)
    target_valid = target_present
    must_preserve_results: list[dict[str, Any]] = []
    should_include_results: list[dict[str, Any]] = []
    avoid_results: list[dict[str, Any]] = []
    missing_must_preserve: list[str] = []
    missing_should_include: list[str] = []
    present_avoid_satisfying_with: list[str] = []
    limits = [
        "deterministic lexical/metadata evidence only",
        "required resolved referents may be satisfied by discourse-anchor provenance",
        "does not claim full semantic entailment",
    ]
    evaluation_mode = "deterministic_retrieved_evidence_match_with_discourse_anchor_evidence"
    if not target_present:
        return {
            "target_present": False,
            "target_valid": False,
            "target_hash": None,
            "evaluation_mode": evaluation_mode,
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
        target_hash = sha256_json(semantic_target)
    except Exception:
        target_hash = None
        target_valid = False
    if not isinstance(semantic_target.get("must_preserve"), list):
        target_valid = False
    if not isinstance(semantic_target.get("should_include"), list):
        target_valid = False
    if not isinstance(semantic_target.get("avoid_satisfying_with"), list):
        target_valid = False
    if "query_text" not in semantic_target or semantic_target.get("query_text") is None:
        target_valid = False
    elif not isinstance(semantic_target.get("query_text"), str):
        target_valid = False
    if "allow_no_retrieval_needed" not in semantic_target or semantic_target.get("allow_no_retrieval_needed") is None:
        target_valid = False
    elif not isinstance(semantic_target.get("allow_no_retrieval_needed"), bool):
        target_valid = False

    for target_value in list(semantic_target.get("must_preserve") or []):
        target_text = str(target_value)
        discourse_anchor_evidence = _match_target_to_resolved_referent_anchor(
            target_text,
            turn_compilation_packet=turn_compilation_packet,
        )
        if discourse_anchor_evidence is not None:
            must_preserve_results.append(
                {
                    "target": target_text,
                    "covered": True,
                    "match_type": "resolved_referent_discourse_anchor",
                    "evidence": [discourse_anchor_evidence],
                }
            )
            continue
        if _is_deemphasized_generic_must_preserve_target(target_text, turn_compilation_packet=turn_compilation_packet):
            must_preserve_results.append(
                {
                    "target": target_text,
                    "covered": True,
                    "match_type": "generic_relation_shell_deemphasized_after_anchor_repair",
                    "evidence": [
                        {
                            "diagnostic": "not treated as hard proof anchor after concrete-anchor repair",
                        }
                    ],
                }
            )
            continue
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

    for target_value in list(semantic_target.get("should_include") or []):
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

    for target_value in list(semantic_target.get("avoid_satisfying_with") or []):
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
        "evaluation_mode": evaluation_mode,
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
    semantic_target: dict[str, Any] | None,
    semantic_compiler_packet: dict[str, Any] | None = None,
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
    compiler_plan_terms: list[str] = []
    if isinstance(semantic_target, dict):
        for key in ("must_preserve", "should_include"):
            for term in _collect_string_terms(semantic_target.get(key)):
                if term not in coverage_target_terms:
                    coverage_target_terms.append(term)
                if "semantic_target" not in candidate_term_sources.setdefault(term, []):
                    candidate_term_sources[term].append("semantic_target")
        query_text = semantic_target.get("query_text")
        for term in _collect_string_terms(query_text):
            if term not in coverage_target_terms:
                coverage_target_terms.append(term)
            if "semantic_target.query_text" not in candidate_term_sources.setdefault(term, []):
                candidate_term_sources[term].append("semantic_target.query_text")
    if isinstance(semantic_compiler_packet, dict):
        retrieval_plan = _dict_or_empty(semantic_compiler_packet.get("retrieval_plan"))
        for key in ("lexical_terms", "entity_terms", "relation_terms", "graph_seeds", "avoid_terms"):
            for term in _collect_string_terms(retrieval_plan.get(key)):
                if term not in compiler_plan_terms:
                    compiler_plan_terms.append(term)
                if (
                    "raw_user_input" not in candidate_term_sources.get(term, [])
                    and f"semantic_compiler_packet.retrieval_plan.{key}" not in candidate_term_sources.setdefault(term, [])
                ):
                    candidate_term_sources[term].append(f"semantic_compiler_packet.retrieval_plan.{key}")
        semantic_target = _dict_or_empty(semantic_compiler_packet.get("semantic_target"))
        for term in _collect_string_terms(semantic_target.get("canonical_query")):
            if term not in compiler_plan_terms:
                compiler_plan_terms.append(term)
            if (
                "raw_user_input" not in candidate_term_sources.get(term, [])
                and "semantic_compiler_packet.semantic_target.canonical_query" not in candidate_term_sources.setdefault(term, [])
            ):
                candidate_term_sources[term].append("semantic_compiler_packet.semantic_target.canonical_query")

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
    for term in raw_lexical_terms + compiler_plan_terms + extraction_hint_terms + coverage_target_terms:
        if term not in combined_candidate_terms:
            combined_candidate_terms.append(term)

    model_proposed_only_terms = [
        term
        for term in compiler_plan_terms + extraction_hint_terms + coverage_target_terms
        if "raw_user_input" not in candidate_term_sources.get(term, [])
    ]
    return {
        "raw_lexical_terms": raw_lexical_terms,
        "extraction_hint_terms": extraction_hint_terms,
        "coverage_target_terms": coverage_target_terms,
        "compiler_plan_terms": compiler_plan_terms,
        "combined_candidate_terms": combined_candidate_terms,
        "candidate_term_sources": candidate_term_sources,
        "model_proposed_only_terms": model_proposed_only_terms,
        "used_additively_for_retrieval": True,
    }


def _build_turn_compilation_packet(
    *,
    thread_document: dict[str, Any],
    prior_thread_state: dict[str, Any],
    user_input: str,
    turn_id: int,
    semantic_compiler: SemanticCompilerArtifacts,
    config: RuntimeConfig | None = None,
) -> dict[str, Any]:
    semantic_payload, extracted_semantic_target = _extract_semantic_outputs(
        isolated_packet=semantic_compiler.isolated_packet,
        contextual_packet=semantic_compiler.contextual_packet,
    )
    semantic_compiler_packet, semantic_compiler_reasons = _build_semantic_compiler_packet(
        user_input=user_input,
        prior_thread_state=prior_thread_state,
        semantic_payload=semantic_payload,
        isolated_packet=semantic_compiler.isolated_packet,
        contextual_packet=semantic_compiler.contextual_packet,
        semantic_target=(
            extracted_semantic_target if isinstance(extracted_semantic_target, dict) else None
        ),
    )
    if config is not None:
        coverage_policy = _dict_or_empty(semantic_compiler_packet.get("coverage_policy"))
        if coverage_policy:
            normalized_coverage_policy = dict(coverage_policy)
            if not _list_or_empty(normalized_coverage_policy.get("required_surfaces")) and not _list_or_empty(
                normalized_coverage_policy.get("optional_surfaces")
            ):
                required_surfaces = [
                    surface_name
                    for surface_name, required in config.coverage_require_surface_contributions.items()
                    if required and surface_name != "primary_corpus"
                ]
                optional_surfaces = [
                    surface_name
                    for surface_name, required in config.coverage_require_surface_contributions.items()
                    if not required or surface_name == "primary_corpus"
                ]
                normalized_coverage_policy["required_surfaces"] = required_surfaces
                normalized_coverage_policy["optional_surfaces"] = optional_surfaces
                semantic_compiler_packet = dict(semantic_compiler_packet)
                semantic_compiler_packet["coverage_policy"] = normalized_coverage_policy
    compiler_validation_reasons = _validate_semantic_compiler_packet(
        semantic_compiler_packet,
        raw_user_input=user_input,
    )

    legacy_semantic_payload, resolved_referent_repair_diagnostics = _repair_resolved_referents_from_deterministic_candidates(
        user_input=user_input,
        prior_thread_state=prior_thread_state,
        semantic_payload=semantic_payload,
    )
    legacy_semantic_target, semantic_target_diagnostics, adequacy_reasons = _repair_semantic_target_anchors(
        user_input=user_input,
        prior_thread_state=prior_thread_state,
        isolated_packet=semantic_compiler.isolated_packet,
        contextual_packet=semantic_compiler.contextual_packet,
        semantic_payload=legacy_semantic_payload,
        semantic_target=extracted_semantic_target,
    )
    referent_validation_reasons, referent_diagnostics = _validate_resolved_referents(
        payload=legacy_semantic_payload,
        raw_user_input=user_input,
        prior_thread_state=prior_thread_state,
    ) if legacy_semantic_payload else ([], {
        "deterministic_followup_detection": _detect_followup_signals(user_input, prior_thread_state),
        "deterministic_referent_targets": [],
        "model_referent_targets": [],
        "disagreements": [],
        "requires_referent_resolution": False,
    })
    legacy_contract_reasons = _validate_semantic_context_payload(
        legacy_semantic_payload,
        raw_user_input=user_input,
        prior_thread_state=prior_thread_state,
    ) if legacy_semantic_payload else [
        "legacy semantic payload failed validation",
    ]
    legacy_warnings: list[str] = []
    for reason in legacy_contract_reasons + referent_validation_reasons + adequacy_reasons:
        if reason not in legacy_warnings:
            legacy_warnings.append(reason)

    semantic_target = _build_legacy_compatibility_target_from_compiler(semantic_compiler_packet)
    if semantic_target is None and isinstance(legacy_semantic_target, dict):
        semantic_target = legacy_semantic_target

    followup_detection = _detect_followup_signals(user_input, prior_thread_state)
    resolved_referents = _list_or_empty(legacy_semantic_payload.get("resolved_referents"))
    referential_followup_detection = followup_detection
    retrieval_preparation = _build_retrieval_preparation(
        user_input=user_input,
        isolated_packet=semantic_compiler.isolated_packet,
        contextual_packet=semantic_compiler.contextual_packet,
        semantic_target=semantic_target,
        semantic_compiler_packet=semantic_compiler_packet,
    )
    return {
        "thread_id": thread_document["thread_id"],
        "turn_id": turn_id,
        "user_input": user_input,
        "raw_user_input": user_input,
        "resolved_referents": resolved_referents,
        "referential_followup_detection": referential_followup_detection,
        "semantic_compiler_packet": semantic_compiler_packet,
        "perturbation_nodes": _list_or_empty(legacy_semantic_payload.get("perturbation_nodes")),
        "contextual_salt_nodes": _list_or_empty(legacy_semantic_payload.get("contextual_salt_nodes")),
        "perturbation_semantic_graph": _dict_or_empty(legacy_semantic_payload.get("perturbation_semantic_graph")),
        "semantic_target": semantic_target,
        "semantic_target_diagnostics": semantic_target_diagnostics,
        "activation_hints": _dict_or_empty(legacy_semantic_payload.get("activation_hints")),
        "limitations": _list_or_empty(legacy_semantic_payload.get("limitations")),
        "resolved_referent_repair_diagnostics": resolved_referent_repair_diagnostics,
        "referent_resolution_diagnostics": referent_diagnostics,
        "extracted_lexical_query_terms": list(retrieval_preparation["raw_lexical_terms"]),
        "retrieval_preparation": retrieval_preparation,
        "semantic_contract_validation": {
            "valid": not compiler_validation_reasons,
            "reasons": compiler_validation_reasons,
        },
        "legacy_semantic_compiler_diagnostics": {
            "legacy_contract_validation": {
                "valid": not legacy_warnings,
                "reasons": legacy_warnings,
            },
            "legacy_payload_diagnostics": legacy_contract_reasons,
            "legacy_compatibility_warnings": legacy_warnings,
            "legacy_semantic_target": legacy_semantic_target,
            "resolved_referent_repair_diagnostics": resolved_referent_repair_diagnostics,
            "referent_resolution_diagnostics": referent_diagnostics,
        },
        "semantic_compiler": {
            "isolated": semantic_compiler.isolated_packet,
            "contextual": semantic_compiler.contextual_packet,
            "statuses": {
                "backend_mode": semantic_compiler.isolated_packet["backend_mode"],
                "isolated_status": semantic_compiler.isolated_packet["status"],
                "contextual_status": semantic_compiler.contextual_packet["status"],
            },
        },
        "prior_thread_state_context": {
            "latest_turn_id": prior_thread_state.get("latest_turn_id", 0),
            "conversation_summary": prior_thread_state.get("conversation_summary", ""),
            "recent_semantic_trajectory": list(prior_thread_state.get("recent_semantic_trajectory") or []),
            "recent_messages": list(prior_thread_state.get("recent_messages") or [])[-4:],
        },
        "explicit_limitation": "raw user input remains authoritative; semantic compiler output is additive only",
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


def _build_extractor_thread_context(prior_thread_state: dict[str, Any]) -> dict[str, Any]:
    recent_messages = list(prior_thread_state.get("recent_messages") or [])
    recent_user_messages = [
        str(message.get("content") or "").strip()
        for message in recent_messages
        if isinstance(message, dict)
        and str(message.get("role") or "").lower() == "user"
        and str(message.get("content") or "").strip()
    ][-4:]
    assistant_recent_messages = {
        str(message.get("content") or "").strip()
        for message in recent_messages
        if isinstance(message, dict)
        and str(message.get("role") or "").lower() == "assistant"
        and str(message.get("content") or "").strip()
    }
    recent_semantic_trajectory: list[str] = []
    for item in list(prior_thread_state.get("recent_semantic_trajectory") or []):
        if not isinstance(item, str):
            continue
        cleaned = item.strip()
        if not cleaned or cleaned in assistant_recent_messages:
            continue
        if cleaned not in recent_semantic_trajectory:
            recent_semantic_trajectory.append(cleaned)
    context = {
        "latest_turn_id": prior_thread_state.get("latest_turn_id", 0),
        "latest_user_input": prior_thread_state.get("latest_user_input"),
        "recent_user_messages": recent_user_messages,
        "recent_semantic_trajectory": recent_semantic_trajectory[-4:],
        "current_user_goals": list(prior_thread_state.get("current_user_goals") or []),
        "open_questions": list(prior_thread_state.get("open_questions") or []),
        "active_constraints": list(prior_thread_state.get("active_constraints") or []),
    }
    conversation_summary = str(prior_thread_state.get("conversation_summary") or "").strip()
    if conversation_summary:
        context["conversation_summary"] = conversation_summary
    return context


def _build_contextual_extraction_request(
    *,
    user_input: str,
    prior_thread_state: dict[str, Any],
    isolated_semantic_compiler: dict[str, Any] | None,
) -> dict[str, Any]:
    deterministic_followup_detection = _detect_followup_signals(user_input, prior_thread_state)
    base_instruction = (
        "Hydrate the isolated extraction with conversation context. "
        "Return JSON only. "
        "Do not answer the user. Preserve the raw message. "
        "Produce raw_user_input, perturbation_nodes, contextual_salt_nodes, perturbation_semantic_graph, "
        "semantic_target, activation_hints, and limitations."
    )
    if not deterministic_followup_detection.get("requires_referent_resolution"):
        return {
            "mode": "contextual",
            "raw_user_input": user_input,
            "prior_thread_state": prior_thread_state,
            "isolated_semantic_compiler": isolated_semantic_compiler or {},
            "instruction": base_instruction,
        }

    deterministic_resolved_referent_candidates = _resolve_followup_referents(
        raw_user_input=user_input,
        prior_thread_state=prior_thread_state,
        followup_detection=deterministic_followup_detection,
    )
    return {
        "mode": "contextual",
        "raw_user_input": user_input,
        "extractor_thread_context": _build_extractor_thread_context(prior_thread_state),
        "deterministic_followup_detection": deterministic_followup_detection,
        "deterministic_resolved_referent_candidates": deterministic_resolved_referent_candidates,
        "isolated_semantic_compiler": isolated_semantic_compiler or {},
        "instruction": (
            "Hydrate the isolated extraction with conversation context. "
            "Return JSON only. "
            "Do not answer the user. Preserve the raw message. "
            "Use deterministic_resolved_referent_candidates when present. "
            "Do not resolve pronouns from scratch unless the candidate is clearly contradicted. "
            "For referential follow-ups, semantic_target.must_preserve must include the resolved referent. "
            "Produce raw_user_input, perturbation_nodes, contextual_salt_nodes, perturbation_semantic_graph, "
            "semantic_target, activation_hints, and limitations."
        ),
    }


def _build_extraction_packet(
    *,
    thread_id: str,
    turn_id: int,
    backend_mode: str,
    request_packet: dict[str, Any],
    response: SemanticCompilerResponse,
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
    response: SemanticCompilerResponse,
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


def _run_semantic_compiler(
    *,
    thread_id: str,
    turn_id: int,
    user_input: str,
    prior_thread_state: dict[str, Any],
    backend: SemanticCompilerBackend,
) -> SemanticCompilerArtifacts:
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
        isolated_semantic_compiler=isolated_response.parsed_payload,
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
    return SemanticCompilerArtifacts(
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


def _build_query_embedding_text(turn_compilation_packet: dict[str, Any]) -> str:
    semantic_compiler_packet = _dict_or_empty(turn_compilation_packet.get("semantic_compiler_packet"))
    retrieval_plan = _dict_or_empty(semantic_compiler_packet.get("retrieval_plan"))
    semantic_target = _dict_or_empty(semantic_compiler_packet.get("semantic_target"))
    if semantic_target or retrieval_plan:
        parts = [
            str(semantic_target.get("canonical_query") or ""),
            " ".join(str(item) for item in list(retrieval_plan.get("entity_terms") or [])),
            " ".join(str(item) for item in list(retrieval_plan.get("relation_terms") or [])),
            " ".join(str(item) for item in list(retrieval_plan.get("lexical_terms") or [])),
            str(turn_compilation_packet.get("user_input") or ""),
        ]
        return " ".join(part for part in parts if part).strip()
    semantic_target = turn_compilation_packet.get("semantic_target") or {}
    parts = [
        str(semantic_target.get("query_text") or ""),
        " ".join(str(item) for item in list(semantic_target.get("must_preserve") or [])),
        " ".join(str(item) for item in list(semantic_target.get("should_include") or [])),
        str(turn_compilation_packet.get("user_input") or ""),
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
    turn_compilation_packet: dict[str, Any],
    embedding_backend: EmbeddingBackend,
) -> dict[str, Any]:
    database_path = _database_path(config, data_root)
    retrieval_preparation = dict(turn_compilation_packet.get("retrieval_preparation") or {})
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
            "thread_id": turn_compilation_packet["thread_id"],
            "turn_id": turn_compilation_packet["turn_id"],
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

        query_text = _build_query_embedding_text(turn_compilation_packet)
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
        "thread_id": turn_compilation_packet["thread_id"],
        "turn_id": turn_compilation_packet["turn_id"],
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
    turn_compilation_packet: dict[str, Any],
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
    selected: list[dict[str, Any]] = []
    selected_chunk_ids: list[str] = []
    covered_required_surfaces: set[str] = set()

    def _append_selected_candidate(candidate: dict[str, Any]) -> None:
        chunk_id = str(candidate["chunk_id"])
        if chunk_id in selected_chunk_ids:
            return
        selected.append(candidate)
        selected_chunk_ids.append(chunk_id)
        for surface_name in list(candidate.get("surface_contributions") or []):
            covered_required_surfaces.add(str(surface_name))

    required_surface_contributions = config.coverage_require_surface_contributions
    for surface_name, required in required_surface_contributions.items():
        if len(selected) >= config.max_retrieval_chunks:
            break
        if not required or surface_name in covered_required_surfaces:
            continue
        required_candidate = next(
            (
                candidate
                for candidate in ranked_candidates
                if surface_name in list(candidate.get("surface_contributions") or [])
                and str(candidate["chunk_id"]) not in selected_chunk_ids
            ),
            None,
        )
        if required_candidate is not None:
            _append_selected_candidate(required_candidate)

    for candidate in ranked_candidates:
        if len(selected) >= config.max_retrieval_chunks:
            break
        _append_selected_candidate(candidate)

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
    if not turn_compilation_packet.get("semantic_contract_validation", {}).get("valid"):
        manifest_validity_reasons.append("semantic compiler packet failed validation")
    if turn_compilation_packet.get("perturbation_semantic_graph") is None:
        manifest_validity_reasons.append("perturbation_semantic_graph missing")
    if not isinstance(turn_compilation_packet.get("semantic_compiler_packet"), dict):
        manifest_validity_reasons.append("semantic_compiler_packet missing")
    if not ranked_candidates:
        manifest_validity_reasons.append("semantic traversal produced no candidate regions")
    manifest = {
        "thread_id": turn_compilation_packet["thread_id"],
        "turn_id": turn_compilation_packet["turn_id"],
        "semantic_compiler_packet_hash": (
            sha256_json(turn_compilation_packet["semantic_compiler_packet"])
            if isinstance(turn_compilation_packet.get("semantic_compiler_packet"), dict)
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
    turn_compilation_packet: dict[str, Any],
    activated_semantic_regions: dict[str, Any],
    semantic_traversal_manifest: dict[str, Any],
    retrieval_packet: dict[str, Any],
    config: RuntimeConfig,
    semantic_traversal_manifest_hash: str,
    retrieval_packet_hash: str,
) -> dict[str, Any]:
    statuses = dict(turn_compilation_packet.get("semantic_compiler", {}).get("statuses") or {})
    backend_mode = str(statuses.get("backend_mode") or "unknown")
    isolated_status = str(statuses.get("isolated_status") or "unknown")
    contextual_status = str(statuses.get("contextual_status") or "unknown")
    reasons: list[str] = []

    if backend_mode in {"disabled", "stub", "unavailable"}:
        reasons.append(f"semantic compiler backend `{backend_mode}` is not valid for the normal runtime")
    if contextual_status != "parsed":
        reasons.append(f"contextual semantic compiler packet did not produce a valid parsed result: {contextual_status}")
    contract_validation = dict(turn_compilation_packet.get("semantic_contract_validation") or {})
    if not contract_validation.get("valid"):
        reasons.extend(list(contract_validation.get("reasons") or []))
        reasons.append("semantic compiler packet failed validation")

    surface_statuses = {
        str(surface["surface"]): str(surface["status"])
        for surface in list(activated_semantic_regions.get("activation_surfaces") or [])
    }
    required_surface_contributions = config.coverage_require_surface_contributions
    if required_surface_contributions.get("lexical_index_surface") and surface_statuses.get("lexical_index_surface") == "blocked":
        reasons.append("lexical_index_surface unavailable")
    if required_surface_contributions.get("vector_index_surface") and surface_statuses.get("vector_index_surface") != "activated":
        reasons.append("vector_index_surface unavailable or missing configured embeddings")
    if required_surface_contributions.get("graph_layer") and surface_statuses.get("graph_layer") == "blocked" and (
        "graph_layer" in _list_or_empty((turn_compilation_packet.get("coverage_policy") or {}).get("required_surfaces"))
    ):
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
    semantic_target_coverage = _evaluate_semantic_compiler_alignment(
        turn_compilation_packet=turn_compilation_packet,
        semantic_traversal_manifest=semantic_traversal_manifest,
        retrieval_packet=retrieval_packet,
        config=config,
    )
    if not semantic_target_coverage["target_present"] or not semantic_target_coverage["target_valid"]:
        reasons.append("semantic compiler packet failed validation")
    for gap in list(semantic_target_coverage.get("blocking_gaps") or []):
        reasons.append(gap)
    if semantic_target_coverage.get("avoid_violations"):
        for target in list(semantic_target_coverage.get("avoid_violations") or []):
            reasons.append(f"semantic compiler avoid term present in retrieved evidence: {target}")
    target_allows_no_retrieval = not bool(_dict_or_empty(turn_compilation_packet.get("semantic_compiler_packet")).get("coverage_policy", {}).get("requires_retrieval", True))
    if selected_chunk_count < min_selected_chunks and not (allow_no_retrieval_needed and target_allows_no_retrieval):
        reasons.append(f"selected chunk count below configured minimum: {selected_chunk_count} < {min_selected_chunks}")
    if selected_chunk_count > max_selected_chunks:
        reasons.append(f"selected chunk count exceeds configured maximum: {selected_chunk_count} > {max_selected_chunks}")

    actual_surface_contributions = dict(semantic_traversal_manifest.get("surface_contributions") or {})

    decision = "approved" if not reasons else "blocked"
    semantic_compiler_packet = turn_compilation_packet.get("semantic_compiler_packet")
    referent_resolution_diagnostics = dict(turn_compilation_packet.get("referent_resolution_diagnostics") or {})
    return {
        "decision": decision,
        "semantic_compiler_packet_hash": sha256_json(semantic_compiler_packet) if isinstance(semantic_compiler_packet, dict) else None,
        "semantic_traversal_manifest_hash": semantic_traversal_manifest_hash,
        "retrieval_packet_hash": retrieval_packet_hash,
        "evaluated_activation_surfaces": list(activated_semantic_regions.get("activation_surfaces") or []),
        "semantic_target_coverage": semantic_target_coverage,
        "blocking_reasons": _dedupe_reasons(reasons),
        "referent_resolution_diagnostics": referent_resolution_diagnostics,
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
    turn_compilation_packet: dict[str, Any],
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
        "semantic_compiler_packet": turn_compilation_packet.get("semantic_compiler_packet"),
        "legacy_semantic_compiler_diagnostics": turn_compilation_packet.get("legacy_semantic_compiler_diagnostics", {}),
        "semantic_compiler": turn_compilation_packet["semantic_compiler"],
        "turn_compilation_packet": turn_compilation_packet,
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
            "Treat semantic_compiler_packet as the primary semantic target object.",
            "Use retrieved material only when it has been approved for synthesis.",
            "Do not invent retrieval results or claim thesis-valid traversal when blocked.",
            "Do not decide evidence validity or perform retrieval inside the final synthesis step.",
        ],
    }


def _persist_turn_artifacts(
    *,
    turn_root: Path,
    semantic_compiler_packet: dict[str, Any],
    turn_compilation_packet: dict[str, Any],
    semantic_traversal_manifest: dict[str, Any],
    retrieval_packet: dict[str, Any],
    coverage_report: dict[str, Any],
    synthesis_context_packet: dict[str, Any],
    state_delta: dict[str, Any],
    semantic_compiler: SemanticCompilerArtifacts,
) -> dict[str, Path]:
    turn_root.mkdir(parents=True, exist_ok=True)
    semantic_compiler_packet_path = turn_root / "semantic_compiler_packet.json"
    turn_compilation_packet_path = turn_root / "turn_compilation_packet.json"
    semantic_traversal_manifest_path = turn_root / "semantic_traversal_manifest.json"
    retrieval_packet_path = turn_root / "retrieval_packet.json"
    coverage_report_path = turn_root / "coverage_report.json"
    synthesis_context_packet_path = turn_root / "synthesis_context_packet.json"
    state_delta_path = turn_root / "state_delta.json"
    isolated_semantic_compiler_packet_path = turn_root / "isolated_semantic_compiler_packet.json"
    isolated_semantic_compiler_raw_path = turn_root / "isolated_semantic_compiler_raw.json"
    contextual_semantic_compiler_packet_path = turn_root / "contextual_semantic_compiler_packet.json"
    contextual_semantic_compiler_raw_path = turn_root / "contextual_semantic_compiler_raw.json"
    write_json(semantic_compiler_packet_path, semantic_compiler_packet)
    write_json(turn_compilation_packet_path, turn_compilation_packet)
    write_json(semantic_traversal_manifest_path, semantic_traversal_manifest)
    write_json(retrieval_packet_path, retrieval_packet)
    write_json(coverage_report_path, coverage_report)
    write_json(synthesis_context_packet_path, synthesis_context_packet)
    write_json(state_delta_path, state_delta)
    write_json(isolated_semantic_compiler_packet_path, semantic_compiler.isolated_packet)
    write_json(isolated_semantic_compiler_raw_path, semantic_compiler.isolated_raw_artifact)
    write_json(contextual_semantic_compiler_packet_path, semantic_compiler.contextual_packet)
    write_json(contextual_semantic_compiler_raw_path, semantic_compiler.contextual_raw_artifact)
    return {
        "semantic_compiler_packet_path": semantic_compiler_packet_path,
        "turn_compilation_packet_path": turn_compilation_packet_path,
        "semantic_traversal_manifest_path": semantic_traversal_manifest_path,
        "retrieval_packet_path": retrieval_packet_path,
        "coverage_report_path": coverage_report_path,
        "synthesis_context_packet_path": synthesis_context_packet_path,
        "state_delta_path": state_delta_path,
        "isolated_semantic_compiler_packet_path": isolated_semantic_compiler_packet_path,
        "isolated_semantic_compiler_raw_path": isolated_semantic_compiler_raw_path,
        "contextual_semantic_compiler_packet_path": contextual_semantic_compiler_packet_path,
        "contextual_semantic_compiler_raw_path": contextual_semantic_compiler_raw_path,
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
    semantic_compiler_backend: SemanticCompilerBackend | None = None,
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
    extractor_backend = semantic_compiler_backend or resolve_semantic_compiler_backend(repo_root=repo_root, config=resolved_config)
    resolved_embedding_backend = embedding_backend or resolve_embedding_backend(resolved_config)

    semantic_compiler = _run_semantic_compiler(
        thread_id=paths.thread_id,
        turn_id=turn_id,
        user_input=user_input,
        prior_thread_state=prior_thread_state,
        backend=extractor_backend,
    )
    turn_compilation_packet = _build_turn_compilation_packet(
        thread_document=thread_document,
        prior_thread_state=prior_thread_state,
        user_input=user_input,
        turn_id=turn_id,
        semantic_compiler=semantic_compiler,
        config=config,
    )
    activated_semantic_regions = _build_latent_space_activation(
        repo_root=repo_root,
        data_root=data_root,
        config=resolved_config,
        turn_compilation_packet=turn_compilation_packet,
        embedding_backend=resolved_embedding_backend,
    )
    semantic_traversal_manifest = _build_semantic_traversal_manifest(
        turn_compilation_packet=turn_compilation_packet,
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
        turn_compilation_packet=turn_compilation_packet,
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
        turn_compilation_packet=turn_compilation_packet,
        semantic_traversal_manifest=semantic_traversal_manifest,
        retrieval_packet=retrieval_packet,
        coverage_report=coverage_report,
    )
    semantic_compiler_packet = turn_compilation_packet["semantic_compiler_packet"]
    semantic_compiler_packet_hash = sha256_json(semantic_compiler_packet)
    turn_compilation_packet_hash = sha256_json(turn_compilation_packet)
    coverage_report_hash = sha256_json(coverage_report)
    synthesis_context_packet_hash = sha256_json(synthesis_context_packet)
    isolated_semantic_compiler_packet_hash = sha256_json(semantic_compiler.isolated_packet)
    isolated_semantic_compiler_raw_hash = sha256_json(semantic_compiler.isolated_raw_artifact)
    contextual_semantic_compiler_packet_hash = sha256_json(semantic_compiler.contextual_packet)
    contextual_semantic_compiler_raw_hash = sha256_json(semantic_compiler.contextual_raw_artifact)
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
        semantic_compiler_packet=semantic_compiler_packet,
        turn_compilation_packet=turn_compilation_packet,
        semantic_traversal_manifest=semantic_traversal_manifest,
        retrieval_packet=retrieval_packet,
        coverage_report=coverage_report,
        synthesis_context_packet=synthesis_context_packet,
        state_delta=state_delta,
        semantic_compiler=semantic_compiler,
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
        "isolated_semantic_compiler_packet_hash": isolated_semantic_compiler_packet_hash,
        "isolated_semantic_compiler_raw_hash": isolated_semantic_compiler_raw_hash,
        "contextual_semantic_compiler_packet_hash": contextual_semantic_compiler_packet_hash,
        "contextual_semantic_compiler_raw_hash": contextual_semantic_compiler_raw_hash,
        "semantic_compiler_packet_hash": semantic_compiler_packet_hash,
        "turn_compilation_packet_hash": turn_compilation_packet_hash,
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
        semantic_compiler_packet_path=artifact_paths["semantic_compiler_packet_path"],
        turn_compilation_packet_path=artifact_paths["turn_compilation_packet_path"],
        semantic_traversal_manifest_path=artifact_paths["semantic_traversal_manifest_path"],
        retrieval_packet_path=artifact_paths["retrieval_packet_path"],
        coverage_report_path=artifact_paths["coverage_report_path"],
        synthesis_context_packet_path=artifact_paths["synthesis_context_packet_path"],
        state_delta_path=artifact_paths["state_delta_path"],
        isolated_semantic_compiler_packet_path=artifact_paths["isolated_semantic_compiler_packet_path"],
        isolated_semantic_compiler_raw_path=artifact_paths["isolated_semantic_compiler_raw_path"],
        contextual_semantic_compiler_packet_path=artifact_paths["contextual_semantic_compiler_packet_path"],
        contextual_semantic_compiler_raw_path=artifact_paths["contextual_semantic_compiler_raw_path"],
        assistant_response=assistant_response,
        llm_metadata=llm_metadata,
        runtime_outcome=runtime_outcome,
        blocking_reasons=blocking_reasons,
        prior_thread_state=prior_thread_state,
        next_thread_state=next_thread_state,
        ledger_record=ledger_record,
        semantic_compiler_packet=semantic_compiler_packet,
        turn_compilation_packet=turn_compilation_packet,
        semantic_traversal_manifest=semantic_traversal_manifest,
        retrieval_packet=retrieval_packet,
        coverage_report=coverage_report,
        synthesis_context_packet=synthesis_context_packet,
        isolated_semantic_compiler_packet=semantic_compiler.isolated_packet,
        contextual_semantic_compiler_packet=semantic_compiler.contextual_packet,
    )
