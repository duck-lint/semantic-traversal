"""Exhaustive temporal-v1 access over canonical inherited date facts.

The tables in this module are derivative lookup state.  Hits contain only the
canonical unit identity and the represented date; callers hydrate units from
the canonical substrate after choosing a surface result.
"""

from __future__ import annotations

import datetime as dt
import sqlite3
from dataclasses import dataclass

from .substrate import _decode_value


TEMPORAL_FIELD_CLASS = "semantic_identifier"
TEMPORAL_FIELD_NAME = "journal_entry_date"
TEMPORAL_DOMAIN = "date"
_OPERATORS = ("earliest", "latest", "before", "after", "between", "ordered")


class TemporalProjectionError(ValueError):
    """Canonical state cannot satisfy the closed temporal-v1 contract."""


@dataclass(frozen=True)
class TemporalHit:
    unit_id: int
    date: dt.date


def _require_date(value: object, context: str) -> dt.date:
    if type(value) is not dt.date:
        raise TemporalProjectionError(
            f"{context} must be a native date for temporal-v1, got {type(value).__name__}"
        )
    return value


def _date_text(value: dt.date) -> str:
    return value.isoformat()


def build_temporal_index(connection: sqlite3.Connection) -> None:
    """Rebuild temporal-v1 state solely from persisted canonical identifiers."""

    connection.execute("PRAGMA foreign_keys = ON")
    if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
        raise TemporalProjectionError("SQLite foreign-key enforcement could not be enabled")
    with connection:
        connection.executescript(
            """
            DROP TABLE IF EXISTS temporal_index_entries;
            DROP TABLE IF EXISTS temporal_dimension_registry;
            CREATE TABLE temporal_dimension_registry (
                field_class TEXT NOT NULL,
                field_name TEXT NOT NULL,
                domain TEXT NOT NULL,
                PRIMARY KEY (field_class, field_name)
            );
            CREATE TABLE temporal_index_entries (
                temporal_entry_id INTEGER PRIMARY KEY,
                unit_id INTEGER NOT NULL,
                field_class TEXT NOT NULL,
                field_name TEXT NOT NULL,
                domain TEXT NOT NULL,
                date_value TEXT NOT NULL,
                FOREIGN KEY (unit_id) REFERENCES canonical_units(unit_id) ON DELETE CASCADE
            );
            CREATE INDEX temporal_index_lookup
                ON temporal_index_entries(field_class, field_name, date_value, unit_id);
            """
        )
        connection.execute(
            "INSERT INTO temporal_dimension_registry VALUES (?, ?, ?)",
            (TEMPORAL_FIELD_CLASS, TEMPORAL_FIELD_NAME, TEMPORAL_DOMAIN),
        )
        for unit_id, state, value_json in connection.execute(
            """SELECT unit_id, state, value_json FROM inherited_identifiers
            WHERE field_name = ? ORDER BY unit_id, ordinal""",
            (TEMPORAL_FIELD_NAME,),
        ):
            if state != "present_value":
                continue
            if value_json is None:
                raise TemporalProjectionError("present_value journal_entry_date has no stored value")
            value = _require_date(_decode_value(value_json), f"unit {unit_id} journal_entry_date")
            connection.execute(
                """INSERT INTO temporal_index_entries
                (unit_id, field_class, field_name, domain, date_value)
                VALUES (?, ?, ?, ?, ?)""",
                (unit_id, TEMPORAL_FIELD_CLASS, TEMPORAL_FIELD_NAME, TEMPORAL_DOMAIN, _date_text(value)),
            )


def _check_dimension(connection: sqlite3.Connection, field_name: str) -> None:
    if field_name != TEMPORAL_FIELD_NAME:
        raise TemporalProjectionError(f"temporal-v1 does not license field {field_name!r}")
    if connection.execute(
        "SELECT 1 FROM temporal_dimension_registry WHERE field_class = ? AND field_name = ? AND domain = ?",
        (TEMPORAL_FIELD_CLASS, field_name, TEMPORAL_DOMAIN),
    ).fetchone() is None:
        raise TemporalProjectionError("temporal-v1 dimension is absent")


def _hits(connection: sqlite3.Connection, where: str = "", params: tuple[object, ...] = (), order: str = "date_value ASC, unit_id ASC") -> tuple[TemporalHit, ...]:
    rows = connection.execute(
        f"""SELECT unit_id, date_value FROM temporal_index_entries
        WHERE field_class = ? AND field_name = ? AND domain = ? {where}
        ORDER BY {order}""",
        (TEMPORAL_FIELD_CLASS, TEMPORAL_FIELD_NAME, TEMPORAL_DOMAIN, *params),
    ).fetchall()
    return tuple(TemporalHit(unit_id, dt.date.fromisoformat(date_value)) for unit_id, date_value in rows)


def earliest(connection: sqlite3.Connection, field_name: str = TEMPORAL_FIELD_NAME) -> tuple[TemporalHit, ...]:
    _check_dimension(connection, field_name)
    row = connection.execute("SELECT MIN(date_value) FROM temporal_index_entries").fetchone()
    return () if row[0] is None else _hits(connection, "AND date_value = ?", (row[0],), "unit_id ASC")


def latest(connection: sqlite3.Connection, field_name: str = TEMPORAL_FIELD_NAME) -> tuple[TemporalHit, ...]:
    _check_dimension(connection, field_name)
    row = connection.execute("SELECT MAX(date_value) FROM temporal_index_entries").fetchone()
    return () if row[0] is None else _hits(connection, "AND date_value = ?", (row[0],), "unit_id ASC")


def before(connection: sqlite3.Connection, anchor: dt.date, field_name: str = TEMPORAL_FIELD_NAME) -> tuple[TemporalHit, ...]:
    _check_dimension(connection, field_name)
    anchor = _require_date(anchor, "anchor")
    return _hits(connection, "AND date_value < ?", (_date_text(anchor),), "date_value DESC, unit_id ASC")


def after(connection: sqlite3.Connection, anchor: dt.date, field_name: str = TEMPORAL_FIELD_NAME) -> tuple[TemporalHit, ...]:
    _check_dimension(connection, field_name)
    anchor = _require_date(anchor, "anchor")
    return _hits(connection, "AND date_value > ?", (_date_text(anchor),), "date_value ASC, unit_id ASC")


def between(connection: sqlite3.Connection, start: dt.date, end: dt.date, field_name: str = TEMPORAL_FIELD_NAME) -> tuple[TemporalHit, ...]:
    _check_dimension(connection, field_name)
    start, end = _require_date(start, "start"), _require_date(end, "end")
    if start > end:
        raise TemporalProjectionError("between requires start <= end")
    return _hits(connection, "AND date_value >= ? AND date_value <= ?", (_date_text(start), _date_text(end)))


def ordered(connection: sqlite3.Connection, direction: str, field_name: str = TEMPORAL_FIELD_NAME) -> tuple[TemporalHit, ...]:
    _check_dimension(connection, field_name)
    if direction not in {"ascending", "descending"}:
        raise TemporalProjectionError("ordered direction must be ascending or descending")
    chronology = "ASC" if direction == "ascending" else "DESC"
    return _hits(connection, order=f"date_value {chronology}, unit_id ASC")


def temporal_integrity_check(connection: sqlite3.Connection) -> tuple[str, ...]:
    """Return deterministic violations without repairing or mutating state."""

    failures: list[str] = []
    registry = connection.execute("SELECT field_class, field_name, domain FROM temporal_dimension_registry").fetchall()
    if registry != [(TEMPORAL_FIELD_CLASS, TEMPORAL_FIELD_NAME, TEMPORAL_DOMAIN)]:
        failures.append("temporal dimension registry is not exactly temporal-v1")
    entries = connection.execute(
        """SELECT e.temporal_entry_id, e.unit_id, e.field_class, e.field_name, e.domain, e.date_value,
        u.unit_id FROM temporal_index_entries e LEFT JOIN canonical_units u ON u.unit_id = e.unit_id
        ORDER BY e.temporal_entry_id"""
    ).fetchall()
    if any(row[6] is None for row in entries):
        failures.append("temporal entry references an unknown canonical unit")
    if any(row[2:5] != (TEMPORAL_FIELD_CLASS, TEMPORAL_FIELD_NAME, TEMPORAL_DOMAIN) for row in entries):
        failures.append("temporal entry has an invalid licensed identity")
    expected: dict[int, str] = {}
    for unit_id, state, value_json in connection.execute(
        "SELECT unit_id, state, value_json FROM inherited_identifiers WHERE field_name = ? ORDER BY unit_id, ordinal",
        (TEMPORAL_FIELD_NAME,),
    ):
        if state != "present_value":
            continue
        try:
            expected[unit_id] = _date_text(_require_date(_decode_value(value_json), f"unit {unit_id} journal_entry_date"))
        except (TypeError, ValueError, TemporalProjectionError) as exc:
            failures.append(str(exc))
    actual = [(unit_id, date_value) for _, unit_id, _, _, _, date_value, _ in entries]
    if len(actual) != len(set(actual)):
        failures.append("temporal index contains duplicate unit/date entries")
    if sorted(actual) != sorted(expected.items()):
        failures.append("temporal entries do not exactly match eligible canonical dates")
    for _, _, _, _, _, date_value, _ in entries:
        try:
            if _date_text(dt.date.fromisoformat(date_value)) != date_value:
                failures.append("temporal date is not canonical ISO text")
        except ValueError:
            failures.append("temporal date is not a valid ISO date")
    return tuple(failures)


__all__ = ["TemporalHit", "TemporalProjectionError", "build_temporal_index", "temporal_integrity_check", "earliest", "latest", "before", "after", "between", "ordered"]
