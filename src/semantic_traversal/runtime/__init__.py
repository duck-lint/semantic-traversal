"""Mutable runtime state for durable conversational threads and inference runs."""

from .retrieval.control_plane import (
    ConformanceRequestResult, RetrievalConformanceError, RetrievalConformanceResult,
    conform_retrieval,
)
from .conversation import (
    Conversation, Message, RuntimeConversationError, append_message, create_conversation,
    get_conversation, initialize_runtime, migrate_runtime,
)
from .config import ModelConfig, PacketConfig, RuntimeConfig, RuntimeConfigError, load_runtime_config
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
from .retrieval.hydration import (
    HydratedCanonicalTarget, HydratedExactHit, HydratedExactResult,
    HydratedGraphDiscoveryHit, HydratedGraphDiscoveryResult, HydratedGraphOccurrence,
    HydratedGraphRelationResult, HydratedLexicalHit, HydratedLexicalResult,
    HydratedRequestResult, HydratedRetrievalResult, HydratedVectorHit,
    HydratedVectorResult, RetrievalHydrationError, hydrate_retrieval_execution,
)
from .retrieval.packet import (
    PACKET_CONTRACT_VERSION, SELECTION_RULE_VERSION, CanonicalTargetPayload, CanonicalTargetRef,
    PacketAssemblyResult, RetrievalPacket, RetrievalPacketError, assemble_retrieval_packet,
)
from .synthesis import (
    LEGAL_SYNTHESIS_ROUTES, SYNTHESIS_INPUT_CONTRACT_VERSION, SynthesisError,
    SynthesisInput, SynthesisMessage, SynthesisProvider, SynthesisProviderError, SynthesisProviderResult,
    SynthesisResult, SynthesisUsage, serialize_synthesis_input, synthesis_input_sha256,
    synthesize_conversation,
)

__all__ = [
    "Conversation", "Message", "RuntimeConversationError", "append_message", "create_conversation",
    "get_conversation", "initialize_runtime", "migrate_runtime", "ModelConfig", "PacketConfig",
    "RuntimeConfig", "RuntimeConfigError", "load_runtime_config", "RetrievalInferenceResult",
    "RuntimeRetrievalError", "infer_retrieval", "RouterResult", "RuntimeRouterError", "route_conversation",
    "IDENTITY_VERSION", "RetrievalPackage", "RetrievalPackageError", "RetrievalPackageIdentity",
    "load_retrieval_package", "require_catalog_binding", "require_current_package_identity",
    "VERIFICATION_CONTRACT_VERSION", "RetrievalPackageVerificationError", "VerifiedRetrievalPackage",
    "verify_retrieval_package",
    "EXECUTION_CONTRACT_VERSION", "RetrievalExecutionError", "RetrievalExecutionRequestResult",
    "RetrievalExecutionResult", "execute_retrieval", "load_retrieval_execution",
    "HydratedCanonicalTarget", "HydratedExactHit", "HydratedExactResult",
    "HydratedGraphDiscoveryHit", "HydratedGraphDiscoveryResult", "HydratedGraphOccurrence",
    "HydratedGraphRelationResult", "HydratedLexicalHit", "HydratedLexicalResult",
    "HydratedRequestResult", "HydratedRetrievalResult", "HydratedVectorHit",
    "HydratedVectorResult", "RetrievalHydrationError", "hydrate_retrieval_execution",
    "ConformanceRequestResult", "RetrievalConformanceError", "RetrievalConformanceResult", "conform_retrieval",
    "PACKET_CONTRACT_VERSION", "SELECTION_RULE_VERSION", "RetrievalPacketError",
    "CanonicalTargetRef", "CanonicalTargetPayload", "RetrievalPacket", "PacketAssemblyResult",
    "assemble_retrieval_packet",
    "SYNTHESIS_INPUT_CONTRACT_VERSION", "LEGAL_SYNTHESIS_ROUTES", "SynthesisError",
    "SynthesisProviderError", "SynthesisMessage", "SynthesisInput", "SynthesisProvider", "SynthesisProviderResult",
    "SynthesisUsage", "SynthesisResult", "serialize_synthesis_input", "synthesis_input_sha256",
    "synthesize_conversation",
]
