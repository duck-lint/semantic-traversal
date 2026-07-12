from __future__ import annotations

import json
import os
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from typing import Any, Protocol

from .config import RuntimeConfig
from .hashing import sha256_text


class LLMBackend(Protocol):
    def generate(self, synthesis_context_packet: dict[str, Any]) -> "LLMResponse":
        ...


@dataclass(frozen=True)
class LLMResponse:
    assistant_response: str
    metadata: dict[str, Any]


class LiveLLMNotConfigured(RuntimeError):
    pass


def _build_openai_client(api_key: str) -> Any:
    try:
        openai_module = import_module("openai")
    except ModuleNotFoundError as exc:
        raise LiveLLMNotConfigured(
            "OpenAI SDK is not installed. Run `python -m pip install openai` and configure "
            "OPENAI_API_KEY before using live execution."
        ) from exc

    openai_client = getattr(openai_module, "OpenAI", None)
    if openai_client is None:
        raise LiveLLMNotConfigured(
            "OpenAI SDK import succeeded but `openai.OpenAI` is unavailable. "
            "Reinstall the `openai` package before using live execution."
        )
    return openai_client(api_key=api_key)


class UnavailableLLMBackend:
    mode_name = "unavailable"

    def __init__(self, *, reason: str) -> None:
        self.unavailable_reason = reason

    def generate(self, synthesis_context_packet: dict[str, Any]) -> LLMResponse:
        raise LiveLLMNotConfigured(self.unavailable_reason)


class OpenAIResponsesBackend:
    def __init__(
        self,
        api_key: str,
        model: str,
        reasoning_effort: str,
        max_output_tokens: int,
        instructions: str,
        prompt_cache_enabled: bool,
        prompt_cache_key: str,
        prompt_cache_retention: str,
    ) -> None:
        self._client = _build_openai_client(api_key=api_key)
        self._model = model
        self._reasoning_effort = reasoning_effort
        self._max_output_tokens = max_output_tokens
        self._instructions = instructions.strip()
        self._instructions_hash = sha256_text(self._instructions)
        self._prompt_cache_enabled = prompt_cache_enabled
        self._prompt_cache_key = prompt_cache_key.strip()
        self._prompt_cache_retention = prompt_cache_retention.strip()

    def describe_call(self) -> dict[str, Any]:
        """Return configured call identity when no provider response is available."""
        return {
            "mode": "live",
            "provider": "openai",
            "model": self._model,
            "reasoning_effort": self._reasoning_effort,
            "prompt_cache_enabled": getattr(self, "_prompt_cache_enabled", False),
            "prompt_cache_key": getattr(self, "_prompt_cache_key", "") or None,
            "prompt_cache_retention": getattr(self, "_prompt_cache_retention", "") or None,
        }

    def generate(self, synthesis_context_packet: dict[str, Any]) -> LLMResponse:
        request: dict[str, Any] = {
            "model": self._model,
            "instructions": self._instructions,
            "input": json.dumps(synthesis_context_packet, ensure_ascii=True, indent=2),
            "reasoning": {"effort": self._reasoning_effort},
            "max_output_tokens": self._max_output_tokens,
            "store": False,
        }
        # Prompt caching is opt-in at the request boundary. Keeping the cache
        # key explicit makes cache invalidation a deliberate control surface
        # when instructions or the synthesis packet contract changes.
        prompt_cache_enabled = getattr(self, "_prompt_cache_enabled", False)
        prompt_cache_key = getattr(self, "_prompt_cache_key", "")
        prompt_cache_retention = getattr(self, "_prompt_cache_retention", "")
        if prompt_cache_enabled:
            if prompt_cache_key:
                request["prompt_cache_key"] = prompt_cache_key
            if prompt_cache_retention:
                request["prompt_cache_retention"] = prompt_cache_retention
        response = self._client.responses.create(**request)
        assistant_text = (getattr(response, "output_text", "") or "").strip()
        if not assistant_text:
            assistant_text = "The model returned an empty response."
        usage = getattr(response, "usage", None)
        response_id = getattr(response, "id", None)
        usage_payload = usage.model_dump() if hasattr(usage, "model_dump") else None
        return LLMResponse(
            assistant_response=assistant_text,
            metadata={
                **self.describe_call(),
                "response_id": response_id,
                "usage": usage_payload,
                "frontier_synthesis_prompt_hash": self._instructions_hash,
            },
        )


def load_dotenv_local(repo_root: Path) -> dict[str, str]:
    dotenv_path = repo_root / ".env.local"
    if not dotenv_path.exists():
        return {}
    values: dict[str, str] = {}
    for raw_line in dotenv_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip("\"'")
    return values


def resolve_openai_settings(
    repo_root: Path,
    config: RuntimeConfig,
    model_override: str | None = None,
) -> tuple[str | None, str, str, int, bool, str, str]:
    dotenv_values = load_dotenv_local(repo_root)
    api_key = os.environ.get("OPENAI_API_KEY") or dotenv_values.get("OPENAI_API_KEY")
    model = model_override or config.llm_model
    return (
        api_key,
        model,
        config.llm_reasoning_effort,
        config.llm_max_output_tokens,
        config.llm_prompt_cache_enabled,
        config.llm_prompt_cache_key,
        config.llm_prompt_cache_retention,
    )


def resolve_llm_backend(
    repo_root: Path,
    config: RuntimeConfig,
    llm_mode: str,
    model_override: str | None = None,
) -> LLMBackend:
    api_key, model, reasoning_effort, max_output_tokens, prompt_cache_enabled, prompt_cache_key, prompt_cache_retention = resolve_openai_settings(
        repo_root=repo_root,
        config=config,
        model_override=model_override,
    )
    if not api_key:
        if llm_mode == "auto":
            return UnavailableLLMBackend(reason="OPENAI_API_KEY is not available for live execution.")
        raise LiveLLMNotConfigured("OPENAI_API_KEY is not available for live execution.")

    instructions = config.frontier_synthesis_instructions
    if llm_mode == "auto":
        return OpenAIResponsesBackend(
            api_key=api_key,
            model=model,
            reasoning_effort=reasoning_effort,
            max_output_tokens=max_output_tokens,
            instructions=instructions,
            prompt_cache_enabled=prompt_cache_enabled,
            prompt_cache_key=prompt_cache_key,
            prompt_cache_retention=prompt_cache_retention,
        )
    if llm_mode == "live":
        return OpenAIResponsesBackend(
            api_key=api_key,
            model=model,
            reasoning_effort=reasoning_effort,
            max_output_tokens=max_output_tokens,
            instructions=instructions,
            prompt_cache_enabled=prompt_cache_enabled,
            prompt_cache_key=prompt_cache_key,
            prompt_cache_retention=prompt_cache_retention,
        )
    raise ValueError(f"Unsupported llm_mode: {llm_mode}")
