from __future__ import annotations

import re
from typing import Any

from .config import RuntimeConfig


_APPS_SHORT_ALL_CAPS_RE = re.compile(r"^[A-Z0-9 .,&'()\-]+$")
_APPS_DOI_RE = re.compile(r"^(?:doi\b|10\.\d{4,9}/)", re.IGNORECASE)
_APPS_ISBN_RE = re.compile(r"^isbn\b", re.IGNORECASE)
_APPS_PAGE_RE = re.compile(r"^(?:page\s+\d+|p\.?\s*\d+)$", re.IGNORECASE)
_APPS_YEAR_RE = re.compile(r"\(\d{4}\)")
_PROSE_MARKERS = (
    "argu",
    "because",
    "concern",
    "discuss",
    "explain",
    "interpret",
    "mention",
    "serious",
    "take",
    "therefore",
    "however",
    "while",
    "although",
)


def _normalized_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _normalized_lower_values(values: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(_normalized_text(value).lower() for value in values if _normalized_text(value))


def _has_any_marker(lowered: str, markers: tuple[str, ...]) -> bool:
    return any(marker in lowered for marker in markers)


def _has_any_prefix(lowered: str, prefixes: tuple[str, ...]) -> bool:
    return any(lowered.startswith(prefix) for prefix in prefixes)


def _has_prose_markers(lowered: str) -> bool:
    return any(marker in lowered for marker in _PROSE_MARKERS)


def is_low_signal_apparatus_text(
    text: str,
    *,
    config: RuntimeConfig,
    section_label: str | None = None,
    note_title: str | None = None,
    relative_path: str | None = None,
) -> bool:
    if not config.chunking_low_signal_apparatus_enabled:
        return False
    normalized = _normalized_text(text)
    if not normalized:
        return False
    lowered = normalized.lower()
    if "{" in normalized or "}" in normalized or "[" in normalized or "]" in normalized:
        return False

    exact_lines = _normalized_lower_values(config.chunking_low_signal_apparatus_exact_lines)
    prefixes = _normalized_lower_values(config.chunking_low_signal_apparatus_prefixes)
    contains = _normalized_lower_values(config.chunking_low_signal_apparatus_contains)
    short_all_caps_max_chars = config.chunking_low_signal_apparatus_short_all_caps_max_chars
    short_text = len(normalized) <= short_all_caps_max_chars
    has_prose_markers = _has_prose_markers(lowered)

    if _APPS_DOI_RE.match(lowered) or _APPS_ISBN_RE.match(lowered) or _APPS_PAGE_RE.match(lowered):
        return True

    if short_text and lowered in exact_lines and not has_prose_markers:
        return True

    if short_text and _has_any_prefix(lowered, prefixes) and not has_prose_markers:
        return True

    if short_text and _has_any_marker(lowered, contains) and not has_prose_markers:
        return True

    if short_text and _APPS_SHORT_ALL_CAPS_RE.match(normalized) and any(ch.isalpha() for ch in normalized):
        return True

    note_title_lower = _normalized_text(note_title).lower()
    if short_text and note_title_lower and lowered in note_title_lower and not any(ch in normalized for ch in ".:;!?"):
        return True

    if short_text and _APPS_YEAR_RE.search(lowered) and not has_prose_markers:
        return True

    if section_label:
        section_label_lower = _normalized_text(section_label).lower()
        if short_text and section_label_lower and lowered == section_label_lower and not any(ch in normalized for ch in ".:;!?"):
            return True

    if relative_path:
        relative_lower = _normalized_text(relative_path).lower()
        if short_text and any(fragment in relative_lower for fragment in ("title", "frontmatter", "cover", "copyright", "bibliograph", "isbn")):
            if (lowered in exact_lines or _has_any_prefix(lowered, prefixes) or _has_any_marker(lowered, contains)) and not has_prose_markers:
                return True

    return False
