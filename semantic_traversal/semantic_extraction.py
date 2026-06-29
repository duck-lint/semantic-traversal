from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from urllib import error, request

from .config import RuntimeConfig


EXTRACTION_TOKEN_RE = re.compile(r"[A-Za-z0-9']+")
EXTRACTION_STOP_WORDS = {
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

FOLLOWUP_SURFACE_FORMS = (
    "it",
    "that",
    "this",
    "those",
    "they",
    "them",
)

FOLLOWUP_PHRASE_PATTERNS = (
    (re.compile(r"\bhow\s+(?:it|that|this|those|they|them)\b", re.IGNORECASE), "how it"),
    (re.compile(r"\bwhat\s+about\s+(?:that|it|this|those|them|they)\b", re.IGNORECASE), "what about that"),
    (re.compile(r"\bsame\s+thing\b", re.IGNORECASE), "same thing"),
    (re.compile(r"\bthat\s+makes\s+me\b", re.IGNORECASE), "that makes me"),
    (re.compile(r"\bhow\s+that\s+makes\s+me\s+feel\b", re.IGNORECASE), "how that makes me feel"),
    (re.compile(r"\bhow\s+it\s+makes\s+me\s+feel\b", re.IGNORECASE), "how it makes me feel"),
)

FOLLOWUP_EXPLETIVE_PATTERNS = (
    re.compile(r"\bis\s+it\s+possible\b", re.IGNORECASE),
    re.compile(r"\bis\s+it\s+worth\b", re.IGNORECASE),
    re.compile(r"\bit\s+seems\b", re.IGNORECASE),
    re.compile(r"\bit\s+looks\s+like\b", re.IGNORECASE),
)

REFERENT_EXTRACTION_PATTERNS = (
    re.compile(
        r"\b(?:about|regarding|around|concerning|on|for|with|toward|towards|linked to|related to)\s+(.+?)(?:[?.!,]|$)",
        re.IGNORECASE,
    ),
    re.compile(r"\b(?:think|feel|wonder|care|ask)\s+about\s+(.+?)(?:[?.!,]|$)", re.IGNORECASE),
)


@dataclass(frozen=True)
class SemanticExtractionResponse:
    parsed_payload: dict[str, Any] | None
    raw_response: str | None
    metadata: dict[str, Any]
    diagnostics: dict[str, Any]
    status: str


class SemanticExtractorBackend(Protocol):
    mode_name: str

    def extract_isolated(self, packet: dict[str, Any]) -> SemanticExtractionResponse:
        ...

    def extract_contextual(self, packet: dict[str, Any]) -> SemanticExtractionResponse:
        ...


def extract_terms(text: str) -> list[str]:
    terms: list[str] = []
    seen: set[str] = set()
    for token in EXTRACTION_TOKEN_RE.findall(text.lower()):
        if len(token) < 3 or token in EXTRACTION_STOP_WORDS or token.isdigit():
            continue
        if token not in seen:
            seen.add(token)
            terms.append(token)
    return terms


def _default_limitations() -> list[str]:
    return [
        "model-generated extraction",
        "additive only",
        "not authoritative",
    ]


def _default_contextual_coverage_fields() -> dict[str, Any]:
    return {
        "must_preserve": [],
        "should_include": [],
        "avoid_satisfying_with": [],
        "query_text": "",
        "allow_no_retrieval_needed": False,
    }


def _default_activation_hints() -> dict[str, Any]:
    return {
        "lexical_terms": [],
        "phrases": [],
        "conceptual_neighbors": [],
        "relation_hints": [],
        "temporal_hints": [],
        "entity_hints": [],
    }


def _semantic_extraction_schema(mode: str) -> dict[str, Any]:
    if mode == "isolated":
        return {
            "type": "object",
            "additionalProperties": True,
            "required": [
                "raw_user_input",
                "probable_user_intent",
                "candidate_targets",
                "candidate_relations",
                "question_shape",
                "explicit_user_constraints",
                "implicit_needs_or_pressures",
                "terms_or_phrases_not_to_discard",
                "ambiguities",
                "extraction_confidence",
                "limitations",
            ],
            "properties": {
                "raw_user_input": {"type": "string"},
                "probable_user_intent": {"type": "string"},
                "candidate_targets": {"type": "array", "items": {"type": "string"}},
                "candidate_relations": {"type": "array", "items": {"type": "string"}},
                "question_shape": {"type": ["string", "null"]},
                "explicit_user_constraints": {"type": "array", "items": {"type": "string"}},
                "implicit_needs_or_pressures": {"type": "array", "items": {"type": "string"}},
                "terms_or_phrases_not_to_discard": {"type": "array", "items": {"type": "string"}},
                "ambiguities": {"type": "array", "items": {"type": "string"}},
                "extraction_confidence": {"type": "string"},
                "limitations": {"type": "array", "items": {"type": "string"}},
            },
        }
    return {
        "type": "object",
        "additionalProperties": True,
        "required": [
            "raw_user_input",
            "contextual_user_intent",
            "thread_relevant_context",
            "semantic_pressure",
            "resolved_referents",
            "perturbation_nodes",
            "contextual_salt_nodes",
            "perturbation_semantic_graph",
            "semantic_coverage_target",
            "activation_hints",
            "limitations",
        ],
        "properties": {
            "raw_user_input": {"type": "string"},
            "contextual_user_intent": {"type": "string"},
            "thread_relevant_context": {"type": "array", "items": {"type": "string"}},
            "semantic_pressure": {"type": ["string", "null"]},
            "resolved_referents": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": True,
                    "required": [
                        "surface_form",
                        "resolved_to",
                        "source",
                        "confidence",
                        "required_for_target",
                    ],
                    "properties": {
                        "surface_form": {"type": "string"},
                        "resolved_to": {"type": "string"},
                        "source": {"type": "string"},
                        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                        "required_for_target": {"type": "boolean"},
                    },
                },
            },
            "perturbation_nodes": {
                "type": "array",
                "items": {"type": "object"},
            },
            "contextual_salt_nodes": {
                "type": "array",
                "items": {"type": "object"},
            },
            "perturbation_semantic_graph": {"type": "object"},
            "semantic_coverage_target": {
                "type": "object",
                "additionalProperties": True,
                "required": [
                    "must_preserve",
                    "should_include",
                    "avoid_satisfying_with",
                    "query_text",
                    "allow_no_retrieval_needed",
                ],
                "properties": {
                    "must_preserve": {"type": "array", "items": {"type": "string"}},
                    "should_include": {"type": "array", "items": {"type": "string"}},
                    "avoid_satisfying_with": {"type": "array", "items": {"type": "string"}},
                    "query_text": {"type": "string"},
                    "allow_no_retrieval_needed": {"type": "boolean"},
                },
            },
            "activation_hints": {
                "type": "object",
                "additionalProperties": True,
                "required": [
                    "lexical_terms",
                    "phrases",
                    "conceptual_neighbors",
                    "relation_hints",
                    "temporal_hints",
                    "entity_hints",
                ],
                "properties": {
                    "lexical_terms": {"type": "array", "items": {"type": "string"}},
                    "phrases": {"type": "array", "items": {"type": "string"}},
                    "conceptual_neighbors": {"type": "array", "items": {"type": "string"}},
                    "relation_hints": {"type": "array", "items": {"type": "string"}},
                    "temporal_hints": {"type": "array", "items": {"type": "string"}},
                    "entity_hints": {"type": "array", "items": {"type": "string"}},
                },
            },
            "followup_detection": {"type": ["object", "null"]},
            "limitations": {"type": "array", "items": {"type": "string"}},
        },
    }


def _isolated_json_skeleton() -> dict[str, Any]:
    return {
        "raw_user_input": "",
        "probable_user_intent": "",
        "candidate_targets": [],
        "candidate_relations": [],
        "question_shape": None,
        "explicit_user_constraints": [],
        "implicit_needs_or_pressures": [],
        "terms_or_phrases_not_to_discard": [],
        "ambiguities": [],
        "extraction_confidence": "low",
        "limitations": _default_limitations(),
    }


def _contextual_json_skeleton() -> dict[str, Any]:
    return {
        "raw_user_input": "",
        "contextual_user_intent": "",
        "thread_relevant_context": [],
        "semantic_pressure": None,
        "resolved_referents": [],
        "perturbation_nodes": [{"id": "", "label": "", "kind": ""}],
        "contextual_salt_nodes": [{"id": "", "label": "", "kind": ""}],
        "perturbation_semantic_graph": {
            "nodes": [{"id": "", "label": "", "kind": ""}],
            "edges": [{"source": "", "target": "", "kind": ""}],
        },
        "semantic_coverage_target": {
            "must_preserve": [],
            "should_include": [],
            "avoid_satisfying_with": [],
            "query_text": "",
            "allow_no_retrieval_needed": False,
        },
        "activation_hints": {
            "lexical_terms": [],
            "phrases": [],
            "conceptual_neighbors": [],
            "relation_hints": [],
            "temporal_hints": [],
            "entity_hints": [],
        },
        "followup_detection": {
            "is_referential_followup": False,
            "signals": [],
            "surface_forms": [],
            "requires_referent_resolution": False,
        },
        "candidate_targets": [],
        "candidate_relations": [],
        "limitations": _default_limitations(),
    }


def _build_ollama_prompt(*, packet: dict[str, Any]) -> str:
    mode = str(packet.get("mode") or "contextual").strip().lower()
    if mode == "isolated":
        skeleton = _isolated_json_skeleton()
        mode_instruction = (
            "This is isolated extraction. Return a JSON object that matches the isolated schema exactly. "
            "Do not include contextual-only fields. Keep every field type correct."
        )
    else:
        skeleton = _contextual_json_skeleton()
        mode_instruction = (
            "This is contextual extraction. Return a JSON object that matches the contextual schema exactly. "
            "semantic_coverage_target must be an object, activation_hints must be an object, "
            "perturbation_nodes and contextual_salt_nodes must be arrays of objects, and "
            "perturbation_semantic_graph must be an object with nodes and edges arrays. "
            "resolved_referents must be an array of objects when follow-up resolution is required."
        )
    return (
        "Return JSON only.\n"
        f"{mode_instruction}\n"
        "Do not answer the user.\n"
        "Preserve the raw_user_input field exactly.\n"
        "Return JSON only matching this skeleton.\n"
        "Use this exact JSON skeleton as the target shape:\n"
        f"{json.dumps(skeleton, ensure_ascii=True, indent=2)}\n"
        "Packet:\n"
        f"{json.dumps(packet, ensure_ascii=True, indent=2)}"
    )


def _normalize_raw_user_input(
    payload: dict[str, Any],
    raw_user_input: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    model_supplied_present = "raw_user_input" in payload
    model_supplied_raw_user_input = payload.get("raw_user_input") if model_supplied_present else None
    model_supplied_matches = model_supplied_raw_user_input == raw_user_input if model_supplied_present else False
    raw_user_input_repaired = not model_supplied_present or not model_supplied_matches
    result = dict(payload)
    result["raw_user_input"] = raw_user_input
    if "limitations" not in result or not isinstance(result["limitations"], list):
        result["limitations"] = _default_limitations()
    return result, {
        "raw_user_input_validation": {
            "authoritative_raw_user_input": raw_user_input,
            "model_supplied_raw_user_input": model_supplied_raw_user_input,
            "model_supplied_raw_user_input_present": model_supplied_present,
            "model_supplied_raw_user_input_matches": model_supplied_matches,
            "raw_user_input_repaired": raw_user_input_repaired,
        }
    }


def _clean_referent_text(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", text).strip()
    cleaned = cleaned.strip(" \t\r\n\"'`")
    cleaned = re.sub(r"^(?:the|a|an|this|that|these|those)\s+", "", cleaned, flags=re.IGNORECASE)
    return cleaned.strip(" \t\r\n\"'`")


def _recent_user_messages(prior_thread_state: dict[str, Any]) -> list[str]:
    recent_messages = []
    for message in list(prior_thread_state.get("recent_messages") or []):
        if isinstance(message, dict) and str(message.get("role") or "").lower() == "user":
            content = str(message.get("content") or "").strip()
            if content:
                recent_messages.append(content)
    return recent_messages


def _detect_followup_signals(raw_user_input: str, prior_thread_state: dict[str, Any]) -> dict[str, Any]:
    lowered = raw_user_input.lower()
    signals: list[str] = []
    referential_signals: list[str] = []
    surface_forms: list[str] = []
    has_recent_context = bool(prior_thread_state.get("recent_messages") or prior_thread_state.get("recent_semantic_trajectory"))
    expletive_pattern_matched = any(pattern.search(lowered) for pattern in FOLLOWUP_EXPLETIVE_PATTERNS)

    for pattern, label in FOLLOWUP_PHRASE_PATTERNS:
        if pattern.search(lowered):
            signals.append(label)
            referential_signals.append(label)
            surface_forms.append(label)

    for surface_form in FOLLOWUP_SURFACE_FORMS:
        if expletive_pattern_matched and surface_form == "it":
            continue
        if re.search(rf"\b{re.escape(surface_form)}\b", lowered):
            surface_forms.append(surface_form)
            if has_recent_context:
                signals.append(f"deictic:{surface_form}")
                if surface_form in {"it", "that", "this", "those", "they", "them"}:
                    referential_signals.append(f"deictic:{surface_form}")

    question_token_count = len(extract_terms(raw_user_input))
    if has_recent_context and "?" in raw_user_input and question_token_count <= 8:
        signals.append("short_followup_question")

    is_followup = bool(signals)
    requires_resolution = bool(referential_signals) and has_recent_context
    return {
        "is_referential_followup": is_followup,
        "requires_referent_resolution": requires_resolution,
        "signals": signals,
        "referential_signals": referential_signals,
        "surface_forms": list(dict.fromkeys(surface_forms)),
    }


def _extract_referent_candidate(text: str) -> str | None:
    for pattern in REFERENT_EXTRACTION_PATTERNS:
        match = pattern.search(text)
        if match:
            candidate = _clean_referent_text(match.group(1))
            if candidate:
                return candidate
    return None


def _resolve_followup_referents(
    *,
    raw_user_input: str,
    prior_thread_state: dict[str, Any],
    followup_detection: dict[str, Any],
) -> list[dict[str, Any]]:
    if not followup_detection.get("requires_referent_resolution"):
        return []

    referent_candidates = _recent_user_messages(prior_thread_state)
    resolved_to: str | None = None
    source = "prior_thread_state.recent_messages"
    for candidate_text in reversed(referent_candidates):
        resolved_to = _extract_referent_candidate(candidate_text)
        if resolved_to:
            break
    if not resolved_to:
        for candidate_text in reversed(list(prior_thread_state.get("recent_semantic_trajectory") or [])):
            if not isinstance(candidate_text, str):
                continue
            resolved_to = _extract_referent_candidate(candidate_text)
            if resolved_to:
                source = "prior_thread_state.recent_semantic_trajectory"
                break
    if not resolved_to:
        return []

    surface_form = followup_detection.get("surface_forms", [None])[0] or "it"
    return [
        {
            "surface_form": str(surface_form),
            "resolved_to": resolved_to,
            "source": source,
            "confidence": "high" if source == "prior_thread_state.recent_messages" else "medium",
            "required_for_target": True,
        }
    ]


class DisabledSemanticExtractorBackend:
    mode_name = "disabled"

    def extract_isolated(self, packet: dict[str, Any]) -> SemanticExtractionResponse:
        return SemanticExtractionResponse(
            parsed_payload=None,
            raw_response=None,
            metadata={
                "backend_mode": self.mode_name,
                "reason": "semantic extraction disabled",
            },
            diagnostics={},
            status="disabled",
        )

    def extract_contextual(self, packet: dict[str, Any]) -> SemanticExtractionResponse:
        return SemanticExtractionResponse(
            parsed_payload=None,
            raw_response=None,
            metadata={
                "backend_mode": self.mode_name,
                "reason": "semantic extraction disabled",
            },
            diagnostics={},
            status="disabled",
        )


class StubSemanticExtractorBackend:
    mode_name = "stub"

    def __init__(
        self,
        *,
        isolated_payload: dict[str, Any] | None = None,
        contextual_payload: dict[str, Any] | None = None,
    ) -> None:
        self._isolated_payload = isolated_payload
        self._contextual_payload = contextual_payload

    def extract_isolated(self, packet: dict[str, Any]) -> SemanticExtractionResponse:
        raw_user_input = str(packet.get("raw_user_input", ""))
        payload = self._isolated_payload or self._build_default_isolated_payload(raw_user_input)
        normalized_payload, diagnostics = _normalize_raw_user_input(payload, raw_user_input)
        return SemanticExtractionResponse(
            parsed_payload=normalized_payload,
            raw_response=None,
            metadata={
                "backend_mode": self.mode_name,
                "stub_kind": "deterministic",
            },
            diagnostics=diagnostics,
            status="stub",
        )

    def extract_contextual(self, packet: dict[str, Any]) -> SemanticExtractionResponse:
        raw_user_input = str(packet.get("raw_user_input", ""))
        prior_thread_state = packet.get("prior_thread_state") or {}
        isolated_payload = packet.get("isolated_semantic_extraction") or {}
        payload = self._contextual_payload or self._build_default_contextual_payload(
            raw_user_input=raw_user_input,
            prior_thread_state=prior_thread_state,
            isolated_payload=isolated_payload,
        )
        normalized_payload, diagnostics = _normalize_raw_user_input(payload, raw_user_input)
        return SemanticExtractionResponse(
            parsed_payload=normalized_payload,
            raw_response=None,
            metadata={
                "backend_mode": self.mode_name,
                "stub_kind": "deterministic",
            },
            diagnostics=diagnostics,
            status="stub",
        )

    def _build_default_isolated_payload(self, raw_user_input: str) -> dict[str, Any]:
        terms = extract_terms(raw_user_input)
        return {
            "raw_user_input": raw_user_input,
            "probable_user_intent": "stub additive semantic extraction of the latest raw user message",
            "candidate_targets": terms[:3],
            "candidate_relations": terms[3:5],
            "question_shape": "question" if "?" in raw_user_input else None,
            "explicit_user_constraints": [],
            "implicit_needs_or_pressures": [],
            "terms_or_phrases_not_to_discard": terms[:5],
            "ambiguities": [],
            "extraction_confidence": "low",
            "limitations": _default_limitations(),
        }

    def _build_default_contextual_payload(
        self,
        *,
        raw_user_input: str,
        prior_thread_state: dict[str, Any],
        isolated_payload: dict[str, Any],
    ) -> dict[str, Any]:
        terms = extract_terms(raw_user_input)
        preserved_terms = list(isolated_payload.get("terms_or_phrases_not_to_discard") or [])[:5]
        recent_trajectory = list(prior_thread_state.get("recent_semantic_trajectory") or [])[-2:]
        followup_detection = _detect_followup_signals(raw_user_input, prior_thread_state)
        resolved_referents = _resolve_followup_referents(
            raw_user_input=raw_user_input,
            prior_thread_state=prior_thread_state,
            followup_detection=followup_detection,
        )
        if resolved_referents:
            must_preserve = [referent["resolved_to"] for referent in resolved_referents if referent.get("resolved_to")]
            should_include = [raw_user_input]
            avoid_satisfying_with = ["feelings", "felt", "anxiety", "urgency", "context", "influence"]
        else:
            must_preserve = preserved_terms
            should_include = list(isolated_payload.get("candidate_targets") or terms[:2])
            avoid_satisfying_with = []
        return {
            "raw_user_input": raw_user_input,
            "contextual_user_intent": "stub contextual hydration of the isolated semantic extraction",
            "followup_detection": followup_detection,
            "resolved_referents": resolved_referents,
            "perturbation_nodes": [{"id": f"term:{term}", "label": term, "kind": "lexical_term"} for term in terms[:4]],
            "contextual_salt_nodes": [
                {"id": f"context:{index}", "label": text, "kind": "recent_trajectory"}
                for index, text in enumerate(recent_trajectory, start=1)
            ],
            "perturbation_semantic_graph": {
                "nodes": [
                    {"id": f"term:{term}", "label": term, "kind": "lexical_term"}
                    for term in terms[:4]
                ],
                "edges": [],
            },
            "semantic_coverage_target": {
                "must_preserve": must_preserve,
                "should_include": should_include,
                "avoid_satisfying_with": avoid_satisfying_with,
                "query_text": raw_user_input,
                "allow_no_retrieval_needed": False,
            },
            "thread_relevant_context": recent_trajectory,
            "semantic_pressure": None,
            "candidate_targets": list(isolated_payload.get("candidate_targets") or terms[:3]),
            "candidate_relations": list(isolated_payload.get("candidate_relations") or []),
            "activation_hints": {
                "lexical_terms": terms[:4],
                "phrases": [referent["resolved_to"] for referent in resolved_referents if referent.get("resolved_to")] or [],
                "conceptual_neighbors": [],
                "relation_hints": list(isolated_payload.get("candidate_relations") or []),
                "temporal_hints": [],
                "entity_hints": list(isolated_payload.get("candidate_targets") or [])[:2],
            },
            "delta_from_isolated_read": {
                "added_by_context": ["thread_state_available"] if recent_trajectory else [],
                "removed_or_deemphasized_by_context": [],
                "unchanged": preserved_terms,
            },
            "ambiguities": [],
            "extraction_confidence": "low",
            "limitations": _default_limitations(),
        }


class OllamaSemanticExtractorBackend:
    mode_name = "ollama"

    def __init__(self, *, model: str | None, base_url: str, timeout_seconds: int = 20) -> None:
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds

    def extract_isolated(self, packet: dict[str, Any]) -> SemanticExtractionResponse:
        return self._extract(packet)

    def extract_contextual(self, packet: dict[str, Any]) -> SemanticExtractionResponse:
        return self._extract(packet)

    def _extract(self, packet: dict[str, Any]) -> SemanticExtractionResponse:
        if not self._model:
            return SemanticExtractionResponse(
                parsed_payload=None,
                raw_response=None,
                metadata={
                    "backend_mode": self.mode_name,
                    "base_url": self._base_url,
                    "reason": "SEMANTIC_EXTRACTOR_MODEL not configured",
                },
                diagnostics={},
                status="unavailable",
            )
        prompt = _build_ollama_prompt(packet=packet)
        payload = {
            "model": self._model,
            "prompt": prompt,
            "stream": False,
        }
        raw_response_text: str | None = None
        try:
            http_request = request.Request(
                f"{self._base_url}/api/generate",
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with request.urlopen(http_request, timeout=self._timeout_seconds) as response:
                envelope_text = response.read().decode("utf-8")
            envelope = json.loads(envelope_text)
            raw_response_text = str(envelope.get("response", ""))
        except (error.HTTPError, error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
            return SemanticExtractionResponse(
                parsed_payload=None,
                raw_response=raw_response_text,
                metadata={
                    "backend_mode": self.mode_name,
                    "base_url": self._base_url,
                    "model": self._model,
                    "error": str(exc),
                },
                diagnostics={},
                status="unavailable",
            )

        try:
            parsed_payload = json.loads(raw_response_text or "")
        except json.JSONDecodeError:
            return SemanticExtractionResponse(
                parsed_payload=None,
                raw_response=raw_response_text,
                metadata={
                    "backend_mode": self.mode_name,
                    "base_url": self._base_url,
                    "model": self._model,
                },
                diagnostics={},
                status="invalid_json",
            )
        if not isinstance(parsed_payload, dict):
            return SemanticExtractionResponse(
                parsed_payload=None,
                raw_response=raw_response_text,
                metadata={
                    "backend_mode": self.mode_name,
                    "base_url": self._base_url,
                    "model": self._model,
                    "error": f"semantic extractor returned JSON {type(parsed_payload).__name__}; expected object",
                },
                diagnostics={},
                status="invalid_json",
            )

        normalized_payload, diagnostics = _normalize_raw_user_input(parsed_payload, str(packet.get("raw_user_input", "")))
        return SemanticExtractionResponse(
            parsed_payload=normalized_payload,
            raw_response=raw_response_text,
            metadata={
                "backend_mode": self.mode_name,
                "base_url": self._base_url,
                "model": self._model,
            },
            diagnostics=diagnostics,
            status="parsed",
        )


class UnavailableSemanticExtractorBackend:
    mode_name = "unavailable"

    def __init__(self, *, reason: str, configured_mode: str | None = None) -> None:
        self._reason = reason
        self._configured_mode = configured_mode

    def extract_isolated(self, packet: dict[str, Any]) -> SemanticExtractionResponse:
        return self._response()

    def extract_contextual(self, packet: dict[str, Any]) -> SemanticExtractionResponse:
        return self._response()

    def _response(self) -> SemanticExtractionResponse:
        metadata = {
            "backend_mode": self.mode_name,
            "reason": self._reason,
        }
        if self._configured_mode is not None:
            metadata["configured_mode"] = self._configured_mode
        return SemanticExtractionResponse(
            parsed_payload=None,
            raw_response=None,
            metadata=metadata,
            diagnostics={},
            status="unavailable",
        )


def resolve_semantic_extractor_backend(
    *,
    repo_root: Path,
    config: RuntimeConfig,
    extractor_mode: str | None = None,
    model_override: str | None = None,
    base_url_override: str | None = None,
    allow_test_backends: bool = False,
) -> SemanticExtractorBackend:
    configured_mode = extractor_mode.strip().lower() if isinstance(extractor_mode, str) and extractor_mode.strip() else None
    configured_provider = config.semantic_extraction_provider.strip().lower()
    configured_model = model_override or config.semantic_extraction_model
    configured_base_url = base_url_override or config.semantic_extraction_base_url
    timeout_seconds = config.semantic_extraction_request_timeout_seconds

    if configured_mode in {"disabled", "stub"}:
        if allow_test_backends:
            return DisabledSemanticExtractorBackend() if configured_mode == "disabled" else StubSemanticExtractorBackend()
        return UnavailableSemanticExtractorBackend(
            reason=f"{configured_mode} semantic extraction is test-only and not valid for the normal runtime",
            configured_mode=configured_mode,
        )
    if configured_mode and configured_mode != configured_provider:
        if configured_mode != "ollama":
            return UnavailableSemanticExtractorBackend(
                reason=f"unsupported semantic extractor mode: {configured_mode}",
                configured_mode=configured_mode,
            )
    if configured_provider == "ollama":
        if not isinstance(configured_model, str) or not configured_model.strip():
            return UnavailableSemanticExtractorBackend(
                reason="SEMANTIC_EXTRACTOR_MODEL is not configured for the normal runtime",
                configured_mode=configured_provider,
            )
        return OllamaSemanticExtractorBackend(
            model=configured_model,
            base_url=configured_base_url,
            timeout_seconds=timeout_seconds,
        )
    return UnavailableSemanticExtractorBackend(
        reason=f"unsupported semantic extraction provider: {configured_provider}",
        configured_mode=configured_provider,
    )
