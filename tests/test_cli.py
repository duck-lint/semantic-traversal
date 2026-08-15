import contextlib
import hashlib
import io
import json
import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import numpy as np

from ugh_parser import EmbeddingContract, EmbeddingProviderError, GraphHandle, vector_eligible_targets
from ugh_parser.cli import _graph_traverse, main


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
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            with self.assertRaises(SystemExit):
                main(["build", "--help"])
        self.assertNotIn("skip", stdout.getvalue().lower())

    def test_build_creates_exact_artifacts_and_emits_stages(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            vault, config = self._fixture(root)
            source_before = {path.relative_to(vault): path.read_bytes() for path in vault.rglob("*") if path.is_file()}
            output = root / "build"
            with patch("ugh_parser.cli.OllamaEmbeddingProvider", CliProvider):
                code, stdout, stderr = self._run("build", "--vault", str(vault), "--config", str(config), "--output", str(output), "--json")
            self.assertEqual(code, 0)
            self.assertEqual(set(json.loads(stdout)), {"substrate", "vectors"})
            self.assertEqual({path.name for path in output.iterdir()}, {"substrate.sqlite3", "vectors.npy"})
            self.assertIn("canonical objects: 3; regions: 0; units: 2", stderr)
            self.assertIn("exact entries:", stderr)
            self.assertIn("lexical dimensions:", stderr)
            self.assertIn("occurrences:", stderr)
            self.assertIn("graph nodes:", stderr)
            self.assertIn("edges:", stderr)
            self.assertIn("relation types:", stderr)
            self.assertEqual(source_before, {path.relative_to(vault): path.read_bytes() for path in vault.rglob("*") if path.is_file()})
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
                by_path = json.loads(stdout)
                self.assertEqual(by_path["source_object_uuid"], "source")
                code, stdout, _ = self._run("inspect", "object", "--build", str(output), "--uuid", "source", "--json")
                self.assertEqual(code, 0)
                self.assertEqual(json.loads(stdout), by_path)
                code, stdout, _ = self._run("inspect", "unit", "--build", str(output), "--unit-id", "1", "--json")
                self.assertEqual(code, 0)
                self.assertEqual(json.loads(stdout)["unit_id"], 1)
                code, _, _ = self._run("inspect", "verify", "--build", str(output), "--json")
                self.assertEqual(code, 0)
                code, stdout, _ = self._run("inspect", "artifacts", "--build", str(output), "--json")
                self.assertEqual(code, 0)
                artifacts = json.loads(stdout)
                self.assertTrue(all(Path(item["path"]).is_absolute() and item["exists"] for item in artifacts.values()))
                tables = []
                for _ in range(2):
                    code, stdout, _ = self._run("inspect", "tables", "--build", str(output), "--json")
                    self.assertEqual(code, 0)
                    tables.append(json.loads(stdout))
                self.assertEqual(tables[0], tables[1])
                vectors = output / "vectors.npy"
                original_vectors = vectors.read_bytes()
                vectors.write_bytes(b"corrupted")
                self.assertNotEqual(self._run("inspect", "verify", "--build", str(output))[0], 0)
                vectors.write_bytes(original_vectors)

    def test_cli_delegates_lexical_graph_and_vector_contracts(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            vault, config = self._fixture(root)
            output = root / "build"
            with patch("ugh_parser.cli.OllamaEmbeddingProvider", CliProvider):
                self.assertEqual(self._run("build", "--vault", str(vault), "--config", str(config), "--output", str(output))[0], 0)

            code, stdout, _ = self._run("lexical", "terms", "--build", str(output), "--field-class", "intrinsic", "--field-name", "parsed_text", "--term", "target", "--json")
            self.assertEqual(code, 0)
            self.assertTrue(json.loads(stdout))
            code, stdout, _ = self._run("lexical", "phrase", "--build", str(output), "--field-class", "intrinsic", "--field-name", "parsed_text", "--phrase", "source body", "--json")
            self.assertEqual(code, 0)
            self.assertTrue(json.loads(stdout))
            code, _, _ = self._run("lexical", "terms", "--build", str(output), "--field-class", "intrinsic", "--field-name", "parsed_text", "--term", "two words")
            self.assertNotEqual(code, 0)

            code, stdout, _ = self._run("graph", "discover", "terms", "--build", str(output), "--node-kind", "semantic_object", "--dimension-name", "tag", "--term", "empty", "--json")
            self.assertEqual(code, 0)
            self.assertTrue(json.loads(stdout))
            code, stdout, _ = self._run("graph", "relations", "--build", str(output), "--relation-class", "semantic_identifier", "--relation-name", "relation", "--json")
            self.assertEqual(code, 0)
            self.assertEqual(len(json.loads(stdout)), 1)
            code, stdout, _ = self._run("graph", "traverse", "--build", str(output), "--handle-json", json.dumps({"node_kind": "semantic_object", "identity": ["source"]}), "--relation-class", "semantic_identifier", "--relation-name", "relation", "--direction", "outbound", "--json")
            self.assertEqual(code, 0)
            self.assertEqual(len(json.loads(stdout)), 1)

            query_provider = CliProvider()
            with patch("ugh_parser.cli.OllamaEmbeddingProvider", return_value=query_provider):
                code, stdout, _ = self._run("vector", "--build", str(output), "--query", "query text", "--json")
            self.assertEqual(code, 0)
            self.assertEqual(len(json.loads(stdout)), 3)
            self.assertEqual(query_provider.calls, [("query text", False)])
            with patch("ugh_parser.cli.OllamaEmbeddingProvider", return_value=CliProvider(model="other")):
                self.assertNotEqual(self._run("vector", "--build", str(output), "--query", "query text", "--json")[0], 0)

    def test_counts_use_python_strip_for_non_sqlite_whitespace(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            vault, config = self._fixture(root)
            output = root / "build"
            with patch("ugh_parser.cli.OllamaEmbeddingProvider", CliProvider):
                self.assertEqual(self._run("build", "--vault", str(vault), "--config", str(config), "--output", str(output))[0], 0)
            connection = sqlite3.connect(output / "substrate.sqlite3")
            connection.execute("UPDATE canonical_units SET parsed_text = ? WHERE unit_id = 1", ("\t\n",))
            connection.commit()
            eligible_unit_ids = {
                target.target_identity
                for target in vector_eligible_targets(connection)
                if target.target_kind == "semantic_unit"
            }
            canonical_unit_count = connection.execute("SELECT COUNT(*) FROM canonical_units").fetchone()[0]
            expected_empty = canonical_unit_count - len(eligible_unit_ids)
            connection.close()
            code, stdout, _ = self._run("inspect", "counts", "--build", str(output), "--json")
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(stdout)["empty_units"], expected_empty)

    def test_exact_operand_forms_are_exclusive(self):
        bad = (
            ("--component", "x", "--value-type", "string", "--value", "x"),
            ("--component", "x", "--value", "x"),
            ("--value-type", "string"),
            ("--value", "x"),
            tuple(),
            ("--value-type", "null", "--value", "x"),
        )
        for suffix in bad:
            with self.subTest(suffix=suffix):
                code, _, _ = self._run("exact", "--build", "missing", "--field-class", "x", "--field-name", "y", *suffix)
                self.assertNotEqual(code, 0)

    def test_graph_handle_json_decodes_nested_identity_shapes(self):
        captured = []
        connection = sqlite3.connect(":memory:")
        with patch("ugh_parser.cli._connection", return_value=connection), patch(
            "ugh_parser.cli.graph_traverse", side_effect=lambda _c, handle, *_args: captured.append(handle) or ()
        ):
            for raw, expected in (
                ({"node_kind": "semantic_object", "identity": ["u"]}, ("u",)),
                ({"node_kind": "semantic_unit", "identity": [7]}, (7,)),
                ({"node_kind": "semantic_region", "identity": ["u", ["r1", "r2"]]}, ("u", ("r1", "r2"))),
                ({"node_kind": "scope", "identity": [["a", "b"]]}, (("a", "b"),)),
            ):
                args = type("Args", (), {"build": "unused", "handle_json": json.dumps(raw), "relation_class": "x", "relation_name": "y", "direction": "outbound"})()
                _graph_traverse(args)
                self.assertEqual(captured[-1], GraphHandle(raw["node_kind"], expected))
        connection.close()

    def test_inspect_verify_preserves_completed_artifact_bytes(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            vault, config = self._fixture(root)
            output = root / "build"
            with patch("ugh_parser.cli.OllamaEmbeddingProvider", CliProvider):
                self.assertEqual(self._run("build", "--vault", str(vault), "--config", str(config), "--output", str(output))[0], 0)
            artifact = output / "substrate.sqlite3"
            before = hashlib.sha256(artifact.read_bytes()).hexdigest()
            self.assertEqual(self._run("inspect", "verify", "--build", str(output))[0], 0)
            after = hashlib.sha256(artifact.read_bytes()).hexdigest()
            self.assertEqual(before, after)

    def test_build_reports_parse_resolution_and_provider_failures_without_artifacts(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            vault, config = self._fixture(root)
            (vault / "Broken.md").write_text("not frontmatter", encoding="utf-8")
            output = root / "parse-build"
            code, _, stderr = self._run("build", "--vault", str(vault), "--config", str(config), "--output", str(output))
            self.assertNotEqual(code, 0); self.assertIn("must begin with yaml frontmatter", stderr.lower()); self.assertFalse((output / "substrate.sqlite3").exists())

            (vault / "Broken.md").unlink()
            (vault / "Source.md").write_text("---\nuuid: source\nrelation: \"[[Missing]]\"\n---\nsource body\n", encoding="utf-8")
            output = root / "resolution-build"
            code, _, stderr = self._run("build", "--vault", str(vault), "--config", str(config), "--output", str(output))
            self.assertNotEqual(code, 0); self.assertIn("unresolved", stderr.lower()); self.assertFalse((output / "substrate.sqlite3").exists())

            (vault / "Source.md").write_text("---\nuuid: source\nrelation: \"[[Target]]\"\n---\nsource body\n", encoding="utf-8")
            output = root / "provider-build"
            with patch("ugh_parser.cli.OllamaEmbeddingProvider", side_effect=EmbeddingProviderError("provider down")):
                code, _, stderr = self._run("build", "--vault", str(vault), "--config", str(config), "--output", str(output))
            self.assertNotEqual(code, 0)
            self.assertIn("provider down", stderr)
            self.assertFalse((output / "substrate.sqlite3").exists()); self.assertFalse((output / "vectors.npy").exists())
