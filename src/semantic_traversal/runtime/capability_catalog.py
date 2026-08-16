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


def _text_list(value: Any, label: str, *, nonempty: bool = False) -> None:
    if not isinstance(value, list):
        raise CapabilityCatalogError(f"{label} must be an array")
    if nonempty and not value:
        raise CapabilityCatalogError(f"{label} must not be empty")
    if any(not isinstance(item, str) or not item.strip() for item in value):
        raise CapabilityCatalogError(f"{label} must contain only non-empty text")
    if len(set(value)) != len(value):
        raise CapabilityCatalogError(f"{label} contains duplicate values")


def _validate_value(value: Any, label: str) -> None:
    envelope = _mapping(value, {"shapes"}, label)
    shapes = envelope["shapes"]
    if not isinstance(shapes, list):
        raise CapabilityCatalogError(f"{label}.shapes must be an array")
    seen_shapes: set[str] = set()
    for index, shape_value in enumerate(shapes):
        shape_label = f"{label}.shapes[{index}]"
        if not isinstance(shape_value, dict) or "shape" not in shape_value:
            raise CapabilityCatalogError(f"{shape_label} is malformed")
        shape = _text(shape_value["shape"], f"{shape_label}.shape")
        if shape in seen_shapes:
            raise CapabilityCatalogError(f"{label} contains duplicate shape identities")
        seen_shapes.add(shape)
        if shape == "scalar":
            shape_mapping = _mapping(shape_value, {"shape", "domains"}, shape_label)
            _text_list(shape_mapping["domains"], f"{shape_label}.domains", nonempty=True)
        elif shape == "sequence":
            shape_mapping = _mapping(shape_value, {"shape", "member_domains"}, shape_label)
            _text_list(shape_mapping["member_domains"], f"{shape_label}.member_domains", nonempty=True)
        elif shape == "ordered_sequence":
            shape_mapping = _mapping(shape_value, {"shape", "domains"}, shape_label)
            _text_list(shape_mapping["domains"], f"{shape_label}.domains", nonempty=True)
        else:
            raise CapabilityCatalogError(f"{shape_label}.shape is unsupported")


def _validate_access(access: Any, label: str) -> tuple[str, str]:
    if not isinstance(access, dict) or "operator" not in access or "target" not in access:
        raise CapabilityCatalogError(f"{label} is malformed")
    operator = _text(access["operator"], f"{label}.operator")
    target = _text(access["target"], f"{label}.target")
    if target not in {"complete_value", "member"}:
        raise CapabilityCatalogError(f"{label}.target is unsupported")
    if "domains" in access:
        entry = _mapping(access, {"operator", "target", "domains"}, label)
        _text_list(entry["domains"], f"{label}.domains", nonempty=True)
    elif "operand" in access:
        entry = _mapping(access, {"operator", "target", "operand"}, label)
        operand = _mapping(entry["operand"], {"shape", "member_domains"}, f"{label}.operand")
        if operand["shape"] != "ordered_sequence":
            raise CapabilityCatalogError(f"{label}.operand.shape is unsupported")
        _text_list(operand["member_domains"], f"{label}.operand.member_domains", nonempty=True)
    else:
        raise CapabilityCatalogError(f"{label} has no accepted operand representation")
    return operator, target


def _validate_semantic_dimensions(value: Any) -> None:
    if not isinstance(value, list):
        raise CapabilityCatalogError("semantic_dimensions must be an array")
    identities: set[tuple[str, str]] = set()
    for index, item in enumerate(value):
        label = f"semantic_dimensions[{index}]"
        entry = _mapping(item, {"field_class", "field_name", "description", "value", "access"}, label)
        identity = (_text(entry["field_class"], f"{label}.field_class"), _text(entry["field_name"], f"{label}.field_name"))
        if identity in identities:
            raise CapabilityCatalogError("semantic_dimensions contains duplicate identities")
        identities.add(identity)
        _text(entry["description"], f"{label}.description")
        _validate_value(entry["value"], f"{label}.value")
        if not isinstance(entry["access"], list):
            raise CapabilityCatalogError(f"{label}.access must be an array")
        access_identities: set[tuple[str, str]] = set()
        for access_index, access in enumerate(entry["access"]):
            access_identity = _validate_access(access, f"{label}.access[{access_index}]")
            if access_identity in access_identities:
                raise CapabilityCatalogError(f"{label}.access contains duplicate identities")
            access_identities.add(access_identity)


def _validate_graph(value: Any) -> None:
    graph = _mapping(value, {"node_kinds", "discovery", "relations"}, "graph")
    _text_list(graph["node_kinds"], "graph.node_kinds")
    if not isinstance(graph["discovery"], list) or not isinstance(graph["relations"], list):
        raise CapabilityCatalogError("graph.discovery and graph.relations must be arrays")
    discovery_ids: set[tuple[str, str]] = set()
    for index, item in enumerate(graph["discovery"]):
        label = f"graph.discovery[{index}]"
        entry = _mapping(item, {"node_kind", "dimension_name", "description", "operators", "result"}, label)
        identity = (_text(entry["node_kind"], f"{label}.node_kind"), _text(entry["dimension_name"], f"{label}.dimension_name"))
        if identity in discovery_ids:
            raise CapabilityCatalogError("graph.discovery contains duplicate identities")
        discovery_ids.add(identity)
        _text(entry["description"], f"{label}.description")
        _text_list(entry["operators"], f"{label}.operators", nonempty=True)
        _text(entry["result"], f"{label}.result")
    relation_ids: set[tuple[str, str]] = set()
    for index, item in enumerate(graph["relations"]):
        label = f"graph.relations[{index}]"
        entry = _mapping(item, {"relation_class", "relation_name", "description", "source_kinds", "target_kinds", "operations"}, label)
        identity = (_text(entry["relation_class"], f"{label}.relation_class"), _text(entry["relation_name"], f"{label}.relation_name"))
        if identity in relation_ids:
            raise CapabilityCatalogError("graph.relations contains duplicate identities")
        relation_ids.add(identity)
        _text(entry["description"], f"{label}.description")
        _text_list(entry["source_kinds"], f"{label}.source_kinds", nonempty=True)
        _text_list(entry["target_kinds"], f"{label}.target_kinds", nonempty=True)
        _text_list(entry["operations"], f"{label}.operations", nonempty=True)


def _validate_vector(value: Any) -> None:
    vector = _mapping(value, {"operator", "query", "targets"}, "vector")
    _text(vector["operator"], "vector.operator")
    query = _mapping(vector["query"], {"shape", "requirement", "segmentation", "truncation", "deterministic_enrichment"}, "vector.query")
    _text(query["shape"], "vector.query.shape")
    _text(query["requirement"], "vector.query.requirement")
    for key in ("segmentation", "truncation", "deterministic_enrichment"):
        if not isinstance(query[key], bool):
            raise CapabilityCatalogError(f"vector.query.{key} must be boolean")
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
        _text(entry["input"], f"{label}.input")


def _validate_operators(value: Any) -> None:
    if not isinstance(value, dict):
        raise CapabilityCatalogError("operators must be an object")
    for name, item in value.items():
        _text(name, "operator name")
        entry = _mapping(item, {"surface", "meaning"}, f"operators[{name!r}]")
        _text(entry["surface"], f"operators[{name!r}].surface")
        _text(entry["meaning"], f"operators[{name!r}].meaning")


def _validate_schema_one(parsed: Any) -> None:
    envelope = _mapping(parsed, {"catalog_schema_version", "semantic_dimensions", "graph", "vector", "operators"}, "catalog")
    if envelope["catalog_schema_version"] != "1":
        raise CapabilityCatalogError("capability catalog schema version is unsupported")
    _validate_semantic_dimensions(envelope["semantic_dimensions"])
    _validate_graph(envelope["graph"])
    _validate_vector(envelope["vector"])
    _validate_operators(envelope["operators"])


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