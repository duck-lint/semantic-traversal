"""Relational SQLite persistence for a completed canonical ingest.

This module is deliberately limited to the canonical substrate boundary.  The
tables store canonical facts and their factual ordering; they do not constitute
retrieval, graph, vector, catalog, manifest, or publication state.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime
from typing import Any

from .canonical import (
    CanonicalObject,
    CanonicalObjectRelation,
    CanonicalRelation,
    CanonicalRegionReference,
    CanonicalUnit,
    CompletedIngest,
)
from .parser import Embed, FrontmatterField


class SubstrateError(ValueError):
    """The canonical substrate cannot represent or hydrate the supplied data."""


SCHEMA = """
CREATE TABLE canonical_objects (
    source_object_uuid TEXT PRIMARY KEY,
    source_path TEXT NOT NULL,
    canonical_ordinal INTEGER NOT NULL UNIQUE
);
CREATE TABLE object_path_components (
    source_object_uuid TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    component TEXT NOT NULL,
    PRIMARY KEY (source_object_uuid, ordinal),
    FOREIGN KEY (source_object_uuid) REFERENCES canonical_objects(source_object_uuid)
);
CREATE TABLE object_identifiers (
    source_object_uuid TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    field_name TEXT NOT NULL,
    state TEXT NOT NULL,
    value_json TEXT,
    PRIMARY KEY (source_object_uuid, ordinal),
    FOREIGN KEY (source_object_uuid) REFERENCES canonical_objects(source_object_uuid)
);
CREATE TABLE canonical_regions (
    source_object_uuid TEXT NOT NULL,
    region_path_json TEXT NOT NULL,
    canonical_ordinal INTEGER NOT NULL UNIQUE,
    level INTEGER NOT NULL,
    raw_markdown TEXT NOT NULL,
    parsed_text TEXT NOT NULL,
    address_text TEXT NOT NULL,
    parent_region_id TEXT,
    PRIMARY KEY (source_object_uuid, region_path_json),
    FOREIGN KEY (source_object_uuid) REFERENCES canonical_objects(source_object_uuid)
);
CREATE TABLE region_path_components (
    source_object_uuid TEXT NOT NULL,
    region_path_json TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    component TEXT NOT NULL,
    PRIMARY KEY (source_object_uuid, region_path_json, ordinal),
    FOREIGN KEY (source_object_uuid, region_path_json)
        REFERENCES canonical_regions(source_object_uuid, region_path_json)
);
CREATE TABLE canonical_units (
    unit_id INTEGER PRIMARY KEY,
    source_object_uuid TEXT NOT NULL,
    source_path TEXT NOT NULL,
    source_local_order INTEGER NOT NULL,
    raw_markdown TEXT NOT NULL,
    parsed_text TEXT NOT NULL,
    FOREIGN KEY (source_object_uuid) REFERENCES canonical_objects(source_object_uuid)
);
CREATE TABLE object_relations (
    relation_row_id INTEGER PRIMARY KEY,
    source_object_uuid TEXT NOT NULL,
    relation_name TEXT NOT NULL,
    origin TEXT NOT NULL,
    source_field TEXT,
    raw TEXT NOT NULL,
    authored_target TEXT NOT NULL,
    authored_label TEXT NOT NULL,
    authored_region_fragment TEXT,
    source_path TEXT NOT NULL,
    target_object_uuid TEXT NOT NULL,
    target_region_path_json TEXT,
    FOREIGN KEY (source_object_uuid) REFERENCES canonical_objects(source_object_uuid),
    FOREIGN KEY (target_object_uuid) REFERENCES canonical_objects(source_object_uuid),
    FOREIGN KEY (target_object_uuid, target_region_path_json)
        REFERENCES canonical_regions(source_object_uuid, region_path_json)
);
CREATE TABLE unit_path_components (
    unit_id INTEGER NOT NULL,
    ordinal INTEGER NOT NULL,
    component TEXT NOT NULL,
    PRIMARY KEY (unit_id, ordinal),
    FOREIGN KEY (unit_id) REFERENCES canonical_units(unit_id) ON DELETE CASCADE
);
CREATE TABLE unit_region_path (
    unit_id INTEGER NOT NULL,
    ordinal INTEGER NOT NULL,
    source_object_uuid TEXT NOT NULL,
    region_path_json TEXT NOT NULL,
    PRIMARY KEY (unit_id, ordinal),
    FOREIGN KEY (unit_id) REFERENCES canonical_units(unit_id) ON DELETE CASCADE,
    FOREIGN KEY (source_object_uuid, region_path_json)
        REFERENCES canonical_regions(source_object_uuid, region_path_json)
);
CREATE TABLE inherited_identifiers (
    unit_id INTEGER NOT NULL,
    ordinal INTEGER NOT NULL,
    field_name TEXT NOT NULL,
    state TEXT NOT NULL,
    value_json TEXT,
    PRIMARY KEY (unit_id, ordinal),
    FOREIGN KEY (unit_id) REFERENCES canonical_units(unit_id) ON DELETE CASCADE
);
CREATE TABLE canonical_relations (
    relation_row_id INTEGER PRIMARY KEY,
    unit_id INTEGER NOT NULL,
    relation_name TEXT NOT NULL,
    origin TEXT NOT NULL,
    source_field TEXT,
    raw TEXT NOT NULL,
    authored_target TEXT NOT NULL,
    authored_label TEXT NOT NULL,
    authored_region_fragment TEXT,
    source_object_uuid TEXT NOT NULL,
    source_path TEXT NOT NULL,
    source_local_order INTEGER NOT NULL,
    target_object_uuid TEXT NOT NULL,
    target_region_path_json TEXT,
    FOREIGN KEY (unit_id) REFERENCES canonical_units(unit_id) ON DELETE CASCADE,
    FOREIGN KEY (source_object_uuid) REFERENCES canonical_objects(source_object_uuid),
    FOREIGN KEY (target_object_uuid) REFERENCES canonical_objects(source_object_uuid),
    FOREIGN KEY (target_object_uuid, target_region_path_json)
        REFERENCES canonical_regions(source_object_uuid, region_path_json)
);
CREATE TABLE structured_embeds (
    embed_row_id INTEGER PRIMARY KEY,
    unit_id INTEGER NOT NULL,
    raw TEXT NOT NULL,
    target TEXT NOT NULL,
    label TEXT NOT NULL,
    target_region_fragment TEXT,
    FOREIGN KEY (unit_id) REFERENCES canonical_units(unit_id) ON DELETE CASCADE
);
"""


def _path_json(path: tuple[str, ...]) -> str:
    return json.dumps(list(path), ensure_ascii=False, separators=(",", ":"))


def _json_value(value: Any) -> str:
    """Encode values losslessly without coercing typed authored values to text.

    The parser's YAML runtime admits dates, which are not native JSON values.
    Every value therefore carries an explicit codec tag; this also prevents a
    user-authored mapping from being confused with codec metadata.
    """

    def encode(item: Any) -> Any:
        if item is None:
            return ["null", None]
        if isinstance(item, bool):
            return ["bool", item]
        if isinstance(item, int):
            return ["int", item]
        if isinstance(item, float):
            if item != item or item in (float("inf"), float("-inf")):
                raise SubstrateError("identifier value contains a non-finite float")
            return ["float", item]
        if isinstance(item, datetime):
            return ["datetime", item.isoformat()]
        if isinstance(item, date):
            return ["date", item.isoformat()]
        if isinstance(item, str):
            return ["string", item]
        if isinstance(item, list):
            return ["list", [encode(value) for value in item]]
        if isinstance(item, dict) and all(isinstance(key, str) for key in item):
            return ["dict", [[key, encode(value)] for key, value in item.items()]]
        raise SubstrateError(f"identifier value is not supported: {type(item).__name__}")

    return json.dumps(encode(value), ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _decode_value(value_json: str | None) -> Any:
    if value_json is None:
        return None
    try:
        encoded = json.loads(value_json)
    except json.JSONDecodeError as exc:
        raise SubstrateError("stored identifier value is not valid JSON") from exc

    def decode(item: Any) -> Any:
        if not isinstance(item, list) or len(item) != 2:
            raise SubstrateError("stored identifier value has an invalid codec shape")
        tag, payload = item
        if tag == "null":
            return None
        if tag == "bool":
            return payload
        if tag == "int":
            return payload
        if tag == "float":
            return payload
        if tag == "datetime":
            return datetime.fromisoformat(payload)
        if tag == "date":
            return date.fromisoformat(payload)
        if tag == "string":
            return payload
        if tag == "list":
            return [decode(value) for value in payload]
        if tag == "dict":
            return {key: decode(value) for key, value in payload}
        raise SubstrateError(f"stored identifier value has an unknown codec tag: {tag!r}")

    return decode(encoded)


def _enable_foreign_keys(connection: sqlite3.Connection) -> None:
    connection.execute("PRAGMA foreign_keys = ON")
    if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
        raise SubstrateError("SQLite foreign-key enforcement could not be enabled")


def _region_key(reference: CanonicalRegionReference) -> tuple[str, str]:
    return reference.source_object_uuid, _path_json(reference.region_path)


def write_completed_ingest(connection: sqlite3.Connection, completed_ingest: CompletedIngest) -> None:
    """Write exactly the canonical substrate for one completed ingest."""

    if not isinstance(completed_ingest, CompletedIngest):
        raise TypeError("write_completed_ingest requires CompletedIngest")
    _enable_foreign_keys(connection)
    try:
        with connection:
            connection.executescript(SCHEMA)
            for object_ordinal, obj in enumerate(completed_ingest.objects):
                connection.execute(
                    "INSERT INTO canonical_objects VALUES (?, ?, ?)",
                    (obj.source_object_uuid, obj.source_path, object_ordinal),
                )
                connection.executemany(
                    "INSERT INTO object_path_components VALUES (?, ?, ?)",
                    ((obj.source_object_uuid, i, component) for i, component in enumerate(obj.path_hierarchy)),
                )
            for region_ordinal, region in enumerate(completed_ingest.regions):
                object_uuid, path_json = _region_key(region.reference)
                connection.execute(
                    "INSERT INTO canonical_regions VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (object_uuid, path_json, region_ordinal, region.level, region.raw_markdown,
                     region.parsed_text, region.address_text, region.parent_region_id),
                )
                connection.executemany(
                    "INSERT INTO region_path_components VALUES (?, ?, ?, ?)",
                    ((object_uuid, path_json, i, component) for i, component in enumerate(region.reference.region_path)),
                )
            for obj in completed_ingest.objects:
                for i, field in enumerate(obj.admitted_identifiers):
                    if field.state not in {"absent", "present_blank", "present_value"}:
                        raise SubstrateError(f"unknown object identifier state: {field.state!r}")
                    if field.state != "present_value" and field.value is not None:
                        raise SubstrateError("non-value object identifier state has a value")
                    connection.execute(
                        "INSERT INTO object_identifiers VALUES (?, ?, ?, ?, ?)",
                        (obj.source_object_uuid, i, field.name, field.state,
                         _json_value(field.value) if field.state == "present_value" else None),
                    )
                for relation in obj.relations:
                    target_path_json = (_path_json(relation.target_region.region_path)
                                        if relation.target_region is not None else None)
                    connection.execute(
                        """INSERT INTO object_relations
                        (source_object_uuid, relation_name, origin, source_field, raw, authored_target,
                         authored_label, authored_region_fragment, source_path, target_object_uuid,
                         target_region_path_json)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (relation.source_object_uuid, relation.relation_name, relation.origin,
                         relation.source_field, relation.raw, relation.authored_target,
                         relation.authored_label, relation.authored_region_fragment, relation.source_path,
                         relation.target_object_uuid, target_path_json),
                    )
            for unit in completed_ingest.units:
                connection.execute(
                    "INSERT INTO canonical_units VALUES (?, ?, ?, ?, ?, ?)",
                    (unit.unit_id, unit.source_object_uuid, unit.source_path, unit.source_local_order,
                     unit.raw_markdown, unit.parsed_text),
                )
                connection.executemany(
                    "INSERT INTO unit_path_components VALUES (?, ?, ?)",
                    ((unit.unit_id, i, component) for i, component in enumerate(unit.path_hierarchy)),
                )
                connection.executemany(
                    "INSERT INTO unit_region_path VALUES (?, ?, ?, ?)",
                    ((unit.unit_id, i, *_region_key(unit.region_path[i]))
                     for i in range(len(unit.region_path))),
                )
                for i, field in enumerate(unit.inherited_identifiers):
                    if field.state not in {"absent", "present_blank", "present_value"}:
                        raise SubstrateError(f"unknown inherited identifier state: {field.state!r}")
                    if field.state != "present_value" and field.value is not None:
                        raise SubstrateError("non-value inherited identifier state has a value")
                    connection.execute(
                        "INSERT INTO inherited_identifiers VALUES (?, ?, ?, ?, ?)",
                        (unit.unit_id, i, field.name, field.state,
                         _json_value(field.value) if field.state == "present_value" else None),
                    )
                for relation in unit.relations:
                    target_path_json = (_path_json(relation.target_region.region_path)
                                        if relation.target_region is not None else None)
                    connection.execute(
                        """INSERT INTO canonical_relations
                        (unit_id, relation_name, origin, source_field, raw, authored_target,
                         authored_label, authored_region_fragment, source_object_uuid,
                         source_path, source_local_order, target_object_uuid, target_region_path_json)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (unit.unit_id, relation.relation_name, relation.origin, relation.source_field,
                         relation.raw, relation.authored_target, relation.authored_label,
                         relation.authored_region_fragment, relation.source_object_uuid,
                         relation.source_path, relation.source_local_order, relation.target_object_uuid,
                         target_path_json),
                    )
                connection.executemany(
                    "INSERT INTO structured_embeds (unit_id, raw, target, label, target_region_fragment) VALUES (?, ?, ?, ?, ?)",
                    ((unit.unit_id, embed.raw, embed.target, embed.label, embed.target_region_fragment)
                     for embed in unit.embeds),
                )
    except sqlite3.IntegrityError as exc:
        raise SubstrateError(f"canonical substrate integrity failure: {exc}") from exc


def _path_from_rows(rows: list[tuple[int, str]]) -> tuple[str, ...]:
    return tuple(value for _, value in rows)


def _region_reference(connection: sqlite3.Connection, object_uuid: str, path_json: str) -> CanonicalRegionReference:
    row = connection.execute(
        "SELECT 1 FROM canonical_regions WHERE source_object_uuid = ? AND region_path_json = ?",
        (object_uuid, path_json),
    ).fetchone()
    if row is None:
        raise SubstrateError("stored unit or relation refers to an unknown canonical region")
    components = connection.execute(
        "SELECT ordinal, component FROM region_path_components WHERE source_object_uuid = ? AND region_path_json = ? ORDER BY ordinal",
        (object_uuid, path_json),
    ).fetchall()
    return CanonicalRegionReference(object_uuid, _path_from_rows(components))


def hydrate_unit(connection: sqlite3.Connection, unit_id: int) -> CanonicalUnit:
    """Hydrate one full canonical unit using SQLite state only."""

    _enable_foreign_keys(connection)
    row = connection.execute(
        "SELECT source_object_uuid, source_path, source_local_order, raw_markdown, parsed_text FROM canonical_units WHERE unit_id = ?",
        (unit_id,),
    ).fetchone()
    if row is None:
        raise KeyError(f"unknown canonical unit_id: {unit_id}")
    source_uuid, source_path, local_order, raw_markdown, parsed_text = row
    path_hierarchy = _path_from_rows(connection.execute(
        "SELECT ordinal, component FROM unit_path_components WHERE unit_id = ? ORDER BY ordinal", (unit_id,)
    ).fetchall())
    region_path = tuple(_region_reference(connection, object_uuid, path_json) for _, object_uuid, path_json in connection.execute(
        "SELECT ordinal, source_object_uuid, region_path_json FROM unit_region_path WHERE unit_id = ? ORDER BY ordinal", (unit_id,)
    ).fetchall())
    fields: list[FrontmatterField] = []
    for _, name, state, value_json in connection.execute(
        "SELECT ordinal, field_name, state, value_json FROM inherited_identifiers WHERE unit_id = ? ORDER BY ordinal", (unit_id,)
    ).fetchall():
        fields.append(FrontmatterField(name, state, _decode_value(value_json)))
    relations: list[CanonicalRelation] = []
    for row in connection.execute(
        """SELECT relation_name, origin, source_field, raw, authored_target, authored_label,
        authored_region_fragment, source_object_uuid, source_path, source_local_order,
        target_object_uuid, target_region_path_json FROM canonical_relations
        WHERE unit_id = ? ORDER BY relation_row_id""", (unit_id,)
    ).fetchall():
        (name, origin, source_field, raw, target, label, fragment, rel_source_uuid,
         rel_source_path, rel_local_order, target_uuid, target_path_json) = row
        target_region = (_region_reference(connection, target_uuid, target_path_json)
                         if target_path_json is not None else None)
        relations.append(CanonicalRelation(name, origin, source_field, raw, target, label, fragment,
                                            rel_source_uuid, rel_source_path, rel_local_order,
                                            target_uuid, target_region))
    embeds = tuple(Embed(raw, target, label, fragment) for raw, target, label, fragment in connection.execute(
        "SELECT raw, target, label, target_region_fragment FROM structured_embeds WHERE unit_id = ? ORDER BY embed_row_id",
        (unit_id,),
    ).fetchall())
    return CanonicalUnit(unit_id, source_uuid, source_path, local_order, path_hierarchy, region_path,
                         raw_markdown, parsed_text, tuple(fields), tuple(relations), embeds)


def hydrate_object(connection: sqlite3.Connection, source_object_uuid: str) -> CanonicalObject:
    """Hydrate one complete canonical object using SQLite state only."""

    _enable_foreign_keys(connection)
    row = connection.execute(
        "SELECT source_path FROM canonical_objects WHERE source_object_uuid = ?",
        (source_object_uuid,),
    ).fetchone()
    if row is None:
        raise KeyError(f"unknown canonical source_object_uuid: {source_object_uuid}")
    source_path = row[0]
    path_hierarchy = _path_from_rows(connection.execute(
        "SELECT ordinal, component FROM object_path_components WHERE source_object_uuid = ? ORDER BY ordinal",
        (source_object_uuid,),
    ).fetchall())
    fields = tuple(
        FrontmatterField(name, state, _decode_value(value_json))
        for _, name, state, value_json in connection.execute(
            """SELECT ordinal, field_name, state, value_json FROM object_identifiers
            WHERE source_object_uuid = ? ORDER BY ordinal""", (source_object_uuid,)
        ).fetchall()
    )
    relations: list[CanonicalObjectRelation] = []
    for row in connection.execute(
        """SELECT relation_name, origin, source_field, raw, authored_target, authored_label,
        authored_region_fragment, source_path, target_object_uuid, target_region_path_json
        FROM object_relations WHERE source_object_uuid = ? ORDER BY relation_row_id""",
        (source_object_uuid,),
    ).fetchall():
        (name, origin, source_field, raw, target, label, fragment, rel_source_path,
         target_uuid, target_path_json) = row
        target_region = (_region_reference(connection, target_uuid, target_path_json)
                         if target_path_json is not None else None)
        relations.append(CanonicalObjectRelation(
            name, origin, source_field, raw, target, label, fragment,
            source_object_uuid, rel_source_path, target_uuid, target_region,
        ))
    regions: list[Any] = []
    for path_json, level, raw, parsed_text, address_text, parent_region_id in connection.execute(
        """SELECT region_path_json, level, raw_markdown, parsed_text, address_text, parent_region_id
        FROM canonical_regions WHERE source_object_uuid = ? ORDER BY canonical_ordinal""",
        (source_object_uuid,),
    ).fetchall():
        reference = _region_reference(connection, source_object_uuid, path_json)
        from .canonical import CanonicalRegion
        regions.append(CanonicalRegion(reference, level, raw, parsed_text, address_text, parent_region_id))
    return CanonicalObject(source_object_uuid, source_path, path_hierarchy,
                           fields, tuple(relations), tuple(regions))


def foreign_key_check(connection: sqlite3.Connection) -> tuple[tuple[Any, ...], ...]:
    """Return SQLite's complete foreign-key violation report."""

    _enable_foreign_keys(connection)
    return tuple(connection.execute("PRAGMA foreign_key_check").fetchall())
