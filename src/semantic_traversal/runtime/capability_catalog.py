"""Single immutable admission boundary for schema-1 capability catalogs."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any


class CapabilityCatalogError(ValueError):
    """The supplied capability catalog artifact is not a supported artifact."""


@dataclass(frozen=True)
class CapabilityCatalogArtifact:
    text: str
    parsed: Mapping[str, Any]
    sha256: str


class _DuplicateObjectKey(ValueError):
    pass


_DOMAIN_VALUES = frozenset({"null", "boolean", "integer", "float", "date", "datetime", "string"})
_FIELD_ACCESS_OPERATORS = frozenset({"exact.equals", "lexical.terms", "lexical.phrase", "vector.semantic_similarity"})
_GRAPH_DISCOVERY_OPERATORS = frozenset({"graph.discovery.terms", "graph.discovery.phrase"})
_GRAPH_RELATION_OPERATIONS = frozenset({"graph.relation_occurrence_lookup", "graph.inbound_traversal", "graph.outbound_traversal"})
_GRAPH_NODE_KINDS = frozenset({"scope", "semantic_object", "semantic_region", "semantic_unit"})
_VECTOR_TARGET_INPUTS = {
    "semantic_unit": "exact canonical parsed_text",
    "semantic_object": "canonical authored object name for a zero-unit object",
}
_GLOBAL_OPERATOR_SURFACES = {
    "exact.equals": "exact",
    "lexical.terms": "lexical",
    "lexical.phrase": "lexical",
    "vector.semantic_similarity": "vector",
    "graph.discovery.terms": "graph",
    "graph.discovery.phrase": "graph",
    "graph.relation_occurrence_lookup": "graph",
    "graph.inbound_traversal": "graph",
    "graph.outbound_traversal": "graph",
}
_FIELD_CLASSES = frozenset({"intrinsic", "semantic_identifier", "region", "semantic_path"})
_FIXED_VALUE_MODELS = {
    ("intrinsic", "raw_markdown"): {"scalar": ("string",)},
    ("intrinsic", "parsed_text"): {"scalar": ("string",)},
    ("region", "region_path"): {"ordered_sequence": ("string",)},
    ("region", "region_text"): {"scalar": ("string",)},
    ("semantic_path", "path_component"): {"scalar": ("string",)},
    ("semantic_path", "path_hierarchy"): {"ordered_sequence": ("string",)},
}
_GRAPH_DISCOVERY_IDENTITIES = frozenset({
    ("semantic_object", "address_text"),
    ("semantic_object", "tag"),
    ("semantic_region", "address_text"),
})
_RELATION_ENDPOINTS = {
    ("body_wikilink", "linked_to"): (("semantic_unit",), ("semantic_object", "semantic_region")),
    ("structural", "contains_scope"): (("scope",), ("scope",)),
    ("structural", "contains_object"): (("scope",), ("semantic_object",)),
    ("structural", "contains_region"): (("semantic_object", "semantic_region"), ("semantic_region",)),
    ("structural", "contains_unit"): (("semantic_object", "semantic_region"), ("semantic_unit",)),
}
_CONSTITUTIVE_DIMENSIONS = frozenset({
    ("intrinsic", "raw_markdown"),
    ("intrinsic", "parsed_text"),
    ("region", "region_path"),
    ("semantic_path", "path_hierarchy"),
    ("semantic_path", "path_component"),
})
_CONSTITUTIVE_RELATIONS = frozenset({
    ("structural", "contains_scope"),
    ("structural", "contains_object"),
    ("structural", "contains_region"),
    ("structural", "contains_unit"),
    ("body_wikilink", "linked_to"),
})
_CONSTITUTIVE_DISCOVERY_IDENTITIES = frozenset({
    ("semantic_object", "address_text"),
    ("semantic_region", "address_text"),
})


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateObjectKey(f"duplicate JSON object key: {key}")
        result[key] = value
    return result


def _freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def _mapping(value: Any, keys: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise CapabilityCatalogError(f"{label} must be an object")
    if set(value) != keys:
        raise CapabilityCatalogError(f"{label} has an incompatible key set")
    return value


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CapabilityCatalogError(f"{label} must be non-empty text")
    return value


def _text_list(value: Any, label: str, *, nonempty: bool = False) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise CapabilityCatalogError(f"{label} must be an array")
    if nonempty and not value:
        raise CapabilityCatalogError(f"{label} must not be empty")
    if any(not isinstance(item, str) or not item.strip() for item in value):
        raise CapabilityCatalogError(f"{label} must contain only non-empty text")
    if len(set(value)) != len(value):
        raise CapabilityCatalogError(f"{label} contains duplicate values")
    return tuple(value)


def _domain_list(value: Any, label: str, *, nonempty: bool = False) -> tuple[str, ...]:
    domains = _text_list(value, label, nonempty=nonempty)
    unsupported = [domain for domain in domains if domain not in _DOMAIN_VALUES]
    if unsupported:
        raise CapabilityCatalogError(f"{label} contains unsupported domain: {unsupported[0]}")
    return domains


def _validate_value(value: Any, label: str) -> dict[str, tuple[str, ...]]:
    envelope = _mapping(value, {"shapes"}, label)
    shapes = envelope["shapes"]
    if not isinstance(shapes, list) or not shapes:
        raise CapabilityCatalogError(f"{label}.shapes must be a non-empty array")
    models: dict[str, tuple[str, ...]] = {}
    for index, shape_value in enumerate(shapes):
        shape_label = f"{label}.shapes[{index}]"
        if not isinstance(shape_value, dict) or "shape" not in shape_value:
            raise CapabilityCatalogError(f"{shape_label} is malformed")
        shape = _text(shape_value["shape"], f"{shape_label}.shape")
        if shape in models:
            raise CapabilityCatalogError(f"{label} contains duplicate shape identities")
        if shape == "scalar":
            shape_mapping = _mapping(shape_value, {"shape", "domains"}, shape_label)
            models[shape] = _domain_list(shape_mapping["domains"], f"{shape_label}.domains", nonempty=True)
        elif shape == "sequence":
            shape_mapping = _mapping(shape_value, {"shape", "member_domains"}, shape_label)
            models[shape] = _domain_list(shape_mapping["member_domains"], f"{shape_label}.member_domains", nonempty=True)
        elif shape == "ordered_sequence":
            shape_mapping = _mapping(shape_value, {"shape", "domains"}, shape_label)
            models[shape] = _domain_list(shape_mapping["domains"], f"{shape_label}.domains", nonempty=True)
        else:
            raise CapabilityCatalogError(f"{shape_label}.shape is unsupported")
    return models


def _validate_access(access: Any, label: str, value_models: Mapping[str, tuple[str, ...]], field_class: str, field_name: str) -> tuple[str, str]:
    if not isinstance(access, dict) or "operator" not in access or "target" not in access:
        raise CapabilityCatalogError(f"{label} is malformed")
    operator = _text(access["operator"], f"{label}.operator")
    target = _text(access["target"], f"{label}.target")
    if operator not in _FIELD_ACCESS_OPERATORS:
        raise CapabilityCatalogError(f"{label}.operator is unsupported")
    if target not in {"complete_value", "member"}:
        raise CapabilityCatalogError(f"{label}.target is unsupported")

    if "domains" in access:
        entry = _mapping(access, {"operator", "target", "domains"}, label)
        domains = _domain_list(entry["domains"], f"{label}.domains", nonempty=True)
        if operator == "exact.equals":
            expected_shape = "scalar" if target == "complete_value" else "sequence"
            if expected_shape not in value_models or set(value_models[expected_shape]) != set(domains):
                raise CapabilityCatalogError(f"{label} is inconsistent with its value model")
        elif operator in {"lexical.terms", "lexical.phrase"}:
            if domains != ("string",):
                raise CapabilityCatalogError(f"{label} requires the string domain")
            expected_shape = "scalar" if target == "complete_value" else "sequence"
            if expected_shape not in value_models or "string" not in value_models[expected_shape]:
                raise CapabilityCatalogError(f"{label} is inconsistent with its value model")
        else:
            if operator != "vector.semantic_similarity" or field_class != "intrinsic" or field_name != "parsed_text" or target != "complete_value" or domains != ("string",) or "scalar" not in value_models or "string" not in value_models["scalar"]:
                raise CapabilityCatalogError(f"{label} is an illegal vector access")
    elif "operand" in access:
        entry = _mapping(access, {"operator", "target", "operand"}, label)
        if operator != "exact.equals" or target != "complete_value":
            raise CapabilityCatalogError(f"{label} is an illegal ordered-sequence access")
        operand = _mapping(entry["operand"], {"shape", "member_domains"}, f"{label}.operand")
        if operand["shape"] != "ordered_sequence":
            raise CapabilityCatalogError(f"{label}.operand.shape is unsupported")
        member_domains = _domain_list(operand["member_domains"], f"{label}.operand.member_domains", nonempty=True)
        if set(value_models.get("ordered_sequence", ())) != set(member_domains):
            raise CapabilityCatalogError(f"{label} is inconsistent with its value model")
    else:
        raise CapabilityCatalogError(f"{label} has no accepted operand representation")
    return operator, target


def _validate_fixed_access_grammar(identity: tuple[str, str], access_identities: set[tuple[str, str]]) -> None:
    exact = ("exact.equals", "complete_value")
    lexical_terms = ("lexical.terms", "complete_value")
    lexical_phrase = ("lexical.phrase", "complete_value")
    vector = ("vector.semantic_similarity", "complete_value")
    if identity == ("intrinsic", "raw_markdown"):
        if access_identities != {exact}:
            raise CapabilityCatalogError("raw_markdown has an incompatible access grammar")
    elif identity == ("intrinsic", "parsed_text"):
        allowed = {exact, lexical_terms, lexical_phrase, vector}
        required = {exact, lexical_terms, lexical_phrase}
        if not required.issubset(access_identities) or not access_identities.issubset(allowed):
            raise CapabilityCatalogError("parsed_text has an incompatible access grammar")
    elif identity == ("region", "region_path"):
        if access_identities != {exact}:
            raise CapabilityCatalogError("region_path has an incompatible access grammar")
    elif identity == ("region", "region_text"):
        if access_identities != {lexical_terms, lexical_phrase}:
            raise CapabilityCatalogError("region_text has an incompatible access grammar")
    elif identity == ("semantic_path", "path_hierarchy"):
        if access_identities != {exact}:
            raise CapabilityCatalogError("path_hierarchy has an incompatible access grammar")
    elif identity == ("semantic_path", "path_component"):
        allowed = {exact, lexical_terms, lexical_phrase}
        if exact not in access_identities or not access_identities.issubset(allowed):
            raise CapabilityCatalogError("path_component has an incompatible access grammar")
        lexical = {lexical_terms, lexical_phrase} & access_identities
        if lexical and lexical != {lexical_terms, lexical_phrase}:
            raise CapabilityCatalogError("path_component lexical access is incomplete")

def _validate_semantic_identifier_access(
    value_models: Mapping[str, tuple[str, ...]],
    access_identities: set[tuple[str, str]],
) -> None:
    exact = ("exact.equals", "complete_value")
    member_exact = ("exact.equals", "member")
    terms_complete = ("lexical.terms", "complete_value")
    phrase_complete = ("lexical.phrase", "complete_value")
    terms_member = ("lexical.terms", "member")
    phrase_member = ("lexical.phrase", "member")
    expected: set[tuple[str, str]] = set()

    if "scalar" in value_models:
        expected.add(exact)
        if "string" in value_models["scalar"]:
            expected.update({terms_complete, phrase_complete})
    if "sequence" in value_models:
        expected.add(member_exact)
        if "string" in value_models["sequence"]:
            expected.update({terms_member, phrase_member})

    if access_identities != expected:
        raise CapabilityCatalogError("semantic_identifier has an incompatible access grammar")

def _validate_semantic_dimensions(value: Any) -> tuple[set[str], dict[tuple[str, str], set[tuple[str, str]]]]:
    if not isinstance(value, list):
        raise CapabilityCatalogError("semantic_dimensions must be an array")
    identities: set[tuple[str, str]] = set()
    references: set[str] = set()
    access_by_dimension: dict[tuple[str, str], set[tuple[str, str]]] = {}
    for index, item in enumerate(value):
        label = f"semantic_dimensions[{index}]"
        entry = _mapping(item, {"field_class", "field_name", "description", "value", "access"}, label)
        field_class = _text(entry["field_class"], f"{label}.field_class")
        field_name = _text(entry["field_name"], f"{label}.field_name")
        identity = (field_class, field_name)
        if field_class not in _FIELD_CLASSES:
            raise CapabilityCatalogError(f"{label}.field_class is unsupported")
        if field_class != "semantic_identifier" and identity not in _FIXED_VALUE_MODELS:
            raise CapabilityCatalogError(f"{label} has an unsupported fixed field identity")
        if identity in identities:
            raise CapabilityCatalogError("semantic_dimensions contains duplicate identities")
        identities.add(identity)
        _text(entry["description"], f"{label}.description")
        value_models = _validate_value(entry["value"], f"{label}.value")
        if field_class == "semantic_identifier":
            if "ordered_sequence" in value_models:
                raise CapabilityCatalogError(f"{label} semantic identifiers cannot use ordered_sequence")
        elif value_models != _FIXED_VALUE_MODELS[identity]:
            raise CapabilityCatalogError(f"{label} has an incompatible fixed value model")
        if not isinstance(entry["access"], list):
            raise CapabilityCatalogError(f"{label}.access must be an array")
        access_identities: set[tuple[str, str]] = set()
        for access_index, access in enumerate(entry["access"]):
            access_identity = _validate_access(access, f"{label}.access[{access_index}]", value_models, field_class, field_name)
            if access_identity in access_identities:
                raise CapabilityCatalogError(f"{label}.access contains duplicate identities")
            access_identities.add(access_identity)
            references.add(access_identity[0])
        if field_class == "semantic_identifier":
            _validate_semantic_identifier_access(value_models, access_identities)
        else:
            _validate_fixed_access_grammar(identity, access_identities)
        access_by_dimension[identity] = access_identities
    return references, access_by_dimension


def _validate_graph(value: Any) -> tuple[set[str], set[str], set[tuple[str, str]], set[tuple[str, str]]]:
    graph = _mapping(value, {"node_kinds", "discovery", "relations"}, "graph")
    node_kinds = _text_list(graph["node_kinds"], "graph.node_kinds")
    unsupported_nodes = [kind for kind in node_kinds if kind not in _GRAPH_NODE_KINDS]
    if unsupported_nodes:
        raise CapabilityCatalogError(f"graph.node_kinds contains unsupported node kind: {unsupported_nodes[0]}")

    if set(node_kinds) != _GRAPH_NODE_KINDS:
        raise CapabilityCatalogError("graph.node_kinds does not match the constitutive schema-1 node set")
    if not isinstance(graph["discovery"], list) or not isinstance(graph["relations"], list):
        raise CapabilityCatalogError("graph.discovery and graph.relations must be arrays")
    references: set[str] = set()
    discovery_ids: set[tuple[str, str]] = set()
    for index, item in enumerate(graph["discovery"]):
        label = f"graph.discovery[{index}]"
        entry = _mapping(item, {"node_kind", "dimension_name", "description", "operators", "result"}, label)
        identity = (_text(entry["node_kind"], f"{label}.node_kind"), _text(entry["dimension_name"], f"{label}.dimension_name"))
        if identity in discovery_ids:
            raise CapabilityCatalogError("graph.discovery contains duplicate identities")
        discovery_ids.add(identity)
        if identity not in _GRAPH_DISCOVERY_IDENTITIES:
            raise CapabilityCatalogError(f"{label} has an unsupported schema-1 discovery identity")
        if identity[0] not in node_kinds:
            raise CapabilityCatalogError(f"{label}.node_kind is not admitted by graph.node_kinds")
        _text(entry["description"], f"{label}.description")
        operators = _text_list(entry["operators"], f"{label}.operators", nonempty=True)
        if set(operators) != _GRAPH_DISCOVERY_OPERATORS:
            raise CapabilityCatalogError(f"{label}.operators does not match the schema-1 discovery grammar")
        references.update(operators)
        if _text(entry["result"], f"{label}.result") != "opaque_graph_handle":
            raise CapabilityCatalogError(f"{label}.result is unsupported")
    relation_ids: set[tuple[str, str]] = set()
    for index, item in enumerate(graph["relations"]):
        label = f"graph.relations[{index}]"
        entry = _mapping(item, {"relation_class", "relation_name", "description", "source_kinds", "target_kinds", "operations"}, label)
        identity = (_text(entry["relation_class"], f"{label}.relation_class"), _text(entry["relation_name"], f"{label}.relation_name"))
        if identity in relation_ids:
            raise CapabilityCatalogError("graph.relations contains duplicate identities")
        relation_ids.add(identity)
        relation_class, relation_name = identity
        if relation_class == "semantic_identifier":
            if relation_name == "tags":
                raise CapabilityCatalogError("semantic_identifier/tags is not graph topology")
            expected_endpoints = (("semantic_object",), ("semantic_object", "semantic_region"))
        elif identity in _RELATION_ENDPOINTS:
            expected_endpoints = _RELATION_ENDPOINTS[identity]
        else:
            raise CapabilityCatalogError(f"{label} has an unsupported schema-1 relation identity")
        _text(entry["description"], f"{label}.description")
        source_kinds = _text_list(entry["source_kinds"], f"{label}.source_kinds", nonempty=True)
        target_kinds = _text_list(entry["target_kinds"], f"{label}.target_kinds", nonempty=True)
        if (set(source_kinds) != set(expected_endpoints[0]) or set(target_kinds) != set(expected_endpoints[1])):
            raise CapabilityCatalogError(f"{label} has incompatible schema-1 endpoints")
        if any(kind not in node_kinds for kind in (*source_kinds, *target_kinds)):
            raise CapabilityCatalogError(f"{label} references a node kind not admitted by graph.node_kinds")
        operations = _text_list(entry["operations"], f"{label}.operations", nonempty=True)
        if set(operations) != _GRAPH_RELATION_OPERATIONS:
            raise CapabilityCatalogError(f"{label}.operations does not match the schema-1 relation grammar")
        references.update(operations)
    return references, set(node_kinds), discovery_ids, relation_ids


def _validate_vector(value: Any) -> tuple[str, set[str]]:
    vector = _mapping(value, {"operator", "query", "targets"}, "vector")
    if vector["operator"] != "vector.semantic_similarity":
        raise CapabilityCatalogError("vector.operator is unsupported")
    query = _mapping(vector["query"], {"shape", "requirement", "segmentation", "truncation", "deterministic_enrichment"}, "vector.query")
    expected_query = {
        "shape": "string",
        "requirement": "exactly one non-empty string",
        "segmentation": False,
        "truncation": False,
        "deterministic_enrichment": False,
    }
    if query != expected_query:
        raise CapabilityCatalogError("vector.query does not match the schema-1 grammar")
    if not isinstance(vector["targets"], list):
        raise CapabilityCatalogError("vector.targets must be an array")
    target_ids: set[str] = set()
    for index, item in enumerate(vector["targets"]):
        label = f"vector.targets[{index}]"
        entry = _mapping(item, {"target_kind", "input"}, label)
        target_kind = _text(entry["target_kind"], f"{label}.target_kind")
        if target_kind in target_ids:
            raise CapabilityCatalogError("vector.targets contains duplicate identities")
        target_ids.add(target_kind)
        if target_kind not in _VECTOR_TARGET_INPUTS or entry["input"] != _VECTOR_TARGET_INPUTS[target_kind]:
            raise CapabilityCatalogError(f"{label} has an unsupported target/input pairing")
    return vector["operator"], target_ids


def _validate_operators(value: Any, references: set[str]) -> None:
    if not isinstance(value, dict):
        raise CapabilityCatalogError("operators must be an object")
    for name, item in value.items():
        _text(name, "operator name")
        if name not in _GLOBAL_OPERATOR_SURFACES:
            raise CapabilityCatalogError(f"operator {name!r} is unsupported")
        entry = _mapping(item, {"surface", "meaning"}, f"operators[{name!r}]")
        if _text(entry["surface"], f"operators[{name!r}].surface") != _GLOBAL_OPERATOR_SURFACES[name]:
            raise CapabilityCatalogError(f"operator {name!r} has an incompatible surface")
        _text(entry["meaning"], f"operators[{name!r}].meaning")
    if set(value) != set(_GLOBAL_OPERATOR_SURFACES):
        raise CapabilityCatalogError("operators does not match the exact schema-1 operator set")
    missing = sorted(references.difference(value))
    if missing:
        raise CapabilityCatalogError(f"referenced operator is absent from operators: {missing[0]}")


def _validate_schema_one(parsed: Any) -> None:
    envelope = _mapping(parsed, {"catalog_schema_version", "semantic_dimensions", "graph", "vector", "operators"}, "catalog")
    if envelope["catalog_schema_version"] != "1":
        raise CapabilityCatalogError("capability catalog schema version is unsupported")
    references, access_by_dimension = _validate_semantic_dimensions(envelope["semantic_dimensions"])
    graph_references, node_kinds, discovery_ids, relation_ids = _validate_graph(envelope["graph"])
    references.update(graph_references)
    vector_operator, vector_target_kinds = _validate_vector(envelope["vector"])
    references.add(vector_operator)
    if not _CONSTITUTIVE_DIMENSIONS.issubset(access_by_dimension):
        raise CapabilityCatalogError("catalog omits constitutive semantic dimensions")
    if not _CONSTITUTIVE_RELATIONS.issubset(relation_ids):
        raise CapabilityCatalogError("catalog omits constitutive graph relations")
    if not _CONSTITUTIVE_DISCOVERY_IDENTITIES.issubset(discovery_ids):
        raise CapabilityCatalogError("catalog omits constitutive graph discovery")
    parsed_text_access = access_by_dimension[("intrinsic", "parsed_text")]
    parsed_text_vector = ("vector.semantic_similarity", "complete_value") in parsed_text_access
    if parsed_text_vector != ("semantic_unit" in vector_target_kinds):
        raise CapabilityCatalogError("parsed_text vector access and semantic_unit target are inconsistent")
    _validate_operators(envelope["operators"], references)


def load_capability_catalog(path: str | Path) -> CapabilityCatalogArtifact:
    try:
        raw = Path(path).read_bytes()
    except OSError as exc:
        raise CapabilityCatalogError(f"could not read capability catalog: {exc}") from exc
    digest = "sha256:" + hashlib.sha256(raw).hexdigest()
    try:
        text = raw.decode("utf-8")
        parsed = json.loads(text, object_pairs_hook=_reject_duplicate_keys)
    except (UnicodeDecodeError, json.JSONDecodeError, _DuplicateObjectKey) as exc:
        raise CapabilityCatalogError(f"capability catalog is not valid unambiguous UTF-8 JSON: {exc}") from exc
    _validate_schema_one(parsed)
    return CapabilityCatalogArtifact(text, _freeze(parsed), digest)


__all__ = ["CapabilityCatalogArtifact", "CapabilityCatalogError", "load_capability_catalog"]