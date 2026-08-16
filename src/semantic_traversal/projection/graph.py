"""Deterministic graph projection, discovery, and one-hop traversal.

The graph is a derivative of the persisted canonical substrate.  This module
does not consult parser objects, the vault, or the accepted unit retrieval
indexes, and it does not infer relations from authored metadata.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import sqlite3
from pathlib import PurePosixPath
from typing import Any

from .lexical import _TOKENIZER, _fts_literal, _quoted_identifier, _validate_terms_operands
from .substrate import _decode_value


SEMANTIC_OBJECT = "semantic_object"
SEMANTIC_REGION = "semantic_region"
SEMANTIC_UNIT = "semantic_unit"
SCOPE = "scope"

SEMANTIC_IDENTIFIER = "semantic_identifier"
BODY_WIKILINK = "body_wikilink"
STRUCTURAL = "structural"

_DISCOVERY_DIMENSIONS = {
    (SEMANTIC_OBJECT, "address_text"),
    (SEMANTIC_OBJECT, "tag"),
    (SEMANTIC_REGION, "address_text"),
}
_STRUCTURAL_RELATIONS = ("contains_scope", "contains_object", "contains_region", "contains_unit")
_BODY_RELATION = (BODY_WIKILINK, "linked_to")
_DISCOVERY_PREFIX = "graph_discovery_fts_"


@dataclass(frozen=True)
class GraphHandle:
    """Opaque execution handle carrying canonical graph identity."""

    node_kind: str
    identity: tuple[Any, ...]


@dataclass(frozen=True)
class GraphEdgeOccurrence:
    """One direct graph edge occurrence, including authored multiplicity."""

    edge_id: int
    relation_class: str
    relation_name: str
    source: GraphHandle
    target: GraphHandle


@dataclass(frozen=True)
class GraphDiscoveryHit:
    node: GraphHandle
    score: float


@dataclass(frozen=True)
class GraphTraversalHit:
    edge: GraphEdgeOccurrence
    adjacent_node: GraphHandle


class GraphError(ValueError):
    """The persisted graph cannot represent or execute the requested operation."""


GRAPH_SCHEMA = """
CREATE TABLE graph_nodes (
    graph_node_id INTEGER PRIMARY KEY,
    node_kind TEXT NOT NULL,
    canonical_identity_json TEXT NOT NULL,
    UNIQUE (node_kind, canonical_identity_json)
);
CREATE INDEX graph_nodes_kind ON graph_nodes(node_kind);
CREATE TABLE graph_relation_types (
    relation_class TEXT NOT NULL,
    relation_name TEXT NOT NULL,
    PRIMARY KEY (relation_class, relation_name)
);
CREATE TABLE graph_edges (
    graph_edge_id INTEGER PRIMARY KEY,
    source_node_id INTEGER NOT NULL,
    target_node_id INTEGER NOT NULL,
    relation_class TEXT NOT NULL,
    relation_name TEXT NOT NULL,
    FOREIGN KEY (source_node_id) REFERENCES graph_nodes(graph_node_id),
    FOREIGN KEY (target_node_id) REFERENCES graph_nodes(graph_node_id),
    FOREIGN KEY (relation_class, relation_name)
        REFERENCES graph_relation_types(relation_class, relation_name)
);
CREATE INDEX graph_edges_source ON graph_edges(source_node_id);
CREATE INDEX graph_edges_target ON graph_edges(target_node_id);
CREATE INDEX graph_edges_relation ON graph_edges(relation_class, relation_name);
CREATE TABLE graph_discovery_registry (
    node_kind TEXT NOT NULL,
    dimension_name TEXT NOT NULL,
    table_name TEXT NOT NULL UNIQUE,
    PRIMARY KEY (node_kind, dimension_name)
);
"""


def _json_identity(identity: tuple[Any, ...]) -> str:
    return json.dumps(identity, ensure_ascii=False, separators=(",", ":"))


def _object_handle(source_object_uuid: str) -> GraphHandle:
    return GraphHandle(SEMANTIC_OBJECT, (source_object_uuid,))


def _region_handle(source_object_uuid: str, region_path: tuple[str, ...]) -> GraphHandle:
    return GraphHandle(SEMANTIC_REGION, (source_object_uuid, region_path))


def _unit_handle(unit_id: int) -> GraphHandle:
    return GraphHandle(SEMANTIC_UNIT, (unit_id,))


def _scope_handle(scope_path: tuple[str, ...]) -> GraphHandle:
    return GraphHandle(SCOPE, (scope_path,))


def _handle_key(handle: GraphHandle) -> tuple[str, str]:
    return handle.node_kind, _json_identity(handle.identity)


def _decode_path(path_json: str) -> tuple[str, ...]:
    value = json.loads(path_json)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise GraphError("stored canonical path is invalid")
    return tuple(value)


def _object_name(source_path: str) -> str:
    name = PurePosixPath(source_path).name
    return name[:-3] if name.endswith(".md") else name


def _safe_discovery_table(node_kind: str, dimension_name: str) -> str:
    digest = hashlib.sha256(f"{node_kind}\0{dimension_name}".encode("utf-8")).hexdigest()[:24]
    return f"{_DISCOVERY_PREFIX}{digest}"


def _drop_graph(connection: sqlite3.Connection) -> None:
    rows = connection.execute(
        "SELECT table_name FROM graph_discovery_registry"
    ).fetchall() if _table_exists(connection, "graph_discovery_registry") else ()
    for (table_name,) in rows:
        connection.execute(f"DROP TABLE IF EXISTS {_quoted_identifier(table_name)}")
    for table_name in (
        "graph_discovery_registry", "graph_edges", "graph_relation_types", "graph_nodes"
    ):
        connection.execute(f"DROP TABLE IF EXISTS {table_name}")


def _table_exists(connection: sqlite3.Connection, table_name: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table_name,)
    ).fetchone() is not None


def _insert_node(connection: sqlite3.Connection, handle: GraphHandle) -> int:
    node_kind, identity_json = _handle_key(handle)
    connection.execute(
        "INSERT INTO graph_nodes (node_kind, canonical_identity_json) VALUES (?, ?)",
        (node_kind, identity_json),
    )
    return int(connection.execute("SELECT last_insert_rowid()").fetchone()[0])


def _node_id(connection: sqlite3.Connection, handle: GraphHandle) -> int:
    node_kind, identity_json = _handle_key(handle)
    row = connection.execute(
        """SELECT graph_node_id FROM graph_nodes
        WHERE node_kind = ? AND canonical_identity_json = ?""",
        (node_kind, identity_json),
    ).fetchone()
    if row is None:
        raise KeyError(f"unknown graph node handle: {handle!r}")
    return int(row[0])


def _add_edge(
    connection: sqlite3.Connection,
    source: GraphHandle,
    target: GraphHandle,
    relation_class: str,
    relation_name: str,
) -> None:
    connection.execute(
        """INSERT INTO graph_edges
        (source_node_id, target_node_id, relation_class, relation_name)
        VALUES (?, ?, ?, ?)""",
        (_node_id(connection, source), _node_id(connection, target), relation_class, relation_name),
    )


def _register_relations(connection: sqlite3.Connection) -> None:
    connection.executemany(
        "INSERT INTO graph_relation_types VALUES (?, ?)",
        [(STRUCTURAL, name) for name in _STRUCTURAL_RELATIONS] + [_BODY_RELATION],
    )
    fields = connection.execute(
        """SELECT DISTINCT relation_name FROM object_relations
        WHERE relation_name <> 'tags' ORDER BY relation_name"""
    ).fetchall()
    connection.executemany(
        "INSERT INTO graph_relation_types VALUES (?, ?)",
        ((SEMANTIC_IDENTIFIER, relation_name) for (relation_name,) in fields),
    )


def _add_nodes(connection: sqlite3.Connection) -> dict[tuple[str, str], GraphHandle]:
    handles: dict[tuple[str, str], GraphHandle] = {}
    scopes: set[tuple[str, ...]] = set()
    objects = connection.execute(
        "SELECT source_object_uuid, source_path FROM canonical_objects ORDER BY canonical_ordinal"
    ).fetchall()
    for source_uuid, _ in objects:
        path_rows = connection.execute(
            "SELECT component FROM object_path_components WHERE source_object_uuid = ? ORDER BY ordinal",
            (source_uuid,),
        ).fetchall()
        hierarchy = tuple(row[0] for row in path_rows)
        scopes.update(hierarchy[:index] for index in range(1, len(hierarchy) + 1))

    for scope_path in sorted(scopes):
        handle = _scope_handle(scope_path)
        _insert_node(connection, handle)
        handles[_handle_key(handle)] = handle
    for source_uuid, _ in objects:
        handle = _object_handle(source_uuid)
        _insert_node(connection, handle)
        handles[_handle_key(handle)] = handle
    for source_uuid, path_json in connection.execute(
        "SELECT source_object_uuid, region_path_json FROM canonical_regions ORDER BY canonical_ordinal"
    ):
        handle = _region_handle(source_uuid, _decode_path(path_json))
        _insert_node(connection, handle)
        handles[_handle_key(handle)] = handle
    for (unit_id,) in connection.execute("SELECT unit_id FROM canonical_units ORDER BY unit_id"):
        handle = _unit_handle(unit_id)
        _insert_node(connection, handle)
        handles[_handle_key(handle)] = handle
    return handles


def _add_structural_edges(connection: sqlite3.Connection, handles: dict[tuple[str, str], GraphHandle]) -> None:
    scopes = [
        _handle_from_row(SCOPE, identity_json)
        for identity_json, in connection.execute(
            "SELECT canonical_identity_json FROM graph_nodes WHERE node_kind = ? ORDER BY graph_node_id",
            (SCOPE,),
        )
    ]
    for scope in scopes:
        path = scope.identity[0]
        if len(path) > 1:
            _add_edge(connection, _scope_handle(path[:-1]), scope, STRUCTURAL, "contains_scope")

    for source_uuid, _ in connection.execute(
        "SELECT source_object_uuid, source_path FROM canonical_objects ORDER BY canonical_ordinal"
    ):
        hierarchy = tuple(row[0] for row in connection.execute(
            "SELECT component FROM object_path_components WHERE source_object_uuid = ? ORDER BY ordinal",
            (source_uuid,),
        ))
        if hierarchy:
            _add_edge(connection, _scope_handle(hierarchy), _object_handle(source_uuid), STRUCTURAL, "contains_object")

    for source_uuid, path_json in connection.execute(
        """SELECT source_object_uuid, region_path_json FROM canonical_regions
        ORDER BY canonical_ordinal"""
    ):
        path = _decode_path(path_json)
        parent = _region_handle(source_uuid, path[:-1]) if len(path) > 1 else _object_handle(source_uuid)
        _add_edge(connection, parent, _region_handle(source_uuid, path), STRUCTURAL, "contains_region")

    unit_regions: dict[int, tuple[str, tuple[str, ...]] | None] = {}
    for unit_id, source_uuid, path_json in connection.execute(
        """SELECT unit_id, source_object_uuid, region_path_json FROM unit_region_path
        ORDER BY unit_id, ordinal"""
    ):
        unit_regions[unit_id] = (source_uuid, _decode_path(path_json))
    for (unit_id, source_uuid) in connection.execute(
        "SELECT unit_id, source_object_uuid FROM canonical_units ORDER BY unit_id"
    ):
        containing = unit_regions.get(unit_id)
        parent = _region_handle(*containing) if containing is not None else _object_handle(source_uuid)
        _add_edge(connection, parent, _unit_handle(unit_id), STRUCTURAL, "contains_unit")


def _target_handle(target_object_uuid: str, target_path_json: str | None) -> GraphHandle:
    return (_region_handle(target_object_uuid, _decode_path(target_path_json))
            if target_path_json is not None else _object_handle(target_object_uuid))


def _add_authored_edges(connection: sqlite3.Connection) -> None:
    for row in connection.execute(
        """SELECT source_object_uuid, relation_name, authored_target, target_object_uuid,
        target_region_path_json FROM object_relations
        WHERE relation_name <> 'tags' ORDER BY relation_row_id"""
    ):
        source_uuid, relation_name, _, target_uuid, target_path_json = row
        _add_edge(connection, _object_handle(source_uuid), _target_handle(target_uuid, target_path_json),
                  SEMANTIC_IDENTIFIER, relation_name)
    for row in connection.execute(
        """SELECT unit_id, authored_target, target_object_uuid, target_region_path_json
        FROM canonical_relations WHERE origin = 'body' AND relation_name = 'linked_to'
        ORDER BY relation_row_id"""
    ):
        unit_id, _, target_uuid, target_path_json = row
        _add_edge(connection, _unit_handle(unit_id), _target_handle(target_uuid, target_path_json),
                  BODY_WIKILINK, "linked_to")


def _create_discovery_dimension(connection: sqlite3.Connection, node_kind: str, dimension_name: str) -> str:
    table_name = _safe_discovery_table(node_kind, dimension_name)
    quoted = _quoted_identifier(table_name)
    connection.execute(
        f"CREATE VIRTUAL TABLE {quoted} USING fts5(node_id UNINDEXED, content, tokenize='{_TOKENIZER}')"
    )
    connection.execute(
        "INSERT INTO graph_discovery_registry VALUES (?, ?, ?)",
        (node_kind, dimension_name, table_name),
    )
    return table_name


def _add_discovery_occurrence(connection: sqlite3.Connection, table_name: str, node_id: int, content: str) -> None:
    connection.execute(
        f"INSERT INTO {_quoted_identifier(table_name)} (node_id, content) VALUES (?, ?)",
        (node_id, content),
    )


def _direct_text_members(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    if isinstance(value, list):
        return tuple(item for item in value if isinstance(item, str))
    return ()


def _add_discovery_indexes(connection: sqlite3.Connection) -> None:
    object_address = _create_discovery_dimension(connection, SEMANTIC_OBJECT, "address_text")
    has_tags = connection.execute(
        "SELECT 1 FROM object_identifiers WHERE field_name = 'tags' LIMIT 1"
    ).fetchone() is not None
    object_tags = (_create_discovery_dimension(connection, SEMANTIC_OBJECT, "tag")
                   if has_tags else None)
    region_address = _create_discovery_dimension(connection, SEMANTIC_REGION, "address_text")
    for source_uuid, source_path in connection.execute(
        "SELECT source_object_uuid, source_path FROM canonical_objects ORDER BY canonical_ordinal"
    ):
        node_id = _node_id(connection, _object_handle(source_uuid))
        _add_discovery_occurrence(connection, object_address, node_id, _object_name(source_path))
        for field_name, state, value_json in connection.execute(
            """SELECT field_name, state, value_json FROM object_identifiers
            WHERE source_object_uuid = ? AND field_name IN ('aliases', 'tags')
            ORDER BY ordinal""", (source_uuid,)
        ):
            if state != "present_value" or value_json is None:
                continue
            for value in _direct_text_members(_decode_value(value_json)):
                if field_name == "aliases":
                    _add_discovery_occurrence(connection, object_address, node_id, value)
                elif object_tags is not None:
                    _add_discovery_occurrence(connection, object_tags, node_id, value)
    for source_uuid, path_json, address_text in connection.execute(
        "SELECT source_object_uuid, region_path_json, address_text FROM canonical_regions ORDER BY canonical_ordinal"
    ):
        _add_discovery_occurrence(
            connection, region_address, _node_id(connection, _region_handle(source_uuid, _decode_path(path_json))), address_text
        )


def build_graph(connection: sqlite3.Connection) -> None:
    """Build graph storage and isolated discovery indexes from canonical SQLite only."""

    connection.execute("PRAGMA foreign_keys = ON")
    if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
        raise GraphError("SQLite foreign-key enforcement could not be enabled")
    with connection:
        _drop_graph(connection)
        connection.executescript(GRAPH_SCHEMA)
        _register_relations(connection)
        handles = _add_nodes(connection)
        _add_structural_edges(connection, handles)
        _add_authored_edges(connection)
        _add_discovery_indexes(connection)


def _handle_from_row(node_kind: str, identity_json: str) -> GraphHandle:
    identity = json.loads(identity_json)
    if node_kind == SEMANTIC_REGION:
        return _region_handle(identity[0], tuple(identity[1]))
    if node_kind == SCOPE:
        return _scope_handle(tuple(identity[0]))
    if node_kind == SEMANTIC_OBJECT:
        return _object_handle(identity[0])
    if node_kind == SEMANTIC_UNIT:
        return _unit_handle(identity[0])
    raise GraphError(f"unknown stored graph node kind: {node_kind!r}")


def _edge_from_row(row: tuple[Any, ...]) -> GraphEdgeOccurrence:
    edge_id, relation_class, relation_name, source_kind, source_identity, target_kind, target_identity = row
    return GraphEdgeOccurrence(
        int(edge_id), relation_class, relation_name,
        _handle_from_row(source_kind, source_identity),
        _handle_from_row(target_kind, target_identity),
    )


def _edge_query(connection: sqlite3.Connection, where: str = "", params: tuple[Any, ...] = ()) -> tuple[GraphEdgeOccurrence, ...]:
    rows = connection.execute(
        f"""SELECT e.graph_edge_id, e.relation_class, e.relation_name,
        sn.node_kind, sn.canonical_identity_json, tn.node_kind, tn.canonical_identity_json
        FROM graph_edges e
        JOIN graph_nodes sn ON sn.graph_node_id = e.source_node_id
        JOIN graph_nodes tn ON tn.graph_node_id = e.target_node_id
        {where} ORDER BY e.graph_edge_id""", params,
    ).fetchall()
    return tuple(_edge_from_row(row) for row in rows)


def _validate_relation_type(connection: sqlite3.Connection, relation_class: str, relation_name: str) -> None:
    row = connection.execute(
        "SELECT 1 FROM graph_relation_types WHERE relation_class = ? AND relation_name = ?",
        (relation_class, relation_name),
    ).fetchone()
    if row is None:
        raise KeyError(f"unknown graph relation type: {relation_class}/{relation_name}")


def graph_relation_lookup(connection: sqlite3.Connection, relation_class: str, relation_name: str) -> tuple[GraphEdgeOccurrence, ...]:
    """Return every occurrence of one exact represented relation type."""

    _validate_relation_type(connection, relation_class, relation_name)
    return _edge_query(
        connection,
        "WHERE e.relation_class = ? AND e.relation_name = ?",
        (relation_class, relation_name),
    )


def graph_traverse(
    connection: sqlite3.Connection,
    node: GraphHandle,
    relation_class: str,
    relation_name: str,
    direction: str,
) -> tuple[GraphTraversalHit, ...]:
    """Follow exactly one represented edge type in one direction."""

    node_id = _node_id(connection, node)
    _validate_relation_type(connection, relation_class, relation_name)
    if direction not in {"outbound", "inbound"}:
        raise ValueError("graph traversal direction must be outbound or inbound")
    column = "e.source_node_id" if direction == "outbound" else "e.target_node_id"
    edges = _edge_query(
        connection,
        f"WHERE {column} = ? AND e.relation_class = ? AND e.relation_name = ?",
        (node_id, relation_class, relation_name),
    )
    return tuple(GraphTraversalHit(edge, edge.target if direction == "outbound" else edge.source) for edge in edges)


def _discovery_table(connection: sqlite3.Connection, node_kind: str, dimension_name: str) -> str:
    if (node_kind, dimension_name) not in _DISCOVERY_DIMENSIONS:
        raise KeyError(f"unknown graph discovery dimension: {node_kind}/{dimension_name}")
    row = connection.execute(
        "SELECT table_name FROM graph_discovery_registry WHERE node_kind = ? AND dimension_name = ?",
        (node_kind, dimension_name),
    ).fetchone()
    if row is None:
        raise KeyError(f"graph discovery dimension is not built: {node_kind}/{dimension_name}")
    return row[0]


def graph_discover(
    connection: sqlite3.Connection,
    node_kind: str,
    dimension_name: str,
    operator: str,
    operand: Any,
) -> tuple[GraphDiscoveryHit, ...]:
    """Discover graph nodes through one isolated authored lexical dimension."""

    table_name = _discovery_table(connection, node_kind, dimension_name)
    if operator == "terms":
        if not isinstance(operand, (list, tuple)) or not operand or not all(isinstance(item, str) and item for item in operand):
            raise TypeError("terms requires a non-empty sequence of non-empty strings")
        _validate_terms_operands(connection, tuple(operand))
        expression = " OR ".join(_fts_literal(item) for item in operand)
    elif operator == "phrase":
        if not isinstance(operand, str) or not operand.strip():
            raise TypeError("phrase requires a non-empty string")
        expression = _fts_literal(operand)
    else:
        raise ValueError("graph discovery operator must be terms or phrase")
    quoted = _quoted_identifier(table_name)
    rows = connection.execute(
        f"""SELECT node_id, bm25({quoted}) FROM {quoted}
        WHERE {quoted} MATCH ?""", (expression,)
    ).fetchall()
    best: dict[int, float] = {}
    for node_id, score in rows:
        best[int(node_id)] = min(best.get(int(node_id), float("inf")), float(score))
    hits: list[GraphDiscoveryHit] = []
    for node_id, score in best.items():
        row = connection.execute(
            "SELECT node_kind, canonical_identity_json FROM graph_nodes WHERE graph_node_id = ?",
            (node_id,),
        ).fetchone()
        if row is None:
            raise GraphError("graph discovery occurrence refers to an unknown node")
        hits.append(GraphDiscoveryHit(_handle_from_row(row[0], row[1]), score))
    return tuple(sorted(hits, key=lambda hit: (hit.score, _handle_key(hit.node))))


def graph_integrity_check(connection: sqlite3.Connection) -> tuple[tuple[Any, ...], ...]:
    """Return graph foreign-key and discovery-index integrity failures."""

    connection.execute("PRAGMA foreign_keys = ON")
    failures = list(connection.execute("PRAGMA foreign_key_check").fetchall())
    for (table_name,) in connection.execute("SELECT table_name FROM graph_discovery_registry ORDER BY table_name"):
        quoted = _quoted_identifier(table_name)
        try:
            connection.execute(f"INSERT INTO {quoted}({quoted}) VALUES ('integrity-check')")
        except sqlite3.DatabaseError as exc:
            failures.append((table_name, str(exc)))
    return tuple(failures)
