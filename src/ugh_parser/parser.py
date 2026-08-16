from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
import re
from types import MappingProxyType
from typing import Any, Iterable

from markdown_it import MarkdownIt
from mdit_py_plugins.gfm import gfm_plugin
from ruamel.yaml import YAML
from ruamel.yaml.constructor import DuplicateKeyError
from ruamel.yaml.error import YAMLError


class NoteParseError(ValueError):
    """The note cannot be represented by the scoped parse contract."""


@dataclass(frozen=True)
class SemanticIdentifierDeclaration:
    """One authored semantic-identifier admission and its meaning."""

    field_name: str
    description: str


@dataclass(frozen=True)
class BuildConfig:
    """Parser-affecting build configuration loaded from the build seed."""

    vault_name: str
    uuid_field: str
    excluded_folders: tuple[str, ...]
    semantic_identifiers: tuple[SemanticIdentifierDeclaration, ...]

    def __post_init__(self) -> None:
        seen: set[str] = set()
        for declaration in self.semantic_identifiers:
            if not isinstance(declaration, SemanticIdentifierDeclaration):
                raise NoteParseError("semantic_identifiers must contain declarations")
            if not isinstance(declaration.field_name, str) or not declaration.field_name.strip():
                raise NoteParseError("semantic identifier field names must be non-empty strings")
            if declaration.field_name in seen:
                raise NoteParseError(f"duplicate semantic identifier declaration: {declaration.field_name!r}")
            if not isinstance(declaration.description, str) or not declaration.description.strip():
                raise NoteParseError(
                    f"semantic identifier description must be a non-empty string: {declaration.field_name!r}"
                )
            seen.add(declaration.field_name)
        if self.uuid_field in seen:
            raise NoteParseError("uuid_field cannot also be a semantic identifier field")
        for excluded_folder in self.excluded_folders:
            _validate_excluded_folder(excluded_folder)

    @property
    def semantic_identifier_fields(self) -> tuple[str, ...]:
        """The ordered admission set derived from semantic_identifiers."""

        return tuple(declaration.field_name for declaration in self.semantic_identifiers)

    @property
    def semantic_identifier_descriptions(self) -> MappingProxyType:
        """An immutable mapping of authored meanings by admitted field name."""

        return MappingProxyType({
            declaration.field_name: declaration.description
            for declaration in self.semantic_identifiers
        })


def _validate_excluded_folder(value: str) -> None:
    """Require an exact, normalized vault-relative directory path."""

    if not isinstance(value, str) or not value.strip():
        raise NoteParseError(f"excluded_folders entry is not a vault-relative directory path: {value!r}")
    normalized = value.replace("\\", "/")
    raw_parts = tuple(normalized.split("/"))
    path = PurePosixPath(normalized)
    if (
        path.is_absolute()
        or ".." in raw_parts
        or (path.parts and ":" in path.parts[0])
    ):
        raise NoteParseError(f"excluded_folders entry is not a vault-relative directory path: {value!r}")


def _load_config_values(path: str | Path) -> dict[str, Any]:
    yaml = YAML(typ="safe", pure=True)
    yaml.version = (1, 2)
    try:
        values = yaml.load(Path(path).read_text(encoding="utf-8")) or {}
    except DuplicateKeyError as exc:
        raise NoteParseError("build configuration contains duplicate keys") from exc
    if not isinstance(values, dict):
        raise NoteParseError("build configuration must contain a mapping")
    if "semantic_identifier_fields" in values:
        raise NoteParseError(
            "build configuration must use semantic_identifiers declarations, not semantic_identifier_fields"
        )
    return dict(values)


def _semantic_identifier_declarations(
    values: dict[str, Any],
    *,
    allow_incomplete: bool = False,
) -> tuple[SemanticIdentifierDeclaration, ...]:
    raw = values.get("semantic_identifiers", {})
    if not isinstance(raw, dict):
        raise NoteParseError("build configuration semantic_identifiers must be a mapping")
    declarations: list[SemanticIdentifierDeclaration] = []
    seen: set[str] = set()
    for field_name, entry in raw.items():
        if not isinstance(field_name, str) or not field_name.strip():
            raise NoteParseError("semantic identifier field names must be non-empty strings")
        if field_name in seen:
            raise NoteParseError(f"duplicate semantic identifier declaration: {field_name!r}")
        seen.add(field_name)
        if not isinstance(entry, dict):
            raise NoteParseError(f"semantic identifier declaration must be a mapping: {field_name!r}")
        if "description" not in entry:
            if not allow_incomplete:
                raise NoteParseError(f"semantic identifier declaration lacks description: {field_name!r}")
            description = ""
        else:
            description = entry["description"]
            if not isinstance(description, str):
                raise NoteParseError(f"semantic identifier description must be a string: {field_name!r}")
        if not allow_incomplete and not description.strip():
            raise NoteParseError(
                f"semantic identifier description must be non-empty: {field_name!r}"
            )
        declarations.append(SemanticIdentifierDeclaration(field_name, description))
    return tuple(declarations)


def _missing_semantic_identifier_descriptions(path: str | Path) -> tuple[str, ...]:
    """Return all incomplete meanings for catalog-command validation."""

    values = _load_config_values(path)
    declarations = _semantic_identifier_declarations(values, allow_incomplete=True)
    return tuple(sorted(
        declaration.field_name
        for declaration in declarations
        if not declaration.description.strip()
    ))


def load_build_config(path: str | Path) -> BuildConfig:
    """Load the typed configuration used by note parsing.

    Exclusions are retained as configuration data, but enumeration and
    exclusion policy belong to the later whole-vault assembly stage.
    """

    values = _load_config_values(path)

    def required_string(name: str) -> str:
        value = values.get(name)
        if not isinstance(value, str) or not value.strip():
            raise NoteParseError(f"build configuration requires a non-empty {name}")
        return value

    def string_tuple(name: str) -> tuple[str, ...]:
        value = values.get(name, [])
        if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
            raise NoteParseError(f"build configuration {name} must be a list of non-empty strings")
        return tuple(value)

    uuid_field = required_string("uuid_field")
    return BuildConfig(
        vault_name=required_string("vault_name"),
        uuid_field=uuid_field,
        excluded_folders=string_tuple("excluded_folders"),
        semantic_identifiers=_semantic_identifier_declarations(values),
    )


@dataclass(frozen=True)
class Wikilink:
    """An authored wikilink, retained without target resolution."""

    raw: str
    target: str
    label: str
    target_region_fragment: str | None


@dataclass(frozen=True)
class Embed:
    """An authored Obsidian embed retained without materialization/resolution."""

    raw: str
    target: str
    label: str
    target_region_fragment: str | None


@dataclass(frozen=True)
class FrontmatterField:
    """An admitted field with authored presence/blank/value distinction."""

    name: str
    state: str  # absent, present_blank, or present_value
    value: Any = None


@dataclass(frozen=True)
class SemanticObject:
    uuid: str | None
    authored_path: str
    path_hierarchy: tuple[str, ...]
    frontmatter: dict[str, Any]
    admitted_fields: tuple[FrontmatterField, ...]


@dataclass(frozen=True)
class HeadingRegion:
    region_id: str
    level: int
    heading: str
    raw_markdown: str
    region_path: tuple[str, ...]
    parent_region_id: str | None
    parsed_text: str
    address_text: str


@dataclass(frozen=True)
class SemanticUnit:
    local_order: int
    source_object_uuid: str | None
    authored_path: str
    path_hierarchy: tuple[str, ...]
    region_path: tuple[str, ...]
    raw_markdown: str
    parsed_text: str
    wikilinks: tuple[Wikilink, ...]
    embeds: tuple[Embed, ...]


@dataclass(frozen=True)
class ParsedNote:
    semantic_object: SemanticObject
    regions: tuple[HeadingRegion, ...]
    units: tuple[SemanticUnit, ...]


_EMBED = re.compile(r"!\[\[([^\]|#]+)(?:#([^\]|]+))?(?:\|([^\]]+))?\]\]")
_WIKILINK = re.compile(r"\[\[([^\]|#]+)(?:#([^\]|]+))?(?:\|([^\]]+))?\]\]")
_CALLOUT = re.compile(r"^\[!([A-Za-z0-9_-]+)\](?:[ \t]+|$)")


def _wikilink_rule(state: Any, silent: bool) -> bool:
    """markdown-it inline rule for Obsidian wikilinks.

    It runs as an inline rule, so code spans are already consumed by the
    upstream parser and cannot accidentally become wikilink tokens.
    """

    embed_match = _EMBED.match(state.src, state.pos)
    if embed_match is not None:
        if silent:
            return True
        token = state.push("embed", "", 0)
        target = embed_match.group(1)
        label = embed_match.group(3) or target
        token.content = label
        token.attrs = {
            "target": target,
            "target_region_fragment": embed_match.group(2),
            "label": label,
            "raw": embed_match.group(0),
        }
        state.pos = embed_match.end()
        return True

    match = _WIKILINK.match(state.src, state.pos)
    if match is None or (state.pos > 0 and state.src[state.pos - 1] == "\\"):
        return False
    if silent:
        return True
    token = state.push("wikilink", "", 0)
    token.content = match.group(3) or match.group(1)
    token.attrs = {
        "target": match.group(1),
        "label": match.group(3) or match.group(1),
        "target_region_fragment": match.group(2),
        "raw": match.group(0),
    }
    state.pos = match.end()
    return True


def _markdown_parser() -> MarkdownIt:
    parser = MarkdownIt("commonmark")
    # GFM's table rule is the supported markdown-it-py block extension used
    # here; without it a pipe table is only a paragraph.
    gfm_plugin(parser)
    parser.inline.ruler.before("text", "semantic_traversal_wikilink", _wikilink_rule)
    return parser


def _frontmatter(source: str) -> tuple[dict[str, Any], int]:
    lines = source.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        raise NoteParseError("note must begin with YAML frontmatter")
    end = next((i for i, line in enumerate(lines[1:], 1) if line.strip() == "---"), None)
    if end is None:
        raise NoteParseError("frontmatter closing delimiter is missing")
    yaml = YAML(typ="safe", pure=True)
    yaml.version = (1, 2)
    try:
        values = yaml.load("".join(lines[1:end])) or {}
    except DuplicateKeyError as exc:
        raise NoteParseError("frontmatter contains duplicate keys") from exc
    if not isinstance(values, dict):
        raise NoteParseError("frontmatter must contain a mapping")
    return dict(values), end + 1


def _path_data(authored_path: str) -> tuple[str, ...]:
    normalized = authored_path.replace("\\", "/")
    path = PurePosixPath(normalized)
    if path.is_absolute() or ".." in path.parts:
        raise NoteParseError("authored path must be a relative, vault-scoped path")
    return path.parts


def _admitted_fields(frontmatter: dict[str, Any], names: Iterable[str]) -> tuple[FrontmatterField, ...]:
    fields = []
    for name in names:
        if name not in frontmatter:
            fields.append(FrontmatterField(name, "absent"))
        elif frontmatter[name] is None:
            fields.append(FrontmatterField(name, "present_blank", frontmatter[name]))
        else:
            fields.append(FrontmatterField(name, "present_value", frontmatter[name]))
    return tuple(fields)


def _root_block_spans(tokens: list[Any], body_lines: list[str]) -> list[tuple[int, int, Any]]:
    """Return root Markdown blocks; nesting, not blank lines, defines spans."""

    spans: list[tuple[int, int, Any]] = []
    depth = 0
    current: tuple[int, Any] | None = None
    current_end = 0
    for token in tokens:
        token_map = token.map
        if depth == 0 and token_map is not None and token.type != "heading_close":
            if current is not None:
                spans.append((current[0], current_end, current[1]))
                current = None
            current = (token_map[0], token)
            current_end = token_map[1]
        if current is not None and token_map is not None:
            current_end = max(current_end, token_map[1])
        depth += token.nesting
        if depth < 0:
            raise NoteParseError("Markdown parser produced invalid block nesting")
    if current is not None:
        spans.append((current[0], current_end, current[1]))
    return [(start, end, token) for start, end, token in spans if end > start and end <= len(body_lines)]


def _parsed_text_and_links(parser: MarkdownIt, raw: str, *, callout: bool = False) -> tuple[str, tuple[Wikilink, ...], tuple[Embed, ...]]:
    """Extract visible text from parsed Markdown blocks, not source lines."""

    tokens = parser.parse(raw)
    text_parts: list[str] = []
    links: list[Wikilink] = []
    embeds: list[Embed] = []
    for token in tokens:
        if token.type == "inline" and token.children:
            inline_parts: list[str] = []
            for child in token.children:
                if child.type == "wikilink":
                    attrs = child.attrs or {}
                    links.append(Wikilink(attrs["raw"], attrs["target"], attrs["label"], attrs["target_region_fragment"]))
                    inline_parts.append(child.content)
                elif child.type == "embed":
                    attrs = child.attrs or {}
                    embeds.append(Embed(attrs["raw"], attrs["target"], attrs["label"], attrs["target_region_fragment"]))
                elif child.type in {"text", "code_inline", "softbreak", "hardbreak"}:
                    inline_parts.append(child.content if child.type != "softbreak" else "\n")
            if inline_parts:
                text_parts.append("".join(inline_parts))
        elif token.type in {"code_block", "fence"}:
            text_parts.append(token.content)
    # Block-level inline tokens are separate Markdown structures.  Joining
    # them with a newline preserves lexical separation without authored syntax.
    parsed_text = "\n".join(part for part in text_parts if part)
    if callout:
        parsed_text = _CALLOUT.sub("", parsed_text, count=1)
    return parsed_text, tuple(links), tuple(embeds)


def _heading_texts(parser: MarkdownIt, raw: str) -> tuple[str, str]:
    """Extract rendered and addressed text from a parsed heading structure."""

    tokens = parser.parse(raw)
    parsed_parts: list[str] = []
    address_parts: list[str] = []
    for token in tokens:
        if token.type != "inline" or not token.children:
            continue
        parsed_inline: list[str] = []
        address_inline: list[str] = []
        for child in token.children:
            if child.type == "wikilink":
                attrs = child.attrs or {}
                parsed_inline.append(child.content)
                address_inline.append(attrs["target"])
                if child.content != attrs["target"]:
                    address_inline.append(" " + child.content)
            elif child.type in {"text", "code_inline", "softbreak", "hardbreak"}:
                value = child.content if child.type != "softbreak" else "\n"
                parsed_inline.append(value)
                address_inline.append(value)
        if parsed_inline:
            parsed_parts.append("".join(parsed_inline))
        if address_inline:
            address_parts.append("".join(address_inline))
    return "\n".join(parsed_parts), "\n".join(address_parts)


def parse_note(
    path: str | Path,
    *,
    vault_root: str | Path,
    build_config: BuildConfig,
    require_uuid: bool = True,
) -> ParsedNote:
    """Parse exactly one Markdown note using the supplied BuildConfig declarations."""

    source_path = Path(path)
    source = source_path.read_text(encoding="utf-8")
    frontmatter, body_start = _frontmatter(source)
    uuid = frontmatter.get(build_config.uuid_field)
    if not isinstance(uuid, str) or not uuid.strip():
        if require_uuid:
            raise NoteParseError(f"frontmatter must contain one non-empty {build_config.uuid_field}")
        uuid = None
    try:
        authored_path = source_path.relative_to(Path(vault_root)).as_posix()
    except ValueError as exc:
        raise NoteParseError("note path must be beneath vault_root") from exc
    hierarchy = _path_data("/".join(PurePosixPath(authored_path).parts[:-1]))
    semantic_object = SemanticObject(uuid, authored_path, hierarchy, frontmatter, _admitted_fields(frontmatter, build_config.semantic_identifier_fields))

    body = "".join(source.splitlines(keepends=True)[body_start:])
    body_lines = body.splitlines(keepends=True)
    parser = _markdown_parser()
    tokens = parser.parse(body)
    for index, token in enumerate(tokens):
        if token.type != "blockquote_open":
            continue
        inline = next((candidate for candidate in tokens[index + 1 :] if candidate.type == "inline"), None)
        if inline is not None and _CALLOUT.match(inline.content):
            token.type = "callout_open"
            token.attrs = {"kind": _CALLOUT.match(inline.content).group(1)}
            closing = next((candidate for candidate in tokens[index + 1 :] if candidate.type == "blockquote_close"), None)
            if closing is not None:
                closing.type = "callout_close"
    regions: list[HeadingRegion] = []
    active: list[HeadingRegion] = []
    units: list[SemanticUnit] = []
    for start, end, token in _root_block_spans(tokens, body_lines):
        raw = "".join(body_lines[start:end])
        if token.type == "heading_open":
            level = int(token.tag[1:])
            inline = next(t for t in tokens[tokens.index(token) + 1 :] if t.type == "inline")
            parsed_text, address_text = _heading_texts(parser, raw)
            active[:] = [region for region in active if region.level < level]
            region = HeadingRegion(
                f"region-{len(regions) + 1:04d}",
                level,
                parsed_text,
                raw,
                tuple(r.region_id for r in active),
                active[-1].region_id if active else None,
                parsed_text,
                address_text,
            )
            regions.append(region)
            active.append(region)
        else:
            is_callout = token.type == "callout_open"
            parsed, links, embeds = _parsed_text_and_links(parser, raw, callout=is_callout)
            units.append(SemanticUnit(len(units) + 1, uuid, authored_path, hierarchy, tuple(r.region_id for r in active), raw, parsed, links, embeds))
    return ParsedNote(semantic_object, tuple(regions), tuple(units))
