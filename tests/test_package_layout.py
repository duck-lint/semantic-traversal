import importlib.metadata
from pathlib import Path
import subprocess
import sys
import unittest

import semantic_traversal


class PackageLayoutTests(unittest.TestCase):
    def test_new_namespace_is_importable_and_old_source_tree_is_absent(self):
        self.assertEqual(Path(semantic_traversal.__file__).parent.name, "semantic_traversal")
        repository_root = Path(__file__).parents[1]
        old_namespace = "ugh" + "_parser"
        self.assertFalse(repository_root.joinpath("src", old_namespace).exists())
        result = subprocess.run(
            [sys.executable, "-S", "-c", "import " + old_namespace],
            cwd=repository_root,
            env={"PYTHONPATH": str(repository_root / "src")},
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(result.returncode, 0)

    def test_console_entry_point_targets_new_namespace(self):
        distribution = importlib.metadata.distribution("semantic-traversal")
        entry_points = {
            entry_point.name: entry_point.value
            for entry_point in distribution.entry_points
            if entry_point.group == "console_scripts"
        }
        self.assertEqual(entry_points["semantic-traversal"], "semantic_traversal.cli:main")


if __name__ == "__main__":
    unittest.main()
