"""Router-v1 classification over the exact persisted conversation thread."""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
from dataclasses import dataclass
from typing import Any, Callable
from uuid import uuid4

from .config import RuntimeConfig
from .conversation import Conversation, Message, RuntimeConversationError, _connect_runtime, _timestamp
from .model_runs import complete_router_run, fail_router_run, insert_router_run
from .openai_provider import OpenAIProviderError, OpenAIResponsesProvider, ProviderInference


ROUTER_PROMPT_VERSION = "router-v1"
ROUTER_PROMPT_V1 = """You are the turn router for Semantic Traversal.

Decide whether producing the next conversational response requires evidence from the user's semantic projection.

direct:
The turn can be satisfied using the supplied conversation and ordinary reasoning without consulting the user's semantic projection.

semantic_retrieval:
The turn requires evidence from the user's semantic projection, including authored notes, history, entities, journals, sources, or semantic state not fully present in the supplied conversation.

User/project facts explicitly present in the supplied conversation do not require retrieval merely because they are personal.

If required user/project-specific evidence may exist outside the supplied conversation and you cannot establish that the conversation contains what is needed, choose semantic_retrieval.

Do not answer the user.
Do not propose retrieval operations.
Choose exactly one route."""
LEGAL_ROUTES = frozenset({"direct", "semantic_retrieval"})
Clock = Callable[[], dt.datetime]


class RuntimeRouterError(ValueError):
    """A router invocation could not produce a valid durable result."""


@dataclass(frozen=True)
class RouterResult:
    run_id: str
    conversation_id: str
    trigger_message_id: int
    route: str
    provider: str
    model: str
    prompt_version: str
    status: str
    provider_response_id: str | None
    input_tokens: int | None
    cached_input_tokens: int | None
    output_tokens: int | None
    reasoning_tokens: int | None
    total_tokens: int | None


def _conversation_from_connection(connection: sqlite3.Connection, conversation_id: str) -> Conversation:
    row = connection.execute(
        "SELECT conversation_id, created_at FROM conversations WHERE conversation_id = ?",
        (conversation_id,),
    ).fetchone()
    if row is None:
        raise RuntimeRouterError(f"conversation does not exist: {conversation_id}")
    messages = tuple(
        Message(
            message_id=message["message_id"],
            conversation_id=message["conversation_id"],
            ordinal=message["ordinal"],
            role=message["role"],
            content=message["content"],
            created_at=message["created_at"],
        )
        for message in connection.execute(
            """
            SELECT message_id, conversation_id, ordinal, role, content, created_at
            FROM messages WHERE conversation_id = ? ORDER BY ordinal ASC
            """,
            (conversation_id,),
        )
    )
    if not messages:
        raise RuntimeRouterError("router requires a conversation containing a user message")
    latest = messages[-1]
    if latest.role != "user":
        raise RuntimeRouterError("router requires the latest conversation message to be user")
    if any(message.role not in {"user", "synthesis"} for message in messages):
        raise RuntimeRouterError("conversation contains an invalid persisted role")
    return Conversation(row["conversation_id"], row["created_at"], messages)


def _canonical_output(route: str) -> str:
    if route not in LEGAL_ROUTES:
        raise RuntimeRouterError(f"invalid router route: {route!r}")
    return json.dumps({"route": route}, ensure_ascii=False, separators=(",", ":"))


def _provider_failure(error: Exception) -> tuple[str, str]:
    if isinstance(error, OpenAIProviderError):
        return error.error_type, str(error)
    if isinstance(error, (RuntimeRouterError, RuntimeConversationError, sqlite3.Error)):
        return "runtime_validation", str(error)
    return "provider_status", str(error)


def route_conversation(
    database_path: str,
    runtime_config: RuntimeConfig,
    conversation_id: str,
    *,
    provider: Any | None = None,
    clock: Clock | None = None,
) -> RouterResult:
    """Run exactly one router attempt for the latest persisted user message."""

    try:
        connection = _connect_runtime(database_path)
    except RuntimeConversationError as exc:
        raise RuntimeRouterError(str(exc)) from exc
    run_id = str(uuid4())
    started_at = _timestamp(clock)
    try:
        connection.execute("BEGIN IMMEDIATE")
        conversation = _conversation_from_connection(connection, conversation_id)
        trigger = conversation.messages[-1]
        insert_router_run(
            connection,
            run_id=run_id,
            conversation_id=conversation_id,
            trigger_message_id=trigger.message_id,
            provider=runtime_config.router.provider,
            model=runtime_config.router.model,
            prompt_version=ROUTER_PROMPT_VERSION,
            started_at=started_at,
        )
        connection.commit()
    except RuntimeRouterError:
        connection.rollback()
        connection.close()
        raise
    except (RuntimeConversationError, sqlite3.Error) as exc:
        connection.rollback()
        connection.close()
        raise RuntimeRouterError(str(exc)) from exc

    selected_provider = provider if provider is not None else OpenAIResponsesProvider()
    try:
        inference = selected_provider.infer(runtime_config, ROUTER_PROMPT_V1, conversation.messages)
        if not isinstance(inference, ProviderInference):
            raise RuntimeRouterError("provider returned an unsupported router result")
        output_json = _canonical_output(inference.route)
        completed_at = _timestamp(clock)
        usage = inference.usage
        connection.execute("BEGIN IMMEDIATE")
        complete_router_run(
            connection,
            run_id=run_id,
            completed_at=completed_at,
            provider_response_id=inference.provider_response_id,
            output_json=output_json,
            input_tokens=usage.input_tokens,
            cached_input_tokens=usage.cached_input_tokens,
            output_tokens=usage.output_tokens,
            reasoning_tokens=usage.reasoning_tokens,
            total_tokens=usage.total_tokens,
        )
        connection.commit()
        return RouterResult(
            run_id=run_id,
            conversation_id=conversation_id,
            trigger_message_id=trigger.message_id,
            route=inference.route,
            provider=runtime_config.router.provider,
            model=runtime_config.router.model,
            prompt_version=ROUTER_PROMPT_VERSION,
            status="succeeded",
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
            fail_router_run(
                connection,
                run_id=run_id,
                completed_at=_timestamp(clock),
                error_type=error_type,
                error_message=error_message,
            )
            connection.commit()
        except sqlite3.Error:
            connection.rollback()
        raise RuntimeRouterError(f"router run failed ({error_type}): {error_message}") from exc
    finally:
        connection.close()


__all__ = [
    "LEGAL_ROUTES",
    "ROUTER_PROMPT_V1",
    "ROUTER_PROMPT_VERSION",
    "RouterResult",
    "RuntimeRouterError",
    "route_conversation",
]
