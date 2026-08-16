"""Durable router execution evidence for runtime schema v2."""

from __future__ import annotations

import sqlite3


def insert_router_run(
    connection: sqlite3.Connection,
    *,
    run_id: str,
    conversation_id: str,
    trigger_message_id: int,
    provider: str,
    model: str,
    prompt_version: str,
    started_at: str,
) -> None:
    connection.execute(
        """
        INSERT INTO model_runs (
            run_id, conversation_id, trigger_message_id, run_kind, provider, model,
            prompt_version, status, started_at
        ) VALUES (?, ?, ?, 'router', ?, ?, ?, 'running', ?)
        """,
        (run_id, conversation_id, trigger_message_id, provider, model, prompt_version, started_at),
    )


def complete_router_run(
    connection: sqlite3.Connection,
    *,
    run_id: str,
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
        WHERE run_id = ? AND run_kind = 'router' AND status = 'running'
        """,
        (
            completed_at,
            provider_response_id,
            output_json,
            input_tokens,
            cached_input_tokens,
            output_tokens,
            reasoning_tokens,
            total_tokens,
            run_id,
        ),
    )
    if cursor.rowcount != 1:
        raise sqlite3.IntegrityError(f"router run is not in running state: {run_id}")


def fail_router_run(
    connection: sqlite3.Connection,
    *,
    run_id: str,
    completed_at: str,
    error_type: str,
    error_message: str,
) -> None:
    cursor = connection.execute(
        """
        UPDATE model_runs
        SET status = 'failed', completed_at = ?, error_type = ?, error_message = ?
        WHERE run_id = ? AND run_kind = 'router' AND status = 'running'
        """,
        (completed_at, error_type, error_message, run_id),
    )
    if cursor.rowcount != 1:
        raise sqlite3.IntegrityError(f"router run is not in running state: {run_id}")


__all__ = ["complete_router_run", "fail_router_run", "insert_router_run"]
