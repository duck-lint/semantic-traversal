"""Deterministic execution of already-conforming retrieval requests."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Mapping
from uuid import uuid4

from ..projection.exact import exact_lookup
from ..projection.graph import GraphError, graph_discover, graph_relation_lookup
from ..projection.lexical import lexical_lookup
from ..projection.substrate import SubstrateError
from ..projection.vector import EmbeddingProviderError, VectorError, vector_lookup
from .control_plane import CATALOG_CONFORMANCE_CONTRACT_VERSION, _canonical_persisted_proposal
from .conversation import RuntimeConversationError, _connect_runtime, _timestamp
from .retrieval_package import (
    IDENTITY_VERSION, RetrievalPackage, RetrievalPackageError,
    require_catalog_binding, require_current_package_identity,
)
from .retrieval_package_verification import (
    VERIFICATION_CONTRACT_VERSION, RetrievalPackageVerificationError, verify_retrieval_package,
)


EXECUTION_CONTRACT_VERSION = "retrieval-execution-v1"
Clock = Callable[[], dt.datetime]


class RetrievalExecutionError(ValueError):
    """A retrieval execution precondition or durable evidence is invalid."""


def _immutable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _immutable(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_immutable(item) for item in value)
    return value


def _json_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    return value


@dataclass(frozen=True)
class RetrievalExecutionRequestResult:
    ordinal: int
    request: Mapping[str, Any]
    status: str
    result: Mapping[str, Any] | None
    failure: Mapping[str, Any] | None


@dataclass(frozen=True)
class RetrievalExecutionResult:
    execution_id: str
    conformance_id: str
    retrieval_run_id: str
    retrieval_proposal_sha256: str
    capability_catalog_sha256: str
    retrieval_package_id: str
    retrieval_package_identity_version: str
    substrate_sha256: str
    vectors_sha256: str
    package_verification_contract_version: str
    execution_contract_version: str
    status: str
    started_at: str
    completed_at: str | None
    requests: tuple[RetrievalExecutionRequestResult, ...]
    execution_failure: Mapping[str, Any] | None


@dataclass(frozen=True)
class _Authority:
    conformance_id: str
    retrieval_run_id: str
    retrieval_proposal_sha256: str
    capability_catalog_sha256: str
    requests: tuple[dict[str, Any], ...]


def _load_authority(connection: sqlite3.Connection, conformance_id: str) -> _Authority:
    row = connection.execute(
        "SELECT * FROM retrieval_conformance WHERE conformance_id = ?", (conformance_id,)
    ).fetchone()
    if row is None:
        raise RetrievalExecutionError(f"retrieval conformance does not exist: {conformance_id}")
    if row["contract_version"] != CATALOG_CONFORMANCE_CONTRACT_VERSION or row["status"] != "valid":
        raise RetrievalExecutionError("retrieval conformance is not a valid current approval")
    run = connection.execute(
        "SELECT run_id, run_kind, status, output_json, capability_catalog_sha256 "
        "FROM model_runs WHERE run_id = ?", (row["retrieval_run_id"],)
    ).fetchone()
    if run is None or run["run_kind"] != "retrieval_inference" or run["status"] != "succeeded":
        raise RetrievalExecutionError("conformance does not refer to a succeeded retrieval-inference run")
    if run["capability_catalog_sha256"] != row["capability_catalog_sha256"]:
        raise RetrievalExecutionError("conformance catalog identity conflicts with retrieval inference")
    if not isinstance(run["output_json"], str) or not run["output_json"]:
        raise RetrievalExecutionError("retrieval-inference output is absent")
    try:
        requests, canonical_json = _canonical_persisted_proposal(run["output_json"])
    except ValueError as exc:
        raise RetrievalExecutionError(f"persisted retrieval proposal is malformed: {exc}") from exc
    proposal_sha = "sha256:" + hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()
    if proposal_sha != row["retrieval_proposal_sha256"]:
        raise RetrievalExecutionError("persisted retrieval proposal identity conflicts with conformance")
    try:
        payload = json.loads(row["result_json"])
    except (TypeError, json.JSONDecodeError) as exc:
        raise RetrievalExecutionError("persisted conformance result is malformed") from exc
    if not isinstance(payload, dict) or set(payload) != {"status", "requests"}:
        raise RetrievalExecutionError("persisted conformance result envelope is malformed")
    if payload["status"] != "valid" or not isinstance(payload["requests"], list) or len(payload["requests"]) != len(requests):
        raise RetrievalExecutionError("persisted conformance result is not a complete valid approval")
    for ordinal, item in enumerate(payload["requests"]):
        if not isinstance(item, dict) or set(item) != {"ordinal", "status", "violations"}:
            raise RetrievalExecutionError("persisted conformance request result is malformed")
        if item["ordinal"] != ordinal or item["status"] != "valid" or item["violations"] != []:
            raise RetrievalExecutionError("persisted conformance contains a non-valid request result")
    return _Authority(row["conformance_id"], row["retrieval_run_id"], proposal_sha, row["capability_catalog_sha256"], requests)


def _open_substrate_read_only(path: Path) -> sqlite3.Connection:
    try:
        connection = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection
    except sqlite3.Error as exc:
        raise RetrievalExecutionError(f"could not open retrieval substrate read-only: {exc}") from exc


def _native_exact_operand(operand: Mapping[str, Any]) -> Any:
    if operand["shape"] == "ordered_sequence":
        return tuple(operand["value"])
    value, domain = operand["value"], operand["domain"]
    if domain == "date":
        return dt.date.fromisoformat(value)
    if domain == "datetime":
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    return value


def _handle_json(handle: Any) -> dict[str, Any]:
    return {"node_kind": handle.node_kind, "identity": _json_value(handle.identity)}


def _surface_result(operator: str, native: Any) -> dict[str, Any]:
    if operator == "exact.equals":
        return {"kind": "exact", "unit_ids": list(native)}
    if operator in {"lexical.terms", "lexical.phrase"}:
        return {"kind": "lexical", "hits": [{"unit_id": hit.unit_id, "score": hit.score} for hit in native]}
    if operator == "vector.semantic_similarity":
        return {"kind": "vector", "hits": [
            {"target_kind": hit.target_kind, "target_identity": hit.target_identity,
             "score": hit.score, "segment_ordinal": hit.segment_ordinal} for hit in native
        ]}
    if operator in {"graph.discovery.terms", "graph.discovery.phrase"}:
        return {"kind": "graph_discovery", "hits": [
            {"node": _handle_json(hit.node), "score": hit.score} for hit in native
        ]}
    if operator == "graph.relation_occurrence_lookup":
        return {"kind": "graph_relation_occurrences", "occurrences": [
            {"edge_id": edge.edge_id, "relation_class": edge.relation_class,
             "relation_name": edge.relation_name, "source": _handle_json(edge.source),
             "target": _handle_json(edge.target)} for edge in native
        ]}
    raise RetrievalExecutionError(f"unsupported conformed retrieval operator: {operator}")


def _execute_surface(connection: sqlite3.Connection, package: RetrievalPackage, request: Mapping[str, Any], vector_provider: Any | None) -> dict[str, Any]:
    operator = request["operator"]
    if operator == "exact.equals":
        native = exact_lookup(connection, request["field_class"], request["field_name"], _native_exact_operand(request["operand"]))
    elif operator in {"lexical.terms", "lexical.phrase"}:
        native = lexical_lookup(
            connection, request["field_class"], request["field_name"],
            "terms" if operator.endswith("terms") else "phrase", request["operand"],
        )
    elif operator == "vector.semantic_similarity":
        if vector_provider is None:
            raise RetrievalExecutionError("vector provider is required for vector retrieval")
        native = vector_lookup(connection, package.vectors_path, request["query"], vector_provider)
    elif operator in {"graph.discovery.terms", "graph.discovery.phrase"}:
        native = graph_discover(
            connection, request["node_kind"], request["dimension_name"],
            "terms" if operator.endswith("terms") else "phrase", request["operand"],
        )
    elif operator == "graph.relation_occurrence_lookup":
        native = graph_relation_lookup(connection, request["relation_class"], request["relation_name"])
    else:
        raise RetrievalExecutionError(f"unsupported conformed retrieval operator: {operator}")
    return _surface_result(operator, native)


_SURFACE_ERRORS: dict[str, tuple[type[BaseException], ...]] = {
    "exact.equals": (TypeError, ValueError, KeyError, sqlite3.Error, SubstrateError),
    "lexical.terms": (TypeError, ValueError, KeyError, sqlite3.Error, SubstrateError),
    "lexical.phrase": (TypeError, ValueError, KeyError, sqlite3.Error, SubstrateError),
    "vector.semantic_similarity": (VectorError, EmbeddingProviderError, OSError, sqlite3.Error),
    "graph.discovery.terms": (TypeError, ValueError, KeyError, sqlite3.Error, GraphError),
    "graph.discovery.phrase": (TypeError, ValueError, KeyError, sqlite3.Error, GraphError),
    "graph.relation_occurrence_lookup": (TypeError, ValueError, KeyError, sqlite3.Error, GraphError),
}


def _surface_failure(operator: str, error: BaseException) -> dict[str, Any]:
    return {"kind": "surface_error", "surface": operator, "exception_type": type(error).__name__, "message": str(error)}


def _outcome(ordinal: int, request: Mapping[str, Any], status: str, result: Mapping[str, Any] | None = None, failure: Mapping[str, Any] | None = None) -> RetrievalExecutionRequestResult:
    return RetrievalExecutionRequestResult(
        ordinal, _immutable(request), status,
        _immutable(result) if result is not None else None,
        _immutable(failure) if failure is not None else None,
    )


def _payload(outcomes: tuple[RetrievalExecutionRequestResult, ...], execution_failure: Mapping[str, Any] | None) -> dict[str, Any]:
    return {
        "requests": [
            {"ordinal": item.ordinal, "request": _json_value(item.request), "status": item.status,
             "result": _json_value(item.result), "failure": _json_value(item.failure)}
            for item in outcomes
        ],
        "execution_failure": _json_value(execution_failure),
    }


def _serialize(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _result_from_row(row: sqlite3.Row, expected_requests: tuple[dict[str, Any], ...]) -> RetrievalExecutionResult:
    try:
        payload = json.loads(row["result_json"])
    except (TypeError, json.JSONDecodeError) as exc:
        raise RetrievalExecutionError("persisted retrieval execution result is malformed") from exc
    if not isinstance(payload, dict) or set(payload) != {"requests", "execution_failure"} or not isinstance(payload["requests"], list):
        raise RetrievalExecutionError("persisted retrieval execution envelope is malformed")
    outcomes: list[RetrievalExecutionRequestResult] = []
    for item in payload["requests"]:
        if not isinstance(item, dict) or set(item) != {"ordinal", "request", "status", "result", "failure"}:
            raise RetrievalExecutionError("persisted retrieval request outcome is malformed")
        ordinal = item["ordinal"]
        if not isinstance(ordinal, int) or ordinal != len(outcomes) or ordinal >= len(expected_requests) or item["request"] != expected_requests[ordinal]:
            raise RetrievalExecutionError("persisted retrieval request ordering or identity is malformed")
        status = item["status"]
        if status not in {"succeeded", "failed", "not_executed"}:
            raise RetrievalExecutionError("persisted retrieval request status is malformed")
        if status == "succeeded" and (not isinstance(item["result"], dict) or item["failure"] is not None):
            raise RetrievalExecutionError("persisted successful retrieval outcome is malformed")
        if status == "failed":
            if item["result"] is not None or not isinstance(item["failure"], dict):
                raise RetrievalExecutionError("persisted failed retrieval outcome is malformed")
            if set(item["failure"]) != {"kind", "surface", "exception_type", "message"} or item["failure"]["kind"] != "surface_error":
                raise RetrievalExecutionError("persisted surface failure evidence is malformed")
        if status == "not_executed":
            if item["result"] is not None or not isinstance(item["failure"], dict):
                raise RetrievalExecutionError("persisted not-executed retrieval outcome is malformed")
            if item["failure"].get("kind") == "prior_request_failed":
                if set(item["failure"]) != {"kind", "failed_ordinal"} or not isinstance(item["failure"]["failed_ordinal"], int):
                    raise RetrievalExecutionError("persisted prior-request failure evidence is malformed")
            elif item["failure"] != {"kind": "package_identity_changed"}:
                raise RetrievalExecutionError("persisted execution-level failure evidence is malformed")
        outcomes.append(_outcome(ordinal, item["request"], status, item["result"], item["failure"]))
    execution_failure = payload["execution_failure"]
    if execution_failure is not None and not isinstance(execution_failure, dict):
        raise RetrievalExecutionError("persisted execution failure is malformed")
    if row["status"] == "running":
        if execution_failure is not None or any(item.status != "succeeded" for item in outcomes):
            raise RetrievalExecutionError("persisted running execution is malformed")
    elif row["status"] == "succeeded":
        if execution_failure is not None or len(outcomes) != len(expected_requests) or any(item.status != "succeeded" for item in outcomes):
            raise RetrievalExecutionError("persisted successful execution is malformed")
    elif row["status"] == "failed":
        failed = [item.ordinal for item in outcomes if item.status == "failed"]
        if failed:
            failed_ordinal = failed[0]
            if (
                len(failed) != 1
                or execution_failure is not None
                or len(outcomes) != len(expected_requests)
                or any(item.status != "succeeded" for item in outcomes[:failed_ordinal])
                or any(
                    item.status != "not_executed"
                    or item.failure != {"kind": "prior_request_failed", "failed_ordinal": failed_ordinal}
                    for item in outcomes[failed_ordinal + 1:]
                )
            ):
                raise RetrievalExecutionError("persisted fail-fast outcomes are malformed")
        else:
            if (
                execution_failure is None
                or execution_failure.get("kind") != "package_identity_changed"
                or len(outcomes) != len(expected_requests)
                or any(item.status != "succeeded" for item in outcomes if item.status != "not_executed")
                or any(
                    item.status != "not_executed"
                    or item.failure != {"kind": "package_identity_changed"}
                    for item in outcomes
                    if item.status == "not_executed"
                )
            ):
                raise RetrievalExecutionError("persisted execution-level failure evidence is malformed")
    else:
        raise RetrievalExecutionError("persisted retrieval execution status is unsupported")
    return RetrievalExecutionResult(
        row["execution_id"], row["conformance_id"], row["retrieval_run_id"],
        row["retrieval_proposal_sha256"], row["capability_catalog_sha256"],
        row["retrieval_package_id"], row["retrieval_package_identity_version"],
        row["substrate_sha256"], row["vectors_sha256"],
        row["package_verification_contract_version"], row["execution_contract_version"],
        row["status"], row["started_at"], row["completed_at"], tuple(outcomes),
        _immutable(execution_failure) if execution_failure is not None else None,
    )


def _lineage_matches(row: sqlite3.Row, package: RetrievalPackage, authority: _Authority) -> bool:
    keys = (
        "conformance_id", "retrieval_run_id", "retrieval_proposal_sha256", "capability_catalog_sha256",
        "retrieval_package_id", "retrieval_package_identity_version", "substrate_sha256", "vectors_sha256",
        "package_verification_contract_version", "execution_contract_version",
    )
    expected = (
        authority.conformance_id, authority.retrieval_run_id, authority.retrieval_proposal_sha256,
        authority.capability_catalog_sha256, package.identity.package_id, IDENTITY_VERSION,
        package.identity.substrate_sha256, package.identity.vectors_sha256,
        VERIFICATION_CONTRACT_VERSION, EXECUTION_CONTRACT_VERSION,
    )
    return tuple(row[key] for key in keys) == expected


def execute_retrieval(
    database_path: str | Path,
    package: RetrievalPackage,
    conformance_id: str,
    *,
    vector_provider: Any | None = None,
    clock: Clock | None = None,
) -> RetrievalExecutionResult:
    """Execute one existing valid conformance against one verified package."""
    try:
        verify_retrieval_package(package)
        runtime_connection = _connect_runtime(database_path)
        try:
            authority = _load_authority(runtime_connection, conformance_id)
            require_catalog_binding(package, authority.capability_catalog_sha256)
            require_current_package_identity(package)
            if any(item["operator"] == "vector.semantic_similarity" for item in authority.requests) and vector_provider is None:
                raise RetrievalExecutionError("vector provider is required before retrieval execution can start")
            existing = runtime_connection.execute(
                "SELECT * FROM retrieval_executions WHERE conformance_id = ?", (conformance_id,)
            ).fetchone()
            if existing is not None:
                if not _lineage_matches(existing, package, authority):
                    raise RetrievalExecutionError("existing retrieval execution lineage conflicts with supplied authority")
                if existing["status"] == "running":
                    raise RetrievalExecutionError("a prior retrieval execution is still running")
                return _result_from_row(existing, authority.requests)

            execution_id = str(uuid4())
            started_at = _timestamp(clock)
            runtime_connection.execute("BEGIN IMMEDIATE")
            runtime_connection.execute(
                "INSERT INTO retrieval_executions ("
                "execution_id, conformance_id, retrieval_run_id, retrieval_proposal_sha256, capability_catalog_sha256, "
                "retrieval_package_id, retrieval_package_identity_version, substrate_sha256, vectors_sha256, "
                "package_verification_contract_version, execution_contract_version, status, started_at, completed_at, result_json"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'running', ?, NULL, ?)",
                (
                    execution_id, authority.conformance_id, authority.retrieval_run_id,
                    authority.retrieval_proposal_sha256, authority.capability_catalog_sha256,
                    package.identity.package_id, IDENTITY_VERSION, package.identity.substrate_sha256,
                    package.identity.vectors_sha256, VERIFICATION_CONTRACT_VERSION,
                    EXECUTION_CONTRACT_VERSION, started_at,
                    _serialize({"requests": [], "execution_failure": None}),
                ),
            )
            runtime_connection.commit()
        finally:
            runtime_connection.close()
    except (RetrievalExecutionError, RetrievalPackageError, RetrievalPackageVerificationError, RuntimeConversationError) as exc:
        raise RetrievalExecutionError(str(exc)) from exc
    except sqlite3.Error as exc:
        raise RetrievalExecutionError(f"retrieval execution precondition failed: {exc}") from exc

    outcomes: list[RetrievalExecutionRequestResult] = []
    execution_failure: Mapping[str, Any] | None = None
    substrate = _open_substrate_read_only(package.substrate_path)
    try:
        for ordinal, request in enumerate(authority.requests):
            try:
                require_current_package_identity(package)
            except RetrievalPackageError as exc:
                execution_failure = {"kind": "package_identity_changed", "message": str(exc)}
                outcomes.extend(
                    _outcome(index, item, "not_executed", failure={"kind": "package_identity_changed"})
                    for index, item in enumerate(authority.requests[ordinal:], ordinal)
                )
                break
            try:
                result = _execute_surface(substrate, package, request, vector_provider)
            except _SURFACE_ERRORS[request["operator"]] as exc:
                outcomes.append(_outcome(ordinal, request, "failed", failure=_surface_failure(request["operator"], exc)))
                outcomes.extend(
                    _outcome(index, item, "not_executed", failure={"kind": "prior_request_failed", "failed_ordinal": ordinal})
                    for index, item in enumerate(authority.requests[ordinal + 1:], ordinal + 1)
                )
                break
            outcomes.append(_outcome(ordinal, request, "succeeded", result=result))
            connection = _connect_runtime(database_path)
            try:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    "UPDATE retrieval_executions SET result_json = ? WHERE execution_id = ? AND status = 'running'",
                    (_serialize(_payload(tuple(outcomes), None)), execution_id),
                )
                connection.commit()
            finally:
                connection.close()

        if execution_failure is None:
            try:
                require_current_package_identity(package)
            except RetrievalPackageError as exc:
                execution_failure = {"kind": "package_identity_changed", "message": str(exc)}
        status = "failed" if execution_failure is not None or any(item.status == "failed" for item in outcomes) else "succeeded"
        completed_at = _timestamp(clock)
        connection = _connect_runtime(database_path)
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "UPDATE retrieval_executions SET status = ?, completed_at = ?, result_json = ? "
                "WHERE execution_id = ? AND status = 'running'",
                (status, completed_at, _serialize(_payload(tuple(outcomes), execution_failure)), execution_id),
            )
            connection.commit()
            row = connection.execute("SELECT * FROM retrieval_executions WHERE execution_id = ?", (execution_id,)).fetchone()
            return _result_from_row(row, authority.requests)
        finally:
            connection.close()
    finally:
        substrate.close()


__all__ = [
    "EXECUTION_CONTRACT_VERSION", "RetrievalExecutionError",
    "RetrievalExecutionRequestResult", "RetrievalExecutionResult", "execute_retrieval",
]
