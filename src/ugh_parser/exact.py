"""Derivative fielded exact lookup over the canonical SQLite substrate.

The exact table stores only lookup keys and canonical ``unit_id`` values.  It
does not duplicate canonical semantic state and it cannot hydrate a unit by
itself; callers must use :func:`ugh_parser.substrate.hydrate_unit`.
"""

from __future__ import annotations

import json
import re
import sqlite3
from typing import Any, Iterable

from .substrate import SubstrateError, _json_value


REGION_PATH_FIELD = "region_path"
PATH_HIERARCHY_FIELD = "path_hierarchy"
PATH_COMPONENT_FIELD = "path_component"

INTRINSIC_FIELD_CLASS = "intrinsic"
SEMANTIC_IDENTIFIER_FIELD_CLASS = "semantic_identifier"
REGION_FIELD_CLASS = "region"
SEMANTIC_PATH_FIELD_CLASS = "semantic_path"

_WHITESPACE = re.compile(r"\s+")


def _normalize_text(value: str) -> str:
    return _WHITESPACE.sub(" ", value.casefold().strip())


def _text_entry(field_class: str, field_name: str, value: str, unit_id: int) -> tuple[str, str, str, str, int]:
    return field_class, field_name, "text", _normalize_text(value), unit_id


def _typed_entry(field_class: str, field_name: str, value: Any, unit_id: int) -> tuple[str, str, str, str, int]:
    return field_class, field_name, "typed", _json_value(value), unit_id


def _path_entry(field_class: str, field_name: str, values: Iterable[str], unit_id: int) -> tuple[str, str, str, str, int]:
    normalized = [_normalize_text(value) for value in values]
    return field_class, field_name, "path", json.dumps(normalized, ensure_ascii=False, separators=(",", ":")), unit_id


def _insert_identifier_entries(
    connection: sqlite3.Connection,
    unit_id: int,
    field_name: str,
    state: str,
    value_json: str | None,
) -> None:
    if state != "present_value":
        return
    if value_json is None:
        raise SubstrateError("present_value identifier has no stored value")
    value = _decode_storage_value(value_json)
    values = value if isinstance(value, list) else (value,)
    for member in values:
        if isinstance(member, (list, dict)):
            raise SubstrateError(
                f"exact indexing does not support structured sequence member for {field_name!r}"
            )
        entry = (_text_entry(SEMANTIC_IDENTIFIER_FIELD_CLASS, field_name, member, unit_id)
                 if isinstance(member, str)
                 else _typed_entry(SEMANTIC_IDENTIFIER_FIELD_CLASS, field_name, member, unit_id))
        connection.execute("INSERT INTO exact_index_entries (field_class, field_name, value_type, normalized_value, unit_id) VALUES (?, ?, ?, ?, ?)", entry)


def _decode_storage_value(value_json: str) -> Any:
    """Decode the accepted substrate codec without consulting canonical memory."""

    from .substrate import _decode_value

    return _decode_value(value_json)


def build_exact_index(connection: sqlite3.Connection) -> None:
    """Derive the exact lookup table solely from persisted canonical tables."""

    connection.execute("PRAGMA foreign_keys = ON")
    if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
        raise SubstrateError("SQLite foreign-key enforcement could not be enabled")
    with connection:
        connection.executescript(
            """
            DROP TABLE IF EXISTS exact_index_entries;
            CREATE TABLE exact_index_entries (
                exact_entry_id INTEGER PRIMARY KEY,
                field_class TEXT NOT NULL,
                field_name TEXT NOT NULL,
                value_type TEXT NOT NULL,
                normalized_value TEXT NOT NULL,
                unit_id INTEGER NOT NULL,
                FOREIGN KEY (unit_id) REFERENCES canonical_units(unit_id) ON DELETE CASCADE
            );
            CREATE INDEX exact_index_lookup
                ON exact_index_entries(field_class, field_name, value_type, normalized_value, unit_id);
            """
        )

        units = connection.execute(
            "SELECT unit_id, raw_markdown, parsed_text FROM canonical_units ORDER BY unit_id"
        ).fetchall()
        for unit_id, raw_markdown, parsed_text in units:
            connection.executemany(
                "INSERT INTO exact_index_entries (field_class, field_name, value_type, normalized_value, unit_id) VALUES (?, ?, ?, ?, ?)",
                (_text_entry(INTRINSIC_FIELD_CLASS, "raw_markdown", raw_markdown, unit_id),
                 _text_entry(INTRINSIC_FIELD_CLASS, "parsed_text", parsed_text, unit_id)),
            )

            path_components = [value for _, value in connection.execute(
                "SELECT ordinal, component FROM unit_path_components WHERE unit_id = ? ORDER BY ordinal",
                (unit_id,),
            ).fetchall()]
            connection.execute(
                "INSERT INTO exact_index_entries (field_class, field_name, value_type, normalized_value, unit_id) VALUES (?, ?, ?, ?, ?)",
                _path_entry(SEMANTIC_PATH_FIELD_CLASS, PATH_HIERARCHY_FIELD, path_components, unit_id),
            )
            for component in path_components:
                connection.execute(
                    "INSERT INTO exact_index_entries (field_class, field_name, value_type, normalized_value, unit_id) VALUES (?, ?, ?, ?, ?)",
                    _text_entry(SEMANTIC_PATH_FIELD_CLASS, PATH_COMPONENT_FIELD, component, unit_id),
                )

            region_addresses = [row[0] for row in connection.execute(
                """SELECT r.address_text FROM unit_region_path p
                JOIN canonical_regions r
                  ON r.source_object_uuid = p.source_object_uuid
                 AND r.region_path_json = p.region_path_json
                WHERE p.unit_id = ? ORDER BY p.ordinal""",
                (unit_id,),
            ).fetchall()]
            connection.execute(
                "INSERT INTO exact_index_entries (field_class, field_name, value_type, normalized_value, unit_id) VALUES (?, ?, ?, ?, ?)",
                _path_entry(REGION_FIELD_CLASS, REGION_PATH_FIELD, region_addresses, unit_id),
            )

            for field_name, state, value_json in connection.execute(
                "SELECT field_name, state, value_json FROM inherited_identifiers WHERE unit_id = ? ORDER BY ordinal",
                (unit_id,),
            ).fetchall():
                _insert_identifier_entries(connection, unit_id, field_name, state, value_json)


def _lookup_key(field_class: str, field_name: str, value: Any) -> tuple[str, str]:
    if field_class == REGION_FIELD_CLASS and field_name == REGION_PATH_FIELD:
        if not isinstance(value, (list, tuple)) or not all(isinstance(item, str) for item in value):
            raise TypeError("region_path exact lookup requires an ordered sequence of strings")
        return "path", json.dumps([_normalize_text(item) for item in value], ensure_ascii=False, separators=(",", ":"))
    if field_class == SEMANTIC_PATH_FIELD_CLASS and field_name == PATH_HIERARCHY_FIELD:
        if not isinstance(value, (list, tuple)) or not all(isinstance(item, str) for item in value):
            raise TypeError("path_hierarchy exact lookup requires an ordered sequence of strings")
        return "path", json.dumps([_normalize_text(item) for item in value], ensure_ascii=False, separators=(",", ":"))
    if field_class == SEMANTIC_PATH_FIELD_CLASS and field_name == PATH_COMPONENT_FIELD:
        if not isinstance(value, str):
            raise TypeError("path_component exact lookup requires text")
        return "text", _normalize_text(value)
    if isinstance(value, str):
        return "text", _normalize_text(value)
    if isinstance(value, (list, tuple, dict)):
        raise TypeError("exact lookup accepts identifier members, not a complete sequence or mapping")
    return "typed", _json_value(value)


def exact_lookup(connection: sqlite3.Connection, field_class: str, field_name: str, value: Any) -> tuple[int, ...]:
    """Return matching canonical unit IDs, in ascending numeric order."""

    value_type, normalized_value = _lookup_key(field_class, field_name, value)
    rows = connection.execute(
        """SELECT DISTINCT unit_id FROM exact_index_entries
        WHERE field_class = ? AND field_name = ? AND value_type = ? AND normalized_value = ?
        ORDER BY unit_id""",
        (field_class, field_name, value_type, normalized_value),
    ).fetchall()
    return tuple(row[0] for row in rows)
