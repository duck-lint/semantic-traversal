from __future__ import annotations

import unittest
import tempfile
from pathlib import Path

from semantic_traversal.config import ConfigError, load_runtime_config


REPO_ROOT = Path(__file__).resolve().parent.parent


class RuntimeConfigTests(unittest.TestCase):
    def test_runtime_config_loads_without_dead_coverage_block(self) -> None:
        config = load_runtime_config(repo_root=REPO_ROOT)
        self.assertIn("graph_traversal", config.raw)
        self.assertIn("retrieval", config.raw)
        self.assertIn("scope_aliases", config.raw["retrieval"])
        self.assertNotIn("runtime", config.raw)
        self.assertNotIn("coverage", config.raw)
        self.assertGreater(config.max_retrieval_chunks, 0)
        self.assertTrue(config.graph_traversal_enabled)
        self.assertGreater(config.retrieval_graph_max_depth, 0)
        self.assertFalse(hasattr(config, "coverage_require_surface_contributions"))
        self.assertIn("journal", config.retrieval_scope_aliases)
        self.assertTrue(config.chunking_low_signal_apparatus_enabled)
        self.assertTrue(config.chunking_low_signal_apparatus_skip_during_ingest)
        self.assertTrue(config.chunking_low_signal_apparatus_skip_during_graph_representatives)
        self.assertIn("OXFORD", config.chunking_low_signal_apparatus_exact_lines)
        self.assertIn("DOI", config.chunking_low_signal_apparatus_prefixes)
        self.assertIn("all rights reserved", config.chunking_low_signal_apparatus_contains)
        self.assertEqual(config.chunking_low_signal_apparatus_short_all_caps_max_chars, 80)

    def test_relative_data_root_resolves_under_vault_root(self) -> None:
        config = load_runtime_config(repo_root=REPO_ROOT)
        self.assertEqual(config.data_root, config.vault_root / ".semantic-traversal")

    def test_invalid_graph_direction_is_rejected_during_config_loading(self) -> None:
        source = (REPO_ROOT / "semantic_traversal.runtime.yaml").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "invalid-direction.yaml"
            path.write_text(source.replace("direction: outbound", "direction: sideways", 1), encoding="utf-8")
            with self.assertRaisesRegex(ConfigError, "graph_traversal.direction"):
                load_runtime_config(repo_root=REPO_ROOT, config_path=str(path))

    def test_vector_controls_are_yaml_owned_and_validated(self) -> None:
        config = load_runtime_config(repo_root=REPO_ROOT)
        self.assertEqual(config.retrieval_vector_min_similarity, 0.5)
        self.assertEqual(config.retrieval_vector_per_query_max_candidates, 50)
        self.assertEqual(config.retrieval_vector_max_chunks_per_note, 4)
        self.assertIsNone(config.embedding_dimensions)
        source = (REPO_ROOT / "semantic_traversal.runtime.yaml").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "invalid-vector.yaml"
            path.write_text(source.replace("min_similarity: 0.5", "min_similarity: 1.5", 1), encoding="utf-8")
            with self.assertRaisesRegex(ConfigError, "min_similarity"):
                load_runtime_config(repo_root=REPO_ROOT, config_path=str(path))

    def test_inventory_controls_are_yaml_owned(self) -> None:
        config = load_runtime_config(repo_root=REPO_ROOT)
        self.assertEqual(config.retrieval_resource_inventory["schema_version"], 1)
        self.assertEqual(config.retrieval_resource_inventory["path_depth"], 2)
        self.assertGreater(config.retrieval_resource_inventory["max_values_per_facet"], 0)



if __name__ == "__main__":
    unittest.main()
