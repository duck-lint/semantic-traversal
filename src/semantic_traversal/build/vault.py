"""Whole-vault Markdown discovery and object-level validation."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from ruamel.yaml.error import YAMLError

from .parser import BuildConfig, NoteParseError, ParsedNote, parse_note


@dataclass(frozen=True)
class CorpusFailure:
    """An independently detected candidate failure, before repair serialization."""

    kind: str
    message: str
    source_paths: tuple[str, ...]


@dataclass(frozen=True)
class VaultParseResult:
    """Parsed candidate notes plus aggregate validation failures."""

    notes: tuple[ParsedNote, ...]
    failures: tuple[CorpusFailure, ...]
    build_config: BuildConfig

    @property
    def is_valid(self) -> bool:
        """Whether this candidate is a valid completed parsed corpus."""

        return not self.failures


def _relative_path(path: Path, vault_root: Path) -> str:
    return path.relative_to(vault_root).as_posix()


def _excluded(relative_path: PurePosixPath, config: BuildConfig) -> bool:
    """Apply exact path-prefix exclusion, not leaf-name or glob matching."""

    for excluded_folder in config.excluded_folders:
        excluded = PurePosixPath(excluded_folder.replace("\\", "/"))
        if relative_path.parts[: len(excluded.parts)] == excluded.parts:
            return True
    return False


def discover_markdown_notes(vault_root: str | Path, build_config: BuildConfig) -> tuple[Path, ...]:
    """Discover included Markdown notes in deterministic vault-relative order."""

    root = Path(vault_root)
    if not root.is_dir():
        raise NoteParseError(f"vault root is not a directory: {root}")

    discovered: list[tuple[str, Path]] = []
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix != ".md":
            continue
        relative = PurePosixPath(_relative_path(path, root))
        if not _excluded(relative, build_config):
            discovered.append((relative.as_posix(), path))
    discovered.sort(key=lambda item: item[0])
    return tuple(path for _, path in discovered)


def parse_vault(vault_root: str | Path, build_config: BuildConfig) -> VaultParseResult:
    """Parse all included notes and aggregate parse and UUID failures."""

    root = Path(vault_root)
    notes: list[ParsedNote] = []
    failures: list[CorpusFailure] = []
    for path in discover_markdown_notes(root, build_config):
        source_path = (_relative_path(path, root),)
        try:
            note = parse_note(path, vault_root=root, build_config=build_config, require_uuid=False)
            notes.append(note)
            if note.semantic_object.uuid is None:
                failures.append(
                    CorpusFailure(
                        "missing_uuid",
                        f"frontmatter must contain one non-empty {build_config.uuid_field}",
                        source_path,
                    )
                )
        except (NoteParseError, OSError, UnicodeError, YAMLError) as exc:
            failures.append(CorpusFailure("parse", str(exc), source_path))

    by_uuid: dict[str, list[str]] = defaultdict(list)
    for note in notes:
        if note.semantic_object.uuid is not None:
            by_uuid[note.semantic_object.uuid].append(note.semantic_object.authored_path)
    for uuid, paths in sorted(by_uuid.items()):
        if len(paths) > 1:
            collision_paths = tuple(sorted(paths))
            failures.append(
                CorpusFailure(
                    "duplicate_uuid",
                    f"duplicate {build_config.uuid_field} value {uuid!r}",
                    collision_paths,
                )
            )

    return VaultParseResult(tuple(notes), tuple(failures), build_config)
