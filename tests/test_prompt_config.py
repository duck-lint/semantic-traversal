from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from semantic_traversal.config import ConfigError, load_runtime_config
from semantic_traversal.semantic_compiler import _render_ollama_prompt


REPO_ROOT = Path(__file__).resolve().parent.parent


class PromptConfigTests(unittest.TestCase):
    def test_ollama_prompt_renderer_uses_config_template_only(self) -> None:
        prompt = _render_ollama_prompt(
            packet={"raw_user_input": "hello"},
            template="Compiler prompt starts here.\n{packet}",
        )

        self.assertTrue(prompt.startswith("Compiler prompt starts here."))
        self.assertIn('"raw_user_input": "hello"', prompt)
        self.assertNotIn("Non-negotiable compiler contract", prompt)
        self.assertNotIn("Editable compiler instruction", prompt)

    def test_semantic_compiler_prompt_template_requires_packet_marker(self) -> None:
        config_text = (REPO_ROOT / "semantic_traversal.runtime.yaml").read_text(encoding="utf-8")
        config_text = config_text.replace("{packet}", "PACKET_MARKER_REMOVED")

        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "semantic_traversal.runtime.yaml"
            config_path.write_text(config_text, encoding="utf-8")

            with self.assertRaises(ConfigError):
                load_runtime_config(repo_root=REPO_ROOT, config_path=str(config_path))

    def test_semantic_compiler_prompt_template_uses_planner_retrieval_plan(self) -> None:
        config = load_runtime_config(repo_root=REPO_ROOT)
        self.assertIn("planner_retrieval_plan", config.semantic_compiler_prompt_template)
        self.assertNotIn("scope_filters", config.semantic_compiler_prompt_template)


if __name__ == "__main__":
    unittest.main()
