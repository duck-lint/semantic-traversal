"""Sequence-only execution of one already-authored conversational turn."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ..projection.vector import OllamaEmbeddingProvider
from .config import RuntimeConfig
from .conversation import RuntimeConversationError, _connect_runtime, get_conversation
from .retrieval.control_plane import (
    RetrievalConformanceError, RetrievalConformanceResult, conform_retrieval,
)
from .retrieval.execution import RetrievalExecutionError, RetrievalExecutionResult, execute_retrieval
from .retrieval.hydration import RetrievalHydrationError, HydratedRetrievalResult, hydrate_retrieval_execution
from .retrieval.inference import RetrievalInferenceResult, RuntimeRetrievalError, infer_retrieval
from .retrieval.package import RetrievalPackageError, RetrievalPackage, load_retrieval_package
from .retrieval.package_verification import RetrievalPackageVerificationError, verify_retrieval_package
from .retrieval.packet import PacketAssemblyResult, RetrievalPacketError, assemble_retrieval_packet
from .router import RouterResult, RuntimeRouterError, route_conversation
from .synthesis import SynthesisError, SynthesisProvider, SynthesisResult, synthesize_conversation

Clock = Callable[[], Any]


class RuntimeTurnError(ValueError):
    """A deterministic orchestration precondition or stage failure."""


@dataclass(frozen=True)
class TurnResult:
    conversation_id: str
    trigger_message_id: int
    route: str
    router_result: RouterResult
    retrieval_inference_result: RetrievalInferenceResult | None
    conformance_result: RetrievalConformanceResult | None
    execution_result: RetrievalExecutionResult | None
    hydrated_result: HydratedRetrievalResult | None
    packet_assembly: PacketAssemblyResult | None
    synthesis_result: SynthesisResult


def _require_fresh_trigger(database_path: str, conversation_id: str) -> int:
    try:
        conversation = get_conversation(database_path, conversation_id)
    except RuntimeConversationError as exc:
        raise RuntimeTurnError(str(exc)) from exc
    if not conversation.messages or conversation.messages[-1].role != "user":
        raise RuntimeTurnError("full turn requires the latest persisted conversation message to be user")
    trigger_message_id = conversation.messages[-1].message_id
    try:
        connection = _connect_runtime(database_path)
    except RuntimeConversationError as exc:
        raise RuntimeTurnError(str(exc)) from exc
    try:
        existing = connection.execute(
            "SELECT run_id, status FROM model_runs "
            "WHERE conversation_id = ? AND trigger_message_id = ? AND run_kind = 'router'",
            (conversation_id, trigger_message_id),
        ).fetchone()
    finally:
        connection.close()
    if existing is not None:
        raise RuntimeTurnError(
            f"a prior router attempt already exists for trigger {trigger_message_id}: {existing['status']}"
        )
    return trigger_message_id


def _stage_error(stage: str, exc: Exception) -> RuntimeTurnError:
    return RuntimeTurnError(f"{stage} failed: {exc}")


def execute_turn(
    database_path: str,
    runtime_config: RuntimeConfig,
    conversation_id: str,
    *,
    retrieval_build: str | Path | None = None,
    ollama_url: str = "http://127.0.0.1:11434",
    router_provider: Any | None = None,
    retrieval_provider: Any | None = None,
    vector_provider: Any | None = None,
    synthesis_provider: SynthesisProvider | None = None,
    clock: Clock | None = None,
) -> TurnResult:
    """Execute one fresh turn through the already-accepted runtime stages."""
    trigger_message_id = _require_fresh_trigger(database_path, conversation_id)

    try:
        router_result = route_conversation(
            database_path, runtime_config, conversation_id,
            provider=router_provider, clock=clock,
        )
    except RuntimeRouterError as exc:
        raise _stage_error("router", exc) from exc

    if router_result.trigger_message_id != trigger_message_id:
        raise RuntimeTurnError("router returned a different trigger message")

    if router_result.route == "direct":
        try:
            synthesis_result = synthesize_conversation(
                database_path, runtime_config, conversation_id, router_result.run_id,
                retrieval_packet=None, provider=synthesis_provider, clock=clock,
            )
        except SynthesisError as exc:
            raise _stage_error("synthesis", exc) from exc
        return TurnResult(
            conversation_id, trigger_message_id, "direct", router_result,
            None, None, None, None, None, synthesis_result,
        )

    if router_result.route != "semantic_retrieval":
        raise RuntimeTurnError(f"router returned unsupported route: {router_result.route!r}")
    if retrieval_build is None:
        raise RuntimeTurnError("semantic_retrieval requires a completed retrieval build")

    try:
        package: RetrievalPackage = load_retrieval_package(retrieval_build)
        verified_package = verify_retrieval_package(package)
    except (RetrievalPackageError, RetrievalPackageVerificationError) as exc:
        raise _stage_error("retrieval package verification", exc) from exc
    package = verified_package.package
    catalog_path = package.capability_catalog_path

    try:
        retrieval_result = infer_retrieval(
            database_path, runtime_config, catalog_path, router_result.run_id,
            provider=retrieval_provider, clock=clock,
        )
    except RuntimeRetrievalError as exc:
        raise _stage_error("retrieval inference", exc) from exc

    try:
        conformance_result = conform_retrieval(
            database_path, catalog_path, retrieval_result.run_id, clock=clock,
        )
    except RetrievalConformanceError as exc:
        raise _stage_error("conformance", exc) from exc
    if conformance_result.status != "valid":
        raise RuntimeTurnError("conformance returned invalid retrieval proposal")

    selected_vector_provider = vector_provider
    if any(item["operator"] == "vector.semantic_similarity" for item in retrieval_result.requests) and selected_vector_provider is None:
        selected_vector_provider = OllamaEmbeddingProvider(base_url=ollama_url)
    try:
        execution_result = execute_retrieval(
            database_path, verified_package, conformance_result.conformance_id,
            vector_provider=selected_vector_provider, clock=clock,
        )
    except RetrievalExecutionError as exc:
        raise _stage_error("retrieval execution", exc) from exc

    try:
        hydrated_result = hydrate_retrieval_execution(
            database_path, verified_package, execution_result.execution_id,
        )
    except RetrievalHydrationError as exc:
        raise _stage_error("retrieval hydration", exc) from exc

    try:
        packet_assembly = assemble_retrieval_packet(hydrated_result, runtime_config.packet)
    except RetrievalPacketError as exc:
        raise _stage_error("packet assembly", exc) from exc

    try:
        synthesis_result = synthesize_conversation(
            database_path, runtime_config, conversation_id, router_result.run_id,
            retrieval_packet=packet_assembly.packet, provider=synthesis_provider, clock=clock,
        )
    except SynthesisError as exc:
        raise _stage_error("synthesis", exc) from exc
    return TurnResult(
        conversation_id, trigger_message_id, "semantic_retrieval", router_result,
        retrieval_result, conformance_result, execution_result, hydrated_result,
        packet_assembly, synthesis_result,
    )


__all__ = ["RuntimeTurnError", "TurnResult", "execute_turn"]
