from __future__ import annotations

import json
import re
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from .config import RuntimeConfig, load_runtime_config
from .embeddings import EmbeddingBackend, resolve_embedding_backend
from .hashing import sha256_json, sha256_text


HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*$")
INLINE_LABEL_RE = re.compile(r"^(?P<label>[A-Za-z0-9][A-Za-z0-9/&()'., \-]{0,80}):(?:\s*(?P<remainder>.*))?$")
LIST_ITEM_RE = re.compile(r"^\s*(?:[-*+]|\d+\.)\s+")
THEMATIC_BREAK_RE = re.compile(r"^\s*(?:---|\*\*\*|___)\s*$")
WIKILINK_RE = re.compile(r"\[\[([^\]]+)\]\]")


@dataclass(frozen=True)
class IngestSourceRoot:
    label: str
    path: Path


@dataclass(frozen=True)
class IngestPaths:
    data_root: Path
    ingest_root: Path
    database_path: Path
    manifests_root: Path
    latest_manifest_path: Path


@dataclass(frozen=True)
class SectionContext:
    section_id: str
    label: str
    kind: str
    path_labels: tuple[str, ...]
    occurrence: int
    heading_level: int | None


@dataclass(frozen=True)
class ChunkRecord:
    chunk_id: str
    note_id: str
    source_root_label: str
    source_root_path: str
    relative_path: str
    note_path: str
    note_title: str
    section_id: str
    section_label: str
    section_kind: str
    section_path: tuple[str, ...]
    section_occurrence: int
    heading_level: int | None
    paragraph_ordinal: int
    paragraph_text: str
    chunk_hash: str


@dataclass(frozen=True)
class NoteRecord:
    note_id: str
    source_root_label: str
    source_root_path: str
    relative_path: str
    note_path: str
    note_title: str
    frontmatter: dict[str, Any]
    note_hash: str
    tag_values: tuple[str, ...]
    wikilink_targets: tuple[dict[str, Any], ...]
    chunks: tuple[ChunkRecord, ...]


@dataclass(frozen=True)
class IngestRunResult:
    run_id: str
    generated_at: str
    repo_root: Path
    data_root: Path
    database_path: Path
    manifest_path: Path
    source_roots: tuple[IngestSourceRoot, ...]
    note_count: int
    chunk_count: int
    inserted_chunks: int
    updated_chunks: int
    unchanged_chunks: int
    deleted_chunks: int
    deleted_notes: int


@dataclass(frozen=True)
class _Block:
    kind: str
    text: str
    heading_level: int | None = None


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _resolve_runtime_storage_path(data_root: Path, raw_path: Path) -> Path:
    if raw_path.is_absolute():
        return raw_path.resolve()
    return (data_root / raw_path).resolve()


def create_ingest_paths(data_root: Path, *, config: RuntimeConfig) -> IngestPaths:
    ingest_root = _resolve_runtime_storage_path(data_root, config.storage_ingestion_root)
    manifests_root = _resolve_runtime_storage_path(data_root, config.storage_ingestion_manifests_root)
    ingest_root.mkdir(parents=True, exist_ok=True)
    manifests_root.mkdir(parents=True, exist_ok=True)
    return IngestPaths(
        data_root=data_root,
        ingest_root=ingest_root,
        database_path=ingest_root / config.storage_ingestion_database_filename,
        manifests_root=manifests_root,
        latest_manifest_path=manifests_root / config.storage_latest_ingest_manifest_filename,
    )


def build_default_source_roots(repo_root: Path, config: RuntimeConfig | None = None) -> tuple[IngestSourceRoot, ...]:
    resolved_config = config or load_runtime_config(repo_root=repo_root)
    return tuple(IngestSourceRoot(label=root.label, path=root.path) for root in resolved_config.corpus_roots)


def parse_source_root_argument(raw: str, repo_root: Path) -> IngestSourceRoot:
    if "=" not in raw:
        raise ValueError(f"Expected source root in label=path form, got: {raw}")
    label, raw_path = raw.split("=", 1)
    label = label.strip()
    if not label:
        raise ValueError(f"Source root label is required in: {raw}")
    resolved_path = Path(raw_path.strip())
    if not resolved_path.is_absolute():
        resolved_path = (repo_root / resolved_path).resolve()
    else:
        resolved_path = resolved_path.resolve()
    return IngestSourceRoot(label=label, path=resolved_path)


def run_ingest(
    *,
    repo_root: Path,
    data_root: Path,
    source_roots: tuple[IngestSourceRoot, ...] | None = None,
    config: RuntimeConfig | None = None,
    embedding_backend: EmbeddingBackend | None = None,
) -> IngestRunResult:
    resolved_repo_root = repo_root.resolve()
    resolved_data_root = data_root.resolve()
    resolved_data_root.mkdir(parents=True, exist_ok=True)
    resolved_config = config or load_runtime_config(repo_root=resolved_repo_root)
    resolved_source_roots = source_roots or build_default_source_roots(resolved_repo_root, config=resolved_config)
    for source_root in resolved_source_roots:
        if not source_root.path.exists():
            raise FileNotFoundError(f"Source root does not exist: {source_root.path}")

    generated_at = _utc_now()
    run_id = f"ingest-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"
    ingest_paths = create_ingest_paths(resolved_data_root, config=resolved_config)
    note_records = tuple(_discover_and_parse_notes(resolved_source_roots))
    resolved_embedding_backend = embedding_backend or resolve_embedding_backend(resolved_config)

    connection = sqlite3.connect(ingest_paths.database_path)
    try:
        connection.row_factory = sqlite3.Row
        _initialize_schema(connection, config=resolved_config)
        counts = _materialize_records(
            connection=connection,
            note_records=note_records,
            source_roots=resolved_source_roots,
            run_id=run_id,
            generated_at=generated_at,
            config=resolved_config,
            embedding_backend=resolved_embedding_backend,
        )
    finally:
        connection.close()

    manifest = _build_manifest(
        run_id=run_id,
        generated_at=generated_at,
        repo_root=resolved_repo_root,
        data_root=resolved_data_root,
        database_path=ingest_paths.database_path,
        source_roots=resolved_source_roots,
        note_records=note_records,
        counts=counts,
    )
    manifest_path = ingest_paths.manifests_root / f"{run_id}.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    ingest_paths.latest_manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )

    return IngestRunResult(
        run_id=run_id,
        generated_at=generated_at,
        repo_root=resolved_repo_root,
        data_root=resolved_data_root,
        database_path=ingest_paths.database_path,
        manifest_path=manifest_path,
        source_roots=resolved_source_roots,
        note_count=len(note_records),
        chunk_count=sum(len(note_record.chunks) for note_record in note_records),
        inserted_chunks=counts["inserted_chunks"],
        updated_chunks=counts["updated_chunks"],
        unchanged_chunks=counts["unchanged_chunks"],
        deleted_chunks=counts["deleted_chunks"],
        deleted_notes=counts["deleted_notes"],
    )


def _discover_and_parse_notes(source_roots: tuple[IngestSourceRoot, ...]) -> list[NoteRecord]:
    note_records: list[NoteRecord] = []
    for source_root in source_roots:
        for note_path in sorted(source_root.path.rglob("*.md")):
            relative_path = note_path.relative_to(source_root.path).as_posix()
            note_records.append(_parse_markdown_note(source_root=source_root, note_path=note_path, relative_path=relative_path))
    return note_records


def _parse_markdown_note(*, source_root: IngestSourceRoot, note_path: Path, relative_path: str) -> NoteRecord:
    raw_text = note_path.read_text(encoding="utf-8")
    frontmatter_text, body_text = _split_frontmatter(raw_text)
    frontmatter = _parse_frontmatter(frontmatter_text)
    note_title = _derive_note_title(frontmatter=frontmatter, note_path=note_path)
    tag_values = _extract_tag_values(frontmatter)
    wikilink_targets = _extract_wikilink_targets(body_text)
    blocks = _tokenize_markdown_blocks(body_text)
    chunks = _extract_chunks(
        blocks=blocks,
        note_id=_build_note_id(source_root.label, relative_path),
        source_root=source_root,
        relative_path=relative_path,
        note_path=note_path,
        note_title=note_title,
        frontmatter=frontmatter,
    )
    return NoteRecord(
        note_id=_build_note_id(source_root.label, relative_path),
        source_root_label=source_root.label,
        source_root_path=str(source_root.path),
        relative_path=relative_path,
        note_path=str(note_path),
        note_title=note_title,
        frontmatter=frontmatter,
        note_hash=sha256_text(raw_text),
        tag_values=tag_values,
        wikilink_targets=wikilink_targets,
        chunks=tuple(chunks),
    )


def _split_frontmatter(raw_text: str) -> tuple[str, str]:
    if not raw_text.startswith("---"):
        return "", raw_text
    lines = raw_text.splitlines()
    if not lines or lines[0].strip() != "---":
        return "", raw_text
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            frontmatter = "\n".join(lines[1:index])
            body = "\n".join(lines[index + 1 :])
            return frontmatter, body
    return "", raw_text


def _parse_frontmatter(frontmatter_text: str) -> dict[str, Any]:
    frontmatter: dict[str, Any] = {}
    current_key: str | None = None
    for raw_line in frontmatter_text.splitlines():
        stripped = raw_line.strip()
        if not stripped:
            continue
        if stripped.startswith("- ") and current_key is not None:
            existing = frontmatter.setdefault(current_key, [])
            if isinstance(existing, list):
                existing.append(_strip_wrapping_quotes(stripped[2:].strip()))
            continue
        if ":" not in raw_line:
            current_key = None
            continue
        key, value = raw_line.split(":", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            current_key = None
            continue
        if value:
            frontmatter[key] = _strip_wrapping_quotes(value)
            current_key = key
        else:
            frontmatter[key] = []
            current_key = key
    return frontmatter


def _strip_wrapping_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _derive_note_title(*, frontmatter: dict[str, Any], note_path: Path) -> str:
    journal_entry_date = frontmatter.get("journal_entry_date")
    if isinstance(journal_entry_date, str):
        try:
            return date.fromisoformat(journal_entry_date).strftime("%B %d, %Y")
        except ValueError:
            pass
    return note_path.stem.replace("_", " ")


def _extract_tag_values(frontmatter: dict[str, Any]) -> tuple[str, ...]:
    raw_tags = frontmatter.get("tags")
    values: list[str] = []
    if isinstance(raw_tags, list):
        candidates = raw_tags
    elif isinstance(raw_tags, str):
        candidates = [raw_tags]
    else:
        candidates = []
    for candidate in candidates:
        if not isinstance(candidate, str):
            continue
        normalized = _normalize_inline_whitespace(candidate).lstrip("#")
        if normalized and normalized not in values:
            values.append(normalized)
    return tuple(values)


def _extract_wikilink_targets(body_text: str) -> tuple[dict[str, Any], ...]:
    targets: list[dict[str, Any]] = []
    for match in WIKILINK_RE.findall(body_text):
        parsed = _parse_wikilink_target(match)
        if parsed is None:
            continue
        if parsed not in targets:
            targets.append(parsed)
    return tuple(targets)


def _parse_wikilink_target(raw_link_text: str) -> dict[str, Any] | None:
    raw_text = _normalize_inline_whitespace(raw_link_text)
    if not raw_text:
        return None
    alias_text: str | None = None
    if "|" in raw_text:
        target_part, alias_part = raw_text.split("|", 1)
        alias_text = _normalize_inline_whitespace(alias_part) or None
    else:
        target_part = raw_text
    target_part = _normalize_inline_whitespace(target_part)
    if not target_part:
        return None
    heading_text: str | None = None
    if "#" in target_part:
        target_note_text, heading_part = target_part.split("#", 1)
        heading_text = _normalize_inline_whitespace(heading_part) or None
    else:
        target_note_text = target_part
    target_note_text = _strip_optional_md_suffix(_normalize_inline_whitespace(target_note_text))
    if not target_note_text:
        return None
    return {
        "raw_wikilink_text": raw_text,
        "target_raw": target_part,
        "target_note": target_note_text,
        "target_heading": heading_text,
        "alias": alias_text,
    }


def _strip_optional_md_suffix(value: str) -> str:
    normalized = _normalize_inline_whitespace(value)
    if normalized.lower().endswith(".md"):
        return normalized[:-3].rstrip()
    return normalized


def _tokenize_markdown_blocks(body_text: str) -> list[_Block]:
    blocks: list[_Block] = []
    current_lines: list[str] = []

    def flush_paragraph(kind: str = "paragraph") -> None:
        nonlocal current_lines
        if not current_lines:
            return
        text = _normalize_inline_whitespace(" ".join(line.strip() for line in current_lines))
        if text:
            blocks.append(_Block(kind=kind, text=text))
        current_lines = []

    lines = body_text.splitlines()
    index = 0
    while index < len(lines):
        raw_line = lines[index]
        stripped = raw_line.strip()
        heading_match = HEADING_RE.match(stripped)
        if heading_match:
            flush_paragraph()
            blocks.append(
                _Block(
                    kind="heading",
                    text=_normalize_section_label(heading_match.group(2)),
                    heading_level=len(heading_match.group(1)),
                )
            )
            index += 1
            continue
        if not stripped:
            flush_paragraph()
            index += 1
            continue
        if THEMATIC_BREAK_RE.match(stripped):
            flush_paragraph()
            index += 1
            continue
        if LIST_ITEM_RE.match(stripped):
            flush_paragraph()
            item_lines = [stripped]
            lookahead = index + 1
            while lookahead < len(lines):
                next_line = lines[lookahead]
                next_stripped = next_line.strip()
                if not next_stripped or HEADING_RE.match(next_stripped) or LIST_ITEM_RE.match(next_stripped):
                    break
                item_lines.append(next_stripped)
                lookahead += 1
            blocks.append(_Block(kind="list_item", text=_normalize_inline_whitespace(" ".join(item_lines))))
            index = lookahead
            continue
        current_lines.append(raw_line)
        index += 1

    flush_paragraph()
    return blocks


def _extract_chunks(
    *,
    blocks: list[_Block],
    note_id: str,
    source_root: IngestSourceRoot,
    relative_path: str,
    note_path: Path,
    note_title: str,
    frontmatter: dict[str, Any],
) -> list[ChunkRecord]:
    chunks: list[ChunkRecord] = []
    current_heading_path: list[tuple[int, str]] = []
    section_counters: dict[tuple[str, tuple[str, ...]], int] = {}
    paragraph_ordinals: dict[str, int] = {}
    current_section: SectionContext | None = None
    first_heading_pending = True

    for block in blocks:
        if block.kind == "heading":
            label = block.text
            if first_heading_pending and _is_note_title_heading(label=label, note_title=note_title, frontmatter=frontmatter):
                first_heading_pending = False
                current_section = None
                continue
            first_heading_pending = False
            level = block.heading_level or 1
            current_heading_path = [entry for entry in current_heading_path if entry[0] < level]
            current_heading_path.append((level, label))
            current_section = _create_section_context(
                note_id=note_id,
                kind="heading",
                label=label,
                path_labels=tuple(entry[1] for entry in current_heading_path),
                heading_level=level,
                section_counters=section_counters,
            )
            continue

        paragraph_text = block.text
        if block.kind == "paragraph":
            inline_label_match = INLINE_LABEL_RE.match(paragraph_text)
            if inline_label_match:
                inline_label = _normalize_section_label(inline_label_match.group("label"))
                current_section = _create_section_context(
                    note_id=note_id,
                    kind="inline_label",
                    label=inline_label,
                    path_labels=tuple([entry[1] for entry in current_heading_path] + [inline_label]),
                    heading_level=current_heading_path[-1][0] if current_heading_path else None,
                    section_counters=section_counters,
                )
                paragraph_text = _normalize_inline_whitespace(inline_label_match.group("remainder") or "")
                if not paragraph_text:
                    continue

        if current_section is None:
            fallback_path = tuple(entry[1] for entry in current_heading_path) or (note_title,)
            current_section = _create_section_context(
                note_id=note_id,
                kind="body",
                label=fallback_path[-1],
                path_labels=fallback_path,
                heading_level=current_heading_path[-1][0] if current_heading_path else None,
                section_counters=section_counters,
            )

        paragraph_ordinal = paragraph_ordinals.get(current_section.section_id, 0) + 1
        paragraph_ordinals[current_section.section_id] = paragraph_ordinal
        chunks.append(
            ChunkRecord(
                chunk_id=f"{note_id}::{current_section.section_id}::p{paragraph_ordinal:04d}",
                note_id=note_id,
                source_root_label=source_root.label,
                source_root_path=str(source_root.path),
                relative_path=relative_path,
                note_path=str(note_path),
                note_title=note_title,
                section_id=current_section.section_id,
                section_label=current_section.label,
                section_kind=current_section.kind,
                section_path=current_section.path_labels,
                section_occurrence=current_section.occurrence,
                heading_level=current_section.heading_level,
                paragraph_ordinal=paragraph_ordinal,
                paragraph_text=paragraph_text,
                chunk_hash=sha256_text(paragraph_text),
            )
        )

    return chunks


def _create_section_context(
    *,
    note_id: str,
    kind: str,
    label: str,
    path_labels: tuple[str, ...],
    heading_level: int | None,
    section_counters: dict[tuple[str, tuple[str, ...]], int],
) -> SectionContext:
    normalized_path = tuple(_normalize_section_label(path_label) for path_label in path_labels)
    counter_key = (kind, normalized_path)
    occurrence = section_counters.get(counter_key, 0) + 1
    section_counters[counter_key] = occurrence
    section_key = {
        "note_id": note_id,
        "kind": kind,
        "path": normalized_path,
        "occurrence": occurrence,
    }
    return SectionContext(
        section_id=f"section-{sha256_json(section_key)[:16]}",
        label=_normalize_section_label(label),
        kind=kind,
        path_labels=normalized_path,
        occurrence=occurrence,
        heading_level=heading_level,
    )


def _build_note_id(source_root_label: str, relative_path: str) -> str:
    return f"{source_root_label}::{relative_path}"


def _is_note_title_heading(*, label: str, note_title: str, frontmatter: dict[str, Any]) -> bool:
    if label != note_title:
        return False
    journal_entry_date = frontmatter.get("journal_entry_date")
    if not isinstance(journal_entry_date, str):
        return False
    try:
        expected_title = date.fromisoformat(journal_entry_date).strftime("%B %d, %Y")
    except ValueError:
        return False
    return label == expected_title


def _normalize_section_label(value: str) -> str:
    normalized = _normalize_inline_whitespace(value)
    if normalized.endswith(":"):
        normalized = normalized[:-1].rstrip()
    return normalized


def _normalize_inline_whitespace(value: str) -> str:
    return " ".join(value.split())


def _initialize_schema(connection: sqlite3.Connection, *, config: RuntimeConfig) -> None:
    vector_table = config.vector_table
    graph_nodes_table = config.graph_nodes_table
    graph_edges_table = config.graph_edges_table
    connection.executescript(
        f"""
        CREATE TABLE IF NOT EXISTS ingest_runs (
            run_id TEXT PRIMARY KEY,
            generated_at TEXT NOT NULL,
            source_roots_json TEXT NOT NULL,
            note_count INTEGER NOT NULL,
            chunk_count INTEGER NOT NULL,
            inserted_chunks INTEGER NOT NULL,
            updated_chunks INTEGER NOT NULL,
            unchanged_chunks INTEGER NOT NULL,
            deleted_chunks INTEGER NOT NULL,
            deleted_notes INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS notes (
            note_id TEXT PRIMARY KEY,
            source_root_label TEXT NOT NULL,
            source_root_path TEXT NOT NULL,
            relative_path TEXT NOT NULL,
            note_path TEXT NOT NULL,
            note_title TEXT NOT NULL,
            frontmatter_json TEXT NOT NULL,
            note_hash TEXT NOT NULL,
            last_ingested_run_id TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS chunks (
            chunk_id TEXT PRIMARY KEY,
            note_id TEXT NOT NULL,
            source_root_label TEXT NOT NULL,
            source_root_path TEXT NOT NULL,
            relative_path TEXT NOT NULL,
            note_path TEXT NOT NULL,
            note_title TEXT NOT NULL,
            section_id TEXT NOT NULL,
            section_label TEXT NOT NULL,
            section_kind TEXT NOT NULL,
            section_path_json TEXT NOT NULL,
            section_occurrence INTEGER NOT NULL,
            heading_level INTEGER,
            paragraph_ordinal INTEGER NOT NULL,
            paragraph_text TEXT NOT NULL,
            chunk_hash TEXT NOT NULL,
            last_ingested_run_id TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_notes_source_root_label ON notes(source_root_label);
        CREATE INDEX IF NOT EXISTS idx_chunks_note_id ON chunks(note_id);
        CREATE INDEX IF NOT EXISTS idx_chunks_source_root_label ON chunks(source_root_label);

        CREATE TABLE IF NOT EXISTS {vector_table} (
            chunk_id TEXT PRIMARY KEY,
            vector_json TEXT NOT NULL,
            vector_dimensions INTEGER NOT NULL,
            embedding_provider TEXT NOT NULL,
            embedding_model TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            last_indexed_run_id TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS {graph_nodes_table} (
            node_id TEXT PRIMARY KEY,
            node_type TEXT NOT NULL,
            label TEXT NOT NULL,
            ref_id TEXT,
            metadata_json TEXT NOT NULL,
            last_ingested_run_id TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS {graph_edges_table} (
            edge_id TEXT PRIMARY KEY,
            source_node_id TEXT NOT NULL,
            target_node_id TEXT NOT NULL,
            edge_type TEXT NOT NULL,
            metadata_json TEXT NOT NULL,
            last_ingested_run_id TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_{vector_table}_content_hash ON {vector_table}(content_hash);
        CREATE INDEX IF NOT EXISTS idx_{graph_nodes_table}_node_type ON {graph_nodes_table}(node_type);
        CREATE INDEX IF NOT EXISTS idx_{graph_edges_table}_source ON {graph_edges_table}(source_node_id);
        CREATE INDEX IF NOT EXISTS idx_{graph_edges_table}_target ON {graph_edges_table}(target_node_id);
        CREATE INDEX IF NOT EXISTS idx_{graph_edges_table}_type ON {graph_edges_table}(edge_type);
        """
    )
    connection.commit()


def _materialize_records(
    *,
    connection: sqlite3.Connection,
    note_records: tuple[NoteRecord, ...],
    source_roots: tuple[IngestSourceRoot, ...],
    run_id: str,
    generated_at: str,
    config: RuntimeConfig,
    embedding_backend: EmbeddingBackend | None,
) -> dict[str, int]:
    counts = {
        "inserted_chunks": 0,
        "updated_chunks": 0,
        "unchanged_chunks": 0,
        "deleted_chunks": 0,
        "deleted_notes": 0,
    }
    processed_note_ids = {note_record.note_id for note_record in note_records}
    processed_chunk_ids = {chunk.chunk_id for note_record in note_records for chunk in note_record.chunks}

    for note_record in note_records:
        _upsert_note(connection=connection, note_record=note_record, run_id=run_id, generated_at=generated_at)
        for chunk in note_record.chunks:
            classification = _upsert_chunk(connection=connection, chunk=chunk, run_id=run_id, generated_at=generated_at)
            counts[f"{classification}_chunks"] += 1

    source_root_labels = tuple(source_root.label for source_root in source_roots)
    counts["deleted_chunks"] += _delete_absent_chunks(
        connection=connection,
        source_root_labels=source_root_labels,
        processed_chunk_ids=processed_chunk_ids,
    )
    counts["deleted_notes"] += _delete_absent_notes(
        connection=connection,
        source_root_labels=source_root_labels,
        processed_note_ids=processed_note_ids,
    )
    _rebuild_graph_layer(
        connection=connection,
        note_records=note_records,
        run_id=run_id,
        generated_at=generated_at,
        config=config,
    )
    _refresh_chunk_vectors(
        connection=connection,
        note_records=note_records,
        run_id=run_id,
        generated_at=generated_at,
        config=config,
        embedding_backend=embedding_backend,
    )

    connection.execute(
        """
        INSERT OR REPLACE INTO ingest_runs (
            run_id,
            generated_at,
            source_roots_json,
            note_count,
            chunk_count,
            inserted_chunks,
            updated_chunks,
            unchanged_chunks,
            deleted_chunks,
            deleted_notes
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            generated_at,
            json.dumps([{"label": root.label, "path": str(root.path)} for root in source_roots], ensure_ascii=True),
            len(note_records),
            sum(len(note_record.chunks) for note_record in note_records),
            counts["inserted_chunks"],
            counts["updated_chunks"],
            counts["unchanged_chunks"],
            counts["deleted_chunks"],
            counts["deleted_notes"],
        ),
    )
    connection.commit()
    return counts


def _upsert_note(
    *,
    connection: sqlite3.Connection,
    note_record: NoteRecord,
    run_id: str,
    generated_at: str,
) -> None:
    connection.execute(
        """
        INSERT INTO notes (
            note_id,
            source_root_label,
            source_root_path,
            relative_path,
            note_path,
            note_title,
            frontmatter_json,
            note_hash,
            last_ingested_run_id,
            updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(note_id) DO UPDATE SET
            source_root_label=excluded.source_root_label,
            source_root_path=excluded.source_root_path,
            relative_path=excluded.relative_path,
            note_path=excluded.note_path,
            note_title=excluded.note_title,
            frontmatter_json=excluded.frontmatter_json,
            note_hash=excluded.note_hash,
            last_ingested_run_id=excluded.last_ingested_run_id,
            updated_at=excluded.updated_at
        """,
        (
            note_record.note_id,
            note_record.source_root_label,
            note_record.source_root_path,
            note_record.relative_path,
            note_record.note_path,
            note_record.note_title,
            json.dumps(note_record.frontmatter, ensure_ascii=True, sort_keys=True),
            note_record.note_hash,
            run_id,
            generated_at,
        ),
    )


def _upsert_chunk(
    *,
    connection: sqlite3.Connection,
    chunk: ChunkRecord,
    run_id: str,
    generated_at: str,
) -> str:
    existing_row = connection.execute(
        "SELECT chunk_hash, paragraph_text FROM chunks WHERE chunk_id = ?",
        (chunk.chunk_id,),
    ).fetchone()
    if existing_row is None:
        classification = "inserted"
    elif existing_row["chunk_hash"] == chunk.chunk_hash and existing_row["paragraph_text"] == chunk.paragraph_text:
        classification = "unchanged"
    else:
        classification = "updated"

    connection.execute(
        """
        INSERT INTO chunks (
            chunk_id,
            note_id,
            source_root_label,
            source_root_path,
            relative_path,
            note_path,
            note_title,
            section_id,
            section_label,
            section_kind,
            section_path_json,
            section_occurrence,
            heading_level,
            paragraph_ordinal,
            paragraph_text,
            chunk_hash,
            last_ingested_run_id,
            updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(chunk_id) DO UPDATE SET
            note_id=excluded.note_id,
            source_root_label=excluded.source_root_label,
            source_root_path=excluded.source_root_path,
            relative_path=excluded.relative_path,
            note_path=excluded.note_path,
            note_title=excluded.note_title,
            section_id=excluded.section_id,
            section_label=excluded.section_label,
            section_kind=excluded.section_kind,
            section_path_json=excluded.section_path_json,
            section_occurrence=excluded.section_occurrence,
            heading_level=excluded.heading_level,
            paragraph_ordinal=excluded.paragraph_ordinal,
            paragraph_text=excluded.paragraph_text,
            chunk_hash=excluded.chunk_hash,
            last_ingested_run_id=excluded.last_ingested_run_id,
            updated_at=excluded.updated_at
        """,
        (
            chunk.chunk_id,
            chunk.note_id,
            chunk.source_root_label,
            chunk.source_root_path,
            chunk.relative_path,
            chunk.note_path,
            chunk.note_title,
            chunk.section_id,
            chunk.section_label,
            chunk.section_kind,
            json.dumps(chunk.section_path, ensure_ascii=True),
            chunk.section_occurrence,
            chunk.heading_level,
            chunk.paragraph_ordinal,
            chunk.paragraph_text,
            chunk.chunk_hash,
            run_id,
            generated_at,
        ),
    )
    return classification


def _delete_absent_chunks(
    *,
    connection: sqlite3.Connection,
    source_root_labels: tuple[str, ...],
    processed_chunk_ids: set[str],
) -> int:
    if not source_root_labels:
        return 0
    existing_ids = _select_existing_ids(
        connection=connection,
        table_name="chunks",
        id_column="chunk_id",
        source_root_labels=source_root_labels,
    )
    chunk_ids_to_delete = sorted(existing_ids - processed_chunk_ids)
    if not chunk_ids_to_delete:
        return 0
    _delete_ids(connection=connection, table_name="chunks", id_column="chunk_id", ids=chunk_ids_to_delete)
    return len(chunk_ids_to_delete)


def _delete_absent_notes(
    *,
    connection: sqlite3.Connection,
    source_root_labels: tuple[str, ...],
    processed_note_ids: set[str],
) -> int:
    if not source_root_labels:
        return 0
    existing_ids = _select_existing_ids(
        connection=connection,
        table_name="notes",
        id_column="note_id",
        source_root_labels=source_root_labels,
    )
    note_ids_to_delete = sorted(existing_ids - processed_note_ids)
    if not note_ids_to_delete:
        return 0
    chunk_rows = connection.execute(
        f"SELECT chunk_id FROM chunks WHERE note_id IN ({','.join('?' for _ in note_ids_to_delete)})",
        tuple(note_ids_to_delete),
    ).fetchall()
    if chunk_rows:
        _delete_ids(
            connection=connection,
            table_name="chunks",
            id_column="chunk_id",
            ids=[row["chunk_id"] for row in chunk_rows],
        )
    _delete_ids(connection=connection, table_name="notes", id_column="note_id", ids=note_ids_to_delete)
    return len(note_ids_to_delete)


def _select_existing_ids(
    *,
    connection: sqlite3.Connection,
    table_name: str,
    id_column: str,
    source_root_labels: tuple[str, ...],
) -> set[str]:
    placeholders = ",".join("?" for _ in source_root_labels)
    rows = connection.execute(
        f"SELECT {id_column} FROM {table_name} WHERE source_root_label IN ({placeholders})",
        source_root_labels,
    ).fetchall()
    return {str(row[id_column]) for row in rows}


def _delete_ids(
    *,
    connection: sqlite3.Connection,
    table_name: str,
    id_column: str,
    ids: list[str],
) -> None:
    if not ids:
        return
    placeholders = ",".join("?" for _ in ids)
    connection.execute(
        f"DELETE FROM {table_name} WHERE {id_column} IN ({placeholders})",
        tuple(ids),
    )


def _rebuild_graph_layer(
    *,
    connection: sqlite3.Connection,
    note_records: tuple[NoteRecord, ...],
    run_id: str,
    generated_at: str,
    config: RuntimeConfig,
) -> None:
    graph_nodes_table = config.graph_nodes_table
    graph_edges_table = config.graph_edges_table
    connection.execute(f"DELETE FROM {graph_edges_table}")
    connection.execute(f"DELETE FROM {graph_nodes_table}")

    note_lookup: dict[str, str] = {}
    for note_record in note_records:
        for lookup_key in _note_lookup_keys(note_record):
            if lookup_key not in note_lookup:
                note_lookup[lookup_key] = note_record.note_id

    node_rows: list[tuple[Any, ...]] = []
    edge_rows: list[tuple[Any, ...]] = []
    seen_tag_nodes: set[str] = set()
    for note_record in note_records:
        note_node_id = _note_node_id(note_record.note_id)
        node_rows.append(
            (
                note_node_id,
                "note",
                note_record.note_title,
                note_record.note_id,
                json.dumps(
                    {
                        "relative_path": note_record.relative_path,
                        "source_root_label": note_record.source_root_label,
                        "tags": list(note_record.tag_values),
                    },
                    ensure_ascii=True,
                    sort_keys=True,
                ),
                run_id,
                generated_at,
            )
        )
        for chunk in note_record.chunks:
            chunk_node_id = _chunk_node_id(chunk.chunk_id)
            node_rows.append(
                (
                    chunk_node_id,
                    "chunk",
                    chunk.section_label,
                    chunk.chunk_id,
                    json.dumps(
                        {
                            "note_id": chunk.note_id,
                            "relative_path": chunk.relative_path,
                            "paragraph_ordinal": chunk.paragraph_ordinal,
                            "source_root_label": chunk.source_root_label,
                        },
                        ensure_ascii=True,
                        sort_keys=True,
                    ),
                    run_id,
                    generated_at,
                )
            )
            edge_rows.extend(
                [
                    (
                        _edge_id("note_contains_chunk", note_node_id, chunk_node_id),
                        note_node_id,
                        chunk_node_id,
                        "note_contains_chunk",
                        json.dumps({"note_id": note_record.note_id, "chunk_id": chunk.chunk_id}, ensure_ascii=True, sort_keys=True),
                        run_id,
                        generated_at,
                    ),
                    (
                        _edge_id("chunk_derived_from_note", chunk_node_id, note_node_id),
                        chunk_node_id,
                        note_node_id,
                        "chunk_derived_from_note",
                        json.dumps({"note_id": note_record.note_id, "chunk_id": chunk.chunk_id}, ensure_ascii=True, sort_keys=True),
                        run_id,
                        generated_at,
                    ),
                ]
            )

        for tag_value in note_record.tag_values:
            tag_node_id = _tag_node_id(tag_value)
            if tag_node_id not in seen_tag_nodes:
                node_rows.append(
                    (
                        tag_node_id,
                        "tag",
                        tag_value,
                        tag_value,
                        json.dumps({"tag": tag_value}, ensure_ascii=True, sort_keys=True),
                        run_id,
                        generated_at,
                    )
                )
                seen_tag_nodes.add(tag_node_id)
            edge_rows.append(
                (
                    _edge_id("note_has_tag", note_node_id, tag_node_id),
                    note_node_id,
                    tag_node_id,
                    "note_has_tag",
                    json.dumps({"tag": tag_value}, ensure_ascii=True, sort_keys=True),
                    run_id,
                    generated_at,
                )
            )

        for target_record in note_record.wikilink_targets:
            target_note = str(target_record.get("target_note") or "").strip()
            if not target_note:
                continue
            resolved_note_id = note_lookup.get(_normalize_note_reference(target_note))
            if resolved_note_id is None:
                continue
            target_node_id = _note_node_id(resolved_note_id)
            edge_rows.append(
                (
                    _edge_id("note_links_note", note_node_id, target_node_id, str(target_record.get("raw_wikilink_text") or "")),
                    note_node_id,
                    target_node_id,
                    "note_links_note",
                    json.dumps(
                        {
                            "raw_wikilink_text": target_record.get("raw_wikilink_text"),
                            "target_raw": target_record.get("target_raw"),
                            "target_note": target_note,
                            "target_heading": target_record.get("target_heading"),
                            "alias": target_record.get("alias"),
                            "resolved": True,
                            "resolved_note_id": resolved_note_id,
                        },
                        ensure_ascii=True,
                        sort_keys=True,
                    ),
                    run_id,
                    generated_at,
                )
            )

    connection.executemany(
        f"INSERT INTO {graph_nodes_table} (node_id, node_type, label, ref_id, metadata_json, last_ingested_run_id, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        node_rows,
    )
    connection.executemany(
        f"INSERT INTO {graph_edges_table} (edge_id, source_node_id, target_node_id, edge_type, metadata_json, last_ingested_run_id, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        edge_rows,
    )


def _refresh_chunk_vectors(
    *,
    connection: sqlite3.Connection,
    note_records: tuple[NoteRecord, ...],
    run_id: str,
    generated_at: str,
    config: RuntimeConfig,
    embedding_backend: EmbeddingBackend | None,
) -> None:
    vector_table = config.vector_table
    processed_chunk_ids = [chunk.chunk_id for note_record in note_records for chunk in note_record.chunks]
    if not processed_chunk_ids:
        connection.execute(f"DELETE FROM {vector_table}")
        return

    placeholders = ",".join("?" for _ in processed_chunk_ids)
    existing_rows = connection.execute(
        f"SELECT chunk_id, content_hash FROM {vector_table} WHERE chunk_id IN ({placeholders})",
        tuple(processed_chunk_ids),
    ).fetchall()
    existing_hashes = {str(row["chunk_id"]): str(row["content_hash"]) for row in existing_rows}
    rows_to_index: list[ChunkRecord] = []
    for note_record in note_records:
        for chunk in note_record.chunks:
            if existing_hashes.get(chunk.chunk_id) != chunk.chunk_hash:
                rows_to_index.append(chunk)

    if embedding_backend is not None and rows_to_index:
        response = embedding_backend.embed_texts([_embedding_text_for_chunk(chunk) for chunk in rows_to_index])
        if response.status == "embedded" and response.vectors is not None and len(response.vectors) == len(rows_to_index):
            model_name = str(response.metadata.get("model") or "unknown")
            provider_name = str(response.metadata.get("backend_mode") or getattr(embedding_backend, "mode_name", "unknown"))
            connection.executemany(
                f"""
                INSERT INTO {vector_table} (
                    chunk_id,
                    vector_json,
                    vector_dimensions,
                    embedding_provider,
                    embedding_model,
                    content_hash,
                    last_indexed_run_id,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(chunk_id) DO UPDATE SET
                    vector_json=excluded.vector_json,
                    vector_dimensions=excluded.vector_dimensions,
                    embedding_provider=excluded.embedding_provider,
                    embedding_model=excluded.embedding_model,
                    content_hash=excluded.content_hash,
                    last_indexed_run_id=excluded.last_indexed_run_id,
                    updated_at=excluded.updated_at
                """,
                [
                    (
                        chunk.chunk_id,
                        json.dumps(vector, ensure_ascii=True),
                        len(vector),
                        provider_name,
                        model_name,
                        chunk.chunk_hash,
                        run_id,
                        generated_at,
                    )
                    for chunk, vector in zip(rows_to_index, response.vectors, strict=True)
                ],
            )
        else:
            placeholders = ",".join("?" for _ in rows_to_index)
            connection.execute(
                f"DELETE FROM {vector_table} WHERE chunk_id IN ({placeholders})",
                tuple(chunk.chunk_id for chunk in rows_to_index),
            )

    connection.execute(
        f"DELETE FROM {vector_table} WHERE chunk_id NOT IN ({placeholders})",
        tuple(processed_chunk_ids),
    )


def _embedding_text_for_chunk(chunk: ChunkRecord) -> str:
    return " | ".join(
        [
            chunk.note_title,
            chunk.relative_path,
            chunk.section_label,
            chunk.paragraph_text,
        ]
    )


def _note_node_id(note_id: str) -> str:
    return f"note::{note_id}"


def _chunk_node_id(chunk_id: str) -> str:
    return f"chunk::{chunk_id}"


def _tag_node_id(tag_value: str) -> str:
    return f"tag::{_normalize_note_reference(tag_value)}"


def _note_lookup_keys(note_record: NoteRecord) -> list[str]:
    keys: list[str] = []

    def add(value: str | None) -> None:
        if not value:
            return
        normalized = _normalize_note_reference(value)
        if normalized and normalized not in keys:
            keys.append(normalized)

    add(note_record.note_title)
    relative_path = _normalize_inline_whitespace(note_record.relative_path)
    add(relative_path)
    add(_strip_optional_md_suffix(relative_path))
    add(Path(note_record.relative_path).stem.replace("_", " "))
    add(Path(note_record.relative_path).with_suffix("").as_posix())
    return keys


def _normalize_note_reference(value: str) -> str:
    return _normalize_inline_whitespace(value).replace("_", " ").lower()


def _edge_id(edge_type: str, source_node_id: str, target_node_id: str, salt: str | None = None) -> str:
    payload = {"edge_type": edge_type, "source": source_node_id, "target": target_node_id, "salt": salt}
    return f"edge-{sha256_json(payload)[:16]}"


def _build_manifest(
    *,
    run_id: str,
    generated_at: str,
    repo_root: Path,
    data_root: Path,
    database_path: Path,
    source_roots: tuple[IngestSourceRoot, ...],
    note_records: tuple[NoteRecord, ...],
    counts: dict[str, int],
) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "generated_at": generated_at,
        "repo_root": str(repo_root),
        "data_root": str(data_root),
        "database_path": str(database_path),
        "source_roots": [{"label": root.label, "path": str(root.path)} for root in source_roots],
        "summary": {
            "note_count": len(note_records),
            "chunk_count": sum(len(note_record.chunks) for note_record in note_records),
            "inserted_chunks": counts["inserted_chunks"],
            "updated_chunks": counts["updated_chunks"],
            "unchanged_chunks": counts["unchanged_chunks"],
            "deleted_chunks": counts["deleted_chunks"],
            "deleted_notes": counts["deleted_notes"],
        },
        "notes": [
            {
                "note_id": note_record.note_id,
                "source_root_label": note_record.source_root_label,
                "source_root_path": note_record.source_root_path,
                "relative_path": note_record.relative_path,
                "note_path": note_record.note_path,
                "note_title": note_record.note_title,
                "note_hash": note_record.note_hash,
                "frontmatter": note_record.frontmatter,
                "tag_values": list(note_record.tag_values),
                "wikilink_targets": [dict(target_record) for target_record in note_record.wikilink_targets],
                "chunk_ids": [chunk.chunk_id for chunk in note_record.chunks],
            }
            for note_record in note_records
        ],
        "chunks": [
            {
                "chunk_id": chunk.chunk_id,
                "note_id": chunk.note_id,
                "source_root_label": chunk.source_root_label,
                "source_root_path": chunk.source_root_path,
                "relative_path": chunk.relative_path,
                "note_path": chunk.note_path,
                "note_title": chunk.note_title,
                "section_id": chunk.section_id,
                "section_label": chunk.section_label,
                "section_kind": chunk.section_kind,
                "section_path": list(chunk.section_path),
                "section_occurrence": chunk.section_occurrence,
                "heading_level": chunk.heading_level,
                "paragraph_ordinal": chunk.paragraph_ordinal,
                "paragraph_text": chunk.paragraph_text,
                "chunk_hash": chunk.chunk_hash,
            }
            for note_record in note_records
            for chunk in note_record.chunks
        ],
    }
