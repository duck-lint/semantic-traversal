"""Narrow OpenAI Responses transport for router classification."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Sequence

import openai

from .config import RuntimeConfig
from .conversation import Message


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


class OpenAIResponsesProvider:
    """The only provider adapter implemented by this runtime seam."""

    def infer(
        self,
        config: RuntimeConfig,
        prompt: str,
        messages: Sequence[Message],
    ) -> ProviderInference:
        request_messages = [
            {
                "role": "user" if message.role == "user" else "assistant",
                "content": message.content,
            }
            for message in messages
        ]
        try:
            client = openai.OpenAI(
                max_retries=0,
                timeout=config.router.timeout_seconds,
            )
            response = client.responses.create(
                model=config.router.model,
                instructions=prompt,
                input=request_messages,
                store=False,
                truncation="disabled",
                text={
                    "format": {
                        "type": "json_schema",
                        "name": "router_result",
                        "strict": True,
                        "schema": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["route"],
                            "properties": {
                                "route": {
                                    "type": "string",
                                    "enum": ["direct", "semantic_retrieval"],
                                }
                            },
                        },
                    }
                },
            )
        except openai.OpenAIError as exc:
            raise OpenAIProviderError(_classify_provider_error(exc), _safe_message(exc)) from exc
        except Exception as exc:
            raise OpenAIProviderError("provider_status", _safe_message(exc)) from exc

        raw_output = getattr(response, "output_text", None)
        if not isinstance(raw_output, str):
            raise OpenAIProviderError("structured_output", "provider returned no structured router output")
        try:
            parsed = json.loads(raw_output)
        except json.JSONDecodeError as exc:
            raise OpenAIProviderError("structured_output", "provider returned invalid router JSON") from exc
        if not isinstance(parsed, dict) or set(parsed) != {"route"} or parsed.get("route") not in {"direct", "semantic_retrieval"}:
            raise OpenAIProviderError("runtime_validation", "provider returned an invalid router result")
        output_json = json.dumps({"route": parsed["route"]}, ensure_ascii=False, separators=(",", ":"))
        response_id = getattr(response, "id", None)
        return ProviderInference(
            route=parsed["route"],
            output_json=output_json,
            provider_response_id=response_id if isinstance(response_id, str) else None,
            usage=_usage(response),
        )


__all__ = ["OpenAIProviderError", "OpenAIResponsesProvider", "ProviderInference", "ProviderUsage"]
