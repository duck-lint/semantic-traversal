"""Deterministic, retrieval-owned assembly of a bounded evidence packet.

This module intentionally does not know how to execute or hydrate retrieval.
Its only inputs are the completed hydrated execution and the one packet
capacity policy.  Occurrences remain surface-specific; payload de-duplication
is only a storage normalization.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping

from ...projection.graph import GraphHandle
from .hydration import (
    HydratedCanonicalTarget, HydratedExactResult, HydratedGraphDiscoveryResult,
    HydratedGraphRelationResult, HydratedLexicalResult, HydratedRequestResult,
    HydratedRetrievalResult, HydratedTemporalResult, HydratedVectorResult,
)
from ..config import PacketConfig


PACKET_CONTRACT_VERSION = "retrieval-packet-v1"
SELECTION_RULE_VERSION = "request-round-robin-v1"


class RetrievalPacketError(ValueError):
    """Hydrated evidence cannot be assembled into a valid packet."""


@dataclass(frozen=True)
class CanonicalTargetRef:
    target_kind: str
    identity: Any


@dataclass(frozen=True)
class CanonicalTargetPayload:
    target_ref: CanonicalTargetRef
    canonical_unit: Any | None = None
    canonical_object: Any | None = None
    canonical_region: Any | None = None
    owning_object_ref: CanonicalTargetRef | None = None
    graph_handle: GraphHandle | None = None


@dataclass(frozen=True)
class PacketOccurrence:
    request_ordinal: int
    occurrence_ordinal: int
    operator: str


@dataclass(frozen=True)
class ExactOccurrence(PacketOccurrence):
    unit_id: int
    target_ref: CanonicalTargetRef


@dataclass(frozen=True)
class LexicalOccurrence(PacketOccurrence):
    unit_id: int
    lexical_score: float
    target_ref: CanonicalTargetRef


@dataclass(frozen=True)
class TemporalOccurrence(PacketOccurrence):
    unit_id: int
    temporal_date: dt.date
    target_ref: CanonicalTargetRef


@dataclass(frozen=True)
class VectorOccurrence(PacketOccurrence):
    target_kind: str
    target_identity: int | str
    vector_score: float
    segment_ordinal: int
    target_ref: CanonicalTargetRef


@dataclass(frozen=True)
class GraphDiscoveryOccurrence(PacketOccurrence):
    graph_handle: GraphHandle
    graph_discovery_score: float
    target_ref: CanonicalTargetRef


@dataclass(frozen=True)
class GraphRelationOccurrence(PacketOccurrence):
    edge_id: int
    relation_class: str
    relation_name: str
    source_handle: GraphHandle
    target_handle: GraphHandle
    source_target_ref: CanonicalTargetRef
    target_target_ref: CanonicalTargetRef


@dataclass(frozen=True)
class RequestCoverage:
    ordinal: int
    request: Mapping[str, Any]
    operator: str
    status: str
    failure: Mapping[str, Any] | None
    returned_occurrences: int | None
    selected_occurrences: int
    omitted_occurrences: int
    packet_truncated: bool
    exhaustive_exact_match_count: int | None


@dataclass(frozen=True)
class PacketCoverage:
    max_occurrences: int
    selection_rule: str
    total_returned_occurrences: int
    selected_occurrences: int
    omitted_occurrences: int
    packet_truncated: bool


@dataclass(frozen=True)
class RemovalRecord:
    execution_id: str
    request_ordinal: int
    occurrence_ordinal: int
    operator: str
    native_identity: Mapping[str, Any]
    stage: str = "packet_assembly"
    reason: str = "max_occurrences_exhausted"
    rule_authority: str = "runtime_config.packet.max_occurrences"
    selection_rule: str = SELECTION_RULE_VERSION


@dataclass(frozen=True)
class RetrievalPacket:
    packet_contract_version: str
    selection_rule: str
    execution_id: str
    conformance_id: str
    retrieval_run_id: str
    retrieval_package_id: str
    execution_status: str
    execution_failure: Mapping[str, Any] | None
    requests: tuple[RequestCoverage, ...]
    selected_occurrences: tuple[PacketOccurrence, ...]
    canonical_payloads: Mapping[CanonicalTargetRef, CanonicalTargetPayload]
    coverage: PacketCoverage


@dataclass(frozen=True)
class PacketAssemblyResult:
    packet: RetrievalPacket
    removals: tuple[RemovalRecord, ...]


@dataclass(frozen=True)
class _Candidate:
    request: HydratedRequestResult
    occurrence_ordinal: int
    occurrence: PacketOccurrence
    native_identity: Mapping[str, Any]
    targets: tuple[HydratedCanonicalTarget, ...]


def _ref(target: HydratedCanonicalTarget) -> CanonicalTargetRef:
    if target.target_kind == "semantic_unit":
        return CanonicalTargetRef(target.target_kind, target.canonical_unit.unit_id)
    if target.target_kind == "semantic_object":
        return CanonicalTargetRef(target.target_kind, target.canonical_object.source_object_uuid)
    if target.target_kind == "semantic_region":
        reference = target.canonical_region.reference
        return CanonicalTargetRef(target.target_kind, (reference.source_object_uuid, reference.region_path))
    if target.target_kind == "scope":
        return CanonicalTargetRef(target.target_kind, target.graph_handle)
    raise RetrievalPacketError(f"unsupported hydrated target kind: {target.target_kind!r}")


def _identity(value: Mapping[str, Any]) -> Mapping[str, Any]:
    return MappingProxyType(dict(value))


def _candidate(request: HydratedRequestResult, index: int, occurrence: PacketOccurrence,
               native_identity: Mapping[str, Any], targets: tuple[HydratedCanonicalTarget, ...]) -> _Candidate:
    return _Candidate(request, index, occurrence, _identity(native_identity), targets)


def _lane(request: HydratedRequestResult) -> tuple[_Candidate, ...]:
    if request.status != "succeeded":
        return ()
    result = request.result
    candidates: list[_Candidate] = []
    for index, hit in enumerate(result.hits if isinstance(result, (HydratedExactResult, HydratedLexicalResult, HydratedTemporalResult, HydratedVectorResult, HydratedGraphDiscoveryResult)) else result.occurrences):
        if isinstance(result, HydratedExactResult):
            occurrence = ExactOccurrence(request.ordinal, index, request.operator, hit.unit_id, _ref(hit.target))
            candidates.append(_candidate(request, index, occurrence, {"target_kind": "semantic_unit", "unit_id": hit.unit_id}, (hit.target,)))
        elif isinstance(result, HydratedLexicalResult):
            occurrence = LexicalOccurrence(request.ordinal, index, request.operator, hit.unit_id, hit.score, _ref(hit.target))
            candidates.append(_candidate(request, index, occurrence, {"target_kind": "semantic_unit", "unit_id": hit.unit_id}, (hit.target,)))
        elif isinstance(result, HydratedTemporalResult):
            occurrence = TemporalOccurrence(request.ordinal, index, request.operator, hit.unit_id, hit.date, _ref(hit.target))
            candidates.append(_candidate(request, index, occurrence, {"target_kind": "semantic_unit", "unit_id": hit.unit_id, "temporal_date": hit.date.isoformat()}, (hit.target,)))
        elif isinstance(result, HydratedVectorResult):
            occurrence = VectorOccurrence(request.ordinal, index, request.operator, hit.target_kind, hit.target_identity, hit.score, hit.segment_ordinal, _ref(hit.target))
            candidates.append(_candidate(request, index, occurrence, {"target_kind": hit.target_kind, "target_identity": hit.target_identity, "segment_ordinal": hit.segment_ordinal}, (hit.target,)))
        elif isinstance(result, HydratedGraphDiscoveryResult):
            occurrence = GraphDiscoveryOccurrence(request.ordinal, index, request.operator, hit.node, hit.score, _ref(hit.target))
            candidates.append(_candidate(request, index, occurrence, {"graph_handle": hit.node}, (hit.target,)))
        elif isinstance(result, HydratedGraphRelationResult):
            occurrence = GraphRelationOccurrence(request.ordinal, index, request.operator, hit.edge_id, hit.relation_class, hit.relation_name, hit.source.graph_handle, hit.target.graph_handle, _ref(hit.source), _ref(hit.target))
            candidates.append(_candidate(request, index, occurrence, {"edge_id": hit.edge_id, "relation_class": hit.relation_class, "relation_name": hit.relation_name, "source_handle": hit.source.graph_handle, "target_handle": hit.target.graph_handle}, (hit.source, hit.target)))
        else:
            raise RetrievalPacketError(f"successful request has unsupported hydrated result: {type(result).__name__}")
    return tuple(candidates)


def _payloads(candidates: tuple[_Candidate, ...]) -> Mapping[CanonicalTargetRef, CanonicalTargetPayload]:
    payloads: dict[CanonicalTargetRef, CanonicalTargetPayload] = {}

    def add(target: HydratedCanonicalTarget) -> None:
        target_ref = _ref(target)
        if target_ref in payloads:
            return
        if target.target_kind in {"semantic_unit", "semantic_region"}:
            owner = HydratedCanonicalTarget("semantic_object", canonical_object=target.owning_object)
            add(owner)
            payloads[target_ref] = CanonicalTargetPayload(
                target_ref,
                canonical_unit=target.canonical_unit,
                canonical_region=target.canonical_region,
                owning_object_ref=_ref(owner),
                graph_handle=target.graph_handle,
            )
        elif target.target_kind == "semantic_object":
            payloads[target_ref] = CanonicalTargetPayload(target_ref, canonical_object=target.canonical_object, graph_handle=target.graph_handle)
        elif target.target_kind == "scope":
            payloads[target_ref] = CanonicalTargetPayload(target_ref, graph_handle=target.graph_handle)
        else:
            raise RetrievalPacketError(f"unsupported selected target kind: {target.target_kind!r}")

    for candidate in candidates:
        for target in candidate.targets:
            add(target)
    return MappingProxyType(payloads)


def assemble_retrieval_packet(hydrated_result: HydratedRetrievalResult, packet_config: PacketConfig) -> PacketAssemblyResult:
    """Assemble one bounded packet without I/O, retrieval, hydration, or providers."""
    if not isinstance(packet_config, PacketConfig):
        raise RetrievalPacketError("packet_config must be PacketConfig")
    lanes = tuple(_lane(request) for request in sorted(hydrated_result.requests, key=lambda item: item.ordinal))
    candidates = tuple(candidate for lane in lanes for candidate in lane)
    ordered: list[_Candidate] = []
    for round_index in range(max((len(lane) for lane in lanes), default=0)):
        ordered.extend(lane[round_index] for lane in lanes if round_index < len(lane))
    selected = tuple(ordered[:packet_config.max_occurrences])
    omitted = tuple(ordered[packet_config.max_occurrences:])
    selected_by_request = {request.ordinal: 0 for request in hydrated_result.requests}
    for candidate in selected:
        selected_by_request[candidate.request.ordinal] += 1
    request_records = tuple(
        RequestCoverage(
            request.ordinal, request.request, request.operator, request.status, request.failure,
            len(lane) if request.status == "succeeded" else None,
            selected_by_request[request.ordinal],
            len(lane) - selected_by_request[request.ordinal] if request.status == "succeeded" else 0,
            request.status == "succeeded" and len(lane) > selected_by_request[request.ordinal],
            len(lane) if request.status == "succeeded" and request.operator == "exact.equals" else None,
        )
        for request, lane in zip(sorted(hydrated_result.requests, key=lambda item: item.ordinal), lanes)
    )
    total = len(candidates)
    coverage = PacketCoverage(packet_config.max_occurrences, SELECTION_RULE_VERSION, total, len(selected), len(omitted), bool(omitted))
    if len(selected) > packet_config.max_occurrences or total != len(selected) + len(omitted):
        raise RetrievalPacketError("packet coverage arithmetic is inconsistent")
    removals = tuple(RemovalRecord(hydrated_result.execution_id, item.request.ordinal, item.occurrence_ordinal, item.request.operator, item.native_identity) for item in omitted)
    packet = RetrievalPacket(
        PACKET_CONTRACT_VERSION,
        SELECTION_RULE_VERSION,
        hydrated_result.execution_id,
        hydrated_result.conformance_id,
        hydrated_result.retrieval_run_id,
        hydrated_result.retrieval_package_id,
        hydrated_result.status,
        hydrated_result.execution_failure,
        request_records,
        tuple(item.occurrence for item in selected),
        _payloads(selected),
        coverage,
    )
    return PacketAssemblyResult(packet, removals)


__all__ = [
    "PACKET_CONTRACT_VERSION", "SELECTION_RULE_VERSION", "RetrievalPacketError",
    "CanonicalTargetRef", "CanonicalTargetPayload", "PacketOccurrence", "ExactOccurrence",
    "LexicalOccurrence", "TemporalOccurrence", "VectorOccurrence", "GraphDiscoveryOccurrence", "GraphRelationOccurrence",
    "RequestCoverage", "PacketCoverage", "RemovalRecord", "RetrievalPacket", "PacketAssemblyResult",
    "assemble_retrieval_packet",
]
