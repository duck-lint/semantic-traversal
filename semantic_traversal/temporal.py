"""Typed temporal anchors and interval helpers.

This module deliberately owns parsing and relation semantics, while runtime
configuration owns which fields and authorities are admissible.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

YEAR_RE = re.compile(r"^\d{4}$")
MONTH_RE = re.compile(r"^\d{4}-\d{2}$")
DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
PRECISIONS = {"year", "month", "day", "datetime"}
AUTHORITIES = {"explicit_primary", "explicit_secondary", "operational_low"}
ANCHOR_TYPES = {"journal_entry", "authored", "created", "modified", "encountered_or_read", "publication", "event"}


@dataclass(frozen=True)
class TemporalAnchor:
    anchor_id: str
    note_id: str
    chunk_id: str | None
    anchor_type: str
    canonical_start: str | None
    canonical_end: str | None
    precision: str | None
    source_field: str
    original_source_value: str
    authority: str
    parsing_status: str
    conflict_group: str | None
    unresolved: bool
    diagnostic_reason: str | None
    ingest_run_id: str


def stable_anchor_id(*, note_id: str, chunk_id: str | None, source_field: str, original_value: str, anchor_type: str, authority: str) -> str:
    payload = json.dumps({"note_id": note_id, "chunk_id": chunk_id, "source_field": source_field, "original_value": original_value, "anchor_type": anchor_type, "authority": authority}, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return "anchor-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def _datetime_bounds(value: str) -> tuple[str, str, str]:
    if YEAR_RE.fullmatch(value):
        start = datetime(int(value), 1, 1, tzinfo=UTC)
        end = datetime(int(value) + 1, 1, 1, tzinfo=UTC) - timedelta(microseconds=1)
        return start.isoformat().replace("+00:00", "Z"), end.isoformat().replace("+00:00", "Z"), "year"
    if MONTH_RE.fullmatch(value):
        year, month = (int(part) for part in value.split("-"))
        start = datetime(year, month, 1, tzinfo=UTC)
        end = datetime(year + (month == 12), 1 if month == 12 else month + 1, 1, tzinfo=UTC) - timedelta(microseconds=1)
        return start.isoformat().replace("+00:00", "Z"), end.isoformat().replace("+00:00", "Z"), "month"
    if DAY_RE.fullmatch(value):
        start = datetime.fromisoformat(value).replace(tzinfo=UTC)
        end = start + timedelta(days=1) - timedelta(microseconds=1)
        return start.isoformat().replace("+00:00", "Z"), end.isoformat().replace("+00:00", "Z"), "day"
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    parsed = parsed.astimezone(UTC)
    canonical = parsed.isoformat().replace("+00:00", "Z")
    return canonical, canonical, "datetime"


def parse_temporal_value(value: Any) -> tuple[str | None, str | None, str | None, str]:
    if not isinstance(value, str):
        raise ValueError("temporal value must be a string")
    text = value.strip()
    if not text:
        raise ValueError("temporal value is empty")
    if "/" in text or re.fullmatch(r"\d{1,2}-\d{1,2}-\d{2,4}", text):
        raise ValueError("ambiguous locale-dependent temporal value")
    try:
        start, end, precision = _datetime_bounds(text)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"invalid ISO temporal value: {text}") from exc
    return start, end, precision, "valid"


def build_temporal_anchors(*, note_id: str, frontmatter: dict[str, Any], mappings: dict[str, Any], ingest_run_id: str) -> tuple[list[TemporalAnchor], list[dict[str, Any]]]:
    anchors: list[TemporalAnchor] = []
    diagnostics: list[dict[str, Any]] = []
    for field in sorted(mappings):
        mapping = mappings[field] if isinstance(mappings[field], dict) else {}
        anchor_type = str(mapping.get("anchor_type") or "").strip()
        authority = str(mapping.get("authority") or "").strip()
        raw = frontmatter.get(field)
        values = raw if isinstance(raw, list) else [raw]
        if raw is None:
            continue
        for value in values:
            original = str(value)
            anchor_id = stable_anchor_id(note_id=note_id, chunk_id=None, source_field=field, original_value=original, anchor_type=anchor_type, authority=authority)
            try:
                start, end, precision, status = parse_temporal_value(value)
                reason = None
            except ValueError as exc:
                start = end = precision = None
                status = "ambiguous" if "ambiguous" in str(exc) else "invalid"
                reason = str(exc)
                diagnostics.append({"anchor_id": anchor_id, "note_id": note_id, "source_field": field, "status": status, "reason": reason})
            anchors.append(TemporalAnchor(anchor_id, note_id, None, anchor_type, start, end, precision, field, original, authority, status, None, status != "valid", reason, ingest_run_id))

    groups: dict[tuple[str, str, str | None], list[TemporalAnchor]] = defaultdict(list)
    for anchor in anchors:
        if anchor.parsing_status == "valid":
            groups[(anchor.note_id, anchor.anchor_type, anchor.chunk_id)].append(anchor)
    updated: list[TemporalAnchor] = []
    for anchor in anchors:
        group = groups.get((anchor.note_id, anchor.anchor_type, anchor.chunk_id), [])
        values = {(item.canonical_start, item.canonical_end) for item in group}
        conflict = len(values) > 1
        conflict_group = None
        if conflict:
            conflict_group = "conflict-" + hashlib.sha256(json.dumps(sorted(values), separators=(",", ":")).encode()).hexdigest()[:16]
        updated.append(TemporalAnchor(**{**anchor.__dict__, "conflict_group": conflict_group, "unresolved": anchor.unresolved or conflict}))
    return updated, diagnostics


def temporal_diagnostics(anchors: list[TemporalAnchor], issues: list[dict[str, Any]]) -> dict[str, Any]:
    valid = [a for a in anchors if a.parsing_status == "valid"]
    counts = lambda values: dict(sorted(Counter(values).items()))
    return {
        "status": "valid",
        "total_anchor_count": len(anchors),
        "anchored_note_count": len({a.note_id for a in valid}),
        "anchored_chunk_count": len({a.chunk_id for a in valid if a.chunk_id}),
        "unanchored_note_count": 0,
        "valid_count": len(valid),
        "invalid_count": sum(a.parsing_status == "invalid" for a in anchors),
        "ambiguous_count": sum(a.parsing_status == "ambiguous" for a in anchors),
        "conflict_count": len({a.conflict_group for a in anchors if a.conflict_group}),
        "counts_by_anchor_type": counts(a.anchor_type for a in valid),
        "counts_by_authority": counts(a.authority for a in valid),
        "counts_by_precision": counts(a.precision for a in valid),
        "invalid_samples": issues[:10],
        "conflict_samples": [{"anchor_id": a.anchor_id, "note_id": a.note_id, "source_field": a.source_field, "conflict_group": a.conflict_group} for a in anchors if a.conflict_group][:10],
    }


def relation_for_anchor(anchor: dict[str, Any], *, mode: str, boundary_start: str | None = None, boundary_end: str | None = None, include_unresolved: bool = False) -> str | None:
    if anchor.get("parsing_status") != "valid" or (anchor.get("unresolved") and not include_unresolved):
        return "unresolved" if include_unresolved and anchor.get("parsing_status") == "valid" else None
    start, end = str(anchor["canonical_start"]), str(anchor["canonical_end"])
    if mode == "before" and boundary_start:
        return "definite" if end < boundary_start else None
    if mode == "after" and boundary_end:
        return "definite" if start > boundary_end else None
    if mode == "between" and boundary_start and boundary_end:
        return "definite" if start >= boundary_start and end <= boundary_end else None
    return "definite"
