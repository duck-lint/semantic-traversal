"""Narrow OpenAI Responses transport for runtime model inference."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Sequence

import openai

from .config import ModelConfig
from .conversation import Message
from .retrieval.requests import (
    RetrievalRequestError,
    canonicalize_retrieval_requests,
    retrieval_proposal_schema,
)


class OpenAIProviderError(RuntimeError):
    """A provider attempt failed with a narrow operational classification."""

    def __init__(self, error_type: str, message: str):
        super().__init__(message)
        self.error_type = error_type


@dataclass(frozen=True)
class ProviderUsage:
    input_tokens: int | None = None
    cached_input_tokens: int | None = None
    output_tokens: int | None = None
    reasoning_tokens: int | None = None
    total_tokens: int | None = None


@dataclass(frozen=True)
class ProviderInference:
    route: str
    output_json: str
    provider_response_id: str | None
    usage: ProviderUsage


@dataclass(frozen=True)
class RetrievalProviderInference:
    requests: tuple[dict[str, Any], ...]
    output_json: str
    provider_response_id: str | None
    usage: ProviderUsage


def _safe_message(value: object) -> str:
    message = str(value)
    secret = os.environ.get("OPENAI_API_KEY")
    if secret:
        message = message.replace(secret, "<redacted>")
    return message


def _classify_provider_error(error: Exception) -> str:
    if isinstance(error, openai.APITimeoutError):
        return "timeout"
    if isinstance(error, openai.APIConnectionError):
        return "connection"
    if isinstance(error, openai.AuthenticationError):
        return "authentication"
    if isinstance(error, openai.RateLimitError):
        return "rate_limit"
    if isinstance(error, openai.BadRequestError):
        return "bad_request"
    if isinstance(error, openai.APIStatusError):
        return "provider_status"
    return "provider_status"


def _nested_int(value: Any, attribute: str) -> int | None:
    raw = getattr(value, attribute, None) if value is not None else None
    return raw if isinstance(raw, int) and not isinstance(raw, bool) else None


def _usage(response: Any) -> ProviderUsage:
    usage = getattr(response, "usage", None)
    input_details = getattr(usage, "input_tokens_details", None)
    output_details = getattr(usage, "output_tokens_details", None)
    return ProviderUsage(
        input_tokens=_nested_int(usage, "input_tokens"),
        cached_input_tokens=_nested_int(input_details, "cached_tokens"),
        output_tokens=_nested_int(usage, "output_tokens"),
        reasoning_tokens=_nested_int(output_details, "reasoning_tokens"),
        total_tokens=_nested_int(usage, "total_tokens"),
    )


def _request_messages(messages: Sequence[Message]) -> list[dict[str, str]]:
    result = []
    for message in messages:
        if message.role == "user":
            provider_role = "user"
        elif message.role == "synthesis":
            provider_role = "assistant"
        else:
            raise OpenAIProviderError("runtime_validation", f"unsupported persisted message role: {message.role!r}")
        result.append({"role": provider_role, "content": message.content})
    return result


def _response_text(response: Any, kind: str) -> str:
    raw_output = getattr(response, "output_text", None)
    if not isinstance(raw_output, str):
        raise OpenAIProviderError("structured_output", f"provider returned no structured {kind} output")
    return raw_output


def _model_request(
    model_config: ModelConfig,
    input_messages: list[dict[str, str]],
    schema_name: str,
    schema: dict[str, Any],
) -> Any:
    try:
        client = openai.OpenAI(max_retries=0, timeout=model_config.timeout_seconds)
        return client.responses.create(
            model=model_config.model,
            instructions=model_config.prompt,
            input=input_messages,
            store=False,
            truncation="disabled",
            text={"format": {"type": "json_schema", "name": schema_name, "strict": True, "schema": schema}},
        )
    except openai.OpenAIError as exc:
        raise OpenAIProviderError(_classify_provider_error(exc), _safe_message(exc)) from exc
    except Exception as exc:
        raise OpenAIProviderError("provider_status", _safe_message(exc)) from exc


def _router_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["route"],
        "properties": {
            "route": {"type": "string", "enum": ["direct", "semantic_retrieval"]}
        },
    }


class OpenAIResponsesProvider:
    """The only provider adapter implemented by this runtime seam."""

    def infer_router(self, model_config: ModelConfig, messages: Sequence[Message]) -> ProviderInference:
        response = _model_request(model_config, _request_messages(messages), "router_result", _router_schema())
        raw_output = _response_text(response, "router")
        try:
            parsed = json.loads(raw_output)
        except json.JSONDecodeError as exc:
            raise OpenAIProviderError("structured_output", "provider returned invalid router JSON") from exc
        if not isinstance(parsed, dict) or set(parsed) != {"route"} or parsed.get("route") not in {"direct", "semantic_retrieval"}:
            raise OpenAIProviderError("runtime_validation", "provider returned an invalid router result")
        output_json = json.dumps({"route": parsed["route"]}, ensure_ascii=False, separators=(",", ":"))
        response_id = getattr(response, "id", None)
        return ProviderInference(parsed["route"], output_json, response_id if isinstance(response_id, str) else None, _usage(response))

    def infer_retrieval(
        self,
        model_config: ModelConfig,
        catalog_text: str,
        messages: Sequence[Message],
    ) -> RetrievalProviderInference:
        if not isinstance(catalog_text, str):
            raise OpenAIProviderError("runtime_validation", "catalog content must be text")
        input_messages = [{"role": "user", "content": catalog_text}, *_request_messages(messages)]
        response = _model_request(model_config, input_messages, "retrieval_proposal", retrieval_proposal_schema())
        raw_output = _response_text(response, "retrieval")
        try:
            parsed = json.loads(raw_output)
        except json.JSONDecodeError as exc:
            raise OpenAIProviderError("structured_output", "provider returned invalid retrieval JSON") from exc
        if not isinstance(parsed, dict) or set(parsed) != {"requests"}:
            raise OpenAIProviderError("runtime_validation", "provider returned an invalid retrieval proposal")
        try:
            requests = canonicalize_retrieval_requests(parsed["requests"])
        except RetrievalRequestError as exc:
            raise OpenAIProviderError("runtime_validation", str(exc)) from exc
        output_json = json.dumps({"requests": list(requests)}, ensure_ascii=False, separators=(",", ":"))
        response_id = getattr(response, "id", None)
        return RetrievalProviderInference(requests, output_json, response_id if isinstance(response_id, str) else None, _usage(response))


__all__ = [
    "OpenAIProviderError",
    "OpenAIResponsesProvider",
    "ProviderInference",
    "ProviderUsage",
    "RetrievalProviderInference",
]
