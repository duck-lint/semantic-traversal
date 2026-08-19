"""Deterministic bounded selection over a complete CandidateWorkspace.

Selection bounds canonical candidates, not retrieval occurrences.  It is a
derived representation only; hydration and production orchestration remain
outside this module.
"""

from __future__ import annotations

import datetime as dt
import json
import math
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping

from ...projection.graph import GraphHandle
from .candidates import (
    CANDIDATE_WORKSPACE_CONTRACT_VERSION, Candidate, CandidateRef,
    CandidateWorkspace, RelationEvidence,
)
from .ownership_topology import (
    CANDIDATE_OWNERSHIP_TOPOLOGY_CONTRACT_VERSION,
    CandidateOwnershipTopology,
)


CANDIDATE_SELECTION_CONTRACT_VERSION = "candidate-selection-v2"
_GRAPH_RELATION_OPERATOR = "graph.relation_occurrence_lookup"
_PROTECTED_OPERATORS = frozenset({"lexical.terms", "lexical.phrase", "vector.semantic_similarity"})
_UNAFFECTED_OPERATORS = frozenset({
    "exact.equals", "graph.discovery.terms", "graph.discovery.phrase",
    "graph.relation_occurrence_lookup", "temporal.earliest", "temporal.latest",
    "temporal.before", "temporal.after", "temporal.between", "temporal.ordered",
})


class CandidateSelectionError(ValueError):
    """The workspace or explicit selection capacity is invalid."""


@dataclass(frozen=True)
class CandidateAdmission:
    selection_ordinal: int
    target_ref: CandidateRef
    trigger_request_ordinal: int
    trigger_occurrence_ordinal: int
    trigger_role: str
    admission_kind: str


@dataclass(frozen=True)
class SelectionRequestCoverage:
    ordinal: int
    request: Mapping[str, Any]
    operator: str
    status: str
    failure: Mapping[str, Any] | None
    returned_occurrences: int | None


@dataclass(frozen=True)
class SelectionCoverage:
    workspace_candidate_count: int
    selected_candidate_count: int
    omitted_candidate_count: int
    workspace_relation_count: int
    selected_relation_count: int
    omitted_relation_count: int
    max_candidates: int
    relation_endpoint_closure_used: bool
    selection_truncated: bool


@dataclass(frozen=True)
class CandidateSelection:
    contract_version: str
    workspace_contract_version: str
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
    max_candidates: int
    topology_contract_version: str
    protected_owner_fraction: float
    protected_owner_limit: int
    owner_depth_fallback_used: bool
    requests: tuple[SelectionRequestCoverage, ...]
    admissions: tuple[CandidateAdmission, ...]
    selected_candidates: tuple[Candidate, ...]
    selected_relation_evidence: tuple[RelationEvidence, ...]
    omitted_candidate_refs: tuple[CandidateRef, ...]
    coverage: SelectionCoverage


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


def candidate_selection_json(selection: CandidateSelection) -> dict[str, Any]:
    """Return a stable JSON-compatible diagnostic representation."""
    return _json_value(selection)


def serialize_candidate_selection(selection: CandidateSelection) -> str:
    return json.dumps(candidate_selection_json(selection), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _validate_capacity(max_candidates: int) -> None:
    if isinstance(max_candidates, bool) or not isinstance(max_candidates, int) or max_candidates < 0:
        raise CandidateSelectionError("max_candidates must be an integer greater than or equal to zero")


def _validate_fraction(protected_owner_fraction: float) -> None:
    if (
        isinstance(protected_owner_fraction, bool)
        or not isinstance(protected_owner_fraction, (int, float))
        or not math.isfinite(float(protected_owner_fraction))
        or protected_owner_fraction <= 0
        or protected_owner_fraction > 1
    ):
        raise CandidateSelectionError("protected_owner_fraction must be finite and in the interval (0, 1]")


def _validate_topology(workspace: CandidateWorkspace, topology: CandidateOwnershipTopology) -> None:
    if not isinstance(topology, CandidateOwnershipTopology):
        raise TypeError("topology must be CandidateOwnershipTopology")
    if topology.contract_version != CANDIDATE_OWNERSHIP_TOPOLOGY_CONTRACT_VERSION:
        raise CandidateSelectionError("unsupported candidate ownership topology contract")
    workspace_lineage = (
        workspace.contract_version, workspace.execution_id, workspace.conformance_id,
        workspace.retrieval_run_id, workspace.retrieval_proposal_sha256,
        workspace.capability_catalog_sha256, workspace.retrieval_package_id,
        workspace.retrieval_package_identity_version, workspace.substrate_sha256,
        workspace.vectors_sha256, workspace.package_verification_contract_version,
        workspace.execution_contract_version,
    )
    topology_lineage = (
        topology.workspace_contract_version, topology.execution_id, topology.conformance_id,
        topology.retrieval_run_id, topology.retrieval_proposal_sha256,
        topology.capability_catalog_sha256, topology.retrieval_package_id,
        topology.retrieval_package_identity_version, topology.substrate_sha256,
        topology.vectors_sha256, topology.package_verification_contract_version,
        topology.execution_contract_version,
    )
    if topology_lineage != workspace_lineage:
        raise CandidateSelectionError("candidate ownership topology lineage disagrees with workspace")
    expected = tuple(candidate.target_ref for candidate in workspace.candidates)
    actual = tuple(entry.target_ref for entry in topology.entries)
    if actual != expected or len(set(actual)) != len(actual):
        raise CandidateSelectionError("candidate ownership topology coverage or order disagrees with workspace")


def _request_coverage(workspace: CandidateWorkspace) -> tuple[SelectionRequestCoverage, ...]:
    return tuple(
        SelectionRequestCoverage(
            item.ordinal, item.request, item.operator, item.status, item.failure,
            item.returned_occurrences,
        )
        for item in workspace.requests
    )


def select_candidates(
    workspace: CandidateWorkspace,
    topology: CandidateOwnershipTopology,
    max_candidates: int,
    protected_owner_fraction: float,
) -> CandidateSelection:
    """Select candidates by breadth-first request-lane admission with soft owner depth."""
    if not isinstance(workspace, CandidateWorkspace):
        raise TypeError("workspace must be CandidateWorkspace")
    if workspace.contract_version != CANDIDATE_WORKSPACE_CONTRACT_VERSION:
        raise CandidateSelectionError("unsupported candidate workspace contract")
    _validate_capacity(max_candidates)
    _validate_fraction(protected_owner_fraction)
    _validate_topology(workspace, topology)
    protected_owner_limit = math.ceil(max_candidates * protected_owner_fraction)

    for request in workspace.requests:
        if request.operator not in _PROTECTED_OPERATORS | _UNAFFECTED_OPERATORS:
            raise CandidateSelectionError(f"request operator is not classified for owner depth: {request.operator!r}")

    candidates_by_ref = {candidate.target_ref: candidate for candidate in workspace.candidates}
    selected_refs: list[CandidateRef] = []
    selected_set: set[CandidateRef] = set()
    admissions: list[CandidateAdmission] = []
    closure_used = False
    owner_counts: dict[str, int] = {}
    owner_by_ref = {entry.target_ref: entry.owner_object_uuid for entry in topology.entries}

    def admit(
        ref: CandidateRef,
        request_ordinal: int,
        occurrence_ordinal: int,
        trigger_role: str,
        admission_kind: str,
        *,
        protected: bool,
    ) -> None:
        if ref in selected_set:
            return
        if ref not in candidates_by_ref:
            raise CandidateSelectionError("workspace occurrence references an unknown candidate")
        selected_set.add(ref)
        selected_refs.append(ref)
        if protected:
            owner = owner_by_ref[ref]
            if owner is None:
                raise CandidateSelectionError("protected candidate has no owning semantic object")
            owner_counts[owner] = owner_counts.get(owner, 0) + 1
        admissions.append(CandidateAdmission(
            len(admissions), ref, request_ordinal, occurrence_ordinal,
            trigger_role, admission_kind,
        ))

    def visit(request, occurrence, enforce_owner_depth: bool, fallback: bool) -> None:
        nonlocal closure_used
        refs = occurrence.target_refs
        if request.operator == _GRAPH_RELATION_OPERATOR:
            if len(refs) != 2:
                raise CandidateSelectionError("graph relation occurrence must have two endpoints")
            source_ref, target_ref = refs
            unseen = []
            for ref in (source_ref, target_ref):
                if ref not in selected_set and ref not in unseen:
                    unseen.append(ref)
            if not unseen or len(selected_refs) >= max_candidates:
                return
            remaining = max_candidates - len(selected_refs)
            if len(unseen) == 1:
                admit(unseen[0], request.ordinal, occurrence.occurrence_ordinal,
                      "source" if unseen[0] == source_ref else "target", "ordinary", protected=False)
            elif remaining >= 2:
                admit(source_ref, request.ordinal, occurrence.occurrence_ordinal, "source", "ordinary", protected=False)
                admit(target_ref, request.ordinal, occurrence.occurrence_ordinal, "target", "ordinary", protected=False)
            elif remaining == 1 and not closure_used:
                admit(source_ref, request.ordinal, occurrence.occurrence_ordinal, "source", "ordinary", protected=False)
                admit(target_ref, request.ordinal, occurrence.occurrence_ordinal, "target", "relation_endpoint_closure", protected=False)
                closure_used = True
            return
        if len(refs) != 1:
            raise CandidateSelectionError("unary occurrence must have one target")
        ref = refs[0]
        if ref in selected_set or len(selected_refs) >= max_candidates:
            return
        protected = request.operator in _PROTECTED_OPERATORS
        if enforce_owner_depth and protected:
            owner = owner_by_ref[ref]
            if owner is None:
                raise CandidateSelectionError("protected candidate has no owning semantic object")
            if owner_counts.get(owner, 0) >= protected_owner_limit:
                return
        admit(ref, request.ordinal, occurrence.occurrence_ordinal, "unary", "owner_depth_fallback" if fallback and protected else "ordinary", protected=protected)

    lanes = workspace.requests
    max_rounds = max((len(item.occurrences) for item in lanes), default=0)
    for occurrence_ordinal in range(max_rounds):
        for request in lanes:
            if occurrence_ordinal < len(request.occurrences):
                visit(request, request.occurrences[occurrence_ordinal], True, False)
                if len(selected_refs) > max_candidates or len(selected_refs) >= max_candidates:
                    break
        if len(selected_refs) >= max_candidates:
            break

    fallback_used = False
    if len(selected_refs) < max_candidates:
        fallback_used = True
        for occurrence_ordinal in range(max_rounds):
            for request in lanes:
                if occurrence_ordinal < len(request.occurrences):
                    visit(request, request.occurrences[occurrence_ordinal], False, True)
                    if len(selected_refs) > max_candidates or len(selected_refs) >= max_candidates:
                        break
            if len(selected_refs) >= max_candidates:
                break

    selected_candidates = tuple(candidates_by_ref[ref] for ref in selected_refs)
    selected_relation_evidence = tuple(
        relation for relation in workspace.relation_evidence
        if relation.source_ref in selected_set and relation.target_ref in selected_set
    )
    omitted_refs = tuple(candidate.target_ref for candidate in workspace.candidates if candidate.target_ref not in selected_set)
    coverage = SelectionCoverage(
        len(workspace.candidates), len(selected_candidates), len(omitted_refs),
        len(workspace.relation_evidence), len(selected_relation_evidence),
        len(workspace.relation_evidence) - len(selected_relation_evidence),
        max_candidates, closure_used, bool(omitted_refs),
    )
    return CandidateSelection(
        CANDIDATE_SELECTION_CONTRACT_VERSION, workspace.contract_version,
        workspace.execution_id, workspace.conformance_id, workspace.retrieval_run_id,
        workspace.retrieval_proposal_sha256, workspace.capability_catalog_sha256,
        workspace.retrieval_package_id, workspace.retrieval_package_identity_version,
        workspace.substrate_sha256, workspace.vectors_sha256,
        workspace.package_verification_contract_version, workspace.execution_contract_version,
        workspace.execution_status, workspace.execution_failure, max_candidates,
        topology.contract_version, float(protected_owner_fraction), protected_owner_limit,
        fallback_used,
        _request_coverage(workspace), tuple(admissions), selected_candidates,
        selected_relation_evidence, omitted_refs, coverage,
    )


__all__ = [
    "CANDIDATE_SELECTION_CONTRACT_VERSION", "CandidateSelectionError", "CandidateAdmission",
    "SelectionRequestCoverage", "SelectionCoverage", "CandidateSelection",
    "select_candidates", "candidate_selection_json", "serialize_candidate_selection",
]
