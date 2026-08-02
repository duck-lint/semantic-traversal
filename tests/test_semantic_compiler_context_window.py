from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError

from semantic_traversal.config import ConfigError, load_runtime_config
from semantic_traversal.semantic_compiler import resolve_semantic_compiler_backend


REPO_ROOT = Path(__file__).resolve().parent.parent


class _MockOllamaResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = json.dumps(payload).encode("utf-8")

    def __enter__(self) -> "_MockOllamaResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return self._payload


class SemanticCompilerContextWindowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.source = (REPO_ROOT / "semantic_traversal.runtime.yaml").read_text(encoding="utf-8")

    def _load_mutated(self, replacement: str) -> object:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "runtime.yaml"
            path.write_text(replacement, encoding="utf-8")
            # Keep the temporary directory alive for the returned config's path.
            return load_runtime_config(repo_root=REPO_ROOT, config_path=str(path))

    def test_checked_in_context_window_is_yaml_owned(self) -> None:
        config = load_runtime_config(repo_root=REPO_ROOT)
        self.assertEqual(config.semantic_compiler_context_window_tokens, 16384)

    def test_mutated_context_window_reaches_runtime_property(self) -> None:
        config = self._load_mutated(self.source.replace("  context_window_tokens: 16384", "  context_window_tokens: 12288", 1))
        self.assertEqual(config.semantic_compiler_context_window_tokens, 12288)

    def test_context_window_must_be_positive_and_exact_integer(self) -> None:
        for value in ("0", "-1"):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ConfigError, "context_window_tokens must be greater than zero"):
                    self._load_mutated(self.source.replace("  context_window_tokens: 16384", f"  context_window_tokens: {value}", 1))
        with self.assertRaisesRegex(ConfigError, "context_window_tokens.*expected int"):
            self._load_mutated(self.source.replace("  context_window_tokens: 16384", '  context_window_tokens: "16384"', 1))

    def test_context_window_is_required(self) -> None:
        with self.assertRaisesRegex(ConfigError, "Missing required runtime config field: root.semantic_compiler.context_window_tokens"):
            self._load_mutated(self.source.replace("  context_window_tokens: 16384\n", "", 1))

    def test_resolver_propagates_mutated_yaml_value_into_exact_request(self) -> None:
        source = self.source.replace("  context_window_tokens: 16384", "  context_window_tokens: 12288", 1)
        config = self._load_mutated(source)
        captured: dict[str, object] = {}

        def fake_urlopen(http_request: object, timeout: int) -> _MockOllamaResponse:
            captured["request"] = http_request
            captured["timeout"] = timeout
            return _MockOllamaResponse({"response": '{"raw_user_input":"probe"}', "prompt_eval_count": 6173})

        with patch("semantic_traversal.semantic_compiler.request.urlopen", side_effect=fake_urlopen):
            response = resolve_semantic_compiler_backend(config=config).compile_turn({"raw_user_input": "probe"})

        request = captured["request"]
        request_payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(request_payload["options"], {"num_ctx": 12288})
        self.assertEqual(request_payload["model"], "qwen3:8b")
        self.assertFalse(request_payload["stream"])
        self.assertNotIn("num_predict", request_payload["options"])
        self.assertNotIn("temperature", request_payload["options"])
        self.assertNotIn("top_k", request_payload["options"])
        self.assertNotIn("top_p", request_payload["options"])
        self.assertNotIn("seed", request_payload["options"])
        self.assertNotIn("keep_alive", request_payload["options"])
        self.assertEqual(response.metadata["semantic_compiler_context_window_tokens"], 12288)
        self.assertEqual(response.metadata["ollama_prompt_eval_count"], 6173)

    def test_request_failure_preserves_context_provenance(self) -> None:
        config = load_runtime_config(repo_root=REPO_ROOT)
        with patch("semantic_traversal.semantic_compiler.request.urlopen", side_effect=URLError("offline")):
            response = resolve_semantic_compiler_backend(config=config).compile_turn({"raw_user_input": "probe"})
        self.assertEqual(response.status, "unavailable")
        self.assertEqual(response.metadata["semantic_compiler_context_window_tokens"], 16384)
        self.assertNotIn("ollama_prompt_eval_count", response.metadata)

    def test_no_model_outcome_preserves_context_provenance(self) -> None:
        config = self._load_mutated(self.source.replace("  model: qwen3:8b", "  model: null", 1))
        response = resolve_semantic_compiler_backend(config=config).compile_turn({"raw_user_input": "probe"})
        self.assertEqual(response.status, "unavailable")
        self.assertEqual(response.metadata["semantic_compiler_context_window_tokens"], 16384)
        self.assertNotIn("ollama_prompt_eval_count", response.metadata)


if __name__ == "__main__":
    unittest.main()
