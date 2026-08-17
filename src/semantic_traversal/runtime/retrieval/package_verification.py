"""Deterministic verification that one identified package is execution-coherent."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ...projection.catalog import CatalogGenerationError, catalog_from_facts
from ...projection.capability_facts import CapabilityObservationError, observe_capability_facts
from ...projection.verification import CompletedBuildVerificationError, verify_completed_build
from .package import (
    RetrievalPackage,
    RetrievalPackageError,
    require_current_package_identity,
)


VERIFICATION_CONTRACT_VERSION = "retrieval-package-verification-v1"


class RetrievalPackageVerificationError(ValueError):
    """A package failed deterministic pre-execution verification."""


@dataclass(frozen=True)
class VerifiedRetrievalPackage:
    """An immutable package value that has passed the verification contract."""

    package: RetrievalPackage
    verification_contract_version: str = VERIFICATION_CONTRACT_VERSION


def _catalog_descriptions(catalog: Mapping[str, Any]) -> dict[str, str]:
    descriptions: dict[str, str] = {}
    for entry in catalog["semantic_dimensions"]:
        if entry["field_class"] == "semantic_identifier":
            _merge_description(descriptions, entry["field_name"], entry["description"])
    for entry in catalog["graph"]["relations"]:
        if entry["relation_class"] == "semantic_identifier":
            _merge_description(descriptions, entry["relation_name"], entry["description"])
    return descriptions


def _merge_description(descriptions: dict[str, str], name: str, description: str) -> None:
    previous = descriptions.get(name)
    if previous is not None and previous != description:
        raise RetrievalPackageVerificationError(
            f"semantic identifier {name!r} has inconsistent catalog descriptions"
        )
    descriptions[name] = description


def _sort_key(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


_SET_LIKE_ARRAY_KEYS = frozenset({
    "domains",
    "member_domains",
    "node_kinds",
    "source_kinds",
    "target_kinds",
    "operators",
    "operations",
})
_IDENTITY_ARRAY_KEYS = frozenset({
    "semantic_dimensions",
    "shapes",
    "access",
    "discovery",
    "relations",
    "targets",
})


def _normalize_catalog(value: Any, key: str | None = None) -> Any:
    if isinstance(value, Mapping):
        return {
            name: _normalize_catalog(item, name)
            for name, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        values = [_normalize_catalog(item, key) for item in value]
        if key in _SET_LIKE_ARRAY_KEYS or key in _IDENTITY_ARRAY_KEYS:
            return sorted(values, key=_sort_key)
        return values
    return value


def _assert_catalog_matches_facts(
    catalog: Mapping[str, Any],
    facts: Mapping[str, Any],
) -> None:
    descriptions = _catalog_descriptions(catalog)
    try:
        expected = catalog_from_facts(facts, descriptions)
    except CatalogGenerationError as exc:
        raise RetrievalPackageVerificationError(
            f"catalog cannot represent observed capability facts: {exc}"
        ) from exc
    actual_catalog = _normalize_catalog(catalog)
    expected_catalog = _normalize_catalog(expected)
    if actual_catalog != expected_catalog:
        raise RetrievalPackageVerificationError(
            "capability catalog machine semantics do not match observed completed-build facts"
        )


def verify_retrieval_package(package: RetrievalPackage) -> VerifiedRetrievalPackage:
    """Verify current bytes, completed-build integrity, and catalog/facts coherence."""

    try:
        require_current_package_identity(package)
        connection = sqlite3.connect(
            f"file:{package.substrate_path.as_posix()}?mode=rw",
            uri=True,
        )
        try:
            verify_completed_build(connection, package.vectors_path)
            facts = observe_capability_facts(connection, package.vectors_path)
        finally:
            connection.close()
        _assert_catalog_matches_facts(package.capability_catalog.parsed, facts)
        require_current_package_identity(package)
    except (
        OSError,
        sqlite3.Error,
        CapabilityObservationError,
        CompletedBuildVerificationError,
        RetrievalPackageError,
        ValueError,
        RetrievalPackageVerificationError,
    ) as exc:
        if isinstance(exc, RetrievalPackageVerificationError):
            raise
        raise RetrievalPackageVerificationError(
            f"retrieval package verification failed: {exc}"
        ) from exc
    return VerifiedRetrievalPackage(package)


__all__ = [
    "VERIFICATION_CONTRACT_VERSION",
    "RetrievalPackageVerificationError",
    "VerifiedRetrievalPackage",
    "verify_retrieval_package",
]
