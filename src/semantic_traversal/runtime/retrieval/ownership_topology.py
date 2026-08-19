"""Canonical owner topology derived between composition and selection.

This module exposes only structural ownership.  It deliberately does not
hydrate canonical content or attach retrieval support to the topology.
"""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .candidates import CANDIDATE_WORKSPACE_CONTRACT_VERSION, CandidateRef, CandidateWorkspace
from .package import IDENTITY_VERSION, require_current_package_identity
from .package_verification import VERIFICATION_CONTRACT_VERSION, VerifiedRetrievalPackage


CANDIDATE_OWNERSHIP_TOPOLOGY_CONTRACT_VERSION = "candidate-ownership-topology-v1"


class CandidateOwnershipTopologyError(ValueError):
    """Canonical ownership cannot be derived or does not match its workspace."""


@dataclass(frozen=True)
class CandidateOwnership:
    target_ref: CandidateRef
    owner_object_uuid: str | None


@dataclass(frozen=True)
class CandidateOwnershipTopology:
    contract_version: str
    workspace_contract_version: str
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
    entries: tuple[CandidateOwnership, ...]


def _error(message: str, cause: BaseException | None = None) -> CandidateOwnershipTopologyError:
    error = CandidateOwnershipTopologyError(message)
    if cause is not None:
        error.__cause__ = cause
    return error


def _json_value(value: Any) -> Any:
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    if isinstance(value, dt.date):
        return {"__date__": value.isoformat()}
    if isinstance(value, CandidateRef):
        return {"target_kind": value.target_kind, "identity": _json_value(value.identity)}
    if hasattr(value, "__dataclass_fields__"):
        return {name: _json_value(getattr(value, name)) for name in value.__dataclass_fields__}
    return value


def candidate_ownership_topology_json(topology: CandidateOwnershipTopology) -> dict[str, Any]:
    return _json_value(topology)


def serialize_candidate_ownership_topology(topology: CandidateOwnershipTopology) -> str:
    return json.dumps(
        candidate_ownership_topology_json(topology),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _workspace_lineage(workspace: CandidateWorkspace) -> tuple[Any, ...]:
    return (
        workspace.contract_version, workspace.execution_id, workspace.conformance_id,
        workspace.retrieval_run_id, workspace.retrieval_proposal_sha256,
        workspace.capability_catalog_sha256, workspace.retrieval_package_id,
        workspace.retrieval_package_identity_version, workspace.substrate_sha256,
        workspace.vectors_sha256, workspace.package_verification_contract_version,
        workspace.execution_contract_version,
    )


def _topology_lineage(topology: CandidateOwnershipTopology) -> tuple[Any, ...]:
    return (
        topology.workspace_contract_version, topology.execution_id, topology.conformance_id,
        topology.retrieval_run_id, topology.retrieval_proposal_sha256,
        topology.capability_catalog_sha256, topology.retrieval_package_id,
        topology.retrieval_package_identity_version, topology.substrate_sha256,
        topology.vectors_sha256, topology.package_verification_contract_version,
        topology.execution_contract_version,
    )


def _open_read_only(path: Path) -> sqlite3.Connection:
    try:
        connection = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)
        connection.execute("PRAGMA foreign_keys = ON")
        return connection
    except sqlite3.Error as exc:
        raise _error(f"could not open canonical substrate read-only: {exc}", exc)


def _unique_mapping(rows: list[tuple[Any, Any]], label: str) -> dict[Any, Any]:
    result: dict[Any, Any] = {}
    for key, value in rows:
        if key in result:
            raise _error(f"canonical topology contains duplicate {label}: {key!r}")
        result[key] = value
    return result


def derive_candidate_ownership_topology(
    verified_package: VerifiedRetrievalPackage,
    workspace: CandidateWorkspace,
) -> CandidateOwnershipTopology:
    """Derive one immutable owner entry for every workspace candidate."""
    if not isinstance(verified_package, VerifiedRetrievalPackage):
        raise _error("ownership topology requires a VerifiedRetrievalPackage")
    if verified_package.verification_contract_version != VERIFICATION_CONTRACT_VERSION:
        raise _error("unsupported retrieval package verification contract")
    if not isinstance(workspace, CandidateWorkspace):
        raise TypeError("workspace must be CandidateWorkspace")
    if workspace.contract_version != CANDIDATE_WORKSPACE_CONTRACT_VERSION:
        raise _error("unsupported candidate workspace contract")

    try:
        require_current_package_identity(verified_package.package)
    except Exception as exc:
        raise _error(f"retrieval package is no longer current: {exc}", exc)
    identity = verified_package.package.identity
    expected = (
        workspace.retrieval_package_id == identity.package_id,
        workspace.retrieval_package_identity_version == IDENTITY_VERSION,
        workspace.substrate_sha256 == identity.substrate_sha256,
        workspace.vectors_sha256 == identity.vectors_sha256,
        workspace.capability_catalog_sha256 == identity.capability_catalog_sha256,
        workspace.package_verification_contract_version == VERIFICATION_CONTRACT_VERSION,
    )
    if not all(expected):
        raise _error("workspace does not match the verified retrieval package lineage")

    refs = tuple(candidate.target_ref for candidate in workspace.candidates)
    if len(set(refs)) != len(refs):
        raise _error("workspace contains duplicate candidate references")
    if not refs:
        return CandidateOwnershipTopology(
            CANDIDATE_OWNERSHIP_TOPOLOGY_CONTRACT_VERSION, *_workspace_lineage(workspace), (),
        )

    connection = _open_read_only(verified_package.package.substrate_path)
    try:
        object_rows = connection.execute(
            "SELECT source_object_uuid, source_object_uuid FROM canonical_objects"
        ).fetchall()
        objects = _unique_mapping(object_rows, "object UUID")

        unit_ids = {ref.identity[0] for ref in refs if ref.target_kind == "semantic_unit"}
        unit_rows = connection.execute(
            "SELECT unit_id, source_object_uuid FROM canonical_units"
        ).fetchall()
        units = _unique_mapping(unit_rows, "unit ID")

        region_rows = connection.execute(
            "SELECT source_object_uuid, region_path_json FROM canonical_regions"
        ).fetchall()
        regions: dict[tuple[str, tuple[str, ...]], str] = {}
        for source_uuid, path_json in region_rows:
            try:
                path = tuple(json.loads(path_json))
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                raise _error("canonical region path is not valid JSON", exc)
            if not path or not all(isinstance(component, str) and component for component in path):
                raise _error("canonical region path is malformed")
            key = (source_uuid, path)
            if key in regions:
                raise _error(f"canonical topology contains duplicate region: {key!r}")
            regions[key] = source_uuid

        entries: list[CandidateOwnership] = []
        for ref in refs:
            if ref.target_kind == "semantic_unit":
                if len(ref.identity) != 1 or ref.identity[0] not in unit_ids or ref.identity[0] not in units:
                    raise _error(f"canonical unit owner is missing: {ref!r}")
                owner = units[ref.identity[0]]
                if owner not in objects:
                    raise _error(f"canonical unit owner object is missing: {ref!r}")
            elif ref.target_kind == "semantic_object":
                if len(ref.identity) != 1 or ref.identity[0] not in objects:
                    raise _error(f"canonical object is missing: {ref!r}")
                owner = ref.identity[0]
            elif ref.target_kind == "semantic_region":
                if len(ref.identity) != 2:
                    raise _error(f"canonical region identity is malformed: {ref!r}")
                key = (ref.identity[0], ref.identity[1])
                owner = regions.get(key)
                if owner is None or owner not in objects:
                    raise _error(f"canonical region owner is missing: {ref!r}")
            elif ref.target_kind == "scope":
                owner = None
            else:
                raise _error(f"unsupported candidate target kind: {ref.target_kind!r}")
            entries.append(CandidateOwnership(ref, owner))
    except sqlite3.Error as exc:
        raise _error(f"canonical ownership lookup failed: {exc}", exc)
    finally:
        connection.close()

    return CandidateOwnershipTopology(
        CANDIDATE_OWNERSHIP_TOPOLOGY_CONTRACT_VERSION, *_workspace_lineage(workspace), tuple(entries),
    )


__all__ = [
    "CANDIDATE_OWNERSHIP_TOPOLOGY_CONTRACT_VERSION", "CandidateOwnershipTopologyError",
    "CandidateOwnership", "CandidateOwnershipTopology", "derive_candidate_ownership_topology",
    "candidate_ownership_topology_json", "serialize_candidate_ownership_topology",
]
