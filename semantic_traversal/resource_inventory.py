from __future__ import annotations

import json
import sqlite3
from collections import Counter
from datetime import UTC, datetime
from typing import Any

from .config import RuntimeConfig
from .hashing import sha256_json, sha256_text


INVENTORY_SCHEMA_VERSION = 2
COMPILER_INVENTORY_PROJECTION_VERSION = 1
_CURRENT_SNAPSHOT_KEY = "current"


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


def _path_segments(relative_path: str, depth: int) -> tuple[str, ...]:
    parts = [part for part in relative_path.replace("\\", "/").split("/") if part]
    return tuple("/".join(parts[:level]) for level in range(1, depth + 1) if len(parts) >= level)


def _load_rows(connection: sqlite3.Connection, table_name: str, columns: str) -> list[sqlite3.Row]:
    if not _table_exists(connection, table_name):
        return []
    try:
        return connection.execute(f"SELECT {columns} FROM {table_name} ORDER BY rowid").fetchall()
    except sqlite3.OperationalError:
        return []


def _inventory_policy(config: RuntimeConfig) -> dict[str, Any]:
    controls = config.retrieval_resource_inventory
    return {
        "inventory_schema_version": INVENTORY_SCHEMA_VERSION,
        "admitted_frontmatter_fields": sorted(config.chunking_semantic_frontmatter_fields),
        "path_depth": controls["path_depth"],
        "max_values_per_facet": controls["max_values_per_facet"],
        "max_path_values": controls["max_path_values"],
        "max_graph_types": controls["max_graph_types"],
        "max_temporal_types": controls["max_temporal_types"],
        "capability_sections": ["exact_fts", "vector", "graph", "temporal"],
        "semantic_chunk_surface_version": 1,
    }


def inventory_policy_hash(config: RuntimeConfig) -> str:
    return sha256_json(_inventory_policy(config))


def _bounded_counter(counter: Counter[str], limit: int) -> dict[str, Any]:
    values = [{"value": value, "count": count} for value, count in sorted(counter.items())]
    returned = values[: max(0, limit)]
    return {
        "observed_unique_count": len(values),
        "returned_count": len(returned),
        "omitted_count": max(0, len(values) - len(returned)),
        "truncated": len(returned) < len(values),
        "values": returned,
    }


def _parse_semantics(value: Any) -> dict[str, Any]:
    try:
        parsed = json.loads(str(value or "{}"))
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _facet_value_counter(counter: Counter[str], value: Any) -> None:
    if isinstance(value, str) and value.strip():
        counter[value.strip()] += 1
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, str) and item.strip():
                counter[item.strip()] += 1


def _capability_inventory(connection: sqlite3.Connection, config: RuntimeConfig) -> dict[str, Any]:
    connection.row_factory = sqlite3.Row
    chunk_count = _safe_int(connection.execute("SELECT COUNT(*) FROM chunks").fetchone()[0])
    exact = {
        "table_present": _table_exists(connection, "chunks_fts"),
        "projection_status": "unavailable",
        "row_count": 0,
    }
    if exact["table_present"]:
        exact["row_count"] = _safe_int(connection.execute("SELECT COUNT(*) FROM chunks_fts").fetchone()[0])
        exact["projection_status"] = "valid" if exact["row_count"] == chunk_count else "invalid"

    vector_table = config.vector_table
    vector = {"table_present": _table_exists(connection, vector_table), "row_count": 0, "validation_status": "unavailable", "homogeneous_identity": False, "identities": []}
    if vector["table_present"]:
        rows = connection.execute(f"SELECT embedding_identity_json FROM {vector_table} ORDER BY chunk_id").fetchall()
        identities: dict[str, dict[str, Any]] = {}
        for row in rows:
            try:
                identity = json.loads(str(row[0] or "{}"))
            except json.JSONDecodeError:
                identity = {}
            if isinstance(identity, dict):
                identities[json.dumps(identity, sort_keys=True, separators=(",", ":"))] = identity
        vector.update({"row_count": len(rows), "validation_status": "valid", "homogeneous_identity": len(identities) <= 1, "identities": [identities[key] for key in sorted(identities)]})

    def typed_counts(table: str, field: str, limit: int) -> dict[str, Any]:
        if not _table_exists(connection, table):
            return {"table_present": False, "row_count": 0, "types": _bounded_counter(Counter(), limit)}
        rows = connection.execute(f"SELECT {field}, COUNT(*) FROM {table} GROUP BY {field}").fetchall()
        counter = Counter({str(row[0] or ""): _safe_int(row[1]) for row in rows})
        return {"table_present": True, "row_count": _safe_int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]), "types": _bounded_counter(counter, limit)}

    graph_nodes = typed_counts(config.graph_nodes_table, "node_type", config.retrieval_resource_inventory["max_graph_types"])
    graph_edges = typed_counts(config.graph_edges_table, "edge_type", config.retrieval_resource_inventory["max_graph_types"])
    temporal = typed_counts("temporal_anchors", "anchor_type", config.retrieval_resource_inventory["max_temporal_types"])
    if temporal["table_present"]:
        temporal["authorities"] = _bounded_counter(Counter(str(row[0] or "") for row in connection.execute("SELECT authority FROM temporal_anchors")), config.retrieval_resource_inventory["max_temporal_types"])
        temporal["precisions"] = _bounded_counter(Counter(str(row[0] or "") for row in connection.execute("SELECT precision FROM temporal_anchors")), config.retrieval_resource_inventory["max_temporal_types"])
        temporal["conflict_count"] = _safe_int(connection.execute("SELECT COUNT(*) FROM temporal_anchors WHERE conflict_group IS NOT NULL").fetchone()[0])
        temporal["unresolved_count"] = _safe_int(connection.execute("SELECT COUNT(*) FROM temporal_anchors WHERE unresolved != 0").fetchone()[0])
    return {"exact_fts": exact, "vector": vector, "graph": {"nodes": graph_nodes, "edges": graph_edges}, "temporal": temporal}


def _build_observed_inventory(connection: sqlite3.Connection, config: RuntimeConfig) -> dict[str, Any]:
    connection.row_factory = sqlite3.Row
    controls = config.retrieval_resource_inventory
    note_rows = _load_rows(connection, "notes", "source_root_label, relative_path, frontmatter_semantics_json")
    chunk_count = _safe_int(connection.execute("SELECT COUNT(*) FROM chunks").fetchone()[0])
    source_counter: Counter[str] = Counter()
    path_counters: dict[int, Counter[str]] = {level: Counter() for level in range(1, controls["path_depth"] + 1)}
    facet_counters: dict[str, Counter[str]] = {field: Counter() for field in config.chunking_semantic_frontmatter_fields}
    missing_counts: Counter[str] = Counter()
    for row in note_rows:
        source = str(row["source_root_label"] or "").strip()
        if source:
            source_counter[source] += 1
        for level, value in enumerate(_path_segments(str(row["relative_path"] or ""), controls["path_depth"]), start=1):
            path_counters[level][value] += 1
        semantics = _parse_semantics(row["frontmatter_semantics_json"])
        for field in facet_counters:
            if field not in semantics or semantics[field] in (None, "", []):
                missing_counts[field] += 1
            _facet_value_counter(facet_counters[field], semantics.get(field))
    facet_limit = controls["max_values_per_facet"]
    path_limit = controls["max_path_values"]
    bounded_facets = {field: _bounded_counter(counter, facet_limit) | {"missing_count": missing_counts[field]} for field, counter in sorted(facet_counters.items())}
    path_levels = {str(level): _bounded_counter(counter, path_limit) for level, counter in sorted(path_counters.items())}
    compatibility_facets = {field: bounded_facets[field]["values"] for field in bounded_facets}
    compatibility_paths = {"top_level": path_levels.get("1", {}).get("values", []), "second_level": path_levels.get("2", {}).get("values", [])}
    source_values = [{"label": value, "count": count} for value, count in sorted(source_counter.items())]
    semantic_chunk_surface = {
        "surface_version": 1,
        "components": ["note_title", "relative_path", "section_path", "frontmatter_semantics", "semantic_unit_text"],
        "admitted_frontmatter_fields": sorted(config.chunking_semantic_frontmatter_fields),
        "operator_visibility": {
            "exact_chunk_search": "fts",
            "lexical_chunk_search": "fts",
            "vector_search": "embedding",
            "graph_expand": "canonical_chunk_hydration",
            "temporal_retrieve": "relevance_plus_anchor_with_canonical_chunk_hydration",
        },
    }
    return {
        "corpus_note_count": len(note_rows),
        "corpus_chunk_count": chunk_count,
        "configured_source_labels": sorted([config.vault_source_label]),
        "observed_source_labels": source_values,
        "frontmatter_facets": compatibility_facets,
        "frontmatter_facet_details": bounded_facets,
        "path_topology": compatibility_paths | {"levels": path_levels, "path_depth": controls["path_depth"]},
        "scope_binding_observations": {"note_type": compatibility_facets.get("note_type", []), "source_labels": source_values, "paths": [*compatibility_paths["top_level"], *compatibility_paths["second_level"]]},
        "semantic_chunk_surface": semantic_chunk_surface,
        "capabilities": _capability_inventory(connection, config),
    }


def _with_runtime_overlay(payload: dict[str, Any], config: RuntimeConfig, diagnostics: dict[str, Any]) -> dict[str, Any]:
    summary = json.loads(json.dumps(payload))
    summary["inventory_diagnostics"] = diagnostics
    return summary


def _deterministic_json(value: Any, *, indent: int | None = None) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, indent=indent, separators=None if indent else (",", ":"))


def _projection_capabilities(inventory_summary: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
    capabilities = inventory_summary.get("capabilities")
    if not isinstance(capabilities, dict):
        return [], {"status": "unavailable"}
    available: list[str] = []
    if capabilities.get("exact_fts", {}).get("projection_status") == "valid":
        available.extend(("exact_chunk_search", "lexical_chunk_search"))
    vector = capabilities.get("vector", {})
    if vector.get("table_present") and vector.get("validation_status") == "valid":
        available.append("vector_search")
    graph = capabilities.get("graph", {})
    if all(isinstance(graph.get(key), dict) and graph[key].get("table_present") for key in ("nodes", "edges")):
        available.append("graph_expand")
    temporal = capabilities.get("temporal", {})
    if temporal.get("table_present"):
        available.append("temporal_retrieve")
    statuses = {
        "exact_fts": capabilities.get("exact_fts", {}).get("projection_status", "unavailable"),
        "vector": vector.get("validation_status", "unavailable"),
        "graph": "valid" if "graph_expand" in available else "unavailable",
        "temporal": "valid" if "temporal_retrieve" in available else "unavailable",
    }
    return available, statuses


def build_compiler_inventory_projection(
    *,
    inventory_summary: dict[str, Any],
    config: RuntimeConfig,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build the bounded, compiler-only view of the authoritative inventory.

    This is deliberately an in-memory view. The persisted inventory remains the
    authority for validation, resolver binding, execution, and manifests.
    """
    controls = config.semantic_compiler_inventory_projection
    admitted_fields = sorted(config.chunking_semantic_frontmatter_fields)
    details = inventory_summary.get("frontmatter_facet_details")
    details = details if isinstance(details, dict) else {}
    low_values: dict[str, list[str]] = {}
    omitted_fields: list[str] = []
    omitted_value_count = 0
    fields_with_values_count = 0
    for field in admitted_fields:
        detail = details.get(field) if isinstance(details.get(field), dict) else {}
        unique_count = _safe_int(detail.get("observed_unique_count"))
        values = detail.get("values") if isinstance(detail.get("values"), list) else []
        if unique_count <= controls["low_cardinality_max_unique"]:
            bounded_values = [
                str(item.get("value"))
                for item in sorted(values, key=lambda item: str(item.get("value") if isinstance(item, dict) else ""))[: controls["max_values_per_field"]]
                if isinstance(item, dict) and str(item.get("value") or "").strip()
            ]
            if bounded_values:
                low_values[field] = bounded_values
                fields_with_values_count += 1
            omitted_value_count += max(0, len(values) - len(bounded_values))
        else:
            omitted_fields.append(field)
            omitted_value_count += unique_count

    operators, capability_status = _projection_capabilities(inventory_summary)
    semantic_chunk = {
        "components": ["note_title", "relative_path", "section_path", "admitted_frontmatter", "paragraph_text"],
        "admitted_frontmatter_fields": admitted_fields,
    }
    projection: dict[str, Any] = {
        "projection_version": COMPILER_INVENTORY_PROJECTION_VERSION,
        "inventory_status": str(inventory_summary.get("inventory_diagnostics", {}).get("status") or "unavailable"),
        "semantic_chunk": semantic_chunk,
        "available_retrieval_operators": operators,
        "capability_status": capability_status,
        "low_cardinality_values": low_values,
        "value_enumeration_omitted_for": omitted_fields,
    }
    source_labels = inventory_summary.get("observed_source_labels")
    if isinstance(source_labels, list):
        projection["observed_source_labels"] = [
            str(item.get("label")) for item in sorted(source_labels, key=lambda item: str(item.get("label") if isinstance(item, dict) else ""))[: controls["max_values_per_field"]]
            if isinstance(item, dict) and str(item.get("label") or "").strip()
        ]
    path_topology = inventory_summary.get("path_topology")
    if isinstance(path_topology, dict):
        levels = path_topology.get("levels")
        if isinstance(levels, dict):
            projection["path_regions"] = {
                str(level): [
                    str(item.get("value"))
                    for item in sorted(level_data.get("values", []), key=lambda item: str(item.get("value") if isinstance(item, dict) else ""))[: controls["max_path_values"]]
                    if isinstance(item, dict) and str(item.get("value") or "").strip()
                ]
                for level, level_data in sorted(levels.items())
                if isinstance(level_data, dict)
            }

    mandatory_keys = {"projection_version", "inventory_status", "semantic_chunk", "available_retrieval_operators", "capability_status"}
    diagnostics: dict[str, Any] = {
        "projection_version": COMPILER_INVENTORY_PROJECTION_VERSION,
        "full_inventory_chars": len(_deterministic_json(inventory_summary, indent=2)),
        "max_chars": controls["max_chars"],
        "admitted_field_count": len(admitted_fields),
        "fields_with_values_count": fields_with_values_count,
        "fields_without_values_count": len(admitted_fields) - fields_with_values_count,
        "omitted_value_count": omitted_value_count,
        "omitted_path_count": 0,
        "truncation_applied": False,
    }

    def serialized() -> str:
        return _deterministic_json(projection, indent=2)

    # Reduce optional observations only; field names and operator/surface keys
    # are never sacrificed to meet the budget.
    if len(serialized()) > controls["max_chars"]:
        projection.pop("path_regions", None)
        diagnostics["truncation_applied"] = True
    if len(serialized()) > controls["max_chars"]:
        projection.pop("observed_source_labels", None)
        diagnostics["truncation_applied"] = True
    if len(serialized()) > controls["max_chars"]:
        projection["low_cardinality_values"] = {}
        diagnostics["truncation_applied"] = True
    if len(serialized()) > controls["max_chars"]:
        raise ValueError(
            "semantic compiler inventory projection mandatory content exceeds "
            f"semantic_compiler.inventory_projection.max_chars ({controls['max_chars']})"
        )
    diagnostics["projection_chars"] = len(serialized())
    diagnostics["projection_sha256"] = sha256_text(serialized())
    diagnostics["omitted_path_count"] = sum(
        len(values) for values in (path_topology.get("levels", {}).values() if isinstance(path_topology, dict) and isinstance(path_topology.get("levels"), dict) else [])
        if isinstance(values, dict) and isinstance(values.get("values"), list)
    ) if "path_regions" not in projection else 0
    diagnostics["mandatory_keys_preserved"] = sorted(mandatory_keys)
    return projection, diagnostics


def build_resource_inventory(*, connection: sqlite3.Connection | None, config: RuntimeConfig) -> dict[str, Any]:
    """Recompute the compatibility summary for legacy/fallback paths only."""
    if connection is None:
        payload = {
            "configured_source_labels": [config.vault_source_label],
            "observed_source_labels": [],
            "frontmatter_facets": {"note_type": []},
            "path_topology": {"top_level": [], "second_level": []},
            "graph_capabilities": {"nodes_table_present": False, "edges_table_present": False, "node_count": 0, "edge_count": 0},
        }
        return _with_runtime_overlay(payload, config, {"source": "config_only_fallback", "status": "unavailable", "snapshot_load_count": 0, "full_inventory_rebuilds": 0})
    payload = _build_observed_inventory(connection, config)
    capabilities = payload.pop("capabilities")
    graph = capabilities["graph"]
    payload["graph_capabilities"] = {
        "nodes_table_present": graph["nodes"]["table_present"],
        "edges_table_present": graph["edges"]["table_present"],
        "node_count": graph["nodes"]["row_count"],
        "edge_count": graph["edges"]["row_count"],
    }
    payload["capabilities"] = capabilities
    return _with_runtime_overlay(payload, config, {"source": "recomputed_fallback", "status": "legacy_missing", "snapshot_load_count": 0, "full_inventory_rebuilds": 1})


def build_inventory_snapshot(*, connection: sqlite3.Connection, config: RuntimeConfig, source_ingest_run_id: str, generated_at: str) -> dict[str, Any]:
    payload = _build_observed_inventory(connection, config)
    logical_hash = sha256_json(payload)
    return {
        "snapshot_id": f"snapshot-{logical_hash[:24]}",
        "inventory_schema_version": INVENTORY_SCHEMA_VERSION,
        "source_ingest_run_id": source_ingest_run_id,
        "generated_at": generated_at,
        "inventory_policy_hash": inventory_policy_hash(config),
        "logical_inventory_hash": logical_hash,
        "payload": payload,
        "validation_status": "pending",
    }


def _snapshot_table_exists(connection: sqlite3.Connection) -> bool:
    return _table_exists(connection, "resource_inventory_snapshots")


def persist_inventory_snapshot(*, connection: sqlite3.Connection, snapshot: dict[str, Any]) -> None:
    connection.execute("DELETE FROM resource_inventory_snapshots")
    connection.execute(
        "INSERT INTO resource_inventory_snapshots (snapshot_key, snapshot_id, inventory_schema_version, source_ingest_run_id, generated_at, inventory_policy_hash, logical_inventory_hash, payload_json, validation_status) VALUES (?,?,?,?,?,?,?,?,?)",
        (_CURRENT_SNAPSHOT_KEY, snapshot["snapshot_id"], snapshot["inventory_schema_version"], snapshot["source_ingest_run_id"], snapshot["generated_at"], snapshot["inventory_policy_hash"], snapshot["logical_inventory_hash"], json.dumps(snapshot["payload"], sort_keys=True, separators=(",", ":"), ensure_ascii=True), snapshot["validation_status"]),
    )
    connection.commit()


def validate_inventory_snapshot(*, connection: sqlite3.Connection, config: RuntimeConfig, deep: bool = False) -> dict[str, Any]:
    connection.row_factory = sqlite3.Row
    result: dict[str, Any] = {"status": "invalid", "snapshot_count": 0, "errors": []}
    if not _snapshot_table_exists(connection):
        result["errors"] = ["resource inventory snapshot table is missing"]
        return result
    rows = connection.execute("SELECT * FROM resource_inventory_snapshots ORDER BY snapshot_key").fetchall()
    result["snapshot_count"] = len(rows)
    errors: list[str] = []
    if len(rows) != 1 or str(rows[0]["snapshot_key"]) != _CURRENT_SNAPSHOT_KEY:
        errors.append("exactly one current snapshot is required")
        result["errors"] = errors
        return result
    row = rows[0]
    try:
        payload = json.loads(str(row["payload_json"]))
    except json.JSONDecodeError:
        payload = None
        errors.append("snapshot payload JSON is malformed")
    if not isinstance(payload, dict):
        errors.append("snapshot payload must be an object")
    else:
        if int(row["inventory_schema_version"]) != INVENTORY_SCHEMA_VERSION:
            errors.append("unsupported inventory schema version")
        if str(row["inventory_policy_hash"]) != inventory_policy_hash(config):
            errors.append("inventory policy hash mismatch")
        if str(row["logical_inventory_hash"]) != sha256_json(payload):
            errors.append("logical inventory hash mismatch")
        if not connection.execute("SELECT 1 FROM ingest_runs WHERE run_id = ?", (row["source_ingest_run_id"],)).fetchone():
            errors.append("source ingest run is missing")
        else:
            active_note_run_count = _safe_int(connection.execute("SELECT COUNT(*) FROM notes WHERE last_ingested_run_id = ?", (row["source_ingest_run_id"],)).fetchone()[0])
            if active_note_run_count != _safe_int(payload.get("corpus_note_count")):
                errors.append("snapshot source ingest run is not the active note state")
        if _safe_int(payload.get("corpus_note_count")) != _safe_int(connection.execute("SELECT COUNT(*) FROM notes").fetchone()[0]):
            errors.append("note count mismatch")
        if _safe_int(payload.get("corpus_chunk_count")) != _safe_int(connection.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]):
            errors.append("chunk count mismatch")
        if deep:
            expected = _build_observed_inventory(connection, config)
            if expected != payload:
                errors.append("observed inventory projection mismatch")
    result["errors"] = sorted(set(errors))
    if not errors and str(row["validation_status"]) == "valid":
        result.update({"status": "valid", "snapshot_id": str(row["snapshot_id"]), "logical_inventory_hash": str(row["logical_inventory_hash"]), "inventory_policy_hash": str(row["inventory_policy_hash"]), "source_ingest_run_id": str(row["source_ingest_run_id"])})
    return result


def load_persisted_inventory(*, connection: sqlite3.Connection, config: RuntimeConfig) -> tuple[dict[str, Any], dict[str, Any]]:
    connection.row_factory = sqlite3.Row
    if not _snapshot_table_exists(connection):
        summary = build_resource_inventory(connection=connection, config=config)
        summary["inventory_diagnostics"] = {"source": "recomputed_fallback", "status": "legacy_missing", "snapshot_load_count": 0, "full_inventory_rebuilds": 1, "fallback_reason": "snapshot table is missing"}
        return summary, summary["inventory_diagnostics"]
    validation = validate_inventory_snapshot(connection=connection, config=config)
    if validation.get("status") != "valid":
        summary = build_resource_inventory(connection=connection, config=config)
        diagnostics = {"source": "recomputed_fallback", "status": "corrupt" if validation.get("snapshot_count") else "unavailable", "snapshot_load_count": 1, "full_inventory_rebuilds": 1, "fallback_reason": "; ".join(validation.get("errors", []))}
        summary["inventory_diagnostics"] = diagnostics
        return summary, diagnostics
    row = connection.execute("SELECT payload_json FROM resource_inventory_snapshots WHERE snapshot_key = ?", (_CURRENT_SNAPSHOT_KEY,)).fetchone()
    payload = json.loads(str(row[0]))
    diagnostics = {"source": "persisted", "status": "valid", "snapshot_load_count": 1, "full_inventory_rebuilds": 0, "snapshot_id": validation["snapshot_id"], "source_ingest_run_id": validation["source_ingest_run_id"], "logical_inventory_hash": validation["logical_inventory_hash"], "inventory_policy_hash": validation["inventory_policy_hash"]}
    return _with_runtime_overlay(payload, config, diagnostics), diagnostics
