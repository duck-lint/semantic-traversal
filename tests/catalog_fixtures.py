from __future__ import annotations


_OPERATORS = {
    "exact.equals": {"surface": "exact", "meaning": "typed exact equality"},
    "lexical.terms": {"surface": "lexical", "meaning": "lexical terms"},
    "lexical.phrase": {"surface": "lexical", "meaning": "lexical phrase"},
    "vector.semantic_similarity": {"surface": "vector", "meaning": "vector similarity"},
    "graph.discovery.terms": {"surface": "graph", "meaning": "graph discovery terms"},
    "graph.discovery.phrase": {"surface": "graph", "meaning": "graph discovery phrase"},
    "graph.relation_occurrence_lookup": {"surface": "graph", "meaning": "relation occurrence lookup"},
    "graph.inbound_traversal": {"surface": "graph", "meaning": "graph inbound traversal"},
    "graph.outbound_traversal": {"surface": "graph", "meaning": "graph outbound traversal"},
}

_DISCOVERY_OPERATORS = ["graph.discovery.terms", "graph.discovery.phrase"]
_RELATION_OPERATIONS = [
    "graph.relation_occurrence_lookup",
    "graph.inbound_traversal",
    "graph.outbound_traversal",
]


def _scalar_dimension(field_class, field_name, description):
    return {
        "field_class": field_class,
        "field_name": field_name,
        "description": description,
        "value": {"shapes": [{"shape": "scalar", "domains": ["string"]}]},
        "access": [{"operator": "exact.equals", "target": "complete_value", "domains": ["string"]}],
    }


def _ordered_dimension(field_class, field_name, description):
    return {
        "field_class": field_class,
        "field_name": field_name,
        "description": description,
        "value": {"shapes": [{"shape": "ordered_sequence", "domains": ["string"]}]},
        "access": [
            {
                "operator": "exact.equals",
                "target": "complete_value",
                "operand": {"shape": "ordered_sequence", "member_domains": ["string"]},
            }
        ],
    }


def _discovery(node_kind, dimension_name, description):
    return {
        "node_kind": node_kind,
        "dimension_name": dimension_name,
        "description": description,
        "operators": list(_DISCOVERY_OPERATORS),
        "result": "opaque_graph_handle",
    }


def _relation(relation_class, relation_name, description, source_kinds, target_kinds):
    return {
        "relation_class": relation_class,
        "relation_name": relation_name,
        "description": description,
        "source_kinds": list(source_kinds),
        "target_kinds": list(target_kinds),
        "operations": list(_RELATION_OPERATIONS),
    }


def minimum_catalog(*, semantic_unit_target=False, include_tag=False):
    parsed_text = _scalar_dimension("intrinsic", "parsed_text", "parsed text")
    parsed_text["access"].extend(
        [
            {"operator": "lexical.terms", "target": "complete_value", "domains": ["string"]},
            {"operator": "lexical.phrase", "target": "complete_value", "domains": ["string"]},
        ]
    )
    if semantic_unit_target:
        parsed_text["access"].append(
            {"operator": "vector.semantic_similarity", "target": "complete_value", "domains": ["string"]}
        )

    discovery = [
        _discovery("semantic_object", "address_text", "object address"),
        _discovery("semantic_region", "address_text", "region address"),
    ]
    if include_tag:
        discovery.append(_discovery("semantic_object", "tag", "object tag"))

    if include_tag:
        tag = {
            "field_class": "semantic_identifier",
            "field_name": "tags",
            "description": "tags",
            "value": {"shapes": [{"shape": "sequence", "member_domains": ["string"]}]},
            "access": [
                {"operator": "exact.equals", "target": "member", "domains": ["string"]},
                {"operator": "lexical.terms", "target": "member", "domains": ["string"]},
                {"operator": "lexical.phrase", "target": "member", "domains": ["string"]},
            ],
        }
    else:
        tag = None

    return {
        "catalog_schema_version": "1",
        "semantic_dimensions": [
            parsed_text,
            *([tag] if tag is not None else []),
            _scalar_dimension("intrinsic", "raw_markdown", "raw markdown"),
            _ordered_dimension("region", "region_path", "region path"),
            _ordered_dimension("semantic_path", "path_hierarchy", "path hierarchy"),
            _scalar_dimension("semantic_path", "path_component", "path component"),
        ],
        "graph": {
            "node_kinds": ["scope", "semantic_object", "semantic_region", "semantic_unit"],
            "discovery": discovery,
            "relations": [
                _relation("body_wikilink", "linked_to", "body link", ["semantic_unit"], ["semantic_object", "semantic_region"]),
                _relation("structural", "contains_scope", "scope containment", ["scope"], ["scope"]),
                _relation("structural", "contains_object", "object containment", ["scope"], ["semantic_object"]),
                _relation("structural", "contains_region", "region containment", ["semantic_object", "semantic_region"], ["semantic_region"]),
                _relation("structural", "contains_unit", "unit containment", ["semantic_object", "semantic_region"], ["semantic_unit"]),
            ],
        },
        "vector": {
            "operator": "vector.semantic_similarity",
            "query": {
                "shape": "string",
                "requirement": "exactly one non-empty string",
                "segmentation": False,
                "truncation": False,
                "deterministic_enrichment": False,
            },
            "targets": (
                [{"target_kind": "semantic_unit", "input": "exact canonical parsed_text"}]
                if semantic_unit_target
                else []
            ),
        },
        "operators": {name: dict(value) for name, value in _OPERATORS.items()},
    }