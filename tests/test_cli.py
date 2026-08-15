import contextlib
import io
import json
import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import numpy as np

from ugh_parser import EmbeddingContract, EmbeddingProviderError
from ugh_parser.cli import main


class CliProvider:
    def __init__(self, model="qwen3-embedding:0.6b", base_url=None):
        self.contract = EmbeddingContract(model, "sha256-" + "a" * 64, 1024, "float32", "l2", "cosine", 100)
        self.calls = []

    def embed(self, text, *, truncate=False):
        self.calls.append((text, truncate))
        if truncate:
            raise AssertionError("CLI must preserve provider truncation disabled")
        if len(text) > self.contract.input_capacity:
            raise EmbeddingProviderError("capacity", capacity_exceeded=True)
        vector = np.zeros(1024, dtype=np.float32)
        vector[0] = 1
        vector[1] = (hash(text) % 17) + 1
        return vector


class CliTests(unittest.TestCase):
    def _fixture(self, root: Path) -> tuple[Path, Path]:
        vault = root / "vault"
        vault.mkdir()
        (vault / "Target.md").write_text("---\nuuid: target\n---\ntarget body\n", encoding="utf-8")
        (vault / "Zero.md").write_text("---\nuuid: zero\ntags: [empty]\n---\n", encoding="utf-8")
        source = vault / "Source.md"
        source.write_text("---\nuuid: source\nrelation: \"[[Target]]\"\n---\nsource body\n", encoding="utf-8")
        config = root / "config.yaml"
        config.write_text(
            "vault_name: test\nuuid_field: uuid\nexcluded_folders: []\nsemantic_identifier_fields: [tags, relation]\n",
            encoding="utf-8",
        )
        return vault, config

    def _run(self, *argv):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = main(list(argv))
        return code, stdout.getvalue(), stderr.getvalue()

    def test_command_tree_requires_explicit_build_paths(self):
        with self.assertRaises(SystemExit):
            main(["inspect", "object", "--uuid", "x"])
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            with self.assertRaises(SystemExit) as raised:
                main(["--help"])
        self.assertEqual(raised.exception.code, 0)
        self.assertIn("build", stdout.getvalue())
        self.assertIn("vector", stdout.getvalue())
        self.assertNotIn("retrieve", stdout.getvalue())

    def test_build_creates_exact_artifacts_and_emits_stages(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            vault, config = self._fixture(root)
            output = root / "build"
            with patch("ugh_parser.cli.OllamaEmbeddingProvider", CliProvider):
                code, stdout, stderr = self._run("build", "--vault", str(vault), "--config", str(config), "--output", str(output), "--json")
            self.assertEqual(code, 0)
            self.assertEqual(set(json.loads(stdout)), {"substrate", "vectors"})
            self.assertEqual({path.name for path in output.iterdir()}, {"substrate.sqlite3", "vectors.npy"})
            for stage in ("config", "parse", "materialize", "resolve", "canonical", "substrate", "exact", "lexical", "graph", "vector", "verify", "complete"):
                self.assertIn(stage, stderr)

    def test_nonempty_output_is_rejected_without_mutation(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            vault, config = self._fixture(root)
            output = root / "build"
            output.mkdir()
            sentinel = output / "operator.txt"
            sentinel.write_text("keep", encoding="utf-8")
            code, _, stderr = self._run("build", "--vault", str(vault), "--config", str(config), "--output", str(output))
            self.assertNotEqual(code, 0)
            self.assertIn("non-empty", stderr)
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep")

    def test_inspection_and_native_surfaces_use_completed_build(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            vault, config = self._fixture(root)
            output = root / "build"
            with patch("ugh_parser.cli.OllamaEmbeddingProvider", CliProvider):
                self.assertEqual(self._run("build", "--vault", str(vault), "--config", str(config), "--output", str(output))[0], 0)
                code, stdout, _ = self._run("inspect", "counts", "--build", str(output), "--json")
                self.assertEqual(code, 0)
                self.assertEqual(json.loads(stdout)["canonical_objects"], 3)
                code, stdout, _ = self._run("exact", "--build", str(output), "--field-class", "semantic_identifier", "--field-name", "relation", "--value-type", "string", "--value", "[[Target]]", "--json")
                self.assertEqual(code, 0)
                self.assertEqual(len(json.loads(stdout)), 1)
                code, stdout, _ = self._run("inspect", "object", "--build", str(output), "--path", "Source.md", "--json")
                self.assertEqual(code, 0)
                self.assertEqual(json.loads(stdout)["source_object_uuid"], "source")
                code, _, _ = self._run("inspect", "verify", "--build", str(output), "--json")
                self.assertEqual(code, 0)
