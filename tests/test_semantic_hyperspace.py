from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import numpy as np

from semantic_traversal.semantic_hyperspace import (
    BuildConfig,
    DeterministicEmbeddingProvider,
    build_semantic_hyperspace,
)


class SemanticHyperspaceBuildTests(unittest.TestCase):
    def test_build_config_rejects_legacy_chunking_fallbacks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config_path = Path(temporary) / "legacy.yaml"
            config_path.write_text(
                "chunking:\n  required_uuid_field: uuid\n  semantic_frontmatter_fields: [title]\n",
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                BuildConfig.from_yaml(config_path)

    def test_code_blocks_do_not_create_wikilink_relations(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            vault = root / "vault"
            output = root / "build"
            vault.mkdir()
            (vault / "note.md").write_text(
                "---\nuuid: 00000000-0000-4000-8000-000000000001\n---\n```markdown\n[[Missing#Region]]\n```\n\n    [[Also Missing#Region]]\n",
                encoding="utf-8",
            )
            build_semantic_hyperspace(
                vault_root=vault,
                output_root=output,
                config=BuildConfig(()),
                embedding_provider=DeterministicEmbeddingProvider(),
            )
            connection = sqlite3.connect(output / "substrate.sqlite3")
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM relations").fetchone()[0], 0)
            connection.close()

    def test_excluded_folders_are_not_parsed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            vault = root / "vault"
            output = root / "build"
            (vault / "VAULT DESIGN").mkdir(parents=True)
            (vault / "VAULT DESIGN" / "template.md").write_text("not a semantic object", encoding="utf-8")
            (vault / "included.md").write_text("---\nuuid: 00000000-0000-4000-8000-000000000001\n---\nIncluded.\n", encoding="utf-8")
            manifest = build_semantic_hyperspace(
                vault_root=vault,
                output_root=output,
                config=BuildConfig((), excluded_folders=("VAULT DESIGN",)),
                embedding_provider=DeterministicEmbeddingProvider(),
            )
            self.assertEqual(manifest["object_count"], 1)
            self.assertEqual(manifest["build_configuration"]["excluded_folders"], ["VAULT DESIGN"])

    def test_specimen_contracts_are_materialized(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            vault = root / "vault"
            output = root / "build"
            vault.mkdir()
            (vault / "target.md").write_text(
                "---\nuuid: 00000000-0000-4000-8000-000000000002\n---\n# Child\nTarget text.\n",
                encoding="utf-8",
            )
            (vault / "source.md").write_text(
                "---\nuuid: 00000000-0000-4000-8000-000000000001\nrelated: '[[target]]'\nblank_field: ''\n---\n# Outer\n## Inner\nFirst [[target#Child|visible]] link.\n\nSecond paragraph.\n",
                encoding="utf-8",
            )
            config = BuildConfig(("related", "blank_field", "missing_field"))
            manifest = build_semantic_hyperspace(
                vault_root=vault,
                output_root=output,
                config=config,
                embedding_provider=DeterministicEmbeddingProvider(),
            )
            self.assertEqual(manifest["status"], "published")
            connection = sqlite3.connect(output / "substrate.sqlite3")
            connection.row_factory = sqlite3.Row
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM objects").fetchone()[0], 2)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM regions").fetchone()[0], 3)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM units").fetchone()[0], 3)
            source_unit = connection.execute("SELECT * FROM units WHERE object_uuid = ? AND parsed_text LIKE '%visible%'", ("00000000-0000-4000-8000-000000000001",)).fetchone()
            self.assertIsNotNone(source_unit)
            self.assertEqual(json.loads(source_unit["region_path_json"]), ["Outer", "Inner"])
            self.assertIn("[[target#Child|visible]]", source_unit["raw_markdown"])
            self.assertIn("visible", source_unit["parsed_text"])
            states = {row["field_name"]: row["state"] for row in connection.execute("SELECT field_name, state FROM identifier_values WHERE unit_id = ?", (source_unit["unit_id"],))}
            self.assertEqual(states, {"related": "present_value", "blank_field": "present_blank", "missing_field": "absent"})
            relations = connection.execute("SELECT relation_name, source_kind, target_region_id FROM relations WHERE source_unit_id = ? ORDER BY relation_id", (source_unit["unit_id"],)).fetchall()
            self.assertEqual([(row[0], row[1]) for row in relations], [("related", "frontmatter"), ("linked_to", "body")])
            self.assertIsNotNone(relations[1][2])
            fts_columns = {row[1] for row in connection.execute("PRAGMA table_info(units_fts)")}
            self.assertIn("field_related", fts_columns)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM graph_edges WHERE relation_name = 'contains_region'").fetchone()[0], 3)
            connection.close()
            vectors = np.load(output / "vectors.npy")
            self.assertEqual(vectors.shape, (3, 1024))
            self.assertEqual(json.loads((output / "capability_catalog.json").read_text(encoding="utf-8"))["operations"]["lexical"], ["terms", "phrase"])

    def test_unresolved_link_does_not_publish(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            vault = root / "vault"
            output = root / "build"
            vault.mkdir()
            (vault / "source.md").write_text(
                "---\nuuid: 00000000-0000-4000-8000-000000000001\nrelated: '[[missing]]'\n---\nText.\n",
                encoding="utf-8",
            )
            with self.assertRaises(RuntimeError):
                build_semantic_hyperspace(
                    vault_root=vault,
                    output_root=output,
                    config=BuildConfig(("related",)),
                    embedding_provider=DeterministicEmbeddingProvider(),
                )
            manifest = json.loads((output / "repair_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["status"], "failed")
            self.assertFalse((output / "substrate.sqlite3").exists())

    def test_repair_manifest_collects_all_unresolved_links(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            vault = root / "vault"
            output = root / "build"
            vault.mkdir()
            (vault / "source.md").write_text(
                "---\nuuid: 00000000-0000-4000-8000-000000000001\n---\nFirst [[missing-one]].\nSecond [[missing-two]].\n",
                encoding="utf-8",
            )
            with self.assertRaises(RuntimeError):
                build_semantic_hyperspace(
                    vault_root=vault,
                    output_root=output,
                    config=BuildConfig(()),
                    embedding_provider=DeterministicEmbeddingProvider(),
                )
            manifest = json.loads((output / "repair_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["failure_stage"], "link_resolution")
            self.assertEqual(len(manifest["repair_manifest"]), 2)
            self.assertEqual({item["link"] for item in manifest["repair_manifest"]}, {"[[missing-one]]", "[[missing-two]]"})

    def test_note_name_resolution_is_case_insensitive(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            vault = root / "vault"
            output = root / "build"
            vault.mkdir()
            (vault / "Destination.md").write_text("---\nuuid: 00000000-0000-4000-8000-000000000002\n---\nTarget.\n", encoding="utf-8")
            (vault / "source.md").write_text("---\nuuid: 00000000-0000-4000-8000-000000000001\n---\n[[destination]]\n", encoding="utf-8")
            build_semantic_hyperspace(vault_root=vault, output_root=output, config=BuildConfig(()), embedding_provider=DeterministicEmbeddingProvider())
            connection = sqlite3.connect(output / "substrate.sqlite3")
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM relations").fetchone()[0], 1)
            connection.close()


if __name__ == "__main__":
    unittest.main()
