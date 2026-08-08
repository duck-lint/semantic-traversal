"""Read-only, pre-admission observation of an authored Markdown vault.

This module deliberately does not import the production ingest pipeline.  Its
records describe source evidence and parsing observations, not semantic
objects, units, identities, or admission decisions.
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import re
import subprocess
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable

import yaml


SCHEMA_VERSION = "authored-vault-observation/v1"
LINK_RE = re.compile(r"(?P<embed>!)?\[\[(?P<body>[^\]]+)\]\]")
HEADING_RE = re.compile(r"^(?P<hashes>#{1,6})[ \t]+(?P<text>.*?)[ \t]*$")
FENCE_RE = re.compile(r"^[ \t]*(?P<marker>`{3,}|~{3,})(?P<info>.*)$")
LIST_RE = re.compile(r"^[ \t]*(?:[-+*]|\d+[.)])[ \t]+")
TABLE_SEPARATOR_RE = re.compile(r"^[ \t]*\|?[ \t]*:?-{3,}:?[ \t]*(?:\|[ \t]*:?-{3,}:?[ \t]*)+\|?[ \t]*$")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_json(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return _sha256_bytes(encoded)


def _shape(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
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
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    return str(value)


def _line_spans(text: str) -> list[tuple[int, int, str]]:
    spans: list[tuple[int, int, str]] = []
    offset = 0
    for line in text.splitlines(keepends=True):
        end = offset + len(line)
        spans.append((offset, end, line))
        offset = end
    if not spans or offset < len(text):
        spans.append((offset, len(text), text[offset:]))
    return spans


def _frontmatter(text: str) -> dict[str, Any]:
    lines = _line_spans(text)
    if not lines or lines[0][2].strip("\r\n") != "---":
        return {"status": "absent", "raw_text": None, "delimiter_span": None, "source_span": None, "keys": [], "values": {}}
    closing = next((index for index, (_, _, line) in enumerate(lines[1:], 1) if line.strip("\r\n") in {"---", "..."}), None)
    if closing is None:
        raw = text[lines[0][1]:]
        return {"status": "unterminated", "raw_text": raw, "delimiter_span": [lines[0][0], lines[0][1]], "source_span": [lines[0][0], len(text)], "keys": [], "values": {}, "parse_error": "missing closing delimiter"}
    raw = text[lines[0][1]:lines[closing][0]]
    parsed: Any = None
    error = None
    try:
        parsed = yaml.safe_load(raw)
    except Exception as exc:  # PyYAML's concrete exception type is not part of this boundary.
        error = f"{type(exc).__name__}: {exc}"
    if error:
        status = "malformed"
        values: dict[str, Any] = {}
    elif not isinstance(parsed, dict):
        status = "non_mapping"
        values = {}
    else:
        status = "valid"
        values = {str(key): _json_safe(value) for key, value in parsed.items()}
    result = {
        "status": status,
        "raw_text": raw,
        "delimiter_span": [lines[0][0], lines[closing][1]],
        "source_span": [lines[0][0], lines[closing][1]],
        "keys": sorted(values),
        "values": values,
    }
    if error:
        result["parse_error"] = error
    return result


def _uuid_observation(frontmatter: dict[str, Any]) -> dict[str, Any]:
    if "uuid" not in frontmatter.get("values", {}):
        return {"field_present": False, "raw_value": None, "value_shape": None, "parse_status": "missing"}
    raw = frontmatter["values"]["uuid"]
    status = "valid" if isinstance(raw, str) and re.fullmatch(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}", raw) else "invalid"
    return {"field_present": True, "raw_value": raw, "value_shape": _shape(raw), "parse_status": status}


def _parse_link(raw_body: str, *, embedded: bool, span: tuple[int, int]) -> dict[str, Any]:
    parts = raw_body.split("|")
    target = parts[0]
    display = parts[1] if len(parts) > 1 else None
    base, heading, block = target, None, None
    if "#" in base:
        base, heading = base.split("#", 1)
    if "^" in base:
        base, block = base.split("^", 1)
    return {
        "raw_link_text": ("!" if embedded else "") + "[[" + raw_body + "]]",
        "raw_target": target,
        "raw_target_without_fragment": base,
        "display_alias": display,
        "heading_fragment": heading,
        "block_fragment": block,
        "embedded": embedded,
        "source_span": [span[0], span[1]],
    }


def _blocks(text: str, body_start: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    lines = _line_spans(text)
    headings: list[dict[str, Any]] = []
    for start, end, line in lines:
        raw = line.rstrip("\r\n")
        match = HEADING_RE.match(raw)
        if match:
            headings.append({"level": len(match.group("hashes")), "raw_text": match.group("text"), "source_span": [start, end]})

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
            while last + 1 < len(lines) and not re.match(r"^[ \t]*" + re.escape(marker[0]) + "{" + str(len(marker)) + r",}[ \t]*$", lines[last + 1][2].rstrip("\r\n")):
                last += 1
            kind = "code_fence"
        else:
            last = index
            kind = "heading" if HEADING_RE.match(raw) else "list" if LIST_RE.match(raw) else "table" if "|" in raw and index + 1 < len(lines) and TABLE_SEPARATOR_RE.match(lines[index + 1][2].rstrip("\r\n")) else "blockquote_or_callout" if raw.lstrip().startswith(">") else "paragraph"
            if kind == "paragraph":
                while last + 1 < len(lines) and lines[last + 1][2].strip() and not HEADING_RE.match(lines[last + 1][2].rstrip("\r\n")) and not FENCE_RE.match(lines[last + 1][2].rstrip("\r\n")):
                    last += 1
        block_end = lines[last][1]
        raw_source = text[start:block_end]
        explicit_block_ids = re.findall(r"\^([A-Za-z0-9_-]+)\s*$", raw_source, flags=re.MULTILINE)
        candidates.append({"block_kind_observation": kind, "raw_markdown": raw_source, "source_span": [start, block_end], "line_start": index + 1, "line_end": last + 1, "explicit_block_ids": explicit_block_ids})
        index = last + 1
    return headings, candidates


def _runtime_policy(relative: str, patterns: Iterable[str]) -> dict[str, Any]:
    for pattern in patterns:
        if fnmatch.fnmatch(relative, pattern) or fnmatch.fnmatch("/" + relative, pattern):
            return {"excluded_by_current_runtime": True, "matched_rule": pattern}
    return {"excluded_by_current_runtime": False, "matched_rule": None}


def _git_commit(repo_root: Path | None) -> str:
    if repo_root is None:
        return "unknown"
    try:
        return subprocess.check_output(["git", "-C", str(repo_root), "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def observe(vault_root: Path, output_root: Path, *, repo_root: Path | None = None, runtime_config: Path | None = None, attestation: Path | None = None) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    vault_root = vault_root.resolve()
    output_root = output_root.resolve()
    if not vault_root.is_dir():
        raise ValueError(f"vault root is not a directory: {vault_root}")
    if output_root == vault_root or vault_root in output_root.parents:
        raise ValueError("output-root must not be inside vault-root; this keeps the source tree complete and read-only")
    patterns: list[str] = []
    if runtime_config and runtime_config.exists():
        config = yaml.safe_load(runtime_config.read_text(encoding="utf-8")) or {}
        patterns = [str(item) for item in config.get("paths", {}).get("vault_exclude_globs", [])]

    entries: list[dict[str, Any]] = []
    markdown_records: list[dict[str, Any]] = []
    source_files = sorted((path for path in vault_root.rglob("*") if path.is_file() and not path.is_symlink()), key=lambda path: path.relative_to(vault_root).as_posix())
    for path in source_files:
        relative = path.relative_to(vault_root).as_posix()
        data = path.read_bytes()
        suffix = path.suffix.lower()
        kind = "markdown" if suffix in {".md", ".markdown", ".mdown", ".mkdn"} else "binary_or_non_markdown"
        decoding = "not_attempted"
        text_value: str | None = None
        if kind == "markdown":
            try:
                text_value = data.decode("utf-8")
                decoding = "utf8"
            except UnicodeDecodeError as exc:
                decoding = "invalid_utf8"
                text_value = None
        entry = {"relative_path": relative, "source_kind": kind, "extension": suffix, "byte_size": len(data), "source_byte_hash": _sha256_bytes(data), "text_decoding_status": decoding, "observation_status": "observed", "legacy_runtime_policy": _runtime_policy(relative, patterns)}
        entries.append(entry)
        if text_value is None:
            continue
        fm = _frontmatter(text_value)
        body_start = fm.get("source_span", [0, 0])[1] if fm["status"] in {"valid", "malformed", "non_mapping", "unterminated"} else 0
        headings, blocks = _blocks(text_value, body_start)
        links = [_parse_link(match.group("body"), embedded=bool(match.group("embed")), span=(match.start(), match.end())) for match in LINK_RE.finditer(text_value)]
        markdown_records.append({"source": entry, "raw_markdown": text_value, "frontmatter": fm, "uuid": _uuid_observation(fm), "headings": headings, "block_candidates": blocks, "authored_links": links, "unsupported_syntax_observations": [], "parse_issues": ([fm["parse_error"]] if fm.get("parse_error") else [])})

    uuid_paths: dict[str, list[str]] = {}
    for record in markdown_records:
        uuid_value = record["uuid"].get("raw_value")
        if record["uuid"].get("parse_status") == "valid":
            uuid_paths.setdefault(uuid_value, []).append(record["source"]["relative_path"])
    for record in markdown_records:
        uuid_value = record["uuid"].get("raw_value")
        record["uuid"]["duplicate_source_paths"] = sorted(uuid_paths.get(uuid_value, [])) if uuid_value else []

    aliases: dict[str, list[str]] = {}
    for record in markdown_records:
        values = record["frontmatter"].get("values", {})
        for alias in values.get("aliases", []) if isinstance(values.get("aliases"), list) else ([values["aliases"]] if isinstance(values.get("aliases"), str) else []):
            aliases.setdefault(str(alias), []).append(record["source"]["relative_path"])
    lookup: dict[str, list[str]] = {}
    for record in markdown_records:
        relative = record["source"]["relative_path"]
        path = Path(relative)
        for key in {relative, relative.removesuffix(path.suffix), path.stem}:
            lookup.setdefault(key, []).append(relative)
        for alias, paths in aliases.items():
            if relative in paths:
                lookup.setdefault(alias, []).append(relative)
    records_by_path = {record["source"]["relative_path"]: record for record in markdown_records}
    for record in markdown_records:
        for link in record["authored_links"]:
            target = link["raw_target_without_fragment"].strip()
            candidates = sorted(set(lookup.get(target, [])))
            if not candidates and target.endswith(".md"):
                candidates = sorted(set(lookup.get(target[:-3], [])))
            status = "unique" if len(candidates) == 1 else "ambiguous" if candidates else "unresolved"
            target_record = records_by_path.get(candidates[0]) if len(candidates) == 1 else None
            headings = {heading["raw_text"] for heading in target_record["headings"]} if target_record else set()
            block_ids = {block_id for block in target_record["block_candidates"] for block_id in block["explicit_block_ids"]} if target_record else set()
            link["target_resolution"] = {
                "status": status,
                "source_candidates": candidates,
                "heading_fragment_status": "observed" if link["heading_fragment"] else "absent",
                "heading_target_status": "observed" if link["heading_fragment"] and link["heading_fragment"] in headings else "absent" if link["heading_fragment"] and status == "unique" else "unknown",
                "block_fragment_status": "observed" if link["block_fragment"] else "absent",
                "block_target_status": "observed" if link["block_fragment"] and link["block_fragment"] in block_ids else "absent" if link["block_fragment"] and status == "unique" else "unknown",
            }

    inventory_identity = _sha256_json([{key: entry[key] for key in ("relative_path", "source_kind", "extension", "byte_size", "source_byte_hash", "text_decoding_status", "observation_status")} for entry in entries])
    logical_sources = [{key: value for key, value in entry.items() if key != "legacy_runtime_policy"} for entry in entries]
    logical_identity = _sha256_json({"sources": logical_sources, "markdown": [{key: value for key, value in record.items() if key != "raw_markdown"} for record in markdown_records]})
    snapshot = _sha256_json({"sources": [{"relative_path": e["relative_path"], "source_byte_hash": e["source_byte_hash"], "byte_size": e["byte_size"], "source_kind": e["source_kind"]} for e in entries]})
    generated_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    observation = {"observation_schema_version": SCHEMA_VERSION, "observer_repository_commit": _git_commit(repo_root), "generated_at": generated_at, "vault_source_identity": snapshot, "corpus_snapshot_identity": snapshot, "inventory_identity": inventory_identity, "logical_observation_hash": logical_identity, "record_count": len(entries), "source_inventory": entries, "markdown_observations": markdown_records}

    counts = Counter(entry["source_kind"] for entry in entries)
    fm_counts = Counter(record["frontmatter"]["status"] for record in markdown_records)
    uuid_counts = Counter(record["uuid"]["parse_status"] for record in markdown_records)
    resolution_counts = Counter(link["target_resolution"]["status"] for record in markdown_records for link in record["authored_links"])
    value_shapes = Counter(value_shape for record in markdown_records for value_shape in (_shape(value) for value in record["frontmatter"].get("values", {}).values()))
    block_kinds = Counter(block["block_kind_observation"] for record in markdown_records for block in record["block_candidates"])
    summary = {
        "observation_schema_version": SCHEMA_VERSION,
        "observer_repository_commit": observation["observer_repository_commit"],
        "corpus_snapshot_identity": snapshot,
        "logical_observation_hash": logical_identity,
        "inventory_identity": inventory_identity,
        "record_count": len(entries),
        "file_kind_counts": dict(sorted(counts.items())),
        "frontmatter_parse_status_counts": dict(sorted(fm_counts.items())),
        "uuid_status_counts": dict(sorted(uuid_counts.items())),
        "value_shape_counts": dict(sorted(value_shapes.items())),
        "heading_count": sum(len(r["headings"]) for r in markdown_records),
        "block_candidate_count": sum(len(r["block_candidates"]) for r in markdown_records),
        "block_kind_counts": dict(sorted(block_kinds.items())),
        "authored_link_count": sum(len(r["authored_links"]) for r in markdown_records),
        "target_resolution_status_counts": dict(sorted(resolution_counts.items())),
        "ambiguous_target_count": resolution_counts["ambiguous"],
        "unresolved_target_count": resolution_counts["unresolved"],
        "unsupported_syntax_count": sum(len(r["unsupported_syntax_observations"]) for r in markdown_records),
        "parse_issue_count": sum(len(r["parse_issues"]) for r in markdown_records),
        "current_runtime_exclusion_match_count": sum(entry["legacy_runtime_policy"]["excluded_by_current_runtime"] for entry in entries),
        "source_reference_hashes": [_sha256_json({"relative_path": e["relative_path"], "source_byte_hash": e["source_byte_hash"]}) for e in entries],
    }

    baseline = {"python_repository_commit": "unknown", "corpus_snapshot_identity": snapshot, "inventory_identity": inventory_identity, "materialization_or_index_identity": "unknown", "private_uat_suite_identities": "unknown", "report_identities": "unknown", "demonstrated_capabilities": "unknown", "known_failures": "unknown", "known_architectural_violations": "unknown", "unmeasured_areas": "unknown", "private_artifact_hashes_or_locations": "unknown", "attestation_timestamp": "unknown"}
    if attestation and attestation.exists():
        supplied = yaml.safe_load(attestation.read_text(encoding="utf-8")) or {}
        if not isinstance(supplied, dict):
            raise ValueError("attestation must contain a mapping")
        for key in baseline:
            if key in supplied and key not in {"corpus_snapshot_identity", "inventory_identity"}:
                baseline[key] = _json_safe(supplied[key])
    output_root.mkdir(parents=True, exist_ok=True)
    (output_root / "authored-vault-observation.json").write_text(json.dumps(observation, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (output_root / "authored-vault-summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (output_root / "recovery-baseline-manifest.json").write_text(json.dumps(baseline, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return observation, summary, baseline


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Observe an authored vault without semantic admission or production ingest.", allow_abbrev=False)
    parser.add_argument("--vault-root", required=True, type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--repo-root", type=Path)
    parser.add_argument("--runtime-config", type=Path)
    parser.add_argument("--attestation", type=Path)
    args = parser.parse_args(argv)
    observe(args.vault_root, args.output_root, repo_root=args.repo_root, runtime_config=args.runtime_config, attestation=args.attestation)
    print(json.dumps({"status": "pass", "output_root": str(args.output_root.resolve())}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
