"""The specification-owned corpus build for semantic hyperspace.

This module deliberately has no dependency on conversation runtime state.  The
SQLite database is the canonical substrate; FTS, exact lookup, vectors, graph
edges, the catalog, and the manifest are derived publication artifacts.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable, Protocol
from urllib import error, request

import numpy as np
from markdown_it import MarkdownIt
from ruamel.yaml import YAML


WIKILINK_RE = re.compile(r"(?P<embed>!)?\[\[(?P<body>[^\]]+)\]\]")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*$")
UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$")


class BuildError(RuntimeError):
    """A build that cannot be published."""


class EmbeddingProvider(Protocol):
    model_identity: dict[str, Any]

    def embed(self, text: str) -> list[float]:
        ...


class OllamaEmbeddingProvider:
    """Strict Ollama provider for the model named by the specification."""

    def __init__(self, *, base_url: str = "http://localhost:11434", model: str = "qwen3-embedding:0.6b", timeout: int = 120) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.model_identity = {"provider": "ollama", "model": model, "dimensions": 1024, "normalize": True}

    def embed(self, text: str) -> list[float]:
        payload = json.dumps({"model": self.model, "prompt": text}).encode("utf-8")
        try:
            req = request.Request(f"{self.base_url}/api/embeddings", data=payload, headers={"Content-Type": "application/json"})
            with request.urlopen(req, timeout=self.timeout) as response:
                body = json.loads(response.read().decode("utf-8"))
        except (OSError, error.URLError, error.HTTPError, json.JSONDecodeError) as exc:
            raise BuildError(f"embedding provider unavailable: {exc}") from exc
        vector = body.get("embedding")
        if not isinstance(vector, list) or len(vector) != 1024:
            raise BuildError("embedding provider returned a non-1024-dimensional vector")
        values = np.asarray(vector, dtype=np.float32)
        norm = float(np.linalg.norm(values))
        if norm == 0.0:
            raise BuildError("embedding provider returned a zero vector")
        return (values / norm).astype(np.float32).tolist()


class DeterministicEmbeddingProvider:
    """Small deterministic provider used only by acceptance tests."""

    def __init__(self, dimensions: int = 1024) -> None:
        self.dimensions = dimensions
        self.model_identity = {"provider": "test", "model": "deterministic", "dimensions": dimensions, "normalize": True}

    def embed(self, text: str) -> list[float]:
        raw = hashlib.sha256(text.encode("utf-8")).digest()
        values = np.resize(np.frombuffer(raw, dtype=np.uint8).astype(np.float32), self.dimensions)
        values = values / max(float(np.linalg.norm(values)), 1.0)
        return values.tolist()


@dataclass(frozen=True)
class BuildConfig:
    semantic_identifier_fields: tuple[str, ...]
    uuid_field: str = "uuid"
    excluded_folders: tuple[str, ...] = ()
    embedding_max_chars: int = 4000
    parser_version: str = "markdown-it-py"

    @classmethod
    def from_yaml(cls, path: Path) -> "BuildConfig":
        yaml = YAML(typ="safe")
        yaml.version = (1, 2)
        payload = yaml.load(path.read_text(encoding="utf-8")) or {}
        chunking = payload.get("chunking", {})
        uuid_field = str(payload.get("uuid_field") or chunking.get("required_uuid_field", "uuid"))
        configured_fields = payload.get("semantic_identifier_fields")
        fields = tuple(str(value) for value in (configured_fields if configured_fields is not None else chunking.get("semantic_frontmatter_fields", [])))
        excluded = tuple(str(value).replace("\\", "/").strip("/") for value in payload.get("excluded_folders", []))
        return cls(tuple(field for field in fields if field != uuid_field), uuid_field, excluded)


@dataclass
class _Object:
    uuid: str
    relative_path: str
    path_components: tuple[str, ...]
    frontmatter: dict[str, Any]
    aliases: tuple[str, ...]
    regions: list[dict[str, Any]]
    units: list[dict[str, Any]]


def _json(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json(v) for v in value]
    return value


def _normal(value: Any) -> str:
    return " ".join(str(value).split()).casefold()


def _display_wikilinks(text: str) -> str:
    def replace(match: re.Match[str]) -> str:
        body = match.group("body")
        return body.split("|", 1)[1].strip() if "|" in body else body.split("#", 1)[0].strip()
    return WIKILINK_RE.sub(replace, text)


def _parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith("---\n"):
        return {}, text
    lines = text.splitlines(keepends=True)
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if end is None:
        return {}, text
    yaml = YAML(typ="safe")
    yaml.version = (1, 2)
    payload = yaml.load("".join(lines[1:end])) or {}
    if not isinstance(payload, dict):
        raise BuildError("frontmatter must be a mapping")
    return _json(dict(payload)), "".join(lines[end + 1 :])


def _parsed_text(raw: str) -> str:
    md = MarkdownIt("commonmark", {"breaks": True}).enable("table")
    tokens = md.parse(_display_wikilinks(raw))
    parts: list[str] = []
    for token in tokens:
        if token.type == "inline":
            parts.append(token.content)
        elif token.type == "fence":
            parts.append(token.content)
    return " ".join(" ".join(parts).split())


def _blocks(body: str) -> Iterable[tuple[str, str, int | None]]:
    lines = body.splitlines()
    current: list[str] = []
    region_stack: list[tuple[int, str]] = []
    def flush() -> tuple[str, str, int | None] | None:
        nonlocal current
        if not current:
            return None
        raw = "\n".join(current).rstrip()
        current = []
        return ("unit", raw, None)
    for line in lines:
        heading = HEADING_RE.match(line.strip())
        if heading:
            item = flush()
            if item:
                yield item
            level, label = len(heading.group(1)), heading.group(2).strip()
            while region_stack and region_stack[-1][0] >= level:
                region_stack.pop()
            region_stack.append((level, label))
            yield ("region", "|".join(label for _, label in region_stack), level)
        elif not line.strip():
            item = flush()
            if item:
                yield item
        else:
            current.append(line)
    item = flush()
    if item:
        yield item


def _make_regions_and_units(body: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    regions: list[dict[str, Any]] = []
    units: list[dict[str, Any]] = []
    current_region: tuple[str, ...] = ()
    current_region_id: str | None = None
    for kind, raw, level in _blocks(body):
        if kind == "region":
            current_region = tuple(raw.split("|"))
            current_region_id = f"region:{len(regions)}"
            regions.append({"region_id": current_region_id, "heading": current_region[-1], "path": current_region, "level": level})
        else:
            units.append({"region_path": current_region, "region_id": current_region_id, "raw_markdown": raw, "parsed_text": _parsed_text(raw)})
    return regions, units


def _link_parts(raw: str) -> dict[str, Any]:
    body = raw.strip()
    alias = body.split("|", 1)[1].strip() if "|" in body else None
    target = body.split("|", 1)[0].strip()
    note, _, region = target.partition("#")
    return {"raw": f"[[{body}]]", "target": note.removesuffix(".md"), "region": region.strip() or None, "visible_text": alias or (region.strip() if region else note)}


def _links(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, str):
        for match in WIKILINK_RE.finditer(value):
            yield _link_parts(match.group("body"))
    elif isinstance(value, dict):
        for child in value.values():
            yield from _links(child)
    elif isinstance(value, list):
        for child in value:
            yield from _links(child)


def _resolve_target(source: _Object, link: dict[str, Any], objects: dict[str, _Object]) -> tuple[_Object, dict[str, Any] | None]:
    target = link["target"].replace("\\", "/").strip("/")
    candidates = [obj for obj in objects.values() if obj.relative_path.removesuffix(".md") == target or obj.relative_path.rsplit("/", 1)[-1].removesuffix(".md") == target or Path(obj.relative_path).stem == target or target in obj.aliases]
    if "/" in target:
        candidates = [obj for obj in candidates if obj.relative_path.removesuffix(".md") == target]
    if len(candidates) != 1:
        kind = "unresolved" if not candidates else "ambiguous"
        names = [obj.relative_path for obj in candidates]
        raise BuildError(json.dumps({"issue": kind, "source": source.relative_path, "link": link["raw"], "candidates": names}, ensure_ascii=True))
    region = None
    if link["region"] is not None:
        matches = [item for item in candidates[0].regions if item["heading"] == link["region"] or "/".join(item["path"]) == link["region"]]
        if len(matches) != 1:
            raise BuildError(json.dumps({"issue": "unresolved_region", "source": source.relative_path, "link": link["raw"], "target_object_uuid": candidates[0].uuid, "candidates": [item["path"] for item in matches]}, ensure_ascii=True))
        region = matches[0]
    return candidates[0], region


def _is_excluded(relative_path: str, excluded_folders: tuple[str, ...]) -> bool:
    normalized = relative_path.replace("\\", "/").strip("/")
    return any(normalized == folder or normalized.startswith(f"{folder}/") for folder in excluded_folders if folder)


def _safe_column(field: str) -> str:
    return "field_" + re.sub(r"[^A-Za-z0-9_]", "_", field)


def _schema(connection: sqlite3.Connection, fields: tuple[str, ...]) -> None:
    connection.execute("PRAGMA foreign_keys = ON")
    connection.executescript("""
    CREATE TABLE objects (object_uuid TEXT PRIMARY KEY, source_path TEXT NOT NULL UNIQUE, path_components_json TEXT NOT NULL, frontmatter_json TEXT NOT NULL);
    CREATE TABLE regions (region_id TEXT PRIMARY KEY, object_uuid TEXT NOT NULL REFERENCES objects(object_uuid), heading TEXT NOT NULL, region_path_json TEXT NOT NULL, heading_level INTEGER NOT NULL, region_ordinal INTEGER NOT NULL);
    CREATE TABLE units (unit_id TEXT PRIMARY KEY, object_uuid TEXT NOT NULL REFERENCES objects(object_uuid), source_path TEXT NOT NULL, path_components_json TEXT NOT NULL, region_path_json TEXT NOT NULL, raw_markdown TEXT NOT NULL, parsed_text TEXT NOT NULL, identifiers_json TEXT NOT NULL);
    CREATE TABLE identifier_values (unit_id TEXT NOT NULL REFERENCES units(unit_id), field_name TEXT NOT NULL, state TEXT NOT NULL, authored_value_json TEXT, PRIMARY KEY(unit_id, field_name));
    CREATE TABLE relations (relation_id INTEGER PRIMARY KEY, source_unit_id TEXT NOT NULL REFERENCES units(unit_id), relation_name TEXT NOT NULL, source_kind TEXT NOT NULL, target_object_uuid TEXT NOT NULL REFERENCES objects(object_uuid), target_region_id TEXT REFERENCES regions(region_id), raw_markdown TEXT NOT NULL, visible_text TEXT NOT NULL);
    CREATE TABLE graph_nodes (node_id TEXT PRIMARY KEY, node_kind TEXT NOT NULL, identity_json TEXT NOT NULL);
    CREATE TABLE graph_edges (edge_id INTEGER PRIMARY KEY, source_node_id TEXT NOT NULL, relation_name TEXT NOT NULL, target_node_id TEXT NOT NULL);
    CREATE TABLE exact_index (field_name TEXT NOT NULL, normalized_value TEXT NOT NULL, authored_value TEXT NOT NULL, unit_id TEXT NOT NULL REFERENCES units(unit_id), PRIMARY KEY(field_name, normalized_value, unit_id));
    CREATE INDEX exact_lookup ON exact_index(field_name, normalized_value);
    CREATE INDEX graph_source ON graph_edges(source_node_id, relation_name);
    CREATE INDEX graph_target ON graph_edges(target_node_id, relation_name);
    """)


def build_semantic_hyperspace(*, vault_root: Path, output_root: Path, config: BuildConfig, embedding_provider: EmbeddingProvider | None = None) -> dict[str, Any]:
    """Parse, validate, materialize, and atomically publish one completed build."""
    output_root.mkdir(parents=True, exist_ok=True)
    staging = output_root / ".staging"
    staging.mkdir(exist_ok=True)
    database = staging / "substrate.sqlite3"
    for path in (database, staging / "vectors.npy", staging / "capability_catalog.json", staging / "manifest.json"):
        path.unlink(missing_ok=True)
    provider = embedding_provider or OllamaEmbeddingProvider()
    objects: dict[str, _Object] = {}
    failures: list[dict[str, Any]] = []
    for path in sorted(vault_root.rglob("*.md")):
        relative = path.relative_to(vault_root).as_posix()
        if _is_excluded(relative, config.excluded_folders):
            continue
        try:
            frontmatter, body = _parse_frontmatter(path.read_text(encoding="utf-8"))
            value = frontmatter.get(config.uuid_field)
            if not isinstance(value, str) or not UUID_RE.fullmatch(value.strip()):
                raise BuildError(f"missing or invalid {config.uuid_field}")
            object_uuid = str(uuid.UUID(value.strip()))
            regions, units = _make_regions_and_units(body)
            aliases = tuple(str(item) for item in (frontmatter.get("aliases", []) if isinstance(frontmatter.get("aliases", []), list) else [frontmatter.get("aliases")]) if item)
            if object_uuid in objects:
                raise BuildError(f"duplicate UUID {object_uuid}")
            objects[object_uuid] = _Object(object_uuid, relative, tuple(Path(relative).parts[:-1]), frontmatter, aliases, regions, units)
        except Exception as exc:
            failures.append({"source_path": relative, "issue": str(exc)})
    if failures:
        manifest = {"status": "failed", "failure_stage": "parse", "repair_manifest": failures}
        (output_root / "repair_manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
        raise BuildError(f"build failed during parse; repair manifest has {len(failures)} issue(s)")
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(database)
        connection.row_factory = sqlite3.Row
        _schema(connection, config.semantic_identifier_fields)
        all_vectors: list[list[float]] = []
        vector_rows: list[dict[str, Any]] = []
        # Foreign-key validation requires every object and region to exist
        # before any unit relation is materialized.
        for obj in objects.values():
            connection.execute("INSERT INTO objects VALUES (?, ?, ?, ?)", (obj.uuid, obj.relative_path, json.dumps(obj.path_components), json.dumps(obj.frontmatter, ensure_ascii=True, sort_keys=True)))
            for ordinal, region in enumerate(obj.regions):
                region["region_id"] = f"region:{obj.uuid}:{ordinal}"
                region["ordinal"] = ordinal
                connection.execute("INSERT INTO regions VALUES (?, ?, ?, ?, ?, ?)", (region["region_id"], obj.uuid, region["heading"], json.dumps(region["path"]), region["level"], ordinal))
        for obj in objects.values():
            scope_node = "scope:" + "/".join(obj.path_components)
            connection.execute("INSERT OR IGNORE INTO graph_nodes VALUES (?, 'scope', ?)", (scope_node, json.dumps({"path": obj.path_components})))
            parent = "scope:"
            for index in range(len(obj.path_components)):
                scope = "scope:" + "/".join(obj.path_components[: index + 1])
                connection.execute("INSERT OR IGNORE INTO graph_nodes VALUES (?, 'scope', ?)", (scope, json.dumps({"path": obj.path_components[: index + 1]})))
                if index:
                    connection.execute("INSERT INTO graph_edges(source_node_id, relation_name, target_node_id) VALUES (?, 'contains_scope', ?)", (parent, scope))
                parent = scope
            object_node = f"object:{obj.uuid}"
            connection.execute("INSERT INTO graph_nodes VALUES (?, 'semantic_object', ?)", (object_node, json.dumps({"object_uuid": obj.uuid})))
            connection.execute("INSERT INTO graph_edges(source_node_id, relation_name, target_node_id) VALUES (?, 'contains_object', ?)", (scope_node, object_node))
            region_id_by_ordinal: dict[int, str] = {}
            for ordinal, region in enumerate(obj.regions):
                region["region_id"] = f"region:{obj.uuid}:{ordinal}"
                region["ordinal"] = ordinal
                region_id_by_ordinal[ordinal] = region["region_id"]
                region_node = f"region:{obj.uuid}:{ordinal}"
                region["node_id"] = region_node
                connection.execute("INSERT INTO graph_nodes VALUES (?, 'semantic_region', ?)", (region_node, json.dumps({"object_uuid": obj.uuid, "path": region["path"], "ordinal": ordinal})))
                parent_region = next((candidate for candidate in reversed(obj.regions[:ordinal]) if tuple(region["path"][:-1]) == tuple(candidate["path"])), None)
                connection.execute("INSERT INTO graph_edges(source_node_id, relation_name, target_node_id) VALUES (?, 'contains_region', ?)", (parent_region["node_id"] if parent_region else object_node, region_node))
            for ordinal, unit in enumerate(obj.units):
                if unit["region_id"] is not None:
                    unit["region_id"] = region_id_by_ordinal[int(str(unit["region_id"]).rsplit(":", 1)[-1])]
                unit_id = f"unit:{obj.uuid}:{ordinal}"
                unit["unit_id"] = unit_id
                states = {field: ("present_blank" if field in obj.frontmatter and obj.frontmatter[field] in (None, "", []) else "present_value") if field in obj.frontmatter else "absent" for field in config.semantic_identifier_fields}
                identifiers = {field: {"state": state, "value": _json(obj.frontmatter.get(field)) if state == "present_value" else None} for field, state in states.items()}
                connection.execute("INSERT INTO units VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (unit_id, obj.uuid, obj.relative_path, json.dumps(obj.path_components), json.dumps(unit["region_path"]), unit["raw_markdown"], unit["parsed_text"], json.dumps(identifiers, ensure_ascii=True, sort_keys=True)))
                connection.execute("INSERT INTO graph_nodes VALUES (?, 'semantic_unit', ?)", (unit_id, json.dumps({"unit_id": unit_id})))
                parent_node = obj.regions[next((r["ordinal"] for r in obj.regions if r["region_id"] == unit["region_id"]), -1)]["node_id"] if unit["region_id"] is not None else object_node
                connection.execute("INSERT INTO graph_edges(source_node_id, relation_name, target_node_id) VALUES (?, 'contains_unit', ?)", (parent_node, unit_id))
                for field, state in states.items():
                    value = obj.frontmatter.get(field)
                    connection.execute("INSERT INTO identifier_values VALUES (?, ?, ?, ?)", (unit_id, field, state, json.dumps(_json(value), ensure_ascii=True) if state == "present_value" else None))
                    if state == "present_value":
                        values = value if field == "tags" and isinstance(value, list) else [value]
                        for item in values:
                            if isinstance(item, (str, int, float, date, datetime)):
                                authored = str(_json(item))
                                connection.execute("INSERT INTO exact_index VALUES (?, ?, ?, ?)", (field, _normal(authored), authored, unit_id))
                for field in config.semantic_identifier_fields:
                    if field in obj.frontmatter:
                        for link in _links(obj.frontmatter[field]):
                            target, region = _resolve_target(obj, link, objects)
                            connection.execute("INSERT INTO relations(source_unit_id, relation_name, source_kind, target_object_uuid, target_region_id, raw_markdown, visible_text) VALUES (?, ?, 'frontmatter', ?, ?, ?, ?)", (unit_id, field, target.uuid, region["region_id"] if region else None, link["raw"], link["visible_text"]))
                for link in _links(unit["raw_markdown"]):
                    target, region = _resolve_target(obj, link, objects)
                    connection.execute("INSERT INTO relations(source_unit_id, relation_name, source_kind, target_object_uuid, target_region_id, raw_markdown, visible_text) VALUES (?, 'linked_to', 'body', ?, ?, ?, ?)", (unit_id, target.uuid, region["region_id"] if region else None, link["raw"], link["visible_text"]))
                fts_name = f"fts_{ordinal}"  # created below as one fielded table per representation
                connection.execute("INSERT INTO exact_index VALUES ('raw_markdown', ?, ?, ?)", (_normal(unit["raw_markdown"]), unit["raw_markdown"], unit_id))
                connection.execute("INSERT INTO exact_index VALUES ('parsed_text', ?, ?, ?)", (_normal(unit["parsed_text"]), unit["parsed_text"], unit_id))
                for segment_ordinal, segment in enumerate(_segments(unit["parsed_text"], config.embedding_max_chars)):
                    all_vectors.append(provider.embed(segment))
                    vector_rows.append({"unit_id": unit_id, "segment_ordinal": segment_ordinal})
        connection.commit()
        fts_columns = ["unit_id UNINDEXED", "raw_markdown", "parsed_text", "region_path", "semantic_path"] + [_safe_column(field) for field in config.semantic_identifier_fields]
        connection.execute(f"CREATE VIRTUAL TABLE units_fts USING fts5({', '.join(fts_columns)})")
        select_columns = "unit_id, raw_markdown, parsed_text, region_path_json, path_components_json, identifiers_json"
        for row in connection.execute(f"SELECT {select_columns} FROM units"):
            identifiers = json.loads(row["identifiers_json"])
            values = [row["unit_id"], row["raw_markdown"], row["parsed_text"], row["region_path_json"], row["path_components_json"]]
            values.extend(json.dumps(identifiers[field], ensure_ascii=True) for field in config.semantic_identifier_fields)
            connection.execute(f"INSERT INTO units_fts VALUES ({', '.join('?' for _ in values)})", values)
        connection.commit()
        connection.close()
        connection = None
        np.save(staging / "vectors.npy", np.asarray(all_vectors, dtype=np.float32))
        connection = sqlite3.connect(database)
        connection.execute("CREATE TABLE vector_records(vector_id INTEGER PRIMARY KEY, unit_id TEXT NOT NULL, segment_ordinal INTEGER NOT NULL, matrix_row INTEGER NOT NULL)")
        connection.executemany("INSERT INTO vector_records VALUES (?, ?, ?, ?)", [(i, row["unit_id"], row["segment_ordinal"], i) for i, row in enumerate(vector_rows)])
        connection.commit()
        connection.close()
        connection = None
    except Exception as exc:
        if connection is not None:
            connection.close()
        if database.exists(): database.unlink()
        repair = {"status": "failed", "failure_stage": "materialization", "repair_manifest": [{"issue": str(exc)}]}
        (output_root / "repair_manifest.json").write_text(json.dumps(repair, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
        raise
    catalog = _catalog(connection_path=database, fields=config.semantic_identifier_fields, objects=objects)
    (staging / "capability_catalog.json").write_text(json.dumps(catalog, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    manifest = {"status": "published", "schema_version": 1, "build_configuration": {"semantic_identifier_fields": list(config.semantic_identifier_fields), "excluded_folders": list(config.excluded_folders), "uuid_field": config.uuid_field}, "parser": {"name": "markdown-it-py", "version": config.parser_version}, "embedding": provider.model_identity, "artifacts": {name: name for name in ("substrate.sqlite3", "vectors.npy", "capability_catalog.json", "manifest.json")}, "object_count": len(objects), "region_count": sum(len(obj.regions) for obj in objects.values()), "unit_count": sum(len(obj.units) for obj in objects.values()), "vector_count": len(all_vectors)}
    (staging / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    for name in ("substrate.sqlite3", "vectors.npy", "capability_catalog.json", "manifest.json"):
        staging.joinpath(name).replace(output_root / name)
    staging.rmdir()
    return manifest


def _segments(text: str, max_chars: int) -> list[str]:
    if len(text) <= max_chars:
        return [text]
    segments: list[str] = []
    start = 0
    while start < len(text):
        end = min(len(text), start + max_chars)
        if end < len(text):
            boundary = max(text.rfind(". ", start, end), text.rfind(" ", start, end))
            if boundary > start:
                end = boundary + (2 if text[boundary:boundary + 2] == ". " else 1)
        segments.append(text[start:end])
        start = end
    return segments


def _catalog(*, connection_path: Path, fields: tuple[str, ...], objects: dict[str, _Object]) -> dict[str, Any]:
    connection = sqlite3.connect(connection_path)
    field_values: dict[str, set[str]] = {field: set() for field in fields}
    for row in connection.execute("SELECT field_name, authored_value FROM exact_index"):
        if row[0] in field_values and row[1] is not None: field_values[row[0]].add(str(row[1]))
    relations = sorted({str(row[0]) for row in connection.execute("SELECT DISTINCT relation_name FROM relations")})
    connection.close()
    field_matrix = [
        {"field_class": "parsed_text", "values": "free text from semantic units", "exact": True, "lexical": True, "vector": True},
        *[
            {"field_class": "admitted semantic identifier", "field_name": field, "values": sorted(field_values[field]), "exact": True, "lexical": True, "vector": False}
            for field in fields
        ],
        {"field_class": "semantic region", "values": sorted({region["heading"] for obj in objects.values() for region in obj.regions}), "exact": True, "lexical": True, "vector": False},
        {"field_class": "semantic path component", "values": sorted({component for obj in objects.values() for component in obj.path_components}), "exact": True, "lexical": True, "vector": False},
        {"field_class": "raw_markdown", "values": "authored Markdown of semantic units", "exact": True, "lexical": False, "vector": False},
    ]
    relation_grammar = [
        {"relation_class": "authored", "relation_name": name, "source": "semantic_unit", "target": "semantic_object / semantic_region"}
        for name in relations
    ]
    relation_grammar.extend(
        {"relation_class": "structural", "relation_name": name, "source": source, "target": target}
        for name, source, target in (("contains_scope", "scope", "scope"), ("contains_object", "scope", "semantic_object"), ("contains_region", "semantic_object / semantic_region", "semantic_region"), ("contains_unit", "semantic_object / semantic_region", "semantic_unit"))
    )
    return {"fields": field_matrix, "relations": relation_grammar, "operations": {"exact": ["equals"], "lexical": ["terms", "phrase"], "vector": ["semantic similarity"], "graph": ["relation traversal"]}}
