from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Protocol
from urllib import error, request

from .config import RuntimeConfig
from .hashing import sha256_text
from .retrieval_plan import build_default_retrieval_plan, canonicalize_retrieval_plan, scope_requests_from_text


INTERNAL_COMPILER_ECHO_FIELDS = {"planner_diagnostics"}


COMPILER_TOKEN_RE = re.compile(r"[A-Za-z0-9']+")
IDENTIFIER_QUERY_RE = re.compile(r"^[a-z0-9]+(?:[_-][a-z0-9]+)*$")
INTENT_ONLY_TERMS = {
    "origin", "development", "precursor", "precursors", "history", "relation",
    "comparison", "compare", "graph", "temporal", "query",
}
COMPILER_STOP_WORDS = {
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
class SemanticCompilerResponse:
    parsed_payload: dict[str, Any] | None
    raw_response: str | None
    metadata: dict[str, Any]
    diagnostics: dict[str, Any]
    status: str


class SemanticCompilerBackend(Protocol):
    mode_name: str

    def compile_turn(self, packet: dict[str, Any]) -> SemanticCompilerResponse:
        ...


def collect_compiler_terms(text: str) -> list[str]:
    terms: list[str] = []
    seen: set[str] = set()
    for token in COMPILER_TOKEN_RE.findall(text.lower()):
        if len(token) < 3 or token in COMPILER_STOP_WORDS or token.isdigit():
            continue
        if token not in seen:
            seen.add(token)
            terms.append(token)
    return terms


def _clean_subject_values(value: Any) -> list[str]:
    values: list[str] = []
    if not isinstance(value, list):
        return values
    for item in value:
        if isinstance(item, dict):
            item = item.get("label") or item.get("resolved_to") or item.get("surface_form") or item.get("value")
        text = str(item or "").strip()
        if text and text not in values:
            values.append(text)
    return values


def _normalize_subject_for_comparison(value: str) -> str:
    return " ".join(str(value or "").casefold().split())


def _deduplicate_subject_candidates(values: list[str]) -> list[str]:
    candidates: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = " ".join(str(value or "").split())
        normalized = _normalize_subject_for_comparison(text)
        if text and normalized not in seen:
            seen.add(normalized)
            candidates.append(text)
    return candidates


def _normalized_phrase_contains(container: str, contained: str) -> bool:
    container_tokens = _normalize_subject_for_comparison(container).split()
    contained_tokens = _normalize_subject_for_comparison(contained).split()
    if not contained_tokens or len(contained_tokens) > len(container_tokens):
        return False
    return any(
        container_tokens[start : start + len(contained_tokens)] == contained_tokens
        for start in range(len(container_tokens) - len(contained_tokens) + 1)
    )


def _subject_values(*, planner_payload: Any, result: dict[str, Any], query: str, raw_user_input: str) -> tuple[list[str], str]:
    """Select subject-bearing fallback material without topic-specific rules."""
    if isinstance(planner_payload, dict):
        referents = _clean_subject_values(planner_payload.get("resolved_referents"))
        if referents:
            return _deduplicate_subject_candidates(referents), "planner_resolved_referents"
        concepts = _clean_subject_values(planner_payload.get("concepts"))
        if concepts:
            return _deduplicate_subject_candidates(concepts), "planner_concepts"
    for key, source in (("resolved_referents", "resolved_referents"), ("entities", "entities"), ("relations", "relations")):
        values = _clean_subject_values(result.get(key))
        if values:
            return _deduplicate_subject_candidates(values), source
    query_terms = collect_compiler_terms(query) if not _is_identifier_or_intent_only(query) else []
    if query_terms:
        return _deduplicate_subject_candidates(query_terms), "query_tokens"
    raw_terms = collect_compiler_terms(raw_user_input)
    return _deduplicate_subject_candidates(raw_terms), "raw_user_input_tokens"


def _is_identifier_or_intent_only(text: str) -> bool:
    cleaned = str(text or "").strip().lower()
    if not cleaned:
        return True
    if IDENTIFIER_QUERY_RE.fullmatch(cleaned):
        return True
    return cleaned in INTENT_ONLY_TERMS


def _minimal_subject_basis(subjects: list[str]) -> tuple[list[str], list[dict[str, str]]]:
    """Return the smallest deterministic basis without losing independent subjects."""
    basis: list[str] = []
    overlaps: list[dict[str, str]] = []
    for candidate in subjects:
        normalized = _normalize_subject_for_comparison(candidate)
        if not normalized:
            continue
        contained_by_existing = False
        replacements: list[tuple[int, str]] = []
        for index, existing in enumerate(basis):
            existing_normalized = _normalize_subject_for_comparison(existing)
            if normalized == existing_normalized:
                contained_by_existing = True
                break
            if len(normalized.split()) < len(existing_normalized.split()) and _normalized_phrase_contains(existing_normalized, normalized):
                replacements.append((index, existing))
                overlaps.append({"shorter": candidate, "contained_in": existing})
            elif len(existing_normalized.split()) < len(normalized.split()) and _normalized_phrase_contains(normalized, existing_normalized):
                contained_by_existing = True
                overlaps.append({"shorter": existing, "contained_in": candidate})
            if contained_by_existing:
                break
        if contained_by_existing:
            continue
        for index, _ in reversed(replacements):
            basis.pop(index)
        basis.append(candidate)
    return basis, overlaps


def _subject_text(subjects: list[str]) -> str:
    basis, _ = _minimal_subject_basis(subjects)
    return " ".join(basis[:6]).strip()


def _query_contains_subject(query: str, subjects: list[str]) -> bool:
    normalized_query = _normalize_subject_for_comparison(query)
    return bool(normalized_query) and any(
        _normalized_phrase_contains(normalized_query, subject)
        for subject in subjects
        if _normalize_subject_for_comparison(subject)
    )


def _graph_seed_is_meaningful(seed: str) -> bool:
    text = str(seed or "").strip()
    return bool(text) and not _is_identifier_or_intent_only(text)


def _canonical_query_text(candidate: str, subjects: list[str], raw_user_input: str) -> tuple[str, str]:
    subject = _subject_text(subjects)
    query = str(candidate or "").strip()
    if subject:
        if query and not _is_identifier_or_intent_only(query) and _query_contains_subject(query, subjects):
            return query, "model_query"
        if query and not _is_identifier_or_intent_only(query):
            return f"{query} regarding {subject}", "query_plus_subject"
        if query:
            natural_intent = query.replace("_", " ").replace("-", " ").strip()
            return f"{natural_intent} of {subject}", "subject_repaired_query"
        return f"development of {subject}", "subject_default_query"
    if query and not _is_identifier_or_intent_only(query):
        return query, "model_query"
    raw = str(raw_user_input or "").strip()
    return raw, "raw_user_input"


def _subject_bearing_fallback_plan(
    *,
    raw_user_input: str,
    query: str,
    subjects: list[str],
    scope_requests: list[str],
    resolved_referents: list[str],
    planner_defaults: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    natural_query, query_source = _canonical_query_text(query, subjects, raw_user_input)
    subject = _subject_text(subjects)
    fallback_concepts = list(dict.fromkeys(subjects or collect_compiler_terms(natural_query) or collect_compiler_terms(raw_user_input)))
    fallback_graph_seeds = [subject] if subject else []
    fallback = build_default_retrieval_plan(
        raw_user_input=raw_user_input,
        query=natural_query,
        concepts=fallback_concepts,
        scope_requests=scope_requests,
        graph_seeds=fallback_graph_seeds,
        resolved_referents=resolved_referents,
        planner_defaults=planner_defaults,
    )
    return fallback, {"query": query_source, "concepts": "subject_candidates" if subjects else "natural_query"}


def _subject_preservation_status(plan: dict[str, Any], subjects: list[str]) -> dict[str, Any]:
    def contains_subject(values: Any) -> bool:
        return any(_query_contains_subject(str(value), subjects) for value in values or [])
    semantic_ok = contains_subject(plan.get("semantic_queries")) if subjects else bool(plan.get("semantic_queries"))
    lexical_ok = contains_subject(plan.get("lexical_queries")) if subjects else bool(plan.get("lexical_queries"))
    graph_requested = any(isinstance(layer, dict) and layer.get("operator") == "graph_expand" for layer in plan.get("retrieval_layers", []))
    graph_ok = all(_graph_seed_is_meaningful(str(seed)) for seed in plan.get("graph_seeds", [])) if graph_requested else True
    return {
        "status": "subject_preserved" if semantic_ok and lexical_ok and graph_ok else "incomplete",
        "target_concepts_present": bool(subjects),
        "semantic_queries_subject_bearing": semantic_ok,
        "lexical_queries_subject_bearing": lexical_ok,
        "graph_seeds_subject_bearing": graph_ok,
        "graph_requested": graph_requested,
    }


def _repair_subject_bearing_queries(plan: dict[str, Any], subjects: list[str]) -> list[dict[str, Any]]:
    """Apply only structural repairs to non-empty intent-labelled query lists."""
    subject = _subject_text(subjects)
    if not subject:
        return []
    adjustments: list[dict[str, Any]] = []
    for field in ("semantic_queries", "lexical_queries"):
        values = plan.get(field)
        if not isinstance(values, list) or not values:
            continue
        repaired: list[str] = []
        for value in values:
            text = str(value).strip()
            if not text:
                continue
            if not _query_contains_subject(text, subjects):
                replacement = f"{text.replace('_', ' ').replace('-', ' ')} regarding {subject}"
                adjustments.append({"field": field, "requested": text, "effective": replacement, "action": "subject_preserved_structurally"})
                text = replacement
            if text not in repaired:
                repaired.append(text)
        plan[field] = repaired
    return adjustments


def _repair_graph_seeds(plan: dict[str, Any], subjects: list[str]) -> list[dict[str, Any]]:
    values = plan.get("graph_seeds")
    if not isinstance(values, list) or not values:
        return []
    subject = _subject_text(subjects)
    repaired: list[str] = []
    adjustments: list[dict[str, Any]] = []
    for value in values:
        text = str(value or "").strip()
        if not text:
            continue
        if _graph_seed_is_meaningful(text):
            repaired.append(text)
            adjustments.append({"field": "graph_seeds", "requested": text, "effective": text, "action": "preserved_graph_seed"})
            continue
        if subject:
            repaired.append(subject)
            adjustments.append({"field": "graph_seeds", "requested": text, "effective": subject, "action": "repaired_graph_seed"})
        else:
            adjustments.append({"field": "graph_seeds", "requested": text, "effective": None, "action": "rejected_graph_seed"})
    plan["graph_seeds"] = list(dict.fromkeys(repaired))
    return adjustments


def _deterministic_compiler_packet(raw_user_input: str, *, planner_defaults: dict[str, Any]) -> dict[str, Any]:
    query = raw_user_input.strip()
    concepts = collect_compiler_terms(raw_user_input)
    scope_requests = scope_requests_from_text(raw_user_input)
    graph_seeds = [query] if query else []
    packet = {
        "raw_user_input": raw_user_input,
        "intent": "deterministic semantic compiler packet",
        "query": query,
        "entities": [],
        "relations": [],
        "resolved_referents": [],
        "limitations": ["deterministic compiler packet used"],
    }
    packet["planner_retrieval_plan"] = build_default_retrieval_plan(
        raw_user_input=raw_user_input,
        query=query,
        concepts=concepts,
        scope_requests=scope_requests,
        graph_seeds=graph_seeds,
        resolved_referents=[],
        planner_defaults=planner_defaults,
    )
    packet["planner_diagnostics"] = {"ignored_planner_fields": []}
    return packet


def _is_referential_input(text: str) -> bool:
    lowered = text.lower()
    return any(f" {surface} " in f" {lowered} " for surface in ("it", "that", "this", "those", "they", "them"))


def _canonicalize_response_payload(raw_user_input: str, payload: dict[str, Any] | None, *, planner_defaults: dict[str, Any], packet: dict[str, Any] | None = None) -> dict[str, Any]:
    fallback = _deterministic_compiler_packet(raw_user_input, planner_defaults=planner_defaults)
    if not isinstance(payload, dict):
        return fallback
    result = dict(fallback)
    result["raw_user_input"] = raw_user_input
    result["intent"] = str(payload.get("intent") or result["intent"]).strip() or result["intent"]
    model_query = payload.get("query") if isinstance(payload.get("query"), str) else ""
    for key in ("entities", "relations", "resolved_referents"):
        value = payload.get(key)
        if isinstance(value, list):
            cleaned = []
            for item in value:
                if isinstance(item, dict):
                    candidate = item.get("label") or item.get("resolved_to") or item.get("surface_form") or item.get("value")
                else:
                    candidate = item
                text = str(candidate).strip()
                if text and text not in cleaned:
                    cleaned.append(text)
            result[key] = cleaned
    limitations_value = payload.get("limitations")
    if isinstance(limitations_value, list):
        cleaned_limitations = []
        for item in limitations_value:
            text = str(item).strip()
            if text and text not in cleaned_limitations:
                cleaned_limitations.append(text)
        result["limitations"] = cleaned_limitations
    else:
        result["limitations"] = []
    planner_payload = payload.get("planner_retrieval_plan")
    subjects, subject_source = _subject_values(
        planner_payload=planner_payload,
        result=result,
        query=model_query,
        raw_user_input=raw_user_input,
    )
    natural_query, query_source = _canonical_query_text(model_query, subjects, raw_user_input)
    result["query"] = natural_query
    if isinstance(planner_payload, dict) and isinstance(planner_payload.get("scope_requests"), list):
        fallback_scope_requests = [str(item).strip() for item in planner_payload["scope_requests"] if str(item).strip()]
    else:
        fallback_scope_requests = scope_requests_from_text(natural_query)
    fallback_plan, fallback_sources = _subject_bearing_fallback_plan(
        raw_user_input=raw_user_input,
        query=natural_query,
        subjects=subjects,
        scope_requests=fallback_scope_requests,
        resolved_referents=list(result["resolved_referents"]),
        planner_defaults=planner_defaults,
    )
    canonical_plan, planner_diagnostics = canonicalize_retrieval_plan(planner_payload, fallback=fallback_plan, planner_defaults=planner_defaults, raw_user_input=raw_user_input)
    subject_query_adjustments = _repair_subject_bearing_queries(canonical_plan, subjects)
    graph_seed_adjustments = _repair_graph_seeds(canonical_plan, subjects)
    minimal_subject_basis, overlapping_subject_candidates = _minimal_subject_basis(subjects)
    preserved_subject_bearing_fields = [
        field
        for field in ("semantic_queries", "lexical_queries")
        if any(_query_contains_subject(str(value), subjects) for value in canonical_plan.get(field, []))
    ]
    repaired_subjectless_fields = [item["field"] for item in subject_query_adjustments]
    result["planner_retrieval_plan"] = canonical_plan
    result["planner_diagnostics"] = {
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
        ),
        "retired_planner_fields": list(planner_diagnostics.get("retired_planner_fields") or []),
        "defaulted_missing_fields": list(planner_diagnostics.get("defaulted_missing_fields") or []),
        "invalid_planner_fields": list(planner_diagnostics.get("invalid_planner_fields") or []),
        "explicit_empty_fields": list(planner_diagnostics.get("explicit_empty_fields") or []),
        "fallback_query_sources": {
            **fallback_sources,
            "subject_candidates": subject_source,
            "canonical_query": query_source,
        },
        "subject_preservation_status": _subject_preservation_status(canonical_plan, subjects),
        "subject_query_adjustments": subject_query_adjustments,
        "subject_candidates": subjects,
        "minimal_subject_basis": minimal_subject_basis,
        "overlapping_subject_candidates": overlapping_subject_candidates,
        "preserved_subject_bearing_fields": list(dict.fromkeys(preserved_subject_bearing_fields)),
        "repaired_subjectless_fields": list(dict.fromkeys(repaired_subjectless_fields)),
        "preserved_graph_seeds": [item["requested"] for item in graph_seed_adjustments if item["action"] == "preserved_graph_seed"],
        "rejected_or_repaired_graph_seeds": [item for item in graph_seed_adjustments if item["action"] != "preserved_graph_seed"],
    }
    return result


def _render_ollama_prompt(*, packet: dict[str, Any], template: str, planner_defaults: dict[str, Any]) -> str:
    rendered_template = template
    replacements = {
        "{semantic_compiler_lexical_limit}": planner_defaults["lexical_limit"],
        "{semantic_compiler_vector_limit}": planner_defaults["vector_limit"],
        "{semantic_compiler_graph_depth}": planner_defaults["graph_depth"],
        "{resource_inventory_summary}": json.dumps(packet.get("resource_inventory_summary", {}), ensure_ascii=True, indent=2),
    }
    for marker, value in replacements.items():
        rendered_template = rendered_template.replace(marker, str(value))
    packet_json = json.dumps(packet, ensure_ascii=True, indent=2)
    return rendered_template.replace("{packet}", packet_json).strip()


class OllamaSemanticCompilerBackend:
    mode_name = "ollama"

    def __init__(self, *, model: str | None, base_url: str, timeout_seconds: int = 20, prompt_template: str, planner_defaults: dict[str, Any]) -> None:
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._prompt_template = prompt_template
        self._planner_defaults = planner_defaults

    def compile_turn(self, packet: dict[str, Any]) -> SemanticCompilerResponse:
        if not self._model:
            return SemanticCompilerResponse(
                parsed_payload=None,
                raw_response=None,
                metadata={"backend_mode": self.mode_name, "base_url": self._base_url, "reason": "model not configured"},
                diagnostics={},
                status="unavailable",
            )
        prompt = _render_ollama_prompt(packet=packet, template=self._prompt_template, planner_defaults=self._planner_defaults)
        prompt_hash = sha256_text(prompt)
        payload = {"model": self._model, "prompt": prompt, "stream": False}
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
            return SemanticCompilerResponse(
                parsed_payload=None,
                raw_response=raw_response_text,
                metadata={
                    "backend_mode": self.mode_name,
                    "base_url": self._base_url,
                    "model": self._model,
                    "semantic_compiler_prompt_hash": prompt_hash,
                    "error": str(exc),
                },
                diagnostics={},
                status="unavailable",
            )

        try:
            parsed_payload = json.loads(raw_response_text or "")
        except json.JSONDecodeError:
            return SemanticCompilerResponse(
                parsed_payload=None,
                raw_response=raw_response_text,
                metadata={
                    "backend_mode": self.mode_name,
                    "base_url": self._base_url,
                    "model": self._model,
                    "semantic_compiler_prompt_hash": prompt_hash,
                },
                diagnostics={},
                status="invalid_json",
            )
        if not isinstance(parsed_payload, dict):
            return SemanticCompilerResponse(
                parsed_payload=None,
                raw_response=raw_response_text,
                metadata={
                    "backend_mode": self.mode_name,
                    "base_url": self._base_url,
                    "model": self._model,
                    "semantic_compiler_prompt_hash": prompt_hash,
                    "error": f"expected object, got {type(parsed_payload).__name__}",
                },
                diagnostics={},
                status="invalid_json",
            )
        canonical_payload = _canonicalize_response_payload(str(packet.get("raw_user_input") or ""), parsed_payload, planner_defaults=self._planner_defaults, packet=packet)
        return SemanticCompilerResponse(
            parsed_payload=canonical_payload,
            raw_response=raw_response_text,
            metadata={
                "backend_mode": self.mode_name,
                "base_url": self._base_url,
                "model": self._model,
                "semantic_compiler_prompt_hash": prompt_hash,
            },
            diagnostics={},
            status="parsed",
        )


class UnavailableSemanticCompilerBackend:
    mode_name = "unavailable"

    def __init__(self, *, reason: str, configured_mode: str | None = None) -> None:
        self._reason = reason
        self._configured_mode = configured_mode

    def compile_turn(self, packet: dict[str, Any]) -> SemanticCompilerResponse:
        metadata = {"backend_mode": self.mode_name, "reason": self._reason}
        if self._configured_mode is not None:
            metadata["configured_mode"] = self._configured_mode
        return SemanticCompilerResponse(parsed_payload=None, raw_response=None, metadata=metadata, diagnostics={}, status="unavailable")


def resolve_semantic_compiler_backend(
    *,
    config: RuntimeConfig,
    model_override: str | None = None,
    base_url_override: str | None = None,
) -> SemanticCompilerBackend:
    configured_provider = config.semantic_compiler_provider.strip().lower()
    configured_model = model_override or config.semantic_compiler_model
    configured_base_url = base_url_override or config.semantic_compiler_base_url
    timeout_seconds = config.semantic_compiler_request_timeout_seconds

    if configured_provider == "ollama":
        if not isinstance(configured_base_url, str) or not configured_base_url.strip():
            return UnavailableSemanticCompilerBackend(reason="semantic compiler base_url is not configured", configured_mode="ollama")
        return OllamaSemanticCompilerBackend(
            model=configured_model,
            base_url=configured_base_url.strip(),
            timeout_seconds=timeout_seconds,
            prompt_template=config.semantic_compiler_prompt_template,
            planner_defaults=config.retrieval_planner_defaults,
        )
    return UnavailableSemanticCompilerBackend(reason=f"unsupported semantic compiler provider: {configured_provider}", configured_mode=configured_provider)
