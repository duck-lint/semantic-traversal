"""Durable model-run lifecycle writes for runtime schema v6."""

from __future__ import annotations

import sqlite3


def insert_model_run(
    connection: sqlite3.Connection,
    *,
    run_id: str,
    conversation_id: str,
    trigger_message_id: int,
    run_kind: str,
    provider: str,
    model: str,
    prompt_version: str,
    started_at: str,
    parent_run_id: str | None = None,
    capability_catalog_sha256: str | None = None,
    input_json: str | None = None,
    input_sha256: str | None = None,
) -> None:
    connection.execute(
        """
        INSERT INTO model_runs (
            run_id, conversation_id, trigger_message_id, run_kind, parent_run_id,
            capability_catalog_sha256, provider, model, prompt_version, status, started_at,
            input_json, input_sha256
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'running', ?, ?, ?)
        """,
        (
            run_id, conversation_id, trigger_message_id, run_kind, parent_run_id,
            capability_catalog_sha256, provider, model, prompt_version, started_at,
            input_json, input_sha256,
        ),
    )


def insert_router_run(connection: sqlite3.Connection, **kwargs: object) -> None:
    insert_model_run(connection, run_kind="router", parent_run_id=None, capability_catalog_sha256=None, **kwargs)  # type: ignore[arg-type]


def insert_retrieval_run(
    connection: sqlite3.Connection,
    *,
    parent_run_id: str,
    capability_catalog_sha256: str,
    **kwargs: object,
) -> None:
    insert_model_run(
        connection,
        run_kind="retrieval_inference",
        parent_run_id=parent_run_id,
        capability_catalog_sha256=capability_catalog_sha256,
        **kwargs,  # type: ignore[arg-type]
    )


def complete_model_run(
    connection: sqlite3.Connection,
    *,
    run_id: str,
    run_kind: str,
    completed_at: str,
    provider_response_id: str | None,
    output_json: str,
    input_tokens: int | None,
    cached_input_tokens: int | None,
    output_tokens: int | None,
    reasoning_tokens: int | None,
    total_tokens: int | None,
) -> None:
    cursor = connection.execute(
        """
        UPDATE model_runs
        SET status = 'succeeded', completed_at = ?, provider_response_id = ?, output_json = ?,
            input_tokens = ?, cached_input_tokens = ?, output_tokens = ?,
            reasoning_tokens = ?, total_tokens = ?
        WHERE run_id = ? AND run_kind = ? AND status = 'running'
        """,
        (
            completed_at, provider_response_id, output_json, input_tokens,
            cached_input_tokens, output_tokens, reasoning_tokens, total_tokens,
            run_id, run_kind,
        ),
    )
    if cursor.rowcount != 1:
        raise sqlite3.IntegrityError(f"{run_kind} run is not in running state: {run_id}")


def fail_model_run(
    connection: sqlite3.Connection,
    *,
    run_id: str,
    run_kind: str,
    completed_at: str,
    error_type: str,
    error_message: str,
) -> None:
    cursor = connection.execute(
        """
        UPDATE model_runs
        SET status = 'failed', completed_at = ?, error_type = ?, error_message = ?
        WHERE run_id = ? AND run_kind = ? AND status = 'running'
        """,
        (completed_at, error_type, error_message, run_id, run_kind),
    )
    if cursor.rowcount != 1:
        raise sqlite3.IntegrityError(f"{run_kind} run is not in running state: {run_id}")


def complete_router_run(connection: sqlite3.Connection, **kwargs: object) -> None:
    complete_model_run(connection, run_kind="router", **kwargs)  # type: ignore[arg-type]


def fail_router_run(connection: sqlite3.Connection, **kwargs: object) -> None:
    fail_model_run(connection, run_kind="router", **kwargs)  # type: ignore[arg-type]


def complete_retrieval_run(connection: sqlite3.Connection, **kwargs: object) -> None:
    complete_model_run(connection, run_kind="retrieval_inference", **kwargs)  # type: ignore[arg-type]


def fail_retrieval_run(connection: sqlite3.Connection, **kwargs: object) -> None:
    fail_model_run(connection, run_kind="retrieval_inference", **kwargs)  # type: ignore[arg-type]


__all__ = [
    "complete_retrieval_run", "complete_router_run", "fail_retrieval_run", "fail_router_run",
    "insert_model_run", "insert_retrieval_run", "insert_router_run",
]
