import hashlib
import json
import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from semantic_traversal.runtime.control_plane import conform_retrieval
from semantic_traversal.runtime.conversation import (
    RuntimeConversationError,
    append_message,
    create_conversation,
    initialize_runtime,
    migrate_runtime,
)


class RuntimeConformanceAuthorityTests(unittest.TestCase):
    def _catalog(self):
        return {
            "catalog_schema_version": "1",
            "semantic_dimensions": [
                {
                    "field_class": "intrinsic",
                    "field_name": "parsed_text",
                    "description": "parsed text",
                    "value": {"shapes": [{"shape": "scalar", "domains": ["string"]}]},
                    "access": [{"operator": "exact.equals", "target": "complete_value", "domains": ["string"]}],
                }
            ],
            "graph": {"node_kinds": [], "discovery": [], "relations": []},
            "vector": {
                "operator": "vector.semantic_similarity",
                "query": {"shape": "string", "requirement": "one string", "segmentation": False, "truncation": False, "deterministic_enrichment": False},
                "targets": [],
            },
            "operators": {"exact.equals": {"surface": "exact", "meaning": "typed exact equality"}},
        }

    def _prepare(self, directory):
        database = Path(directory) / "runtime.sqlite3"
        catalog_path = Path(directory) / "capability_catalog.json"
        raw_catalog = json.dumps(self._catalog(), separators=(",", ":")).encode("utf-8")
        catalog_path.write_bytes(raw_catalog)
        initialize_runtime(database)
        conversation = create_conversation(database)
        append_message(database, conversation.conversation_id, "user", "question")
        catalog_sha = "sha256:" + hashlib.sha256(raw_catalog).hexdigest()
        connection = sqlite3.connect(database)
        connection.execute(
            """
            INSERT INTO model_runs (
                run_id, conversation_id, trigger_message_id, run_kind, provider, model,
                prompt_version, status, started_at, output_json
            ) VALUES ('router', ?, 1, 'router', 'openai', 'model', 'prompt',
                      'succeeded', 'started', '{"route":"semantic_retrieval"}')
            """,
            (conversation.conversation_id,),
        )
        connection.execute(
            """
            INSERT INTO model_runs (
                run_id, conversation_id, trigger_message_id, run_kind, parent_run_id,
                capability_catalog_sha256, provider, model, prompt_version, status,
                started_at, output_json
            ) VALUES (
                'retrieval', ?, 1, 'retrieval_inference', 'router', ?, 'openai',
                'model', 'prompt', 'succeeded', 'started',
                '{"requests":[{"operator":"exact.equals","field_class":"intrinsic","field_name":"missing","target":"complete_value","operand":{"shape":"scalar","domain":"string","value":"x"}}]}'
            )
            """,
            (conversation.conversation_id, catalog_sha),
        )
        connection.commit()
        connection.close()
        return database, catalog_path

    def _make_v3(self, directory):
        database, catalog_path = self._prepare(directory)
        connection = sqlite3.connect(database)
        before = {
            table: tuple(connection.execute(f"SELECT * FROM {table} ORDER BY rowid"))
            for table in ("conversations", "messages", "model_runs")
        }
        connection.execute("DROP TABLE retrieval_conformance")
        connection.execute("PRAGMA user_version = 3")
        connection.commit()
        connection.close()
        return database, catalog_path, before

    def test_returned_violation_state_is_recursively_immutable(self):
        with TemporaryDirectory() as directory:
            database, catalog_path = self._prepare(directory)
            result = conform_retrieval(database, catalog_path, "retrieval")
            violation = result.requests[0].violations[0]
            self.assertEqual(type(violation).__name__, "mappingproxy")
            with self.assertRaises(TypeError):
                violation["code"] = "changed"
            with self.assertRaises(TypeError):
                violation["field_class"] = "changed"
    def test_valid_v3_to_v4_migration_preserves_all_factual_rows(self):
        with TemporaryDirectory() as directory:
            database, _, before = self._make_v3(directory)
            migrate_runtime(database)
            connection = sqlite3.connect(database)
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 4)
            for table, rows in before.items():
                self.assertEqual(tuple(connection.execute(f"SELECT * FROM {table} ORDER BY rowid")), rows)
            self.assertIsNotNone(connection.execute("SELECT name FROM sqlite_master WHERE name = 'retrieval_conformance'").fetchone())
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM retrieval_conformance").fetchone()[0], 0)
            connection.close()

    def test_v3_to_v4_candidate_validation_failure_rolls_back_exact_v3(self):
        with TemporaryDirectory() as directory:
            database, _, before = self._make_v3(directory)
            with patch(
                "semantic_traversal.runtime.conversation._validate_schema",
                side_effect=[None, RuntimeConversationError("injected candidate-v4 validation failure")],
            ), self.assertRaisesRegex(RuntimeConversationError, "injected candidate-v4"):
                migrate_runtime(database)
            connection = sqlite3.connect(database)
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 3)
            for table, rows in before.items():
                self.assertEqual(tuple(connection.execute(f"SELECT * FROM {table} ORDER BY rowid")), rows)
            self.assertIsNone(connection.execute("SELECT name FROM sqlite_master WHERE name = 'retrieval_conformance'").fetchone())
            connection.close()

    def _weaken_conformance(self, database, variant):
        initialize_runtime(database)
        connection = sqlite3.connect(database)
        connection.execute("ALTER TABLE retrieval_conformance RENAME TO retrieval_conformance_original")
        unique = "" if variant == "unique" else " UNIQUE"
        foreign_key = "" if variant == "foreign_key" else ", FOREIGN KEY (retrieval_run_id) REFERENCES model_runs(run_id)"
        contract = "" if variant == "contract_check" else " CHECK (contract_version = 'catalog-conformance-v1')"
        status = "" if variant == "status_check" else " CHECK (status IN ('valid', 'invalid'))"
        connection.execute(
            f"""
            CREATE TABLE retrieval_conformance (
                conformance_id TEXT PRIMARY KEY,
                retrieval_run_id TEXT NOT NULL{unique},
                retrieval_proposal_sha256 TEXT NOT NULL,
                capability_catalog_sha256 TEXT NOT NULL,
                contract_version TEXT NOT NULL{contract},
                status TEXT NOT NULL{status},
                checked_at TEXT NOT NULL,
                result_json TEXT NOT NULL
                {foreign_key}
            )
            """
        )
        connection.execute("DROP TABLE retrieval_conformance_original")
        connection.execute("PRAGMA user_version = 4")
        connection.commit()
        connection.close()

    def test_weakened_v4_conformance_authority_fails_closed(self):
        for variant in ("unique", "foreign_key", "contract_check", "status_check"):
            with self.subTest(variant=variant), TemporaryDirectory() as directory:
                database = Path(directory) / "runtime.sqlite3"
                self._weaken_conformance(database, variant)
                with self.assertRaises(RuntimeConversationError):
                    initialize_runtime(database)


if __name__ == "__main__":
    unittest.main()