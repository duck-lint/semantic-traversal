"""Deterministic composition of accepted post-execution evidence stages."""

from __future__ import annotations

from ..config import CandidateSelectionConfig
from .candidate_hydration import hydrate_candidate_selection
from .candidates import compose_candidate_workspace
from .evidence_projection import EvidenceProjection, project_evidence
from .execution import RetrievalExecutionResult
from .ownership_topology import derive_candidate_ownership_topology
from .package_verification import VerifiedRetrievalPackage
from .selection import select_candidates


def prepare_evidence(
    verified_package: VerifiedRetrievalPackage,
    execution_result: RetrievalExecutionResult,
    candidate_selection: CandidateSelectionConfig,
) -> EvidenceProjection:
    """Prepare deterministic model-facing evidence from completed execution."""
    workspace = compose_candidate_workspace(execution_result)
    topology = derive_candidate_ownership_topology(verified_package, workspace)
    selection = select_candidates(
        workspace,
        topology,
        candidate_selection.max_candidates,
        candidate_selection.protected_owner_fraction,
    )
    hydrated = hydrate_candidate_selection(verified_package, selection)
    return project_evidence(hydrated)


__all__ = ["prepare_evidence"]
