"""Mutable runtime state for durable conversational threads and inference runs."""

from .retrieval.control_plane import (
    ConformanceRequestResult, RetrievalConformanceError, RetrievalConformanceResult,
    conform_retrieval,
)
from .conversation import (
    Conversation, Message, RuntimeConversationError, append_message, create_conversation,
    get_conversation, initialize_runtime, migrate_runtime,
)
from .config import CandidateSelectionConfig, ModelConfig, RuntimeConfig, RuntimeConfigError, load_runtime_config
from .retrieval.inference import RetrievalInferenceResult, RuntimeRetrievalError, infer_retrieval
from .retrieval.package import (
    IDENTITY_VERSION, RetrievalPackage, RetrievalPackageError, RetrievalPackageIdentity,
    load_retrieval_package, require_catalog_binding, require_current_package_identity,
)
from .router import RouterResult, RuntimeRouterError, route_conversation
from .retrieval.package_verification import (
    VERIFICATION_CONTRACT_VERSION, RetrievalPackageVerificationError, VerifiedRetrievalPackage,
    verify_retrieval_package,
)
from .retrieval.execution import (
    EXECUTION_CONTRACT_VERSION, RetrievalExecutionError,
    RetrievalExecutionRequestResult, RetrievalExecutionResult, execute_retrieval,
    load_retrieval_execution,
)
from .retrieval.candidates import CandidateWorkspace, compose_candidate_workspace
from .retrieval.selection import CandidateSelection, select_candidates
from .retrieval.candidate_hydration import (
    CandidateHydrationError, HydratedCandidate, HydratedCandidateSelection,
    hydrate_candidate_selection,
)
from .retrieval.evidence_projection import (
    EVIDENCE_PROJECTION_CONTRACT_VERSION, EvidenceProjection, EvidenceProjectionError,
    measure_evidence_projection, project_evidence, serialize_evidence_projection,
)
from .synthesis_input import (
    SYNTHESIS_INPUT_CONTRACT_VERSION, SynthesisInput, SynthesisInputError, SynthesisMessage,
    build_synthesis_input, serialize_synthesis_input, snapshot_synthesis_conversation,
    synthesis_input_json, synthesis_input_sha256,
)
from .synthesis import (
    SynthesisError, SynthesisProvider, SynthesisProviderError, SynthesisProviderResult,
    SynthesisResult, SynthesisUsage, synthesize_conversation,
)

__all__ = [
    "Conversation", "Message", "RuntimeConversationError", "append_message", "create_conversation",
    "get_conversation", "initialize_runtime", "migrate_runtime", "CandidateSelectionConfig", "ModelConfig",
    "RuntimeConfig", "RuntimeConfigError", "load_runtime_config", "RetrievalInferenceResult",
    "RuntimeRetrievalError", "infer_retrieval", "RouterResult", "RuntimeRouterError", "route_conversation",
    "IDENTITY_VERSION", "RetrievalPackage", "RetrievalPackageError", "RetrievalPackageIdentity",
    "load_retrieval_package", "require_catalog_binding", "require_current_package_identity",
    "VERIFICATION_CONTRACT_VERSION", "RetrievalPackageVerificationError", "VerifiedRetrievalPackage",
    "verify_retrieval_package",
    "EXECUTION_CONTRACT_VERSION", "RetrievalExecutionError", "RetrievalExecutionRequestResult",
    "RetrievalExecutionResult", "execute_retrieval", "load_retrieval_execution",
    "ConformanceRequestResult", "RetrievalConformanceError", "RetrievalConformanceResult", "conform_retrieval",
    "CandidateWorkspace", "compose_candidate_workspace", "CandidateSelection", "select_candidates",
    "CandidateHydrationError", "HydratedCandidate", "HydratedCandidateSelection",
    "hydrate_candidate_selection",
    "EVIDENCE_PROJECTION_CONTRACT_VERSION", "EvidenceProjection", "EvidenceProjectionError",
    "project_evidence", "serialize_evidence_projection", "measure_evidence_projection",
    "SYNTHESIS_INPUT_CONTRACT_VERSION", "SynthesisInputError", "SynthesisMessage", "SynthesisInput",
    "snapshot_synthesis_conversation", "build_synthesis_input", "synthesis_input_json",
    "serialize_synthesis_input", "synthesis_input_sha256",
    "SynthesisError", "SynthesisProviderError", "SynthesisUsage", "SynthesisProviderResult",
    "SynthesisProvider", "SynthesisResult", "synthesize_conversation",
]
