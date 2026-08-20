"""Provider-neutral durable synthesis lifecycle for runtime schema v6.

This module owns the deterministic boundary around one injected synthesis
provider.  It does not construct provider transports, execute retrieval, or
persist derived evidence stages.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass, field
from typing import Callable, Protocol
from uuid import uuid4

from .config import ModelConfig, RuntimeConfig
from .conversation import Conversation, Message, RuntimeConversationError, _connect_runtime, _timestamp
from .model_runs import complete_synthesis_run, fail_synthesis_run, insert_synthesis_run
from .prompts import prompt_version
from .retrieval.candidate_hydration import HydratedCandidateSelection
from .retrieval.evidence_projection import (
    EVIDENCE_PROJECTION_CONTRACT_VERSION,
    EvidenceProjection,
    EvidenceProjectionError,
    project_evidence,
)
from .retrieval.execution import EXECUTION_CONTRACT_VERSION
from .retrieval.package import IDENTITY_VERSION
from .retrieval.package_verification import VERIFICATION_CONTRACT_VERSION
from .synthesis_input import (
    LEGAL_SYNTHESIS_ROUTES,
    SynthesisInput,
    SynthesisInputError,
    build_synthesis_input,
    serialize_synthesis_input,
    synthesis_input_sha256,
)


Clock = Callable[[], dt.datetime]
_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


class SynthesisError(ValueError):
    """A synthesis lifecycle, lineage, persistence, or concurrency failure."""


class SynthesisProviderError(ValueError):
    """An injected synthesis provider failed before returning a valid result."""

    def __init__(self, error_type: str, message: str | None = None):
        if not isinstance(error_type, str) or not error_type.strip():
            raise ValueError("provider error_type must be nonblank text")
        super().__init__(error_type if message is None else message)
        self.error_type = error_type


def _usage_value(name: str, value: int | None) -> None:
    if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 0):
        raise SynthesisProviderError("runtime_validation", f"provider usage {name} must be a non-negative integer or None")


@dataclass(frozen=True)
class SynthesisUsage:
    input_tokens: int | None = None
    cached_input_tokens: int | None = None
    output_tokens: int | None = None
    reasoning_tokens: int | None = None
    total_tokens: int | None = None

    def __post_init__(self) -> None:
        for name in ("input_tokens", "cached_input_tokens", "output_tokens", "reasoning_tokens", "total_tokens"):
            _usage_value(name, getattr(self, name))


@dataclass(frozen=True)
class SynthesisProviderResult:
    response_text: str
    provider_response_id: str | None = None
    usage: SynthesisUsage = field(default_factory=SynthesisUsage)

    def __post_init__(self) -> None:
        if not isinstance(self.response_text, str) or not self.response_text.strip():
            raise SynthesisProviderError("runtime_validation", "provider response_text must be nonblank text")
        if self.provider_response_id is not None and (
            not isinstance(self.provider_response_id, str) or not self.provider_response_id.strip()
        ):
            raise SynthesisProviderError("runtime_validation", "provider_response_id must be None or nonblank text")
        if not isinstance(self.usage, SynthesisUsage):
            raise SynthesisProviderError("runtime_validation", "provider usage must be SynthesisUsage")


class SynthesisProvider(Protocol):
    def synthesize(self, model_config: ModelConfig, synthesis_input: SynthesisInput) -> SynthesisProviderResult:
        """Return one provider-neutral synthesis result."""


@dataclass(frozen=True)
class SynthesisResult:
    run_id: str
    conversation_id: str
    trigger_message_id: int
    route: str
    parent_run_id: str
    provider: str
    model: str
    prompt_version: str
    input_sha256: str
    status: str
    response_text: str
    produced_message_id: int
    provider_response_id: str | None
    usage: SynthesisUsage


def _conversation_from_connection(connection: sqlite3.Connection, conversation_id: str) -> Conversation:
    row = connection.execute(
        "SELECT conversation_id, created_at FROM conversations WHERE conversation_id = ?", (conversation_id,)
    ).fetchone()
    if row is None:
        raise SynthesisError(f"conversation does not exist: {conversation_id}")
    messages = tuple(
        Message(
            message_id=item["message_id"], conversation_id=item["conversation_id"], ordinal=item["ordinal"],
            role=item["role"], content=item["content"], created_at=item["created_at"],
        )
        for item in connection.execute(
            "SELECT message_id, conversation_id, ordinal, role, content, created_at "
            "FROM messages WHERE conversation_id = ? ORDER BY ordinal ASC", (conversation_id,)
        )
    )
    if any(item.role not in {"user", "synthesis"} for item in messages):
        raise SynthesisError("conversation contains an invalid persisted role")
    return Conversation(row["conversation_id"], row["created_at"], messages)


def _router_authority(connection: sqlite3.Connection, router_run_id: str, conversation_id: str) -> tuple[sqlite3.Row, str]:
    row = connection.execute("SELECT * FROM model_runs WHERE run_id = ?", (router_run_id,)).fetchone()
    if row is None:
        raise SynthesisError(f"router run does not exist: {router_run_id}")
    if row["run_kind"] != "router" or row["status"] != "succeeded":
        raise SynthesisError("synthesis requires a succeeded router run")
    if row["conversation_id"] != conversation_id:
        raise SynthesisError("router run conversation does not match synthesis conversation")
    router_rows = tuple(connection.execute(
        "SELECT status, run_id FROM model_runs WHERE conversation_id = ? AND trigger_message_id = ? AND run_kind = 'router'",
        (conversation_id, row["trigger_message_id"]),
    ))
    if any(item["status"] not in {"running", "succeeded", "failed"} for item in router_rows):
        raise SynthesisError("persisted router status is unsupported")
    successes = tuple(item for item in router_rows if item["status"] == "succeeded")
    running = tuple(item for item in router_rows if item["status"] == "running")
    if len(successes) != 1 or running or successes[0]["run_id"] != router_run_id:
        raise SynthesisError("router lineage is not one authoritative succeeded attempt")
    try:
        payload = json.loads(row["output_json"])
    except (TypeError, json.JSONDecodeError) as exc:
        raise SynthesisError("persisted router output is malformed") from exc
    if not isinstance(payload, dict) or set(payload) != {"route"} or not isinstance(payload["route"], str) or payload["route"] not in LEGAL_SYNTHESIS_ROUTES:
        raise SynthesisError("persisted router output does not contain one legal route")
    trigger = connection.execute(
        "SELECT message_id, conversation_id, role FROM messages WHERE message_id = ?", (row["trigger_message_id"],)
    ).fetchone()
    if trigger is None or trigger["conversation_id"] != conversation_id or trigger["role"] != "user":
        raise SynthesisError("router trigger message is missing or belongs to another conversation")
    return row, payload["route"]


def _lineage_error(message: str) -> SynthesisError:
    return SynthesisError(f"semantic synthesis lineage invalid: {message}")


def _validate_semantic_lineage(
    connection: sqlite3.Connection,
    evidence: EvidenceProjection,
    router_run: sqlite3.Row,
    conversation_id: str,
) -> str:
    if not isinstance(evidence, EvidenceProjection) or evidence.contract_version != EVIDENCE_PROJECTION_CONTRACT_VERSION:
        raise _lineage_error("evidence must use the current EvidenceProjection contract")
    if not isinstance(evidence.source, HydratedCandidateSelection):
        raise _lineage_error("evidence source is malformed")
    try:
        expected_projection = project_evidence(evidence.source)
    except EvidenceProjectionError as exc:
        raise _lineage_error("evidence source cannot produce a valid projection") from exc
    if evidence != expected_projection:
        raise _lineage_error("evidence does not equal the deterministic projection of its source")
    selection = evidence.source.selection
    if selection.contract_version not in {"candidate-selection-v2", "candidate-selection-v3"}:
        raise _lineage_error("candidate selection contract is unsupported")
    parent_id = selection.retrieval_run_id
    parent = connection.execute("SELECT * FROM model_runs WHERE run_id = ?", (parent_id,)).fetchone()
    if parent is None or parent["run_kind"] != "retrieval_inference" or parent["status"] != "succeeded":
        raise _lineage_error("parent retrieval-inference run is not succeeded")
    if (
        parent["parent_run_id"] != router_run["run_id"]
        or parent["conversation_id"] != conversation_id
        or parent["trigger_message_id"] != router_run["trigger_message_id"]
        or parent["capability_catalog_sha256"] != selection.capability_catalog_sha256
    ):
        raise _lineage_error("retrieval-inference ancestry or catalog identity disagrees")
    conformance = connection.execute(
        "SELECT * FROM retrieval_conformance WHERE conformance_id = ?", (selection.conformance_id,)
    ).fetchone()
    if conformance is None or conformance["contract_version"] not in {"catalog-conformance-v1", "catalog-conformance-v2"} or (conformance["contract_version"] == "catalog-conformance-v1" and conformance["status"] not in {"valid", "invalid"}) or (conformance["contract_version"] == "catalog-conformance-v2" and conformance["status"] not in {"valid", "partial", "invalid"}) or conformance["status"] not in {"valid", "partial"}:
        raise _lineage_error("conformance is not a valid persisted approval")
    if (
        conformance["retrieval_run_id"] != selection.retrieval_run_id
        or conformance["retrieval_proposal_sha256"] != selection.retrieval_proposal_sha256
        or conformance["capability_catalog_sha256"] != selection.capability_catalog_sha256
    ):
        raise _lineage_error("conformance lineage disagrees with selection")
    execution = connection.execute(
        "SELECT * FROM retrieval_executions WHERE execution_id = ?", (selection.execution_id,)
    ).fetchone()
    if execution is None or execution["execution_contract_version"] not in {"retrieval-execution-v1", EXECUTION_CONTRACT_VERSION} or (execution["execution_contract_version"] == "retrieval-execution-v1" and execution["status"] not in {"succeeded", "failed"}) or (execution["execution_contract_version"] == EXECUTION_CONTRACT_VERSION and execution["status"] not in {"succeeded", "partial", "failed"}):
        raise _lineage_error("execution is not a terminal persisted result")
    fields = (
        "conformance_id", "retrieval_run_id", "retrieval_proposal_sha256", "capability_catalog_sha256",
        "retrieval_package_id", "retrieval_package_identity_version", "substrate_sha256", "vectors_sha256",
        "package_verification_contract_version", "execution_contract_version",
    )
    expected = (
        selection.conformance_id, selection.retrieval_run_id, selection.retrieval_proposal_sha256,
        selection.capability_catalog_sha256, selection.retrieval_package_id,
        selection.retrieval_package_identity_version, selection.substrate_sha256, selection.vectors_sha256,
        selection.package_verification_contract_version, selection.execution_contract_version,
    )
    if tuple(execution[field] for field in fields) != expected or selection.execution_status != execution["status"]:
        raise _lineage_error("execution lineage or status disagrees with selection")
    if selection.retrieval_package_identity_version != IDENTITY_VERSION:
        raise _lineage_error("retrieval package identity contract is unsupported")
    if selection.package_verification_contract_version != VERIFICATION_CONTRACT_VERSION:
        raise _lineage_error("package verification contract is unsupported")
    if selection.execution_contract_version != EXECUTION_CONTRACT_VERSION:
        raise _lineage_error("execution contract is unsupported")
    return parent_id


def _generic_input_sha256(input_json: str) -> str:
    return "sha256:" + hashlib.sha256(input_json.encode("utf-8")).hexdigest()


def _validate_persisted_input(input_json: str | None, stored_hash: str | None) -> tuple[dict, str]:
    if not isinstance(input_json, str) or not input_json or not isinstance(stored_hash, str) or not _SHA256_RE.fullmatch(stored_hash):
        raise SynthesisError("persisted synthesis input identity is malformed")
    if _generic_input_sha256(input_json) != stored_hash:
        raise SynthesisError("persisted synthesis input hash does not match input_json")
    try:
        payload = json.loads(input_json)
    except (TypeError, json.JSONDecodeError) as exc:
        raise SynthesisError("persisted synthesis input JSON is malformed") from exc
    if not isinstance(payload, dict) or set(payload) != {"contract_version", "route", "conversation", "evidence"}:
        raise SynthesisError("persisted synthesis input envelope is malformed")
    if payload["contract_version"] != "synthesis-input-v1" or not isinstance(payload["route"], str) or payload["route"] not in LEGAL_SYNTHESIS_ROUTES:
        raise SynthesisError("persisted synthesis input contract or route is malformed")
    conversation = payload["conversation"]
    if not isinstance(conversation, list) or not conversation:
        raise SynthesisError("persisted synthesis input conversation is malformed")
    for ordinal, message in enumerate(conversation):
        if not isinstance(message, dict) or set(message) != {"ordinal", "role", "content"}:
            raise SynthesisError("persisted synthesis input message is malformed")
        if isinstance(message["ordinal"], bool) or not isinstance(message["ordinal"], int) or message["ordinal"] != ordinal or message["role"] not in {"user", "synthesis"}:
            raise SynthesisError("persisted synthesis input message ordering is malformed")
        if not isinstance(message["content"], str) or not message["content"].strip():
            raise SynthesisError("persisted synthesis input message content is malformed")
    if conversation[-1]["role"] != "user":
        raise SynthesisError("persisted synthesis input does not end in a user message")
    if payload["route"] == "direct" and payload["evidence"] is not None:
        raise SynthesisError("direct persisted synthesis input contains evidence")
    if payload["route"] == "semantic_retrieval":
        evidence = payload["evidence"]
        if (
            not isinstance(evidence, dict)
            or set(evidence) != {"contract_version", "coverage", "requests", "object_contexts", "candidates", "retrieval_relations"}
            or evidence.get("contract_version") != EVIDENCE_PROJECTION_CONTRACT_VERSION
        ):
            raise SynthesisError("semantic persisted synthesis input evidence is malformed")
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    if canonical != input_json:
        raise SynthesisError("persisted synthesis input is not canonical")
    return payload, payload["route"]


def _validate_persisted_parent(connection: sqlite3.Connection, row: sqlite3.Row, router_run: sqlite3.Row, route: str) -> None:
    if route == "direct":
        if row["parent_run_id"] != router_run["run_id"]:
            raise SynthesisError("direct synthesis parent is not the router run")
        return
    parent = connection.execute("SELECT * FROM model_runs WHERE run_id = ?", (row["parent_run_id"],)).fetchone()
    if parent is None or parent["run_kind"] != "retrieval_inference" or parent["status"] != "succeeded":
        raise SynthesisError("semantic synthesis parent retrieval run is invalid")
    if (
        parent["parent_run_id"] != router_run["run_id"]
        or parent["conversation_id"] != router_run["conversation_id"]
        or parent["trigger_message_id"] != router_run["trigger_message_id"]
    ):
        raise SynthesisError("semantic synthesis parent ancestry is invalid")
    conformance = connection.execute(
        "SELECT * FROM retrieval_conformance WHERE retrieval_run_id = ?", (parent["run_id"],)
    ).fetchone()
    if conformance is None or conformance["status"] not in {"valid", "partial"}:
        raise SynthesisError("semantic synthesis parent lacks valid conformance")
    execution = connection.execute(
        "SELECT * FROM retrieval_executions WHERE retrieval_run_id = ?", (parent["run_id"],)
    ).fetchone()
    if execution is None or execution["status"] not in {"succeeded", "partial", "failed"}:
        raise SynthesisError("semantic synthesis parent lacks terminal execution")
    if execution["conformance_id"] != conformance["conformance_id"] or parent["capability_catalog_sha256"] != conformance["capability_catalog_sha256"]:
        raise SynthesisError("semantic synthesis persisted retrieval lineage is inconsistent")


def _usage_from_row(row: sqlite3.Row) -> SynthesisUsage:
    try:
        return SynthesisUsage(
            row["input_tokens"], row["cached_input_tokens"], row["output_tokens"],
            row["reasoning_tokens"], row["total_tokens"],
        )
    except SynthesisProviderError as exc:
        raise SynthesisError(str(exc)) from exc


def _result_from_success_row(connection: sqlite3.Connection, row: sqlite3.Row, router_run: sqlite3.Row) -> SynthesisResult:
    if row["run_kind"] != "synthesis" or row["status"] != "succeeded":
        raise SynthesisError("persisted synthesis success row is not succeeded")
    if row["conversation_id"] != router_run["conversation_id"] or row["trigger_message_id"] != router_run["trigger_message_id"]:
        raise SynthesisError("persisted synthesis conversation or trigger is invalid")
    payload, route = _validate_persisted_input(row["input_json"], row["input_sha256"])
    try:
        router_payload = json.loads(router_run["output_json"])
    except (TypeError, json.JSONDecodeError) as exc:
        raise SynthesisError("persisted router output is malformed") from exc
    if route != router_payload.get("route"):
        raise SynthesisError("persisted synthesis route disagrees with router authority")
    persisted_messages = tuple(connection.execute(
        "SELECT ordinal, role, content FROM messages WHERE conversation_id = ? AND ordinal <= "
        "(SELECT ordinal FROM messages WHERE message_id = ?) ORDER BY ordinal ASC",
        (row["conversation_id"], row["trigger_message_id"]),
    ))
    expected_messages = tuple(
        (item["ordinal"], item["role"], item["content"])
        for item in payload["conversation"]
    )
    if tuple((item["ordinal"], item["role"], item["content"]) for item in persisted_messages) != expected_messages:
        raise SynthesisError("persisted synthesis input conversation disagrees with trigger history")
    _validate_persisted_parent(connection, row, router_run, route)
    if not isinstance(row["output_text"], str) or not row["output_text"].strip():
        raise SynthesisError("persisted synthesis output is blank")
    if row["provider_response_id"] is not None and (
        not isinstance(row["provider_response_id"], str) or not row["provider_response_id"].strip()
    ):
        raise SynthesisError("persisted provider response identity is malformed")
    if row["produced_message_id"] is None:
        raise SynthesisError("persisted synthesis output message link is missing")
    message = connection.execute(
        "SELECT message_id, conversation_id, role, content FROM messages WHERE message_id = ?",
        (row["produced_message_id"],),
    ).fetchone()
    if message is None or message["conversation_id"] != row["conversation_id"] or message["role"] != "synthesis" or message["content"] != row["output_text"]:
        raise SynthesisError("persisted synthesis output message is missing or mismatched")
    return SynthesisResult(
        row["run_id"], row["conversation_id"], row["trigger_message_id"], route,
        row["parent_run_id"], row["provider"], row["model"], row["prompt_version"],
        row["input_sha256"], row["status"], row["output_text"], row["produced_message_id"],
        row["provider_response_id"], _usage_from_row(row),
    )


def _existing_synthesis_state(
    connection: sqlite3.Connection,
    conversation_id: str,
    trigger_message_id: int,
    router_run: sqlite3.Row,
) -> SynthesisResult | None:
    rows = tuple(connection.execute(
        "SELECT * FROM model_runs WHERE conversation_id = ? AND trigger_message_id = ? AND run_kind = 'synthesis' ORDER BY started_at, run_id",
        (conversation_id, trigger_message_id),
    ))
    successes = tuple(row for row in rows if row["status"] == "succeeded")
    running = tuple(row for row in rows if row["status"] == "running")
    if len(successes) > 1 or len(running) > 1 or (successes and running):
        raise SynthesisError("impossible synthesis success/running history")
    if successes:
        return _result_from_success_row(connection, successes[0], router_run)
    if running:
        raise SynthesisError("synthesis is already running for this trigger")
    if any(row["status"] != "failed" for row in rows):
        raise SynthesisError("persisted synthesis status is unsupported")
    return None


def load_synthesis_success(
    database_path: str,
    conversation_id: str,
    router_run_id: str,
) -> SynthesisResult | None:
    """Read an existing synthesis success without claiming or reconstructing input."""

    if not isinstance(conversation_id, str) or not conversation_id.strip() or not isinstance(router_run_id, str) or not router_run_id.strip():
        raise SynthesisError("conversation_id and router_run_id must be nonblank text")
    try:
        connection = _connect_runtime(database_path)
        try:
            router_run, _ = _router_authority(connection, router_run_id, conversation_id)
            return _existing_synthesis_state(connection, conversation_id, router_run["trigger_message_id"], router_run)
        finally:
            connection.close()
    except SynthesisError:
        raise
    except (RuntimeConversationError, sqlite3.Error) as exc:
        raise SynthesisError(str(exc)) from exc


def _mark_failed(database_path: str, run_id: str, error_type: str, error_message: str, clock: Clock | None) -> None:
    try:
        connection = _connect_runtime(database_path)
        try:
            connection.execute("BEGIN IMMEDIATE")
            fail_synthesis_run(
                connection,
                run_id=run_id,
                completed_at=_timestamp(clock),
                error_type=error_type,
                error_message=error_message,
            )
            connection.commit()
        finally:
            connection.close()
    except Exception:
        return


def _validate_provider_result(value: object) -> SynthesisProviderResult:
    if not isinstance(value, SynthesisProviderResult):
        raise SynthesisProviderError("runtime_validation", "provider returned an unsupported synthesis result")
    return value


def _complete_success(
    database_path: str,
    run_id: str,
    conversation: Conversation,
    input_json: str,
    input_hash: str,
    provider_result: SynthesisProviderResult,
    clock: Clock | None,
) -> tuple[int, str]:
    connection = _connect_runtime(database_path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute("SELECT * FROM model_runs WHERE run_id = ?", (run_id,)).fetchone()
        if row is None or row["status"] != "running" or row["run_kind"] != "synthesis":
            raise SynthesisError("synthesis claim is no longer running")
        if row["input_json"] != input_json or row["input_sha256"] != input_hash:
            raise SynthesisError("persisted synthesis input changed while provider was running")
        current = _conversation_from_connection(connection, conversation.conversation_id)
        if current.messages != conversation.messages or not current.messages or current.messages[-1].message_id != conversation.messages[-1].message_id or current.messages[-1].role != "user":
            raise SynthesisError("conversation changed while synthesis provider was running")
        ordinal = current.messages[-1].ordinal + 1
        created_at = _timestamp(clock)
        cursor = connection.execute(
            "INSERT INTO messages (conversation_id, ordinal, role, content, created_at) VALUES (?, ?, 'synthesis', ?, ?)",
            (conversation.conversation_id, ordinal, provider_result.response_text, created_at),
        )
        message_id = int(cursor.lastrowid)
        complete_synthesis_run(
            connection,
            run_id=run_id,
            completed_at=created_at,
            provider_response_id=provider_result.provider_response_id,
            output_text=provider_result.response_text,
            produced_message_id=message_id,
            input_tokens=provider_result.usage.input_tokens,
            cached_input_tokens=provider_result.usage.cached_input_tokens,
            output_tokens=provider_result.usage.output_tokens,
            reasoning_tokens=provider_result.usage.reasoning_tokens,
            total_tokens=provider_result.usage.total_tokens,
        )
        connection.commit()
        return message_id, created_at
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def synthesize_conversation(
    database_path: str,
    runtime_config: RuntimeConfig,
    conversation_id: str,
    router_run_id: str,
    *,
    evidence: EvidenceProjection | None = None,
    provider: SynthesisProvider,
    clock: Clock | None = None,
) -> SynthesisResult:
    """Claim, execute, and durably complete one provider-neutral synthesis."""

    if not isinstance(runtime_config, RuntimeConfig):
        raise SynthesisError("runtime_config must be RuntimeConfig")
    if not isinstance(conversation_id, str) or not conversation_id.strip() or not isinstance(router_run_id, str) or not router_run_id.strip():
        raise SynthesisError("conversation_id and router_run_id must be nonblank text")
    if provider is None or not callable(getattr(provider, "synthesize", None)):
        raise SynthesisError("provider must implement synthesize(model_config, synthesis_input)")
    run_id = str(uuid4())
    synthesis_input: SynthesisInput
    conversation: Conversation
    route: str
    parent_run_id: str
    input_json: str
    input_hash: str
    try:
        prompt_version_value = prompt_version(runtime_config.synthesis.prompt)
    except Exception as exc:
        raise SynthesisError("synthesis configuration prompt is invalid") from exc

    try:
        connection = _connect_runtime(database_path)
        try:
            connection.execute("BEGIN IMMEDIATE")
            router_run, route = _router_authority(connection, router_run_id, conversation_id)
            existing = _existing_synthesis_state(connection, conversation_id, router_run["trigger_message_id"], router_run)
            if existing is not None:
                connection.commit()
                return existing
            conversation = _conversation_from_connection(connection, conversation_id)
            if not conversation.messages or conversation.messages[-1].message_id != router_run["trigger_message_id"] or conversation.messages[-1].role != "user":
                raise SynthesisError("new synthesis requires the router trigger to be the current latest user message")
            if route == "direct":
                if evidence is not None:
                    raise SynthesisError("direct synthesis cannot receive evidence")
                parent_run_id = router_run_id
            else:
                if evidence is None:
                    raise SynthesisError("semantic-retrieval synthesis requires EvidenceProjection")
                parent_run_id = _validate_semantic_lineage(connection, evidence, router_run, conversation_id)
            try:
                synthesis_input = build_synthesis_input(conversation, route, evidence)
                input_json = serialize_synthesis_input(synthesis_input)
                input_hash = synthesis_input_sha256(synthesis_input)
            except SynthesisInputError as exc:
                raise SynthesisError("synthesis input contract rejected the claimed conversation") from exc
            insert_synthesis_run(
                connection,
                run_id=run_id,
                conversation_id=conversation_id,
                trigger_message_id=router_run["trigger_message_id"],
                parent_run_id=parent_run_id,
                provider=runtime_config.synthesis.provider,
                model=runtime_config.synthesis.model,
                prompt_version=prompt_version_value,
                started_at=_timestamp(clock),
                input_json=input_json,
                input_sha256=input_hash,
            )
            connection.commit()
        finally:
            connection.close()
    except SynthesisError:
        raise
    except (RuntimeConversationError, sqlite3.Error) as exc:
        raise SynthesisError(str(exc)) from exc

    try:
        provider_result = _validate_provider_result(provider.synthesize(runtime_config.synthesis, synthesis_input))
    except SynthesisProviderError as exc:
        _mark_failed(database_path, run_id, exc.error_type, str(exc), clock)
        raise SynthesisError(f"synthesis provider failed ({exc.error_type}): {exc}") from exc
    except Exception as exc:
        _mark_failed(database_path, run_id, "provider_status", str(exc), clock)
        raise SynthesisError(f"synthesis provider failed: {exc}") from exc

    try:
        produced_message_id, _ = _complete_success(database_path, run_id, conversation, input_json, input_hash, provider_result, clock)
    except Exception as exc:
        _mark_failed(database_path, run_id, "runtime_validation", str(exc), clock)
        if isinstance(exc, SynthesisError):
            raise
        raise SynthesisError(f"synthesis completion failed: {exc}") from exc
    return SynthesisResult(
        run_id, conversation_id, conversation.messages[-1].message_id, route, parent_run_id,
        runtime_config.synthesis.provider, runtime_config.synthesis.model, prompt_version_value,
        input_hash, "succeeded", provider_result.response_text, produced_message_id,
        provider_result.provider_response_id, provider_result.usage,
    )


__all__ = [
    "SynthesisError", "SynthesisProviderError", "SynthesisUsage", "SynthesisProviderResult",
    "SynthesisProvider", "SynthesisResult", "load_synthesis_success", "synthesize_conversation",
]
