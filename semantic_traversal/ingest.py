from __future__ import annotations

import fnmatch
import json
import math
import os
import re
import sqlite3
import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import yaml

from .config import RuntimeConfig, load_runtime_config
from .embeddings import (
    EmbeddingBackend,
    embedding_identity_from_response,
    embedding_identity_hash,
    embedding_identity_hint,
    resolve_embedding_backend,
)
from .hashing import sha256_json, sha256_text
from .text_filters import is_low_signal_apparatus_text
from .temporal import TemporalAnchor, build_temporal_anchors, temporal_diagnostics
from .resource_inventory import build_inventory_snapshot, persist_inventory_snapshot, validate_inventory_snapshot


HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*$")
INLINE_LABEL_RE = re.compile(r"^(?P<label>[A-Za-z0-9][A-Za-z0-9/&()'., \-]{0,80}):(?:\s*(?P<remainder>.*))?$")
LIST_ITEM_RE = re.compile(r"^\s*(?:[-*+]|\d+\.)\s+")
THEMATIC_BREAK_RE = re.compile(r"^\s*(?:---|\*\*\*|___)\s*$")
CODE_FENCE_RE = re.compile(r"^\s*(?P<fence>`{3,}|~{3,})(?P<info>.*)$")
TABLE_SEPARATOR_RE = re.compile(r"^\s*\|?(?:\s*:?-{3,}:?\s*\|)+\s*:?-{3,}:?\s*\|?\s*$")
APPARATUS_REFERENCE_LINE_RE = re.compile(
    r"^(?:"
    r"\d{1,4}"
    r"|\d{2,4}[a-eA-EаАеЕсС](?:\s+[a-eA-EаАеЕсС]){0,5}"
    r"|[a-eA-EаАеЕсС](?:\s+[a-eA-EаАеЕсС]){0,5}"
    r")$"
)
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
    latest_success_manifest_path: Path


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
    source_uuid: str
    source_root_label: str
    source_root_path: str
    relative_path: str
    note_path: str
    note_title: str
    frontmatter_semantics: dict[str, Any]
    section_id: str
    section_label: str
    section_kind: str
    section_path: tuple[str, ...]
    section_occurrence: int
    heading_level: int | None
    semantic_unit_kind: str
    paragraph_ordinal: int
    split_ordinal: int
    paragraph_text: str
    embedding_text: str
    embedding_text_hash: str
    chunk_hash: str
    chunking_warnings: tuple[str, ...]


@dataclass(frozen=True)
class NoteRecord:
    note_id: str
    source_uuid: str
    source_root_label: str
    source_root_path: str
    relative_path: str
    note_path: str
    note_title: str
    frontmatter: dict[str, Any]
    frontmatter_semantics: dict[str, Any]
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


class IngestFrontmatterError(RuntimeError):
    def __init__(self, message: str, *, manifest_path: Path) -> None:
        super().__init__(message)
        self.manifest_path = manifest_path


class IngestStageError(RuntimeError):
    """Retain the bounded failure stage while preserving the originating error."""

    def __init__(self, stage: str, cause: Exception) -> None:
        super().__init__(str(cause))
        self.stage = stage
        self.cause = cause


@dataclass(frozen=True)
class _Block:
    kind: str
    text: str
    heading_level: int | None = None
    warnings: tuple[str, ...] = ()


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
        latest_success_manifest_path=manifests_root / "latest-success.json",
    )


def build_configured_source_roots(repo_root: Path, config: RuntimeConfig | None = None) -> tuple[IngestSourceRoot, ...]:
    resolved_config = config or load_runtime_config(repo_root=repo_root)
    return (IngestSourceRoot(label=resolved_config.vault_source_label, path=resolved_config.vault_root),)


def _candidate_database_path(active_database_path: Path, run_id: str) -> Path:
    return active_database_path.with_name(f"{active_database_path.name}.candidate-{run_id}")


def _sqlite_sidecar_paths(database_path: Path) -> tuple[Path, ...]:
    return tuple(database_path.parent / f"{database_path.name}{suffix}" for suffix in ("-journal", "-wal", "-shm"))


def _clone_active_database(*, active_database_path: Path, candidate_database_path: Path) -> None:
    if candidate_database_path.exists() or any(path.exists() for path in _sqlite_sidecar_paths(candidate_database_path)):
        _cleanup_candidate_database(candidate_database_path)
    if not active_database_path.exists():
        return
    source = sqlite3.connect(active_database_path)
    target = sqlite3.connect(candidate_database_path)
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()


def _cleanup_sqlite_sidecars(database_path: Path) -> None:
    for path in _sqlite_sidecar_paths(database_path):
        if path.exists():
            path.unlink()


def _cleanup_candidate_database(candidate_database_path: Path) -> None:
    _cleanup_sqlite_sidecars(candidate_database_path)
    if candidate_database_path.exists():
        candidate_database_path.unlink()


def _write_success_artifact(*, ingest_paths: IngestPaths, manifest_path: Path, manifest: dict[str, Any]) -> None:
    serialized = json.dumps(manifest, indent=2, ensure_ascii=True) + "\n"
    manifest_path.write_text(serialized, encoding="utf-8")
    ingest_paths.latest_manifest_path.write_text(serialized, encoding="utf-8")
    ingest_paths.latest_success_manifest_path.write_text(serialized, encoding="utf-8")


def _write_failure_artifact(*, ingest_paths: IngestPaths, run_id: str, manifest: dict[str, Any]) -> Path:
    manifest_path = ingest_paths.manifests_root / f"{run_id}.json"
    serialized = json.dumps(manifest, indent=2, ensure_ascii=True) + "\n"
    manifest_path.write_text(serialized, encoding="utf-8")
    ingest_paths.latest_manifest_path.write_text(serialized, encoding="utf-8")
    return manifest_path


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
    resolved_source_roots = source_roots or build_configured_source_roots(resolved_repo_root, config=resolved_config)
    for source_root in resolved_source_roots:
        if not source_root.path.exists():
            raise FileNotFoundError(f"Source root does not exist: {source_root.path}")

    generated_at = _utc_now()
    run_id = f"ingest-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"
    ingest_paths = create_ingest_paths(resolved_data_root, config=resolved_config)
    note_records, validation_issues, skipped_sources = _discover_and_parse_notes(
        resolved_source_roots,
        config=resolved_config,
    )
    if validation_issues:
        failure_manifest = _build_failure_manifest(
            run_id=run_id,
            generated_at=generated_at,
            repo_root=resolved_repo_root,
            data_root=resolved_data_root,
            database_path=ingest_paths.database_path,
            source_roots=resolved_source_roots,
            validation_issues=validation_issues,
            skipped_sources=skipped_sources,
            failure_stage="frontmatter_validation",
            active_database_replaced=False,
            prior_active_database_preserved=ingest_paths.database_path.exists(),
            candidate_database_cleaned=True,
            error_type="IngestFrontmatterError",
            error_message="frontmatter validation failed",
            latest_success_manifest_path=ingest_paths.latest_success_manifest_path,
        )
        manifest_path = _write_failure_artifact(ingest_paths=ingest_paths, run_id=run_id, manifest=failure_manifest)
        raise IngestFrontmatterError(
            f"Ingest frontmatter validation failed; see failure manifest at {manifest_path}",
            manifest_path=manifest_path,
        )
    resolved_embedding_backend = embedding_backend or resolve_embedding_backend(resolved_config)

    active_database_path = ingest_paths.database_path
    candidate_database_path = _candidate_database_path(active_database_path, run_id)
    prior_active_exists = active_database_path.exists()
    failure_stage = "before_schema_initialization"
    active_database_replaced = False
    candidate_database_cleaned = False
    lexical_index: dict[str, Any] | None = None
    vector_index: dict[str, Any] | None = None
    temporal_index: dict[str, Any] | None = None
    resource_inventory: dict[str, Any] | None = None
    try:
        _clone_active_database(
            active_database_path=active_database_path,
            candidate_database_path=candidate_database_path,
        )
        failure_stage = "schema_initialization"
        connection = sqlite3.connect(candidate_database_path)
        connection.row_factory = sqlite3.Row
        try:
            _initialize_schema(connection, config=resolved_config)
            failure_stage = "materialization"
            counts = _materialize_records(
                connection=connection,
                note_records=note_records,
                source_roots=resolved_source_roots,
                run_id=run_id,
                generated_at=generated_at,
                config=resolved_config,
                embedding_backend=resolved_embedding_backend,
            )
            failure_stage = "candidate_validation"
            lexical_index = _validate_lexical_index(connection=connection, config=resolved_config)
            if lexical_index.get("status") != "valid":
                raise RuntimeError(
                    f"candidate lexical index validation failed: {lexical_index.get('failure_reason', 'alignment mismatch')}"
                )
            vector_index = _validate_vector_index(
                connection=connection,
                config=resolved_config,
                embedding_backend=resolved_embedding_backend,
            )
            if vector_index.get("status") == "invalid":
                raise RuntimeError(
                    f"candidate vector index validation failed: {vector_index.get('failure_reason', 'invalid vector index')}"
                )
            temporal_index = _validate_temporal_index(connection=connection, config=resolved_config)
            if temporal_index.get("status") != "valid":
                raise RuntimeError(
                    f"candidate temporal index validation failed: {temporal_index.get('failure_reason', 'invalid temporal index')}"
                )
            failure_stage = "inventory_build"
            resource_inventory = build_inventory_snapshot(
                connection=connection,
                config=resolved_config,
                source_ingest_run_id=run_id,
                generated_at=generated_at,
            )
            resource_inventory["validation_status"] = "valid"
            failure_stage = "inventory_persistence"
            persist_inventory_snapshot(connection=connection, snapshot=resource_inventory)
            failure_stage = "inventory_validation"
            inventory_validation = validate_inventory_snapshot(connection=connection, config=resolved_config, deep=True)
            if inventory_validation.get("status") != "valid":
                raise RuntimeError(
                    f"candidate resource inventory validation failed: {inventory_validation.get('errors', ['unknown failure'])}"
                )
        finally:
            connection.close()

        failure_stage = "activation"
        os.replace(candidate_database_path, active_database_path)
        active_database_replaced = True
        _cleanup_sqlite_sidecars(active_database_path)
        manifest = _build_manifest(
            run_id=run_id,
            generated_at=generated_at,
            repo_root=resolved_repo_root,
            data_root=resolved_data_root,
            database_path=active_database_path,
            source_roots=resolved_source_roots,
            note_records=note_records,
            counts=counts,
            skipped_sources=skipped_sources,
            lexical_index=lexical_index or {},
            vector_index=vector_index or {},
            temporal_index=temporal_index or {},
            resource_inventory=resource_inventory or {},
        )
        manifest_path = ingest_paths.manifests_root / f"{run_id}.json"
        _write_success_artifact(ingest_paths=ingest_paths, manifest_path=manifest_path, manifest=manifest)
        candidate_database_cleaned = True
    except Exception as exc:
        cleanup_error: Exception | None = None
        try:
            _cleanup_candidate_database(candidate_database_path)
            candidate_database_cleaned = not any(path.exists() for path in _sqlite_sidecar_paths(candidate_database_path))
        except Exception as cleanup_exc:
            cleanup_error = cleanup_exc
        cause = exc.cause if isinstance(exc, IngestStageError) else exc
        if isinstance(exc, IngestStageError):
            failure_stage = exc.stage
        failure_manifest = _build_failure_manifest(
            run_id=run_id,
            generated_at=generated_at,
            repo_root=resolved_repo_root,
            data_root=resolved_data_root,
            database_path=active_database_path,
            source_roots=resolved_source_roots,
            validation_issues=[],
            skipped_sources=skipped_sources,
            failure_stage=failure_stage,
            active_database_replaced=active_database_replaced,
            prior_active_database_preserved=prior_active_exists and not active_database_replaced,
            candidate_database_cleaned=candidate_database_cleaned,
            error_type=type(cause).__name__,
            error_message=str(cause),
            cleanup_error=cleanup_error,
            latest_success_manifest_path=ingest_paths.latest_success_manifest_path,
        )
        try:
            _write_failure_artifact(ingest_paths=ingest_paths, run_id=run_id, manifest=failure_manifest)
        except Exception:
            pass
        if isinstance(exc, IngestStageError):
            raise cause from exc
        raise

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


def _discover_and_parse_notes(
    source_roots: tuple[IngestSourceRoot, ...],
    *,
    config: RuntimeConfig,
) -> tuple[list[NoteRecord], list[dict[str, Any]], list[dict[str, Any]]]:
    note_records: list[NoteRecord] = []
    validation_issues: list[dict[str, Any]] = []
    skipped_sources: list[dict[str, Any]] = []
    exclude_globs = config.vault_exclude_globs
    for source_root in source_roots:
        for note_path in sorted(source_root.path.rglob("*.md")):
            relative_path = note_path.relative_to(source_root.path).as_posix()
            matched_exclude_glob = _matched_corpus_exclude_glob(relative_path, exclude_globs)
            if matched_exclude_glob is not None:
                skipped_sources.append(
                    _skipped_source(
                        source_root=source_root,
                        note_path=note_path,
                        relative_path=relative_path,
                        matched_exclude_glob=matched_exclude_glob,
                    )
                )
                continue
            note_record, issues = _parse_markdown_note(
                source_root=source_root,
                note_path=note_path,
                relative_path=relative_path,
                config=config,
            )
            validation_issues.extend(issues)
            if note_record is not None:
                note_records.append(note_record)
    validation_issues.extend(
        _duplicate_source_uuid_validation_issues(
            note_records=note_records,
            field_name=config.chunking_required_uuid_field,
        )
    )
    return note_records, validation_issues, skipped_sources


def _duplicate_source_uuid_validation_issues(
    *,
    note_records: list[NoteRecord],
    field_name: str,
) -> list[dict[str, Any]]:
    records_by_uuid: dict[str, list[NoteRecord]] = {}
    for note_record in note_records:
        records_by_uuid.setdefault(note_record.source_uuid, []).append(note_record)

    validation_issues: list[dict[str, Any]] = []
    for source_uuid, duplicate_records in sorted(records_by_uuid.items()):
        if len(duplicate_records) < 2:
            continue
        duplicate_paths = sorted(record.relative_path for record in duplicate_records)
        duplicate_path_text = ", ".join(duplicate_paths)
        for note_record in sorted(duplicate_records, key=lambda record: record.relative_path):
            validation_issues.append(
                {
                    "source_root_label": note_record.source_root_label,
                    "source_root_path": note_record.source_root_path,
                    "relative_path": note_record.relative_path,
                    "note_path": note_record.note_path,
                    "field_name": field_name,
                    "issue": (
                        f"duplicate UUID field `{field_name}` value `{source_uuid}`; "
                        f"also present in: {duplicate_path_text}"
                    ),
                }
            )
    return validation_issues


def _matched_corpus_exclude_glob(relative_path: str, exclude_globs: tuple[str, ...]) -> str | None:
    normalized_path = relative_path.replace("\\", "/").strip("/")
    for raw_pattern in exclude_globs:
        pattern = raw_pattern.replace("\\", "/").strip("/")
        if not pattern:
            continue
        if fnmatch.fnmatchcase(normalized_path, pattern):
            return raw_pattern
        if pattern.endswith("/**"):
            directory_prefix = pattern[:-3].rstrip("/")
            if normalized_path == directory_prefix or normalized_path.startswith(f"{directory_prefix}/"):
                return raw_pattern
    return None


def _skipped_source(
    *,
    source_root: IngestSourceRoot,
    note_path: Path,
    relative_path: str,
    matched_exclude_glob: str,
) -> dict[str, Any]:
    return {
        "source_root_label": source_root.label,
        "source_root_path": str(source_root.path),
        "relative_path": relative_path,
        "note_path": str(note_path),
        "matched_exclude_glob": matched_exclude_glob,
    }


def _parse_markdown_note(
    *,
    source_root: IngestSourceRoot,
    note_path: Path,
    relative_path: str,
    config: RuntimeConfig,
) -> tuple[NoteRecord | None, list[dict[str, Any]]]:
    raw_text = note_path.read_text(encoding="utf-8")
    frontmatter_text, body_text = _split_frontmatter(raw_text)
    frontmatter, frontmatter_issue = _parse_frontmatter(frontmatter_text)
    if frontmatter_issue is not None:
        return None, [
            _validation_issue(
                source_root=source_root,
                note_path=note_path,
                relative_path=relative_path,
                issue=frontmatter_issue,
                field_name=config.chunking_required_uuid_field,
            )
        ]
    source_uuid, uuid_issue = _extract_required_uuid(frontmatter, field_name=config.chunking_required_uuid_field)
    if uuid_issue is not None:
        return None, [
            _validation_issue(
                source_root=source_root,
                note_path=note_path,
                relative_path=relative_path,
                issue=uuid_issue,
                field_name=config.chunking_required_uuid_field,
            )
        ]
    note_title = _derive_note_title(frontmatter=frontmatter, note_path=note_path)
    tag_values = _extract_tag_values(frontmatter)
    frontmatter_semantics = _extract_semantic_frontmatter(frontmatter, config=config)
    wikilink_targets = _extract_wikilink_targets(body_text, frontmatter_semantics=frontmatter_semantics)
    blocks = _tokenize_markdown_blocks(body_text)
    chunks = _extract_chunks(
        blocks=blocks,
        note_id=_build_note_id(source_root.label, source_uuid),
        source_uuid=source_uuid,
        source_root=source_root,
        relative_path=relative_path,
        note_path=note_path,
        note_title=note_title,
        frontmatter=frontmatter,
        frontmatter_semantics=frontmatter_semantics,
        config=config,
        max_chunk_chars=config.chunking_max_chunk_chars,
    )
    return NoteRecord(
        note_id=_build_note_id(source_root.label, source_uuid),
        source_uuid=source_uuid,
        source_root_label=source_root.label,
        source_root_path=str(source_root.path),
        relative_path=relative_path,
        note_path=str(note_path),
        note_title=note_title,
        frontmatter=frontmatter,
        frontmatter_semantics=frontmatter_semantics,
        note_hash=sha256_text(raw_text),
        tag_values=tag_values,
        wikilink_targets=wikilink_targets,
        chunks=tuple(chunks),
    ), []


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


def _parse_frontmatter(frontmatter_text: str) -> tuple[dict[str, Any], str | None]:
    if not frontmatter_text.strip():
        return {}, None
    try:
        parsed = yaml.safe_load(frontmatter_text)
    except yaml.YAMLError as exc:
        return {}, f"invalid YAML frontmatter: {exc}"
    if parsed is None:
        return {}, None
    if not isinstance(parsed, dict):
        return {}, "frontmatter must parse as a mapping"
    return _json_safe_value(dict(parsed)), None


def _validation_issue(
    *,
    source_root: IngestSourceRoot,
    note_path: Path,
    relative_path: str,
    issue: str,
    field_name: str,
) -> dict[str, Any]:
    return {
        "source_root_label": source_root.label,
        "source_root_path": str(source_root.path),
        "relative_path": relative_path,
        "note_path": str(note_path),
        "field_name": field_name,
        "issue": issue,
    }


def _extract_required_uuid(frontmatter: dict[str, Any], *, field_name: str) -> tuple[str | None, str | None]:
    raw_value = frontmatter.get(field_name)
    if raw_value is None:
        return None, f"required UUID field `{field_name}` is missing"
    if not isinstance(raw_value, str):
        return None, f"required UUID field `{field_name}` must be a string"
    normalized = _normalize_inline_whitespace(raw_value)
    if not normalized:
        return None, f"required UUID field `{field_name}` is blank"
    try:
        return str(uuid.UUID(normalized)), None
    except ValueError:
        return None, f"required UUID field `{field_name}` is not a valid UUID"


def _extract_semantic_frontmatter(frontmatter: dict[str, Any], *, config: RuntimeConfig) -> dict[str, Any]:
    semantics: dict[str, Any] = {}
    for key in config.chunking_semantic_frontmatter_fields:
        if key in frontmatter:
            semantics[key] = _normalize_semantic_frontmatter_value(frontmatter[key])
    return semantics


def _normalize_semantic_frontmatter_value(value: Any) -> Any:
    if isinstance(value, list):
        normalized_items: list[Any] = []
        for item in value:
            normalized_item = _normalize_semantic_frontmatter_value(item)
            if normalized_item is None:
                continue
            if normalized_item not in normalized_items:
                normalized_items.append(normalized_item)
        return normalized_items
    if isinstance(value, str):
        normalized = _normalize_inline_whitespace(value)
        return normalized or None
    return _json_safe_value(value)


def _json_safe_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe_value(subvalue) for key, subvalue in value.items()}
    if isinstance(value, list):
        return [_json_safe_value(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe_value(item) for item in value]
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, datetime):
        return value.isoformat()
    return value


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


def _extract_wikilink_targets(body_text: str, *, frontmatter_semantics: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    targets: list[dict[str, Any]] = []
    for match in WIKILINK_RE.findall(body_text):
        parsed = _parse_wikilink_target(match)
        if parsed is None:
            continue
        parsed["source_surface"] = "body"
        if parsed not in targets:
            targets.append(parsed)
    def walk(value: Any, path: str) -> None:
        if isinstance(value, dict):
            for key in sorted(value, key=str):
                walk(value[key], f"{path}.{key}" if path else str(key))
        elif isinstance(value, list):
            for index, item in enumerate(value):
                walk(item, f"{path}[{index}]")
        elif isinstance(value, str):
            for match in WIKILINK_RE.findall(value):
                parsed = _parse_wikilink_target(match)
                if parsed is None:
                    continue
                parsed["source_surface"] = "admitted_frontmatter"
                parsed["frontmatter_field_path"] = path
                if parsed not in targets:
                    targets.append(parsed)
    walk(frontmatter_semantics, "")
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


def _is_apparatus_reference_line(value: str) -> bool:
    normalized = _normalize_inline_whitespace(value)
    return bool(APPARATUS_REFERENCE_LINE_RE.match(normalized))


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

    def flush_raw_block(kind: str, lines: list[str], *, warnings: tuple[str, ...] = ()) -> None:
        if not lines:
            return
        text = "\n".join(lines).rstrip("\n")
        if text:
            blocks.append(_Block(kind=kind, text=text, warnings=warnings))

    def is_table_start(index: int) -> bool:
        if index + 1 >= len(lines):
            return False
        current_stripped = lines[index].strip()
        next_stripped = lines[index + 1].strip()
        return "|" in current_stripped and bool(TABLE_SEPARATOR_RE.match(next_stripped))

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
        if _is_apparatus_reference_line(stripped):
            index += 1
            continue
        code_fence_match = CODE_FENCE_RE.match(stripped)
        if code_fence_match:
            flush_paragraph()
            fence_token = code_fence_match.group("fence")
            fence_prefix = fence_token[0]
            fence_length = len(fence_token)
            code_lines = [raw_line]
            index += 1
            while index < len(lines):
                next_raw_line = lines[index]
                code_lines.append(next_raw_line)
                next_stripped = next_raw_line.strip()
                if next_stripped.startswith(fence_prefix * fence_length):
                    index += 1
                    break
                index += 1
            flush_raw_block("code_block", code_lines)
            continue
        if is_table_start(index):
            flush_paragraph()
            table_lines = [raw_line, lines[index + 1]]
            index += 2
            while index < len(lines):
                next_raw_line = lines[index]
                next_stripped = next_raw_line.strip()
                if not next_stripped or HEADING_RE.match(next_stripped) or THEMATIC_BREAK_RE.match(next_stripped):
                    break
                if _is_apparatus_reference_line(next_stripped):
                    index += 1
                    continue
                if CODE_FENCE_RE.match(next_stripped) or LIST_ITEM_RE.match(next_stripped):
                    break
                if "|" not in next_stripped and not TABLE_SEPARATOR_RE.match(next_stripped):
                    break
                table_lines.append(next_raw_line)
                index += 1
            flush_raw_block("table", table_lines)
            continue
        if LIST_ITEM_RE.match(stripped):
            flush_paragraph()
            list_lines = [raw_line]
            index += 1
            while index < len(lines):
                next_raw_line = lines[index]
                next_stripped = next_raw_line.strip()
                if not next_stripped or HEADING_RE.match(next_stripped) or THEMATIC_BREAK_RE.match(next_stripped):
                    break
                if _is_apparatus_reference_line(next_stripped):
                    index += 1
                    continue
                if CODE_FENCE_RE.match(next_stripped) or is_table_start(index):
                    break
                list_lines.append(next_raw_line)
                index += 1
            flush_raw_block("list", list_lines)
            continue
        current_lines.append(raw_line)
        index += 1

    flush_paragraph()
    return blocks


def _extract_chunks(
    *,
    blocks: list[_Block],
    note_id: str,
    source_uuid: str,
    source_root: IngestSourceRoot,
    relative_path: str,
    note_path: Path,
    note_title: str,
    frontmatter: dict[str, Any],
    frontmatter_semantics: dict[str, Any],
    config: RuntimeConfig,
    max_chunk_chars: int,
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

        semantic_unit_text = block.text
        semantic_unit_kind = block.kind
        chunk_warnings = list(block.warnings)
        if semantic_unit_kind == "paragraph":
            inline_label_match = INLINE_LABEL_RE.match(semantic_unit_text)
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
                semantic_unit_text = _normalize_inline_whitespace(inline_label_match.group("remainder") or "")
                if not semantic_unit_text:
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
        if semantic_unit_kind == "paragraph" and len(semantic_unit_text) > max_chunk_chars:
            chunk_texts = _split_prose_unit_text(semantic_unit_text, max_chunk_chars)
            chunk_warnings.append("oversized_prose_paragraph_split")
        else:
            chunk_texts = [(semantic_unit_text, 1)]
            if len(semantic_unit_text) > max_chunk_chars and semantic_unit_kind in {"code_block", "table", "list"}:
                chunk_warnings.append(_chunking_warning(semantic_unit_kind))

        for chunk_text, split_ordinal in chunk_texts:
            if config.chunking_low_signal_apparatus_skip_during_ingest and is_low_signal_apparatus_text(
                chunk_text,
                config=config,
                section_label=current_section.label,
                note_title=note_title,
                relative_path=relative_path,
            ):
                continue
            embedding_text = _build_embedding_text_for_chunk(
                note_title=note_title,
                relative_path=relative_path,
                section_path=current_section.path_labels,
                frontmatter_semantics=frontmatter_semantics,
                semantic_unit_text=chunk_text,
            )
            embedding_text_hash = sha256_text(embedding_text)
            chunks.append(
                ChunkRecord(
                    chunk_id=_build_chunk_id(note_id, current_section.section_id, paragraph_ordinal, split_ordinal),
                    note_id=note_id,
                    source_uuid=source_uuid,
                    source_root_label=source_root.label,
                    source_root_path=str(source_root.path),
                    relative_path=relative_path,
                    note_path=str(note_path),
                    note_title=note_title,
                    frontmatter_semantics=frontmatter_semantics,
                    section_id=current_section.section_id,
                    section_label=current_section.label,
                    section_kind=current_section.kind,
                    section_path=current_section.path_labels,
                    section_occurrence=current_section.occurrence,
                    heading_level=current_section.heading_level,
                    semantic_unit_kind=semantic_unit_kind,
                    paragraph_ordinal=paragraph_ordinal,
                    split_ordinal=split_ordinal,
                    paragraph_text=chunk_text,
                    embedding_text=embedding_text,
                    embedding_text_hash=embedding_text_hash,
                    chunk_hash=embedding_text_hash,
                    chunking_warnings=tuple(dict.fromkeys(chunk_warnings)),
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


def _split_prose_unit_text(text: str, max_chunk_chars: int) -> list[tuple[str, int]]:
    normalized = _normalize_inline_whitespace(text)
    if len(normalized) <= max_chunk_chars:
        return [(normalized, 1)]
    segments: list[tuple[str, int]] = []
    remaining = normalized
    split_ordinal = 1
    while remaining:
        if len(remaining) <= max_chunk_chars:
            segments.append((remaining, split_ordinal))
            break
        candidate = remaining[:max_chunk_chars]
        sentence_match = None
        for match in re.finditer(r"[.!?](?=\s|$)", candidate):
            sentence_match = match
        split_at = sentence_match.end() if sentence_match is not None else -1
        if split_at <= 0:
            whitespace_match = None
            for match in re.finditer(r"\s+", candidate):
                whitespace_match = match
            split_at = whitespace_match.start() if whitespace_match is not None else max_chunk_chars
        split_at = max(1, min(split_at, len(candidate)))
        segments.append((remaining[:split_at].rstrip(), split_ordinal))
        remaining = remaining[split_at:].lstrip()
        split_ordinal += 1
    return segments


def _chunk_suffix(split_ordinal: int) -> str:
    if split_ordinal <= 1:
        return ""
    return f"-s{split_ordinal:02d}"


def _build_chunk_id(note_id: str, section_id: str, paragraph_ordinal: int, split_ordinal: int) -> str:
    return f"{note_id}::{section_id}::p{paragraph_ordinal:04d}{_chunk_suffix(split_ordinal)}"


def _build_embedding_text_for_chunk(
    *,
    note_title: str,
    relative_path: str,
    section_path: tuple[str, ...],
    frontmatter_semantics: dict[str, Any],
    semantic_unit_text: str,
) -> str:
    """Serialize the one admitted semantic chunk surface deterministically.

    This is deliberately a representation of the typed chunk, not an
    interpretation of any field.  JSON is used for values so lists, mappings,
    nulls, and scalar types have stable, unambiguous serialization.
    """
    heading_path = " > ".join(section_path) if section_path else note_title
    metadata = json.dumps(
        _canonical_semantic_value(frontmatter_semantics),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return "\n".join(
        [
            "Title: " + note_title,
            "Path: " + relative_path,
            "Section: " + heading_path,
            "Semantic metadata: " + metadata,
            "Content:",
            semantic_unit_text,
        ]
    )


def _canonical_semantic_value(value: Any) -> Any:
    """Return JSON-safe values with recursively stable mapping order."""
    if isinstance(value, dict):
        return {str(key): _canonical_semantic_value(value[key]) for key in sorted(value, key=str)}
    if isinstance(value, (list, tuple)):
        return [_canonical_semantic_value(item) for item in value]
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    return value


def _chunking_warning(kind: str) -> str:
    return f"oversized_atomic_{kind}"


def _build_note_id(source_root_label: str, source_uuid: str) -> str:
    return f"{source_root_label}::uuid::{source_uuid}"


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
            source_uuid TEXT NOT NULL,
            source_root_label TEXT NOT NULL,
            source_root_path TEXT NOT NULL,
            relative_path TEXT NOT NULL,
            note_path TEXT NOT NULL,
            note_title TEXT NOT NULL,
            frontmatter_json TEXT NOT NULL,
            frontmatter_semantics_json TEXT NOT NULL,
            note_hash TEXT NOT NULL,
            last_ingested_run_id TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS chunks (
            chunk_id TEXT PRIMARY KEY,
            note_id TEXT NOT NULL,
            source_uuid TEXT NOT NULL,
            source_root_label TEXT NOT NULL,
            source_root_path TEXT NOT NULL,
            relative_path TEXT NOT NULL,
            note_path TEXT NOT NULL,
            note_title TEXT NOT NULL,
            frontmatter_semantics_json TEXT NOT NULL,
            section_id TEXT NOT NULL,
            section_label TEXT NOT NULL,
            section_kind TEXT NOT NULL,
            section_path_json TEXT NOT NULL,
            section_occurrence INTEGER NOT NULL,
            heading_level INTEGER,
            semantic_unit_kind TEXT NOT NULL,
            paragraph_ordinal INTEGER NOT NULL,
            split_ordinal INTEGER NOT NULL,
            paragraph_text TEXT NOT NULL,
            chunk_hash TEXT NOT NULL,
            embedding_text TEXT NOT NULL,
            embedding_text_hash TEXT NOT NULL,
            chunking_warnings_json TEXT NOT NULL,
            last_ingested_run_id TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_notes_source_root_label ON notes(source_root_label);
        CREATE INDEX IF NOT EXISTS idx_chunks_note_id ON chunks(note_id);
        CREATE INDEX IF NOT EXISTS idx_chunks_source_root_label ON chunks(source_root_label);

        CREATE TABLE IF NOT EXISTS temporal_anchors (
            anchor_id TEXT PRIMARY KEY,
            note_id TEXT NOT NULL,
            chunk_id TEXT,
            anchor_type TEXT NOT NULL,
            canonical_start TEXT,
            canonical_end TEXT,
            precision TEXT,
            source_field TEXT NOT NULL,
            original_source_value TEXT NOT NULL,
            authority TEXT NOT NULL,
            parsing_status TEXT NOT NULL,
            conflict_group TEXT,
            unresolved INTEGER NOT NULL,
            diagnostic_reason TEXT,
            ingest_run_id TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_temporal_anchors_note_id ON temporal_anchors(note_id);
        CREATE INDEX IF NOT EXISTS idx_temporal_anchors_chunk_id ON temporal_anchors(chunk_id);
        CREATE INDEX IF NOT EXISTS idx_temporal_anchors_type ON temporal_anchors(anchor_type);
        CREATE INDEX IF NOT EXISTS idx_temporal_anchors_start ON temporal_anchors(canonical_start);
        CREATE INDEX IF NOT EXISTS idx_temporal_anchors_end ON temporal_anchors(canonical_end);
        CREATE INDEX IF NOT EXISTS idx_temporal_anchors_authority ON temporal_anchors(authority);
        CREATE INDEX IF NOT EXISTS idx_temporal_anchors_status ON temporal_anchors(parsing_status);

        CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
            chunk_id UNINDEXED,
            paragraph_text,
            note_title,
            section_label,
            section_path,
            relative_path,
            metadata
        );

        CREATE TABLE IF NOT EXISTS {vector_table} (
            chunk_id TEXT PRIMARY KEY,
            vector_json TEXT NOT NULL,
            vector_dimensions INTEGER NOT NULL,
            embedding_provider TEXT NOT NULL,
            embedding_model TEXT NOT NULL,
            embedding_identity_json TEXT NOT NULL DEFAULT '{{}}',
            embedding_identity_hash TEXT NOT NULL DEFAULT '',
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

        CREATE TABLE IF NOT EXISTS resource_inventory_snapshots (
            snapshot_key TEXT PRIMARY KEY,
            snapshot_id TEXT NOT NULL,
            inventory_schema_version INTEGER NOT NULL,
            source_ingest_run_id TEXT NOT NULL,
            generated_at TEXT NOT NULL,
            inventory_policy_hash TEXT NOT NULL,
            logical_inventory_hash TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            validation_status TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_{vector_table}_content_hash ON {vector_table}(content_hash);
        CREATE INDEX IF NOT EXISTS idx_{graph_nodes_table}_node_type ON {graph_nodes_table}(node_type);
        CREATE INDEX IF NOT EXISTS idx_{graph_edges_table}_source ON {graph_edges_table}(source_node_id);
        CREATE INDEX IF NOT EXISTS idx_{graph_edges_table}_target ON {graph_edges_table}(target_node_id);
        CREATE INDEX IF NOT EXISTS idx_{graph_edges_table}_type ON {graph_edges_table}(edge_type);
        """
    )
    vector_columns = {str(row[1]) for row in connection.execute(f"PRAGMA table_info({vector_table})").fetchall()}
    if "embedding_identity_json" not in vector_columns:
        connection.execute(f"ALTER TABLE {vector_table} ADD COLUMN embedding_identity_json TEXT NOT NULL DEFAULT '{{}}'")
    if "embedding_identity_hash" not in vector_columns:
        connection.execute(f"ALTER TABLE {vector_table} ADD COLUMN embedding_identity_hash TEXT NOT NULL DEFAULT ''")
    fts_columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(chunks_fts)").fetchall()}
    if fts_columns and "section_path" not in fts_columns:
        connection.execute("DROP TABLE chunks_fts")
        connection.execute("CREATE VIRTUAL TABLE chunks_fts USING fts5(chunk_id UNINDEXED, paragraph_text, note_title, section_label, section_path, relative_path, metadata)")
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
) -> dict[str, Any]:
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
    counts["temporal_index"] = _refresh_temporal_index(
        connection=connection,
        note_records=note_records,
        run_id=run_id,
        config=config,
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
    try:
        _refresh_lexical_index(connection=connection)
    except Exception as exc:
        raise IngestStageError("lexical_index_refresh", exc) from exc

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


def _refresh_temporal_index(
    *, connection: sqlite3.Connection, note_records: tuple[NoteRecord, ...], run_id: str, config: RuntimeConfig
) -> dict[str, Any]:
    connection.execute("DELETE FROM temporal_anchors")
    all_anchors: list[TemporalAnchor] = []
    issues: list[dict[str, Any]] = []
    for note_record in note_records:
        anchors, note_issues = build_temporal_anchors(
            note_id=note_record.note_id,
            frontmatter=note_record.frontmatter,
            mappings=config.retrieval_temporal_field_mappings,
            ingest_run_id=run_id,
        )
        all_anchors.extend(anchors)
        issues.extend(note_issues)
    connection.executemany(
        """
        INSERT INTO temporal_anchors (
            anchor_id, note_id, chunk_id, anchor_type, canonical_start,
            canonical_end, precision, source_field, original_source_value,
            authority, parsing_status, conflict_group, unresolved,
            diagnostic_reason, ingest_run_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                anchor.anchor_id, anchor.note_id, anchor.chunk_id, anchor.anchor_type,
                anchor.canonical_start, anchor.canonical_end, anchor.precision,
                anchor.source_field, anchor.original_source_value, anchor.authority,
                anchor.parsing_status, anchor.conflict_group, int(anchor.unresolved),
                anchor.diagnostic_reason, anchor.ingest_run_id,
            )
            for anchor in all_anchors
        ],
    )
    diagnostics = temporal_diagnostics(all_anchors, issues)
    diagnostics["unanchored_note_count"] = max(0, len(note_records) - int(diagnostics.get("anchored_note_count") or 0))
    return diagnostics


def _validate_temporal_index(*, connection: sqlite3.Connection, config: RuntimeConfig) -> dict[str, Any]:
    result: dict[str, Any] = {"status": "invalid", "table": "temporal_anchors"}
    table = connection.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'temporal_anchors'").fetchone()
    if table is None:
        result["failure_reason"] = "temporal_anchors table is missing"
        return result
    rows = connection.execute("SELECT * FROM temporal_anchors ORDER BY anchor_id").fetchall()
    note_ids = {str(row[0]) for row in connection.execute("SELECT note_id FROM notes")}
    chunk_ids = {str(row[0]) for row in connection.execute("SELECT chunk_id FROM chunks")}
    ids = [str(row["anchor_id"]) for row in rows]
    errors: list[str] = []
    for row in rows:
        if str(row["note_id"]) not in note_ids:
            errors.append("orphan note reference")
        if row["chunk_id"] is not None and str(row["chunk_id"]) not in chunk_ids:
            errors.append("orphan chunk reference")
        if row["parsing_status"] == "valid":
            if not row["canonical_start"] or not row["canonical_end"] or str(row["canonical_start"]) > str(row["canonical_end"]):
                errors.append("invalid canonical interval")
            if str(row["precision"] or "") not in {"year", "month", "day", "datetime"}:
                errors.append("unknown precision")
        if str(row["anchor_type"]) not in {"journal_entry", "authored", "created", "modified", "encountered_or_read", "publication", "event"}:
            errors.append("unknown anchor type")
        if str(row["authority"]) not in {"explicit_primary", "explicit_secondary", "operational_low"}:
            errors.append("unknown authority")
        if row["conflict_group"] is None and bool(row["unresolved"]) and row["parsing_status"] == "valid":
            errors.append("unresolved anchor missing conflict group")
    if len(ids) != len(set(ids)):
        errors.append("duplicate anchor IDs")
    valid_rows = [row for row in rows if str(row["parsing_status"]) == "valid"]
    result.update({
        "anchor_count": len(rows),
        "total_anchor_count": len(rows),
        "anchored_note_count": len({str(row["note_id"]) for row in valid_rows}),
        "anchored_chunk_count": len({str(row["chunk_id"]) for row in valid_rows if row["chunk_id"] is not None}),
        "unanchored_note_count": max(0, len(note_ids) - len({str(row["note_id"]) for row in valid_rows})),
        "valid_count": len(valid_rows),
        "invalid_count": sum(str(row["parsing_status"]) == "invalid" for row in rows),
        "ambiguous_count": sum(str(row["parsing_status"]) == "ambiguous" for row in rows),
        "conflict_count": len({str(row["conflict_group"]) for row in rows if row["conflict_group"]}),
        "counts_by_anchor_type": dict(sorted(Counter(str(row["anchor_type"]) for row in valid_rows).items())),
        "counts_by_authority": dict(sorted(Counter(str(row["authority"]) for row in valid_rows).items())),
        "counts_by_precision": dict(sorted(Counter(str(row["precision"]) for row in valid_rows).items())),
        "error_count": len(errors),
        "error_samples": sorted(set(errors))[:10],
    })
    if errors:
        result["failure_reason"] = "; ".join(sorted(set(errors)))
        return result
    result["status"] = "valid"
    return result


def _validate_lexical_index(*, connection: sqlite3.Connection, config: RuntimeConfig) -> dict[str, Any]:
    result: dict[str, Any] = {
        "status": "invalid",
        "table": "chunks_fts",
        "canonical_chunk_count": 0,
        "fts_row_count": 0,
        "missing_chunk_count": 0,
        "orphan_row_count": 0,
        "duplicate_chunk_id_count": 0,
        "field_mismatch_count": 0,
        "query_probe_status": "not_run",
        "validation_stage": "candidate_post_materialization",
    }
    table = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'chunks_fts'"
    ).fetchone()
    if table is None:
        result["failure_reason"] = "chunks_fts table is missing"
        return result
    canonical_rows = connection.execute(
        "SELECT chunk_id, paragraph_text, note_title, section_label, section_path_json, relative_path, frontmatter_semantics_json FROM chunks ORDER BY chunk_id"
    ).fetchall()
    fts_rows = connection.execute(
        "SELECT chunk_id, paragraph_text, note_title, section_label, section_path, relative_path, metadata FROM chunks_fts ORDER BY chunk_id"
    ).fetchall()
    canonical_by_id = {str(row["chunk_id"]): row for row in canonical_rows}
    fts_ids = [str(row["chunk_id"]) for row in fts_rows]
    fts_id_set = set(fts_ids)
    duplicate_ids = sorted(chunk_id for chunk_id, count in Counter(fts_ids).items() if count > 1)
    missing_ids = sorted(set(canonical_by_id) - fts_id_set)
    orphan_ids = sorted(fts_id_set - set(canonical_by_id))
    mismatch_ids: list[str] = []
    for row in fts_rows:
        chunk_id = str(row["chunk_id"])
        canonical = canonical_by_id.get(chunk_id)
        if canonical is None:
            continue
        if (
            str(row["paragraph_text"]) != str(canonical["paragraph_text"])
            or str(row["note_title"]) != str(canonical["note_title"])
            or str(row["section_label"]) != str(canonical["section_label"])
            or str(row["section_path"]) != " > ".join(json.loads(str(canonical["section_path_json"] or "[]")))
            or str(row["relative_path"]) != str(canonical["relative_path"])
            or str(row["metadata"]) != str(canonical["frontmatter_semantics_json"])
        ) and chunk_id not in mismatch_ids:
            mismatch_ids.append(chunk_id)
    result.update(
        {
            "canonical_chunk_count": len(canonical_rows),
            "fts_row_count": len(fts_rows),
            "missing_chunk_count": len(missing_ids),
            "orphan_row_count": len(orphan_ids),
            "duplicate_chunk_id_count": len(duplicate_ids),
            "field_mismatch_count": len(mismatch_ids),
            "missing_chunk_sample": missing_ids[:5],
            "orphan_row_sample": orphan_ids[:5],
            "field_mismatch_sample": sorted(mismatch_ids)[:5],
        }
    )
    try:
        connection.execute("SELECT chunk_id FROM chunks_fts WHERE chunks_fts MATCH ? LIMIT 1", ("a",)).fetchone()
        result["query_probe_status"] = "passed"
    except sqlite3.Error as exc:
        result["query_probe_status"] = "failed"
        result["failure_reason"] = f"FTS query probe failed: {exc}"
        return result
    if any(
        result[key]
        for key in ("missing_chunk_count", "orphan_row_count", "duplicate_chunk_id_count", "field_mismatch_count")
    ) or result["canonical_chunk_count"] != result["fts_row_count"]:
        result["failure_reason"] = "canonical chunks and chunks_fts are not aligned"
        return result
    result["status"] = "valid"
    return result


def _refresh_lexical_index(*, connection: sqlite3.Connection) -> None:
    """Keep the FTS surface transactionally aligned with the active chunks."""
    connection.execute("DELETE FROM chunks_fts")
    rows = connection.execute(
        "SELECT chunk_id, paragraph_text, note_title, section_label, section_path_json, relative_path, frontmatter_semantics_json FROM chunks ORDER BY chunk_id"
    ).fetchall()
    connection.executemany(
        "INSERT INTO chunks_fts (chunk_id, paragraph_text, note_title, section_label, section_path, relative_path, metadata) VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            (
                row["chunk_id"], row["paragraph_text"], row["note_title"], row["section_label"],
                " > ".join(json.loads(str(row["section_path_json"] or "[]"))), row["relative_path"], row["frontmatter_semantics_json"],
            )
            for row in rows
        ],
    )


def _validate_vector_index(
    *,
    connection: sqlite3.Connection,
    config: RuntimeConfig,
    embedding_backend: EmbeddingBackend | None,
) -> dict[str, Any]:
    vector_table = config.vector_table
    configured_hint = embedding_identity_hint(backend=embedding_backend, config=config) if embedding_backend is not None else None
    result: dict[str, Any] = {
        "status": "invalid",
        "table": vector_table,
        "canonical_chunk_count": 0,
        "vector_row_count": 0,
        "compatible_vector_count": 0,
        "missing_vector_count": 0,
        "orphan_vector_count": 0,
        "invalid_json_count": 0,
        "invalid_numeric_count": 0,
        "empty_vector_count": 0,
        "dimension_mismatch_count": 0,
        "identity_mismatch_count": 0,
        "zero_norm_count": 0,
        "configured_identity": configured_hint or {
            "provider": config.embedding_provider,
            "model": config.embedding_model,
            "dimensions": config.embedding_dimensions,
            "normalize_embeddings": config.embedding_normalize_embeddings,
            "encoding_strategy": "chunk_embedding_text_v1",
        },
        "observed_identities": [],
        "bad_chunk_id_sample": [],
    }
    table = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?", (vector_table,)
    ).fetchone()
    if table is None:
        result["status"] = "degraded_unavailable"
        result["failure_reason"] = "vector table is missing"
        return result
    canonical_ids = {
        str(row[0]) for row in connection.execute("SELECT chunk_id FROM chunks ORDER BY chunk_id").fetchall()
    }
    rows = connection.execute(
        f"SELECT chunk_id, vector_json, vector_dimensions, embedding_provider, embedding_model, embedding_identity_json, embedding_identity_hash FROM {vector_table} ORDER BY chunk_id"
    ).fetchall()
    observed: dict[str, dict[str, Any]] = {}
    bad_ids: set[str] = set()
    compatible_count = 0
    for row in rows:
        chunk_id = str(row["chunk_id"])
        if chunk_id not in canonical_ids:
            result["orphan_vector_count"] += 1
            bad_ids.add(chunk_id)
            continue
        try:
            identity = json.loads(str(row["embedding_identity_json"]))
        except (TypeError, ValueError, json.JSONDecodeError):
            result["identity_mismatch_count"] += 1
            bad_ids.add(chunk_id)
            continue
        required_identity_fields = {"provider", "model", "dimensions", "normalize_embeddings", "encoding_strategy"}
        if not isinstance(identity, dict) or not required_identity_fields.issubset(identity):
            result["identity_mismatch_count"] += 1
            bad_ids.add(chunk_id)
            continue
        identity_key = json.dumps(identity, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        observed[identity_key] = identity
        try:
            columns_match = (
                str(row["embedding_provider"]) == str(identity["provider"])
                and str(row["embedding_model"]) == str(identity["model"])
                and int(row["vector_dimensions"]) == int(identity["dimensions"])
            )
        except (TypeError, ValueError):
            columns_match = False
        if not columns_match:
            result["identity_mismatch_count"] += 1
            bad_ids.add(chunk_id)
            continue
        if str(row["embedding_identity_hash"] or "") != embedding_identity_hash(identity):
            result["identity_mismatch_count"] += 1
            bad_ids.add(chunk_id)
            continue
        if configured_hint is not None:
            for field in ("provider", "model", "normalize_embeddings", "encoding_strategy"):
                if identity.get(field) != configured_hint.get(field):
                    result["identity_mismatch_count"] += 1
                    bad_ids.add(chunk_id)
                    break
            else:
                if configured_hint.get("dimensions") is not None and identity.get("dimensions") != configured_hint.get("dimensions"):
                    result["dimension_mismatch_count"] += 1
                    bad_ids.add(chunk_id)
                    continue
            if chunk_id in bad_ids:
                continue
        try:
            vector = json.loads(str(row["vector_json"]))
        except (TypeError, ValueError, json.JSONDecodeError):
            result["invalid_json_count"] += 1
            bad_ids.add(chunk_id)
            continue
        if not isinstance(vector, list):
            result["invalid_numeric_count"] += 1
            bad_ids.add(chunk_id)
            continue
        if not vector:
            result["empty_vector_count"] += 1
            bad_ids.add(chunk_id)
            continue
        if not all(isinstance(value, (int, float)) for value in vector):
            result["invalid_numeric_count"] += 1
            bad_ids.add(chunk_id)
            continue
        if len(vector) != int(identity["dimensions"]) or len(vector) != int(row["vector_dimensions"]):
            result["dimension_mismatch_count"] += 1
            bad_ids.add(chunk_id)
            continue
        norm = math.sqrt(sum(float(value) * float(value) for value in vector))
        if norm == 0.0:
            result["zero_norm_count"] += 1
            bad_ids.add(chunk_id)
            continue
        compatible_count += 1
    result.update(
        {
            "canonical_chunk_count": len(canonical_ids),
            "vector_row_count": len(rows),
            "compatible_vector_count": compatible_count,
            "missing_vector_count": len(canonical_ids - {str(row["chunk_id"]) for row in rows}),
            "observed_identities": list(sorted(observed.values(), key=lambda value: json.dumps(value, sort_keys=True)))[:5],
            "bad_chunk_id_sample": sorted(bad_ids)[:10],
            "mixed_identity_count": max(0, len(observed) - 1),
        }
    )
    if not rows:
        result["status"] = "valid" if not canonical_ids else "degraded_unavailable"
    elif compatible_count == len(canonical_ids) and len(rows) == len(canonical_ids) and not bad_ids and result["mixed_identity_count"] == 0:
        result["status"] = "valid"
    elif compatible_count or result["missing_vector_count"]:
        result["status"] = "degraded_partial"
    else:
        result["status"] = "invalid"
    if result["status"] != "valid":
        result["failure_reason"] = "vector rows are missing, malformed, incompatible, or degraded"
    return result


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
            source_uuid,
            source_root_label,
            source_root_path,
            relative_path,
            note_path,
            note_title,
            frontmatter_json,
            frontmatter_semantics_json,
            note_hash,
            last_ingested_run_id,
            updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(note_id) DO UPDATE SET
            source_uuid=excluded.source_uuid,
            source_root_label=excluded.source_root_label,
            source_root_path=excluded.source_root_path,
            relative_path=excluded.relative_path,
            note_path=excluded.note_path,
            note_title=excluded.note_title,
            frontmatter_json=excluded.frontmatter_json,
            frontmatter_semantics_json=excluded.frontmatter_semantics_json,
            note_hash=excluded.note_hash,
            last_ingested_run_id=excluded.last_ingested_run_id,
            updated_at=excluded.updated_at
        """,
        (
            note_record.note_id,
            note_record.source_uuid,
            note_record.source_root_label,
            note_record.source_root_path,
            note_record.relative_path,
            note_record.note_path,
            note_record.note_title,
            json.dumps(note_record.frontmatter, ensure_ascii=True, sort_keys=True),
            json.dumps(note_record.frontmatter_semantics, ensure_ascii=True, sort_keys=True),
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
            source_uuid,
            source_root_label,
            source_root_path,
            relative_path,
            note_path,
            note_title,
            frontmatter_semantics_json,
            section_id,
            section_label,
            section_kind,
            section_path_json,
            section_occurrence,
            heading_level,
            semantic_unit_kind,
            paragraph_ordinal,
            split_ordinal,
            paragraph_text,
            chunk_hash,
            embedding_text,
            embedding_text_hash,
            chunking_warnings_json,
            last_ingested_run_id,
            updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(chunk_id) DO UPDATE SET
            note_id=excluded.note_id,
            source_uuid=excluded.source_uuid,
            source_root_label=excluded.source_root_label,
            source_root_path=excluded.source_root_path,
            relative_path=excluded.relative_path,
            note_path=excluded.note_path,
            note_title=excluded.note_title,
            frontmatter_semantics_json=excluded.frontmatter_semantics_json,
            section_id=excluded.section_id,
            section_label=excluded.section_label,
            section_kind=excluded.section_kind,
            section_path_json=excluded.section_path_json,
            section_occurrence=excluded.section_occurrence,
            heading_level=excluded.heading_level,
            semantic_unit_kind=excluded.semantic_unit_kind,
            paragraph_ordinal=excluded.paragraph_ordinal,
            split_ordinal=excluded.split_ordinal,
            paragraph_text=excluded.paragraph_text,
            chunk_hash=excluded.chunk_hash,
            embedding_text=excluded.embedding_text,
            embedding_text_hash=excluded.embedding_text_hash,
            chunking_warnings_json=excluded.chunking_warnings_json,
            last_ingested_run_id=excluded.last_ingested_run_id,
            updated_at=excluded.updated_at
        """,
        (
            chunk.chunk_id,
            chunk.note_id,
            chunk.source_uuid,
            chunk.source_root_label,
            chunk.source_root_path,
            chunk.relative_path,
            chunk.note_path,
            chunk.note_title,
            json.dumps(chunk.frontmatter_semantics, ensure_ascii=True, sort_keys=True),
            chunk.section_id,
            chunk.section_label,
            chunk.section_kind,
            json.dumps(chunk.section_path, ensure_ascii=True),
            chunk.section_occurrence,
            chunk.heading_level,
            chunk.semantic_unit_kind,
            chunk.paragraph_ordinal,
            chunk.split_ordinal,
            chunk.paragraph_text,
            chunk.chunk_hash,
            chunk.embedding_text,
            chunk.embedding_text_hash,
            json.dumps(list(chunk.chunking_warnings), ensure_ascii=True),
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
                        "source_uuid": note_record.source_uuid,
                        "relative_path": note_record.relative_path,
                        "source_root_label": note_record.source_root_label,
                        "frontmatter_semantics": note_record.frontmatter_semantics,
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
                            "source_uuid": chunk.source_uuid,
                            "relative_path": chunk.relative_path,
                            "paragraph_ordinal": chunk.paragraph_ordinal,
                            "split_ordinal": chunk.split_ordinal,
                            "semantic_unit_kind": chunk.semantic_unit_kind,
                            "source_root_label": chunk.source_root_label,
                            "chunking_warnings": list(chunk.chunking_warnings),
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
                            "source_surface": target_record.get("source_surface", "body"),
                            "frontmatter_field_path": target_record.get("frontmatter_field_path"),
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

    # A directed authored relation has one canonical edge identity. Merge all
    # body/frontmatter occurrences into deterministic provenance instead of
    # inflating graph ranking with duplicate links.
    merged_edges: dict[tuple[str, str, str], tuple[Any, ...]] = {}
    provenance: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for edge in edge_rows:
        key = (str(edge[1]), str(edge[2]), str(edge[3]))
        if edge[3] != "note_links_note":
            merged_edges[key] = edge
            continue
        try:
            record = json.loads(str(edge[4]))
        except json.JSONDecodeError:
            record = {}
        provenance.setdefault(key, []).append(record)
        if key not in merged_edges:
            merged_edges[key] = edge
    for key, records in provenance.items():
        edge = merged_edges[key]
        merged = dict(records[0])
        merged["provenance"] = sorted(records, key=lambda item: (str(item.get("source_surface")), str(item.get("frontmatter_field_path")), str(item.get("raw_wikilink_text"))))
        merged["source_surfaces"] = sorted({str(item.get("source_surface") or "body") for item in records})
        merged_edges[key] = (*edge[:4], json.dumps(merged, ensure_ascii=True, sort_keys=True), *edge[5:])
    edge_rows = list(merged_edges.values())

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
        f"SELECT chunk_id, content_hash, embedding_identity_json, embedding_identity_hash FROM {vector_table} WHERE chunk_id IN ({placeholders})",
        tuple(processed_chunk_ids),
    ).fetchall()
    identity_hint = embedding_identity_hint(backend=embedding_backend, config=config) if embedding_backend is not None else None
    existing_rows_by_id = {str(row["chunk_id"]): row for row in existing_rows}
    rows_to_index: list[ChunkRecord] = []
    for note_record in note_records:
        for chunk in note_record.chunks:
            existing = existing_rows_by_id.get(chunk.chunk_id)
            reusable = False
            if existing is not None and str(existing["content_hash"]) == chunk.chunk_hash:
                try:
                    stored_identity = json.loads(str(existing["embedding_identity_json"]))
                    reusable = _embedding_identity_compatible_for_reuse(
                        stored_identity=stored_identity,
                        expected_identity=identity_hint,
                        configured_dimensions=config.embedding_dimensions,
                    ) and str(existing["embedding_identity_hash"] or "") == embedding_identity_hash(stored_identity)
                except (TypeError, ValueError, json.JSONDecodeError):
                    reusable = False
            if not reusable:
                rows_to_index.append(chunk)

    if embedding_backend is not None and rows_to_index:
        response = embedding_backend.embed_texts([_embedding_text_for_chunk(chunk) for chunk in rows_to_index])
        if response.status == "embedded" and response.vectors is not None and len(response.vectors) == len(rows_to_index):
            vector_rows = []
            for chunk, vector in zip(rows_to_index, response.vectors, strict=True):
                identity = embedding_identity_from_response(response, config=config, vector=vector)
                vector_rows.append(
                    (
                        chunk.chunk_id,
                        json.dumps(vector, ensure_ascii=True),
                        len(vector),
                        str(identity["provider"]),
                        str(identity["model"]),
                        json.dumps(identity, sort_keys=True, ensure_ascii=True),
                        embedding_identity_hash(identity),
                        chunk.chunk_hash,
                        run_id,
                        generated_at,
                    )
                )
            connection.executemany(
                f"""
                INSERT INTO {vector_table} (
                    chunk_id,
                    vector_json,
                    vector_dimensions,
                    embedding_provider,
                    embedding_model,
                    embedding_identity_json,
                    embedding_identity_hash,
                    content_hash,
                    last_indexed_run_id,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(chunk_id) DO UPDATE SET
                    vector_json=excluded.vector_json,
                    vector_dimensions=excluded.vector_dimensions,
                    embedding_provider=excluded.embedding_provider,
                    embedding_model=excluded.embedding_model,
                    embedding_identity_json=excluded.embedding_identity_json,
                    embedding_identity_hash=excluded.embedding_identity_hash,
                    content_hash=excluded.content_hash,
                    last_indexed_run_id=excluded.last_indexed_run_id,
                    updated_at=excluded.updated_at
                """,
                vector_rows,
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
    return chunk.embedding_text


def _embedding_identity_compatible_for_reuse(
    *,
    stored_identity: Any,
    expected_identity: dict[str, Any] | None,
    configured_dimensions: int | None,
) -> bool:
    if not isinstance(stored_identity, dict):
        return False
    required = {"provider", "model", "dimensions", "normalize_embeddings", "encoding_strategy"}
    if not required.issubset(stored_identity):
        return False
    if expected_identity is not None:
        for field in ("provider", "model", "normalize_embeddings", "encoding_strategy"):
            if stored_identity.get(field) != expected_identity.get(field):
                return False
    if configured_dimensions is not None and int(stored_identity.get("dimensions", -1)) != configured_dimensions:
        return False
    try:
        return int(stored_identity["dimensions"]) > 0
    except (TypeError, ValueError):
        return False


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
    skipped_sources: list[dict[str, Any]],
    lexical_index: dict[str, Any],
    vector_index: dict[str, Any],
    temporal_index: dict[str, Any],
    resource_inventory: dict[str, Any],
) -> dict[str, Any]:
    return {
        "status": "success",
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
            "skipped_source_count": len(skipped_sources),
        },
        "skipped_sources": skipped_sources,
        "lexical_index": lexical_index,
        "vector_index": vector_index,
        "temporal_index": temporal_index,
        "resource_inventory": {
            "status": "valid" if resource_inventory.get("validation_status") == "valid" else resource_inventory.get("validation_status", "unknown"),
            "schema_version": resource_inventory.get("inventory_schema_version"),
            "snapshot_id": resource_inventory.get("snapshot_id"),
            "source_ingest_run_id": resource_inventory.get("source_ingest_run_id"),
            "logical_inventory_hash": resource_inventory.get("logical_inventory_hash"),
            "inventory_policy_hash": resource_inventory.get("inventory_policy_hash"),
            "note_count": (resource_inventory.get("payload") or {}).get("corpus_note_count"),
            "chunk_count": (resource_inventory.get("payload") or {}).get("corpus_chunk_count"),
            "path_depth": ((resource_inventory.get("payload") or {}).get("path_topology") or {}).get("path_depth"),
            "capabilities": {
                "exact_fts": ((resource_inventory.get("payload") or {}).get("capabilities") or {}).get("exact_fts", {}),
                "vector": ((resource_inventory.get("payload") or {}).get("capabilities") or {}).get("vector", {}),
                "graph": ((resource_inventory.get("payload") or {}).get("capabilities") or {}).get("graph", {}),
                "temporal": ((resource_inventory.get("payload") or {}).get("capabilities") or {}).get("temporal", {}),
            },
            "validation": resource_inventory.get("validation_status"),
        },
        "notes": [
            {
                "note_id": note_record.note_id,
                "source_uuid": note_record.source_uuid,
                "source_root_label": note_record.source_root_label,
                "source_root_path": note_record.source_root_path,
                "relative_path": note_record.relative_path,
                "note_path": note_record.note_path,
                "note_title": note_record.note_title,
                "note_hash": note_record.note_hash,
                "frontmatter": note_record.frontmatter,
                "frontmatter_semantics": note_record.frontmatter_semantics,
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
                "source_uuid": chunk.source_uuid,
                "source_root_label": chunk.source_root_label,
                "source_root_path": chunk.source_root_path,
                "relative_path": chunk.relative_path,
                "note_path": chunk.note_path,
                "note_title": chunk.note_title,
                "frontmatter_semantics": chunk.frontmatter_semantics,
                "section_id": chunk.section_id,
                "section_label": chunk.section_label,
                "section_kind": chunk.section_kind,
                "section_path": list(chunk.section_path),
                "section_occurrence": chunk.section_occurrence,
                "heading_level": chunk.heading_level,
                "semantic_unit_kind": chunk.semantic_unit_kind,
                "paragraph_ordinal": chunk.paragraph_ordinal,
                "split_ordinal": chunk.split_ordinal,
                "paragraph_text": chunk.paragraph_text,
                "embedding_text": chunk.embedding_text,
                "embedding_text_hash": chunk.embedding_text_hash,
                "chunk_hash": chunk.chunk_hash,
                "chunking_warnings": list(chunk.chunking_warnings),
            }
            for note_record in note_records
            for chunk in note_record.chunks
        ],
    }


def _build_failure_manifest(
    *,
    run_id: str,
    generated_at: str,
    repo_root: Path,
    data_root: Path,
    database_path: Path,
    source_roots: tuple[IngestSourceRoot, ...],
    validation_issues: list[dict[str, Any]],
    skipped_sources: list[dict[str, Any]],
    failure_stage: str = "frontmatter_validation",
    active_database_replaced: bool = False,
    prior_active_database_preserved: bool = False,
    candidate_database_cleaned: bool = True,
    error_type: str | None = None,
    error_message: str | None = None,
    cleanup_error: Exception | None = None,
    latest_success_manifest_path: Path | None = None,
) -> dict[str, Any]:
    return {
        "status": "failed",
        "run_id": run_id,
        "generated_at": generated_at,
        "repo_root": str(repo_root),
        "data_root": str(data_root),
        "database_path": str(database_path),
        "latest_success_manifest_path": str(latest_success_manifest_path) if latest_success_manifest_path else None,
        "failure_stage": failure_stage,
        "active_database_replaced": active_database_replaced,
        "prior_active_database_preserved": prior_active_database_preserved,
        "candidate_database_cleaned": candidate_database_cleaned,
        "error_type": error_type,
        "error_message": error_message,
        "cleanup_error": type(cleanup_error).__name__ if cleanup_error else None,
        "source_roots": [{"label": root.label, "path": str(root.path)} for root in source_roots],
        "summary": {
            "note_count": 0,
            "chunk_count": 0,
            "validation_issue_count": len(validation_issues),
            "skipped_source_count": len(skipped_sources),
        },
        "skipped_sources": skipped_sources,
        "validation_issues": validation_issues,
    }
