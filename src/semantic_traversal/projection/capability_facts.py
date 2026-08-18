"""Read-only diagnostic capability facts for one completed build.

This module deliberately describes a completed build's mechanical retrieval
surface.  Its dictionary serialization is diagnostic observer output, not the
future capability-catalog schema or a control-plane API.
"""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import Any

import numpy as np

from .substrate import _decode_value
from .vector import (
    NORMALIZATION_RULE,
    REQUESTED_MODEL,
    SIMILARITY_METRIC,
    VECTOR_DIMENSION,
    VECTOR_DTYPE,
    validate_vector_index,
)


class CapabilityObservationError(ValueError):
    """The completed build contains a fact outside the accepted grammar."""


_DOMAIN_ORDER = (
    "null",
    "boolean",
    "integer",
    "float",
    "date",
    "datetime",
    "string",
    "sequence",
    "mapping",
)
_DOMAIN_RANK = {name: index for index, name in enumerate(_DOMAIN_ORDER)}

_FIXED_EXACT_SHAPES = {
    ("intrinsic", "raw_markdown"): (("string",), None),
    ("intrinsic", "parsed_text"): (("string",), None),
    ("region", "region_path"): (("sequence",), ("string",)),
    ("semantic_path", "path_hierarchy"): (("sequence",), ("string",)),
    ("semantic_path", "path_component"): (("string",), None),
}
_FIXED_LEXICAL_DIMENSIONS = {
    ("intrinsic", "parsed_text"),
    ("region", "region_text"),
    ("semantic_path", "path_component"),
}
_GRAPH_DISCOVERY_DIMENSIONS = {
    ("semantic_object", "address_text"),
    ("semantic_object", "tag"),
    ("semantic_region", "address_text"),
}
_GRAPH_NODE_ORDER = ("scope", "semantic_object", "semantic_region", "semantic_unit")
_GRAPH_ENDPOINTS = {
    ("body_wikilink", "linked_to"): (("semantic_unit",), ("semantic_object", "semantic_region")),
    ("structural", "contains_scope"): (("scope",), ("scope",)),
    ("structural", "contains_object"): (("scope",), ("semantic_object",)),
    ("structural", "contains_region"): (("semantic_object", "semantic_region"), ("semantic_region",)),
    ("structural", "contains_unit"): (("semantic_object", "semantic_region"), ("semantic_unit",)),
}
_RELATION_OPERATIONS = (
    "relation_occurrence_lookup",
    "inbound_traversal",
    "outbound_traversal",
)
_SURFACE_OPERATIONS = {
    "exact": ("equals",),
    "lexical": ("terms", "phrase"),
    "vector": ("semantic_similarity",),
    "graph": (
        "node_discovery",
        "relation_occurrence_lookup",
        "inbound_traversal",
        "outbound_traversal",
    ),
    "temporal": ("earliest", "latest", "before", "after", "between", "ordered"),
}


def _ordered_domains(values: set[str]) -> list[str]:
    return sorted(values, key=_DOMAIN_RANK.__getitem__)


def _value_domain(value: Any) -> str:
    # bool and datetime must precede their int/date base classes.
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "float"
    if isinstance(value, datetime):
        return "datetime"
    if isinstance(value, date):
        return "date"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "sequence"
    if isinstance(value, dict):
        return "mapping"
    raise CapabilityObservationError(f"unsupported canonical value type: {type(value).__name__}")


def _identifier_shapes(
    connection: sqlite3.Connection,
    field_name: str,
) -> tuple[list[str], list[str], list[str]]:
    """Classify only identifier values inherited by canonical units.

    Object-level state remains authoritative canonical provenance, but it is
    not the represented population for unit-returning exact/lexical surfaces.
    """

    domains: set[str] = set()
    scalar_domains: set[str] = set()
    member_domains: set[str] = set()
    rows = connection.execute(
        """SELECT state, value_json FROM inherited_identifiers
        WHERE field_name = ? ORDER BY unit_id, ordinal""",
        (field_name,),
    ).fetchall()
    for state, value_json in rows:
        if state != "present_value":
            continue
        value = _decode_value(value_json)
        domain = _value_domain(value)
        domains.add(domain)
        if isinstance(value, list):
            value_member_domains = {_value_domain(member) for member in value}
            if "mapping" in value_member_domains:
                raise CapabilityObservationError(
                    f"exact surface cannot represent mapping semantic identifier: {field_name!r}"
                )
            member_domains.update(value_member_domains)
        else:
            if domain == "mapping":
                raise CapabilityObservationError(
                    f"exact surface cannot represent mapping semantic identifier: {field_name!r}"
                )
            scalar_domains.add(domain)
    if not domains:
        raise CapabilityObservationError(
            f"represented semantic identifier has no inherited unit value shape: {field_name!r}"
        )
    return (
        _ordered_domains(domains),
        _ordered_domains(scalar_domains),
        _ordered_domains(member_domains),
    )


def _validate_exact_dimension(field_class: str, field_name: str) -> None:
    if (field_class, field_name) in _FIXED_EXACT_SHAPES:
        return
    if field_class == "semantic_identifier" and field_name:
        return
    raise CapabilityObservationError(f"unsupported exact field dimension: {field_class}/{field_name}")


def _validate_lexical_dimension(field_class: str, field_name: str) -> None:
    if (field_class, field_name) in _FIXED_LEXICAL_DIMENSIONS:
        return
    if field_class == "semantic_identifier" and field_name:
        return
    raise CapabilityObservationError(f"unsupported lexical field dimension: {field_class}/{field_name}")


def _field_capabilities(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    exact_table = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'exact_index_entries'"
    ).fetchone()
    if exact_table is None:
        raise CapabilityObservationError("completed exact surface is absent")
    exact_rows = tuple(connection.execute(
        "SELECT DISTINCT field_class, field_name FROM exact_index_entries ORDER BY field_class, field_name"
    ))
    # These dimensions are part of the accepted exact grammar even when this
    # particular build has no matching rows for one of them.
    exact = tuple(sorted(set(exact_rows) | set(_FIXED_EXACT_SHAPES)))
    lexical = tuple(connection.execute(
        "SELECT field_class, field_name FROM lexical_dimension_registry ORDER BY field_class, field_name"
    ))
    for field_class, field_name in exact_rows:
        _validate_exact_dimension(field_class, field_name)
    for field_class, field_name in lexical:
        _validate_lexical_dimension(field_class, field_name)

    dimensions = sorted(set(exact) | set(lexical))
    facts: list[dict[str, Any]] = []
    for field_class, field_name in dimensions:
        if field_class == "semantic_identifier":
            shapes, scalar_domains, member_domains = _identifier_shapes(connection, field_name)
        else:
            fixed = _FIXED_EXACT_SHAPES.get((field_class, field_name))
            if fixed is None:
                shapes, scalar_domains, member_domains = ["string"], ["string"], []
            else:
                shapes = list(fixed[0])
                scalar_domains = []
                member_domains = list(fixed[1] or ())
        surfaces: dict[str, Any] = {}
        if (field_class, field_name) in exact:
            exact_fact: dict[str, Any] = {"operators": list(_SURFACE_OPERATIONS["exact"])}
            if field_class == "semantic_identifier":
                exact_fact["operand_domains"] = _ordered_domains(
                    set(scalar_domains) | set(member_domains)
                )
                if "sequence" in shapes:
                    exact_fact["sequence_behavior"] = "member_equality"
            elif field_class in {"region", "semantic_path"} and "sequence" in shapes:
                exact_fact["operand_semantics"] = "ordered_sequence"
            surfaces["exact"] = exact_fact
        if (field_class, field_name) in lexical:
            lexical_fact: dict[str, Any] = {"operators": list(_SURFACE_OPERATIONS["lexical"])}
            if field_class == "semantic_identifier":
                # Lexical indexing admits only direct text members; a mixed
                # typed field therefore exposes only its represented strings.
                lexical_fact["operand_domains"] = [
                    "string"
                ] if "string" in scalar_domains or "string" in member_domains else []
            surfaces["lexical"] = lexical_fact
        if (field_class, field_name) == ("semantic_identifier", "journal_entry_date"):
            temporal = connection.execute(
                """SELECT 1 FROM temporal_dimension_registry
                WHERE field_class = 'semantic_identifier'
                  AND field_name = 'journal_entry_date' AND domain = 'date'"""
            ).fetchone()
            if temporal is None:
                raise CapabilityObservationError("journal_entry_date temporal dimension is absent")
            surfaces["temporal"] = {
                "operators": list(_SURFACE_OPERATIONS["temporal"]),
                "domain": "date",
                "result_identity": "unit_id / semantic_unit",
                "coverage": "exhaustive",
            }
        fact: dict[str, Any] = {
            "field_class": field_class,
            "field_name": field_name,
            "canonical_shapes": shapes,
            "surfaces": surfaces,
        }
        if field_class == "semantic_identifier":
            fact["scalar_domains"] = scalar_domains
        if member_domains:
            fact["sequence_member_domains"] = member_domains
        facts.append(fact)
    return facts


def _graph_discovery(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = tuple(connection.execute(
        "SELECT node_kind, dimension_name FROM graph_discovery_registry ORDER BY node_kind, dimension_name"
    ))
    for node_kind, dimension_name in rows:
        if (node_kind, dimension_name) not in _GRAPH_DISCOVERY_DIMENSIONS:
            raise CapabilityObservationError(
                f"unsupported graph discovery dimension: {node_kind}/{dimension_name}"
            )
    return [
        {"node_kind": node_kind, "dimension_name": dimension_name, "operators": list(_SURFACE_OPERATIONS["lexical"])}
        for node_kind, dimension_name in rows
    ]


def _graph_relations(connection: sqlite3.Connection) -> tuple[list[dict[str, Any]], list[str]]:
    rows = tuple(connection.execute(
        "SELECT relation_class, relation_name FROM graph_relation_types ORDER BY relation_class, relation_name"
    ))
    relation_facts: list[dict[str, Any]] = []
    node_kinds: set[str] = set()
    for relation_class, relation_name in rows:
        if relation_class == "semantic_identifier":
            if not relation_name or relation_name == "tags":
                raise CapabilityObservationError(
                    f"unsupported semantic-identifier graph relation: {relation_name!r}"
                )
            source_kinds, target_kinds = ("semantic_object",), ("semantic_object", "semantic_region")
        else:
            endpoints = _GRAPH_ENDPOINTS.get((relation_class, relation_name))
            if endpoints is None:
                raise CapabilityObservationError(
                    f"unsupported graph relation type: {relation_class}/{relation_name}"
                )
            source_kinds, target_kinds = endpoints
        node_kinds.update(source_kinds)
        node_kinds.update(target_kinds)
        relation_facts.append({
            "relation_class": relation_class,
            "relation_name": relation_name,
            "source_kinds": list(source_kinds),
            "target_kinds": list(target_kinds),
            "operations": list(_RELATION_OPERATIONS),
        })
    return relation_facts, [kind for kind in _GRAPH_NODE_ORDER if kind in node_kinds]


def _vector_facts(connection: sqlite3.Connection, vector_path: Path) -> dict[str, Any]:
    if not vector_path.is_file():
        raise CapabilityObservationError(f"missing vector artifact: {vector_path}")
    try:
        validate_vector_index(connection, vector_path)
    except (OSError, ValueError, sqlite3.Error) as exc:
        raise CapabilityObservationError(f"completed vector state is invalid: {exc}") from exc
    contract = connection.execute(
        """SELECT requested_model, resolved_model_identity, embedding_dimension,
        vector_dtype, normalization_rule, similarity_metric, input_capacity,
        capacity_mechanism, tokenization_contract
        FROM vector_build_contract WHERE contract_id = 1"""
    ).fetchone()
    if contract is None:
        raise CapabilityObservationError("vector build contract is absent")
    requested, identity, dimension, dtype, normalization, similarity, capacity, mechanism, tokenization = contract
    if (
        requested != REQUESTED_MODEL
        or not re.fullmatch(r"sha256-[0-9a-f]{64}", identity, re.IGNORECASE)
        or dimension != VECTOR_DIMENSION
        or dtype != VECTOR_DTYPE
        or str(normalization).lower() != NORMALIZATION_RULE
        or str(similarity).lower() != SIMILARITY_METRIC
        or not isinstance(capacity, int)
        or capacity <= 0
        or mechanism != "ollama_provider_acceptance_truncate_false"
        or tokenization != "provider-opaque acceptance; no local tokenizer"
    ):
        raise CapabilityObservationError("persisted vector contract is outside the accepted grammar")
    target_kinds = [row[0] for row in connection.execute(
        "SELECT DISTINCT target_kind FROM vector_segments ORDER BY target_kind"
    )]
    if any(kind not in {"semantic_unit", "semantic_object"} for kind in target_kinds):
        raise CapabilityObservationError("unsupported persisted vector target kind")
    targets = []
    for kind in target_kinds:
        targets.append({
            "target_kind": kind,
            "input_dimension": (
                "exact_canonical_parsed_text"
                if kind == "semantic_unit"
                else "canonical_authored_object_name_for_zero_unit_object"
            ),
        })
    return {
        "available": True,
        "operations": list(_SURFACE_OPERATIONS["vector"]),
        "query_operand": {
            "shape": "string",
            "requirement": "exactly one non-empty string",
            "segmentation": False,
            "truncation": False,
            "deterministic_enrichment": False,
        },
        "represented_target_kinds": targets,
    }


def observe_capability_facts(
    connection: sqlite3.Connection,
    vector_path: str | Path,
) -> dict[str, Any]:
    """Observe normalized diagnostic facts from one completed build only.

    The returned mapping is intentionally diagnostic serialization.  It is not
    a stable catalog schema and does not enumerate corpus instances or counts.
    """

    fields = _field_capabilities(connection)
    discovery = _graph_discovery(connection)
    relations, node_classes = _graph_relations(connection)
    return {
        "observer": "capability-facts",
        "field_capabilities": fields,
        "graph_discovery_capabilities": discovery,
        "graph_relation_capabilities": relations,
        "graph_node_classes": node_classes,
        "vector_capability": _vector_facts(connection, Path(vector_path).resolve()),
        "surface_operation_grammar": {
            surface: list(operations) for surface, operations in _SURFACE_OPERATIONS.items()
        },
    }


def human_capability_facts(facts: dict[str, Any]) -> str:
    """Render simple operator diagnostics; this is not catalog presentation."""

    lines = ["FIELD CAPABILITIES"]
    for field in facts["field_capabilities"]:
        surfaces = ", ".join(field["surfaces"])
        shapes = ", ".join(field["canonical_shapes"])
        lines.append(f"  {field['field_class']} / {field['field_name']}: {shapes}; {surfaces}")
    lines.append("GRAPH DISCOVERY")
    for item in facts["graph_discovery_capabilities"]:
        lines.append(f"  {item['node_kind']} / {item['dimension_name']}: terms, phrase")
    lines.append("GRAPH RELATIONS")
    for item in facts["graph_relation_capabilities"]:
        lines.append(f"  {item['relation_class']} / {item['relation_name']}")
    lines.append("VECTOR")
    vector = facts["vector_capability"]
    lines.append(f"  semantic_similarity; targets: {', '.join(item['target_kind'] for item in vector['represented_target_kinds']) or 'none'}")
    lines.append("SURFACE OPERATIONS")
    for surface, operations in facts["surface_operation_grammar"].items():
        lines.append(f"  {surface}: {', '.join(operations)}")
    return "\n".join(lines)
