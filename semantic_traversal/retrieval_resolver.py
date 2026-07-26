from __future__ import annotations

from typing import Any

from .config import RuntimeConfig
from .retrieval_plan import SUPPORTED_EVIDENCE_REQUIREMENTS, coerce_string_list, is_search_intent


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


def validate_plan_completeness(*, planner_retrieval_plan: dict[str, Any], config: RuntimeConfig) -> dict[str, Any]:
    """Validate compiler-declared evidence against the YAML-owned operator map."""
    mapping = config.retrieval_evidence_requirement_operators
    declared: list[str] = []
    for value in coerce_string_list(planner_retrieval_plan.get("evidence_requirements")):
        if value not in declared:
            declared.append(value)
    layers = {
        str(layer.get("operator") or "")
        for layer in planner_retrieval_plan.get("retrieval_layers", [])
        if isinstance(layer, dict)
    }
    unsupported = [value for value in declared if value not in SUPPORTED_EVIDENCE_REQUIREMENTS or value not in mapping]
    missing_operators = [value for value in declared if value in mapping and mapping[value] not in layers]
    unavailable_operators: list[dict[str, str]] = []
    for requirement in declared:
        operator = mapping.get(requirement)
        if operator == "graph_expand" and not config.graph_traversal_enabled:
            unavailable_operators.append({"requirement": requirement, "operator": operator, "reason": "graph traversal is disabled"})
        elif operator == "temporal_retrieve" and not config.retrieval_temporal_enabled:
            unavailable_operators.append({"requirement": requirement, "operator": operator, "reason": "temporal retrieval is disabled"})
    invalid_operator_configuration = [
        {"requirement": requirement, "operator": operator}
        for requirement, operator in mapping.items()
        if requirement in declared and operator not in {"exact_chunk_search", "lexical_chunk_search", "vector_search", "graph_expand", "temporal_retrieve"}
    ]
    status = "complete" if not (unsupported or missing_operators or unavailable_operators or invalid_operator_configuration) else "incomplete"
    return {
        "declared_requirements": declared,
        "satisfied_requirements": [value for value in declared if value not in unsupported and value not in missing_operators and not any(item["requirement"] == value for item in unavailable_operators)],
        "missing_operators": [{"requirement": value, "operator": mapping.get(value)} for value in missing_operators],
        "unsupported_requirements": unsupported,
        "unavailable_operators": unavailable_operators,
        "invalid_operator_configuration": invalid_operator_configuration,
        "repair_attempted": False,
        "repair_result": None,
        "status": status,
    }


def validate_plan_executability(*, planner_retrieval_plan: dict[str, Any], config: RuntimeConfig) -> dict[str, Any]:
    """Check that each requested operator has usable inputs from this plan only."""
    plan = planner_retrieval_plan if isinstance(planner_retrieval_plan, dict) else {}
    requested: list[str] = []
    for layer in plan.get("retrieval_layers", []):
        if not isinstance(layer, dict):
            continue
        operator = str(layer.get("operator") or "").strip()
        if operator and operator not in requested:
            requested.append(operator)

    semantic_queries = [value.strip() for value in coerce_string_list(plan.get("semantic_queries")) if value.strip()]
    lexical_queries = [value.strip() for value in coerce_string_list(plan.get("lexical_queries")) if value.strip()]
    graph_seeds = [value.strip() for value in coerce_string_list(plan.get("graph_seeds")) if value.strip()]
    literal_terms = [
        str(entry.get("term") or "").strip()
        for entry in plan.get("literal_terms", [])
        if isinstance(entry, dict) and str(entry.get("term") or "").strip()
    ]
    configured_graph_sources = tuple(config.graph_traversal_seed_sources)
    operator_results: list[dict[str, Any]] = []
    executable: list[str] = []
    blocking_reasons: list[str] = []
    supported = {"exact_chunk_search", "lexical_chunk_search", "vector_search", "graph_expand", "temporal_retrieve"}

    for operator in requested:
        if operator not in supported:
            result = {
                "operator": operator,
                "status": "invalid_input",
                "required_inputs": [],
                "present_inputs": [],
                "reason": "retrieval operator is unsupported by this runtime seam",
            }
        elif operator == "exact_chunk_search":
            present = ["literal_terms"] if literal_terms else []
            result = {
                "operator": operator,
                "status": "executable" if present else "missing_input",
                "required_inputs": ["literal_terms"],
                "present_inputs": present,
                "reason": "" if present else "no usable literal-term record is present in the current plan",
            }
        elif operator == "lexical_chunk_search":
            present = ["lexical_queries"] if lexical_queries else ["semantic_queries"] if semantic_queries else []
            result = {
                "operator": operator,
                "status": "executable" if present else "missing_input",
                "required_inputs": ["lexical_queries", "semantic_queries"],
                "present_inputs": present,
                "reason": "" if not present or present == ["lexical_queries"] else "lexical_queries absent; using current-plan semantic_queries as the explicit lexical fallback",
            }
        elif operator == "vector_search":
            present = ["semantic_queries"] if semantic_queries else []
            result = {
                "operator": operator,
                "status": "executable" if present else "missing_input",
                "required_inputs": ["semantic_queries"],
                "present_inputs": present,
                "reason": "" if present else "no usable semantic_queries value is present in the current plan",
            }
        elif operator == "graph_expand":
            present_sources = [
                source for source in configured_graph_sources
                if (source == "graph_seeds" and graph_seeds) or (source == "semantic_queries" and semantic_queries)
            ]
            present = list(present_sources)
            result = {
                "operator": operator,
                "status": "executable" if present else "missing_input",
                "required_inputs": list(configured_graph_sources),
                "present_inputs": present,
                "reason": "" if present else "no permitted configured graph seed source has a usable current-plan value",
            }
        else:
            present = []
            if semantic_queries:
                present.append("semantic_queries")
            if lexical_queries:
                present.append("lexical_queries")
            result = {
                "operator": operator,
                "status": "executable" if present else "missing_input",
                "required_inputs": ["semantic_queries", "lexical_queries"],
                "present_inputs": present,
                "reason": "" if present else "no usable temporal relevance query is present in the current plan",
            }
        operator_results.append(result)
        if result["status"] == "executable":
            executable.append(operator)
        else:
            blocking_reasons.append(f"{operator}: {result['reason']}")

    has_retrieval_intent = bool(requested)
    if not requested:
        blocking_reasons.append("current plan requested no supported retrieval operator")
    return {
        "status": "executable" if requested and not blocking_reasons else "non_executable",
        "requested_operators": requested,
        "executable_operators": executable,
        "operator_results": operator_results,
        "has_retrieval_intent": has_retrieval_intent,
        "blocking_reasons": blocking_reasons,
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

        observed_kind = None
        if scope_request in observed_note_types:
            observed_kind = "note_type"
        elif scope_request in observed_source_labels:
            observed_kind = "source_label"
        elif scope_request in observed_paths:
            observed_kind = "path"
        adjustments.append(
            {
                "field": "scope_requests",
                "value": scope_request,
                "action": "unauthorized_inventory_scope" if observed_kind else "unavailable_scope_alias",
                "reason": (
                    f"observed inventory {observed_kind} values are descriptive only and cannot bind executable scope"
                    if observed_kind
                    else "scope request is not a configured YAML scope alias"
                ),
                "observed_inventory_match": observed_kind,
            }
        )

    literal_terms = [entry for entry in planner_retrieval_plan.get("literal_terms", []) if isinstance(entry, dict)]
    retrieval_layers = [entry for entry in planner_retrieval_plan.get("retrieval_layers", []) if isinstance(entry, dict)]
    canonical_layers: list[dict[str, Any]] = []
    for raw_layer in retrieval_layers:
        layer = dict(raw_layer)
        operator = str(layer.get("operator") or "")
        if operator in {"lexical_chunk_search", "vector_search", "graph_expand", "temporal_retrieve"}:
            if operator == "lexical_chunk_search":
                default_limit = config.retrieval_planner_defaults["lexical_limit"]
                maximum_limit = config.retrieval_lexical_max_candidates
            elif operator == "vector_search":
                default_limit = config.retrieval_planner_defaults["vector_limit"]
                maximum_limit = config.retrieval_vector_max_candidates
            elif operator == "graph_expand":
                default_limit = config.retrieval_graph_max_candidates
                maximum_limit = config.retrieval_graph_max_candidates
            else:
                default_limit = config.retrieval_temporal_default_limit
                maximum_limit = config.retrieval_temporal_max_candidates
            raw_limit = layer.get("limit") if "limit" in layer else None
            requested_limit: int | None
            limit_adjustment = "none"
            try:
                requested_limit = int(raw_limit) if raw_limit is not None else None
            except (TypeError, ValueError):
                requested_limit = None
                limit_adjustment = "defaulted_invalid"
            if requested_limit is None:
                effective_limit = max(0, int(default_limit))
                if limit_adjustment == "none":
                    limit_adjustment = "defaulted"
            else:
                effective_limit = max(0, requested_limit)
                if effective_limit != requested_limit:
                    limit_adjustment = "sanitized"
                if effective_limit > int(maximum_limit):
                    effective_limit = int(maximum_limit)
                    limit_adjustment = "clamped_to_max"
            layer["limit"] = effective_limit
            layer["requested_limit"] = requested_limit
            layer["default_limit"] = int(default_limit)
            layer["maximum_limit"] = int(maximum_limit)
            layer["effective_limit"] = effective_limit
            layer["limit_adjustment"] = limit_adjustment
            adjustments.append(
                {
                    "field": f"retrieval_layers.{operator}.limit",
                    "requested": requested_limit,
                    "effective": effective_limit,
                    "action": limit_adjustment,
                    "reason": "runtime-owned executor default/max bounds",
                }
            )
        if operator == "graph_expand":
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
        if operator == "temporal_retrieve":
            mode = str(layer.get("mode") or config.retrieval_temporal_default_mode).strip()
            if mode not in config.retrieval_temporal_allowed_modes:
                adjustments.append({"field": "retrieval_layers.temporal_retrieve.mode", "requested": mode, "effective": None, "action": "unsupported", "reason": "runtime YAML allowed temporal modes"})
                layer["mode_adjustment"] = "unsupported"
            else:
                layer["mode_adjustment"] = "none"
            requested_types = [str(value) for value in layer.get("anchor_types", []) if str(value).strip()]
            layer["anchor_types"] = [value for value in requested_types if value in config.retrieval_temporal_default_anchor_types or value in {str(item.get("anchor_type")) for item in config.retrieval_temporal_field_mappings.values()}]
            if not layer["anchor_types"]:
                layer["anchor_types"] = list(config.retrieval_temporal_default_anchor_types)
                layer["anchor_types_adjustment"] = "defaulted"
            requested_authorities = [str(value) for value in layer.get("authorities", []) if str(value).strip()]
            layer["authorities"] = [value for value in requested_authorities if value in config.retrieval_temporal_allowed_authorities] or list(config.retrieval_temporal_allowed_authorities)
            layer["include_unresolved"] = bool(layer.get("include_unresolved", config.retrieval_temporal_include_conflicted_by_default))
        canonical_layers.append(layer)
    retrieval_layers = canonical_layers
    requested_selection_policy = dict(planner_retrieval_plan.get("selection_policy") or {})
    runtime_selection_policy = config.retrieval_planner_defaults["selection_policy"]
    runtime_max_chunks = min(
        max(0, int(runtime_selection_policy["max_chunks"])),
        max(0, int(config.max_retrieval_chunks)),
    )
    selection_policy = {
        "max_chunks": runtime_max_chunks,
        "preserve_required_layers": bool(runtime_selection_policy["preserve_required_layers"]),
    }
    budget_retirement = {
        "field": "selection_policy.budgets",
        "requested": requested_selection_policy.get("budgets"),
        "effective": None,
        "action": "retired",
        "reason": "ordinary retrieval-layer allocation is retired for ordinal-note-breadth selector",
    }
    adjustments.append(budget_retirement)
    if requested_selection_policy != selection_policy:
        adjustments.append(
            {
                "field": "selection_policy",
                "requested": requested_selection_policy,
                "effective": selection_policy,
                "action": "runtime_policy_override",
                "reason": "compiler selection policy is non-authoritative; YAML/runtime policy applies",
            }
        )
    runtime_claim_policy = config.retrieval_planner_defaults["claim_policy"]
    requested_claim_policy = dict(planner_retrieval_plan.get("claim_policy") or {})
    claim_policy = {
        "negative_claims_require_exact_layer": bool(runtime_claim_policy["negative_claims_require_exact_layer"]),
        "coverage_claims_allowed": False,
    }
    if requested_claim_policy != claim_policy:
        adjustments.append(
            {
                "field": "claim_policy",
                "requested": requested_claim_policy,
                "effective": claim_policy,
                "action": "runtime_policy_override",
                "reason": "compiler claim policy is non-authoritative; runtime derives coverage permission",
            }
        )

    bound_plan = {
        "intent_type": str(planner_retrieval_plan.get("intent_type") or "semantic_traversal"),
        "scope_filters": scope_filters,
        "preferred_scope_filters": preferred_scope_filters,
        "scope_resolution": scope_resolution,
        "literal_terms": literal_terms,
        "evidence_requirements": coerce_string_list(planner_retrieval_plan.get("evidence_requirements")),
        "semantic_queries": semantic_queries,
        "lexical_queries": coerce_string_list(planner_retrieval_plan.get("lexical_queries")) or concepts,
        "graph_seeds": coerce_string_list(planner_retrieval_plan.get("graph_seeds")),
        "retrieval_layers": retrieval_layers,
        "selection_policy": selection_policy,
        "claim_policy": claim_policy,
    }
    return bound_plan, adjustments, inventory_summary
