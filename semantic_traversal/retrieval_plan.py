from __future__ import annotations

import re
from typing import Any

PLAN_TOKEN_RE = re.compile(r"[A-Za-z0-9']+")
PLAN_STOP_WORDS = {
    "a", "an", "and", "are", "as", "at", "be", "but", "by", "do", "for", "from", "how", "i",
    "if", "in", "into", "is", "it", "me", "my", "of", "on", "or", "our", "please", "the", "to", "with",
    "you", "your",
}
SEARCH_INTENT_WORDS = {
    "find", "grep", "list", "locate", "mention", "mentions", "occurrence", "occurrences", "search", "show",
}
SCOPE_JOURNAL_WORDS = {"daily", "dailies", "journal", "journals", "journaled", "entry", "entries"}
SEARCH_NOISE_WORDS = SEARCH_INTENT_WORDS | SCOPE_JOURNAL_WORDS | {"exact", "exactly", "literal", "literally", "term", "terms", "text"}
DEFAULT_SELECTION_BUDGETS = {"exact": 12, "lexical": 6, "vector": 6, "graph": 4}


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
            candidate = entry.get("term") or entry.get("query") or entry.get("label") or entry.get("resolved_to") or entry.get("surface_form") or entry.get("value")
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


def scope_filters_from_text(text: str) -> dict[str, Any]:
    tokens = set(collect_plan_terms(text))
    if tokens.intersection(SCOPE_JOURNAL_WORDS):
        return {
            "source_label": None,
            "note_type": ["journal", "journal_entry"],
            "path_contains": ["journal", "daily"],
        }
    return {"source_label": None, "note_type": [], "path_contains": []}


def literal_terms_from_text(text: str) -> list[str]:
    quoted_terms = [match.strip() for match in re.findall(r"[\"“”']([^\"“”']+)[\"“”']", text) if match.strip()]
    if quoted_terms:
        return list(dict.fromkeys(quoted_terms))
    terms = [term for term in collect_plan_terms(text) if term not in SEARCH_NOISE_WORDS]
    return list(dict.fromkeys(terms))


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


def _retrieval_layer(operator: str, *, required: bool = False, limit: int | None = None, depth: int | None = None, return_total_count: bool = False) -> dict[str, Any]:
    layer: dict[str, Any] = {"operator": operator, "required": required}
    if limit is not None:
        layer["limit"] = limit
    if depth is not None:
        layer["depth"] = depth
    if return_total_count:
        layer["return_total_count"] = True
    return layer


def build_default_retrieval_plan(
    *,
    raw_user_input: str,
    query: str,
    retrieval_terms: list[str],
    vector_query: str,
    graph_seeds: list[str],
    resolved_referents: list[str] | None = None,
) -> dict[str, Any]:
    search_intent = is_search_intent(raw_user_input)
    scope_filters = scope_filters_from_text(raw_user_input)
    literal_terms = literal_terms_from_text(raw_user_input) if search_intent else []
    lexical_queries = literal_terms if literal_terms else retrieval_terms
    semantic_queries = [vector_query.strip() or query.strip()] if (vector_query.strip() or query.strip()) else []
    intent_type = "scoped_exact_search" if search_intent and (scope_filters.get("note_type") or scope_filters.get("path_contains")) else "exact_search" if search_intent else "semantic_traversal"

    layers: list[dict[str, Any]] = []
    if search_intent:
        layers.append(_retrieval_layer("exact_chunk_search", required=True, limit=200, return_total_count=True))
        layers.append(_retrieval_layer("lexical_chunk_search", limit=50))
        layers.append(_retrieval_layer("vector_search", limit=24))
        layers.append(_retrieval_layer("graph_expand", depth=1))
    else:
        layers.append(_retrieval_layer("lexical_chunk_search", limit=50))
        layers.append(_retrieval_layer("vector_search", limit=24))
        layers.append(_retrieval_layer("graph_expand", depth=1))

    return {
        "intent_type": intent_type,
        "resolved_referents": list(resolved_referents or []),
        "scope_filters": scope_filters,
        "literal_terms": _literal_term_entries(literal_terms, required=search_intent),
        "semantic_queries": semantic_queries,
        "lexical_queries": lexical_queries,
        "graph_seeds": graph_seeds,
        "retrieval_layers": layers,
        "selection_policy": {
            "dedupe_by": "chunk_id",
            "budgets": dict(DEFAULT_SELECTION_BUDGETS),
            "preserve_required_layers": True,
        },
        "claim_policy": {
            "coverage_claims_allowed": bool(search_intent),
            "negative_claims_require_exact_layer": True,
        },
    }


def _coerce_scope_filters(value: Any, fallback: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict):
        return dict(fallback)
    return {
        "source_label": str(value.get("source_label") or "").strip() or None,
        "note_type": coerce_string_list(value.get("note_type")),
        "path_contains": coerce_string_list(value.get("path_contains")),
    }


def _coerce_literal_terms(value: Any, fallback: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return list(fallback)
    terms: list[dict[str, Any]] = []
    for entry in value:
        if isinstance(entry, dict):
            term = str(entry.get("term") or entry.get("value") or "").strip()
            match = str(entry.get("match") or "case_insensitive_substring").strip() or "case_insensitive_substring"
            required = bool(entry.get("required"))
        else:
            term = str(entry).strip()
            match = "case_insensitive_substring"
            required = False
        if term and not any(item["term"] == term for item in terms):
            terms.append({"term": term, "match": match, "required": required})
    return terms or list(fallback)


def _coerce_retrieval_layers(value: Any, fallback: list[dict[str, Any]]) -> list[dict[str, Any]]:
    allowed = {"exact_chunk_search", "lexical_chunk_search", "vector_search", "graph_lookup", "graph_expand", "graph_paths", "graph_neighbors", "graph_from_results"}
    if not isinstance(value, list):
        return list(fallback)
    layers: list[dict[str, Any]] = []
    for entry in value:
        if not isinstance(entry, dict):
            continue
        operator = str(entry.get("operator") or "").strip()
        if operator not in allowed:
            continue
        layer: dict[str, Any] = {"operator": operator, "required": bool(entry.get("required"))}
        if "limit" in entry:
            try:
                layer["limit"] = max(0, int(entry["limit"]))
            except (TypeError, ValueError):
                pass
        if "depth" in entry:
            try:
                layer["depth"] = max(0, int(entry["depth"]))
            except (TypeError, ValueError):
                pass
        if "return_total_count" in entry:
            layer["return_total_count"] = bool(entry.get("return_total_count"))
        layers.append(layer)
    return layers or list(fallback)


def _coerce_selection_policy(value: Any, fallback: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict):
        return dict(fallback)
    raw_budgets = value.get("budgets") if isinstance(value.get("budgets"), dict) else {}
    fallback_budgets = fallback.get("budgets") if isinstance(fallback.get("budgets"), dict) else DEFAULT_SELECTION_BUDGETS
    budgets: dict[str, int] = {}
    for key in ("exact", "lexical", "vector", "graph"):
        raw_value = raw_budgets.get(key, fallback_budgets.get(key, DEFAULT_SELECTION_BUDGETS[key]))
        try:
            budgets[key] = max(0, int(raw_value))
        except (TypeError, ValueError):
            budgets[key] = DEFAULT_SELECTION_BUDGETS[key]
    return {
        "dedupe_by": str(value.get("dedupe_by") or fallback.get("dedupe_by") or "chunk_id"),
        "budgets": budgets,
        "preserve_required_layers": bool(value.get("preserve_required_layers", fallback.get("preserve_required_layers", True))),
    }


def _coerce_claim_policy(value: Any, fallback: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict):
        return dict(fallback)
    return {
        "coverage_claims_allowed": bool(value.get("coverage_claims_allowed", fallback.get("coverage_claims_allowed", False))),
        "negative_claims_require_exact_layer": bool(value.get("negative_claims_require_exact_layer", fallback.get("negative_claims_require_exact_layer", True))),
    }


def canonicalize_retrieval_plan(value: Any, *, fallback: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict):
        return fallback
    return {
        "intent_type": str(value.get("intent_type") or fallback.get("intent_type") or "semantic_traversal"),
        "resolved_referents": coerce_string_list(value.get("resolved_referents")) or coerce_string_list(fallback.get("resolved_referents")),
        "scope_filters": _coerce_scope_filters(value.get("scope_filters"), fallback.get("scope_filters", {})),
        "literal_terms": _coerce_literal_terms(value.get("literal_terms"), fallback.get("literal_terms", [])),
        "semantic_queries": coerce_string_list(value.get("semantic_queries")) or coerce_string_list(fallback.get("semantic_queries")),
        "lexical_queries": coerce_string_list(value.get("lexical_queries")) or coerce_string_list(fallback.get("lexical_queries")),
        "graph_seeds": coerce_string_list(value.get("graph_seeds")) or coerce_string_list(fallback.get("graph_seeds")),
        "retrieval_layers": _coerce_retrieval_layers(value.get("retrieval_layers"), fallback.get("retrieval_layers", [])),
        "selection_policy": _coerce_selection_policy(value.get("selection_policy"), fallback.get("selection_policy", {})),
        "claim_policy": _coerce_claim_policy(value.get("claim_policy"), fallback.get("claim_policy", {})),
    }


def retrieval_plan_layer(plan: dict[str, Any], operator: str) -> dict[str, Any] | None:
    for layer in plan.get("retrieval_layers", []):
        if isinstance(layer, dict) and layer.get("operator") == operator:
            return layer
    return None
