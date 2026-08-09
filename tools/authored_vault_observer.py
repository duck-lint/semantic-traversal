"""Read-only observation of a vault before any semantic admission decision.

The records in this module describe filesystem and authored-source evidence.
They deliberately do not import production ingest, runtime configuration, or
any semantic projection type. For frontmatter, the observation path is:

    raw authored YAML -> parser-native factual type -> value_shapes
                                      + deterministic JSON-safe value

Parser-native dates and datetimes are representation facts, not automatically
TemporalAnchors. Conversely, a parser-native string is not evidence that a
value lacks temporal meaning.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import unicodedata
import uuid as uuidlib
from collections import Counter
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import yaml


SCHEMA_VERSION = "vault-observation/v3"
APPARATUS_NAMESPACES = {
    ".git": "version_control",
    ".semantic-traversal": "generated_runtime_state",
    ".semantic-traversal-data": "generated_runtime_state",
    ".obsidian": "application_state",
}
MARKDOWN_EXTENSIONS = {".md", ".markdown", ".mdown", ".mkdn"}
LINK_RE = re.compile(r"(?P<embed>!)?\[\[(?P<body>[^\]]+)\]\]")
HEADING_RE = re.compile(r"^(?P<marks>#{1,6})[ \t]+(?P<text>.*?)[ \t]*$")
FENCE_RE = re.compile(r"^[ \t]*(?P<marker>`{3,}|~{3,})(?P<info>.*)$")
LIST_RE = re.compile(r"^[ \t]*(?:[-+*]|\d+[.)])[ \t]+")
TABLE_SEPARATOR_RE = re.compile(r"^[ \t]*\|?[ \t]*:?-{3,}:?[ \t]*(?:\|[ \t]*:?-{3,}:?[ \t]*)+\|?[ \t]*$")
INLINE_WIKILINK_RE = re.compile(r"(?P<embed>!)?\[\[(?P<body>[^\]]+)\]\]")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_json(value: Any) -> str:
    return _sha256_bytes(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode())


def _shape(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    # datetime is a subclass of date, so this check must remain first.
    if isinstance(value, datetime):
        return "datetime"
    if isinstance(value, date):
        return "date"
    if isinstance(value, str):
        return "string"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "mapping"
    return type(value).__name__


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    # Preserve the parser-native value mechanically for JSON transport only.
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    return str(value)


def _line_spans(text: str) -> list[tuple[int, int, str]]:
    result: list[tuple[int, int, str]] = []
    offset = 0
    for line in text.splitlines(keepends=True):
        end = offset + len(line)
        result.append((offset, end, line))
        offset = end
    if not result or offset < len(text):
        result.append((offset, len(text), text[offset:]))
    return result


def _frontmatter(text: str) -> dict[str, Any]:
    lines = _line_spans(text)
    if not lines or lines[0][2].strip("\r\n") != "---":
        return {"status": "absent", "raw_text": None, "source_span": None, "body_span": [0, len(text)], "keys": [], "values": {}, "value_shapes": {}}
    closing = next((index for index, (_, _, line) in enumerate(lines[1:], 1) if line.strip("\r\n") in {"---", "..."}), None)
    if closing is None:
        raw = text[lines[0][1]:]
        return {"status": "unterminated", "raw_text": raw, "source_span": [lines[0][0], len(text)], "body_span": [len(text), len(text)], "keys": [], "values": {}, "value_shapes": {}, "parse_issue": "missing_closing_delimiter"}
    raw = text[lines[0][1]:lines[closing][0]]
    try:
        parsed = yaml.safe_load(raw)
    except Exception as exc:  # The concrete parser exception is not part of this boundary.
        return {"status": "malformed", "raw_text": raw, "source_span": [lines[0][0], lines[closing][1]], "body_span": [lines[closing][1], len(text)], "keys": [], "values": {}, "value_shapes": {}, "parse_issue": f"yaml_parse_failed:{type(exc).__name__}"}
    if not isinstance(parsed, dict):
        return {"status": "non_mapping", "raw_text": raw, "source_span": [lines[0][0], lines[closing][1]], "body_span": [lines[closing][1], len(text)], "keys": [], "values": {}, "value_shapes": {}, "parse_issue": "frontmatter_not_mapping"}
    # Classify the original parser-native values before converting them for JSON.
    value_shapes = {str(key): _shape(value) for key, value in parsed.items()}
    values = {str(key): _json_safe(value) for key, value in parsed.items()}
    return {"status": "valid", "raw_text": raw, "source_span": [lines[0][0], lines[closing][1]], "body_span": [lines[closing][1], len(text)], "keys": sorted(values), "values": values, "value_shapes": {key: value_shapes[key] for key in sorted(value_shapes)}}


def _uuid_observation(frontmatter: dict[str, Any]) -> dict[str, Any]:
    values = frontmatter.get("values", {})
    if "uuid" not in values:
        return {"field_present": False, "raw_value": None, "value_shape": None, "parse_status": "absent"}
    raw = values["uuid"]
    result = {"field_present": True, "raw_value": raw, "value_shape": _shape(raw), "parse_status": "not_parseable", "parsed_version": None, "parsed_value": None}
    if isinstance(raw, str):
        try:
            parsed = uuidlib.UUID(raw)
        except (ValueError, AttributeError):
            pass
        else:
            result.update(parse_status="parseable", parsed_version=parsed.version, parsed_value=str(parsed))
    return result


def _parse_link(match: re.Match[str], surface: str, key_path: str | None) -> dict[str, Any]:
    body = match.group("body")
    # Obsidian-authored links in the corpus use both `|` and the escaped
    # `\|` display separator.  Consume the separator escape as syntax while
    # retaining the original markup in raw_link_markup.
    separator = re.search(r"\\?\|", body)
    if separator:
        target_with_fragments = body[:separator.start()]
        display = body[separator.end():]
    else:
        target_with_fragments = body
        display = None
    base = target_with_fragments
    heading = None
    block = None
    if "#" in base:
        base, fragment = base.split("#", 1)
        if fragment.startswith("^"):
            block = fragment[1:]
        else:
            heading = fragment
    elif "^" in base:
        base, block = base.split("^", 1)
    return {
        "source_surface": surface,
        "frontmatter_key_path": key_path,
        "raw_link_markup": match.group(0),
        "raw_target": target_with_fragments,
        "raw_target_without_fragment": base,
        "display_alias": display,
        "heading_fragment": heading,
        "block_fragment": block,
        "embedded": bool(match.group("embed")),
        "source_span": [match.start(), match.end()],
    }


def _render_inline_wikilinks(text: str) -> str:
    """Derive the visible heading text without discarding raw authored text.

    Heading addresses are evaluated against what an inline wikilink displays,
    while the raw Markdown remains the authoritative source evidence.  This is
    deliberately limited to wikilinks; it is not a general Markdown renderer.
    """
    def replace(match: re.Match[str]) -> str:
        body = match.group("body")
        separator = re.search(r"\\?\|", body)
        if separator:
            return body[separator.end():]
        if "#" in body:
            return body.split("#", 1)[0]
        return body

    return INLINE_WIKILINK_RE.sub(replace, text)


def _source_derived_inline_wikilinks(text: str) -> str:
    """Derive the demonstrated Obsidian address form using target + display.

    This is an address surface only.  It intentionally remains separate from
    ``rendered_text`` because the visible heading may omit the target portion
    of an aliased wikilink even when Obsidian accepts that target in a heading
    fragment address.
    """
    def replace(match: re.Match[str]) -> str:
        body = match.group("body")
        separator = re.search(r"\\?\|", body)
        if not separator:
            return body.split("#", 1)[0] if "#" in body else body
        target = body[:separator.start()]
        display = body[separator.end():]
        if "#" in target:
            target = target.split("#", 1)[0]
        return " ".join(part for part in (target, display) if part)

    return INLINE_WIKILINK_RE.sub(replace, text)


def _heading_address_key(text: str) -> str:
    """Apply only normalization evidenced by authored Obsidian addresses.

    The corpus demonstrates case-insensitive addressing, colon/pipe
    punctuation elision, escaped-pipe syntax, and whitespace folding.  Other
    punctuation is retained so unrelated headings do not silently collapse.
    """
    rendered = _render_inline_wikilinks(text).replace("\\|", "|")
    rendered = unicodedata.normalize("NFC", rendered)
    rendered = rendered.casefold().replace(":", "").replace("|", "")
    rendered = re.sub(r"\(\s+", "(", rendered)
    return " ".join(rendered.split())


def _heading_observation(raw_text: str, level: int, source_span: list[int]) -> dict[str, Any]:
    rendered_text = _render_inline_wikilinks(raw_text)
    source_derived_text = _source_derived_inline_wikilinks(raw_text)
    address_surfaces = []
    for surface_name, surface_text in (("raw", raw_text), ("rendered", rendered_text), ("source_derived", source_derived_text)):
        surface = {"surface": surface_name, "text": surface_text, "address_key": _heading_address_key(surface_text)}
        if not any(existing["address_key"] == surface["address_key"] for existing in address_surfaces):
            address_surfaces.append(surface)
    return {
        "level": level,
        "raw_text": raw_text,
        "rendered_text": rendered_text,
        "source_derived_text": source_derived_text,
        "address_surfaces": address_surfaces,
        "address_key": _heading_address_key(rendered_text),
        "source_span": source_span,
    }


def _frontmatter_key_for_offset(raw_text: str, offset: int) -> str | None:
    current: str | None = None
    running = 0
    for line in raw_text.splitlines(keepends=True):
        match = re.match(r"^([A-Za-z0-9_-]+)\s*:", line)
        if match:
            current = match.group(1)
        if running <= offset < running + len(line):
            return current
        running += len(line)
    return current


def _blocks(text: str, body_start: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    lines = _line_spans(text)
    headings = [_heading_observation(match.group("text"), len(match.group("marks")), [start, end])
                for start, end, line in lines if start >= body_start and (match := HEADING_RE.match(line.rstrip("\r\n")))]
    candidates: list[dict[str, Any]] = []
    index = 0
    while index < len(lines):
        start, end, line = lines[index]
        if end <= body_start or not line.strip():
            index += 1
            continue
        raw = line.rstrip("\r\n")
        fence = FENCE_RE.match(raw)
        if fence:
            marker = fence.group("marker")
            last = index
            while last + 1 < len(lines) and not re.match(r"^[ \t]*" + re.escape(marker[0]) + r"{" + str(len(marker)) + r",}[ \t]*$", lines[last + 1][2].rstrip("\r\n")):
                last += 1
            kind = "code_fence"
        else:
            last = index
            kind = "heading" if HEADING_RE.match(raw) else "list" if LIST_RE.match(raw) else "table" if "|" in raw and index + 1 < len(lines) and TABLE_SEPARATOR_RE.match(lines[index + 1][2].rstrip("\r\n")) else "blockquote_or_callout" if raw.lstrip().startswith(">") else "paragraph"
            if kind == "paragraph":
                while last + 1 < len(lines) and lines[last + 1][2].strip() and not HEADING_RE.match(lines[last + 1][2].rstrip("\r\n")) and not FENCE_RE.match(lines[last + 1][2].rstrip("\r\n")):
                    last += 1
        block_end = lines[last][1]
        raw_block = text[start:block_end]
        candidates.append({"block_kind_observation": kind, "raw_markdown": raw_block, "source_span": [start, block_end], "line_start": index + 1, "line_end": last + 1, "explicit_block_ids": re.findall(r"\^([A-Za-z0-9_-]+)\s*$", raw_block, flags=re.MULTILINE)})
        index = last + 1
    return headings, candidates


def _git_provenance() -> dict[str, Any]:
    checkout = Path(__file__).resolve().parent.parent
    try:
        commit = subprocess.check_output(["git", "-C", str(checkout), "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL).strip()
    except FileNotFoundError:
        return {"status": "git_unavailable", "commit": None}
    except subprocess.CalledProcessError:
        return {"status": "repository_not_found", "commit": None}
    return {"status": "resolved", "commit": commit}


def _relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _apparatus_root(relative: str) -> tuple[str, str] | None:
    first = relative.split("/", 1)[0]
    category = APPARATUS_NAMESPACES.get(first)
    return (first, category) if category else None


def _make_directories(vault_root: Path) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    directories: list[dict[str, Any]] = []
    for path in sorted((p for p in vault_root.rglob("*") if p.is_dir() and not p.is_symlink()), key=lambda p: _relative(p, vault_root)):
        relative = _relative(path, vault_root)
        apparatus = _apparatus_root(relative)
        directories.append({"relative_path": relative, "parent_relative_path": Path(relative).parent.as_posix() if Path(relative).parent.as_posix() != "." else "", "basename": path.name, "direct_child_directory_count": sum(child.is_dir() for child in path.iterdir()), "direct_child_file_count": sum(child.is_file() for child in path.iterdir()), "observation_category": "technical_apparatus" if apparatus else "vault_resident"})
    return directories, {item["relative_path"]: item for item in directories}


def observe(vault_root: Path, output_root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    vault_root = vault_root.resolve()
    output_root = output_root.resolve()
    if not vault_root.is_dir():
        raise ValueError(f"vault root is not a directory: {vault_root}")
    if output_root == vault_root or vault_root in output_root.parents:
        raise ValueError("output-root must not be inside vault-root")

    directories, _ = _make_directories(vault_root)
    files: list[dict[str, Any]] = []
    markdown: list[dict[str, Any]] = []
    apparatus_stats: dict[str, dict[str, Any]] = {}
    paths = sorted((p for p in vault_root.rglob("*") if p.is_file() and not p.is_symlink()), key=lambda p: _relative(p, vault_root))
    for path in paths:
        relative = _relative(path, vault_root)
        data = path.read_bytes()
        apparatus = _apparatus_root(relative)
        category = "technical_apparatus" if apparatus else "vault_resident"
        kind = "markdown" if path.suffix.lower() in MARKDOWN_EXTENSIONS else "non_markdown"
        decoding = "not_attempted"
        decoded: str | None = None
        if kind == "markdown":
            try:
                decoded = data.decode("utf-8")
                decoding = "utf8"
            except UnicodeDecodeError:
                decoding = "decode_failed"
        file_record = {"relative_path": relative, "parent_relative_path": path.parent.relative_to(vault_root).as_posix() if path.parent != vault_root else "", "basename": path.name, "extension": path.suffix.lower(), "source_kind": kind, "byte_size": len(data), "source_byte_hash": _sha256_bytes(data), "text_decoding_status": decoding, "observation_status": "observed", "observation_category": category}
        files.append(file_record)
        if category == "technical_apparatus":
            root_name, apparatus_category = apparatus
            stat = apparatus_stats.setdefault(root_name, {"relative_root_path": root_name, "apparatus_category": apparatus_category, "entry_count": 0, "file_count": 0, "directory_count": 0, "total_byte_count": 0})
            stat["file_count"] += 1
            stat["total_byte_count"] += len(data)
            continue
        if decoded is None:
            continue
        frontmatter = _frontmatter(decoded)
        body_start = frontmatter["body_span"][0]
        headings, blocks = _blocks(decoded, body_start)
        links = []
        for match in LINK_RE.finditer(decoded):
            surface = "frontmatter" if frontmatter["source_span"] and match.start() < frontmatter["source_span"][1] else "body"
            key_path = _frontmatter_key_for_offset(frontmatter["raw_text"] or "", match.start() - (frontmatter["source_span"][0] if frontmatter["source_span"] else 0)) if surface == "frontmatter" else None
            links.append(_parse_link(match, surface, key_path))
        markdown.append({"source": file_record, "raw_markdown": decoded, "frontmatter": frontmatter, "uuid": _uuid_observation(frontmatter), "headings": headings, "block_candidates": blocks, "authored_links": links, "parse_issues": ([frontmatter["parse_issue"]] if frontmatter.get("parse_issue") else [])})

    for item in directories:
        apparatus = _apparatus_root(item["relative_path"])
        if apparatus:
            root_name, _ = apparatus
            stat = apparatus_stats.setdefault(root_name, {"relative_root_path": root_name, "apparatus_category": APPARATUS_NAMESPACES[root_name], "entry_count": 0, "file_count": 0, "directory_count": 0, "total_byte_count": 0})
            stat["directory_count"] += 1
            stat["entry_count"] += 1
    for stat in apparatus_stats.values():
        stat["entry_count"] = stat["file_count"] + stat["directory_count"]

    uuid_groups: dict[str, list[str]] = {}
    for record in markdown:
        parsed = record["uuid"].get("parsed_value")
        if parsed:
            uuid_groups.setdefault(parsed, []).append(record["source"]["relative_path"])
    for record in markdown:
        parsed = record["uuid"].get("parsed_value")
        occurrences = sorted(uuid_groups.get(parsed, [])) if parsed else []
        record["uuid"]["occurrence_source_paths"] = occurrences
        record["uuid"]["duplicate_source_paths"] = occurrences if len(occurrences) > 1 else []

    address_surfaces: dict[str, dict[str, set[str]]] = {
        "exact_relative_path": {},
        "exact_relative_path_without_extension": {},
        "basename_stem": {},
        "authored_alias": {},
        "resident_basename": {},
    }

    def add_surface(surface: str, key: str, source: str) -> None:
        address_surfaces[surface].setdefault(key, set()).add(source)

    for file_record in (item for item in files if item["observation_category"] == "vault_resident"):
        path = Path(file_record["relative_path"])
        source = file_record["relative_path"]
        add_surface("exact_relative_path", source, source)
        if file_record["source_kind"] == "markdown":
            add_surface("exact_relative_path_without_extension", source.removesuffix(path.suffix), source)
            add_surface("basename_stem", path.stem, source)
        else:
            # Non-Markdown files participate only through their authored file
            # address.  Do not infer an extensionless note address for them.
            add_surface("resident_basename", path.name, source)

    for record in markdown:
        source = record["source"]["relative_path"]
        values = record["frontmatter"].get("values", {})
        raw_aliases = values.get("aliases", [])
        if isinstance(raw_aliases, str):
            raw_aliases = [raw_aliases]
        if isinstance(raw_aliases, list):
            for alias in raw_aliases:
                add_surface("authored_alias", str(alias), source)

    # Case folding is an additional observed address surface, not a rewrite
    # of either the authored target or the resident path.  Keeping it in a
    # separate namespace makes the representation-level equivalence explicit.
    for surface, lookup in list(address_surfaces.items()):
        for key, sources in lookup.items():
            address_surfaces[f"{surface}_casefold"] = address_surfaces.get(f"{surface}_casefold", {})
            address_surfaces[f"{surface}_casefold"].setdefault(key.casefold(), set()).update(sources)

    by_source = {record["source"]["relative_path"]: record for record in markdown}
    for record in markdown:
        for link in record["authored_links"]:
            target = link["raw_target_without_fragment"].strip()
            candidate_surfaces: dict[str, set[str]] = {}
            for surface, lookup in address_surfaces.items():
                lookup_target = target.casefold() if surface.endswith("_casefold") else target
                matches = set(lookup.get(lookup_target, set()))
                if lookup_target.endswith(".md") and surface == "exact_relative_path_without_extension":
                    matches.update(lookup.get(lookup_target[:-3], set()))
                if lookup_target.endswith(".md") and surface == "exact_relative_path_without_extension_casefold":
                    matches.update(lookup.get(lookup_target[:-3], set()))
                if matches:
                    candidate_surfaces[surface] = matches
            candidates = sorted({source for sources in candidate_surfaces.values() for source in sources})
            candidate_evidence = [
                {"source_path": source, "surfaces": sorted(surface for surface, sources in candidate_surfaces.items() if source in sources)}
                for source in candidates
            ]
            cardinality = "zero_candidates" if not candidates else "one_candidate" if len(candidates) == 1 else "multiple_candidates"
            target_record = by_source.get(candidates[0]) if len(candidates) == 1 else None
            heading_matches = []
            if target_record and link["heading_fragment"] is not None:
                fragment_key = _heading_address_key(link["heading_fragment"])
                for heading in target_record["headings"]:
                    matched_surfaces = [surface for surface in heading["address_surfaces"] if surface["address_key"] == fragment_key]
                    if matched_surfaces:
                        heading_matches.append((heading, matched_surfaces))
            block_ids = {block_id for block in target_record["block_candidates"] for block_id in block["explicit_block_ids"]} if target_record else set()
            link["target_candidates"] = {"cardinality": cardinality, "candidate_source_paths": candidates, "candidate_evidence": candidate_evidence}
            if link["heading_fragment"] is None:
                link["heading_target_evaluation"] = "not_applicable"
                link["heading_target_match_kind"] = "not_applicable"
                link["heading_target_matches"] = []
            elif not target_record:
                link["heading_target_evaluation"] = "not_evaluable_parent_unresolved"
                link["heading_target_match_kind"] = "not_evaluable_parent_unresolved"
                link["heading_target_matches"] = []
            elif len(heading_matches) == 1:
                match, matched_surfaces = heading_matches[0]
                link["heading_target_evaluation"] = "observed"
                link["heading_target_match_kind"] = "exact_raw" if link["heading_fragment"] == match["raw_text"] else "rendered" if link["heading_fragment"] == match["rendered_text"] else "source_derived" if any(surface["surface"] == "source_derived" for surface in matched_surfaces) else "normalized"
                link["heading_target_matches"] = [{"raw_text": match["raw_text"], "rendered_text": match["rendered_text"], "source_derived_text": match["source_derived_text"], "matched_address_surfaces": matched_surfaces, "source_span": match["source_span"]}]
            elif len(heading_matches) > 1:
                link["heading_target_evaluation"] = "ambiguous"
                link["heading_target_match_kind"] = "ambiguous"
                link["heading_target_matches"] = [{"raw_text": match["raw_text"], "rendered_text": match["rendered_text"], "source_derived_text": match["source_derived_text"], "matched_address_surfaces": matched_surfaces, "source_span": match["source_span"]} for match, matched_surfaces in heading_matches]
            else:
                link["heading_target_evaluation"] = "absent"
                link["heading_target_match_kind"] = "absent"
                link["heading_target_matches"] = []
            link["block_target_evaluation"] = "not_applicable" if link["block_fragment"] is None else "observed" if target_record and link["block_fragment"] in block_ids else "not_evaluable_parent_unresolved" if not target_record else "absent"

    resident_dirs = [d for d in directories if d["observation_category"] == "vault_resident"]
    resident_files = [f for f in files if f["observation_category"] == "vault_resident"]
    source_identity = _sha256_json({"directories": resident_dirs, "files": [{key: item[key] for key in ("relative_path", "source_kind", "extension", "byte_size", "source_byte_hash", "text_decoding_status")} for item in resident_files]})
    provenance = _git_provenance()
    observation = {"observation_schema_version": SCHEMA_VERSION, "observer_provenance": provenance, "generated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"), "vault_resident_snapshot_identity": source_identity, "directory_observations": resident_dirs, "file_observations": resident_files, "technical_apparatus_observations": sorted(apparatus_stats.values(), key=lambda item: item["relative_root_path"]), "markdown_observations": markdown, "measurement_limitations": ["Only the listed Markdown heading, block, frontmatter, wikilink, embed, and fragment forms are mechanically interpreted.", "Unsupported Markdown is retained as raw source and is not interpreted as semantic structure.", "Candidate paths are address observations, not canonical identities."]}

    fm_counts = Counter(record["frontmatter"]["status"] for record in markdown)
    uuid_counts = Counter(record["uuid"]["parse_status"] for record in markdown)
    link_counts = Counter(link["source_surface"] for record in markdown for link in record["authored_links"])
    candidate_counts = Counter(link["target_candidates"]["cardinality"] for record in markdown for link in record["authored_links"])
    summary = {"observation_schema_version": SCHEMA_VERSION, "observer_provenance": provenance, "vault_resident_snapshot_identity": source_identity, "technical_apparatus": observation["technical_apparatus_observations"], "vault_resident_topology": {"directory_count": len(resident_dirs), "file_count": len(resident_files), "top_level_entries": Counter((Path(item["relative_path"]).parts[0] for item in resident_dirs + resident_files))}, "file_kind_counts": Counter(item["source_kind"] for item in resident_files), "extension_counts": Counter(item["extension"] or "[none]" for item in resident_files), "markdown_source_count": len(markdown), "frontmatter_status_counts": fm_counts, "frontmatter_key_census": Counter(key for record in markdown for key in record["frontmatter"].get("keys", [])), "frontmatter_value_shape_census": Counter(shape for record in markdown for shape in record["frontmatter"].get("value_shapes", {}).values()), "uuid_status_counts": uuid_counts, "uuid_version_counts": Counter(str(record["uuid"]["parsed_version"]) for record in markdown if record["uuid"].get("parsed_version") is not None), "duplicate_uuid_group_count": sum(len(paths) > 1 for paths in uuid_groups.values()), "heading_level_counts": Counter(str(heading["level"]) for record in markdown for heading in record["headings"]), "authored_block_kind_counts": Counter(block["block_kind_observation"] for record in markdown for block in record["block_candidates"]), "connections": {"total_occurrence_count": sum(len(record["authored_links"]) for record in markdown), "frontmatter_occurrence_count": link_counts["frontmatter"], "body_occurrence_count": link_counts["body"], "embed_count": sum(link["embedded"] for record in markdown for link in record["authored_links"]), "display_alias_count": sum(link["display_alias"] is not None for record in markdown for link in record["authored_links"]), "heading_fragment_count": sum(link["heading_fragment"] is not None for record in markdown for link in record["authored_links"]), "block_fragment_count": sum(link["block_fragment"] is not None for record in markdown for link in record["authored_links"]), "candidate_cardinality_counts": candidate_counts}, "measurement_limitations": observation["measurement_limitations"]}
    output_root.mkdir(parents=True, exist_ok=True)
    (output_root / "vault-observation.json").write_text(json.dumps(observation, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (output_root / "vault-observation-summary.json").write_text(json.dumps(_json_safe(summary), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return observation, summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Show authored vault structure without semantic admission.", allow_abbrev=False)
    parser.add_argument("--vault-root", required=True, type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    args = parser.parse_args(argv)
    observe(args.vault_root, args.output_root)
    print(json.dumps({"status": "pass", "output_root": str(args.output_root.resolve())}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
