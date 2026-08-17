"""Immutable identity for the exact artifacts used by retrieval execution."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from .capability_catalog import CapabilityCatalogArtifact, CapabilityCatalogError, load_capability_catalog


IDENTITY_VERSION = "retrieval-package-v1"


class RetrievalPackageError(ValueError):
    """A required retrieval package artifact or identity proof is invalid."""


@dataclass(frozen=True)
class RetrievalPackageIdentity:
    """Content identity of the three exact retrieval artifacts."""

    package_id: str
    substrate_sha256: str
    vectors_sha256: str
    capability_catalog_sha256: str


@dataclass(frozen=True)
class RetrievalPackage:
    """Read-only package paths, identity, and admitted catalog artifact."""

    identity: RetrievalPackageIdentity
    substrate_path: Path
    vectors_path: Path
    capability_catalog_path: Path
    capability_catalog: CapabilityCatalogArtifact


def _sha256_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _read_required(path: Path, label: str) -> bytes:
    try:
        return path.read_bytes()
    except OSError as exc:
        raise RetrievalPackageError(f"could not read required {label} artifact: {exc}") from exc


def _package_id(*, substrate_sha256: str, vectors_sha256: str, capability_catalog_sha256: str) -> str:
    identity = {
        "capability_catalog_sha256": capability_catalog_sha256,
        "identity_version": IDENTITY_VERSION,
        "substrate_sha256": substrate_sha256,
        "vectors_sha256": vectors_sha256,
    }
    canonical = json.dumps(
        identity,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return _sha256_bytes(canonical)


def load_retrieval_package(build_path: str | Path) -> RetrievalPackage:
    """Load the fixed retrieval artifact package from one completed-build directory."""

    build = Path(build_path).resolve()
    substrate_path = build / "substrate.sqlite3"
    vectors_path = build / "vectors.npy"
    capability_catalog_path = build / "capability_catalog.json"

    substrate_sha256 = _sha256_bytes(_read_required(substrate_path, "substrate"))
    vectors_sha256 = _sha256_bytes(_read_required(vectors_path, "vectors"))
    try:
        catalog = load_capability_catalog(capability_catalog_path)
    except CapabilityCatalogError as exc:
        raise RetrievalPackageError(f"capability catalog admission failed: {exc}") from exc

    identity = RetrievalPackageIdentity(
        package_id=_package_id(
            substrate_sha256=substrate_sha256,
            vectors_sha256=vectors_sha256,
            capability_catalog_sha256=catalog.sha256,
        ),
        substrate_sha256=substrate_sha256,
        vectors_sha256=vectors_sha256,
        capability_catalog_sha256=catalog.sha256,
    )
    return RetrievalPackage(
        identity=identity,
        substrate_path=substrate_path,
        vectors_path=vectors_path,
        capability_catalog_path=capability_catalog_path,
        capability_catalog=catalog,
    )


def require_current_package_identity(package: RetrievalPackage) -> None:
    """Require the loaded package paths to still contain its exact artifacts."""

    substrate_sha256 = _sha256_bytes(_read_required(package.substrate_path, "substrate"))
    vectors_sha256 = _sha256_bytes(_read_required(package.vectors_path, "vectors"))
    try:
        catalog = load_capability_catalog(package.capability_catalog_path)
    except CapabilityCatalogError as exc:
        raise RetrievalPackageError(f"current capability catalog admission failed: {exc}") from exc
    package_id = _package_id(
        substrate_sha256=substrate_sha256,
        vectors_sha256=vectors_sha256,
        capability_catalog_sha256=catalog.sha256,
    )
    if (
        substrate_sha256 != package.identity.substrate_sha256
        or vectors_sha256 != package.identity.vectors_sha256
        or catalog.sha256 != package.identity.capability_catalog_sha256
        or package_id != package.identity.package_id
    ):
        raise RetrievalPackageError("retrieval package artifacts no longer match their loaded identity")

def require_catalog_binding(
    package: RetrievalPackage,
    expected_capability_catalog_sha256: str,
) -> None:
    """Require the package to contain the exact catalog identity expected by execution."""

    if package.identity.capability_catalog_sha256 != expected_capability_catalog_sha256:
        raise RetrievalPackageError(
            "retrieval package capability catalog does not match the expected exact artifact identity"
        )


__all__ = [
    "IDENTITY_VERSION",
    "RetrievalPackage",
    "RetrievalPackageError",
    "RetrievalPackageIdentity",
    "load_retrieval_package",
    "require_catalog_binding",
    "require_current_package_identity",
]
