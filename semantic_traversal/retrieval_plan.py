from __future__ import annotations

import re
from typing import Any

PLAN_TOKEN_RE = re.compile(r"[A-Za-z0-9']+")
PLAN_STOP_WORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "but",
    "by",
    "do",
    "for",
    "from",
    "how",
    "i",
    "if",
    "in",
    "into",
    "is",
    "it",
    "me",
    "my",
    "of",
    "on",
    "or",
    "our",
    "please",
    "the",
    "to",
    "with",
    "you",
    "your",
}
SEARCH_INTENT_WORDS = {
    "find",
    "grep",
    "list",
    "locate",
    "mention",
    "mentions",
    "occurrence",
    "occurrences",
    "search",
    "show",
}
DISCOURSE_OPERATOR_WORDS = {
    "compare",
    "compares",
    "comparison",
    "contrast",
    "contrasts",
    "difference",
    "differences",
    "relation",
    "relations",
    "related",
    "relate",
    "relates",
    "similarity",
    "similarities",
    "vs",
    "versus",
}
SCOPE_JOURNAL_WORDS = {"daily", "dailies", "journal", "journals", "journaled", "entry", "entries"}
SEARCH_NOISE_WORDS = SEARCH_INTENT_WORDS | SCOPE_JOURNAL_WORDS | {"exact", "exactly", "literal", "literally", "term", "terms", "text"}
MAX_CARRIED_FOCUS_TERMS = 8
_CARRY_NOISE_TERMS = {
    "assistant_response_snippet",
    "chunk_id",
    "feels",
    "feel",
    "has",
    "have",
    "match_reason",
    "note",
    "notes",
    "note_id",
    "paragraph_text",
    "raw_user_input",
    "retrieval_observation",
    "scope_match",
    "selected_chunk_ids",
    "selected_note_titles",
    "selected_section_labels",
    "source_layers",
    "text",
    "that",
    "them",
    "thems",
    "thing",
    "things",
    "this",
    "they",
    "thinking",
    "what",
    "how",
    "why",
}
KNOWN_PLANNER_FIELDS = {
    "intent_type",
    "scope_requests",
    "concepts",
    "resolved_referents",
    "literal_terms",
    "semantic_queries",
    "lexical_queries",
    "graph_seeds",
    "retrieval_layers",
}


def _quoted_terms_from_text(text: str) -> list[str]:
    return [match.strip() for match in re.findall(r"[\"“”']([^\"“”']+)[\"“”']", text) if match.strip()]


def _term_is_explicitly_requested(text: str, term: str) -> bool:
    lowered = f" {text.lower()} "
    normalized = term.lower().strip()
    if not normalized:
        return False
    markers = (
        f" word {normalized} ",
        f" the word {normalized} ",
        f" term {normalized} ",
        f" the term {normalized} ",
        f" mention {normalized} ",
        f" mentions {normalized} ",
        f" mentioned {normalized} ",
        f" occurrence {normalized} ",
        f" occurrences {normalized} ",
    )
    return any(marker in lowered for marker in markers)


def _clean_discourse_operator_terms(text: str, terms: list[str]) -> tuple[list[str], list[str]]:
    quoted_terms = {term.lower() for term in _quoted_terms_from_text(text)}
    cleaned: list[str] = []
    demoted: list[str] = []
    for term in terms:
        normalized = str(term).strip()
        if not normalized:
            continue
        lowered = normalized.lower()
        if lowered in DISCOURSE_OPERATOR_WORDS and lowered not in quoted_terms and not _term_is_explicitly_requested(text, lowered):
            if normalized not in demoted:
                demoted.append(normalized)
            continue
        if normalized not in cleaned:
            cleaned.append(normalized)
    return cleaned, demoted


def is_comparison_intent(text: str) -> bool:
    return bool(set(collect_plan_terms(text)).intersection(DISCOURSE_OPERATOR_WORDS))


def _looks_like_date_or_page_label(term: str) -> bool:
    lowered = term.lower().strip()
    return bool(
        re.fullmatch(r"\d{4}-\d{2}-\d{2}", lowered)
        or re.fullmatch(r"\d{4}/\d{2}/\d{2}", lowered)
        or re.fullmatch(r"(?:page\s+\d+|p\.?\s*\d+)", lowered)
        or re.fullmatch(r"\d{1,3}", lowered)
    )


def _is_compact_focus_term(term: str) -> bool:
    cleaned = str(term or "").strip()
    if not cleaned:
        return False
    lowered = cleaned.lower()
    if lowered in _CARRY_NOISE_TERMS:
        return False
    if len(cleaned) > 120:
        return False
    if "{" in cleaned or "}" in cleaned or "[" in cleaned or "]" in cleaned:
        return False
    if _looks_like_date_or_page_label(cleaned):
        return False
    if cleaned.startswith("selected_") or cleaned in {"raw_user_input", "assistant_response_snippet"}:
        return False
    if len(cleaned.split()) == 1 and len(cleaned) < 3:
        return False
    return True


def _append_compact_term(terms: list[str], candidate: str, *, max_terms: int) -> None:
    cleaned = str(candidate or "").strip()
    if not _is_compact_focus_term(cleaned):
        return
    if cleaned not in terms:
        terms.append(cleaned)


def _append_query_terms(terms: list[str], candidate: str, *, max_terms: int) -> None:
    for token in collect_plan_terms(candidate):
        if token in SEARCH_NOISE_WORDS or token in DISCOURSE_OPERATOR_WORDS:
            continue
        _append_compact_term(terms, token, max_terms=max_terms)
        if len(terms) >= max_terms:
            return


def _focus_carry_terms(
    *,
    active_focus: dict[str, Any] | None,
    recent_semantic_turns: list[dict[str, Any]] | None,
    max_terms: int = MAX_CARRIED_FOCUS_TERMS,
) -> list[str]:
    carried: list[str] = []

    def consume_source(source: dict[str, Any]) -> None:
        for field in ("resolved_referents", "concepts", "literal_terms", "graph_seeds", "lexical_queries"):
            for item in coerce_string_list(source.get(field)):
                _append_compact_term(carried, item, max_terms=max_terms)
                if len(carried) >= max_terms:
                    return
        query = str(source.get("query") or "").strip()
        if query:
            _append_query_terms(carried, query, max_terms=max_terms)
            if len(carried) >= max_terms:
                return
        for field in ("semantic_queries",):
            for item in coerce_string_list(source.get(field)):
                _append_query_terms(carried, item, max_terms=max_terms)
                if len(carried) >= max_terms:
                    return

    if isinstance(active_focus, dict):
        consume_source(active_focus)
    if isinstance(recent_semantic_turns, list):
        for turn in recent_semantic_turns[-2:]:
            if isinstance(turn, dict):
                consume_source(turn)
            if len(carried) >= max_terms:
                break

    if len(carried) < max_terms:
        for source in (active_focus or {}, *(recent_semantic_turns[-2:] if isinstance(recent_semantic_turns, list) else [])):
            if not isinstance(source, dict):
                continue
            for field in ("selected_note_titles", "selected_section_labels"):
                for item in coerce_string_list(source.get(field)):
                    _append_compact_term(carried, item, max_terms=max_terms)
                    if len(carried) >= max_terms:
                        return carried[:max_terms]
    return carried[:max_terms]


def coerce_string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        cleaned = value.strip()
        return [cleaned] if cleaned else []
    if not isinstance(value, list):
        cleaned = str(value).strip()
        return [cleaned] if cleaned else []
    items: list[str] = []
    for entry in value:
        candidate: Any = entry
        if isinstance(entry, dict):
            candidate = (
                entry.get("term")
                or entry.get("query")
                or entry.get("label")
                or entry.get("resolved_to")
                or entry.get("surface_form")
                or entry.get("value")
            )
        if candidate is None:
            continue
        cleaned = str(candidate).strip()
        if cleaned and cleaned not in items:
            items.append(cleaned)
    return items


def collect_plan_terms(text: str) -> list[str]:
    terms: list[str] = []
    seen: set[str] = set()
    for token in PLAN_TOKEN_RE.findall(text.lower()):
        if len(token) < 3 or token.isdigit() or token in PLAN_STOP_WORDS:
            continue
        if token not in seen:
            seen.add(token)
            terms.append(token)
    return terms


def is_search_intent(text: str) -> bool:
    tokens = set(collect_plan_terms(text))
    lowered = f" {text.lower()} "
    return bool(tokens.intersection(SEARCH_INTENT_WORDS)) or " where do i mention " in lowered or " where have i mentioned " in lowered


def scope_requests_from_text(text: str) -> list[str]:
    tokens = set(collect_plan_terms(text))
    if tokens.intersection(SCOPE_JOURNAL_WORDS):
        return ["journal"]
    lowered = text.lower()
    if any(word in lowered for word in (" journal ", " journals ", " daily ", " dailies ", " entries ", " entry ")):
        return ["journal"]
    return []


def concepts_from_text(text: str) -> list[str]:
    tokens = collect_plan_terms(text)
    return [term for term in tokens if term not in SEARCH_NOISE_WORDS]


def literal_terms_from_text(text: str) -> list[str]:
    quoted_terms = _quoted_terms_from_text(text)
    if quoted_terms:
        literal_terms = list(dict.fromkeys(quoted_terms))
    else:
        literal_terms = [term for term in collect_plan_terms(text) if term not in SEARCH_NOISE_WORDS]
    cleaned_terms, _ = _clean_discourse_operator_terms(text, literal_terms)
    return cleaned_terms


def _literal_term_entries(terms: list[str], *, required: bool) -> list[dict[str, Any]]:
    return [
        {
            "term": term,
            "match": "case_insensitive_substring",
            "required": required,
        }
        for term in terms
        if str(term).strip()
    ]


def _retrieval_layer(
    operator: str,
    *,
    required: bool = False,
    limit: int | None = None,
    depth: int | None = None,
    return_total_count: bool | None = None,
    mode: str | None = None,
) -> dict[str, Any]:
    layer: dict[str, Any] = {"operator": operator, "required": required}
    if limit is not None:
        layer["limit"] = limit
    if depth is not None:
        layer["depth"] = depth
    if return_total_count is not None:
        layer["return_total_count"] = return_total_count
    if mode is not None:
        layer["mode"] = mode
    return layer


def build_default_retrieval_plan(
    *,
    raw_user_input: str,
    query: str,
    concepts: list[str],
    scope_requests: list[str],
    graph_seeds: list[str],
    resolved_referents: list[str] | None = None,
    planner_defaults: dict[str, Any],
) -> dict[str, Any]:
    search_intent = is_search_intent(raw_user_input)
    literal_terms = literal_terms_from_text(raw_user_input) if search_intent else []
    semantic_queries = [query.strip()] if query.strip() else []
    lexical_queries = literal_terms if literal_terms else concepts
    lexical_queries, _ = _clean_discourse_operator_terms(raw_user_input, lexical_queries)
    intent_type = "exact_search" if search_intent else "semantic_traversal"
    if scope_requests:
        intent_type = "scoped_exact_search" if search_intent else "scoped_semantic_traversal"

    layers: list[dict[str, Any]] = []
    if search_intent:
        layers.append(_retrieval_layer("exact_chunk_search", required=True, limit=planner_defaults["exact_limit"], return_total_count=planner_defaults["exact_return_total_count"]))
        layers.append(_retrieval_layer("lexical_chunk_search", limit=planner_defaults["lexical_limit"]))
        layers.append(_retrieval_layer("vector_search", limit=planner_defaults["vector_limit"]))
        layers.append(_retrieval_layer("graph_expand", depth=planner_defaults["graph_depth"]))
    else:
        layers.append(_retrieval_layer("lexical_chunk_search", limit=planner_defaults["lexical_limit"]))
        layers.append(_retrieval_layer("vector_search", limit=planner_defaults["vector_limit"]))
        layers.append(_retrieval_layer("graph_expand", depth=planner_defaults["graph_depth"]))

    return {
        "intent_type": intent_type,
        "scope_requests": list(dict.fromkeys(scope_requests)),
        "concepts": list(dict.fromkeys(concepts)),
        "resolved_referents": list(dict.fromkeys(resolved_referents or [])),
        "literal_terms": _literal_term_entries(literal_terms, required=search_intent),
        "semantic_queries": list(dict.fromkeys(semantic_queries)),
        "lexical_queries": list(dict.fromkeys(lexical_queries)),
        "graph_seeds": list(dict.fromkeys(graph_seeds)),
        "retrieval_layers": layers,
    }


def _coerce_literal_terms(value: Any, fallback: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return list(fallback)
    terms: list[dict[str, Any]] = []
    for entry in value:
        if isinstance(entry, dict):
            term = str(entry.get("term") or entry.get("value") or "").strip()
            match = str(entry.get("match") or "case_insensitive_substring").strip() or "case_insensitive_substring"
            # Preserve unsupported modes for runtime diagnostics. Coercing an
            # accepted field here would make the compiler request look
            # executed when the requested behavior was never run.
            required = bool(entry.get("required"))
        else:
            term = str(entry).strip()
            match = "case_insensitive_substring"
            required = False
        if term and not any(item["term"] == term for item in terms):
            terms.append({"term": term, "match": match, "required": required})
    return terms or list(fallback)


def _coerce_retrieval_layers(value: Any, fallback: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return list(fallback)
    layers: list[dict[str, Any]] = []
    for entry in value:
        if not isinstance(entry, dict):
            continue
        operator = str(entry.get("operator") or "").strip()
        if not operator:
            continue
        layer: dict[str, Any] = {"operator": operator, "required": bool(entry.get("required"))}
        if "limit" in entry:
            try:
                layer["limit"] = int(entry["limit"])
            except (TypeError, ValueError):
                # Preserve invalid accepted input for runtime-owned
                # defaulting diagnostics instead of silently dropping it.
                layer["limit"] = entry.get("limit")
        if "depth" in entry:
            try:
                layer["depth"] = max(0, int(entry["depth"]))
            except (TypeError, ValueError):
                layer["depth"] = entry.get("depth")
        if "return_total_count" in entry:
            layer["return_total_count"] = bool(entry.get("return_total_count"))
        if "mode" in entry:
            layer["mode"] = str(entry.get("mode") or "").strip()
        for field in ("anchor_types", "authorities"):
            if field in entry and isinstance(entry.get(field), list):
                layer[field] = [str(item).strip() for item in entry[field] if str(item).strip()]
        for field in ("before", "after", "start", "end", "direction"):
            if field in entry:
                layer[field] = entry.get(field)
        if "include_unresolved" in entry:
            layer["include_unresolved"] = bool(entry.get("include_unresolved"))
        layers.append(layer)
    return layers or list(fallback)


def canonicalize_retrieval_plan(value: Any, *, fallback: dict[str, Any], planner_defaults: dict[str, Any], raw_user_input: str | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    diagnostics: dict[str, Any] = {"ignored_planner_fields": []}
    if not isinstance(value, dict):
        result = dict(fallback)
        if raw_user_input:
            literal_terms, demoted = _clean_discourse_operator_terms(raw_user_input, [entry.get("term", "") for entry in result.get("literal_terms", []) if isinstance(entry, dict)])
            if demoted:
                diagnostics["demoted_discourse_operators"] = demoted
            allowed_terms = set(literal_terms)
            result["literal_terms"] = [entry for entry in result.get("literal_terms", []) if isinstance(entry, dict) and entry.get("term") in allowed_terms]
            lexical_queries, demoted_lexical = _clean_discourse_operator_terms(raw_user_input, coerce_string_list(result.get("lexical_queries")))
            if demoted_lexical:
                diagnostics.setdefault("demoted_discourse_operators", [])
                diagnostics["demoted_discourse_operators"] = list(dict.fromkeys([*diagnostics.get("demoted_discourse_operators", []), *demoted_lexical]))
            result["lexical_queries"] = lexical_queries
        return result, diagnostics

    retired_fields = sorted(set(value) & {"selection_policy", "claim_policy"})
    unknown_fields = sorted(set(value) - KNOWN_PLANNER_FIELDS - set(retired_fields))
    if unknown_fields:
        diagnostics["ignored_planner_fields"] = unknown_fields
    if retired_fields:
        diagnostics["retired_planner_fields"] = [
            {"field": field, "action": "retired", "reason": "compiler does not own runtime selection or claim policy"}
            for field in retired_fields
        ]

    result = {
        "intent_type": str(value.get("intent_type") or fallback.get("intent_type") or "semantic_traversal"),
        "scope_requests": coerce_string_list(value.get("scope_requests")) or coerce_string_list(fallback.get("scope_requests")),
        "concepts": coerce_string_list(value.get("concepts")) or coerce_string_list(fallback.get("concepts")),
        "resolved_referents": coerce_string_list(value.get("resolved_referents")) or coerce_string_list(fallback.get("resolved_referents")),
        "literal_terms": _coerce_literal_terms(value.get("literal_terms"), fallback.get("literal_terms", [])),
        "semantic_queries": coerce_string_list(value.get("semantic_queries")) or coerce_string_list(fallback.get("semantic_queries")),
        "lexical_queries": coerce_string_list(value.get("lexical_queries")) or coerce_string_list(fallback.get("lexical_queries")),
        "graph_seeds": coerce_string_list(value.get("graph_seeds")) or coerce_string_list(fallback.get("graph_seeds")),
        "retrieval_layers": _coerce_retrieval_layers(value.get("retrieval_layers"), fallback.get("retrieval_layers", [])),
    }
    if raw_user_input:
        literal_terms, demoted = _clean_discourse_operator_terms(raw_user_input, [entry["term"] for entry in result["literal_terms"]])
        if demoted:
            diagnostics["demoted_discourse_operators"] = demoted
        allowed_terms = set(literal_terms)
        result["literal_terms"] = [entry for entry in result["literal_terms"] if entry["term"] in allowed_terms]
        lexical_queries, demoted_lexical = _clean_discourse_operator_terms(raw_user_input, result["lexical_queries"])
        if demoted_lexical:
            diagnostics.setdefault("demoted_discourse_operators", [])
            diagnostics["demoted_discourse_operators"] = list(dict.fromkeys([*diagnostics.get("demoted_discourse_operators", []), *demoted_lexical]))
        result["lexical_queries"] = lexical_queries
    return result, diagnostics


def retrieval_plan_layer(plan: dict[str, Any], operator: str) -> dict[str, Any] | None:
    for layer in plan.get("retrieval_layers", []):
        if isinstance(layer, dict) and layer.get("operator") == operator:
            return layer
    return None
