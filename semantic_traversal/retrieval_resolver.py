from __future__ import annotations

from typing import Any

from .config import RuntimeConfig
from .retrieval_plan import coerce_string_list, is_search_intent


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


def _record_unobserved_alias_bindings(
    *,
    request: str,
    alias_filters: dict[str, list[str] | str | None],
    observed_note_types: set[str],
    observed_source_labels: set[str],
    observed_paths: set[str],
) -> list[dict[str, Any]]:
    adjustments: list[dict[str, Any]] = []
    note_types = [str(value).strip() for value in alias_filters.get("note_type") or [] if str(value).strip()]
    source_label = str(alias_filters.get("source_label") or "").strip()
    path_contains = [str(value).strip() for value in alias_filters.get("path_contains") or [] if str(value).strip()]

    for value in note_types:
        if value not in observed_note_types:
            adjustments.append(
                {
                    "field": f"scope_aliases.{request}.note_type",
                    "value": value,
                    "action": "bound_to_alias_unobserved_in_inventory",
                    "reason": "configured alias value was not observed in resource_inventory_summary.frontmatter_facets.note_type",
                }
            )

    if source_label and source_label not in observed_source_labels:
        adjustments.append(
            {
                "field": f"scope_aliases.{request}.source_label",
                "value": source_label,
                "action": "bound_to_alias_unobserved_in_inventory",
                "reason": "configured alias value was not observed in resource_inventory_summary.observed_source_labels",
            }
        )

    for value in path_contains:
        if value not in observed_paths:
            adjustments.append(
                {
                    "field": f"scope_aliases.{request}.path_contains",
                    "value": value,
                    "action": "bound_to_alias_unobserved_in_inventory",
                    "reason": "configured alias value was not observed in resource_inventory_summary.path_topology",
                }
            )
    return adjustments


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
    preferred_scope_filters: dict[str, Any] = {"source_label": None, "note_type": [], "path_contains": []}
    scope_resolution: dict[str, Any] = {"hard": [], "preferred": [], "unsupported": []}
    adjustments: list[dict[str, Any]] = []

    concepts = coerce_string_list(planner_retrieval_plan.get("concepts"))
    semantic_queries = coerce_string_list(planner_retrieval_plan.get("semantic_queries"))

    for scope_request in coerce_string_list(planner_retrieval_plan.get("scope_requests")):
        alias_filters = _alias_filters_for_request(scope_request, scope_aliases)
        if alias_filters is not None:
            intent_type = str(planner_retrieval_plan.get("intent_type") or "semantic_traversal")
            exact_request = "exact" in intent_type or is_search_intent(str(raw_user_input or ""))
            mode_key = "exact_request_mode" if exact_request else "semantic_request_mode"
            mode = str(config.retrieval_scope_policy.get(mode_key) or "").strip().lower()
            target = scope_filters if mode == "hard" else preferred_scope_filters if mode == "preferred" else None
            if target is None:
                scope_resolution["unsupported"].append(scope_request)
                adjustments.append(
                    {
                        "field": "scope_requests",
                        "value": scope_request,
                        "action": "unsupported_scope_mode",
                        "reason": f"runtime scope policy {mode_key} must resolve to hard or preferred",
                    }
                )
                continue
            if alias_filters["source_label"]:
                target["source_label"] = str(alias_filters["source_label"])
            target["note_type"] = _merge_unique(target["note_type"], list(alias_filters["note_type"]))
            target["path_contains"] = _merge_unique(target["path_contains"], list(alias_filters["path_contains"]))
            scope_resolution[mode].append(scope_request)
            adjustments.append(
                {
                    "field": "scope_requests",
                    "value": scope_request,
                    "action": "bound_to_alias" if mode == "hard" else "bound_to_preferred_scope",
                    "reason": "configured scope alias with runtime-owned scope mode",
                }
            )
            adjustments.extend(
                _record_unobserved_alias_bindings(
                    request=scope_request,
                    alias_filters=alias_filters,
                    observed_note_types=observed_note_types,
                    observed_source_labels=observed_source_labels,
                    observed_paths=observed_paths,
                )
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
    canonical_layers: list[dict[str, Any]] = []
    for raw_layer in retrieval_layers:
        layer = dict(raw_layer)
        if str(layer.get("operator") or "") == "graph_expand":
            requested_depth: int | None
            adjustment = "none"
            raw_depth = layer.get("depth")
            try:
                requested_depth = int(raw_depth) if raw_depth is not None else None
            except (TypeError, ValueError):
                requested_depth = None
                adjustment = "defaulted_invalid"
            if requested_depth is None:
                effective_depth = max(0, config.retrieval_graph_default_depth)
                if adjustment == "none":
                    adjustment = "defaulted"
            else:
                effective_depth = max(0, requested_depth)
                if effective_depth != requested_depth:
                    adjustment = "sanitized"
                if effective_depth > config.retrieval_graph_max_depth:
                    effective_depth = config.retrieval_graph_max_depth
                    adjustment = "clamped_to_max"
            layer["depth"] = effective_depth
            layer["requested_depth"] = requested_depth
            layer["default_depth"] = config.retrieval_graph_default_depth
            layer["max_depth"] = config.retrieval_graph_max_depth
            layer["effective_depth"] = effective_depth
            layer["depth_adjustment"] = adjustment
            adjustments.append(
                {
                    "field": "retrieval_layers.graph_expand.depth",
                    "requested": requested_depth,
                    "effective": effective_depth,
                    "action": adjustment,
                    "reason": "runtime-owned retrieval.graph default/max bounds",
                }
            )
        canonical_layers.append(layer)
    retrieval_layers = canonical_layers
    selection_policy = dict(planner_retrieval_plan.get("selection_policy") or {})
    if "max_chunks" not in selection_policy:
        selection_policy["max_chunks"] = config.max_retrieval_chunks
    claim_policy = dict(planner_retrieval_plan.get("claim_policy") or {})

    bound_plan = {
        "intent_type": str(planner_retrieval_plan.get("intent_type") or "semantic_traversal"),
        "scope_filters": scope_filters,
        "preferred_scope_filters": preferred_scope_filters,
        "scope_resolution": scope_resolution,
        "literal_terms": literal_terms,
        "semantic_queries": semantic_queries,
        "lexical_queries": coerce_string_list(planner_retrieval_plan.get("lexical_queries")) or concepts,
        "graph_seeds": coerce_string_list(planner_retrieval_plan.get("graph_seeds")),
        "retrieval_layers": retrieval_layers,
        "selection_policy": selection_policy,
        "claim_policy": claim_policy,
    }
    return bound_plan, adjustments, inventory_summary
