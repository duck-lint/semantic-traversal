from __future__ import annotations

import tempfile
import unittest
from typing import Any
from pathlib import Path
from unittest.mock import patch

from semantic_traversal.llm import StubLLMBackend
from semantic_traversal.probes import probe_new_thread_minimal_turn, probe_same_thread_continuation_turn
from semantic_traversal.runtime import run_thread_turn
from semantic_traversal.storage import load_json, read_ledger


class _FakeSentenceTransformer:
    def __init__(self, model_name: str, device: str | None = None, **kwargs: Any) -> None:
        self.model_name = model_name
        self.device = device

    def encode(self, texts: list[str] | str, **kwargs: Any) -> list[list[float]]:
        if isinstance(texts, str):
            texts = [texts]
        return [[1.0, float(len(text))] for text in texts]

    def encode_document(self, texts: list[str] | str, **kwargs: Any) -> list[list[float]]:
        return self.encode(texts, **kwargs)

    def encode_query(self, texts: list[str] | str, **kwargs: Any) -> list[list[float]]:
        return self.encode(texts, **kwargs)


def _fake_sentence_transformers_import_module(name: str):
    if name == "sentence_transformers":
        return type("FakeSentenceTransformersModule", (), {"SentenceTransformer": _FakeSentenceTransformer})()
    raise ModuleNotFoundError(name)


class FirstBuildTargetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._sentence_transformers_patcher = patch(
            "semantic_traversal.embeddings.import_module",
            side_effect=_fake_sentence_transformers_import_module,
        )
        cls._sentence_transformers_patcher.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls._sentence_transformers_patcher.stop()

    def test_probe_new_thread_minimal_turn(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            result = probe_new_thread_minimal_turn(Path(temp_dir), llm_backend=StubLLMBackend())
            self.assertEqual(result["status"], "pass")
            self.assertEqual(result["ledger_count"], 1)
            self.assertEqual(result["runtime_outcome"], "blocked")

    def test_probe_same_thread_continuation_turn(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            result = probe_same_thread_continuation_turn(Path(temp_dir), llm_backend=StubLLMBackend())
            self.assertEqual(result["status"], "pass")
            self.assertEqual(result["ledger_count_before"], 1)
            self.assertEqual(result["ledger_count_after"], 2)
            self.assertEqual(result["parent_hash"], result["previous_hash"])
            self.assertEqual(result["runtime_outcome"], "blocked")

    def test_cli_runtime_contract_uses_same_shared_runner_shape(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            turn = run_thread_turn(
                repo_root=Path(".").resolve(),
                data_root=Path(temp_dir),
                user_input="Hello from the shared runner.",
                llm_backend=StubLLMBackend(prefix="Shared runner"),
            )
            thread_document = load_json(turn.conversation_thread_path)
            ledger = read_ledger(turn.thread_ledger_path)
            self.assertIsNotNone(thread_document)
            self.assertEqual(turn.turn_id, 1)
            self.assertEqual(len(ledger), 1)
            self.assertEqual(thread_document["thread_id"], turn.thread_id)
            self.assertEqual(turn.runtime_outcome, "blocked")
            self.assertIsNone(turn.assistant_response)


if __name__ == "__main__":
    unittest.main()
