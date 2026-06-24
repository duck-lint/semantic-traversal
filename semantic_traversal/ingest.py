from __future__ import annotations

import json
import re
import sqlite3
import tempfile
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from .hashing import sha256_json, sha256_text


HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*$")
INLINE_LABEL_RE = re.compile(r"^(?P<label>[A-Za-z0-9][A-Za-z0-9/&()'., \-]{0,80}):(?:\s*(?P<remainder>.*))?$")
LIST_ITEM_RE = re.compile(r"^\s*(?:[-*+]|\d+\.)\s+")
THEMATIC_BREAK_RE = re.compile(r"^\s*(?:---|\*\*\*|___)\s*$")


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


def default_data_root() -> Path:
    return Path(tempfile.gettempdir()) / "semantic-traversal"


def create_ingest_paths(data_root: Path) -> IngestPaths:
    ingest_root = data_root / "ingestion"
    manifests_root = ingest_root / "manifests"
    ingest_root.mkdir(parents=True, exist_ok=True)
    manifests_root.mkdir(parents=True, exist_ok=True)
    return IngestPaths(
        data_root=data_root,
        ingest_root=ingest_root,
        database_path=ingest_root / "latent_space.sqlite3",
        manifests_root=manifests_root,
        latest_manifest_path=manifests_root / "latest.json",
    )


def build_default_source_roots(repo_root: Path) -> tuple[IngestSourceRoot, ...]:
    return (
        IngestSourceRoot(label="corpus", path=(repo_root / "corpus").resolve()),
        IngestSourceRoot(label="tests-fixtures", path=(repo_root / "tests" / "fixtures").resolve()),
    )


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
) -> IngestRunResult:
    resolved_repo_root = repo_root.resolve()
    resolved_data_root = data_root.resolve()
    resolved_data_root.mkdir(parents=True, exist_ok=True)
    resolved_source_roots = source_roots or build_default_source_roots(resolved_repo_root)
    for source_root in resolved_source_roots:
        if not source_root.path.exists():
            raise FileNotFoundError(f"Source root does not exist: {source_root.path}")

    generated_at = _utc_now()
    run_id = f"ingest-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"
    ingest_paths = create_ingest_paths(resolved_data_root)
    note_records = tuple(_discover_and_parse_notes(resolved_source_roots))

    connection = sqlite3.connect(ingest_paths.database_path)
    try:
        connection.row_factory = sqlite3.Row
        _initialize_schema(connection)
        counts = _materialize_records(
            connection=connection,
            note_records=note_records,
            source_roots=resolved_source_roots,
            run_id=run_id,
            generated_at=generated_at,
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


def _initialize_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
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
