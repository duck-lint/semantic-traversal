from __future__ import annotations

from typing import Any

from .config import RuntimeConfig
from .retrieval_plan import SUPPORTED_EVIDENCE_REQUIREMENTS, coerce_string_list, is_search_intent
from .semantic_grounding import build_grounding_spec


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
    literal_entries = planner_retrieval_plan.get("literal_terms") if isinstance(planner_retrieval_plan.get("literal_terms"), list) else []
    bare_literals = [entry for entry in literal_entries if isinstance(entry, dict) and entry.get("raw_type") == "bare"]
    unsupported_literal_modes = sorted({str(entry.get("match")) for entry in literal_entries if isinstance(entry, dict) and str(entry.get("match") or "") not in {"", "case_sensitive_substring", "case_insensitive_substring"}})
    exact_layers = [layer for layer in planner_retrieval_plan.get("retrieval_layers", []) if isinstance(layer, dict) and str(layer.get("operator") or "") == "exact_chunk_search"]
    unsupported_exact_layer_modes = sorted({str(layer.get("mode")) for layer in exact_layers if str(layer.get("mode") or "") not in {"", "default"}})
    exact_contract_required = "literal_exhaustive" in declared or any(bool(layer.get("required")) for layer in exact_layers)
    contract_blocking_reasons: list[str] = []
    if exact_contract_required and bare_literals:
        contract_blocking_reasons.append("bare_literal_contract")
    if unsupported_literal_modes:
        contract_blocking_reasons.append("unsupported_literal_match_mode")
    if unsupported_exact_layer_modes:
        contract_blocking_reasons.append("unsupported_exact_layer_mode")
    status = "complete" if not (unsupported or missing_operators or unavailable_operators or invalid_operator_configuration or contract_blocking_reasons) else "incomplete"
    return {
        "declared_requirements": declared,
        "satisfied_requirements": [value for value in declared if value not in unsupported and value not in missing_operators and not any(item["requirement"] == value for item in unavailable_operators)],
        "missing_operators": [{"requirement": value, "operator": mapping.get(value)} for value in missing_operators],
        "unsupported_requirements": unsupported,
        "unavailable_operators": unavailable_operators,
        "invalid_operator_configuration": invalid_operator_configuration,
        "literal_contract": {
            "bare_literal_count": len(bare_literals),
            "structured_literal_count": sum(isinstance(entry, dict) and entry.get("raw_type") != "bare" for entry in literal_entries),
            "unsupported_literal_match_modes": unsupported_literal_modes,
            "unsupported_exact_layer_modes": unsupported_exact_layer_modes,
            "exact_contract_required": exact_contract_required,
            "completeness": "incomplete" if contract_blocking_reasons else "complete",
            "repair_triggered": False,
            "automatic_support_excluded_from_coverage": True,
        },
        "blocking_reasons": contract_blocking_reasons,
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
    scope_filters: dict[str, Any] = {"source_label": None, "note_type": [], "path_contains": []}
    adjustments: list[dict[str, Any]] = []

    concepts = coerce_string_list(planner_retrieval_plan.get("concepts"))
    # Resolved referents are semantic subjects, not executable scope. Preserve
    # the compiler's canonical order and deduplication without interpreting
    # or authorizing the values against the inventory.
    resolved_referents = coerce_string_list(planner_retrieval_plan.get("resolved_referents"))
    semantic_queries = coerce_string_list(planner_retrieval_plan.get("semantic_queries"))

    literal_terms = [entry for entry in planner_retrieval_plan.get("literal_terms", []) if isinstance(entry, dict)]
    retrieval_layers = [entry for entry in planner_retrieval_plan.get("retrieval_layers", []) if isinstance(entry, dict)]
    def canonicalize_layer(
        raw_layer: dict[str, Any],
        *,
        source: str,
        automatic: bool = False,
        support_reason: str | None = None,
    ) -> dict[str, Any]:
        """Apply one runtime-owned contract to requested and support layers."""
        layer = dict(raw_layer)
        operator = str(layer.get("operator") or "")
        layer["source"] = source
        layer["automatic"] = bool(automatic)
        if support_reason:
            layer["support_reason"] = support_reason
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
        return layer

    canonical_layers: list[dict[str, Any]] = [
        canonicalize_layer(raw_layer, source="compiler")
        for raw_layer in retrieval_layers
    ]
    retrieval_layers = canonical_layers
    requested_operators = {str(layer.get("operator") or "") for layer in retrieval_layers}
    expanded_support_surfaces: list[dict[str, Any]] = []

    def add_support(operator: str, *, reason: str, limit: int | None = None) -> None:
        if operator in requested_operators or any(item.get("operator") == operator for item in expanded_support_surfaces):
            return
        layer: dict[str, Any] = {"operator": operator, "required": False, "automatic": True, "support_reason": reason}
        if limit is not None:
            layer["limit"] = int(limit)
        expanded_support_surfaces.append(canonicalize_layer(layer, source="runtime_expansion", automatic=True, support_reason=reason))

    # Compiler layers express required evidence/explicit intent.  They do not
    # suppress structurally compatible grounding surfaces.
    contextual_atoms = [
        *[("concept", value) for value in concepts],
        *[("semantic_query", value) for value in semantic_queries],
        *[("lexical_query", value) for value in coerce_string_list(planner_retrieval_plan.get("lexical_queries"))],
        *[("resolved_referent", value) for value in resolved_referents],
        *[("graph_seed", value) for value in coerce_string_list(planner_retrieval_plan.get("graph_seeds"))],
    ]
    if literal_terms or contextual_atoms:
        add_support("exact_chunk_search", reason="contextual exact grounding")
    if semantic_queries or coerce_string_list(planner_retrieval_plan.get("lexical_queries")) or concepts:
        add_support("lexical_chunk_search", reason="contextual lexical grounding", limit=config.retrieval_lexical_max_candidates)
    if semantic_queries or concepts:
        add_support("vector_search", reason="contextual semantic grounding", limit=config.retrieval_vector_max_candidates)
    if config.graph_traversal_enabled and (semantic_queries or concepts or resolved_referents or coerce_string_list(planner_retrieval_plan.get("graph_seeds"))):
        add_support("graph_expand", reason="graph propagation from grounded semantic objects", limit=config.retrieval_graph_max_candidates)
    runtime_contextual_exact_terms: list[dict[str, Any]] = []
    seen_contextual_terms: set[str] = set()
    explicit_term_values = {
        str(entry.get("term") or "").strip()
        for entry in literal_terms
        if isinstance(entry, dict)
    }
    for atom_type, value in contextual_atoms:
        value = str(value).strip()
        if not value or len(value) < 3 or value in explicit_term_values or value in seen_contextual_terms:
            continue
        seen_contextual_terms.add(value)
        runtime_contextual_exact_terms.append({
            "term": value,
            "match": "case_insensitive_substring",
            "required": False,
            "automatic": True,
            "source_atom": atom_type,
            "exhaustive": False,
        })
    expanded_layers = [*retrieval_layers, *expanded_support_surfaces]
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
        "resolved_referents": resolved_referents,
        "scope_filters": scope_filters,
        "literal_terms": literal_terms,
        "runtime_contextual_exact_terms": runtime_contextual_exact_terms,
        "evidence_requirements": coerce_string_list(planner_retrieval_plan.get("evidence_requirements")),
        "semantic_queries": semantic_queries,
        "lexical_queries": coerce_string_list(planner_retrieval_plan.get("lexical_queries")) or concepts,
        "graph_seeds": coerce_string_list(planner_retrieval_plan.get("graph_seeds")),
        "retrieval_layers": retrieval_layers,
        "requested_retrieval_layers": retrieval_layers,
        "runtime_expanded_layers": expanded_layers,
        "expanded_support_surfaces": expanded_support_surfaces,
        "execution_model": "contextual_surface_closure_then_relation_evaluation",
        "selection_policy": selection_policy,
        "claim_policy": claim_policy,
        "grounding_specification": build_grounding_spec(planner_retrieval_plan).as_dict(),
    }
    return bound_plan, adjustments, inventory_summary
