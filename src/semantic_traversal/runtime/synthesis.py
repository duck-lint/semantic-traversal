"""Provider-neutral synthesis boundary over exact conversation and packet input."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import sqlite3
from dataclasses import dataclass, fields, is_dataclass
from typing import Any, Mapping, Protocol
from uuid import uuid4

from .config import ModelConfig, RuntimeConfig
from .conversation import Conversation, Message, RuntimeConversationError, _connect_runtime, _timestamp
from .model_runs import complete_synthesis_run, fail_synthesis_run, insert_synthesis_run
from .retrieval.packet import RetrievalPacket
from .prompts import prompt_version


SYNTHESIS_INPUT_CONTRACT_VERSION = "synthesis-input-v1"
LEGAL_SYNTHESIS_ROUTES = frozenset({"direct", "semantic_retrieval"})


class SynthesisError(ValueError):
    """A synthesis precondition, provider result, or durable lifecycle failed."""


class SynthesisProviderError(RuntimeError):
    """A provider-neutral synthesis provider failed."""

    def __init__(self, error_type: str, message: str):
        super().__init__(message)
        self.error_type = error_type


@dataclass(frozen=True)
class SynthesisInput:
    contract_version: str
    route: str
    conversation: Conversation
    retrieval_packet: RetrievalPacket | None

    def __post_init__(self) -> None:
        if self.contract_version != SYNTHESIS_INPUT_CONTRACT_VERSION:
            raise SynthesisError("unsupported synthesis input contract version")
        if self.route not in LEGAL_SYNTHESIS_ROUTES:
            raise SynthesisError(f"unsupported synthesis route: {self.route!r}")
        if self.route == "direct" and self.retrieval_packet is not None:
            raise SynthesisError("direct synthesis input cannot contain a retrieval packet")
        if self.route == "semantic_retrieval" and self.retrieval_packet is None:
            raise SynthesisError("semantic-retrieval synthesis input requires a retrieval packet")


@dataclass(frozen=True)
class SynthesisUsage:
    input_tokens: int | None = None
    cached_input_tokens: int | None = None
    output_tokens: int | None = None
    reasoning_tokens: int | None = None
    total_tokens: int | None = None


@dataclass(frozen=True)
class SynthesisProviderResult:
    response_text: str
    provider_response_id: str | None
    usage: SynthesisUsage


class SynthesisProvider(Protocol):
    def synthesize(self, model_config: ModelConfig, synthesis_input: SynthesisInput) -> SynthesisProviderResult:
        ...


@dataclass(frozen=True)
class SynthesisResult:
    run_id: str
    conversation_id: str
    trigger_message_id: int
    route: str
    parent_run_id: str
    synthesis_input_sha256: str
    status: str
    response_text: str | None
    produced_message_id: int | None
    provider_response_id: str | None
    usage: SynthesisUsage


def _serialize_value(value: Any) -> Any:
    """Convert semantic values to explicit deterministic JSON data."""
    if value is None or isinstance(value, (str, bool, int)):
        if isinstance(value, int) and isinstance(value, bool):
            return value
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise SynthesisError("synthesis input cannot contain a non-finite float")
        return value
    if isinstance(value, dt.datetime):
        return {"type": "datetime", "value": value.isoformat()}
    if isinstance(value, dt.date):
        return {"type": "date", "value": value.isoformat()}
    if isinstance(value, Mapping):
        if all(isinstance(key, str) for key in value):
            return {key: _serialize_value(value[key]) for key in sorted(value)}
        entries = [{"key": _serialize_value(key), "value": _serialize_value(item)} for key, item in value.items()]
        return sorted(entries, key=lambda item: json.dumps(item["key"], ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    if isinstance(value, (list, tuple)):
        return [_serialize_value(item) for item in value]
    if is_dataclass(value):
        return {field.name: _serialize_value(getattr(value, field.name)) for field in fields(value)}
    raise SynthesisError(f"unsupported synthesis input value: {type(value).__name__}")


def _conversation_json(conversation: Conversation) -> list[dict[str, Any]]:
    return [
        {"role": message.role, "content": message.content, "ordinal": message.ordinal}
        for message in conversation.messages
    ]


def _input_json_value(synthesis_input: SynthesisInput) -> dict[str, Any]:
    return {
        "contract_version": synthesis_input.contract_version,
        "route": synthesis_input.route,
        "conversation": _conversation_json(synthesis_input.conversation),
        "retrieval_packet": _serialize_value(synthesis_input.retrieval_packet),
    }


def serialize_synthesis_input(synthesis_input: SynthesisInput) -> str:
    """Return the exact compact UTF-8 JSON document supplied to a provider boundary."""
    return json.dumps(
        _input_json_value(synthesis_input), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def synthesis_input_sha256(synthesis_input_json: str) -> str:
    if not isinstance(synthesis_input_json, str):
        raise SynthesisError("synthesis input artifact must be text")
    return "sha256:" + hashlib.sha256(synthesis_input_json.encode("utf-8")).hexdigest()


def _conversation_from_connection(connection: sqlite3.Connection, conversation_id: str) -> Conversation:
    row = connection.execute(
        "SELECT conversation_id, created_at FROM conversations WHERE conversation_id = ?", (conversation_id,)
    ).fetchone()
    if row is None:
        raise SynthesisError(f"conversation does not exist: {conversation_id}")
    messages = tuple(
        Message(item["message_id"], item["conversation_id"], item["ordinal"], item["role"], item["content"], item["created_at"])
        for item in connection.execute(
            "SELECT message_id, conversation_id, ordinal, role, content, created_at FROM messages WHERE conversation_id = ? ORDER BY ordinal ASC",
            (conversation_id,),
        )
    )
    if not messages:
        raise SynthesisError("synthesis requires a non-empty conversation")
    if messages[-1].role != "user":
        raise SynthesisError("synthesis requires the latest conversation message to be user")
    if any(message.role not in {"user", "synthesis"} for message in messages):
        raise SynthesisError("conversation contains an invalid persisted role")
    return Conversation(row["conversation_id"], row["created_at"], messages)


def _route_for_router(connection: sqlite3.Connection, router_run_id: str, conversation: Conversation) -> str:
    row = connection.execute("SELECT * FROM model_runs WHERE run_id = ?", (router_run_id,)).fetchone()
    trigger = conversation.messages[-1]
    if row is None or row["run_kind"] != "router" or row["status"] != "succeeded":
        raise SynthesisError("router run is not a succeeded router authority")
    if row["conversation_id"] != conversation.conversation_id or row["trigger_message_id"] != trigger.message_id:
        raise SynthesisError("router run does not belong to the latest user message")
    try:
        output = json.loads(row["output_json"])
    except (TypeError, json.JSONDecodeError) as exc:
        raise SynthesisError("persisted router output is malformed") from exc
    if not isinstance(output, dict) or set(output) != {"route"} or output["route"] not in LEGAL_SYNTHESIS_ROUTES:
        raise SynthesisError("persisted router route is invalid")
    return output["route"]


def _validate_packet_lineage(connection: sqlite3.Connection, packet: RetrievalPacket, router_run_id: str, conversation: Conversation) -> None:
    trigger = conversation.messages[-1]
    retrieval = connection.execute("SELECT * FROM model_runs WHERE run_id = ?", (packet.retrieval_run_id,)).fetchone()
    if retrieval is None or retrieval["run_kind"] != "retrieval_inference" or retrieval["status"] != "succeeded":
        raise SynthesisError("retrieval packet does not identify a succeeded retrieval-inference run")
    if (
        retrieval["parent_run_id"] != router_run_id
        or retrieval["conversation_id"] != conversation.conversation_id
        or retrieval["trigger_message_id"] != trigger.message_id
    ):
        raise SynthesisError("retrieval packet run lineage conflicts with the routed turn")
    conformance = connection.execute("SELECT * FROM retrieval_conformance WHERE conformance_id = ?", (packet.conformance_id,)).fetchone()
    if conformance is None or conformance["retrieval_run_id"] != packet.retrieval_run_id or conformance["status"] != "valid":
        raise SynthesisError("retrieval packet conformance lineage is invalid")
    execution = connection.execute("SELECT * FROM retrieval_executions WHERE execution_id = ?", (packet.execution_id,)).fetchone()
    if execution is None or execution["conformance_id"] != packet.conformance_id or execution["retrieval_run_id"] != packet.retrieval_run_id:
        raise SynthesisError("retrieval packet execution lineage is invalid")
    if execution["retrieval_package_id"] != packet.retrieval_package_id or execution["status"] != packet.execution_status:
        raise SynthesisError("retrieval packet package or status lineage conflicts with persisted execution")
    try:
        persisted_result = json.loads(execution["result_json"])
    except (TypeError, json.JSONDecodeError) as exc:
        raise SynthesisError("persisted retrieval execution result is malformed") from exc
    if not isinstance(persisted_result, dict) or persisted_result.get("execution_failure") != packet.execution_failure:
        raise SynthesisError("retrieval packet failure lineage conflicts with persisted execution")


def _usage_from_row(row: sqlite3.Row) -> SynthesisUsage:
    return SynthesisUsage(row["input_tokens"], row["cached_input_tokens"], row["output_tokens"], row["reasoning_tokens"], row["total_tokens"])


def _existing_synthesis(connection: sqlite3.Connection, conversation_id: str, trigger_message_id: int) -> SynthesisResult | None:
    rows = connection.execute(
        "SELECT * FROM model_runs WHERE conversation_id = ? AND trigger_message_id = ? AND run_kind = 'synthesis' ORDER BY started_at",
        (conversation_id, trigger_message_id),
    ).fetchall()
    if not rows:
        return None
    row = rows[0]
    if row["status"] == "running":
        raise SynthesisError("an incomplete prior synthesis run exists")
    if row["status"] == "failed":
        raise SynthesisError(f"a prior synthesis run failed: {row['error_message']}")
    message = connection.execute("SELECT * FROM messages WHERE message_id = ?", (row["produced_message_id"],)).fetchone()
    if message is None or message["role"] != "synthesis" or message["content"] != row["output_text"]:
        raise SynthesisError("successful synthesis run has no exact produced message")
    parent = connection.execute("SELECT run_kind FROM model_runs WHERE run_id = ?", (row["parent_run_id"],)).fetchone()
    if parent is None or parent["run_kind"] not in {"router", "retrieval_inference"}:
        raise SynthesisError("successful synthesis run has invalid parent lineage")
    route = "semantic_retrieval" if parent["run_kind"] == "retrieval_inference" else "direct"
    return SynthesisResult(row["run_id"], row["conversation_id"], row["trigger_message_id"], route, row["parent_run_id"], row["input_sha256"], row["status"], row["output_text"], row["produced_message_id"], row["provider_response_id"], _usage_from_row(row))


def _provider_failure(exc: Exception) -> tuple[str, str]:
    if isinstance(exc, SynthesisProviderError):
        return exc.error_type, str(exc)
    if isinstance(exc, SynthesisError):
        return "runtime_validation", str(exc)
    return "provider_status", str(exc)


def _fail_run(database_path: str, run_id: str, error_type: str, error_message: str, clock: Any) -> None:
    connection = _connect_runtime(database_path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        fail_synthesis_run(connection, run_id=run_id, completed_at=_timestamp(clock), error_type=error_type, error_message=error_message)
        connection.commit()
    finally:
        connection.close()


def synthesize_conversation(
    database_path: str,
    runtime_config: RuntimeConfig,
    conversation_id: str,
    router_run_id: str,
    *,
    retrieval_packet: RetrievalPacket | None = None,
    provider: SynthesisProvider,
    clock: Any = None,
) -> SynthesisResult:
    """Run one provider-neutral synthesis attempt with durable atomic success."""
    connection = _connect_runtime(database_path)
    try:
        # A successful prior synthesis appends a message, so the latest
        # message is no longer the triggering user message on an idempotent
        # second call. Resolve that narrow existing-result case first; a new
        # attempt still goes through the complete latest-user validation below.
        router_row = connection.execute("SELECT * FROM model_runs WHERE run_id = ?", (router_run_id,)).fetchone()
        if (
            router_row is None
            or router_row["run_kind"] != "router"
            or router_row["status"] != "succeeded"
            or router_row["conversation_id"] != conversation_id
        ):
            raise SynthesisError("router run does not belong to the requested conversation")
        existing = _existing_synthesis(connection, conversation_id, router_row["trigger_message_id"])
        if existing is not None:
            return existing
        conversation = _conversation_from_connection(connection, conversation_id)
        route = _route_for_router(connection, router_run_id, conversation)
        if route == "direct" and retrieval_packet is not None:
            raise SynthesisError("direct route cannot receive a retrieval packet")
        if route == "semantic_retrieval":
            if retrieval_packet is None:
                raise SynthesisError("semantic-retrieval route requires a retrieval packet")
            _validate_packet_lineage(connection, retrieval_packet, router_run_id, conversation)
        synthesis_input = SynthesisInput(SYNTHESIS_INPUT_CONTRACT_VERSION, route, conversation, retrieval_packet)
        input_json = serialize_synthesis_input(synthesis_input)
        input_sha = synthesis_input_sha256(input_json)
        run_id = str(uuid4())
        insert_parent = router_run_id if route == "direct" else retrieval_packet.retrieval_run_id
        connection.execute("BEGIN IMMEDIATE")
        insert_synthesis_run(
            connection,
            run_id=run_id,
            conversation_id=conversation_id,
            trigger_message_id=conversation.messages[-1].message_id,
            parent_run_id=insert_parent,
            provider=runtime_config.synthesis.provider,
            model=runtime_config.synthesis.model,
            prompt_version=prompt_version(runtime_config.synthesis.prompt),
            started_at=_timestamp(clock),
            input_json=input_json,
            input_sha256=input_sha,
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

    try:
        result = provider.synthesize(runtime_config.synthesis, synthesis_input)
        if not isinstance(result, SynthesisProviderResult) or not isinstance(result.response_text, str) or not result.response_text.strip():
            raise SynthesisError("synthesis provider returned blank or unsupported response text")
        if not isinstance(result.usage, SynthesisUsage):
            raise SynthesisError("synthesis provider returned unsupported usage")
    except Exception as exc:
        error_type, error_message = _provider_failure(exc)
        _fail_run(database_path, run_id, error_type, error_message, clock)
        raise SynthesisError(f"synthesis run failed ({error_type}): {error_message}") from exc

    connection = _connect_runtime(database_path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        ordinal = connection.execute("SELECT COALESCE(MAX(ordinal), -1) + 1 FROM messages WHERE conversation_id = ?", (conversation_id,)).fetchone()[0]
        created_at = _timestamp(clock)
        cursor = connection.execute(
            "INSERT INTO messages (conversation_id, ordinal, role, content, created_at) VALUES (?, ?, 'synthesis', ?, ?)",
            (conversation_id, ordinal, result.response_text, created_at),
        )
        complete_synthesis_run(
            connection,
            run_id=run_id,
            completed_at=created_at,
            provider_response_id=result.provider_response_id,
            output_text=result.response_text,
            produced_message_id=cursor.lastrowid,
            input_tokens=result.usage.input_tokens,
            cached_input_tokens=result.usage.cached_input_tokens,
            output_tokens=result.usage.output_tokens,
            reasoning_tokens=result.usage.reasoning_tokens,
            total_tokens=result.usage.total_tokens,
        )
        connection.commit()
        return SynthesisResult(run_id, conversation_id, conversation.messages[-1].message_id, route, insert_parent, input_sha, "succeeded", result.response_text, cursor.lastrowid, result.provider_response_id, result.usage)
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


__all__ = [
    "SYNTHESIS_INPUT_CONTRACT_VERSION", "SynthesisError", "SynthesisProviderError",
    "SynthesisInput", "SynthesisUsage", "SynthesisProviderResult", "SynthesisProvider",
    "SynthesisResult", "serialize_synthesis_input", "synthesis_input_sha256", "synthesize_conversation",
]
