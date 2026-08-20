"""Deterministic catalog conformance for persisted Model-1 proposals."""

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

from .capability_catalog import CapabilityCatalogError, load_capability_catalog
from ..conversation import CONFORMANCE_CONTRACT_VERSION, RuntimeConversationError, _connect_runtime, _timestamp
from ...projection.lexical import validate_terms_operands
from .requests import RetrievalRequestError, canonicalize_retrieval_requests


CATALOG_CONFORMANCE_CONTRACT_VERSION = CONFORMANCE_CONTRACT_VERSION
Clock = Callable[[], dt.datetime]




def _immutable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _immutable(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_immutable(item) for item in value)
    if isinstance(value, tuple):
        return tuple(_immutable(item) for item in value)
    return value


def _json_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    return value


class RetrievalConformanceError(ValueError):
    """A conformance precondition, artifact identity, or evidence failure."""


@dataclass(frozen=True)
class ConformanceRequestResult:
    ordinal: int
    status: str
    violations: tuple[Mapping[str, Any], ...]


@dataclass(frozen=True)
class RetrievalConformanceResult:
    conformance_id: str
    retrieval_run_id: str
    retrieval_proposal_sha256: str
    capability_catalog_sha256: str
    contract_version: str
    status: str
    checked_at: str
    requests: tuple[ConformanceRequestResult, ...]


def _canonical_persisted_proposal(output_json: str) -> tuple[tuple[dict[str, Any], ...], str]:
    try:
        parsed = json.loads(output_json)
    except (TypeError, json.JSONDecodeError) as exc:
        raise RetrievalConformanceError("retrieval proposal output_json is not valid JSON") from exc
    if not isinstance(parsed, dict) or set(parsed) != {"requests"}:
        raise RetrievalConformanceError("retrieval proposal output_json has an invalid envelope")
    try:
        requests = canonicalize_retrieval_requests(parsed["requests"])
    except RetrievalRequestError as exc:
        raise RetrievalConformanceError(f"persisted retrieval proposal is structurally invalid: {exc}") from exc
    canonical_json = json.dumps({"requests": list(requests)}, ensure_ascii=False, separators=(",", ":"))
    return requests, canonical_json


def _violation(code: str, **fields: Any) -> dict[str, Any]:
    return {"code": code, **fields}


def _field_dimension(catalog: Mapping[str, Any], request: Mapping[str, Any]) -> Mapping[str, Any] | None:
    dimensions = catalog.get("semantic_dimensions")
    if not isinstance(dimensions, (list, tuple)):
        return None
    matches = [
        item for item in dimensions
        if isinstance(item, Mapping)
        and item.get("field_class") == request.get("field_class")
        and item.get("field_name") == request.get("field_name")
    ]
    return matches[0] if matches else None


def _field_violations(catalog: Mapping[str, Any], request: Mapping[str, Any]) -> list[dict[str, Any]]:
    operator = request["operator"]
    identity = {"field_class": request["field_class"], "field_name": request["field_name"]}
    dimension = _field_dimension(catalog, request)
    if dimension is None:
        return [_violation("field_not_advertised", operator=operator, **identity)]
    accesses = dimension.get("access")
    if not isinstance(accesses, (list, tuple)):
        accesses = ()
    access = next(
        (
            item for item in accesses
            if isinstance(item, Mapping)
            and item.get("operator") == operator
            and item.get("target") == request.get("target")
        ),
        None,
    )
    if access is None:
        return [_violation("field_access_not_advertised", operator=operator, target=request["target"], **identity)]
    if operator.startswith("temporal."):
        domains = access.get("domains")
        if not isinstance(domains, (list, tuple)) or tuple(domains) != ("date",):
            return [_violation(
                "operand_domain_not_advertised",
                operator=operator,
                target=request["target"],
                domain="date",
                **identity,
            )]
        return []
    if operator == "exact.equals":
        operand = request["operand"]
        if operand["shape"] == "scalar":
            domains = access.get("domains")
            if not isinstance(domains, (list, tuple)) or operand["domain"] not in domains:
                return [_violation(
                    "operand_domain_not_advertised",
                    operator=operator,
                    target=request["target"],
                    shape="scalar",
                    domain=operand["domain"],
                    **identity,
                )]
        else:
            advertised = access.get("operand")
            if not isinstance(advertised, Mapping) or advertised.get("shape") != "ordered_sequence":
                return [_violation(
                    "operand_shape_not_advertised",
                    operator=operator,
                    target=request["target"],
                    shape="ordered_sequence",
                    **identity,
                )]
            member_domains = advertised.get("member_domains")
            if not isinstance(member_domains, (list, tuple)) or operand["member_domain"] not in member_domains:
                return [_violation(
                    "operand_domain_not_advertised",
                    operator=operator,
                    target=request["target"],
                    shape="ordered_sequence",
                    member_domain=operand["member_domain"],
                    **identity,
                )]
    else:
        domains = access.get("domains")
        if not isinstance(domains, (list, tuple)) or "string" not in domains:
            return [_violation(
                "operand_domain_not_advertised",
                operator=operator,
                target=request["target"],
                domain="string",
                **identity,
            )]
    return []


def _graph_discovery_violations(catalog: Mapping[str, Any], request: Mapping[str, Any]) -> list[dict[str, Any]]:
    operator = request["operator"]
    graph = catalog.get("graph")
    discoveries = graph.get("discovery") if isinstance(graph, Mapping) else None
    matches = [
        item for item in discoveries or ()
        if isinstance(item, Mapping)
        and item.get("node_kind") == request["node_kind"]
        and item.get("dimension_name") == request["dimension_name"]
    ]
    identity = {"node_kind": request["node_kind"], "dimension_name": request["dimension_name"]}
    if not matches:
        return [_violation("graph_discovery_dimension_not_advertised", operator=operator, **identity)]
    operations = matches[0].get("operators")
    if not isinstance(operations, (list, tuple)) or operator not in operations:
        return [_violation("graph_discovery_operator_not_advertised", operator=operator, **identity)]
    return []


def _graph_relation_violations(catalog: Mapping[str, Any], request: Mapping[str, Any]) -> list[dict[str, Any]]:
    operator = request["operator"]
    graph = catalog.get("graph")
    relations = graph.get("relations") if isinstance(graph, Mapping) else None
    matches = [
        item for item in relations or ()
        if isinstance(item, Mapping)
        and item.get("relation_class") == request["relation_class"]
        and item.get("relation_name") == request["relation_name"]
    ]
    identity = {"relation_class": request["relation_class"], "relation_name": request["relation_name"]}
    if not matches:
        return [_violation("graph_relation_not_advertised", operator=operator, **identity)]
    operations = matches[0].get("operations")
    if not isinstance(operations, (list, tuple)) or operator not in operations:
        return [_violation("graph_relation_operation_not_advertised", operator=operator, **identity)]
    return []


def _request_violations(catalog: Mapping[str, Any], request: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    operator = request["operator"]
    operators = catalog.get("operators")
    if not isinstance(operators, Mapping) or operator not in operators:
        return (_violation("operator_not_advertised", operator=operator),)
    if operator in {"exact.equals", "lexical.terms", "lexical.phrase", "temporal.earliest", "temporal.latest", "temporal.before", "temporal.after", "temporal.between", "temporal.ordered"}:
        field_violations = _field_violations(catalog, request)
        if field_violations or operator != "lexical.terms":
            return tuple(field_violations)
        try:
            validate_terms_operands(tuple(request["operand"]))
        except ValueError as exc:
            return (_violation(
                "lexical_terms_operand_not_tokenizable",
                operator=operator,
                target=request["target"],
                field_class=request["field_class"],
                field_name=request["field_name"],
                message=str(exc),
            ),)
        return ()
    if operator == "vector.semantic_similarity":
        vector = catalog.get("vector")
        if not isinstance(vector, Mapping) or vector.get("operator") != operator:
            return (_violation("vector_operator_not_advertised", operator=operator),)
        return ()
    if operator in {"graph.discovery.terms", "graph.discovery.phrase"}:
        discovery_violations = _graph_discovery_violations(catalog, request)
        if discovery_violations or operator != "graph.discovery.terms":
            return tuple(discovery_violations)
        try:
            validate_terms_operands(tuple(request["operand"]))
        except ValueError as exc:
            return (_violation(
                "graph_discovery_terms_operand_not_tokenizable",
                operator=operator,
                node_kind=request["node_kind"],
                dimension_name=request["dimension_name"],
                message=str(exc),
            ),)
        return ()
    if operator == "graph.relation_occurrence_lookup":
        return tuple(_graph_relation_violations(catalog, request))
    return (_violation("operator_not_advertised", operator=operator),)


def _request_result(
    ordinal: int,
    request: Mapping[str, Any],
    catalog: Mapping[str, Any],
) -> ConformanceRequestResult:
    violations = tuple(_immutable(item) for item in _request_violations(catalog, request))
    return ConformanceRequestResult(ordinal, "invalid" if violations else "valid", violations)


def _result_payload(status: str, requests: tuple[ConformanceRequestResult, ...]) -> dict[str, Any]:
    return {
        "status": status,
        "requests": [
            {"ordinal": result.ordinal, "status": result.status, "violations": [_json_value(item) for item in result.violations]}
            for result in requests
        ]
    }


def _result_from_row(row: sqlite3.Row) -> RetrievalConformanceResult:
    try:
        payload = json.loads(row["result_json"])
        request_results = tuple(
            ConformanceRequestResult(item["ordinal"], item["status"], tuple(_immutable(item) for item in item["violations"]))
            for item in payload["requests"]
        )
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise RetrievalConformanceError("persisted conformance result is malformed") from exc
    return RetrievalConformanceResult(
        conformance_id=row["conformance_id"],
        retrieval_run_id=row["retrieval_run_id"],
        retrieval_proposal_sha256=row["retrieval_proposal_sha256"],
        capability_catalog_sha256=row["capability_catalog_sha256"],
        contract_version=row["contract_version"],
        status=row["status"],
        checked_at=row["checked_at"],
        requests=request_results,
    )


def conform_retrieval(
    database_path: str | Path,
    capability_catalog_path: str | Path,
    retrieval_run_id: str,
    *,
    clock: Clock | None = None,
) -> RetrievalConformanceResult:
    """Conform one persisted retrieval proposal against the catalog it was shown."""
    connection = _connect_runtime(database_path)
    try:
        run = connection.execute(
            "SELECT run_id, run_kind, status, output_json, capability_catalog_sha256 "
            "FROM model_runs WHERE run_id = ?",
            (retrieval_run_id,),
        ).fetchone()
        if run is None:
            raise RetrievalConformanceError(f"retrieval run does not exist: {retrieval_run_id}")
        if run["run_kind"] != "retrieval_inference":
            raise RetrievalConformanceError("conformance requires a retrieval_inference run")
        if run["status"] != "succeeded":
            raise RetrievalConformanceError("conformance requires a succeeded retrieval-inference run")
        if not isinstance(run["output_json"], str) or not run["output_json"]:
            raise RetrievalConformanceError("retrieval run has no persisted proposal")
        if not isinstance(run["capability_catalog_sha256"], str) or not run["capability_catalog_sha256"]:
            raise RetrievalConformanceError("retrieval run has no capability catalog identity")

        try:
            catalog = load_capability_catalog(capability_catalog_path)
        except CapabilityCatalogError as exc:
            raise RetrievalConformanceError(str(exc)) from exc
        if catalog.sha256 != run["capability_catalog_sha256"]:
            raise RetrievalConformanceError("capability catalog identity does not match retrieval-inference input")

        requests, canonical_output_json = _canonical_persisted_proposal(run["output_json"])
        proposal_sha = "sha256:" + hashlib.sha256(canonical_output_json.encode("utf-8")).hexdigest()
        results = tuple(
            _request_result(ordinal, request, catalog.parsed)
            for ordinal, request in enumerate(requests)
        )
        valid_count = sum(result.status == "valid" for result in results)
        overall_status = "valid" if valid_count == len(results) else "partial" if valid_count else "invalid"
        payload = _result_payload(overall_status, results)
        result_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

        existing = connection.execute(
            "SELECT conformance_id, retrieval_run_id, retrieval_proposal_sha256, capability_catalog_sha256, "
            "contract_version, status, checked_at, result_json FROM retrieval_conformance "
            "WHERE retrieval_run_id = ?",
            (retrieval_run_id,),
        ).fetchone()
        if existing is not None:
            expected = (proposal_sha, catalog.sha256, CATALOG_CONFORMANCE_CONTRACT_VERSION, overall_status, result_json)
            actual = tuple(existing[index] for index in (2, 3, 4, 5, 7))
            if actual != expected:
                raise RetrievalConformanceError("persisted conformance evidence conflicts with recomputed result")
            return _result_from_row(existing)

        conformance_id = str(uuid4())
        checked_at = _timestamp(clock)
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "INSERT INTO retrieval_conformance (conformance_id, retrieval_run_id, retrieval_proposal_sha256, "
            "capability_catalog_sha256, contract_version, status, checked_at, result_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                conformance_id,
                retrieval_run_id,
                proposal_sha,
                catalog.sha256,
                CATALOG_CONFORMANCE_CONTRACT_VERSION,
                overall_status,
                checked_at,
                result_json,
            ),
        )
        connection.commit()
        return RetrievalConformanceResult(
            conformance_id,
            retrieval_run_id,
            proposal_sha,
            catalog.sha256,
            CATALOG_CONFORMANCE_CONTRACT_VERSION,
            overall_status,
            checked_at,
            results,
        )
    except (RetrievalConformanceError, RuntimeConversationError):
        connection.rollback()
        raise
    except sqlite3.Error as exc:
        connection.rollback()
        raise RetrievalConformanceError(f"could not persist conformance evidence: {exc}") from exc
    finally:
        connection.close()


__all__ = [
    "CATALOG_CONFORMANCE_CONTRACT_VERSION",
    "ConformanceRequestResult",
    "RetrievalConformanceError",
    "RetrievalConformanceResult",
    "conform_retrieval",
]
