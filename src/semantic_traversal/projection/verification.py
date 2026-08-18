"""Neutral completed-build integrity verification shared by CLI and runtime."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from .graph import graph_integrity_check
from .lexical import lexical_integrity_check
from .substrate import foreign_key_check, hydrate_object, hydrate_unit
from .temporal import temporal_integrity_check
from .vector import validate_vector_index


class CompletedBuildVerificationError(ValueError):
    """A completed projection failed one of its accepted integrity checks."""


def verify_completed_build(connection: sqlite3.Connection, vector_path: str | Path) -> None:
    """Run the accepted deterministic completed-build checks without CLI ownership."""

    failures = (foreign_key_check(connection) + lexical_integrity_check(connection)
                + graph_integrity_check(connection) + temporal_integrity_check(connection))
    validate_vector_index(connection, Path(vector_path))
    if failures:
        raise CompletedBuildVerificationError(f"completed build integrity failures: {failures!r}")
    for (uuid,) in connection.execute("SELECT source_object_uuid FROM canonical_objects ORDER BY canonical_ordinal"):
        hydrate_object(connection, uuid)
    for (unit_id,) in connection.execute("SELECT unit_id FROM canonical_units ORDER BY unit_id"):
        hydrate_unit(connection, unit_id)


__all__ = ["CompletedBuildVerificationError", "verify_completed_build"]
