#!/usr/bin/env python3

from __future__ import annotations

import argparse
import difflib
import json
import re
import unicodedata
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Sequence

# =============================================================================
# MANUAL STRUCTURE PROFILE
# =============================================================================
# This is the only authority for promoted Markdown headings.
#
# Add expected headings in top-to-bottom order of appearance. Write them however
# is easiest, usually lowercase. Matching is case-insensitive, punctuation-light,
# whitespace-insensitive, and accent-insensitive. Output headings are normalized
# to title case, with common roman numerals preserved as uppercase.
#
# Accepted forms:
#   "preface"                  -> level 2 heading, output "Preface"
#   (2, "part i")              -> level 2 heading, output "Part I"
#   (3, "of the social pact")  -> level 3 heading, output "Of the Social Pact"
#
# If a book has repeated section names, list the repeated heading each time it is
# expected. The matcher only advances forward; it will not match headings out of
# order.
EXPECTED_HEADINGS: list[str | tuple[int, str]] = [
    # Example:
    # "preface",
    # (2, "part i"),
    # (3, "of the origin of language"),
]

DEFAULT_HEADING_LEVEL = 2
MAX_HEADING_LOOKAHEAD_LINES = 4
HEADING_FUZZY_RATIO = 0.94
ENABLE_FUZZY_HEADING_MATCH = True

# =============================================================================
# CONSERVATIVE CLEANUP SETTINGS
# =============================================================================
DROP_STANDALONE_PAGE_NUMBERS = True
DROP_REPEATED_TITLE_RUNNING_HEADERS = True
FLAG_CANDIDATE_HEADINGS = True
DETECT_DUPLICATES_BY_DEFAULT = False
DUPLICATE_COMPARE_MAX_CHARS = 2400
DUPLICATE_WINDOW = 25

TOKEN_REPAIRS: dict[str, str] = {
    # Conservative browser/PDF extraction spacing repairs.
    # Keep this explicit. Do not add broad regex repairs here unless you are okay
    # with them touching philosophical terms and proper names.
    "ofthe": "of the",
    "ofwhite": "of white",
    "ofwhiteness": "of whiteness",
    "orwhether": "or whether",
    "byvirtue": "by virtue",
    "frommy": "from my",
    "Mytheory": "My theory",
    "Myrepresentation": "My representation",
    "Frommyrepresentation": "From my representation",
    "halfof": "half of",
    "partof": "part of",
    "activity ofthe": "activity of the",
    "virtue ofthe": "virtue of the",
    "law ofcausality": "law of causality",
    "rays oflight": "rays of light",
    "one halfis": "one half is",
    "sucha": "such a",
    "white,is": "white, is",
    "whichwere": "which were",
    "geometric figures,which": "geometric figures, which",
    "experience,which": "experience, which",
    "however,the": "however, the",
    "moreweighty": "more weighty",
    "fromTheodorus": "from Theodorus",
    "don'tlet": "don't let",
    "Youmust": "You must",
    "Butthat": "But that",
    "Ithink": "I think",
    "Andwhat": "And what",
    "Sot it": "So it",
    "Well,is": "Well, is",
    "tellyou": "tell you",
    "theother": "the other",
    "injust": "in just",
    "butwhite": "but white",
    "oughtwe": "ought we",
    "doesthat": "does that",
}

SMALL_TITLE_WORDS = {
    "a", "an", "and", "as", "at", "but", "by", "for", "from", "in", "into",
    "nor", "of", "on", "or", "per", "the", "to", "up", "via", "with",
}
ROMAN_NUMERAL_RE = re.compile(r"^(?=[ivxlcdm]+$)[ivxlcdm]+$", re.I)
SPEAKER_RE = re.compile(r"^[A-Z][A-Z'’.-]{1,25}:\s*")
TIMELINE_ENTRY_RE = re.compile(r"^\s*(1[789]\d{2}|20\d{2})\s*[-–]\s+.+")
# Do not treat standalone Roman numerals as page numbers here. They often
# carry real book structure (for example PART / I or CHAPTER / IV), and the
# manual expected-heading matcher needs to see them before cleanup can decide.
PAGE_NUMBER_RE = re.compile(r"^\s*(?:[i1][o0]|\d{1,4})\s*$", re.I)
STANDALONE_STEPHANUS_RE = re.compile(r"^\s*(?:\d{3,4}[a-eA-Eа-сА-С]?|[a-eA-Eа-сА-С])\s*$")
INLINE_STEPHANUS_RE = re.compile(r"\b\d{3,4}\s*[a-eA-Eа-сА-С]\b")
TRAILING_SINGLE_REF_RE = re.compile(r"([*!?;:])\s+[b-eB-EсС]\s*$")
LEADING_SINGLE_REF_BEFORE_SPEAKER_RE = re.compile(r"^\s*[a-eA-Eа-сА-С]\s+(?=[A-Z][A-Z'’.-]{1,25}:)")
ALL_CAPS_CANDIDATE_RE = re.compile(r"^[A-Z0-9][A-Z0-9 '\-–—?:;,.&()]+$")
PART_CHAPTER_CANDIDATE_RE = re.compile(
    r"^\s*(?:part|book|chapter|section|appendix|preface|introduction|conclusion|notes?|contents)\b",
    re.I,
)
SECTION_SYMBOL_CANDIDATE_RE = re.compile(r"^\s*§\s*\d+\b")
DOT_LEADER_OR_TOC_PAGE_RE = re.compile(r"(?:\.{2,}|\s+\d{1,4}\s*$)")
NUMERIC_FIGURE_JUNK_RE = re.compile(r"^\s*(?:\d+(?:\.\d+)?\s+){2,}\d+(?:\.\d+)?\s*$")
SUSPICIOUS_RE = re.compile(
    r"(\b\w{1,2}[-–]\w{1,2}\b|\bff\w+|\bNense\b|\bRoth\b|\bsoome\b|\bt0\b|\bto0\b|\basso\b)"
)


@dataclass
class Edit:
    kind: str
    line_number: int | None
    before: str
    after: str
    reason: str


@dataclass
class ExpectedHeading:
    ordinal: int
    level: int
    source_text: str
    display_text: str
    match_key: str


@dataclass
class Heading:
    level: int
    text: str
    source_line: int
    expected_ordinal: int | None = None
    matched_source_text: str | None = None
    fuzzy_ratio: float | None = None


@dataclass
class CandidateHeading:
    line_number: int
    text: str
    confidence: str
    reason: str
    promoted: bool = False
    matched_expected_ordinal: int | None = None


@dataclass
class ReviewFlag:
    kind: str
    line_number: int | None
    text: str
    reason: str


@dataclass
class DuplicateCandidate:
    first_paragraph_index: int
    second_paragraph_index: int
    similarity: float
    first_preview: str
    second_preview: str
    recommendation: str


@dataclass
class Block:
    kind: str  # "paragraph" | "heading"
    line_number: int | None
    text: str
    level: int | None = None
    expected_ordinal: int | None = None
    matched_source_text: str | None = None
    fuzzy_ratio: float | None = None


def count_by_kind(values: Iterable[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))


def collapse_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def strip_accents(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def normalize_for_match(text: str) -> str:
    text = strip_accents(text)
    text = text.replace("’", "'").replace("“", '"').replace("”", '"')
    text = text.replace("–", "-").replace("—", "-")
    text = re.sub(r"[#*_`~]", "", text)
    text = re.sub(r"\s+", " ", text).strip().lower()
    text = re.sub(r"[\"'“”‘’.,;:!?()\[\]{}]", "", text)
    text = re.sub(r"\s*[-/]\s*", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def smart_titlecase(text: str) -> str:
    text = collapse_spaces(text)
    if not text:
        return text

    tokens = re.split(r"(\s+|-|–|—|/)", text.lower())
    word_positions = [i for i, token in enumerate(tokens) if token and not re.fullmatch(r"\s+|-|–|—|/", token)]
    first_word = word_positions[0] if word_positions else -1
    last_word = word_positions[-1] if word_positions else -1

    titled: list[str] = []
    for index, token in enumerate(tokens):
        if not token or re.fullmatch(r"\s+|-|–|—|/", token):
            titled.append(token)
            continue
        if ROMAN_NUMERAL_RE.match(token):
            titled.append(token.upper())
            continue
        if index not in (first_word, last_word) and token in SMALL_TITLE_WORDS:
            titled.append(token)
            continue
        if token.startswith("§"):
            titled.append(token)
            continue
        titled.append(token[:1].upper() + token[1:])

    return "".join(titled)


def compile_expected_headings(raw_headings: list[str | tuple[int, str]]) -> list[ExpectedHeading]:
    compiled: list[ExpectedHeading] = []
    for ordinal, raw_heading in enumerate(raw_headings, start=1):
        if isinstance(raw_heading, tuple):
            level, source_text = raw_heading
        else:
            level, source_text = DEFAULT_HEADING_LEVEL, raw_heading
        source_text = collapse_spaces(str(source_text))
        if not source_text:
            continue
        level = max(1, min(6, int(level)))
        compiled.append(
            ExpectedHeading(
                ordinal=ordinal,
                level=level,
                source_text=source_text,
                display_text=smart_titlecase(source_text),
                match_key=normalize_for_match(source_text),
            )
        )
    return compiled


def is_page_number_line(line: str) -> bool:
    return bool(PAGE_NUMBER_RE.match(line.strip()))


def is_reference_marker_line(line: str, reference_style: str) -> bool:
    if reference_style == "stephanus":
        return bool(STANDALONE_STEPHANUS_RE.match(line.strip()))
    return False


def is_exact_title_running_header(line: str, title: str) -> bool:
    return normalize_for_match(line.strip("*")) == normalize_for_match(title)


def looks_like_toc_entry_or_page_header(text: str) -> bool:
    stripped = collapse_spaces(text)
    if NUMERIC_FIGURE_JUNK_RE.match(stripped):
        return True
    if DOT_LEADER_OR_TOC_PAGE_RE.search(stripped):
        return True
    return False


def classify_candidate_heading(line: str) -> tuple[str, str] | None:
    stripped = collapse_spaces(line)
    if not stripped or len(stripped) > 160:
        return None
    if NUMERIC_FIGURE_JUNK_RE.match(stripped):
        return ("low", "Numeric/figure-like line; probably not a structural heading.")
    if SECTION_SYMBOL_CANDIDATE_RE.match(stripped):
        return ("medium", "Section-symbol line. Promote only if it appears in EXPECTED_HEADINGS.")
    if PART_CHAPTER_CANDIDATE_RE.match(stripped):
        return ("medium", "Part/chapter/section-like line. Promote only if expected.")
    if ALL_CAPS_CANDIDATE_RE.match(stripped) and len(stripped.split()) >= 2:
        return ("low", "All-caps line. Could be heading, running header, or page furniture.")
    return None


def clean_reference_noise(line: str, line_no: int, reference_style: str, edits: list[Edit]) -> str:
    cleaned = line
    if reference_style != "stephanus":
        return cleaned

    old = cleaned
    cleaned = INLINE_STEPHANUS_RE.sub("", cleaned)
    if cleaned != old:
        edits.append(Edit(
            kind="reference_marker_removed",
            line_number=line_no,
            before=old,
            after=cleaned,
            reason="Removed inline Stephanus-style reference marker such as 142a / 143C.",
        ))

    old = cleaned
    cleaned = LEADING_SINGLE_REF_BEFORE_SPEAKER_RE.sub("", cleaned)
    if cleaned != old:
        edits.append(Edit(
            kind="reference_marker_removed",
            line_number=line_no,
            before=old,
            after=cleaned,
            reason="Removed leading single-letter margin marker before speaker label.",
        ))

    old = cleaned
    cleaned2 = TRAILING_SINGLE_REF_RE.sub(r"\1", cleaned)
    if cleaned2 != cleaned:
        cleaned = cleaned2
        edits.append(Edit(
            kind="reference_marker_removed",
            line_number=line_no,
            before=old,
            after=cleaned,
            reason="Removed low-risk trailing single-letter Stephanus margin marker.",
        ))

    return cleaned


def _apply_explicit_token_repair(text: str, before: str, after: str) -> str:
    # Compact OCR/browser glitches such as "ofthe" should only be repaired as
    # whole tokens. Phrase-level repairs keep exact replacement semantics because
    # their spaces/punctuation already make accidental mid-token matches unlikely.
    if re.fullmatch(r"[\w'’]+", before):
        return re.sub(rf"(?<!\w){re.escape(before)}(?!\w)", after, text)
    return text.replace(before, after)


def apply_token_repairs(text: str, source_line: int | None, edits: list[Edit]) -> str:
    repaired = text
    for before, after in TOKEN_REPAIRS.items():
        updated = _apply_explicit_token_repair(repaired, before, after)
        if updated != repaired:
            old = repaired
            repaired = updated
            edits.append(Edit(
                kind="token_spacing_repair",
                line_number=source_line,
                before=old,
                after=repaired,
                reason=f"Explicit conservative repair: {before!r} -> {after!r}.",
            ))

    old = repaired
    repaired = re.sub(r"([,;:])(?=[A-Za-z])", r"\1 ", repaired)
    repaired = re.sub(r"([a-z])\.([A-Z])", r"\1. \2", repaired)
    repaired = re.sub(r"\s+", " ", repaired).strip()
    if repaired != old:
        edits.append(Edit(
            kind="punctuation_spacing_repair",
            line_number=source_line,
            before=old,
            after=repaired,
            reason="Inserted obvious missing spaces after punctuation and collapsed repeated whitespace.",
        ))
    return repaired


def should_start_new_paragraph(previous: str, current: str) -> bool:
    prev = previous.strip()
    cur = current.strip()
    if not prev or not cur:
        return True
    if SPEAKER_RE.match(cur):
        return True
    if cur.startswith(("- ", "– ", "— ")) and len(cur) < 160:
        return True
    if TIMELINE_ENTRY_RE.match(cur):
        return True
    if re.search(r"[.!?][\"')\]]?$", prev) and cur[:1].isupper():
        return True
    return False


def should_join(previous: str, current: str) -> bool:
    if should_start_new_paragraph(previous, current):
        return False
    prev = previous.rstrip()
    cur = current.lstrip()
    if prev.endswith("-") and cur[:1].islower():
        return True
    if prev.endswith((",", ";", ":", "—", "–")):
        return True
    if cur[:1].islower():
        return True
    if len(prev) >= 35 and not re.search(r"[.!?;:]$", prev):
        return True
    return False


def split_embedded_speakers(text: str) -> list[str]:
    parts = re.split(r"\s+(?=[A-Z][A-Z'’.-]{1,25}:\s)", text)
    return [part.strip() for part in parts if part.strip()]


def preprocess_lines(raw_lines: list[str], title: str, reference_style: str, edits: list[Edit]) -> list[tuple[int, str]]:
    kept: list[tuple[int, str]] = []
    title_seen = False

    for line_no, raw in enumerate(raw_lines, start=1):
        line = raw.strip()
        if not line:
            kept.append((line_no, ""))
            continue

        if DROP_STANDALONE_PAGE_NUMBERS and is_page_number_line(line):
            edits.append(Edit("page_number_removed", line_no, raw, "", "Removed standalone page-number line."))
            kept.append((line_no, ""))
            continue

        if is_reference_marker_line(line, reference_style):
            edits.append(Edit("reference_marker_line_removed", line_no, raw, "", "Removed standalone margin/reference marker line."))
            kept.append((line_no, ""))
            continue

        if DROP_REPEATED_TITLE_RUNNING_HEADERS and is_exact_title_running_header(line, title):
            if not title_seen:
                title_seen = True
                kept.append((line_no, line))
            else:
                edits.append(Edit("running_header_removed", line_no, raw, "", "Removed repeated running header matching title."))
                kept.append((line_no, ""))
            continue

        cleaned = clean_reference_noise(line, line_no, reference_style, edits)
        kept.append((line_no, cleaned.strip()))

    return kept


def collect_candidate_text(lines: list[tuple[int, str]], start_index: int, line_count: int) -> str | None:
    collected: list[str] = []
    index = start_index
    while index < len(lines) and len(collected) < line_count:
        _, text = lines[index]
        stripped = text.strip()
        if not stripped:
            break
        collected.append(stripped)
        index += 1
    if not collected:
        return None
    return collapse_spaces(" ".join(collected))


def match_expected_heading_at(
    lines: list[tuple[int, str]],
    start_index: int,
    expected: ExpectedHeading,
) -> tuple[int, str, float] | None:
    for line_count in range(1, MAX_HEADING_LOOKAHEAD_LINES + 1):
        candidate = collect_candidate_text(lines, start_index, line_count)
        if candidate is None:
            break
        candidate_key = normalize_for_match(candidate)
        if candidate_key == expected.match_key:
            return line_count, candidate, 1.0
        if ENABLE_FUZZY_HEADING_MATCH and len(candidate_key) >= 8:
            ratio = difflib.SequenceMatcher(None, candidate_key, expected.match_key).ratio()
            if ratio >= HEADING_FUZZY_RATIO:
                return line_count, candidate, round(ratio, 3)
        if len(candidate) > 180 or looks_like_toc_entry_or_page_header(candidate):
            continue
    return None


def build_blocks(
    lines: list[tuple[int, str]],
    expected_headings: list[ExpectedHeading],
    edits: list[Edit],
    candidates: list[CandidateHeading],
) -> list[Block]:
    blocks: list[Block] = []
    current = ""
    current_line: int | None = None
    expected_index = 0
    index = 0

    def flush() -> None:
        nonlocal current, current_line
        if current.strip():
            for part in split_embedded_speakers(current.strip()):
                blocks.append(Block(kind="paragraph", line_number=current_line, text=part))
        current = ""
        current_line = None

    while index < len(lines):
        line_no, line = lines[index]
        stripped = line.strip()
        if not stripped:
            flush()
            index += 1
            continue

        if FLAG_CANDIDATE_HEADINGS:
            candidate = classify_candidate_heading(stripped)
            if candidate is not None:
                confidence, reason = candidate
                candidates.append(CandidateHeading(line_no, stripped, confidence, reason))

        if expected_index < len(expected_headings):
            expected = expected_headings[expected_index]
            match = match_expected_heading_at(lines, index, expected)
            if match is not None:
                consumed_lines, matched_source_text, ratio = match
                flush()
                blocks.append(
                    Block(
                        kind="heading",
                        line_number=line_no,
                        text=expected.display_text,
                        level=expected.level,
                        expected_ordinal=expected.ordinal,
                        matched_source_text=matched_source_text,
                        fuzzy_ratio=ratio,
                    )
                )
                edits.append(Edit(
                    kind="manual_heading_promoted",
                    line_number=line_no,
                    before=matched_source_text,
                    after=f"{'#' * expected.level} {expected.display_text}",
                    reason="Promoted only because this line matched the next EXPECTED_HEADINGS entry.",
                ))
                for candidate in reversed(candidates):
                    if candidate.line_number == line_no:
                        candidate.promoted = True
                        candidate.matched_expected_ordinal = expected.ordinal
                        break
                expected_index += 1
                index += consumed_lines
                continue

        if not current:
            current = stripped
            current_line = line_no
            index += 1
            continue

        if should_join(current, stripped):
            old = current
            if current.endswith("-") and stripped[:1].islower():
                current = current[:-1] + stripped
            else:
                current = f"{current} {stripped}"
            edits.append(Edit("line_wrap_join", line_no, old + "\n" + stripped, current, "Joined probable PDF hard-wrap."))
        else:
            flush()
            current = stripped
            current_line = line_no
        index += 1

    flush()
    return blocks


def blocks_to_markdown(blocks: list[Block], title: str, edits: list[Edit], flags: list[ReviewFlag]) -> tuple[str, list[Heading]]:
    out: list[str] = [f"# {title}", ""]
    headings: list[Heading] = [Heading(1, title, 0)]

    for block in blocks:
        if block.kind == "heading":
            level = block.level or DEFAULT_HEADING_LEVEL
            heading_text = block.text
            if level == 1:
                level = 2
            out.append(f"{'#' * level} {heading_text}")
            out.append("")
            headings.append(
                Heading(
                    level=level,
                    text=heading_text,
                    source_line=block.line_number or -1,
                    expected_ordinal=block.expected_ordinal,
                    matched_source_text=block.matched_source_text,
                    fuzzy_ratio=block.fuzzy_ratio,
                )
            )
            continue

        text = apply_token_repairs(block.text, block.line_number, edits)
        if SUSPICIOUS_RE.search(text):
            flags.append(ReviewFlag(
                "suspicious_extraction_artifact",
                block.line_number,
                text[:260],
                "Token pattern may indicate PDF/browser extraction corruption.",
            ))

        if text.startswith(("- ", "– ", "— ")) and len(text) < 160:
            out.append(f"> {text}")
        else:
            out.append(text)
        out.append("")

    return "\n".join(out).strip() + "\n", headings


def markdown_paragraphs(markdown: str) -> list[str]:
    paragraphs: list[str] = []
    current: list[str] = []
    for line in markdown.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            if current:
                paragraphs.append(" ".join(current).strip())
                current = []
            continue
        if stripped.startswith(">"):
            if current:
                paragraphs.append(" ".join(current).strip())
                current = []
            paragraphs.append(stripped.lstrip("> ").strip())
            continue
        current.append(stripped)
    if current:
        paragraphs.append(" ".join(current).strip())
    return [paragraph for paragraph in paragraphs if len(paragraph) >= 80]


def corruption_score(text: str) -> int:
    score = 0
    score += len(re.findall(r"\b\w{1,2}[-–]\w{1,2}\b", text)) * 3
    score += len(re.findall(r"\bff\w+", text)) * 2
    score += len(re.findall(r"\b(?:Nense|Roth|lirmament|ffleulty|whitenesN)\b", text)) * 4
    return score


def detect_duplicates(paragraphs: list[str]) -> list[DuplicateCandidate]:
    normalized = [re.sub(r"[^a-z0-9]+", " ", paragraph.lower()).strip()[:DUPLICATE_COMPARE_MAX_CHARS] for paragraph in paragraphs]
    candidates: list[DuplicateCandidate] = []
    for first_index in range(len(normalized)):
        if len(normalized[first_index]) < 250:
            continue
        for second_index in range(first_index + 1, min(first_index + DUPLICATE_WINDOW, len(normalized))):
            if len(normalized[second_index]) < 250:
                continue
            ratio = difflib.SequenceMatcher(None, normalized[first_index], normalized[second_index]).ratio()
            if ratio >= 0.72:
                first_score = corruption_score(paragraphs[first_index])
                second_score = corruption_score(paragraphs[second_index])
                if first_score > second_score:
                    recommendation = "manual_review_prefer_second"
                elif second_score > first_score:
                    recommendation = "manual_review_prefer_first"
                else:
                    recommendation = "manual_review_no_clear_preference"
                candidates.append(
                    DuplicateCandidate(
                        first_index,
                        second_index,
                        round(ratio, 3),
                        paragraphs[first_index][:300],
                        paragraphs[second_index][:300],
                        recommendation,
                    )
                )
    return candidates


def write_review_flags(path: Path, flags: list[ReviewFlag]) -> None:
    lines = ["# Manual Review Flags", ""]
    if not flags:
        lines.append("No manual review flags generated.")
    for index, flag in enumerate(flags, start=1):
        lines += [
            f"## {index}. {flag.kind}",
            "",
            f"- Source line: {flag.line_number}",
            f"- Reason: {flag.reason}",
            "",
            "```text",
            flag.text,
            "```",
            "",
        ]
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def write_candidate_headings(path: Path, candidates: list[CandidateHeading]) -> None:
    lines = ["# Candidate Headings", ""]
    if not candidates:
        lines.append("No candidate headings generated.")
    else:
        lines.append("These were flagged for review only. They were not promoted unless they matched EXPECTED_HEADINGS in order.")
        lines.append("")
    for index, candidate in enumerate(candidates, start=1):
        status = "PROMOTED" if candidate.promoted else "not promoted"
        lines += [
            f"## {index}. {status}",
            "",
            f"- Source line: {candidate.line_number}",
            f"- Confidence: {candidate.confidence}",
            f"- Reason: {candidate.reason}",
            f"- Matched expected ordinal: {candidate.matched_expected_ordinal}",
            "",
            "```text",
            candidate.text,
            "```",
            "",
        ]
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def normalize(input_path: Path, out_dir: Path, title: str, reference_style: str, detect_duplicate_text: bool) -> None:
    raw_lines = input_path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
    out_dir.mkdir(parents=True, exist_ok=True)

    edits: list[Edit] = []
    flags: list[ReviewFlag] = []
    candidates: list[CandidateHeading] = []
    expected_headings = compile_expected_headings(EXPECTED_HEADINGS)

    preprocessed = preprocess_lines(raw_lines, title, reference_style, edits)
    blocks = build_blocks(preprocessed, expected_headings, edits, candidates)
    markdown, headings = blocks_to_markdown(blocks, title, edits, flags)
    paragraphs = markdown_paragraphs(markdown)
    duplicates = detect_duplicates(paragraphs) if detect_duplicate_text else []

    for duplicate in duplicates:
        flags.append(ReviewFlag(
            "duplicate_candidate",
            None,
            f"Paragraph {duplicate.first_paragraph_index} vs {duplicate.second_paragraph_index}; similarity={duplicate.similarity}; {duplicate.recommendation}",
            "Fuzzy near-duplicate text detected. Not automatically removed.",
        ))

    missing_expected = expected_headings[len([heading for heading in headings if heading.expected_ordinal is not None]):]
    for expected in missing_expected:
        flags.append(ReviewFlag(
            "expected_heading_not_found",
            None,
            expected.source_text,
            "An EXPECTED_HEADINGS entry was not matched in the scan text. Check spelling/order or add it manually after normalization.",
        ))

    (out_dir / "normalized.md").write_text(markdown, encoding="utf-8")
    (out_dir / "duplicate_candidates.json").write_text(json.dumps([asdict(candidate) for candidate in duplicates], indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    write_review_flags(out_dir / "manual_review_flags.md", flags)
    write_candidate_headings(out_dir / "candidate_headings.md", candidates)

    report: dict[str, Any] = {
        "input_file": str(input_path),
        "title": title,
        "reference_style": reference_style,
        "raw_line_count": len(raw_lines),
        "output_file": str(out_dir / "normalized.md"),
        "manual_expected_heading_count": len(expected_headings),
        "heading_count": len(headings),
        "headings": [asdict(heading) for heading in headings],
        "paragraph_count_estimate": len(paragraphs),
        "edit_count": len(edits),
        "edit_counts_by_kind": count_by_kind(edit.kind for edit in edits),
        "candidate_heading_count": len(candidates),
        "candidate_heading_counts_by_confidence": count_by_kind(candidate.confidence for candidate in candidates),
        "review_flag_count": len(flags),
        "review_flag_counts_by_kind": count_by_kind(flag.kind for flag in flags),
        "duplicate_detection_enabled": detect_duplicate_text,
        "duplicate_candidate_count": len(duplicates),
        "notes": [
            "Raw input was not modified.",
            "Only title plus EXPECTED_HEADINGS entries are promoted to Markdown headings.",
            "Candidate headings are reported only; they are not promoted automatically.",
            "Standalone page numbers are removed.",
            "Stephanus/reference markers are removed only when --reference-style stephanus is used.",
            "Duplicate candidates are reported only when --detect-duplicates is used; no duplicate text is automatically deleted.",
            "Suspicious extraction artifacts are flagged for review.",
        ],
    }
    (out_dir / "normalization_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(json.dumps({
        "normalized": str(out_dir / "normalized.md"),
        "report": str(out_dir / "normalization_report.json"),
        "review_flags": str(out_dir / "manual_review_flags.md"),
        "candidate_headings": str(out_dir / "candidate_headings.md"),
        "duplicate_candidates": str(out_dir / "duplicate_candidates.json"),
        "raw_line_count": len(raw_lines),
        "manual_expected_heading_count": len(expected_headings),
        "heading_count": len(headings),
        "paragraph_count_estimate": len(paragraphs),
        "edit_count": len(edits),
        "candidate_heading_count": len(candidates),
        "review_flag_count": len(flags),
        "duplicate_candidate_count": len(duplicates),
    }, indent=2, ensure_ascii=False))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Normalize browser/PDF-copied scan text into conservative Markdown.")
    parser.add_argument("input", type=Path, help="Raw browser/PDF-copy text file")
    parser.add_argument("--out-dir", type=Path, default=Path("normalized_output"), help="Output directory")
    parser.add_argument("--title", default="Untitled", help="Top-level Markdown title")
    parser.add_argument(
        "--reference-style",
        choices=["none", "stephanus"],
        default="none",
        help="Optional margin/reference marker cleanup. Use 'stephanus' for Plato-style 142a/b/c/d markers.",
    )
    parser.add_argument(
        "--detect-duplicates",
        action="store_true",
        default=DETECT_DUPLICATES_BY_DEFAULT,
        help="Enable fuzzy near-duplicate paragraph reporting. Slower on long books; never deletes text.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.input.is_file():
        raise SystemExit(f"Input file does not exist: {args.input}")
    normalize(args.input, args.out_dir, args.title, args.reference_style, args.detect_duplicates)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
