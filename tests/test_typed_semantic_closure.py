from __future__ import annotations

import sqlite3
import unittest
from pathlib import Path

from semantic_traversal.config import load_runtime_config
from semantic_traversal.resource_inventory import (
    COMPILER_INVENTORY_PROJECTION_VERSION,
    INVENTORY_SCHEMA_VERSION,
    RETRIEVAL_SURFACE_MANIFEST_VERSION,
    build_compiler_inventory_projection,
    build_resource_inventory,
)
from semantic_traversal.retrieval_resolver import bind_retrieval_plan


REPO_ROOT = Path(__file__).resolve().parents[1]


class TypedSemanticClosureContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_runtime_config(repo_root=REPO_ROOT)

    def test_versions_and_closed_world_grammar_are_generated(self) -> None:
        connection = sqlite3.connect(":memory:")
        connection.executescript(
            """
            CREATE TABLE notes (source_root_label TEXT, relative_path TEXT, frontmatter_semantics_json TEXT);
            CREATE TABLE chunks (chunk_id TEXT);
            CREATE TABLE chunks_fts (chunk_id TEXT);
            CREATE TABLE temporal_anchors (anchor_type TEXT, authority TEXT, precision TEXT, conflict_group TEXT, unresolved INTEGER);
            CREATE TABLE graph_nodes (node_type TEXT);
            CREATE TABLE graph_edges (edge_type TEXT, metadata_json TEXT);
            """
        )
        inventory = build_resource_inventory(connection=connection, config=self.config)
        manifest = inventory["retrieval_surface_manifest"]
        self.assertEqual(INVENTORY_SCHEMA_VERSION, 4)
        self.assertEqual(RETRIEVAL_SURFACE_MANIFEST_VERSION, 2)
        self.assertTrue(manifest["closed_world"])
        self.assertEqual(manifest["execution_model"], "contextual_surface_closure_then_relation_evaluation")
        for key in ("semantic_objects", "semantic_units", "type_identifiers", "relations", "transitions", "relation_evaluators", "bounds"):
            self.assertIn(key, manifest)
        pairs = {(item["from"], item["to"]) for item in manifest["transitions"]}
        self.assertIn(("resolved_context", "lexical_chunk_search"), pairs)
        self.assertIn(("semantic_unit", "parent_note"), pairs)

    def test_projection_retains_structural_grammar_under_budget(self) -> None:
        connection = sqlite3.connect(":memory:")
        connection.executescript("CREATE TABLE notes (source_root_label TEXT, relative_path TEXT, frontmatter_semantics_json TEXT); CREATE TABLE chunks (chunk_id TEXT);")
        inventory = build_resource_inventory(connection=connection, config=self.config)
        projection, diagnostics = build_compiler_inventory_projection(inventory_summary=inventory, config=self.config)
        self.assertEqual(projection["projection_version"], COMPILER_INVENTORY_PROJECTION_VERSION)
        self.assertEqual(projection["execution_model"], "contextual_surface_closure_then_relation_evaluation")
        self.assertIn("type_identifiers", projection)
        self.assertIn("transitions", projection)
        self.assertLessEqual(diagnostics["projection_chars"], self.config.semantic_compiler_inventory_projection["max_chars"])

    def test_binding_preserves_requested_layers_and_adds_optional_support(self) -> None:
        plan = {
            "concepts": ["synthetic object"], "resolved_referents": ["object A", "object B"],
            "literal_terms": [], "semantic_queries": ["development of synthetic object"],
            "lexical_queries": ["synthetic object"], "graph_seeds": [],
            "evidence_requirements": ["chronology"],
            "retrieval_layers": [{"operator": "temporal_retrieve", "required": True, "mode": "earliest"}],
        }
        bound, _, _ = bind_retrieval_plan(planner_retrieval_plan=plan, inventory_summary={}, config=self.config)
        self.assertEqual(bound["requested_retrieval_layers"][0]["operator"], "temporal_retrieve")
        self.assertEqual(bound["resolved_referents"], ["object A", "object B"])
        expanded = {layer["operator"] for layer in bound["runtime_expanded_layers"]}
        self.assertTrue({"lexical_chunk_search", "vector_search", "graph_expand"}.issubset(expanded))
        self.assertTrue(all(not layer.get("required") for layer in bound["expanded_support_surfaces"]))

    def test_binding_does_not_turn_referents_into_filters(self) -> None:
        plan = {"concepts": [], "resolved_referents": ["unlisted semantic subject"], "semantic_queries": [], "lexical_queries": [], "literal_terms": [], "graph_seeds": [], "evidence_requirements": [], "retrieval_layers": []}
        bound, _, _ = bind_retrieval_plan(planner_retrieval_plan=plan, inventory_summary={"frontmatter_facets": {}}, config=self.config)
        self.assertEqual(bound["resolved_referents"], ["unlisted semantic subject"])
        self.assertEqual(bound["scope_filters"]["note_type"], [])
        self.assertNotIn("scope_alias", str(bound))

    def test_projection_policy_controls_are_not_inventory_policy_fields(self) -> None:
        self.assertNotIn("projection", str(self.config.retrieval_resource_inventory))


if __name__ == "__main__":
    unittest.main()
