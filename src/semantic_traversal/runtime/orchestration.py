"""Provider-neutral coordination for one already-persisted user turn.

This module owns only stage composition.  Attempt authority, durable lineage,
and semantic validation remain in the runtime stages it invokes.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ..projection.vector import EmbeddingProvider
from .config import RuntimeConfig
from .retrieval import (
    conform_retrieval,
    execute_retrieval,
    infer_retrieval,
    load_retrieval_package,
    prepare_evidence,
    verify_retrieval_package,
)
from .router import RouterResult, route_conversation
from .synthesis import (
    Clock,
    SynthesisProvider,
    SynthesisResult,
    load_synthesis_success,
    synthesize_conversation,
)


class OrchestrationError(RuntimeError):
    """A stage failed while composing one upper-runtime turn."""

    def __init__(self, stage: str, message: str) -> None:
        self.stage = stage
        super().__init__(message)


@dataclass(frozen=True)
class TurnResult:
    """The durable public facts produced by one coordinated turn."""

    conversation_id: str
    trigger_message_id: int
    route: str
    router_run_id: str
    retrieval_run_id: str | None
    synthesis_run_id: str
    synthesis_message_id: int
    response_text: str


def _turn_result(router: RouterResult, synthesis: SynthesisResult) -> TurnResult:
    retrieval_run_id = None if synthesis.route == "direct" else synthesis.parent_run_id
    return TurnResult(
        conversation_id=synthesis.conversation_id,
        trigger_message_id=synthesis.trigger_message_id,
        route=synthesis.route,
        router_run_id=router.run_id,
        retrieval_run_id=retrieval_run_id,
        synthesis_run_id=synthesis.run_id,
        synthesis_message_id=synthesis.produced_message_id,
        response_text=synthesis.response_text,
    )


def _raise(stage: str, exc: Exception) -> None:
    raise OrchestrationError(stage, str(exc)) from exc


def run_current_turn(
    database_path: str,
    runtime_config: RuntimeConfig,
    conversation_id: str,
    *,
    router_provider: Any,
    retrieval_provider: Any,
    synthesis_provider: SynthesisProvider,
    retrieval_build_path: str | Path | None = None,
    vector_provider_factory: Callable[[], EmbeddingProvider] | None = None,
    clock: Clock | None = None,
) -> TurnResult:
    """Coordinate the accepted stages for an already-persisted user turn."""

    try:
        router = route_conversation(
            database_path,
            runtime_config,
            conversation_id,
            provider=router_provider,
            clock=clock,
        )
    except Exception as exc:
        _raise("router", exc)

    # This lookup deliberately precedes every mutable retrieval-package read.
    try:
        synthesis_success = load_synthesis_success(database_path, conversation_id, router.run_id)
    except Exception as exc:
        _raise("synthesis", exc)
    if synthesis_success is not None:
        return _turn_result(router, synthesis_success)

    if router.route == "direct":
        try:
            synthesis = synthesize_conversation(
                database_path,
                runtime_config,
                conversation_id,
                router.run_id,
                evidence=None,
                provider=synthesis_provider,
                clock=clock,
            )
        except Exception as exc:
            _raise("synthesis", exc)
        return _turn_result(router, synthesis)

    if retrieval_build_path is None:
        raise OrchestrationError("package_load", "retrieval_build_path is required for semantic_retrieval")

    try:
        package = load_retrieval_package(retrieval_build_path)
    except Exception as exc:
        _raise("package_load", exc)

    try:
        verified_package = verify_retrieval_package(package)
    except Exception as exc:
        _raise("package_verification", exc)

    catalog_path = verified_package.package.capability_catalog_path
    try:
        retrieval = infer_retrieval(
            database_path,
            runtime_config,
            catalog_path,
            router.run_id,
            provider=retrieval_provider,
            clock=clock,
        )
    except Exception as exc:
        _raise("retrieval_inference", exc)

    try:
        conformance = conform_retrieval(
            database_path,
            catalog_path,
            retrieval.run_id,
            clock=clock,
        )
        if conformance.status not in {"valid", "partial"}:
            raise OrchestrationError("conformance", "retrieval conformance is not valid")
    except OrchestrationError:
        raise
    except Exception as exc:
        _raise("conformance", exc)

    try:
        execution = execute_retrieval(
            database_path,
            verified_package,
            conformance.conformance_id,
            vector_provider_factory=vector_provider_factory,
            clock=clock,
        )
    except Exception as exc:
        _raise("execution", exc)

    try:
        evidence = prepare_evidence(
            verified_package,
            execution,
            runtime_config.candidate_selection,
        )
    except Exception as exc:
        _raise("evidence_preparation", exc)

    try:
        synthesis = synthesize_conversation(
            database_path,
            runtime_config,
            conversation_id,
            router.run_id,
            evidence=evidence,
            provider=synthesis_provider,
            clock=clock,
        )
    except Exception as exc:
        _raise("synthesis", exc)
    return _turn_result(router, synthesis)


__all__ = ["OrchestrationError", "TurnResult", "run_current_turn"]
