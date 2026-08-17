"""Structural first-stage retrieval-request grammar.

This contract deliberately stops before catalog authorization. It answers only
whether a proposal has one of the supported request shapes for Model 1.
"""

from __future__ import annotations

import datetime as dt
import math
from typing import Any


class RetrievalRequestError(ValueError):
    """A retrieval proposal is not structurally legal for the first stage."""


LEGAL_RETRIEVAL_OPERATORS = frozenset({
    "exact.equals",
    "lexical.terms",
    "lexical.phrase",
    "vector.semantic_similarity",
    "graph.discovery.terms",
    "graph.discovery.phrase",
    "graph.relation_occurrence_lookup",
})


def _string_property() -> dict[str, str]:
    return {"type": "string"}


def retrieval_proposal_schema() -> dict[str, Any]:
    """Return the strict provider schema for the same grammar this module validates."""

    def common(operator: str, fields: dict[str, Any], required: list[str]) -> dict[str, Any]:
        properties = {"operator": {"type": "string", "enum": [operator]}, **fields}
        return {
            "type": "object",
            "additionalProperties": False,
            "required": ["operator", *required],
            "properties": properties,
        }

    scalar = {
        "type": "object",
        "additionalProperties": False,
        "required": ["shape", "domain", "value"],
        "properties": {
            "shape": {"type": "string", "enum": ["scalar"]},
            "domain": {
                "type": "string",
                "enum": ["string", "integer", "float", "boolean", "null", "date", "datetime"],
            },
            "value": {"type": ["string", "integer", "number", "boolean", "null"]},
        },
    }
    ordered = {
        "type": "object",
        "additionalProperties": False,
        "required": ["shape", "member_domain", "value"],
        "properties": {
            "shape": {"type": "string", "enum": ["ordered_sequence"]},
            "member_domain": {"type": "string", "enum": ["string"]},
            "value": {"type": "array", "items": {"type": "string"}},
        },
    }
    exact = common(
        "exact.equals",
        {
            "field_class": _string_property(),
            "field_name": _string_property(),
            "target": {"type": "string", "enum": ["complete_value", "member"]},
            "operand": {"anyOf": [scalar, ordered]},
        },
        ["field_class", "field_name", "target", "operand"],
    )
    terms = common(
        "lexical.terms",
        {
            "field_class": _string_property(),
            "field_name": _string_property(),
            "target": {"type": "string", "enum": ["complete_value", "member"]},
            "operand": {"type": "array", "items": {"type": "string"}, "minItems": 1},
        },
        ["field_class", "field_name", "target", "operand"],
    )
    phrase = common(
        "lexical.phrase",
        {
            "field_class": _string_property(),
            "field_name": _string_property(),
            "target": {"type": "string", "enum": ["complete_value", "member"]},
            "operand": _string_property(),
        },
        ["field_class", "field_name", "target", "operand"],
    )
    vector = common("vector.semantic_similarity", {"query": _string_property()}, ["query"])
    graph_terms = common(
        "graph.discovery.terms",
        {
            "node_kind": _string_property(),
            "dimension_name": _string_property(),
            "operand": {"type": "array", "items": {"type": "string"}, "minItems": 1},
        },
        ["node_kind", "dimension_name", "operand"],
    )
    graph_phrase = common(
        "graph.discovery.phrase",
        {
            "node_kind": _string_property(),
            "dimension_name": _string_property(),
            "operand": _string_property(),
        },
        ["node_kind", "dimension_name", "operand"],
    )
    relation = common(
        "graph.relation_occurrence_lookup",
        {"relation_class": _string_property(), "relation_name": _string_property()},
        ["relation_class", "relation_name"],
    )
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["requests"],
        "properties": {
            "requests": {
                "type": "array",
                "items": {"anyOf": [exact, terms, phrase, vector, graph_terms, graph_phrase, relation]},
            }
        },
    }


def _nonblank(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RetrievalRequestError(f"retrieval {name} must be nonblank text")
    return value


def _validate_scalar_operand(operand: Any) -> dict[str, Any]:
    if not isinstance(operand, dict) or set(operand) != {"shape", "domain", "value"} or operand.get("shape") != "scalar":
        raise RetrievalRequestError("invalid exact scalar operand")
    domain = operand.get("domain")
    value = operand.get("value")
    if domain not in {"string", "integer", "float", "boolean", "null", "date", "datetime"}:
        raise RetrievalRequestError("invalid exact scalar domain")
    if domain in {"string", "date", "datetime"} and not isinstance(value, str):
        raise RetrievalRequestError("exact text domain requires a string value")
    if domain == "integer" and (not isinstance(value, int) or isinstance(value, bool)):
        raise RetrievalRequestError("exact integer domain requires an integer value")
    if domain == "float" and (not isinstance(value, float) or not math.isfinite(value)):
        raise RetrievalRequestError("exact float domain requires a finite number")
    if domain == "boolean" and not isinstance(value, bool):
        raise RetrievalRequestError("exact boolean domain requires a boolean value")
    if domain == "date":
        try:
            dt.date.fromisoformat(value)
        except ValueError as exc:
            raise RetrievalRequestError("exact date domain requires an ISO date") from exc
    if domain == "datetime":
        try:
            dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise RetrievalRequestError("exact datetime domain requires an ISO datetime") from exc
    if domain == "null" and value is not None:
        raise RetrievalRequestError("exact null domain requires null")
    return {"shape": "scalar", "domain": domain, "value": value}


def canonicalize_retrieval_request(request: Any) -> dict[str, Any]:
    """Validate and deterministically key-order one first-stage request."""

    if not isinstance(request, dict) or "operator" not in request:
        raise RetrievalRequestError("retrieval request must be an object with an operator")
    operator = request["operator"]
    if operator == "exact.equals":
        if set(request) != {"operator", "field_class", "field_name", "target", "operand"}:
            raise RetrievalRequestError("invalid exact retrieval request shape")
        field_class = _nonblank(request["field_class"], "field_class")
        field_name = _nonblank(request["field_name"], "field_name")
        target = request["target"]
        if target not in {"complete_value", "member"}:
            raise RetrievalRequestError("invalid exact retrieval target")
        operand = request["operand"]
        if isinstance(operand, dict) and operand.get("shape") == "ordered_sequence":
            if (
                set(operand) != {"shape", "member_domain", "value"}
                or operand["member_domain"] != "string"
                or not isinstance(operand["value"], list)
                or not all(isinstance(item, str) for item in operand["value"])
            ):
                raise RetrievalRequestError("invalid ordered exact operand")
            canonical_operand = {
                "shape": "ordered_sequence",
                "member_domain": "string",
                "value": list(operand["value"]),
            }
        else:
            canonical_operand = _validate_scalar_operand(operand)
        return {
            "operator": operator,
            "field_class": field_class,
            "field_name": field_name,
            "target": target,
            "operand": canonical_operand,
        }
    if operator in {"lexical.terms", "lexical.phrase"}:
        if set(request) != {"operator", "field_class", "field_name", "target", "operand"}:
            raise RetrievalRequestError("invalid lexical retrieval request shape")
        field_class = _nonblank(request["field_class"], "field_class")
        field_name = _nonblank(request["field_name"], "field_name")
        if request["target"] not in {"complete_value", "member"}:
            raise RetrievalRequestError("invalid lexical retrieval target")
        operand = request["operand"]
        if operator == "lexical.terms":
            if not isinstance(operand, list) or not operand or not all(isinstance(item, str) and item.strip() for item in operand):
                raise RetrievalRequestError("lexical terms operand must be a nonempty string array")
        else:
            _nonblank(operand, "phrase operand")
        return {
            "operator": operator,
            "field_class": field_class,
            "field_name": field_name,
            "target": request["target"],
            "operand": operand,
        }
    if operator == "vector.semantic_similarity":
        if set(request) != {"operator", "query"}:
            raise RetrievalRequestError("invalid vector retrieval request shape")
        return {"operator": operator, "query": _nonblank(request["query"], "query")}
    if operator in {"graph.discovery.terms", "graph.discovery.phrase"}:
        if set(request) != {"operator", "node_kind", "dimension_name", "operand"}:
            raise RetrievalRequestError("invalid graph discovery request shape")
        node_kind = _nonblank(request["node_kind"], "node_kind")
        dimension_name = _nonblank(request["dimension_name"], "dimension_name")
        operand = request["operand"]
        if operator.endswith("terms"):
            if not isinstance(operand, list) or not operand or not all(isinstance(item, str) and item.strip() for item in operand):
                raise RetrievalRequestError("graph terms operand must be a nonempty string array")
        else:
            _nonblank(operand, "phrase operand")
        return {"operator": operator, "node_kind": node_kind, "dimension_name": dimension_name, "operand": operand}
    if operator == "graph.relation_occurrence_lookup":
        if set(request) != {"operator", "relation_class", "relation_name"}:
            raise RetrievalRequestError("invalid graph relation request shape")
        return {
            "operator": operator,
            "relation_class": _nonblank(request["relation_class"], "relation_class"),
            "relation_name": _nonblank(request["relation_name"], "relation_name"),
        }
    raise RetrievalRequestError(f"unsupported retrieval operator: {operator!r}")


def canonicalize_retrieval_requests(requests: Any) -> tuple[dict[str, Any], ...]:
    """Validate every proposal while preserving request order and duplicates."""

    if not isinstance(requests, (list, tuple)):
        raise RetrievalRequestError("retrieval requests must be an array")
    return tuple(canonicalize_retrieval_request(request) for request in requests)


__all__ = [
    "LEGAL_RETRIEVAL_OPERATORS",
    "RetrievalRequestError",
    "canonicalize_retrieval_request",
    "canonicalize_retrieval_requests",
    "retrieval_proposal_schema",
]
