"""First-stage retrieval-inference over a validated router lineage."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from .config import RuntimeConfig
from .conversation import Conversation, Message, RuntimeConversationError, _connect_runtime, _timestamp
from .model_runs import complete_retrieval_run, fail_retrieval_run, insert_retrieval_run
from .openai_provider import OpenAIProviderError, OpenAIResponsesProvider, RetrievalProviderInference
from .prompts import prompt_version
from .retrieval_requests import RetrievalRequestError, canonicalize_retrieval_requests

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
        raw = Path(path).read_bytes()
    except OSError as exc:
        raise RuntimeRetrievalError(f"could not read capability catalog: {exc}") from exc
    digest = "sha256:" + hashlib.sha256(raw).hexdigest()
    try:
        text = raw.decode("utf-8")
        parsed = json.loads(text)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeRetrievalError(f"capability catalog is not valid UTF-8 JSON: {exc}") from exc
    if not isinstance(parsed, dict) or parsed.get("catalog_schema_version") != "1":
        raise RuntimeRetrievalError("capability catalog schema version is unsupported")
    if not {"semantic_dimensions", "graph", "vector", "operators"}.issubset(parsed):
        raise RuntimeRetrievalError("capability catalog envelope is incomplete")
    return text, digest


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
        parent = connection.execute(
            "SELECT run_id, conversation_id, trigger_message_id, run_kind, status, output_json FROM model_runs WHERE run_id = ?",
            (router_run_id,),
        ).fetchone()
        if parent is None:
            raise RuntimeRetrievalError(f"router run does not exist: {router_run_id}")
        if parent["run_kind"] != "router" or parent["status"] != "succeeded":
            raise RuntimeRetrievalError("retrieval inference requires a succeeded router run")
        if parent["output_json"] != '{"route":"semantic_retrieval"}':
            raise RuntimeRetrievalError("retrieval inference requires a semantic_retrieval router result")
        conversation = _conversation(connection, parent["conversation_id"])
        if not conversation.messages:
            raise RuntimeRetrievalError("retrieval inference requires a nonempty conversation")
        latest = conversation.messages[-1]
        if latest.message_id != parent["trigger_message_id"] or latest.role != "user":
            raise RuntimeRetrievalError("router run is stale or its trigger is not the current user message")

        # Parent authority is established before reading catalog/model inputs.
        catalog_text, catalog_sha256 = _catalog(capability_catalog_path)
        run_id = str(uuid4())
        prompt_hash = prompt_version(runtime_config.retrieval_inference.prompt)
        connection.execute("BEGIN IMMEDIATE")
        insert_retrieval_run(
            connection,
            run_id=run_id,
            conversation_id=conversation.conversation_id,
            trigger_message_id=latest.message_id,
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
