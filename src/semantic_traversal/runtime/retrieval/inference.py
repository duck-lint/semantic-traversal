"""First-stage retrieval-inference over a validated router lineage."""

from __future__ import annotations

import datetime as dt
import json
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from .capability_catalog import CapabilityCatalogError, load_capability_catalog
from ..config import RuntimeConfig
from ..conversation import Conversation, Message, RuntimeConversationError, _connect_runtime, _timestamp
from ..model_runs import complete_retrieval_run, fail_retrieval_run, insert_retrieval_run
from ..openai_provider import OpenAIProviderError, OpenAIResponsesProvider, RetrievalProviderInference
from ..prompts import prompt_version
from .requests import RetrievalRequestError, canonicalize_retrieval_requests

Clock = Callable[[], dt.datetime]


class RuntimeRetrievalError(ValueError):
    """A retrieval-inference precondition or durable result was invalid."""


@dataclass(frozen=True)
class RetrievalInferenceResult:
    run_id: str
    parent_run_id: str
    conversation_id: str
    trigger_message_id: int
    provider: str
    model: str
    prompt_version: str
    capability_catalog_sha256: str
    status: str
    requests: tuple[dict[str, Any], ...]
    provider_response_id: str | None
    input_tokens: int | None
    cached_input_tokens: int | None
    output_tokens: int | None
    reasoning_tokens: int | None
    total_tokens: int | None


def _catalog(path: str | Path) -> tuple[str, str]:
    try:
        artifact = load_capability_catalog(path)
    except CapabilityCatalogError as exc:
        raise RuntimeRetrievalError(str(exc)) from exc
    return artifact.text, artifact.sha256


def _conversation(connection: sqlite3.Connection, conversation_id: str) -> Conversation:
    row = connection.execute("SELECT conversation_id, created_at FROM conversations WHERE conversation_id = ?", (conversation_id,)).fetchone()
    if row is None:
        raise RuntimeRetrievalError(f"conversation does not exist: {conversation_id}")
    messages = tuple(
        Message(message["message_id"], message["conversation_id"], message["ordinal"], message["role"], message["content"], message["created_at"])
        for message in connection.execute(
            "SELECT message_id, conversation_id, ordinal, role, content, created_at FROM messages WHERE conversation_id = ? ORDER BY ordinal ASC",
            (conversation_id,),
        )
    )
    if any(message.role not in {"user", "synthesis"} for message in messages):
        raise RuntimeRetrievalError("conversation contains an invalid persisted role")
    return Conversation(row["conversation_id"], row["created_at"], messages)


def _provider_failure(error: Exception) -> tuple[str, str]:
    if isinstance(error, OpenAIProviderError):
        return error.error_type, str(error)
    if isinstance(error, (RuntimeRetrievalError, RuntimeConversationError, sqlite3.Error)):
        return "runtime_validation", str(error)
    return "provider_status", str(error)


_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


def _usage_value(name: str, value: object) -> None:
    if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 0):
        raise RuntimeRetrievalError(f"persisted retrieval usage {name} is malformed")


def _router_authority(connection: sqlite3.Connection, router_run_id: str) -> sqlite3.Row:
    parent = connection.execute("SELECT * FROM model_runs WHERE run_id = ?", (router_run_id,)).fetchone()
    if parent is None:
        raise RuntimeRetrievalError(f"router run does not exist: {router_run_id}")
    if parent["run_kind"] != "router" or parent["status"] != "succeeded":
        raise RuntimeRetrievalError("retrieval inference requires a succeeded router run")
    if parent["output_json"] != '{"route":"semantic_retrieval"}':
        raise RuntimeRetrievalError("retrieval inference requires a semantic_retrieval router result")
    trigger = connection.execute(
        "SELECT conversation_id, role FROM messages WHERE message_id = ?", (parent["trigger_message_id"],)
    ).fetchone()
    if trigger is None or trigger["conversation_id"] != parent["conversation_id"] or trigger["role"] != "user":
        raise RuntimeRetrievalError("router trigger message is malformed")
    router_rows = tuple(connection.execute(
        "SELECT * FROM model_runs WHERE conversation_id = ? AND trigger_message_id = ? AND run_kind = 'router'",
        (parent["conversation_id"], parent["trigger_message_id"]),
    ))
    if any(row["status"] not in {"running", "succeeded", "failed"} for row in router_rows):
        raise RuntimeRetrievalError("persisted router status is unsupported")
    successes = tuple(row for row in router_rows if row["status"] == "succeeded")
    running = tuple(row for row in router_rows if row["status"] == "running")
    if len(successes) != 1 or running:
        raise RuntimeRetrievalError("router lineage is not one authoritative succeeded attempt")
    if successes[0]["run_id"] != router_run_id:
        raise RuntimeRetrievalError("router run is not the authoritative succeeded attempt")
    return parent


def _retrieval_result_from_success_row(
    row: sqlite3.Row,
    parent: sqlite3.Row,
) -> RetrievalInferenceResult:
    if row["run_kind"] != "retrieval_inference" or row["status"] != "succeeded":
        raise RuntimeRetrievalError("persisted retrieval success row is not succeeded")
    if (
        row["parent_run_id"] != parent["run_id"]
        or row["conversation_id"] != parent["conversation_id"]
        or row["trigger_message_id"] != parent["trigger_message_id"]
    ):
        raise RuntimeRetrievalError("persisted retrieval lineage is malformed")
    if not isinstance(row["capability_catalog_sha256"], str) or not _SHA256_RE.fullmatch(row["capability_catalog_sha256"]):
        raise RuntimeRetrievalError("persisted retrieval catalog identity is malformed")
    for name in ("provider", "model", "prompt_version", "completed_at"):
        if not isinstance(row[name], str) or not row[name].strip():
            raise RuntimeRetrievalError(f"persisted retrieval {name} is malformed")
    if row["provider_response_id"] is not None and (
        not isinstance(row["provider_response_id"], str) or not row["provider_response_id"].strip()
    ):
        raise RuntimeRetrievalError("persisted retrieval provider response identity is malformed")
    for name in ("input_tokens", "cached_input_tokens", "output_tokens", "reasoning_tokens", "total_tokens"):
        _usage_value(name, row[name])
    try:
        payload = json.loads(row["output_json"])
        if not isinstance(payload, dict) or set(payload) != {"requests"}:
            raise ValueError
        requests = canonicalize_retrieval_requests(payload["requests"])
    except (TypeError, json.JSONDecodeError, ValueError, RetrievalRequestError) as exc:
        raise RuntimeRetrievalError("persisted retrieval proposal is malformed") from exc
    expected_json = json.dumps({"requests": list(requests)}, ensure_ascii=False, separators=(",", ":"))
    if row["output_json"] != expected_json:
        raise RuntimeRetrievalError("persisted retrieval proposal is not canonical")
    return RetrievalInferenceResult(
        run_id=row["run_id"], parent_run_id=row["parent_run_id"], conversation_id=row["conversation_id"],
        trigger_message_id=row["trigger_message_id"], provider=row["provider"], model=row["model"],
        prompt_version=row["prompt_version"], capability_catalog_sha256=row["capability_catalog_sha256"],
        status=row["status"], requests=requests, provider_response_id=row["provider_response_id"],
        input_tokens=row["input_tokens"], cached_input_tokens=row["cached_input_tokens"],
        output_tokens=row["output_tokens"], reasoning_tokens=row["reasoning_tokens"], total_tokens=row["total_tokens"],
    )


def _retrieval_state(connection: sqlite3.Connection, parent: sqlite3.Row) -> RetrievalInferenceResult | None:
    rows = tuple(connection.execute(
        "SELECT * FROM model_runs WHERE parent_run_id = ? AND run_kind = 'retrieval_inference'",
        (parent["run_id"],),
    ))
    if any(
        row["conversation_id"] != parent["conversation_id"]
        or row["trigger_message_id"] != parent["trigger_message_id"]
        for row in rows
    ):
        raise RuntimeRetrievalError("persisted retrieval attempt lineage is malformed")
    if any(row["status"] not in {"running", "succeeded", "failed"} for row in rows):
        raise RuntimeRetrievalError("persisted retrieval status is unsupported")
    successes = tuple(row for row in rows if row["status"] == "succeeded")
    running = tuple(row for row in rows if row["status"] == "running")
    if len(successes) > 1 or len(running) > 1 or (successes and running):
        raise RuntimeRetrievalError("impossible retrieval success/running history")
    if successes:
        return _retrieval_result_from_success_row(successes[0], parent)
    if running:
        raise RuntimeRetrievalError("retrieval inference is already running for this router")
    return None


def infer_retrieval(
    database_path: str,
    runtime_config: RuntimeConfig,
    capability_catalog_path: str | Path,
    router_run_id: str,
    *,
    provider: Any | None = None,
    clock: Clock | None = None,
) -> RetrievalInferenceResult:
    """Propose retrieval requests for the current user turn without executing them."""
    connection = _connect_runtime(database_path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        parent = _router_authority(connection, router_run_id)
        conversation = _conversation(connection, parent["conversation_id"])
        existing = _retrieval_state(connection, parent)
        if existing is not None:
            connection.commit()
            connection.close()
            return existing
        connection.commit()

        # Catalog admission is deliberately after replay inspection, so a
        # durable proposal remains replayable even if the current catalog is unavailable.
        catalog_text, catalog_sha256 = _catalog(capability_catalog_path)
        prompt_hash = prompt_version(runtime_config.retrieval_inference.prompt)
        connection.execute("BEGIN IMMEDIATE")
        parent = _router_authority(connection, router_run_id)
        conversation = _conversation(connection, parent["conversation_id"])
        existing = _retrieval_state(connection, parent)
        if existing is not None:
            connection.commit()
            connection.close()
            return existing
        if not conversation.messages or conversation.messages[-1].message_id != parent["trigger_message_id"] or conversation.messages[-1].role != "user":
            raise RuntimeRetrievalError("router run is stale or its trigger is not the current user message")
        run_id = str(uuid4())
        insert_retrieval_run(
            connection,
            run_id=run_id,
            conversation_id=conversation.conversation_id,
            trigger_message_id=parent["trigger_message_id"],
            provider=runtime_config.retrieval_inference.provider,
            model=runtime_config.retrieval_inference.model,
            prompt_version=prompt_hash,
            started_at=_timestamp(clock),
            parent_run_id=router_run_id,
            capability_catalog_sha256=catalog_sha256,
        )
        connection.commit()
    except (RuntimeRetrievalError, RuntimeConversationError):
        connection.rollback()
        connection.close()
        raise
    except sqlite3.Error as exc:
        connection.rollback()
        connection.close()
        raise RuntimeRetrievalError(str(exc)) from exc

    selected_provider = provider if provider is not None else OpenAIResponsesProvider()
    try:
        inference = selected_provider.infer_retrieval(
            runtime_config.retrieval_inference,
            catalog_text,
            conversation.messages,
        )
        if not isinstance(inference, RetrievalProviderInference):
            raise RuntimeRetrievalError("provider returned an unsupported retrieval result")
        try:
            requests = canonicalize_retrieval_requests(inference.requests)
        except RetrievalRequestError as exc:
            raise RuntimeRetrievalError(str(exc)) from exc
        usage = inference.usage
        output_json = json.dumps({"requests": list(requests)}, ensure_ascii=False, separators=(",", ":"))
        connection.execute("BEGIN IMMEDIATE")
        complete_retrieval_run(
            connection,
            run_id=run_id,
            completed_at=_timestamp(clock),
            provider_response_id=inference.provider_response_id,
            output_json=output_json,
            input_tokens=usage.input_tokens,
            cached_input_tokens=usage.cached_input_tokens,
            output_tokens=usage.output_tokens,
            reasoning_tokens=usage.reasoning_tokens,
            total_tokens=usage.total_tokens,
        )
        connection.commit()
        return RetrievalInferenceResult(
            run_id=run_id,
            parent_run_id=router_run_id,
            conversation_id=conversation.conversation_id,
            trigger_message_id=conversation.messages[-1].message_id,
            provider=runtime_config.retrieval_inference.provider,
            model=runtime_config.retrieval_inference.model,
            prompt_version=prompt_hash,
            capability_catalog_sha256=catalog_sha256,
            status="succeeded",
            requests=requests,
            provider_response_id=inference.provider_response_id,
            input_tokens=usage.input_tokens,
            cached_input_tokens=usage.cached_input_tokens,
            output_tokens=usage.output_tokens,
            reasoning_tokens=usage.reasoning_tokens,
            total_tokens=usage.total_tokens,
        )
    except Exception as exc:
        error_type, error_message = _provider_failure(exc)
        try:
            connection.rollback()
            connection.execute("BEGIN IMMEDIATE")
            fail_retrieval_run(
                connection,
                run_id=run_id,
                completed_at=_timestamp(clock),
                error_type=error_type,
                error_message=error_message,
            )
            connection.commit()
        except sqlite3.Error:
            connection.rollback()
        raise RuntimeRetrievalError(f"retrieval inference failed ({error_type}): {error_message}") from exc
    finally:
        connection.close()


__all__ = ["RetrievalInferenceResult", "RuntimeRetrievalError", "infer_retrieval"]
