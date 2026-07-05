from __future__ import annotations

from typing import Any

from .config import RuntimeConfig
from .retrieval_plan import coerce_string_list


def _merge_unique(existing: list[str], new_values: list[str]) -> list[str]:
    merged = list(existing)
    for value in new_values:
        if value and value not in merged:
            merged.append(value)
    return merged


def _observed_strings(inventory_summary: dict[str, Any], key: str, label_key: str = "value") -> set[str]:
    observed: set[str] = set()
    values: list[Any]
    if key == "path_topology" and isinstance(inventory_summary, dict):
        values = []
        topology = inventory_summary.get("path_topology")
        if isinstance(topology, dict):
            values.extend(topology.get("top_level", []))
            values.extend(topology.get("second_level", []))
    else:
        values = inventory_summary.get(key, []) if isinstance(inventory_summary, dict) else []
    for entry in values:
        if isinstance(entry, dict):
            candidate = entry.get(label_key) or entry.get("label")
        else:
            candidate = entry
        text = str(candidate or "").strip()
        if text:
            observed.add(text)
    return observed


def _observed_facet_values(inventory_summary: dict[str, Any], facet_name: str) -> set[str]:
    facets = inventory_summary.get("frontmatter_facets") if isinstance(inventory_summary, dict) else {}
    if not isinstance(facets, dict):
        return set()
    observed: set[str] = set()
    for entry in facets.get(facet_name, []):
        if isinstance(entry, dict):
            candidate = entry.get("value") or entry.get("label")
        else:
            candidate = entry
        text = str(candidate or "").strip()
        if text:
            observed.add(text)
    return observed


def _alias_filters_for_request(request: str, scope_aliases: dict[str, dict[str, Any]]) -> dict[str, list[str] | str | None] | None:
    alias = scope_aliases.get(request)
    if alias is None:
        return None
    return {
        "source_label": alias.get("source_label"),
        "note_type": list(alias.get("note_type") or []),
        "path_contains": list(alias.get("path_contains") or []),
    }


def bind_retrieval_plan(
    *,
    planner_retrieval_plan: dict[str, Any],
    inventory_summary: dict[str, Any],
    config: RuntimeConfig,
    raw_user_input: str | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    scope_aliases = config.retrieval_scope_aliases
    observed_note_types = _observed_facet_values(inventory_summary, "note_type")
    observed_source_labels = _observed_strings(inventory_summary, "observed_source_labels", "label")
    observed_paths = _observed_strings(inventory_summary, "path_topology")
    scope_filters: dict[str, Any] = {"source_label": None, "note_type": [], "path_contains": []}
    adjustments: list[dict[str, Any]] = []

    concepts = coerce_string_list(planner_retrieval_plan.get("concepts"))
    semantic_queries = coerce_string_list(planner_retrieval_plan.get("semantic_queries"))

    for scope_request in coerce_string_list(planner_retrieval_plan.get("scope_requests")):
        alias_filters = _alias_filters_for_request(scope_request, scope_aliases)
        if alias_filters is not None:
            if alias_filters["source_label"]:
                scope_filters["source_label"] = str(alias_filters["source_label"])
            scope_filters["note_type"] = _merge_unique(scope_filters["note_type"], list(alias_filters["note_type"]))
            scope_filters["path_contains"] = _merge_unique(scope_filters["path_contains"], list(alias_filters["path_contains"]))
            adjustments.append(
                {
                    "field": "scope_requests",
                    "value": scope_request,
                    "action": "bound_to_alias",
                    "reason": "configured scope alias",
                }
            )
            continue

        if scope_request in observed_note_types:
            scope_filters["note_type"] = _merge_unique(scope_filters["note_type"], [scope_request])
            adjustments.append(
                {
                    "field": "scope_requests",
                    "value": scope_request,
                    "action": "bound_to_observed_note_type",
                    "reason": "observed frontmatter facet value",
                }
            )
            continue

        if scope_request in observed_source_labels:
            scope_filters["source_label"] = scope_request
            adjustments.append(
                {
                    "field": "scope_requests",
                    "value": scope_request,
                    "action": "bound_to_observed_source_label",
                    "reason": "observed source label",
                }
            )
            continue

        if scope_request in observed_paths:
            scope_filters["path_contains"] = _merge_unique(scope_filters["path_contains"], [scope_request])
            adjustments.append(
                {
                    "field": "scope_requests",
                    "value": scope_request,
                    "action": "bound_to_observed_path",
                    "reason": "observed path topology value",
                }
            )
            continue

        concepts = _merge_unique(concepts, [scope_request])
        if scope_request not in semantic_queries:
            semantic_queries.append(scope_request)
        adjustments.append(
            {
                "field": "scope_requests",
                "value": scope_request,
                "action": "demoted_to_concept",
                "reason": "not a configured scope alias or observed resource scope",
            }
        )

    literal_terms = [entry for entry in planner_retrieval_plan.get("literal_terms", []) if isinstance(entry, dict)]
    retrieval_layers = [entry for entry in planner_retrieval_plan.get("retrieval_layers", []) if isinstance(entry, dict)]
    selection_policy = dict(planner_retrieval_plan.get("selection_policy") or {})
    if "max_chunks" not in selection_policy:
        selection_policy["max_chunks"] = config.max_retrieval_chunks
    claim_policy = dict(planner_retrieval_plan.get("claim_policy") or {})

    bound_plan = {
        "intent_type": str(planner_retrieval_plan.get("intent_type") or "semantic_traversal"),
        "scope_filters": scope_filters,
        "literal_terms": literal_terms,
        "semantic_queries": semantic_queries,
        "lexical_queries": coerce_string_list(planner_retrieval_plan.get("lexical_queries")) or concepts,
        "graph_seeds": coerce_string_list(planner_retrieval_plan.get("graph_seeds")),
        "retrieval_layers": retrieval_layers,
        "selection_policy": selection_policy,
        "claim_policy": claim_policy,
    }
    return bound_plan, adjustments, inventory_summary
