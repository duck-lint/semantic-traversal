from __future__ import annotations

import unittest
from pathlib import Path

from semantic_traversal.config import load_runtime_config


REPO_ROOT = Path(__file__).resolve().parent.parent


class RuntimeConfigTests(unittest.TestCase):
    def test_runtime_config_loads_without_dead_coverage_block(self) -> None:
        config = load_runtime_config(repo_root=REPO_ROOT)
        self.assertIn("graph_traversal", config.raw)
        self.assertIn("runtime", config.raw)
        self.assertNotIn("coverage", config.raw)
        self.assertGreater(config.max_retrieval_chunks, 0)
        self.assertTrue(config.graph_traversal_enabled)
        self.assertEqual(config.graph_traversal_hop_limit, 1)
        self.assertFalse(hasattr(config, "coverage_require_surface_contributions"))


if __name__ == "__main__":
    unittest.main()
