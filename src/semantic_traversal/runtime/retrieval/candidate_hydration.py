"""Complete canonical reconstruction for one accepted CandidateSelection.

This stage begins after candidate selection.  It reconstructs selected targets
and deliberately does not compact evidence, traverse the graph, or persist
derived state.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeAlias

from ...build.canonical import CanonicalObject, CanonicalRegion, CanonicalUnit
from ...projection.graph import GraphHandle
from ...projection.substrate import SubstrateError, hydrate_object, hydrate_region, hydrate_unit
from .candidates import Candidate, CandidateRef, TemporalSupport
from .package import IDENTITY_VERSION, RetrievalPackageError, require_current_package_identity
from .package_verification import VERIFICATION_CONTRACT_VERSION, VerifiedRetrievalPackage
from .selection import CANDIDATE_SELECTION_CONTRACT_VERSION, CandidateSelection


CANDIDATE_HYDRATION_CONTRACT_VERSION = "candidate-hydration-v1"


class CandidateHydrationError(ValueError):
    """The selected candidates cannot be reconstructed canonically."""


@dataclass(frozen=True)
class HydratedUnitTarget:
    unit: CanonicalUnit
    owning_object: CanonicalObject


@dataclass(frozen=True)
class HydratedObjectTarget:
    object: CanonicalObject


@dataclass(frozen=True)
class HydratedRegionTarget:
    region: CanonicalRegion
    owning_object: CanonicalObject


@dataclass(frozen=True)
class HydratedScopeTarget:
    handle: GraphHandle


HydratedCanonicalTarget: TypeAlias = (
    HydratedUnitTarget | HydratedObjectTarget | HydratedRegionTarget | HydratedScopeTarget
)


@dataclass(frozen=True)
class HydratedCandidate:
    candidate: Candidate
    canonical_target: HydratedCanonicalTarget


@dataclass(frozen=True)
class HydratedCandidateSelection:
    contract_version: str
    selection: CandidateSelection
    hydrated_candidates: tuple[HydratedCandidate, ...]


def _error(message: str, cause: BaseException | None = None) -> CandidateHydrationError:
    error = CandidateHydrationError(message)
    if cause is not None:
        error.__cause__ = cause
    return error


def _require_lineage(package: VerifiedRetrievalPackage, selection: CandidateSelection) -> None:
    if not isinstance(package, VerifiedRetrievalPackage):
        raise _error("hydration requires a VerifiedRetrievalPackage")
    if not isinstance(selection, CandidateSelection):
        raise _error("hydration requires a CandidateSelection")
    if package.verification_contract_version != VERIFICATION_CONTRACT_VERSION:
        raise _error("unsupported retrieval package verification contract")
    identity = package.package.identity
    expected = {
        "retrieval_package_id": identity.package_id,
        "retrieval_package_identity_version": IDENTITY_VERSION,
        "substrate_sha256": identity.substrate_sha256,
        "vectors_sha256": identity.vectors_sha256,
        "capability_catalog_sha256": identity.capability_catalog_sha256,
        "package_verification_contract_version": VERIFICATION_CONTRACT_VERSION,
    }
    for name, value in expected.items():
        if getattr(selection, name) != value:
            raise _error(f"candidate selection package lineage mismatch: {name}")
    if selection.contract_version != CANDIDATE_SELECTION_CONTRACT_VERSION:
        raise _error("unsupported candidate selection contract")


def _open_read_only_substrate(path: Path) -> sqlite3.Connection:
    try:
        connection = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)
        connection.execute("PRAGMA foreign_keys = ON")
        return connection
    except sqlite3.Error as exc:
        raise _error(f"could not open canonical substrate read-only: {exc}", exc)


def _require_ref(ref: CandidateRef) -> tuple[str, tuple[Any, ...]]:
    if not isinstance(ref, CandidateRef) or not isinstance(ref.target_kind, str) or not isinstance(ref.identity, tuple):
        raise _error("selected candidate reference is malformed")
    return ref.target_kind, ref.identity


def _unit_identity(ref: CandidateRef) -> int:
    kind, identity = _require_ref(ref)
    if kind != "semantic_unit" or len(identity) != 1 or isinstance(identity[0], bool) or not isinstance(identity[0], int):
        raise _error("semantic_unit candidate identity is malformed")
    return identity[0]


def _object_identity(ref: CandidateRef) -> str:
    kind, identity = _require_ref(ref)
    if kind != "semantic_object" or len(identity) != 1 or not isinstance(identity[0], str) or not identity[0]:
        raise _error("semantic_object candidate identity is malformed")
    return identity[0]


def _region_identity(ref: CandidateRef) -> tuple[str, tuple[str, ...]]:
    kind, identity = _require_ref(ref)
    if (
        kind != "semantic_region" or len(identity) != 2
        or not isinstance(identity[0], str) or not identity[0]
        or not isinstance(identity[1], tuple) or not identity[1]
        or not all(isinstance(component, str) and component for component in identity[1])
    ):
        raise _error("semantic_region candidate identity is malformed")
    return identity[0], identity[1]


def _scope_identity(ref: CandidateRef) -> tuple[str, ...]:
    kind, identity = _require_ref(ref)
    if (
        kind != "scope" or len(identity) != 1 or not isinstance(identity[0], tuple)
        or not identity[0] or not all(isinstance(component, str) and component for component in identity[0])
    ):
        raise _error("scope candidate identity is malformed")
    return identity[0]


def _canonical_date(unit: CanonicalUnit, field_name: str) -> dt.date:
    matches = [field for field in unit.inherited_identifiers if field.name == field_name]
    if len(matches) != 1:
        raise _error(f"canonical temporal field is missing or duplicated: {field_name}")
    field = matches[0]
    if field.state != "present_value" or type(field.value) is not dt.date:
        raise _error(f"canonical temporal field is not a native date: {field_name}")
    return field.value


_TEMPORAL_OPERATORS = frozenset({
    "temporal.earliest", "temporal.latest", "temporal.before", "temporal.after",
    "temporal.between", "temporal.ordered",
})


def _validate_date_literal(value: Any, label: str) -> None:
    if not isinstance(value, Mapping) or set(value) != {"domain", "value"}:
        raise _error(f"{label} temporal literal is malformed")
    if value["domain"] != "date" or not isinstance(value["value"], str):
        raise _error(f"{label} temporal literal is malformed")
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value["value"]) is None:
        raise _error(f"{label} temporal literal is not exact YYYY-MM-DD")
    try:
        parsed = dt.date.fromisoformat(value["value"])
    except ValueError as exc:
        raise _error(f"{label} temporal literal is not a valid calendar date", exc)
    if type(parsed) is not dt.date:
        raise _error(f"{label} temporal literal is not a native date")


def _validate_temporal_supports(candidate: Candidate, selection: CandidateSelection, unit: CanonicalUnit) -> None:
    for support in candidate.supports:
        if not isinstance(support, TemporalSupport):
            continue
        if candidate.target_ref.target_kind != "semantic_unit":
            raise _error("temporal support is attached to a non-unit candidate")
        if support.unit_id != unit.unit_id:
            raise _error("temporal support unit identity disagrees with canonical unit")
        if support.request_ordinal < 0 or support.request_ordinal >= len(selection.requests):
            raise _error("temporal support request provenance is out of range")
        request = selection.requests[support.request_ordinal]
        raw = request.request
        if (
            request.ordinal != support.request_ordinal
            or support.occurrence_ordinal < 0
            or request.operator != support.operator
            or request.status != "succeeded"
            or not isinstance(request.returned_occurrences, int)
            or support.occurrence_ordinal >= request.returned_occurrences
            or support.operator not in _TEMPORAL_OPERATORS
            or raw.get("operator") != support.operator
            or raw.get("field_class") != "semantic_identifier"
            or raw.get("target") != "complete_value"
            or not isinstance(raw.get("field_name"), str)
        ):
            raise _error("temporal support request provenance is malformed")
        if support.operator in {"temporal.before", "temporal.after"}:
            if set(raw) != {"operator", "field_class", "field_name", "target", "anchor"}:
                raise _error("temporal support request shape is malformed")
            _validate_date_literal(raw["anchor"], "anchor")
        elif support.operator == "temporal.between":
            if set(raw) != {"operator", "field_class", "field_name", "target", "start", "end"}:
                raise _error("temporal support request shape is malformed")
            _validate_date_literal(raw["start"], "start")
            _validate_date_literal(raw["end"], "end")
        elif support.operator == "temporal.ordered":
            if set(raw) != {"operator", "field_class", "field_name", "target", "direction"}:
                raise _error("temporal support request shape is malformed")
            if raw["direction"] not in {"ascending", "descending"}:
                raise _error("temporal support request direction is malformed")
        elif set(raw) != {"operator", "field_class", "field_name", "target"}:
            raise _error("temporal support request shape is malformed")
        canonical = _canonical_date(unit, raw["field_name"])
        if canonical != support.temporal_date:
            raise _error("temporal support disagrees with canonical date")


def hydrate_candidate_selection(
    verified_package: VerifiedRetrievalPackage,
    selection: CandidateSelection,
) -> HydratedCandidateSelection:
    """Reconstruct every selected canonical target exactly once."""

    _require_lineage(verified_package, selection)
    if not selection.selected_candidates:
        return HydratedCandidateSelection(CANDIDATE_HYDRATION_CONTRACT_VERSION, selection, ())
    try:
        require_current_package_identity(verified_package.package)
    except (OSError, RetrievalPackageError, ValueError) as exc:
        raise _error(f"retrieval package is no longer current: {exc}", exc)

    connection = _open_read_only_substrate(verified_package.package.substrate_path)
    unit_cache: dict[int, CanonicalUnit] = {}
    object_cache: dict[str, CanonicalObject] = {}
    region_cache: dict[tuple[str, tuple[str, ...]], CanonicalRegion] = {}

    def get_unit(unit_id: int) -> CanonicalUnit:
        if unit_id not in unit_cache:
            unit_cache[unit_id] = hydrate_unit(connection, unit_id)
        return unit_cache[unit_id]

    def get_object(source_uuid: str) -> CanonicalObject:
        if source_uuid not in object_cache:
            object_cache[source_uuid] = hydrate_object(connection, source_uuid)
        return object_cache[source_uuid]

    def get_region(source_uuid: str, path: tuple[str, ...]) -> CanonicalRegion:
        key = (source_uuid, path)
        if key not in region_cache:
            region_cache[key] = hydrate_region(connection, source_uuid, path)
        return region_cache[key]

    hydrated: list[HydratedCandidate] = []
    try:
        for candidate in selection.selected_candidates:
            ref = candidate.target_ref
            kind, _ = _require_ref(ref)
            if kind == "semantic_unit":
                unit = get_unit(_unit_identity(ref))
                if unit.unit_id != ref.identity[0]:
                    raise _error("hydrated unit identity disagrees with candidate reference")
                owner = get_object(unit.source_object_uuid)
                if owner.source_object_uuid != unit.source_object_uuid:
                    raise _error("hydrated unit owning object disagrees with canonical identity")
                _validate_temporal_supports(candidate, selection, unit)
                target: HydratedCanonicalTarget = HydratedUnitTarget(unit, owner)
            elif kind == "semantic_object":
                source_uuid = _object_identity(ref)
                obj = get_object(source_uuid)
                if obj.source_object_uuid != source_uuid:
                    raise _error("hydrated object identity disagrees with candidate reference")
                target = HydratedObjectTarget(obj)
            elif kind == "semantic_region":
                source_uuid, path = _region_identity(ref)
                region = get_region(source_uuid, path)
                if (
                    region.reference.source_object_uuid != source_uuid
                    or region.reference.region_path != path
                ):
                    raise _error("hydrated region identity disagrees with candidate reference")
                owner = get_object(source_uuid)
                if owner.source_object_uuid != region.reference.source_object_uuid:
                    raise _error("hydrated region owning object disagrees with canonical identity")
                target = HydratedRegionTarget(region, owner)
            elif kind == "scope":
                path = _scope_identity(ref)
                target = HydratedScopeTarget(GraphHandle("scope", (path,)))
            else:
                raise _error(f"unsupported selected candidate target kind: {kind}")
            hydrated.append(HydratedCandidate(candidate, target))
    except CandidateHydrationError:
        raise
    except (KeyError, OSError, sqlite3.Error, SubstrateError, TypeError, ValueError) as exc:
        raise _error(f"canonical candidate hydration failed: {exc}", exc)
    finally:
        connection.close()
    return HydratedCandidateSelection(CANDIDATE_HYDRATION_CONTRACT_VERSION, selection, tuple(hydrated))


__all__ = [
    "CANDIDATE_HYDRATION_CONTRACT_VERSION", "CandidateHydrationError",
    "HydratedCanonicalTarget", "HydratedUnitTarget", "HydratedObjectTarget",
    "HydratedRegionTarget", "HydratedScopeTarget", "HydratedCandidate",
    "HydratedCandidateSelection", "hydrate_candidate_selection",
]
