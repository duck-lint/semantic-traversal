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
from .prompts import prompt_version


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


def _conversation_from_connection(
    connection: sqlite3.Connection,
    conversation_id: str,
    *,
    require_latest_user: bool = True,
) -> Conversation:
    row = connection.execute(
        "SELECT conversation_id, created_at FROM conversations WHERE conversation_id = ?", (conversation_id,)
    ).fetchone()
    if row is None:
        raise RuntimeRouterError(f"conversation does not exist: {conversation_id}")
    messages = tuple(
        Message(
            message_id=message["message_id"], conversation_id=message["conversation_id"], ordinal=message["ordinal"],
            role=message["role"], content=message["content"], created_at=message["created_at"],
        )
        for message in connection.execute(
            "SELECT message_id, conversation_id, ordinal, role, content, created_at FROM messages WHERE conversation_id = ? ORDER BY ordinal ASC",
            (conversation_id,),
        )
    )
    if not messages:
        raise RuntimeRouterError("router requires a conversation containing a user message")
    if require_latest_user and messages[-1].role != "user":
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


def _usage_value(name: str, value: object) -> None:
    if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 0):
        raise RuntimeRouterError(f"persisted router usage {name} is malformed")


def _router_result_from_success_row(connection: sqlite3.Connection, row: sqlite3.Row) -> RouterResult:
    if row["run_kind"] != "router" or row["status"] != "succeeded":
        raise RuntimeRouterError("persisted router success row is not succeeded")
    if not isinstance(row["conversation_id"], str) or not row["conversation_id"].strip():
        raise RuntimeRouterError("persisted router conversation identity is malformed")
    trigger = connection.execute(
        "SELECT message_id, conversation_id, role FROM messages WHERE message_id = ?",
        (row["trigger_message_id"],),
    ).fetchone()
    if trigger is None or trigger["conversation_id"] != row["conversation_id"] or trigger["role"] != "user":
        raise RuntimeRouterError("persisted router trigger message is malformed")
    if not isinstance(row["output_json"], str) or row["output_json"] not in {
        _canonical_output("direct"), _canonical_output("semantic_retrieval")
    }:
        raise RuntimeRouterError("persisted router output is malformed")
    route = json.loads(row["output_json"])["route"]
    for name in ("provider", "model", "prompt_version", "completed_at"):
        if not isinstance(row[name], str) or not row[name].strip():
            raise RuntimeRouterError(f"persisted router {name} is malformed")
    if row["provider_response_id"] is not None and (
        not isinstance(row["provider_response_id"], str) or not row["provider_response_id"].strip()
    ):
        raise RuntimeRouterError("persisted router provider response identity is malformed")
    for name in ("input_tokens", "cached_input_tokens", "output_tokens", "reasoning_tokens", "total_tokens"):
        _usage_value(name, row[name])
    return RouterResult(
        run_id=row["run_id"], conversation_id=row["conversation_id"], trigger_message_id=row["trigger_message_id"],
        route=route, provider=row["provider"], model=row["model"], prompt_version=row["prompt_version"],
        status=row["status"], provider_response_id=row["provider_response_id"],
        input_tokens=row["input_tokens"], cached_input_tokens=row["cached_input_tokens"],
        output_tokens=row["output_tokens"], reasoning_tokens=row["reasoning_tokens"], total_tokens=row["total_tokens"],
    )


def _router_state(
    connection: sqlite3.Connection,
    conversation_id: str,
    trigger_message_id: int,
) -> RouterResult | None:
    rows = tuple(connection.execute(
        "SELECT * FROM model_runs WHERE conversation_id = ? AND trigger_message_id = ? AND run_kind = 'router'",
        (conversation_id, trigger_message_id),
    ))
    if any(row["status"] not in {"running", "succeeded", "failed"} for row in rows):
        raise RuntimeRouterError("persisted router status is unsupported")
    successes = tuple(row for row in rows if row["status"] == "succeeded")
    running = tuple(row for row in rows if row["status"] == "running")
    if len(successes) > 1 or len(running) > 1 or (successes and running):
        raise RuntimeRouterError("impossible router success/running history")
    if successes:
        return _router_result_from_success_row(connection, successes[0])
    if running:
        raise RuntimeRouterError("router is already running for this trigger")
    return None


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
    prompt = runtime_config.router.prompt
    prompt_version_value = prompt_version(prompt)
    try:
        connection.execute("BEGIN IMMEDIATE")
        conversation = _conversation_from_connection(connection, conversation_id, require_latest_user=False)
        user_messages = tuple(message for message in conversation.messages if message.role == "user")
        if not user_messages:
            raise RuntimeRouterError("router requires a conversation containing a user message")
        trigger = user_messages[-1]
        existing = _router_state(connection, conversation_id, trigger.message_id)
        if existing is not None:
            connection.commit()
            connection.close()
            return existing
        if conversation.messages[-1].role != "user":
            raise RuntimeRouterError("router requires the latest conversation message to be user")
        insert_router_run(
            connection, run_id=run_id, conversation_id=conversation_id, trigger_message_id=trigger.message_id,
            provider=runtime_config.router.provider, model=runtime_config.router.model,
            prompt_version=prompt_version_value, started_at=started_at,
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
        inference = selected_provider.infer_router(runtime_config.router, conversation.messages)
        if not isinstance(inference, ProviderInference):
            raise RuntimeRouterError("provider returned an unsupported router result")
        output_json = _canonical_output(inference.route)
        usage = inference.usage
        connection.execute("BEGIN IMMEDIATE")
        complete_router_run(
            connection, run_id=run_id, completed_at=_timestamp(clock), provider_response_id=inference.provider_response_id,
            output_json=output_json, input_tokens=usage.input_tokens, cached_input_tokens=usage.cached_input_tokens,
            output_tokens=usage.output_tokens, reasoning_tokens=usage.reasoning_tokens, total_tokens=usage.total_tokens,
        )
        connection.commit()
        return RouterResult(
            run_id=run_id, conversation_id=conversation_id, trigger_message_id=trigger.message_id,
            route=inference.route, provider=runtime_config.router.provider, model=runtime_config.router.model,
            prompt_version=prompt_version_value, status="succeeded", provider_response_id=inference.provider_response_id,
            input_tokens=usage.input_tokens, cached_input_tokens=usage.cached_input_tokens,
            output_tokens=usage.output_tokens, reasoning_tokens=usage.reasoning_tokens, total_tokens=usage.total_tokens,
        )
    except Exception as exc:
        error_type, error_message = _provider_failure(exc)
        try:
            connection.rollback()
            connection.execute("BEGIN IMMEDIATE")
            fail_router_run(
                connection, run_id=run_id, completed_at=_timestamp(clock), error_type=error_type, error_message=error_message,
            )
            connection.commit()
        except sqlite3.Error:
            connection.rollback()
        raise RuntimeRouterError(f"router run failed ({error_type}): {error_message}") from exc
    finally:
        connection.close()


__all__ = ["LEGAL_ROUTES", "RouterResult", "RuntimeRouterError", "route_conversation"]
