from __future__ import annotations

import builtins
import importlib
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from semantic_traversal.config import load_runtime_config


REPO_ROOT = Path(__file__).resolve().parent.parent

class LazyOpenAIImportTests(unittest.TestCase):
    def _import_llm_module(self):
        return importlib.import_module("semantic_traversal.llm")

    def _config(self):
        return load_runtime_config(repo_root=REPO_ROOT)

    def test_module_import_succeeds_without_openai_installed(self) -> None:
        original_import = builtins.__import__
        semantic_traversal_package = importlib.import_module("semantic_traversal")

        def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
            if name == "openai" or name.startswith("openai."):
                raise ModuleNotFoundError("No module named 'openai'")
            return original_import(name, globals, locals, fromlist, level)

        sys.modules.pop("semantic_traversal.llm", None)
        semantic_traversal_package.__dict__.pop("llm", None)

        with patch("builtins.__import__", side_effect=guarded_import):
            llm_module = importlib.import_module("semantic_traversal.llm")

        self.assertIs(llm_module, sys.modules["semantic_traversal.llm"])
        self.assertTrue(hasattr(llm_module, "resolve_llm_backend"))

    def test_stub_mode_does_not_import_openai(self) -> None:
        llm_module = self._import_llm_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=False):
                with patch.object(
                    llm_module,
                    "import_module",
                    side_effect=AssertionError("stub mode should not import openai"),
                ) as mocked_import:
                    backend = llm_module.resolve_llm_backend(repo_root=Path(temp_dir), config=self._config(), llm_mode="stub")

        self.assertIsInstance(backend, llm_module.StubLLMBackend)
        mocked_import.assert_not_called()

    def test_auto_mode_without_api_key_returns_stub_without_openai_import(self) -> None:
        llm_module = self._import_llm_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.dict(os.environ, {"OPENAI_API_KEY": "", "OPENAI_MODEL": ""}, clear=False):
                with patch.object(
                    llm_module,
                    "import_module",
                    side_effect=AssertionError("auto mode without a key should not import openai"),
                ) as mocked_import:
                    backend = llm_module.resolve_llm_backend(repo_root=Path(temp_dir), config=self._config(), llm_mode="auto")

        self.assertIsInstance(backend, llm_module.StubLLMBackend)
        mocked_import.assert_not_called()

    def test_live_mode_missing_openai_sdk_raises_actionable_error(self) -> None:
        llm_module = self._import_llm_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=False):
                with patch.object(
                    llm_module,
                    "import_module",
                    side_effect=ModuleNotFoundError("No module named 'openai'"),
                ) as mocked_import:
                    with self.assertRaises(llm_module.LiveLLMNotConfigured) as exc_info:
                        llm_module.resolve_llm_backend(repo_root=Path(temp_dir), config=self._config(), llm_mode="live")

        self.assertIn("python -m pip install openai", str(exc_info.exception))
        self.assertIn("--llm-mode stub", str(exc_info.exception))
        mocked_import.assert_called_once_with("openai")

    def test_live_mode_missing_openai_client_symbol_raises_actionable_error(self) -> None:
        llm_module = self._import_llm_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=False):
                with patch.object(
                    llm_module,
                    "import_module",
                    return_value=SimpleNamespace(),
                ) as mocked_import:
                    with self.assertRaises(llm_module.LiveLLMNotConfigured) as exc_info:
                        llm_module.resolve_llm_backend(repo_root=Path(temp_dir), config=self._config(), llm_mode="live")

        self.assertIn("openai.OpenAI", str(exc_info.exception))
        self.assertIn("--llm-mode stub", str(exc_info.exception))
        mocked_import.assert_called_once_with("openai")
