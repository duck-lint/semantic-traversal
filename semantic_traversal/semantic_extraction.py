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
        return {
            "raw_user_input": raw_user_input,
            "contextual_user_intent": "stub contextual hydration of the isolated semantic extraction",
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
                "must_preserve": preserved_terms,
                "should_include": list(isolated_payload.get("candidate_targets") or terms[:2]),
                "avoid_satisfying_with": [],
                "query_text": raw_user_input,
                "allow_no_retrieval_needed": False,
            },
            "thread_relevant_context": recent_trajectory,
            "semantic_pressure": None,
            "candidate_targets": list(isolated_payload.get("candidate_targets") or terms[:3]),
            "candidate_relations": list(isolated_payload.get("candidate_relations") or []),
            "activation_hints": {
                "lexical_terms": terms[:4],
                "phrases": [],
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
        prompt = (
            "Return JSON only.\n"
            f"{packet.get('instruction', '')}\n"
            "Do not answer the user.\n"
            "Preserve the raw_user_input field exactly.\n"
            "For contextual mode, produce these required fields: raw_user_input, perturbation_nodes, contextual_salt_nodes, "
            "perturbation_semantic_graph, semantic_coverage_target, activation_hints, limitations.\n"
            "Packet:\n"
            f"{json.dumps(packet, ensure_ascii=True, indent=2)}"
        )
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
