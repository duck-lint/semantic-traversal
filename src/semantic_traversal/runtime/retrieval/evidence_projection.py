"""Deterministic model-facing evidence derived from selected hydration.

This module is deliberately a one-way representation boundary.  The complete
``HydratedCandidateSelection`` remains attached to ``EvidenceProjection`` so
that omissions in the compact representation never become omissions of
authority.
"""

from __future__ import annotations

import datetime as dt
import json
import math
from dataclasses import dataclass, fields, is_dataclass
from typing import Any, Mapping

from ...build.canonical import (
    CanonicalObject,
    CanonicalObjectRelation,
    CanonicalRegionReference,
    CanonicalRelation,
)
from ...build.parser import Embed, FrontmatterField
from ...projection.graph import GraphHandle
from .candidate_hydration import (
    CANDIDATE_HYDRATION_CONTRACT_VERSION,
    HydratedCandidateSelection,
    HydratedObjectTarget,
    HydratedRegionTarget,
    HydratedScopeTarget,
    HydratedUnitTarget,
)
from .candidates import (
    Candidate,
    CandidateRef,
    ExactSupport,
    GraphDiscoverySupport,
    LexicalSupport,
    RelationEvidence,
    TemporalSupport,
    VectorSupport,
)
from .selection import CANDIDATE_SELECTION_CONTRACT_VERSION


EVIDENCE_PROJECTION_CONTRACT_VERSION = "evidence-projection-v2"


class EvidenceProjectionError(ValueError):
    """Canonical hydration cannot be represented without semantic loss."""


@dataclass(frozen=True)
class EvidenceRequest:
    ordinal: int
    request: Mapping[str, Any]
    status: str
    returned_occurrences: int | None
    failure: Mapping[str, Any] | None


@dataclass(frozen=True)
class EvidenceCoverage:
    execution_status: str
    execution_failure: Mapping[str, Any] | None
    workspace_candidate_count: int
    selected_candidate_count: int
    omitted_candidate_count: int
    selection_truncated: bool
    workspace_relation_count: int
    selected_relation_count: int
    omitted_relation_count: int


@dataclass(frozen=True)
class EvidenceObjectContext:
    source_object_uuid: str
    source_path: str
    path_hierarchy: tuple[str, ...]
    semantic_identifiers: tuple[Mapping[str, Any], ...]
    relations: tuple[Mapping[str, Any], ...]


@dataclass(frozen=True)
class EvidenceCandidate:
    target_ref: CandidateRef
    object_ref: str | None
    source_local_order: int | None
    region_address: tuple[Mapping[str, Any], ...]
    level: int | None
    address_text: str | None
    parsed_text: str | None
    relations: tuple[Mapping[str, Any], ...]
    embeds: tuple[Mapping[str, Any], ...]
    supports: tuple[Mapping[str, Any], ...]


@dataclass(frozen=True)
class EvidenceRetrievalRelation:
    request_ordinal: int
    occurrence_ordinal: int
    edge_id: int
    relation_class: str
    relation_name: str
    source: CandidateRef
    target: CandidateRef


@dataclass(frozen=True)
class EvidenceProjection:
    contract_version: str
    source: HydratedCandidateSelection
    requests: tuple[EvidenceRequest, ...]
    coverage: EvidenceCoverage
    object_contexts: tuple[EvidenceObjectContext, ...]
    candidates: tuple[EvidenceCandidate, ...]
    retrieval_relations: tuple[EvidenceRetrievalRelation, ...]


def _fail(message: str) -> EvidenceProjectionError:
    return EvidenceProjectionError(message)


def _value(value: Any) -> Any:
    """Encode authored values without erasing native scalar distinctions."""
    if value is None:
        return {"type": "null"}
    if isinstance(value, bool):
        return {"type": "bool", "value": value}
    if isinstance(value, int):
        return {"type": "int", "value": value}
    if isinstance(value, float):
        if not math.isfinite(value):
            raise _fail("non-finite authored value cannot be projected")
        return {"type": "float", "value": value}
    if isinstance(value, dt.datetime):
        return {"type": "datetime", "value": value.isoformat()}
    if isinstance(value, dt.date):
        return {"type": "date", "value": value.isoformat()}
    if isinstance(value, str):
        return {"type": "string", "value": value}
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise _fail("authored mapping contains a non-string key")
        return {"type": "mapping", "value": {key: _value(item) for key, item in value.items()}}
    if isinstance(value, (list, tuple)):
        return {"type": "list", "value": [_value(item) for item in value]}
    raise _fail(f"unsupported authored value type: {type(value).__name__}")


def _field(field: FrontmatterField) -> Mapping[str, Any]:
    if (
        not isinstance(field, FrontmatterField)
        or not isinstance(field.name, str)
        or not field.name
        or field.state not in {"absent", "present_blank", "present_value"}
    ):
        raise _fail("malformed semantic identifier field")
    result: dict[str, Any] = {"name": field.name, "state": field.state}
    if field.state == "present_value":
        result["value"] = _value(field.value)
    return result


def _ref(ref: CandidateRef) -> Mapping[str, Any]:
    if not isinstance(ref, CandidateRef):
        raise _fail("malformed candidate reference")
    return {"target_kind": ref.target_kind, "identity": [_plain(item) for item in ref.identity]}


def _plain(value: Any) -> Any:
    if is_dataclass(value):
        return {field.name: _plain(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, tuple):
        return [_plain(item) for item in value]
    if isinstance(value, GraphHandle):
        return {"node_kind": value.node_kind, "identity": _plain(value.identity)}
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, dt.datetime):
        return {"type": "datetime", "value": value.isoformat()}
    if isinstance(value, dt.date):
        return {"type": "date", "value": value.isoformat()}
    return value


def _region_ref(reference: CanonicalRegionReference) -> Mapping[str, Any]:
    return {"source_object_uuid": reference.source_object_uuid, "region_path": list(reference.region_path)}


def _relation(relation: CanonicalRelation | CanonicalObjectRelation) -> Mapping[str, Any]:
    result: dict[str, Any] = {
        "relation_name": relation.relation_name,
        "origin": relation.origin,
        "source_field": relation.source_field,
        "authored_target": relation.authored_target,
        "authored_label": relation.authored_label,
        "authored_region_fragment": relation.authored_region_fragment,
        "target_object_uuid": relation.target_object_uuid,
        "target_region": _region_ref(relation.target_region) if relation.target_region else None,
    }
    return result


def _object_relations(object_: CanonicalObject) -> tuple[Mapping[str, Any], ...]:
    """Project object-owned relations and enforce their canonical ownership."""
    for relation in object_.relations:
        if relation.origin != "frontmatter":
            raise _fail("object-owned relation does not have frontmatter origin")
    return tuple(_relation(relation) for relation in object_.relations)


def _embed(embed: Embed) -> Mapping[str, Any]:
    return {"target": embed.target, "label": embed.label, "target_region_fragment": embed.target_region_fragment}


def _support(support: Any) -> Mapping[str, Any]:
    result: dict[str, Any] = {
        "surface": (
            "exact" if isinstance(support, ExactSupport) else
            "lexical" if isinstance(support, LexicalSupport) else
            "temporal" if isinstance(support, TemporalSupport) else
            "vector" if isinstance(support, VectorSupport) else
            "graph_discovery" if isinstance(support, GraphDiscoverySupport) else
            None
        ),
        "request_ordinal": support.request_ordinal,
        "occurrence_ordinal": support.occurrence_ordinal,
        "operator": support.operator,
    }
    if result["surface"] is None:
        raise _fail("unsupported candidate support")
    if isinstance(support, TemporalSupport):
        result["represented_date"] = _value(support.temporal_date)
    elif isinstance(support, VectorSupport):
        result["segment_ordinal"] = support.segment_ordinal
    elif isinstance(support, GraphDiscoverySupport):
        result["graph_handle"] = _plain(support.graph_handle)
    return result


def _address(object_: CanonicalObject, references: tuple[CanonicalRegionReference, ...]) -> tuple[Mapping[str, Any], ...]:
    regions = {region.reference.region_path: region for region in object_.regions}
    output: list[Mapping[str, Any]] = []
    for reference in references:
        if reference.source_object_uuid != object_.source_object_uuid or reference.region_path not in regions:
            raise _fail("unit region path cannot be resolved against its owning object")
        region = regions[reference.region_path]
        output.append({
            "source_object_uuid": reference.source_object_uuid,
            "region_path": list(reference.region_path),
            "level": region.level,
            "address_text": region.address_text,
        })
    return tuple(output)


def _candidate(candidate: Candidate, target: Any) -> EvidenceCandidate:
    kind = candidate.target_ref.target_kind
    if kind == "semantic_unit" and not isinstance(target, HydratedUnitTarget):
        raise _fail("candidate semantic_unit does not match hydrated target")
    if kind == "semantic_object" and not isinstance(target, HydratedObjectTarget):
        raise _fail("candidate semantic_object does not match hydrated target")
    if kind == "semantic_region" and not isinstance(target, HydratedRegionTarget):
        raise _fail("candidate semantic_region does not match hydrated target")
    if kind == "scope" and not isinstance(target, HydratedScopeTarget):
        raise _fail("candidate scope does not match hydrated target")
    if kind not in {"semantic_unit", "semantic_object", "semantic_region", "scope"}:
        raise _fail("unsupported candidate target kind")
    object_ref: str | None = None
    source_local_order: int | None = None
    address: tuple[Mapping[str, Any], ...] = ()
    level: int | None = None
    address_text: str | None = None
    parsed_text: str | None = None
    relations: tuple[Mapping[str, Any], ...] = ()
    embeds: tuple[Mapping[str, Any], ...] = ()
    if isinstance(target, HydratedUnitTarget):
        unit, owner = target.unit, target.owning_object
        if candidate.target_ref.identity != (unit.unit_id,) or unit.source_object_uuid != owner.source_object_uuid:
            raise _fail("unit owning-object identity mismatch")
        if unit.inherited_identifiers != owner.admitted_identifiers:
            raise _fail("unit inherited identifiers disagree with owning object")
        unit_frontmatter = tuple(_relation(relation) for relation in unit.relations if relation.origin == "frontmatter")
        if unit_frontmatter != _object_relations(owner):
            raise _fail("unit frontmatter relations disagree with owning object")
        if any(relation.origin not in {"frontmatter", "body"} for relation in unit.relations):
            raise _fail("unit relation has unsupported authored origin")
        object_ref = owner.source_object_uuid
        source_local_order = unit.source_local_order
        address = _address(owner, unit.region_path)
        parsed_text, relations, embeds = unit.parsed_text, tuple(_relation(item) for item in unit.relations if item.origin == "body"), tuple(_embed(item) for item in unit.embeds)
    elif isinstance(target, HydratedRegionTarget):
        region, owner = target.region, target.owning_object
        if candidate.target_ref.identity != (region.reference.source_object_uuid, region.reference.region_path) or region.reference.source_object_uuid != owner.source_object_uuid:
            raise _fail("region owning-object identity mismatch")
        object_ref = owner.source_object_uuid
        level, address_text, parsed_text = region.level, region.address_text, region.parsed_text
        address = ({"source_object_uuid": region.reference.source_object_uuid, "region_path": list(region.reference.region_path), "level": region.level, "address_text": region.address_text},)
    elif isinstance(target, HydratedObjectTarget):
        if candidate.target_ref.identity != (target.object.source_object_uuid,):
            raise _fail("candidate object identity disagrees with hydrated object")
        object_ref = target.object.source_object_uuid
    elif isinstance(target, HydratedScopeTarget):
        if candidate.target_ref.identity != target.handle.identity:
            raise _fail("candidate scope identity disagrees with hydrated scope")
        pass
    return EvidenceCandidate(candidate.target_ref, object_ref, source_local_order, address, level, address_text, parsed_text, relations, embeds, tuple(_support(item) for item in candidate.supports))


def project_evidence(hydrated_selection: HydratedCandidateSelection) -> EvidenceProjection:
    if not isinstance(hydrated_selection, HydratedCandidateSelection) or hydrated_selection.contract_version != CANDIDATE_HYDRATION_CONTRACT_VERSION:
        raise _fail("unsupported candidate hydration contract")
    selection = hydrated_selection.selection
    if selection.contract_version not in {"candidate-selection-v2", CANDIDATE_SELECTION_CONTRACT_VERSION}:
        raise _fail("unsupported candidate selection contract")
    if len(hydrated_selection.hydrated_candidates) != len(selection.selected_candidates):
        raise _fail("hydrated candidate count disagrees with selection")
    if tuple(item.candidate for item in hydrated_selection.hydrated_candidates) != selection.selected_candidates:
        raise _fail("hydrated candidates disagree with selected candidate order or values")

    requests = tuple(EvidenceRequest(item.ordinal, item.request, item.status, item.returned_occurrences, item.failure) for item in selection.requests)
    coverage = EvidenceCoverage(
        selection.execution_status, selection.execution_failure,
        selection.coverage.workspace_candidate_count, selection.coverage.selected_candidate_count,
        selection.coverage.omitted_candidate_count, selection.coverage.selection_truncated,
        selection.coverage.workspace_relation_count, selection.coverage.selected_relation_count,
        selection.coverage.omitted_relation_count,
    )
    contexts: list[EvidenceObjectContext] = []
    context_by_uuid: dict[str, EvidenceObjectContext] = {}
    projected: list[EvidenceCandidate] = []
    selected_refs: set[CandidateRef] = set()
    for hydrated in hydrated_selection.hydrated_candidates:
        item = _candidate(hydrated.candidate, hydrated.canonical_target)
        projected.append(item)
        selected_refs.add(hydrated.candidate.target_ref)
        owner: CanonicalObject | None = None
        if isinstance(hydrated.canonical_target, HydratedUnitTarget): owner = hydrated.canonical_target.owning_object
        elif isinstance(hydrated.canonical_target, HydratedRegionTarget): owner = hydrated.canonical_target.owning_object
        elif isinstance(hydrated.canonical_target, HydratedObjectTarget): owner = hydrated.canonical_target.object
        if owner is not None and owner.source_object_uuid not in context_by_uuid:
            context = EvidenceObjectContext(owner.source_object_uuid, owner.source_path, owner.path_hierarchy, tuple(_field(field) for field in owner.admitted_identifiers), _object_relations(owner))
            context_by_uuid[owner.source_object_uuid] = context
            contexts.append(context)
        elif owner is not None and context_by_uuid[owner.source_object_uuid] != EvidenceObjectContext(owner.source_object_uuid, owner.source_path, owner.path_hierarchy, tuple(_field(field) for field in owner.admitted_identifiers), _object_relations(owner)):
            raise _fail("conflicting normalized object context")

    relations: list[EvidenceRetrievalRelation] = []
    for relation in selection.selected_relation_evidence:
        if relation.source_ref not in selected_refs or relation.target_ref not in selected_refs:
            raise _fail("retrieval relation references an unselected endpoint")
        relations.append(EvidenceRetrievalRelation(relation.request_ordinal, relation.occurrence_ordinal, relation.edge_id, relation.relation_class, relation.relation_name, relation.source_ref, relation.target_ref))
    return EvidenceProjection(EVIDENCE_PROJECTION_CONTRACT_VERSION, hydrated_selection, requests, coverage, tuple(contexts), tuple(projected), tuple(relations))


def evidence_projection_json(projection: EvidenceProjection) -> dict[str, Any]:
    if not isinstance(projection, EvidenceProjection) or projection.contract_version != EVIDENCE_PROJECTION_CONTRACT_VERSION:
        raise _fail("unsupported evidence projection contract")
    return {
        "contract_version": projection.contract_version,
        "coverage": _plain(projection.coverage),
        "requests": [_plain(item) for item in projection.requests],
        "object_contexts": [_plain(item) for item in projection.object_contexts],
        "candidates": [_plain(item) for item in projection.candidates],
        "retrieval_relations": [_plain(item) for item in projection.retrieval_relations],
    }


def serialize_evidence_projection(projection: EvidenceProjection) -> str:
    return json.dumps(evidence_projection_json(projection), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def measure_evidence_projection(projection: EvidenceProjection) -> Mapping[str, int]:
    serialized = serialize_evidence_projection(projection)
    return {
        "serialized_characters": len(serialized),
        "serialized_utf8_bytes": len(serialized.encode("utf-8")),
        "request_count": len(projection.requests),
        "object_context_count": len(projection.object_contexts),
        "candidate_count": len(projection.candidates),
        "retrieval_relation_count": len(projection.retrieval_relations),
    }


__all__ = [
    "EVIDENCE_PROJECTION_CONTRACT_VERSION", "EvidenceProjectionError", "EvidenceRequest",
    "EvidenceCoverage", "EvidenceObjectContext", "EvidenceCandidate",
    "EvidenceRetrievalRelation", "EvidenceProjection", "project_evidence",
    "evidence_projection_json", "serialize_evidence_projection", "measure_evidence_projection",
]
