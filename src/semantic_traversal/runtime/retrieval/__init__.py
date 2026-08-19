"""Retrieval inference, authority, execution, and candidate composition/selection."""

from .control_plane import (
    ConformanceRequestResult,
    RetrievalConformanceError,
    RetrievalConformanceResult,
    conform_retrieval,
)
from .execution import (
    EXECUTION_CONTRACT_VERSION,
    RetrievalExecutionError,
    RetrievalExecutionRequestResult,
    RetrievalExecutionResult,
    execute_retrieval,
    load_retrieval_execution,
)
from .inference import RetrievalInferenceResult, RuntimeRetrievalError, infer_retrieval
from .package import (
    IDENTITY_VERSION,
    RetrievalPackage,
    RetrievalPackageError,
    RetrievalPackageIdentity,
    load_retrieval_package,
    require_catalog_binding,
    require_current_package_identity,
)
from .package_verification import (
    VERIFICATION_CONTRACT_VERSION,
    RetrievalPackageInput,
    RetrievalPackageVerificationError,
    VerifiedRetrievalPackage,
    normalize_verified_retrieval_package,
    verify_retrieval_package,
)

from .candidates import (
    CANDIDATE_WORKSPACE_CONTRACT_VERSION, Candidate, CandidateCompositionError,
    CandidateRef, CandidateSupport, CandidateWorkspace, CandidateWorkspaceRequest,
    ExactSupport, GraphDiscoverySupport, LexicalSupport, RelationEvidence,
    TemporalSupport, VectorSupport, WorkspaceOccurrence, candidate_workspace_json,
    compose_candidate_workspace, serialize_candidate_workspace,
)
from .selection import (
    CANDIDATE_SELECTION_CONTRACT_VERSION, CandidateAdmission, CandidateSelection,
    CandidateSelectionError, SelectionCoverage, SelectionRequestCoverage,
    candidate_selection_json, select_candidates, serialize_candidate_selection,
)
from .ownership_topology import (
    CANDIDATE_OWNERSHIP_TOPOLOGY_CONTRACT_VERSION, CandidateOwnership,
    CandidateOwnershipTopology, CandidateOwnershipTopologyError,
    candidate_ownership_topology_json, derive_candidate_ownership_topology,
    serialize_candidate_ownership_topology,
)
from .candidate_hydration import (
    CANDIDATE_HYDRATION_CONTRACT_VERSION, CandidateHydrationError,
    HydratedCanonicalTarget, HydratedCandidate, HydratedCandidateSelection,
    HydratedObjectTarget, HydratedRegionTarget, HydratedScopeTarget, HydratedUnitTarget,
    hydrate_candidate_selection,
)
from .evidence_projection import (
    EVIDENCE_PROJECTION_CONTRACT_VERSION, EvidenceCandidate, EvidenceCoverage,
    EvidenceObjectContext, EvidenceProjection, EvidenceProjectionError,
    EvidenceRequest, EvidenceRetrievalRelation, evidence_projection_json,
    measure_evidence_projection, project_evidence, serialize_evidence_projection,
)

__all__ = [
    "ConformanceRequestResult",
    "RetrievalConformanceError",
    "RetrievalConformanceResult",
    "conform_retrieval",
    "EXECUTION_CONTRACT_VERSION",
    "RetrievalExecutionError",
    "RetrievalExecutionRequestResult",
    "RetrievalExecutionResult",
    "execute_retrieval",
    "load_retrieval_execution",
    "RetrievalInferenceResult",
    "RuntimeRetrievalError",
    "infer_retrieval",
    "IDENTITY_VERSION",
    "RetrievalPackage",
    "RetrievalPackageError",
    "RetrievalPackageIdentity",
    "load_retrieval_package",
    "require_catalog_binding",
    "require_current_package_identity",
    "VERIFICATION_CONTRACT_VERSION",
    "RetrievalPackageInput",
    "RetrievalPackageVerificationError",
    "VerifiedRetrievalPackage",
    "normalize_verified_retrieval_package",
    "verify_retrieval_package",
    "CANDIDATE_WORKSPACE_CONTRACT_VERSION", "CandidateCompositionError", "CandidateRef", "CandidateSupport",
    "ExactSupport", "LexicalSupport", "TemporalSupport", "VectorSupport", "GraphDiscoverySupport", "Candidate",
    "RelationEvidence", "WorkspaceOccurrence", "CandidateWorkspaceRequest", "CandidateWorkspace",
    "compose_candidate_workspace", "candidate_workspace_json", "serialize_candidate_workspace",
    "CANDIDATE_SELECTION_CONTRACT_VERSION", "CandidateSelectionError", "CandidateAdmission",
    "SelectionRequestCoverage", "SelectionCoverage", "CandidateSelection",
    "select_candidates", "candidate_selection_json", "serialize_candidate_selection",
    "CANDIDATE_OWNERSHIP_TOPOLOGY_CONTRACT_VERSION", "CandidateOwnership",
    "CandidateOwnershipTopology", "CandidateOwnershipTopologyError",
    "derive_candidate_ownership_topology", "candidate_ownership_topology_json",
    "serialize_candidate_ownership_topology",
    "CANDIDATE_HYDRATION_CONTRACT_VERSION", "CandidateHydrationError",
    "HydratedCanonicalTarget", "HydratedUnitTarget", "HydratedObjectTarget",
    "HydratedRegionTarget", "HydratedScopeTarget", "HydratedCandidate",
    "HydratedCandidateSelection", "hydrate_candidate_selection",
    "EVIDENCE_PROJECTION_CONTRACT_VERSION", "EvidenceProjectionError", "EvidenceRequest",
    "EvidenceCoverage", "EvidenceObjectContext", "EvidenceCandidate",
    "EvidenceRetrievalRelation", "EvidenceProjection", "project_evidence",
    "evidence_projection_json", "serialize_evidence_projection", "measure_evidence_projection",
]
