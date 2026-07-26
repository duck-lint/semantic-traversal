from __future__ import annotations

import copy
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from semantic_traversal.config import RuntimeConfig, load_runtime_config
from semantic_traversal.embeddings import UnavailableEmbeddingBackend
from semantic_traversal.ingest import IngestSourceRoot, run_ingest
from semantic_traversal.resource_inventory import load_persisted_inventory, validate_inventory_snapshot
from semantic_traversal.runtime import _semantic_traversal


class PersistedInventoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.repo_root = Path(__file__).resolve().parents[1]
        cls.config = load_runtime_config(repo_root=cls.repo_root)

    def _write_source(self, root: Path, *, title: str = "Entry", relative: str = "Journal/entry.md") -> None:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "---\n"
            "uuid: 019bc983-8065-7611-b57c-b9ef76d7b848\n"
            "note_type: journal_entry\n"
            "tags: [one, two]\n"
            "journal_entry_date: 2025-09-01\n"
            "---\n\n"
            f"# {title}\n\nA bounded inventory fixture.\n",
            encoding="utf-8",
        )

    def _ingest(self, root: Path, data_root: Path):
        return run_ingest(
            repo_root=self.repo_root,
            data_root=data_root,
            source_roots=(IngestSourceRoot(label="fixture", path=root),),
            embedding_backend=UnavailableEmbeddingBackend(reason="test"),
        )

    def test_snapshot_is_single_current_row_and_hash_is_stable(self) -> None:
        with tempfile.TemporaryDirectory(prefix="inventory-fixture-") as root_text, tempfile.TemporaryDirectory(prefix="inventory-data-") as data_text:
            root, data_root = Path(root_text), Path(data_text)
            self._write_source(root)
            first = self._ingest(root, data_root)
            connection = sqlite3.connect(first.database_path)
            connection.row_factory = sqlite3.Row
            row = connection.execute("SELECT * FROM resource_inventory_snapshots").fetchone()
            payload = json.loads(row["payload_json"])
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM resource_inventory_snapshots").fetchone()[0], 1)
            self.assertEqual(row["source_ingest_run_id"], first.run_id)
            self.assertEqual(row["validation_status"], "valid")
            self.assertIn("exact_fts", payload["capabilities"])
            self.assertIn("vector", payload["capabilities"])
            self.assertIn("graph", payload["capabilities"])
            self.assertIn("temporal", payload["capabilities"])
            self.assertEqual(payload["path_topology"]["path_depth"], 2)
            self.assertNotIn("paragraph_text", json.dumps(payload))
            self.assertNotIn("vector_json", json.dumps(payload))
            first_hash = row["logical_inventory_hash"]
            connection.close()
            second = self._ingest(root, data_root)
            connection = sqlite3.connect(second.database_path)
            row = connection.execute("SELECT logical_inventory_hash, source_ingest_run_id FROM resource_inventory_snapshots").fetchone()
            self.assertEqual(row[0], first_hash)
            self.assertEqual(row[1], second.run_id)
            self.assertNotEqual(first.run_id, second.run_id)
            summary, diagnostics = load_persisted_inventory(connection=connection, config=self.config)
            self.assertEqual(diagnostics["source"], "persisted")
            self.assertEqual(diagnostics["status"], "valid")
            self.assertEqual(diagnostics["snapshot_load_count"], 1)
            self.assertEqual(diagnostics["full_inventory_rebuilds"], 0)
            manifest = json.loads(second.manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["resource_inventory"]["status"], "valid")
            self.assertEqual(manifest["resource_inventory"]["logical_inventory_hash"], first_hash)
            connection.close()

    def test_changed_metadata_and_path_change_logical_inventory(self) -> None:
        with tempfile.TemporaryDirectory(prefix="inventory-fixture-") as root_text, tempfile.TemporaryDirectory(prefix="inventory-data-") as data_text:
            root, data_root = Path(root_text), Path(data_text)
            self._write_source(root)
            first = self._ingest(root, data_root)
            connection = sqlite3.connect(first.database_path)
            first_hash = connection.execute("SELECT logical_inventory_hash FROM resource_inventory_snapshots").fetchone()[0]
            connection.close()
            source = root / "Journal" / "entry.md"
            source.write_text(source.read_text(encoding="utf-8").replace("tags: [one, two]", "tags: [one, three]"), encoding="utf-8")
            second = self._ingest(root, data_root)
            connection = sqlite3.connect(second.database_path)
            second_hash = connection.execute("SELECT logical_inventory_hash FROM resource_inventory_snapshots").fetchone()[0]
            connection.close()
            self.assertNotEqual(first_hash, second_hash)
            source.unlink()
            self._write_source(root, relative="Reading/entry.md")
            third = self._ingest(root, data_root)
            connection = sqlite3.connect(third.database_path)
            third_hash = connection.execute("SELECT logical_inventory_hash FROM resource_inventory_snapshots").fetchone()[0]
            connection.close()
            self.assertNotEqual(second_hash, third_hash)

    def test_deep_validation_detects_malformed_hash_and_count(self) -> None:
        with tempfile.TemporaryDirectory(prefix="inventory-fixture-") as root_text, tempfile.TemporaryDirectory(prefix="inventory-data-") as data_text:
            root, data_root = Path(root_text), Path(data_text)
            self._write_source(root)
            result = self._ingest(root, data_root)
            connection = sqlite3.connect(result.database_path)
            connection.row_factory = sqlite3.Row
            connection.execute("UPDATE resource_inventory_snapshots SET logical_inventory_hash = 'bad'")
            connection.commit()
            validation = validate_inventory_snapshot(connection=connection, config=self.config, deep=True)
            self.assertEqual(validation["status"], "invalid")
            self.assertIn("logical inventory hash mismatch", validation["errors"])
            connection.execute("UPDATE resource_inventory_snapshots SET logical_inventory_hash = (SELECT logical_inventory_hash FROM resource_inventory_snapshots)")
            connection.execute("UPDATE resource_inventory_snapshots SET payload_json = '{bad json'")
            connection.commit()
            validation = validate_inventory_snapshot(connection=connection, config=self.config)
            self.assertIn("snapshot payload JSON is malformed", validation["errors"])
            connection.close()

    def test_legacy_fallback_is_explicit_and_aliases_are_current_overlay(self) -> None:
        with tempfile.TemporaryDirectory(prefix="inventory-fixture-") as root_text, tempfile.TemporaryDirectory(prefix="inventory-data-") as data_text:
            root, data_root = Path(root_text), Path(data_text)
            self._write_source(root)
            result = self._ingest(root, data_root)
            connection = sqlite3.connect(result.database_path)
            connection.execute("DROP TABLE resource_inventory_snapshots")
            connection.commit()
            summary, diagnostics = load_persisted_inventory(connection=connection, config=self.config)
            self.assertEqual(diagnostics["source"], "recomputed_fallback")
            self.assertEqual(diagnostics["status"], "legacy_missing")
            self.assertEqual(diagnostics["full_inventory_rebuilds"], 1)
            connection.close()

            restored = self._ingest(root, data_root)

            alias_raw = copy.deepcopy(self.config.raw)
            alias_raw["retrieval"]["scope_aliases"]["current_fixture"] = {"note_type": ["journal_entry"]}
            alias_config = RuntimeConfig(repo_root=self.config.repo_root, config_path=self.config.config_path, raw=alias_raw)
            connection = sqlite3.connect(restored.database_path)
            summary, diagnostics = load_persisted_inventory(connection=connection, config=alias_config)
            self.assertEqual(diagnostics["source"], "persisted")
            self.assertEqual(summary["scope_aliases"]["current_fixture"]["note_type"], ("journal_entry",))
            connection.close()

    def test_same_loaded_snapshot_reaches_traversal_without_recomputation(self) -> None:
        with tempfile.TemporaryDirectory(prefix="inventory-fixture-") as root_text, tempfile.TemporaryDirectory(prefix="inventory-data-") as data_text:
            root, data_root = Path(root_text), Path(data_text)
            self._write_source(root)
            result = self._ingest(root, data_root)
            connection = sqlite3.connect(result.database_path)
            connection.row_factory = sqlite3.Row
            summary, diagnostics = load_persisted_inventory(connection=connection, config=self.config)
            compiler_packet = {
                "raw_user_input": "fixture",
                "query": "fixture",
                "concepts": ["fixture"],
                "scope_requests": [],
                "graph_seeds": [],
                "planner_retrieval_plan": {
                    "intent_type": "semantic_traversal",
                    "concepts": ["fixture"],
                    "semantic_queries": [],
                    "lexical_queries": ["fixture"],
                    "scope_requests": [],
                    "graph_seeds": [],
                    "retrieval_layers": [{"operator": "lexical_chunk_search", "limit": 5}],
                    "selection_policy": {},
                    "claim_policy": {},
                },
            }
            with patch("semantic_traversal.runtime.build_resource_inventory", side_effect=AssertionError("live inventory rebuild")):
                manifest, _ = _semantic_traversal(
                    connection=connection,
                    config=self.config,
                    semantic_compiler_packet=compiler_packet,
                    prior_thread_state={},
                    embedding_backend=UnavailableEmbeddingBackend(reason="test"),
                    resource_inventory_summary=summary,
                )
            self.assertEqual(diagnostics["source"], "persisted")
            self.assertEqual(manifest["inventory_diagnostics"]["snapshot_load_count"], 1)
            self.assertEqual(manifest["inventory_diagnostics"]["full_inventory_rebuilds"], 0)
            self.assertEqual(manifest["resource_inventory_summary"]["inventory_diagnostics"]["logical_inventory_hash"], diagnostics["logical_inventory_hash"])
            connection.close()
