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
SCOPE_JOURNAL_WORDS = {"daily", "dailies", "journal", "journals", "journaled", "entry", "entries"}
SEARCH_NOISE_WORDS = SEARCH_INTENT_WORDS | SCOPE_JOURNAL_WORDS | {"exact", "exactly", "literal", "literally", "term", "terms", "text"}
DEFAULT_SELECTION_BUDGETS = {"exact": 12, "lexical": 6, "vector": 6, "graph": 4}
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
    "selection_policy",
    "claim_policy",
}


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
    quoted_terms = [match.strip() for match in re.findall(r"[\"“”']([^\"“”']+)[\"“”']", text) if match.strip()]
    if quoted_terms:
        return list(dict.fromkeys(quoted_terms))
    return [term for term in collect_plan_terms(text) if term not in SEARCH_NOISE_WORDS]


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
) -> dict[str, Any]:
    layer: dict[str, Any] = {"operator": operator, "required": required}
    if limit is not None:
        layer["limit"] = limit
    if depth is not None:
        layer["depth"] = depth
    if return_total_count is not None:
        layer["return_total_count"] = return_total_count
    return layer


def build_default_retrieval_plan(
    *,
    raw_user_input: str,
    query: str,
    concepts: list[str],
    scope_requests: list[str],
    graph_seeds: list[str],
    resolved_referents: list[str] | None = None,
) -> dict[str, Any]:
    search_intent = is_search_intent(raw_user_input)
    literal_terms = literal_terms_from_text(raw_user_input) if search_intent else []
    semantic_queries = [query.strip()] if query.strip() else []
    lexical_queries = literal_terms if literal_terms else concepts
    intent_type = "exact_search" if search_intent else "semantic_traversal"
    if scope_requests:
        intent_type = "scoped_exact_search" if search_intent else "scoped_semantic_traversal"

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
        "scope_requests": list(dict.fromkeys(scope_requests)),
        "concepts": list(dict.fromkeys(concepts)),
        "resolved_referents": list(dict.fromkeys(resolved_referents or [])),
        "literal_terms": _literal_term_entries(literal_terms, required=search_intent),
        "semantic_queries": list(dict.fromkeys(semantic_queries)),
        "lexical_queries": list(dict.fromkeys(lexical_queries)),
        "graph_seeds": list(dict.fromkeys(graph_seeds)),
        "retrieval_layers": layers,
        "selection_policy": {
            "max_chunks": 24,
            "preserve_required_layers": True,
            "budgets": dict(DEFAULT_SELECTION_BUDGETS),
        },
        "claim_policy": {
            "coverage_claims_allowed": bool(search_intent),
            "negative_claims_require_exact_layer": True,
        },
    }


def _coerce_literal_terms(value: Any, fallback: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return list(fallback)
    terms: list[dict[str, Any]] = []
    for entry in value:
        if isinstance(entry, dict):
            term = str(entry.get("term") or entry.get("value") or "").strip()
            match = str(entry.get("match") or "case_insensitive_substring").strip() or "case_insensitive_substring"
            if match not in {"case_insensitive_substring", "case_sensitive_substring"}:
                match = "case_insensitive_substring"
            required = bool(entry.get("required"))
        else:
            term = str(entry).strip()
            match = "case_insensitive_substring"
            required = False
        if term and not any(item["term"] == term for item in terms):
            terms.append({"term": term, "match": match, "required": required})
    return terms or list(fallback)


def _coerce_retrieval_layers(value: Any, fallback: list[dict[str, Any]]) -> list[dict[str, Any]]:
    allowed = {"exact_chunk_search", "lexical_chunk_search", "vector_search", "graph_expand"}
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
        "max_chunks": max(0, int(value.get("max_chunks", fallback.get("max_chunks", 24)))),
        "preserve_required_layers": bool(value.get("preserve_required_layers", fallback.get("preserve_required_layers", True))),
        "budgets": budgets,
    }


def _coerce_claim_policy(value: Any, fallback: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict):
        return dict(fallback)
    return {
        "coverage_claims_allowed": bool(value.get("coverage_claims_allowed", fallback.get("coverage_claims_allowed", False))),
        "negative_claims_require_exact_layer": bool(value.get("negative_claims_require_exact_layer", fallback.get("negative_claims_require_exact_layer", True))),
    }


def canonicalize_retrieval_plan(value: Any, *, fallback: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    diagnostics: dict[str, Any] = {"ignored_planner_fields": []}
    if not isinstance(value, dict):
        return dict(fallback), diagnostics

    unknown_fields = sorted(set(value) - KNOWN_PLANNER_FIELDS)
    if unknown_fields:
        diagnostics["ignored_planner_fields"] = unknown_fields

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
        "selection_policy": _coerce_selection_policy(value.get("selection_policy"), fallback.get("selection_policy", {})),
        "claim_policy": _coerce_claim_policy(value.get("claim_policy"), fallback.get("claim_policy", {})),
    }
    return result, diagnostics


def retrieval_plan_layer(plan: dict[str, Any], operator: str) -> dict[str, Any] | None:
    for layer in plan.get("retrieval_layers", []):
        if isinstance(layer, dict) and layer.get("operator") == operator:
            return layer
    return None
