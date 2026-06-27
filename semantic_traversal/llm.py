from __future__ import annotations

import json
import os
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from typing import Any, Protocol

from .config import RuntimeConfig


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
            "OpenAI SDK is not installed. Run `python -m pip install openai` to use "
            "`--llm-mode live`, or rerun with `--llm-mode stub`."
        ) from exc

    openai_client = getattr(openai_module, "OpenAI", None)
    if openai_client is None:
        raise LiveLLMNotConfigured(
            "OpenAI SDK import succeeded but `openai.OpenAI` is unavailable. "
            "Reinstall the `openai` package or rerun with `--llm-mode stub`."
        )
    return openai_client(api_key=api_key)


class StubLLMBackend:
    def __init__(self, prefix: str = "Stub assistant response") -> None:
        self._prefix = prefix

    def generate(self, synthesis_context_packet: dict[str, Any]) -> LLMResponse:
        user_input = synthesis_context_packet["user_input"]
        turn_id = synthesis_context_packet["turn_id"]
        text = f"{self._prefix} for turn {turn_id}: {user_input}"
        return LLMResponse(
            assistant_response=text,
            metadata={
                "mode": "stub",
                "provider": "local-stub",
                "model": "stub-echo",
            },
        )


class OpenAIResponsesBackend:
    def __init__(self, api_key: str, model: str, max_output_tokens: int) -> None:
        self._client = _build_openai_client(api_key=api_key)
        self._model = model
        self._max_output_tokens = max_output_tokens

    def generate(self, synthesis_context_packet: dict[str, Any]) -> LLMResponse:
        response = self._client.responses.create(
            model=self._model,
            instructions=(
                "You are a helpful assistant inside the semantic-traversal first build target. "
                "Respond directly to the user using the provided synthesis context packet only. "
                "Treat semantic extraction as non-authoritative additive context only. "
                "Do not invent retrieval results or graph operations. "
                "Preserve the raw user intent when semantic extraction is uncertain."
            ),
            input=json.dumps(synthesis_context_packet, ensure_ascii=True, indent=2),
            max_output_tokens=self._max_output_tokens,
            store=False,
        )
        assistant_text = (getattr(response, "output_text", "") or "").strip()
        if not assistant_text:
            assistant_text = "The model returned an empty response."
        usage = getattr(response, "usage", None)
        response_id = getattr(response, "id", None)
        usage_payload = usage.model_dump() if hasattr(usage, "model_dump") else None
        return LLMResponse(
            assistant_response=assistant_text,
            metadata={
                "mode": "live",
                "provider": "openai",
                "model": self._model,
                "response_id": response_id,
                "usage": usage_payload,
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
) -> tuple[str | None, str, int]:
    dotenv_values = load_dotenv_local(repo_root)
    api_key = os.environ.get("OPENAI_API_KEY") or dotenv_values.get("OPENAI_API_KEY")
    model = model_override or config.llm_model
    return api_key, model, config.llm_max_output_tokens


def resolve_llm_backend(
    repo_root: Path,
    config: RuntimeConfig,
    llm_mode: str,
    model_override: str | None = None,
    stub_prefix: str = "Stub assistant response",
) -> LLMBackend:
    if llm_mode == "stub":
        return StubLLMBackend(prefix=stub_prefix)

    api_key, model, max_output_tokens = resolve_openai_settings(
        repo_root=repo_root,
        config=config,
        model_override=model_override,
    )
    if not api_key:
        if llm_mode == "auto":
            return StubLLMBackend(prefix=stub_prefix)
        raise LiveLLMNotConfigured("OPENAI_API_KEY is not available for live execution.")

    if llm_mode == "auto":
        return OpenAIResponsesBackend(api_key=api_key, model=model, max_output_tokens=max_output_tokens)
    if llm_mode == "live":
        return OpenAIResponsesBackend(api_key=api_key, model=model, max_output_tokens=max_output_tokens)
    raise ValueError(f"Unsupported llm_mode: {llm_mode}")
