"""Deterministic bounded selection over a complete CandidateWorkspace.

Selection bounds canonical candidates, not retrieval occurrences.  It is a
derived representation only; hydration and production orchestration remain
outside this module.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping

from ...projection.graph import GraphHandle
from .candidates import (
    CANDIDATE_WORKSPACE_CONTRACT_VERSION, Candidate, CandidateRef,
    CandidateWorkspace, RelationEvidence,
)


CANDIDATE_SELECTION_CONTRACT_VERSION = "candidate-selection-v1"
_GRAPH_RELATION_OPERATOR = "graph.relation_occurrence_lookup"


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


def _request_coverage(workspace: CandidateWorkspace) -> tuple[SelectionRequestCoverage, ...]:
    return tuple(
        SelectionRequestCoverage(
            item.ordinal, item.request, item.operator, item.status, item.failure,
            item.returned_occurrences,
        )
        for item in workspace.requests
    )


def select_candidates(workspace: CandidateWorkspace, max_candidates: int) -> CandidateSelection:
    """Select candidates by breadth-first request-lane admission."""
    if not isinstance(workspace, CandidateWorkspace):
        raise TypeError("workspace must be CandidateWorkspace")
    if workspace.contract_version != CANDIDATE_WORKSPACE_CONTRACT_VERSION:
        raise CandidateSelectionError("unsupported candidate workspace contract")
    _validate_capacity(max_candidates)

    candidates_by_ref = {candidate.target_ref: candidate for candidate in workspace.candidates}
    selected_refs: list[CandidateRef] = []
    selected_set: set[CandidateRef] = set()
    admissions: list[CandidateAdmission] = []
    closure_used = False

    def admit(
        ref: CandidateRef,
        request_ordinal: int,
        occurrence_ordinal: int,
        trigger_role: str,
        admission_kind: str,
    ) -> None:
        if ref in selected_set:
            return
        if ref not in candidates_by_ref:
            raise CandidateSelectionError("workspace occurrence references an unknown candidate")
        selected_set.add(ref)
        selected_refs.append(ref)
        admissions.append(CandidateAdmission(
            len(admissions), ref, request_ordinal, occurrence_ordinal,
            trigger_role, admission_kind,
        ))

    lanes = workspace.requests
    max_rounds = max((len(item.occurrences) for item in lanes), default=0)
    for occurrence_ordinal in range(max_rounds):
        for request in lanes:
            if occurrence_ordinal >= len(request.occurrences):
                continue
            occurrence = request.occurrences[occurrence_ordinal]
            refs = occurrence.target_refs
            if request.operator == _GRAPH_RELATION_OPERATOR:
                if len(refs) != 2:
                    raise CandidateSelectionError("graph relation occurrence must have two endpoints")
                source_ref, target_ref = refs
                unseen = []
                for ref in (source_ref, target_ref):
                    if ref not in selected_set and ref not in unseen:
                        unseen.append(ref)
                if not unseen:
                    continue
                remaining = max_candidates - len(selected_refs)
                if remaining <= 0:
                    continue
                if len(unseen) == 1:
                    admit(unseen[0], request.ordinal, occurrence.occurrence_ordinal,
                          "source" if unseen[0] == source_ref else "target", "ordinary")
                elif remaining >= 2:
                    admit(source_ref, request.ordinal, occurrence.occurrence_ordinal, "source", "ordinary")
                    admit(target_ref, request.ordinal, occurrence.occurrence_ordinal, "target", "ordinary")
                elif remaining == 1 and not closure_used:
                    admit(source_ref, request.ordinal, occurrence.occurrence_ordinal, "source", "ordinary")
                    admit(target_ref, request.ordinal, occurrence.occurrence_ordinal, "target", "relation_endpoint_closure")
                    closure_used = True
            else:
                if len(refs) != 1:
                    raise CandidateSelectionError("unary occurrence must have one target")
                if refs[0] not in selected_set and len(selected_refs) < max_candidates:
                    admit(refs[0], request.ordinal, occurrence.occurrence_ordinal, "unary", "ordinary")

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
        _request_coverage(workspace), tuple(admissions), selected_candidates,
        selected_relation_evidence, omitted_refs, coverage,
    )


__all__ = [
    "CANDIDATE_SELECTION_CONTRACT_VERSION", "CandidateSelectionError", "CandidateAdmission",
    "SelectionRequestCoverage", "SelectionCoverage", "CandidateSelection",
    "select_candidates", "candidate_selection_json", "serialize_candidate_selection",
]
