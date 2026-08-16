"""Generate the model-facing capability catalog from accepted facts.

The capability-facts observer is the authority for what a completed build
represents. This module only presents those facts and applies descriptions
supplied by the effective BuildConfig; it does not inspect canonical tables,
the vault, or build configuration.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping



class CatalogGenerationError(ValueError):
    """The effective config or accepted facts cannot produce a catalog."""


_DOMAIN_ORDER = ("null", "boolean", "integer", "float", "date", "datetime", "string", "mapping")
_DOMAIN_RANK = {name: index for index, name in enumerate(_DOMAIN_ORDER)}
_FIXED_DESCRIPTIONS = {
    ("intrinsic", "raw_markdown"): "authored Markdown of a semantic unit",
    ("intrinsic", "parsed_text"): "canonical parsed linguistic text of a semantic unit",
    ("region", "region_path"): "complete ordered canonical region address path",
    ("region", "region_text"): "canonical region address text represented by lexical retrieval",
    ("semantic_path", "path_component"): "one authored semantic path hierarchy component",
    ("semantic_path", "path_hierarchy"): "complete ordered authored semantic path hierarchy",
}
_FIXED_VALUE_MODELS = {
    ("intrinsic", "raw_markdown"): {"shapes": [{"shape": "scalar", "domains": ["string"]}]},
    ("intrinsic", "parsed_text"): {"shapes": [{"shape": "scalar", "domains": ["string"]}]},
    ("region", "region_path"): {"shapes": [{"shape": "ordered_sequence", "domains": ["string"]}]},
    ("region", "region_text"): {"shapes": [{"shape": "scalar", "domains": ["string"]}]},
    ("semantic_path", "path_component"): {"shapes": [{"shape": "scalar", "domains": ["string"]}]},
    ("semantic_path", "path_hierarchy"): {"shapes": [{"shape": "ordered_sequence", "domains": ["string"]}]},
}
_FIXED_GRAPH_DISCOVERY_DESCRIPTIONS = {
    ("semantic_object", "address_text"): "authored semantic object address text",
    ("semantic_object", "tag"): "authored semantic object tag grouping descriptor",
    ("semantic_region", "address_text"): "canonical semantic region address text",
}
_FIXED_RELATION_DESCRIPTIONS = {
    ("body_wikilink", "linked_to"): "body wikilink relation from a semantic unit to a resolved semantic object or region",
    ("structural", "contains_scope"): "direct authored containment from one scope to a nested scope",
    ("structural", "contains_object"): "direct authored containment from a scope to a semantic object",
    ("structural", "contains_region"): "direct authored containment from a semantic object or region to a semantic region",
    ("structural", "contains_unit"): "direct authored containment from a semantic object or region to a semantic unit",
}
_RELATION_OPERATIONS = ["relation_occurrence_lookup", "inbound_traversal", "outbound_traversal"]
_RELATION_ENDPOINTS = {
    ("body_wikilink", "linked_to"): (["semantic_unit"], ["semantic_object", "semantic_region"]),
    ("structural", "contains_scope"): (["scope"], ["scope"]),
    ("structural", "contains_object"): (["scope"], ["semantic_object"]),
    ("structural", "contains_region"): (["semantic_object", "semantic_region"], ["semantic_region"]),
    ("structural", "contains_unit"): (["semantic_object", "semantic_region"], ["semantic_unit"]),
}


def _ordered_domains(values: Any) -> list[str]:
    if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
        raise CatalogGenerationError("capability fact domains must be ordered strings")
    unknown = [value for value in values if value not in _DOMAIN_RANK]
    if unknown:
        raise CatalogGenerationError(f"unsupported capability domain: {unknown[0]}")
    return sorted(set(values), key=_DOMAIN_RANK.__getitem__)


def _represented_semantic_identifier_keys(facts: Mapping[str, Any]) -> list[str]:
    keys = {
        field["field_name"]
        for field in facts["field_capabilities"]
        if field["field_class"] == "semantic_identifier"
    }
    keys.update(
        relation["relation_name"]
        for relation in facts["graph_relation_capabilities"]
        if relation["relation_class"] == "semantic_identifier"
    )
    return sorted(keys)


def _semantic_identifier_value_model(field: Mapping[str, Any]) -> dict[str, Any]:
    scalar_domains = _ordered_domains(field.get("scalar_domains", []))
    member_domains = _ordered_domains(field.get("sequence_member_domains", []))
    shapes = field.get("canonical_shapes")
    if not isinstance(shapes, list) or any(not isinstance(shape, str) for shape in shapes):
        raise CatalogGenerationError("semantic identifier canonical_shapes is invalid")
    value_shapes: list[dict[str, Any]] = []
    if scalar_domains:
        value_shapes.append({"shape": "scalar", "domains": scalar_domains})
    if "sequence" in shapes:
        if not member_domains:
            raise CatalogGenerationError(f"sequence semantic identifier lacks member domains: {field['field_name']!r}")
        value_shapes.append({"shape": "sequence", "member_domains": member_domains})
    if not value_shapes:
        raise CatalogGenerationError(f"semantic identifier has no representable value shape: {field['field_name']!r}")
    return {"shapes": value_shapes}


def _access(operator: str, target: str, domains: list[str], **extra: Any) -> dict[str, Any]:
    value = {"operator": operator, "target": target, "domains": domains}
    value.update(extra)
    return value


def _field_access(field: Mapping[str, Any]) -> list[dict[str, Any]]:
    field_class, field_name = field["field_class"], field["field_name"]
    surfaces = field.get("surfaces")
    if not isinstance(surfaces, dict):
        raise CatalogGenerationError(f"field surfaces are invalid: {field_class}/{field_name}")
    access: list[dict[str, Any]] = []
    if "exact" in surfaces:
        exact = surfaces["exact"]
        if not isinstance(exact, dict) or exact.get("operators") != ["equals"]:
            raise CatalogGenerationError(f"unsupported exact grammar: {field_class}/{field_name}")
        if field_class == "semantic_identifier":
            scalar_domains = _ordered_domains(field.get("scalar_domains", []))
            member_domains = _ordered_domains(field.get("sequence_member_domains", []))
            if scalar_domains:
                access.append(_access("exact.equals", "complete_value", scalar_domains))
            if member_domains:
                access.append(_access("exact.equals", "member", member_domains, sequence_behavior="member_equality"))
        else:
            model = _FIXED_VALUE_MODELS.get((field_class, field_name))
            if model is None:
                raise CatalogGenerationError(f"unsupported exact field dimension: {field_class}/{field_name}")
            access.append(_access("exact.equals", "complete_value", list(model["shapes"][0]["domains"])))
    if "lexical" in surfaces:
        lexical = surfaces["lexical"]
        if not isinstance(lexical, dict) or lexical.get("operators") != ["terms", "phrase"]:
            raise CatalogGenerationError(f"unsupported lexical grammar: {field_class}/{field_name}")
        if field_class == "semantic_identifier":
            scalar_domains = _ordered_domains(field.get("scalar_domains", []))
            member_domains = _ordered_domains(field.get("sequence_member_domains", []))
            if "string" in scalar_domains:
                access.extend((_access("lexical.terms", "complete_value", ["string"]), _access("lexical.phrase", "complete_value", ["string"])))
            if "string" in member_domains:
                access.extend((_access("lexical.terms", "member", ["string"], sequence_behavior="member_equality"), _access("lexical.phrase", "member", ["string"], sequence_behavior="member_equality")))
        else:
            if (field_class, field_name) not in _FIXED_VALUE_MODELS:
                raise CatalogGenerationError(f"unsupported lexical field dimension: {field_class}/{field_name}")
            access.extend((_access("lexical.terms", "complete_value", ["string"]), _access("lexical.phrase", "complete_value", ["string"])))
    return access


def _semantic_dimensions(facts: Mapping[str, Any], descriptions: Mapping[str, str]) -> list[dict[str, Any]]:
    dimensions = []
    fields = sorted(facts["field_capabilities"], key=lambda field: (field["field_class"], field["field_name"]))
    for field in fields:
        field_class, field_name = field["field_class"], field["field_name"]
        if field_class == "semantic_identifier":
            description, value = descriptions[field_name], _semantic_identifier_value_model(field)
        else:
            key = (field_class, field_name)
            if key not in _FIXED_DESCRIPTIONS or key not in _FIXED_VALUE_MODELS:
                raise CatalogGenerationError(f"unsupported field capability: {field_class}/{field_name}")
            description, value = _FIXED_DESCRIPTIONS[key], _FIXED_VALUE_MODELS[key]
        dimensions.append({"field_class": field_class, "field_name": field_name, "description": description, "value": value, "access": _field_access(field)})
    return dimensions


def _graph(facts: Mapping[str, Any], descriptions: Mapping[str, str]) -> dict[str, Any]:
    discovery = []
    for item in sorted(facts["graph_discovery_capabilities"], key=lambda item: (item["node_kind"], item["dimension_name"])):
        key = (item["node_kind"], item["dimension_name"])
        if key not in _FIXED_GRAPH_DISCOVERY_DESCRIPTIONS or item.get("operators") != ["terms", "phrase"]:
            raise CatalogGenerationError(f"unsupported graph discovery capability: {key[0]}/{key[1]}")
        discovery.append({"node_kind": item["node_kind"], "dimension_name": item["dimension_name"], "description": _FIXED_GRAPH_DISCOVERY_DESCRIPTIONS[key], "operators": ["graph.discovery.terms", "graph.discovery.phrase"], "result": "opaque_graph_handle"})
    relations = []
    for item in sorted(facts["graph_relation_capabilities"], key=lambda item: (item["relation_class"], item["relation_name"])):
        relation_class, relation_name = item["relation_class"], item["relation_name"]
        if item.get("operations") != _RELATION_OPERATIONS:
            raise CatalogGenerationError(f"unsupported graph relation grammar: {relation_class}/{relation_name}")
        if relation_class == "semantic_identifier":
            if relation_name == "tags":
                raise CatalogGenerationError("semantic_identifier/tags is not graph topology")
            if item.get("source_kinds") != ["semantic_object"] or item.get("target_kinds") != ["semantic_object", "semantic_region"]:
                raise CatalogGenerationError(f"unsupported semantic-identifier endpoints: {relation_name}")
            description = descriptions[relation_name]
        else:
            description = _FIXED_RELATION_DESCRIPTIONS.get((relation_class, relation_name))
            if description is None:
                raise CatalogGenerationError(f"unsupported graph relation: {relation_class}/{relation_name}")
            expected_endpoints = _RELATION_ENDPOINTS[(relation_class, relation_name)]
            if item.get("source_kinds") != expected_endpoints[0] or item.get("target_kinds") != expected_endpoints[1]:
                raise CatalogGenerationError(f"unsupported graph relation endpoints: {relation_class}/{relation_name}")
        relations.append({"relation_class": relation_class, "relation_name": relation_name, "description": description, "source_kinds": list(item["source_kinds"]), "target_kinds": list(item["target_kinds"]), "operations": [f"graph.{operation}" for operation in item["operations"]]})
    return {"node_kinds": list(facts["graph_node_classes"]), "discovery": discovery, "relations": relations}


def _vector(facts: Mapping[str, Any]) -> dict[str, Any]:
    vector = facts["vector_capability"]
    expected_query = {"shape": "string", "requirement": "exactly one non-empty string", "segmentation": False, "truncation": False, "deterministic_enrichment": False}
    if vector.get("operations") != ["semantic_similarity"] or vector.get("query_operand") != expected_query:
        raise CatalogGenerationError("unsupported vector grammar")
    targets = []
    expected_inputs = {"semantic_unit": "exact_canonical_parsed_text", "semantic_object": "canonical_authored_object_name_for_zero_unit_object"}
    for target in sorted(vector.get("represented_target_kinds", []), key=lambda item: item["target_kind"]):
        kind = target["target_kind"]
        if kind not in expected_inputs or target.get("input_dimension") != expected_inputs[kind]:
            raise CatalogGenerationError(f"unsupported vector target: {kind}")
        targets.append({"target_kind": kind, "input": "exact canonical parsed_text" if kind == "semantic_unit" else "canonical authored object name for a zero-unit object"})
    return {"operator": "vector.semantic_similarity", "query": expected_query, "targets": targets}


def _operators(facts: Mapping[str, Any]) -> dict[str, Any]:
    expected = {"exact": ["equals"], "lexical": ["terms", "phrase"], "vector": ["semantic_similarity"], "graph": ["node_discovery", "relation_occurrence_lookup", "inbound_traversal", "outbound_traversal"]}
    if facts["surface_operation_grammar"] != expected:
        raise CatalogGenerationError("unsupported surface operator grammar")
    return {
        "exact.equals": {"surface": "exact", "meaning": "typed exact equality using the accepted exact normalization; distinct canonical value domains remain distinct"},
        "lexical.terms": {"surface": "lexical", "meaning": "OR matching over one-token operands under the accepted lexical tokenizer"},
        "lexical.phrase": {"surface": "lexical", "meaning": "contiguous token-sequence matching under the accepted lexical tokenizer"},
        "vector.semantic_similarity": {"surface": "vector", "meaning": "cosine similarity for exactly the supplied query text, without segmentation, truncation, or deterministic enrichment"},
        "graph.discovery.terms": {"surface": "graph", "meaning": "discover represented graph instances using OR lexical terms and return an opaque typed graph handle"},
        "graph.discovery.phrase": {"surface": "graph", "meaning": "discover represented graph instances using a contiguous lexical phrase and return an opaque typed graph handle"},
        "graph.relation_occurrence_lookup": {"surface": "graph", "meaning": "locate occurrences of one advertised relation type"},
        "graph.inbound_traversal": {"surface": "graph", "meaning": "follow one advertised relation type one hop inbound from a typed graph handle"},
        "graph.outbound_traversal": {"surface": "graph", "meaning": "follow one advertised relation type one hop outbound from a typed graph handle"},
    }


def generate_catalog(
    facts: Mapping[str, Any],
    semantic_identifier_descriptions: Mapping[str, str],
    output_path: str | Path,
) -> dict[str, Any]:
    """Generate one deterministic catalog from an effective BuildConfig mapping."""
    descriptions = semantic_identifier_descriptions
    if not isinstance(descriptions, Mapping):
        raise CatalogGenerationError("catalog descriptions must come from effective BuildConfig")
    for field_name, description in descriptions.items():
        if not isinstance(field_name, str) or not isinstance(description, str):
            raise CatalogGenerationError("catalog descriptions must be string keyed and string valued")
    required = _represented_semantic_identifier_keys(facts)
    missing = [name for name in required if name not in descriptions or not descriptions[name].strip()]
    if missing:
        raise CatalogGenerationError(
            "effective BuildConfig lacks descriptions for represented semantic identifiers: "
            + ", ".join(missing)
        )
    catalog = {
        "catalog_schema_version": "1",
        "semantic_dimensions": _semantic_dimensions(facts, descriptions),
        "graph": _graph(facts, descriptions),
        "vector": _vector(facts),
        "operators": _operators(facts),
    }
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    return catalog


__all__ = ["CatalogGenerationError", "generate_catalog"]
