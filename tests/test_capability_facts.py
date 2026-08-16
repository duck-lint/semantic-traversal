import contextlib
import hashlib
import io
import json
import shutil
import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import numpy as np

from ugh_parser import EmbeddingContract, EmbeddingProviderError
from ugh_parser.cli import main


class ObserverProvider:
    def __init__(self, model="qwen3-embedding:0.6b", base_url=None):
        self.contract = EmbeddingContract(
            model,
            "sha256-" + "b" * 64,
            1024,
            "float32",
            "l2",
            "cosine",
            10000,
        )

    def embed(self, text, *, truncate=False):
        if truncate:
            raise AssertionError("completed builds must embed with truncation disabled")
        if len(text) > self.contract.input_capacity:
            raise EmbeddingProviderError("capacity", capacity_exceeded=True)
        vector = np.zeros(1024, dtype=np.float32)
        vector[0] = 1.0
        vector[1] = len(text) + 1.0
        return vector


class CapabilityFactsTests(unittest.TestCase):
    def _fixture(self, root: Path) -> tuple[Path, Path]:
        vault = root / "vault"
        vault.mkdir()
        (vault / "Target.md").write_text(
            "---\nuuid: target-secret-uuid\ntitle: target-title-secret\ntags: [target-tag-secret]\n"
            "mixed_value: [sequence-member-secret]\n"
            "mixed_text_number: 11\n"
            "mixed_integer_sequence: [integer-sequence-member-secret]\n"
            "---\n# Region Secret\ntarget body secret\n",
            encoding="utf-8",
        )
        (vault / "Source.md").write_text(
            "---\n"
            "uuid: source-secret-uuid\n"
            "title: source-title-secret\n"
            "tags: [grouping-secret]\n"
            "aliases: [alias-secret]\n"
            "integer_value: 7\n"
            "bool_value: true\n"
            "date_value: 2026-08-15\n"
            "mixed_value: scalar-member-secret\n"
            "mixed_text_number: text-member-secret\n"
            "mixed_integer_sequence: 12\n"
            "relation: \"[[Target]]\"\n"
            "---\n"
            "source body secret [[Target]]\n",
            encoding="utf-8",
        )
        (vault / "Zero.md").write_text(
            "---\nuuid: zero-secret-uuid\ntags: [zero-tag-secret]\ndate_value: \"2026-08-15\"\n---\n",
            encoding="utf-8",
        )
        config = root / "config.yaml"
        config.write_text(
            "vault_name: observer\n"
            "uuid_field: uuid\n"
            "excluded_folders: []\n"
            "semantic_identifiers:\n"
            "  title: {description: test-authored declaration}\n"
            "  tags: {description: test-authored declaration}\n"
            "  aliases: {description: test-authored declaration}\n"
            "  integer_value: {description: test-authored declaration}\n"
            "  bool_value: {description: test-authored declaration}\n"
            "  date_value: {description: test-authored declaration}\n"
            "  mixed_value: {description: test-authored declaration}\n"
            "  mixed_text_number: {description: test-authored declaration}\n"
            "  mixed_integer_sequence: {description: test-authored declaration}\n"
            "  relation: {description: test-authored declaration}\n"
            "  admitted_only: {description: test-authored declaration}\n",
            encoding="utf-8",
        )
        return vault, config

    def _run(self, *argv):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = main(list(argv))
        return code, stdout.getvalue(), stderr.getvalue()

    def _build(self, root: Path) -> tuple[Path, Path, Path]:
        vault, config = self._fixture(root)
        output = root / "build"
        with patch("ugh_parser.cli.OllamaEmbeddingProvider", ObserverProvider):
            code, _, stderr = self._run(
                "build", "--vault", str(vault), "--config", str(config), "--output", str(output)
            )
        self.assertEqual(code, 0, stderr)
        return vault, config, output

    def _observe(self, output: Path) -> tuple[int, str, str]:
        with patch("ugh_parser.cli.OllamaEmbeddingProvider", side_effect=AssertionError("observer contacted Ollama")), patch(
            "ugh_parser.cli.parse_vault", side_effect=AssertionError("observer parsed the vault")
        ), patch("ugh_parser.cli.load_build_config", side_effect=AssertionError("observer read build config")):
            return self._run("inspect", "capability-facts", "--build", str(output), "--json")

    def test_observer_uses_completed_build_only_and_is_read_only(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            vault, config, output = self._build(root)
            before_files = sorted(path.name for path in output.iterdir())
            before_db = hashlib.sha256((output / "substrate.sqlite3").read_bytes()).hexdigest()
            before_vectors = hashlib.sha256((output / "vectors.npy").read_bytes()).hexdigest()
            shutil.rmtree(vault)
            config.unlink()

            code, stdout, stderr = self._observe(output)
            self.assertEqual(code, 0, stderr)
            self.assertEqual(stderr, "")
            facts = json.loads(stdout)
            self.assertEqual(facts["observer"], "capability-facts")
            self.assertEqual(sorted(path.name for path in output.iterdir()), before_files)
            self.assertEqual(hashlib.sha256((output / "substrate.sqlite3").read_bytes()).hexdigest(), before_db)
            self.assertEqual(hashlib.sha256((output / "vectors.npy").read_bytes()).hexdigest(), before_vectors)

            code2, stdout2, stderr2 = self._observe(output)
            self.assertEqual((code2, stdout2, stderr2), (0, stdout, ""))

            serialized = json.dumps(facts, ensure_ascii=False)
            for secret in (
                "target-secret-uuid",
                "source-secret-uuid",
                "zero-secret-uuid",
                "target-title-secret",
                "source-title-secret",
                "grouping-secret",
                "alias-secret",
                "zero-tag-secret",
                "Target",
                "Source",
                "Zero",
                "Region Secret",
                "source body secret",
            ):
                self.assertNotIn(secret, serialized)
            self.assertNotIn("admitted_only", serialized)
            for forbidden in ("count", "cardinality", "unit_id", "graph_node_id", "target_identity"):
                self.assertNotIn(forbidden, serialized.lower())

    def test_facts_preserve_shapes_surfaces_and_fixed_graph_grammar(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            _, _, output = self._build(root)
            code, stdout, stderr = self._observe(output)
            self.assertEqual(code, 0, stderr)
            facts = json.loads(stdout)
            fields = {(item["field_class"], item["field_name"]): item for item in facts["field_capabilities"]}
            self.assertEqual(fields[("semantic_identifier", "integer_value")]["canonical_shapes"], ["integer"])
            self.assertEqual(fields[("semantic_identifier", "bool_value")]["canonical_shapes"], ["boolean"])
            self.assertEqual(fields[("semantic_identifier", "date_value")]["canonical_shapes"], ["date"])
            self.assertEqual(fields[("semantic_identifier", "aliases")]["canonical_shapes"], ["sequence"])
            self.assertEqual(fields[("semantic_identifier", "aliases")]["sequence_member_domains"], ["string"])
            self.assertEqual(fields[("semantic_identifier", "aliases")]["surfaces"]["exact"]["sequence_behavior"], "member_equality")
            self.assertEqual(fields[("semantic_identifier", "aliases")]["surfaces"]["exact"]["operand_domains"], ["string"])
            self.assertEqual(fields[("semantic_identifier", "mixed_value")]["canonical_shapes"], ["string", "sequence"])
            self.assertEqual(fields[("semantic_identifier", "mixed_value")]["scalar_domains"], ["string"])
            self.assertEqual(fields[("semantic_identifier", "mixed_value")]["sequence_member_domains"], ["string"])
            self.assertEqual(fields[("semantic_identifier", "mixed_value")]["surfaces"]["exact"]["operand_domains"], ["string"])
            self.assertEqual(fields[("semantic_identifier", "mixed_value")]["surfaces"]["exact"]["sequence_behavior"], "member_equality")
            self.assertEqual(fields[("semantic_identifier", "mixed_value")]["surfaces"]["lexical"]["operand_domains"], ["string"])
            self.assertEqual(fields[("semantic_identifier", "mixed_text_number")]["canonical_shapes"], ["integer", "string"])
            self.assertEqual(fields[("semantic_identifier", "mixed_text_number")]["scalar_domains"], ["integer", "string"])
            self.assertEqual(fields[("semantic_identifier", "mixed_text_number")]["surfaces"]["exact"]["operand_domains"], ["integer", "string"])
            self.assertEqual(fields[("semantic_identifier", "mixed_text_number")]["surfaces"]["lexical"]["operand_domains"], ["string"])
            self.assertEqual(fields[("semantic_identifier", "mixed_integer_sequence")]["canonical_shapes"], ["integer", "sequence"])
            self.assertEqual(fields[("semantic_identifier", "mixed_integer_sequence")]["scalar_domains"], ["integer"])
            self.assertEqual(fields[("semantic_identifier", "mixed_integer_sequence")]["sequence_member_domains"], ["string"])
            self.assertEqual(fields[("semantic_identifier", "mixed_integer_sequence")]["surfaces"]["exact"]["operand_domains"], ["integer", "string"])
            self.assertEqual(fields[("intrinsic", "parsed_text")]["surfaces"]["lexical"]["operators"], ["terms", "phrase"])
            self.assertIn(("semantic_path", "path_component"), fields)
            self.assertEqual(fields[("semantic_path", "path_component")]["surfaces"]["exact"]["operators"], ["equals"])
            self.assertNotIn(("semantic_identifier", "admitted_only"), fields)

            discovery = {(item["node_kind"], item["dimension_name"]) for item in facts["graph_discovery_capabilities"]}
            self.assertIn(("semantic_object", "tag"), discovery)
            relations = {(item["relation_class"], item["relation_name"]): item for item in facts["graph_relation_capabilities"]}
            self.assertNotIn(("semantic_identifier", "tags"), relations)
            self.assertEqual(relations[("semantic_identifier", "relation")]["source_kinds"], ["semantic_object"])
            self.assertEqual(relations[("semantic_identifier", "relation")]["target_kinds"], ["semantic_object", "semantic_region"])
            self.assertEqual(relations[("structural", "contains_scope")]["source_kinds"], ["scope"])
            self.assertEqual(facts["graph_node_classes"], ["scope", "semantic_object", "semantic_region", "semantic_unit"])
            connection = sqlite3.connect(output / "substrate.sqlite3")
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM graph_edges WHERE relation_class = 'structural' AND relation_name = 'contains_scope'").fetchone()[0], 0)
            connection.close()
            self.assertIn(("structural", "contains_scope"), relations)

            vector = facts["vector_capability"]
            self.assertEqual(vector["operations"], ["semantic_similarity"])
            self.assertEqual(
                {item["target_kind"] for item in vector["represented_target_kinds"]},
                {"semantic_object", "semantic_unit"},
            )
            self.assertFalse(vector["query_operand"]["segmentation"])
            self.assertFalse(vector["query_operand"]["truncation"])
            self.assertNotIn("top_k", json.dumps(facts))
            self.assertNotIn("threshold", json.dumps(facts))
            self.assertEqual(
                facts["surface_operation_grammar"],
                {
                    "exact": ["equals"],
                    "lexical": ["terms", "phrase"],
                    "vector": ["semantic_similarity"],
                    "graph": ["node_discovery", "relation_occurrence_lookup", "inbound_traversal", "outbound_traversal"],
                },
            )

    def test_observer_fails_closed_for_unsupported_persisted_dimensions(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            _, _, output = self._build(root)
            database = output / "substrate.sqlite3"
            connection = sqlite3.connect(database)
            connection.execute(
                "INSERT INTO exact_index_entries (field_class, field_name, value_type, normalized_value, unit_id) VALUES (?, ?, ?, ?, ?)",
                ("unsupported", "dimension", "text", "x", 1),
            )
            connection.commit()
            connection.close()
            code, _, stderr = self._observe(output)
            self.assertNotEqual(code, 0)
            self.assertIn("unsupported exact field dimension", stderr)

            connection = sqlite3.connect(database)
            connection.execute("DELETE FROM exact_index_entries WHERE field_class = 'unsupported'")
            connection.execute(
                "INSERT INTO lexical_dimension_registry (field_class, field_name, table_name) VALUES (?, ?, ?)",
                ("unsupported", "dimension", "fake_lexical_table"),
            )
            connection.commit()
            connection.close()
            code, _, stderr = self._observe(output)
            self.assertNotEqual(code, 0)
            self.assertIn("unsupported lexical field dimension", stderr)

            connection = sqlite3.connect(database)
            connection.execute("DELETE FROM lexical_dimension_registry WHERE field_class = 'unsupported'")
            connection.execute("INSERT INTO graph_relation_types VALUES (?, ?)", ("unsupported", "relation"))
            connection.commit()
            connection.close()
            code, _, stderr = self._observe(output)
            self.assertNotEqual(code, 0)
            self.assertIn("unsupported graph relation type", stderr)

            connection = sqlite3.connect(database)
            connection.execute("DELETE FROM graph_relation_types WHERE relation_class = 'unsupported'")
            connection.execute(
                "INSERT INTO graph_discovery_registry (node_kind, dimension_name, table_name) VALUES (?, ?, ?)",
                ("unsupported", "dimension", "fake_discovery_table"),
            )
            connection.commit()
            connection.close()
            code, _, stderr = self._observe(output)
            self.assertNotEqual(code, 0)
            self.assertIn("unsupported graph discovery dimension", stderr)

            connection = sqlite3.connect(database)
            connection.execute("DELETE FROM graph_discovery_registry WHERE node_kind = 'unsupported'")
            connection.execute("PRAGMA ignore_check_constraints = ON")
            connection.execute("UPDATE vector_segments SET target_kind = 'unsupported' WHERE matrix_row = 0")
            connection.commit()
            connection.close()
            code, _, stderr = self._observe(output)
            self.assertNotEqual(code, 0)
            self.assertIn("unknown vector target kind", stderr)


if __name__ == "__main__":
    unittest.main()
