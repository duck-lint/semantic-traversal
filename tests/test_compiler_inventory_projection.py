from __future__ import annotations

import copy
import hashlib
import tempfile
import unittest
from pathlib import Path

from semantic_traversal.config import ConfigError, RuntimeConfig, load_runtime_config
from semantic_traversal.resource_inventory import build_compiler_inventory_projection, inventory_policy_hash
from semantic_traversal.semantic_compiler import _render_ollama_prompt


REPO_ROOT = Path(__file__).resolve().parents[1]


class CompilerInventoryProjectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = load_runtime_config(repo_root=REPO_ROOT)

    def _inventory(self) -> dict:
        return {
            "inventory_diagnostics": {"status": "valid"},
            "observed_source_labels": [{"label": "vault", "count": 10}],
            "frontmatter_facet_details": {
                "note_type": {
                    "observed_unique_count": 2,
                    "values": [{"value": "journal_entry", "count": 8}, {"value": "book", "count": 2}],
                },
                "uuid": {
                    "observed_unique_count": 500,
                    "values": [{"value": "private-id", "count": 1}],
                },
            },
            "path_topology": {
                "levels": {
                    "1": {"values": [{"value": "Journal", "count": 8}]},
                    "2": {"values": [{"value": "Journal/Daily", "count": 8}]},
                }
            },
            "capabilities": {
                "exact_fts": {"projection_status": "valid"},
                "vector": {"table_present": True, "validation_status": "valid"},
                "graph": {
                    "nodes": {"table_present": True},
                    "edges": {"table_present": True},
                },
                "temporal": {"table_present": False},
            },
            "frontmatter_facets": {"note_type": [{"value": "journal_entry", "count": 8}]},
            "semantic_chunk_surface": {
                "operator_visibility": {"vector_search": "embedding"},
                "admitted_frontmatter_fields": list(self.config.chunking_semantic_frontmatter_fields),
            },
        }

    def test_all_admitted_fields_survive_and_only_low_cardinality_values_are_retained(self) -> None:
        projection, diagnostics = build_compiler_inventory_projection(inventory_summary=self._inventory(), config=self.config)
        self.assertEqual(projection["semantic_chunk"]["admitted_frontmatter_fields"], sorted(self.config.chunking_semantic_frontmatter_fields))
        self.assertEqual(projection["low_cardinality_values"]["note_type"], ["book", "journal_entry"])
        self.assertNotIn("uuid", projection["low_cardinality_values"])
        self.assertIn("uuid", projection["value_enumeration_omitted_for"])
        self.assertEqual(diagnostics["admitted_field_count"], len(self.config.chunking_semantic_frontmatter_fields))

    def test_projection_is_order_independent_and_does_not_mutate_full_inventory(self) -> None:
        inventory = self._inventory()
        before = copy.deepcopy(inventory)
        reordered = copy.deepcopy(inventory)
        reordered["frontmatter_facet_details"]["note_type"]["values"].reverse()
        first, first_diagnostics = build_compiler_inventory_projection(inventory_summary=inventory, config=self.config)
        second, second_diagnostics = build_compiler_inventory_projection(inventory_summary=reordered, config=self.config)
        self.assertEqual(first, second)
        self.assertEqual(first_diagnostics["projection_sha256"], second_diagnostics["projection_sha256"])
        self.assertEqual(inventory, before)

    def test_projection_removes_competing_inventory_surfaces(self) -> None:
        projection, _ = build_compiler_inventory_projection(inventory_summary=self._inventory(), config=self.config)
        text = str(projection)
        self.assertNotIn("frontmatter_facets", text)
        self.assertNotIn("frontmatter_facet_details", text)
        self.assertNotIn("operator_visibility", text)
        self.assertIn("exact_chunk_search", projection["available_retrieval_operators"])
        self.assertNotIn("temporal_retrieve", projection["available_retrieval_operators"])

    def test_prompt_contains_projection_once_without_full_inventory(self) -> None:
        projection, _ = build_compiler_inventory_projection(inventory_summary=self._inventory(), config=self.config)
        sentinel = "PROJECTION_SENTINEL_ONLY"
        projection["sentinel"] = sentinel
        packet = {
            "raw_user_input": "probe",
            "resource_inventory_summary": projection,
        }
        prompt = _render_ollama_prompt(packet=packet, template="{resource_inventory_summary}\n{packet}", planner_defaults=self.config.retrieval_planner_defaults)
        self.assertEqual(prompt.count(sentinel), 1)
        self.assertNotIn("frontmatter_facet_details", prompt)

    def test_projection_budget_trims_optional_content_but_preserves_fields(self) -> None:
        raw = copy.deepcopy(self.config.raw)
        raw["semantic_compiler"]["inventory_projection"].update({"max_chars": 1_500, "max_values_per_field": 12, "max_path_values": 48})
        config = RuntimeConfig(repo_root=self.config.repo_root, config_path=self.config.config_path, raw=raw)
        with self.assertRaises(ValueError):
            build_compiler_inventory_projection(inventory_summary=self._inventory(), config=config)

    def test_projection_controls_do_not_change_persisted_inventory_policy_hash(self) -> None:
        raw = copy.deepcopy(self.config.raw)
        original_hash = inventory_policy_hash(self.config)
        raw["semantic_compiler"]["inventory_projection"]["max_chars"] = 13_000
        changed = RuntimeConfig(repo_root=self.config.repo_root, config_path=self.config.config_path, raw=raw)
        self.assertEqual(inventory_policy_hash(changed), original_hash)

    def test_projection_controls_are_positive_and_bounded(self) -> None:
        text = (REPO_ROOT / "semantic_traversal.runtime.yaml").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "semantic_traversal.runtime.yaml"
            path.write_text(text.replace("    max_chars: 24000", "    max_chars: 0"), encoding="utf-8")
            with self.assertRaises(ConfigError):
                load_runtime_config(repo_root=REPO_ROOT, config_path=str(path))

    def test_projection_hash_is_sha256_of_deterministic_serialization(self) -> None:
        projection, diagnostics = build_compiler_inventory_projection(inventory_summary=self._inventory(), config=self.config)
        import json

        serialized = json.dumps(projection, ensure_ascii=True, sort_keys=True, indent=2)
        self.assertEqual(diagnostics["projection_sha256"], hashlib.sha256(serialized.encode()).hexdigest())


if __name__ == "__main__":
    unittest.main()
