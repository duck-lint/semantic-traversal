"""Canonical hydration of terminal retrieval execution evidence."""

from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from ...build.canonical import CanonicalObject, CanonicalRegion, CanonicalUnit
from ...projection.graph import GraphHandle
from ...projection.substrate import (
    SubstrateError,
    hydrate_object,
    hydrate_region,
    hydrate_unit,
)
from .execution import (
    EXECUTION_CONTRACT_VERSION,
    RetrievalExecutionError,
    RetrievalExecutionRequestResult,
    RetrievalExecutionResult,
    load_retrieval_execution,
)
from .package import (
    IDENTITY_VERSION,
    RetrievalPackage,
    RetrievalPackageError,
    require_catalog_binding,
    require_current_package_identity,
)
from .package_verification import (
    VERIFICATION_CONTRACT_VERSION,
    RetrievalPackageInput,
    RetrievalPackageVerificationError,
    normalize_verified_retrieval_package,
)


SEMANTIC_UNIT = "semantic_unit"
SEMANTIC_OBJECT = "semantic_object"
SEMANTIC_REGION = "semantic_region"
SCOPE = "scope"


class RetrievalHydrationError(ValueError):
    """Persisted retrieval evidence cannot hydrate from its exact package."""


def _immutable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _immutable(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_immutable(item) for item in value)
    return value


@dataclass(frozen=True)
class HydratedCanonicalTarget:
    """One typed canonical target plus optional graph identity provenance."""

    target_kind: str
    canonical_unit: CanonicalUnit | None = None
    canonical_object: CanonicalObject | None = None
    canonical_region: CanonicalRegion | None = None
    owning_object: CanonicalObject | None = None
    graph_handle: GraphHandle | None = None

    def __post_init__(self) -> None:
        if self.graph_handle is not None and self.graph_handle.node_kind != self.target_kind:
            raise RetrievalHydrationError("hydrated graph handle kind conflicts with target kind")
        if self.target_kind == SEMANTIC_UNIT:
            if (
                self.canonical_unit is None
                or self.owning_object is None
                or self.canonical_object is not None
                or self.canonical_region is not None
            ):
                raise RetrievalHydrationError("semantic_unit target payload is incomplete or contradictory")
            if self.canonical_unit.source_object_uuid != self.owning_object.source_object_uuid:
                raise RetrievalHydrationError("semantic_unit owning object does not match canonical unit")
        elif self.target_kind == SEMANTIC_OBJECT:
            if (
                self.canonical_object is None
                or self.canonical_unit is not None
                or self.canonical_region is not None
                or self.owning_object is not None
            ):
                raise RetrievalHydrationError("semantic_object target payload is incomplete or contradictory")
        elif self.target_kind == SEMANTIC_REGION:
            if (
                self.canonical_region is None
                or self.owning_object is None
                or self.canonical_unit is not None
                or self.canonical_object is not None
            ):
                raise RetrievalHydrationError("semantic_region target payload is incomplete or contradictory")
            if self.canonical_region.reference.source_object_uuid != self.owning_object.source_object_uuid:
                raise RetrievalHydrationError("semantic_region owning object does not match canonical region")
        elif self.target_kind == SCOPE:
            if (
                self.graph_handle is None
                or self.canonical_unit is not None
                or self.canonical_object is not None
                or self.canonical_region is not None
                or self.owning_object is not None
            ):
                raise RetrievalHydrationError("scope target must retain only its graph handle")
        else:
            raise RetrievalHydrationError(f"unsupported hydrated target kind: {self.target_kind!r}")


@dataclass(frozen=True)
class HydratedExactHit:
    unit_id: int
    target: HydratedCanonicalTarget


@dataclass(frozen=True)
class HydratedExactResult:
    hits: tuple[HydratedExactHit, ...]


@dataclass(frozen=True)
class HydratedLexicalHit:
    unit_id: int
    score: float
    target: HydratedCanonicalTarget


@dataclass(frozen=True)
class HydratedLexicalResult:
    hits: tuple[HydratedLexicalHit, ...]


@dataclass(frozen=True)
class HydratedVectorHit:
    target_kind: str
    target_identity: int | str
    score: float
    segment_ordinal: int
    target: HydratedCanonicalTarget


@dataclass(frozen=True)
class HydratedVectorResult:
    hits: tuple[HydratedVectorHit, ...]


@dataclass(frozen=True)
class HydratedGraphDiscoveryHit:
    node: GraphHandle
    score: float
    target: HydratedCanonicalTarget


@dataclass(frozen=True)
class HydratedGraphDiscoveryResult:
    hits: tuple[HydratedGraphDiscoveryHit, ...]


@dataclass(frozen=True)
class HydratedGraphOccurrence:
    edge_id: int
    relation_class: str
    relation_name: str
    source: HydratedCanonicalTarget
    target: HydratedCanonicalTarget


@dataclass(frozen=True)
class HydratedGraphRelationResult:
    occurrences: tuple[HydratedGraphOccurrence, ...]


@dataclass(frozen=True)
class HydratedRequestResult:
    ordinal: int
    request: Mapping[str, Any]
    operator: str
    status: str
    result: Any | None
    failure: Mapping[str, Any] | None


@dataclass(frozen=True)
class HydratedRetrievalResult:
    execution_id: str
    conformance_id: str
    retrieval_run_id: str
    retrieval_package_id: str
    status: str
    execution_failure: Mapping[str, Any] | None
    requests: tuple[HydratedRequestResult, ...]


@dataclass
class _HydrationCaches:
    units: dict[int, CanonicalUnit]
    objects: dict[str, CanonicalObject]
    regions: dict[tuple[str, tuple[str, ...]], CanonicalRegion]

    @classmethod
    def create(cls) -> _HydrationCaches:
        return cls({}, {}, {})


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise RetrievalHydrationError(f"{label} must be an object")
    return value


def _sequence(value: Any, label: str) -> tuple[Any, ...]:
    if not isinstance(value, (list, tuple)):
        raise RetrievalHydrationError(f"{label} must be an array")
    return tuple(value)


def _exact_keys(value: Mapping[str, Any], keys: set[str], label: str) -> None:
    if set(value) != keys:
        raise RetrievalHydrationError(f"{label} has an invalid key set")


def _int_identity(value: Any, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise RetrievalHydrationError(f"{label} must be an integer identity")
    return value


def _text_identity(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise RetrievalHydrationError(f"{label} must be a non-empty string identity")
    return value


def _score(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise RetrievalHydrationError(f"{label} must be a finite numeric score")
    return value


def _graph_handle(value: Any) -> GraphHandle:
    value = _mapping(value, "graph handle")
    _exact_keys(value, {"node_kind", "identity"}, "graph handle")
    node_kind = value["node_kind"]
    identity = _sequence(value["identity"], "graph handle identity")
    if node_kind == SEMANTIC_OBJECT:
        if len(identity) != 1:
            raise RetrievalHydrationError("semantic_object graph identity is malformed")
        return GraphHandle(node_kind, (_text_identity(identity[0], "object UUID"),))
    if node_kind == SEMANTIC_UNIT:
        if len(identity) != 1:
            raise RetrievalHydrationError("semantic_unit graph identity is malformed")
        return GraphHandle(node_kind, (_int_identity(identity[0], "unit ID"),))
    if node_kind == SEMANTIC_REGION:
        if len(identity) != 2:
            raise RetrievalHydrationError("semantic_region graph identity is malformed")
        object_uuid = _text_identity(identity[0], "region object UUID")
        path = _sequence(identity[1], "region path")
        if not path or not all(isinstance(item, str) and item for item in path):
            raise RetrievalHydrationError("semantic_region graph path is malformed")
        return GraphHandle(node_kind, (object_uuid, tuple(path)))
    if node_kind == SCOPE:
        if len(identity) != 1:
            raise RetrievalHydrationError("scope graph identity is malformed")
        path = _sequence(identity[0], "scope path")
        if not path or not all(isinstance(item, str) and item for item in path):
            raise RetrievalHydrationError("scope graph path is malformed")
        return GraphHandle(node_kind, (tuple(path),))
    raise RetrievalHydrationError(f"unsupported graph node kind: {node_kind!r}")


def _unit_target(
    connection: sqlite3.Connection,
    unit_id: int,
    handle: GraphHandle | None = None,
    caches: _HydrationCaches | None = None,
) -> HydratedCanonicalTarget:
    caches = caches or _HydrationCaches.create()
    unit = caches.units.get(unit_id)
    if unit is None:
        unit = hydrate_unit(connection, unit_id)
        caches.units[unit_id] = unit
    owner = caches.objects.get(unit.source_object_uuid)
    if owner is None:
        owner = hydrate_object(connection, unit.source_object_uuid)
        caches.objects[unit.source_object_uuid] = owner
    return HydratedCanonicalTarget(SEMANTIC_UNIT, canonical_unit=unit, owning_object=owner, graph_handle=handle)


def _object_target(
    connection: sqlite3.Connection,
    source_object_uuid: str,
    handle: GraphHandle | None = None,
    caches: _HydrationCaches | None = None,
) -> HydratedCanonicalTarget:
    caches = caches or _HydrationCaches.create()
    canonical_object = caches.objects.get(source_object_uuid)
    if canonical_object is None:
        canonical_object = hydrate_object(connection, source_object_uuid)
        caches.objects[source_object_uuid] = canonical_object
    return HydratedCanonicalTarget(
        SEMANTIC_OBJECT,
        canonical_object=canonical_object,
        graph_handle=handle,
    )


def _region_target(
    connection: sqlite3.Connection,
    source_object_uuid: str,
    region_path: tuple[str, ...],
    handle: GraphHandle | None = None,
    caches: _HydrationCaches | None = None,
) -> HydratedCanonicalTarget:
    caches = caches or _HydrationCaches.create()
    key = (source_object_uuid, region_path)
    region = caches.regions.get(key)
    if region is None:
        region = hydrate_region(connection, source_object_uuid, region_path)
        caches.regions[key] = region
    owner = caches.objects.get(source_object_uuid)
    if owner is None:
        owner = hydrate_object(connection, source_object_uuid)
        caches.objects[source_object_uuid] = owner
    return HydratedCanonicalTarget(
        SEMANTIC_REGION,
        canonical_region=region,
        owning_object=owner,
        graph_handle=handle,
    )


def _graph_target(
    connection: sqlite3.Connection,
    handle: GraphHandle,
    caches: _HydrationCaches,
) -> HydratedCanonicalTarget:
    if handle.node_kind == SEMANTIC_UNIT:
        return _unit_target(connection, handle.identity[0], handle, caches)
    if handle.node_kind == SEMANTIC_OBJECT:
        return _object_target(connection, handle.identity[0], handle, caches)
    if handle.node_kind == SEMANTIC_REGION:
        return _region_target(connection, handle.identity[0], handle.identity[1], handle, caches)
    if handle.node_kind == SCOPE:
        return HydratedCanonicalTarget(SCOPE, graph_handle=handle)
    raise RetrievalHydrationError(f"unsupported graph node kind: {handle.node_kind!r}")


def _hydrate_exact(
    connection: sqlite3.Connection,
    result: Mapping[str, Any],
    caches: _HydrationCaches,
) -> HydratedExactResult:
    _exact_keys(result, {"kind", "unit_ids"}, "exact result")
    if result["kind"] != "exact":
        raise RetrievalHydrationError("exact result kind is malformed")
    hits: list[HydratedExactHit] = []
    for raw_unit_id in _sequence(result["unit_ids"], "exact unit_ids"):
        unit_id = _int_identity(raw_unit_id, "exact unit_id")
        hits.append(HydratedExactHit(unit_id, _unit_target(connection, unit_id, caches=caches)))
    return HydratedExactResult(tuple(hits))


def _hydrate_lexical(
    connection: sqlite3.Connection,
    result: Mapping[str, Any],
    caches: _HydrationCaches,
) -> HydratedLexicalResult:
    _exact_keys(result, {"kind", "hits"}, "lexical result")
    if result["kind"] != "lexical":
        raise RetrievalHydrationError("lexical result kind is malformed")
    hits: list[HydratedLexicalHit] = []
    for raw_hit in _sequence(result["hits"], "lexical hits"):
        hit = _mapping(raw_hit, "lexical hit")
        _exact_keys(hit, {"unit_id", "score"}, "lexical hit")
        unit_id = _int_identity(hit["unit_id"], "lexical unit_id")
        score = _score(hit["score"], "lexical score")
        hits.append(HydratedLexicalHit(unit_id, score, _unit_target(connection, unit_id, caches=caches)))
    return HydratedLexicalResult(tuple(hits))


def _hydrate_vector(
    connection: sqlite3.Connection,
    result: Mapping[str, Any],
    caches: _HydrationCaches,
) -> HydratedVectorResult:
    _exact_keys(result, {"kind", "hits"}, "vector result")
    if result["kind"] != "vector":
        raise RetrievalHydrationError("vector result kind is malformed")
    hits: list[HydratedVectorHit] = []
    for raw_hit in _sequence(result["hits"], "vector hits"):
        hit = _mapping(raw_hit, "vector hit")
        _exact_keys(hit, {"target_kind", "target_identity", "score", "segment_ordinal"}, "vector hit")
        target_kind = hit["target_kind"]
        if target_kind == SEMANTIC_UNIT:
            target_identity = _int_identity(hit["target_identity"], "vector unit identity")
        elif target_kind == SEMANTIC_OBJECT:
            target_identity = _text_identity(hit["target_identity"], "vector object identity")
        else:
            raise RetrievalHydrationError(f"unsupported vector target kind: {target_kind!r}")
        score = _score(hit["score"], "vector score")
        segment_ordinal = _int_identity(hit["segment_ordinal"], "vector segment ordinal")
        target = (
            _unit_target(connection, target_identity, caches=caches)
            if target_kind == SEMANTIC_UNIT
            else _object_target(connection, target_identity, caches=caches)
        )
        hits.append(HydratedVectorHit(target_kind, target_identity, score, segment_ordinal, target))
    return HydratedVectorResult(tuple(hits))


def _hydrate_graph_discovery(
    connection: sqlite3.Connection,
    request: Mapping[str, Any],
    result: Mapping[str, Any],
    caches: _HydrationCaches,
) -> HydratedGraphDiscoveryResult:
    _exact_keys(result, {"kind", "hits"}, "graph discovery result")
    if result["kind"] != "graph_discovery":
        raise RetrievalHydrationError("graph discovery result kind is malformed")
    hits: list[HydratedGraphDiscoveryHit] = []
    for raw_hit in _sequence(result["hits"], "graph discovery hits"):
        hit = _mapping(raw_hit, "graph discovery hit")
        _exact_keys(hit, {"node", "score"}, "graph discovery hit")
        node = _graph_handle(hit["node"])
        if node.node_kind != request["node_kind"]:
            raise RetrievalHydrationError("graph discovery result node kind does not match request")
        score = _score(hit["score"], "graph discovery score")
        hits.append(HydratedGraphDiscoveryHit(node, score, _graph_target(connection, node, caches)))
    return HydratedGraphDiscoveryResult(tuple(hits))


def _hydrate_graph_relations(
    connection: sqlite3.Connection,
    request: Mapping[str, Any],
    result: Mapping[str, Any],
    caches: _HydrationCaches,
    catalog: Mapping[str, Any],
) -> HydratedGraphRelationResult:
    _exact_keys(result, {"kind", "occurrences"}, "graph relation result")
    if result["kind"] != "graph_relation_occurrences":
        raise RetrievalHydrationError("graph relation result kind is malformed")
    graph = _mapping(catalog.get("graph"), "capability catalog graph")
    relations = _sequence(graph.get("relations"), "capability catalog graph relations")
    matching_relations = [
        _mapping(relation, "capability catalog graph relation")
        for relation in relations
        if relation.get("relation_class") == request["relation_class"]
        and relation.get("relation_name") == request["relation_name"]
    ]
    if len(matching_relations) != 1:
        raise RetrievalHydrationError("graph relation is absent or ambiguous in capability catalog")
    relation = matching_relations[0]
    source_kinds = _sequence(relation.get("source_kinds"), "capability catalog relation source_kinds")
    target_kinds = _sequence(relation.get("target_kinds"), "capability catalog relation target_kinds")
    occurrences: list[HydratedGraphOccurrence] = []
    for raw_occurrence in _sequence(result["occurrences"], "graph relation occurrences"):
        occurrence = _mapping(raw_occurrence, "graph relation occurrence")
        _exact_keys(
            occurrence,
            {"edge_id", "relation_class", "relation_name", "source", "target"},
            "graph relation occurrence",
        )
        edge_id = _int_identity(occurrence["edge_id"], "graph edge ID")
        relation_class = _text_identity(occurrence["relation_class"], "relation class")
        relation_name = _text_identity(occurrence["relation_name"], "relation name")
        if relation_class != request["relation_class"] or relation_name != request["relation_name"]:
            raise RetrievalHydrationError("graph relation result identity does not match request")
        source = _graph_handle(occurrence["source"])
        target = _graph_handle(occurrence["target"])
        if source.node_kind not in source_kinds or target.node_kind not in target_kinds:
            raise RetrievalHydrationError("graph relation result endpoint kinds conflict with capability catalog")
        occurrences.append(
            HydratedGraphOccurrence(
                edge_id,
                relation_class,
                relation_name,
                _graph_target(connection, source, caches),
                _graph_target(connection, target, caches),
            )
        )
    return HydratedGraphRelationResult(tuple(occurrences))


def _hydrate_surface(
    connection: sqlite3.Connection,
    request: Mapping[str, Any],
    result: Mapping[str, Any],
    caches: _HydrationCaches,
    catalog: Mapping[str, Any],
) -> Any:
    operator = request["operator"]
    if operator == "exact.equals":
        return _hydrate_exact(connection, result, caches)
    if operator in {"lexical.terms", "lexical.phrase"}:
        return _hydrate_lexical(connection, result, caches)
    if operator == "vector.semantic_similarity":
        return _hydrate_vector(connection, result, caches)
    if operator in {"graph.discovery.terms", "graph.discovery.phrase"}:
        return _hydrate_graph_discovery(connection, request, result, caches)
    if operator == "graph.relation_occurrence_lookup":
        return _hydrate_graph_relations(connection, request, result, caches, catalog)
    raise RetrievalHydrationError(f"unsupported hydrated retrieval operator: {operator!r}")


def _require_lineage(execution: RetrievalExecutionResult, package: RetrievalPackage) -> None:
    if execution.retrieval_package_id != package.identity.package_id:
        raise RetrievalHydrationError("retrieval execution package identity does not match supplied package")
    if execution.retrieval_package_identity_version != IDENTITY_VERSION:
        raise RetrievalHydrationError("retrieval execution package identity version is incompatible")
    if execution.substrate_sha256 != package.identity.substrate_sha256:
        raise RetrievalHydrationError("retrieval execution substrate identity does not match supplied package")
    if execution.vectors_sha256 != package.identity.vectors_sha256:
        raise RetrievalHydrationError("retrieval execution vector identity does not match supplied package")
    if execution.capability_catalog_sha256 != package.identity.capability_catalog_sha256:
        raise RetrievalHydrationError("retrieval execution catalog identity does not match supplied package")
    if execution.package_verification_contract_version != VERIFICATION_CONTRACT_VERSION:
        raise RetrievalHydrationError("retrieval execution verification contract is incompatible")
    if execution.execution_contract_version != EXECUTION_CONTRACT_VERSION:
        raise RetrievalHydrationError("retrieval execution contract is incompatible")
    require_catalog_binding(package, execution.capability_catalog_sha256)


def _open_substrate_read_only(path: Path) -> sqlite3.Connection:
    try:
        connection = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
        connection.execute("PRAGMA foreign_keys = ON")
        if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
            raise RetrievalHydrationError("canonical substrate foreign keys are not enabled")
        return connection
    except sqlite3.Error as exc:
        raise RetrievalHydrationError(f"canonical substrate could not be opened read-only: {exc}") from exc


def hydrate_retrieval_execution(
    database_path: str | Path,
    package: RetrievalPackageInput,
    execution_id: str,
) -> HydratedRetrievalResult:
    """Hydrate one terminal persisted retrieval execution from its exact package."""

    connection: sqlite3.Connection | None = None
    try:
        execution = load_retrieval_execution(database_path, execution_id)
        if execution.status not in {"succeeded", "failed"}:
            raise RetrievalHydrationError("retrieval execution is not terminal")
        verified_package = normalize_verified_retrieval_package(package)
        package = verified_package.package
        _require_lineage(execution, package)
        try:
            require_current_package_identity(package)
        except RetrievalPackageError as exc:
            raise RetrievalHydrationError(str(exc)) from exc
        connection = _open_substrate_read_only(package.substrate_path)
        connection.execute("BEGIN")
        caches = _HydrationCaches.create()

        hydrated_requests: list[HydratedRequestResult] = []
        for item in execution.requests:
            result = None
            if item.status == "succeeded":
                if not isinstance(item.result, Mapping):
                    raise RetrievalHydrationError("successful persisted retrieval result is malformed")
                result = _hydrate_surface(connection, item.request, item.result, caches, package.capability_catalog.parsed)
            elif item.status not in {"failed", "not_executed"}:
                raise RetrievalHydrationError("persisted request status is unsupported for hydration")
            hydrated_requests.append(
                HydratedRequestResult(
                    item.ordinal,
                    _immutable(item.request),
                    item.request["operator"],
                    item.status,
                    result,
                    _immutable(item.failure) if item.failure is not None else None,
                )
            )
        try:
            require_current_package_identity(package)
        except RetrievalPackageError as exc:
            raise RetrievalHydrationError(str(exc)) from exc
        return HydratedRetrievalResult(
            execution.execution_id,
            execution.conformance_id,
            execution.retrieval_run_id,
            package.identity.package_id,
            execution.status,
            _immutable(execution.execution_failure) if execution.execution_failure is not None else None,
            tuple(hydrated_requests),
        )
    except RetrievalHydrationError:
        raise
    except (
        RetrievalExecutionError,
        RetrievalPackageError,
        RetrievalPackageVerificationError,
        SubstrateError,
        KeyError,
        sqlite3.Error,
    ) as exc:
        raise RetrievalHydrationError(str(exc)) from exc
    finally:
        if connection is not None:
            connection.close()


__all__ = [
    "HydratedCanonicalTarget",
    "HydratedExactHit",
    "HydratedExactResult",
    "HydratedGraphDiscoveryHit",
    "HydratedGraphDiscoveryResult",
    "HydratedGraphOccurrence",
    "HydratedGraphRelationResult",
    "HydratedLexicalHit",
    "HydratedLexicalResult",
    "HydratedRequestResult",
    "HydratedRetrievalResult",
    "HydratedVectorHit",
    "HydratedVectorResult",
    "RetrievalHydrationError",
    "hydrate_retrieval_execution",
]
