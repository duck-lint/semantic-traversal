from __future__ import annotations

import json
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any

from .config import RuntimeConfig


def _table_exists(connection: sqlite3.Connection, table_name: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table', 'view') AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def _safe_int(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _counts_from_rows(values: list[str]) -> list[dict[str, Any]]:
    counter = Counter(value for value in values if value)
    return [{"value": value, "count": count} for value, count in sorted(counter.items())]


def _path_segments(relative_path: str) -> tuple[str, str]:
    parts = [part for part in relative_path.replace("\\", "/").split("/") if part]
    top_level = parts[0] if parts else ""
    second_level = "/".join(parts[:2]) if len(parts) > 1 else ""
    return top_level, second_level


def _load_rows(connection: sqlite3.Connection, table_name: str, columns: str) -> list[sqlite3.Row]:
    if not _table_exists(connection, table_name):
        return []
    try:
        connection.row_factory = sqlite3.Row
        return connection.execute(f"SELECT {columns} FROM {table_name}").fetchall()
    except sqlite3.OperationalError:
        return []


def build_resource_inventory(
    *,
    connection: sqlite3.Connection | None,
    config: RuntimeConfig,
) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "configured_source_labels": [config.vault_source_label],
        "observed_source_labels": [],
        "frontmatter_facets": {"note_type": []},
        "path_topology": {"top_level": [], "second_level": []},
        "graph_capabilities": {
            "nodes_table_present": False,
            "edges_table_present": False,
            "node_count": 0,
            "edge_count": 0,
        },
        "scope_aliases": {},
    }
    if connection is None:
        summary["scope_aliases"] = config.retrieval_scope_aliases
        return summary

    try:
        note_rows = _load_rows(
            connection,
            "notes",
            "source_root_label, relative_path, frontmatter_semantics_json",
        )
        chunk_rows = _load_rows(
            connection,
            "chunks",
            "source_root_label, relative_path, frontmatter_semantics_json",
        )
    except sqlite3.OperationalError:
        note_rows = []
        chunk_rows = []

    inventory_rows = note_rows if note_rows else chunk_rows
    source_counter = Counter()
    note_type_counter = Counter()
    top_level_counter = Counter()
    second_level_counter = Counter()
    for row in inventory_rows:
        source_label = str(row["source_root_label"] or "").strip()
        if source_label:
            source_counter[source_label] += 1
        relative_path = str(row["relative_path"] or "").strip()
        if relative_path:
            top_level, second_level = _path_segments(relative_path)
            if top_level:
                top_level_counter[top_level] += 1
            if second_level:
                second_level_counter[second_level] += 1
        try:
            semantics = json.loads(str(row["frontmatter_semantics_json"] or "{}"))
        except json.JSONDecodeError:
            semantics = {}
        if isinstance(semantics, dict):
            note_type = semantics.get("note_type")
            if isinstance(note_type, str) and note_type.strip():
                note_type_counter[note_type.strip()] += 1

    summary["observed_source_labels"] = [{"label": label, "count": count} for label, count in sorted(source_counter.items())]
    summary["frontmatter_facets"]["note_type"] = [{"value": value, "count": count} for value, count in sorted(note_type_counter.items())]
    summary["path_topology"]["top_level"] = [{"value": value, "count": count} for value, count in sorted(top_level_counter.items())]
    summary["path_topology"]["second_level"] = [{"value": value, "count": count} for value, count in sorted(second_level_counter.items())]
    summary["graph_capabilities"] = {
        "nodes_table_present": _table_exists(connection, config.graph_nodes_table),
        "edges_table_present": _table_exists(connection, config.graph_edges_table),
        "node_count": 0,
        "edge_count": 0,
    }
    if summary["graph_capabilities"]["nodes_table_present"]:
        try:
            summary["graph_capabilities"]["node_count"] = _safe_int(
                connection.execute(f"SELECT COUNT(*) FROM {config.graph_nodes_table}").fetchone()[0]
            )
        except sqlite3.OperationalError:
            summary["graph_capabilities"]["node_count"] = 0
    if summary["graph_capabilities"]["edges_table_present"]:
        try:
            summary["graph_capabilities"]["edge_count"] = _safe_int(
                connection.execute(f"SELECT COUNT(*) FROM {config.graph_edges_table}").fetchone()[0]
            )
        except sqlite3.OperationalError:
            summary["graph_capabilities"]["edge_count"] = 0
    summary["scope_aliases"] = config.retrieval_scope_aliases
    return summary
