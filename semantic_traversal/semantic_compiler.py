from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Protocol
from urllib import error, request

from .config import RuntimeConfig
from .hashing import sha256_text
from .retrieval_plan import (
    build_default_retrieval_plan,
    canonicalize_retrieval_plan,
    scope_requests_from_text,
)


COMPILER_TOKEN_RE = re.compile(r"[A-Za-z0-9']+")
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


def _deterministic_compiler_packet(raw_user_input: str) -> dict[str, Any]:
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
    )
    packet["planner_diagnostics"] = {"ignored_planner_fields": []}
    return packet


def _active_focus_terms(packet: dict[str, Any]) -> list[str]:
    active_focus = packet.get("active_focus")
    terms: list[str] = []
    if isinstance(active_focus, dict):
        for value in (
            active_focus.get("query"),
            active_focus.get("concepts"),
            active_focus.get("scope_requests"),
            active_focus.get("resolved_referents"),
            active_focus.get("literal_terms"),
            active_focus.get("semantic_queries"),
            active_focus.get("lexical_queries"),
            active_focus.get("graph_seeds"),
            active_focus.get("selected_note_titles"),
            active_focus.get("selected_section_labels"),
        ):
            if isinstance(value, list):
                candidates = value
            else:
                candidates = [value]
            for candidate in candidates:
                if candidate is None:
                    continue
                for term in collect_compiler_terms(str(candidate)) if isinstance(candidate, str) else []:
                    if term not in terms:
                        terms.append(term)
                if isinstance(candidate, str):
                    cleaned = candidate.strip()
                    if cleaned and cleaned not in terms:
                        terms.append(cleaned)
    return terms


def _is_referential_input(text: str) -> bool:
    lowered = text.lower()
    return any(f" {surface} " in f" {lowered} " for surface in ("it", "that", "this", "those", "they", "them"))


def _canonicalize_response_payload(raw_user_input: str, payload: dict[str, Any] | None, *, packet: dict[str, Any] | None = None) -> dict[str, Any]:
    fallback = _deterministic_compiler_packet(raw_user_input)
    if not isinstance(payload, dict):
        return fallback
    result = dict(fallback)
    result["raw_user_input"] = raw_user_input
    result["intent"] = str(payload.get("intent") or result["intent"]).strip() or result["intent"]
    result["query"] = str(payload.get("query") or result["query"]).strip() or result["query"]
    for key in ("entities", "relations", "resolved_referents", "limitations"):
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
            if cleaned:
                result[key] = cleaned
    focus_terms = _active_focus_terms(packet or {})
    if _is_referential_input(raw_user_input) and focus_terms:
        result["resolved_referents"] = list(dict.fromkeys([*result["resolved_referents"], *focus_terms]))
    fallback_plan = build_default_retrieval_plan(
        raw_user_input=raw_user_input,
        query=result["query"],
        concepts=collect_compiler_terms(result["query"]),
        scope_requests=scope_requests_from_text(result["query"]),
        graph_seeds=[result["query"]] if result["query"].strip() else [],
        resolved_referents=list(result["resolved_referents"]),
    )
    canonical_plan, planner_diagnostics = canonicalize_retrieval_plan(payload.get("planner_retrieval_plan"), fallback=fallback_plan)
    if _is_referential_input(raw_user_input) and focus_terms:
        canonical_plan["resolved_referents"] = list(dict.fromkeys([*canonical_plan.get("resolved_referents", []), *focus_terms]))
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
            }
        )
    }
    return result


def _render_ollama_prompt(*, packet: dict[str, Any], template: str) -> str:
    packet_json = json.dumps(packet, ensure_ascii=True, indent=2)
    return template.replace("{packet}", packet_json).strip()


class OllamaSemanticCompilerBackend:
    mode_name = "ollama"

    def __init__(self, *, model: str | None, base_url: str, timeout_seconds: int = 20, prompt_template: str) -> None:
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._prompt_template = prompt_template

    def compile_turn(self, packet: dict[str, Any]) -> SemanticCompilerResponse:
        if not self._model:
            return SemanticCompilerResponse(
                parsed_payload=None,
                raw_response=None,
                metadata={"backend_mode": self.mode_name, "base_url": self._base_url, "reason": "model not configured"},
                diagnostics={},
                status="unavailable",
            )
        prompt = _render_ollama_prompt(packet=packet, template=self._prompt_template)
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
        canonical_payload = _canonicalize_response_payload(str(packet.get("raw_user_input") or ""), parsed_payload, packet=packet)
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
        )
    return UnavailableSemanticCompilerBackend(reason=f"unsupported semantic compiler provider: {configured_provider}", configured_mode=configured_provider)
