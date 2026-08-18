"""Pure composition of completed retrieval execution evidence.

This module deliberately stops at access evidence.  It does not hydrate
canonical payloads, select candidates, or apply a capacity policy.
"""

from __future__ import annotations

import datetime as dt
import json
import math
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping

from ...projection.graph import GraphHandle
from .execution import RetrievalExecutionResult


CANDIDATE_WORKSPACE_CONTRACT_VERSION = "candidate-workspace-v1"
_TARGET_KINDS = {"semantic_unit", "semantic_object", "semantic_region", "scope"}
_UNARY_OPERATORS = {
    "exact.equals": "exact",
    "lexical.terms": "lexical",
    "lexical.phrase": "lexical",
    "temporal.earliest": "temporal",
    "temporal.latest": "temporal",
    "temporal.before": "temporal",
    "temporal.after": "temporal",
    "temporal.between": "temporal",
    "temporal.ordered": "temporal",
    "graph.discovery.terms": "graph_discovery",
    "graph.discovery.phrase": "graph_discovery",
    "vector.semantic_similarity": "vector",
    "graph.relation_occurrence_lookup": "graph_relation_occurrences",
}


class CandidateCompositionError(ValueError):
    """Successful execution evidence cannot be represented losslessly."""


@dataclass(frozen=True)
class CandidateRef:
    target_kind: str
    identity: tuple[Any, ...]


@dataclass(frozen=True)
class CandidateSupport:
    request_ordinal: int
    occurrence_ordinal: int
    operator: str


@dataclass(frozen=True)
class ExactSupport(CandidateSupport):
    unit_id: int


@dataclass(frozen=True)
class LexicalSupport(CandidateSupport):
    unit_id: int
    lexical_score: float


@dataclass(frozen=True)
class TemporalSupport(CandidateSupport):
    unit_id: int
    temporal_date: dt.date


@dataclass(frozen=True)
class VectorSupport(CandidateSupport):
    target_kind: str
    target_identity: Any
    vector_score: float
    segment_ordinal: int


@dataclass(frozen=True)
class GraphDiscoverySupport(CandidateSupport):
    graph_handle: GraphHandle
    graph_discovery_score: float


@dataclass(frozen=True)
class Candidate:
    target_ref: CandidateRef
    supports: tuple[CandidateSupport, ...]


@dataclass(frozen=True)
class RelationEvidence:
    request_ordinal: int
    occurrence_ordinal: int
    operator: str
    edge_id: int
    relation_class: str
    relation_name: str
    source_ref: CandidateRef
    target_ref: CandidateRef
    source_handle: GraphHandle
    target_handle: GraphHandle


@dataclass(frozen=True)
class WorkspaceOccurrence:
    occurrence_ordinal: int
    target_refs: tuple[CandidateRef, ...]


@dataclass(frozen=True)
class CandidateWorkspaceRequest:
    ordinal: int
    request: Mapping[str, Any]
    operator: str
    status: str
    failure: Mapping[str, Any] | None
    returned_occurrences: int | None
    occurrences: tuple[WorkspaceOccurrence, ...]


@dataclass(frozen=True)
class CandidateWorkspace:
    contract_version: str
    execution_id: str
    conformance_id: str
    retrieval_run_id: str
    retrieval_proposal_sha256: str
    capability_catalog_sha256: str
    retrieval_package_id: str
    retrieval_package_identity_version: str
    substrate_sha256: str
    vectors_sha256: str
    package_verification_contract_version: str
    execution_contract_version: str
    execution_status: str
    execution_failure: Mapping[str, Any] | None
    requests: tuple[CandidateWorkspaceRequest, ...]
    candidates: tuple[Candidate, ...]
    relation_evidence: tuple[RelationEvidence, ...]


def _error(message: str) -> CandidateCompositionError:
    return CandidateCompositionError(message)


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise _error(f"{label} must be an object")
    return value


def _sequence(value: Any, label: str) -> tuple[Any, ...]:
    if not isinstance(value, (list, tuple)):
        raise _error(f"{label} must be an array")
    return tuple(value)


def _keys(value: Mapping[str, Any], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise _error(f"{label} has an invalid key set")


def _int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise _error(f"{label} must be an integer")
    return value


def _score(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise _error(f"{label} must be a finite numeric score")
    return value


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise _error(f"{label} must be a non-empty string")
    return value


def _graph_handle(value: Any) -> GraphHandle:
    value = _mapping(value, "graph handle")
    _keys(value, {"node_kind", "identity"}, "graph handle")
    kind = _text(value["node_kind"], "graph node kind")
    identity = _sequence(value["identity"], "graph handle identity")
    if kind not in _TARGET_KINDS:
        raise _error(f"unsupported graph node kind: {kind!r}")
    if kind == "semantic_unit":
        if len(identity) != 1:
            raise _error("semantic_unit graph identity is malformed")
        return GraphHandle(kind, (_int(identity[0], "unit ID"),))
    if kind == "semantic_object":
        if len(identity) != 1:
            raise _error("semantic_object graph identity is malformed")
        return GraphHandle(kind, (_text(identity[0], "object UUID"),))
    if kind == "semantic_region":
        if len(identity) != 2:
            raise _error("semantic_region graph identity is malformed")
        path = _sequence(identity[1], "region path")
        if not path or not all(isinstance(item, str) and item for item in path):
            raise _error("semantic_region graph path is malformed")
        return GraphHandle(kind, (_text(identity[0], "region object UUID"), tuple(path)))
    if len(identity) != 1:
        raise _error("scope graph identity is malformed")
    path = _sequence(identity[0], "scope path")
    if not path or not all(isinstance(item, str) and item for item in path):
        raise _error("scope graph path is malformed")
    return GraphHandle(kind, (tuple(path),))


def _target_ref(kind: Any, identity: Any) -> CandidateRef:
    kind = _text(kind, "target kind")
    if kind not in _TARGET_KINDS:
        raise _error(f"unsupported target kind: {kind!r}")
    if kind == "semantic_unit":
        return CandidateRef(kind, (_int(identity, "unit ID"),))
    if kind == "semantic_object":
        return CandidateRef(kind, (_text(identity, "object UUID"),))
    if kind == "semantic_region":
        parts = _sequence(identity, "region target identity")
        if len(parts) != 2:
            raise _error("semantic_region target identity is malformed")
        path = _sequence(parts[1], "region path")
        if not path or not all(isinstance(item, str) and item for item in path):
            raise _error("semantic_region target path is malformed")
        return CandidateRef(kind, (_text(parts[0], "region object UUID"), tuple(path)))
    parts = _sequence(identity, "scope target identity")
    if len(parts) != 1:
        raise _error("scope target identity is malformed")
    path = _sequence(parts[0], "scope path")
    if not path or not all(isinstance(item, str) and item for item in path):
        raise _error("scope target path is malformed")
    return CandidateRef(kind, (tuple(path),))


def _ref_from_handle(value: Any) -> tuple[GraphHandle, CandidateRef]:
    handle = _graph_handle(value)
    identity = handle.identity
    if handle.node_kind in {"semantic_unit", "semantic_object"}:
        identity = identity[0]
    return handle, _target_ref(handle.node_kind, identity)


def _immutable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _immutable(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_immutable(item) for item in value)
    return value


def _json_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    if isinstance(value, dt.date):
        return {"__date__": value.isoformat()}
    if isinstance(value, GraphHandle):
        return {"node_kind": value.node_kind, "identity": _json_value(value.identity)}
    if isinstance(value, CandidateRef):
        return {"target_kind": value.target_kind, "identity": _json_value(value.identity)}
    if hasattr(value, "__dataclass_fields__"):
        return {name: _json_value(getattr(value, name)) for name in value.__dataclass_fields__}
    return value


def candidate_workspace_json(workspace: CandidateWorkspace) -> dict[str, Any]:
    """Return a stable JSON-compatible diagnostic representation."""
    return _json_value(workspace)


def serialize_candidate_workspace(workspace: CandidateWorkspace) -> str:
    return json.dumps(candidate_workspace_json(workspace), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def compose_candidate_workspace(execution_result: RetrievalExecutionResult) -> CandidateWorkspace:
    """Compose all succeeded execution occurrences without admission policy."""
    if not isinstance(execution_result, RetrievalExecutionResult):
        raise TypeError("execution_result must be RetrievalExecutionResult")
    candidate_order: list[CandidateRef] = []
    supports: dict[CandidateRef, list[CandidateSupport]] = {}
    relations: list[RelationEvidence] = []
    request_workspaces: list[CandidateWorkspaceRequest] = []

    def admit(ref: CandidateRef) -> None:
        if ref not in supports:
            supports[ref] = []
            candidate_order.append(ref)

    for request in execution_result.requests:
        operator = request.request.get("operator") if isinstance(request.request, Mapping) else None
        if not isinstance(operator, str) or operator not in _UNARY_OPERATORS:
            raise _error(f"request {request.ordinal} has unsupported operator")
        occurrence_refs: list[WorkspaceOccurrence] = []
        if request.status == "succeeded":
            result = _mapping(request.result, f"request {request.ordinal} result")
            kind = result.get("kind")
            if kind != _UNARY_OPERATORS[operator]:
                raise _error(f"request {request.ordinal} operator/result-kind conflict")
            if kind == "exact":
                values = _sequence(result.get("unit_ids"), "exact unit_ids")
                for index, unit_id in enumerate(values):
                    unit_id = _int(unit_id, "exact unit ID")
                    ref = _target_ref("semantic_unit", unit_id)
                    admit(ref); supports[ref].append(ExactSupport(request.ordinal, index, operator, unit_id))
                    occurrence_refs.append(WorkspaceOccurrence(index, (ref,)))
            elif kind in {"lexical", "temporal", "vector", "graph_discovery", "graph_relation_occurrences"}:
                hits_key = "occurrences" if kind == "graph_relation_occurrences" else "hits"
                hits = _sequence(result.get(hits_key), f"{kind} {hits_key}")
                for index, raw in enumerate(hits):
                    item = _mapping(raw, f"{kind} occurrence")
                    if kind == "lexical":
                        _keys(item, {"unit_id", "score"}, "lexical hit")
                        unit_id = _int(item["unit_id"], "lexical unit ID")
                        ref = _target_ref("semantic_unit", unit_id); admit(ref)
                        supports[ref].append(LexicalSupport(request.ordinal, index, operator, unit_id, _score(item["score"], "lexical score")))
                        occurrence_refs.append(WorkspaceOccurrence(index, (ref,)))
                    elif kind == "temporal":
                        _keys(item, {"unit_id", "date"}, "temporal hit")
                        unit_id = _int(item["unit_id"], "temporal unit ID")
                        try: temporal_date = dt.date.fromisoformat(_text(item["date"], "temporal date"))
                        except ValueError as exc: raise _error("temporal date is malformed") from exc
                        ref = _target_ref("semantic_unit", unit_id); admit(ref)
                        supports[ref].append(TemporalSupport(request.ordinal, index, operator, unit_id, temporal_date))
                        occurrence_refs.append(WorkspaceOccurrence(index, (ref,)))
                    elif kind == "vector":
                        _keys(item, {"target_kind", "target_identity", "score", "segment_ordinal"}, "vector hit")
                        ref = _target_ref(item["target_kind"], item["target_identity"]); admit(ref)
                        supports[ref].append(VectorSupport(request.ordinal, index, operator, ref.target_kind, item["target_identity"], _score(item["score"], "vector score"), _int(item["segment_ordinal"], "segment ordinal")))
                        occurrence_refs.append(WorkspaceOccurrence(index, (ref,)))
                    elif kind == "graph_discovery":
                        _keys(item, {"node", "score"}, "graph discovery hit")
                        handle, ref = _ref_from_handle(item["node"]); admit(ref)
                        supports[ref].append(GraphDiscoverySupport(request.ordinal, index, operator, handle, _score(item["score"], "graph discovery score")))
                        occurrence_refs.append(WorkspaceOccurrence(index, (ref,)))
                    else:
                        _keys(item, {"edge_id", "relation_class", "relation_name", "source", "target"}, "graph relation occurrence")
                        source_handle, source_ref = _ref_from_handle(item["source"]); target_handle, target_ref = _ref_from_handle(item["target"])
                        admit(source_ref); admit(target_ref)
                        relations.append(RelationEvidence(request.ordinal, index, operator, _int(item["edge_id"], "edge ID"), _text(item["relation_class"], "relation class"), _text(item["relation_name"], "relation name"), source_ref, target_ref, source_handle, target_handle))
                        occurrence_refs.append(WorkspaceOccurrence(index, (source_ref, target_ref)))
            returned = len(occurrence_refs)
        elif request.status in {"failed", "not_executed"}:
            if request.result is not None: raise _error(f"request {request.ordinal} failed result is not empty")
            returned = None
        else:
            raise _error(f"request {request.ordinal} has unsupported status")
        request_workspaces.append(CandidateWorkspaceRequest(request.ordinal, _immutable(request.request), operator, request.status, _immutable(request.failure) if request.failure is not None else None, returned, tuple(occurrence_refs)))

    return CandidateWorkspace(
        CANDIDATE_WORKSPACE_CONTRACT_VERSION, execution_result.execution_id, execution_result.conformance_id,
        execution_result.retrieval_run_id, execution_result.retrieval_proposal_sha256, execution_result.capability_catalog_sha256,
        execution_result.retrieval_package_id, execution_result.retrieval_package_identity_version, execution_result.substrate_sha256,
        execution_result.vectors_sha256, execution_result.package_verification_contract_version, execution_result.execution_contract_version,
        execution_result.status, _immutable(execution_result.execution_failure) if execution_result.execution_failure is not None else None,
        tuple(request_workspaces), tuple(Candidate(ref, tuple(supports[ref])) for ref in candidate_order), tuple(relations),
    )


__all__ = [
    "CANDIDATE_WORKSPACE_CONTRACT_VERSION", "CandidateCompositionError", "CandidateRef", "CandidateSupport",
    "ExactSupport", "LexicalSupport", "TemporalSupport", "VectorSupport", "GraphDiscoverySupport", "Candidate",
    "RelationEvidence", "WorkspaceOccurrence", "CandidateWorkspaceRequest", "CandidateWorkspace",
    "compose_candidate_workspace", "candidate_workspace_json", "serialize_candidate_workspace",
]
