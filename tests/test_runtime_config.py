import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from semantic_traversal.runtime.config import (
    CandidateSelectionConfig,
    RuntimeConfigError,
    load_runtime_config,
)


_ROUTER = "router:\n  provider: openai\n  model: router\n  timeout_seconds: 1\n  prompt: router prompt\n"
_RETRIEVAL = "retrieval_inference:\n  provider: openai\n  model: retrieval\n  timeout_seconds: 1\n  prompt: retrieval prompt\n"


def config_text(selection: str = "  max_candidates: 120\n  protected_owner_fraction: 0.20\n") -> str:
    return _ROUTER + _RETRIEVAL + "candidate_selection:\n" + selection


class RuntimeConfigTests(unittest.TestCase):
    def load(self, text: str):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "runtime.yaml"
            path.write_text(text, encoding="utf-8")
            return load_runtime_config(path)

    def test_documented_three_section_values_parse(self):
        loaded = load_runtime_config(Path(__file__).parents[1] / "docs" / "runtime_config.yaml")
        self.assertEqual(loaded.candidate_selection, CandidateSelectionConfig(120, 0.20))

    def test_zero_capacity_is_legal(self):
        loaded = self.load(config_text("  max_candidates: 0\n  protected_owner_fraction: 0.20\n"))
        self.assertEqual(loaded.candidate_selection.max_candidates, 0)

    def test_candidate_selection_policy_values_are_strict(self):
        for value in ("true", "false", "-1", "1.5", '"1"'):
            with self.subTest(value=value), self.assertRaises(RuntimeConfigError):
                self.load(config_text(f"  max_candidates: {value}\n  protected_owner_fraction: 0.20\n"))
        for value in ("true", "false", "0", "-0.1", "1.1", ".nan", ".inf", "-.inf"):
            with self.subTest(value=value), self.assertRaises(RuntimeConfigError):
                self.load(config_text(f"  max_candidates: 120\n  protected_owner_fraction: {value}\n"))

    def test_root_and_candidate_selection_shapes_are_exact(self):
        cases = (
            _ROUTER + _RETRIEVAL,
            config_text("  max_candidates: 120\n"),
            config_text("  max_candidates: 120\n  protected_owner_fraction: 0.20\n  extra: false\n"),
            config_text() + "extra_root: true\n",
            _ROUTER + _RETRIEVAL + "candidate_selection: []\n",
        )
        for text in cases:
            with self.subTest(text=text), self.assertRaises(RuntimeConfigError):
                self.load(text)


if __name__ == "__main__":
    unittest.main()
