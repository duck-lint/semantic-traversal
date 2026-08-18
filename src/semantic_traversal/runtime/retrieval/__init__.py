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
from .candidate_hydration import (
    CANDIDATE_HYDRATION_CONTRACT_VERSION, CandidateHydrationError,
    HydratedCanonicalTarget, HydratedCandidate, HydratedCandidateSelection,
    HydratedObjectTarget, HydratedRegionTarget, HydratedScopeTarget, HydratedUnitTarget,
    hydrate_candidate_selection,
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
    "CANDIDATE_HYDRATION_CONTRACT_VERSION", "CandidateHydrationError",
    "HydratedCanonicalTarget", "HydratedUnitTarget", "HydratedObjectTarget",
    "HydratedRegionTarget", "HydratedScopeTarget", "HydratedCandidate",
    "HydratedCandidateSelection", "hydrate_candidate_selection",
]
