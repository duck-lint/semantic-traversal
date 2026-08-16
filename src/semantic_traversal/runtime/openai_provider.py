"""Narrow OpenAI Responses transport for runtime model inference."""

from __future__ import annotations

import datetime as dt

import json
import math
import os
from dataclasses import dataclass
from typing import Any, Sequence

import openai

from .config import ModelConfig, RuntimeConfig
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


def _model_request(config: ModelConfig, prompt: str, input_messages: list[dict[str, str]], schema_name: str, schema: dict[str, Any]) -> Any:
    try:
        client = openai.OpenAI(max_retries=0, timeout=config.timeout_seconds)
        return client.responses.create(
            model=config.model,
            instructions=prompt,
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
        "type": "object", "additionalProperties": False, "required": ["route"],
        "properties": {"route": {"type": "string", "enum": ["direct", "semantic_retrieval"]}},
    }


def _string_property() -> dict[str, str]:
    return {"type": "string"}


def _retrieval_schema() -> dict[str, Any]:
    def common(operator: str, fields: dict[str, Any], required: list[str]) -> dict[str, Any]:
        properties = {"operator": {"type": "string", "enum": [operator]}, **fields}
        return {"type": "object", "additionalProperties": False, "required": ["operator", *required], "properties": properties}

    scalar_values = {"type": ["string", "integer", "number", "boolean", "null"]}
    scalar = {
        "type": "object", "additionalProperties": False,
        "required": ["shape", "domain", "value"],
        "properties": {
            "shape": {"type": "string", "enum": ["scalar"]},
            "domain": {"type": "string", "enum": ["string", "integer", "float", "boolean", "null", "date", "datetime"]},
            "value": scalar_values,
        },
    }
    ordered = {
        "type": "object", "additionalProperties": False,
        "required": ["shape", "member_domain", "value"],
        "properties": {
            "shape": {"type": "string", "enum": ["ordered_sequence"]}, "member_domain": {"type": "string", "enum": ["string"]},
            "value": {"type": "array", "items": {"type": "string"}},
        },
    }
    exact = common("exact.equals", {
        "field_class": _string_property(), "field_name": _string_property(),
        "target": {"type": "string", "enum": ["complete_value", "member"]}, "operand": {"anyOf": [scalar, ordered]},
    }, ["field_class", "field_name", "target", "operand"])
    terms = common("lexical.terms", {
        "field_class": _string_property(), "field_name": _string_property(),
        "target": {"type": "string", "enum": ["complete_value", "member"]},
        "operand": {"type": "array", "items": {"type": "string"}, "minItems": 1},
    }, ["field_class", "field_name", "target", "operand"])
    phrase = common("lexical.phrase", {
        "field_class": _string_property(), "field_name": _string_property(),
        "target": {"type": "string", "enum": ["complete_value", "member"]}, "operand": _string_property(),
    }, ["field_class", "field_name", "target", "operand"])
    vector = common("vector.semantic_similarity", {"query": _string_property()}, ["query"])
    graph_terms = common("graph.discovery.terms", {
        "node_kind": _string_property(), "dimension_name": _string_property(),
        "operand": {"type": "array", "items": {"type": "string"}, "minItems": 1},
    }, ["node_kind", "dimension_name", "operand"])
    graph_phrase = common("graph.discovery.phrase", {
        "node_kind": _string_property(), "dimension_name": _string_property(), "operand": _string_property(),
    }, ["node_kind", "dimension_name", "operand"])
    relation = common("graph.relation_occurrence_lookup", {
        "relation_class": _string_property(), "relation_name": _string_property(),
    }, ["relation_class", "relation_name"])
    return {"type": "object", "additionalProperties": False, "required": ["requests"], "properties": {
        "requests": {"type": "array", "items": {"anyOf": [exact, terms, phrase, vector, graph_terms, graph_phrase, relation]}},
    }}


def _nonblank(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise OpenAIProviderError("runtime_validation", f"retrieval {name} must be nonblank text")
    return value


def _validate_scalar_operand(operand: Any) -> dict[str, Any]:
    if not isinstance(operand, dict) or set(operand) != {"shape", "domain", "value"} or operand.get("shape") != "scalar":
        raise OpenAIProviderError("runtime_validation", "invalid exact scalar operand")
    domain = operand.get("domain")
    value = operand.get("value")
    if domain not in {"string", "integer", "float", "boolean", "null", "date", "datetime"}:
        raise OpenAIProviderError("runtime_validation", "invalid exact scalar domain")
    if domain in {"string", "date", "datetime"} and not isinstance(value, str):
        raise OpenAIProviderError("runtime_validation", "exact text domain requires a string value")
    if domain == "integer" and (not isinstance(value, int) or isinstance(value, bool)):
        raise OpenAIProviderError("runtime_validation", "exact integer domain requires an integer value")
    if domain == "float" and (not isinstance(value, float) or not math.isfinite(value)):
        raise OpenAIProviderError("runtime_validation", "exact float domain requires a finite number")
    if domain == "boolean" and not isinstance(value, bool):
        raise OpenAIProviderError("runtime_validation", "exact boolean domain requires a boolean value")
    if domain == "date":
        try:
            dt.date.fromisoformat(value)
        except ValueError as exc:
            raise OpenAIProviderError("runtime_validation", "exact date domain requires an ISO date") from exc
    if domain == "datetime":
        try:
            dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise OpenAIProviderError("runtime_validation", "exact datetime domain requires an ISO datetime") from exc
    if domain == "null" and value is not None:
        raise OpenAIProviderError("runtime_validation", "exact null domain requires null")
    return {"shape": "scalar", "domain": domain, "value": value}


def _canonical_request(request: Any) -> dict[str, Any]:
    if not isinstance(request, dict) or "operator" not in request:
        raise OpenAIProviderError("runtime_validation", "retrieval request must be an object with an operator")
    operator = request["operator"]
    if operator == "exact.equals":
        if set(request) != {"operator", "field_class", "field_name", "target", "operand"}:
            raise OpenAIProviderError("runtime_validation", "invalid exact retrieval request shape")
        field_class = _nonblank(request["field_class"], "field_class")
        field_name = _nonblank(request["field_name"], "field_name")
        target = request["target"]
        if target not in {"complete_value", "member"}:
            raise OpenAIProviderError("runtime_validation", "invalid exact retrieval target")
        operand = request["operand"]
        if isinstance(operand, dict) and operand.get("shape") == "ordered_sequence":
            if set(operand) != {"shape", "member_domain", "value"} or operand["member_domain"] != "string" or not isinstance(operand["value"], list) or not all(isinstance(item, str) for item in operand["value"]):
                raise OpenAIProviderError("runtime_validation", "invalid ordered exact operand")
            canonical_operand = {"shape": "ordered_sequence", "member_domain": "string", "value": list(operand["value"])}
        else:
            canonical_operand = _validate_scalar_operand(operand)
        return {"operator": operator, "field_class": field_class, "field_name": field_name, "target": target, "operand": canonical_operand}
    if operator in {"lexical.terms", "lexical.phrase"}:
        if set(request) != {"operator", "field_class", "field_name", "target", "operand"}:
            raise OpenAIProviderError("runtime_validation", "invalid lexical retrieval request shape")
        field_class = _nonblank(request["field_class"], "field_class")
        field_name = _nonblank(request["field_name"], "field_name")
        if request["target"] not in {"complete_value", "member"}:
            raise OpenAIProviderError("runtime_validation", "invalid lexical retrieval target")
        operand = request["operand"]
        if operator == "lexical.terms":
            if not isinstance(operand, list) or not operand or not all(isinstance(item, str) and item.strip() for item in operand):
                raise OpenAIProviderError("runtime_validation", "lexical terms operand must be a nonempty string array")
        else:
            _nonblank(operand, "phrase operand")
        return {"operator": operator, "field_class": field_class, "field_name": field_name, "target": request["target"], "operand": operand}
    if operator == "vector.semantic_similarity":
        if set(request) != {"operator", "query"}:
            raise OpenAIProviderError("runtime_validation", "invalid vector retrieval request shape")
        return {"operator": operator, "query": _nonblank(request["query"], "query")}
    if operator in {"graph.discovery.terms", "graph.discovery.phrase"}:
        if set(request) != {"operator", "node_kind", "dimension_name", "operand"}:
            raise OpenAIProviderError("runtime_validation", "invalid graph discovery request shape")
        node_kind = _nonblank(request["node_kind"], "node_kind")
        dimension_name = _nonblank(request["dimension_name"], "dimension_name")
        operand = request["operand"]
        if operator.endswith("terms"):
            if not isinstance(operand, list) or not operand or not all(isinstance(item, str) and item.strip() for item in operand):
                raise OpenAIProviderError("runtime_validation", "graph terms operand must be a nonempty string array")
        else:
            _nonblank(operand, "phrase operand")
        return {"operator": operator, "node_kind": node_kind, "dimension_name": dimension_name, "operand": operand}
    if operator == "graph.relation_occurrence_lookup":
        if set(request) != {"operator", "relation_class", "relation_name"}:
            raise OpenAIProviderError("runtime_validation", "invalid graph relation request shape")
        return {"operator": operator, "relation_class": _nonblank(request["relation_class"], "relation_class"), "relation_name": _nonblank(request["relation_name"], "relation_name")}
    raise OpenAIProviderError("runtime_validation", f"unsupported retrieval operator: {operator!r}")


class OpenAIResponsesProvider:
    """The only provider adapter implemented by this runtime seam."""

    def infer(self, config: RuntimeConfig, prompt: str, messages: Sequence[Message]) -> ProviderInference:
        response = _model_request(config.router, prompt, _request_messages(messages), "router_result", _router_schema())
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

    def infer_retrieval(self, config: RuntimeConfig, catalog_text: str, messages: Sequence[Message]) -> RetrievalProviderInference:
        if not isinstance(catalog_text, str):
            raise OpenAIProviderError("runtime_validation", "catalog content must be text")
        input_messages = [{"role": "user", "content": catalog_text}, *_request_messages(messages)]
        response = _model_request(config.retrieval_inference, config.retrieval_inference.prompt, input_messages, "retrieval_proposal", _retrieval_schema())
        raw_output = _response_text(response, "retrieval")
        try:
            parsed = json.loads(raw_output)
        except json.JSONDecodeError as exc:
            raise OpenAIProviderError("structured_output", "provider returned invalid retrieval JSON") from exc
        if not isinstance(parsed, dict) or set(parsed) != {"requests"} or not isinstance(parsed["requests"], list):
            raise OpenAIProviderError("runtime_validation", "provider returned an invalid retrieval proposal")
        requests = tuple(_canonical_request(request) for request in parsed["requests"])
        output_json = json.dumps({"requests": list(requests)}, ensure_ascii=False, separators=(",", ":"))
        response_id = getattr(response, "id", None)
        return RetrievalProviderInference(requests, output_json, response_id if isinstance(response_id, str) else None, _usage(response))


__all__ = [
    "OpenAIProviderError", "OpenAIResponsesProvider", "ProviderInference", "ProviderUsage",
    "RetrievalProviderInference",
]
