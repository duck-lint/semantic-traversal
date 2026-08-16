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

from semantic_traversal.runtime.control_plane import conform_retrieval
from semantic_traversal.runtime.conversation import (
    RuntimeConversationError, append_message, create_conversation, initialize_runtime, migrate_runtime,
)
from semantic_traversal.runtime.retrieval_execution import (
    EXECUTION_CONTRACT_VERSION, RetrievalExecutionError, execute_retrieval,
)
from semantic_traversal.runtime.retrieval_package import load_retrieval_package


class RuntimeRetrievalExecutionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tests.test_retrieval_package_verification import RetrievalPackageVerificationTests
        cls.fixture = RetrievalPackageVerificationTests
        cls.fixture.setUpClass()
        cls.build = cls.fixture.build_a
        cls.other_build = cls.fixture.build_b
        cls.root = Path(cls.fixture.directory.name) / "execution-tests"
        cls.root.mkdir()

    @classmethod
    def tearDownClass(cls):
        cls.fixture.tearDownClass()

    def prepared(self, name, requests):
        root = self.root / name
        root.mkdir()
        database = root / "runtime.sqlite3"
        initialize_runtime(database)
        conversation = create_conversation(database)
        message = append_message(database, conversation.conversation_id, "user", "question")
        package = load_retrieval_package(self.build)
        output = json.dumps({"requests": requests}, ensure_ascii=False, separators=(",", ":"))
        connection = sqlite3.connect(database)
        connection.execute(
            "INSERT INTO model_runs (run_id, conversation_id, trigger_message_id, run_kind, provider, model, "
            "prompt_version, status, started_at, output_json) VALUES (?, ?, ?, 'router', 'p', 'm', 'p', 'succeeded', 't', ?)",
            ("router", conversation.conversation_id, message.message_id, '{"route":"semantic_retrieval"}'),
        )
        connection.execute(
            "INSERT INTO model_runs (run_id, conversation_id, trigger_message_id, run_kind, parent_run_id, "
            "capability_catalog_sha256, provider, model, prompt_version, status, started_at, output_json) "
            "VALUES (?, ?, ?, 'retrieval_inference', 'router', ?, 'p', 'm', 'p', 'succeeded', 't', ?)",
            ("retrieval", conversation.conversation_id, message.message_id, package.identity.capability_catalog_sha256, output),
        )
        connection.commit()
        connection.close()
        conformance = conform_retrieval(database, self.build / "capability_catalog.json", "retrieval")
        return database, package, conformance.conformance_id

    @staticmethod
    def exact(value="missing"):
        return {
            "operator": "exact.equals", "field_class": "intrinsic", "field_name": "parsed_text",
            "target": "complete_value",
            "operand": {"shape": "scalar", "domain": "string", "value": value},
        }

    def test_schema_v5_contains_only_the_execution_table_in_addition_to_v4(self):
        with TemporaryDirectory() as directory:
            database = Path(directory) / "runtime.sqlite3"
            initialize_runtime(database)
            connection = sqlite3.connect(database)
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 5)
            self.assertEqual(
                {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")},
                {"conversations", "messages", "model_runs", "retrieval_conformance", "retrieval_executions"},
            )
            self.assertEqual(
                tuple(row[1] for row in connection.execute("PRAGMA table_info(retrieval_executions)")),
                (
                    "execution_id", "conformance_id", "retrieval_run_id", "retrieval_proposal_sha256",
                    "capability_catalog_sha256", "retrieval_package_id", "retrieval_package_identity_version",
                    "substrate_sha256", "vectors_sha256", "package_verification_contract_version",
                    "execution_contract_version", "status", "started_at", "completed_at", "result_json",
                ),
            )
            connection.close()

    def test_v4_to_v5_migration_preserves_prior_runtime_rows(self):
        with TemporaryDirectory() as directory:
            database = Path(directory) / "runtime.sqlite3"
            initialize_runtime(database)
            conversation = create_conversation(database)
            message = append_message(database, conversation.conversation_id, "user", "preserve")
            connection = sqlite3.connect(database)
            before = {
                table: tuple(connection.execute(f"SELECT * FROM {table} ORDER BY rowid"))
                for table in ("conversations", "messages", "model_runs", "retrieval_conformance")
            }
            connection.execute("DROP TABLE retrieval_executions")
            connection.execute("PRAGMA user_version = 4")
            connection.commit()
            connection.close()
            migrate_runtime(database)
            connection = sqlite3.connect(database)
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 5)
            for table, rows in before.items():
                self.assertEqual(tuple(connection.execute(f"SELECT * FROM {table} ORDER BY rowid")), rows)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM retrieval_executions").fetchone()[0], 0)
            connection.close()

    def test_v4_to_v5_candidate_validation_failure_rolls_back_without_execution_table(self):
        with TemporaryDirectory() as directory:
            database = Path(directory) / "runtime.sqlite3"
            initialize_runtime(database)
            connection = sqlite3.connect(database)
            connection.execute("DROP TABLE retrieval_executions")
            connection.execute("PRAGMA user_version = 4")
            connection.commit()
            connection.close()
            with patch(
                "semantic_traversal.runtime.conversation._validate_schema",
                side_effect=[None, RuntimeConversationError("injected candidate-v5 validation failure")],
            ), self.assertRaisesRegex(RuntimeConversationError, "injected candidate-v5"):
                migrate_runtime(database)
            connection = sqlite3.connect(database)
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 4)
            self.assertIsNone(connection.execute("SELECT name FROM sqlite_master WHERE name='retrieval_executions'").fetchone())
            connection.close()

    def test_exact_zero_hit_and_empty_proposal_succeed_without_hydration(self):
        database, package, conformance_id = self.prepared("zero", [self.exact()])
        with patch("semantic_traversal.runtime.retrieval_execution._surface_result", wraps=__import__("semantic_traversal.runtime.retrieval_execution", fromlist=["_surface_result"])._surface_result):
            result = execute_retrieval(database, package, conformance_id)
        self.assertEqual(result.status, "succeeded")
        self.assertEqual(dict(result.requests[0].result), {"kind": "exact", "unit_ids": ()})
        empty_db, empty_package, empty_conformance = self.prepared("empty", [])
        empty = execute_retrieval(empty_db, empty_package, empty_conformance)
        self.assertEqual(empty.status, "succeeded")
        self.assertEqual(empty.requests, ())
        self.assertIsNone(empty.execution_failure)

    def test_order_duplicates_and_fail_fast_preserve_factual_partial_results(self):
        requests = [self.exact("first"), {"operator": "lexical.terms", "field_class": "intrinsic", "field_name": "parsed_text", "target": "complete_value", "operand": ["two words"]}, self.exact("later")]
        database, package, conformance_id = self.prepared("fail-fast", requests)
        with patch("semantic_traversal.runtime.retrieval_execution.lexical_lookup", side_effect=ValueError("tokenizer rejection")), patch("semantic_traversal.runtime.retrieval_execution.exact_lookup", wraps=__import__("semantic_traversal.runtime.retrieval_execution", fromlist=["exact_lookup"]).exact_lookup) as exact:
            result = execute_retrieval(database, package, conformance_id)
        self.assertEqual([item.status for item in result.requests], ["succeeded", "failed", "not_executed"])
        self.assertEqual(result.status, "failed")
        self.assertEqual(exact.call_count, 1)
        connection = sqlite3.connect(database)
        stored = connection.execute("SELECT status, result_json FROM retrieval_executions").fetchone()
        connection.close()
        self.assertEqual(stored[0], "failed")
        self.assertEqual([item["status"] for item in json.loads(stored[1])["requests"]], ["succeeded", "failed", "not_executed"])

    def test_duplicate_successful_requests_execute_twice_and_second_call_is_idempotent(self):
        database, package, conformance_id = self.prepared("duplicate", [self.exact(), self.exact()])
        module = __import__("semantic_traversal.runtime.retrieval_execution", fromlist=["exact_lookup"])
        with patch.object(module, "exact_lookup", wraps=module.exact_lookup) as exact:
            first = execute_retrieval(database, package, conformance_id)
            second = execute_retrieval(database, package, conformance_id)
        self.assertEqual(first, second)
        self.assertEqual(exact.call_count, 2)
        self.assertEqual(first.execution_contract_version, EXECUTION_CONTRACT_VERSION)

    def test_vector_provider_is_required_before_running_evidence(self):
        request = {"operator": "vector.semantic_similarity", "query": "query"}
        database, package, conformance_id = self.prepared("vector-precondition", [request])
        with self.assertRaises(RetrievalExecutionError):
            execute_retrieval(database, package, conformance_id)
        connection = sqlite3.connect(database)
        self.assertEqual(connection.execute("SELECT COUNT(*) FROM retrieval_executions").fetchone()[0], 0)
        connection.close()

    def test_missing_conformance_and_package_mismatch_fail_before_surface_calls(self):
        database, package, conformance_id = self.prepared("authority", [self.exact()])
        with patch("semantic_traversal.runtime.retrieval_execution.exact_lookup") as exact:
            with self.assertRaises(RetrievalExecutionError):
                execute_retrieval(database, package, "missing")
            exact.assert_not_called()
        other = load_retrieval_package(self.other_build)
        with patch("semantic_traversal.runtime.retrieval_execution.exact_lookup") as exact:
            with self.assertRaises(RetrievalExecutionError):
                execute_retrieval(database, other, conformance_id)
            exact.assert_not_called()
        connection = sqlite3.connect(database)
        self.assertEqual(connection.execute("SELECT COUNT(*) FROM retrieval_executions").fetchone()[0], 0)
        connection.close()

    def test_mutated_package_fails_before_execution_row(self):
        database, package, conformance_id = self.prepared("mutated-before", [self.exact()])
        original = package.substrate_path.read_bytes()
        package.substrate_path.write_bytes(original + b"changed")
        try:
            with patch("semantic_traversal.runtime.retrieval_execution.exact_lookup") as exact:
                with self.assertRaises(RetrievalExecutionError):
                    execute_retrieval(database, package, conformance_id)
                exact.assert_not_called()
            connection = sqlite3.connect(database)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM retrieval_executions").fetchone()[0], 0)
            connection.close()
        finally:
            package.substrate_path.write_bytes(original)

    def test_native_ordered_sequence_adapter_preserves_sequence_shape(self):
        request = {
            "operator": "exact.equals", "field_class": "region", "field_name": "region_path",
            "target": "complete_value",
            "operand": {"shape": "ordered_sequence", "member_domain": "string", "value": ["A", "B"]},
        }
        database, package, conformance_id = self.prepared("ordered", [request])
        module = __import__("semantic_traversal.runtime.retrieval_execution", fromlist=["exact_lookup"])
        with patch.object(module, "exact_lookup", return_value=()) as exact:
            result = execute_retrieval(database, package, conformance_id)
        self.assertEqual(result.status, "succeeded")
        self.assertIsInstance(exact.call_args.args[3], tuple)
        self.assertEqual(exact.call_args.args[3], ("A", "B"))

    def test_execution_does_not_change_projection_bytes(self):
        database, package, conformance_id = self.prepared("read-only", [self.exact()])
        before = hashlib.sha256(package.substrate_path.read_bytes()).hexdigest()
        execute_retrieval(database, package, conformance_id)
        self.assertEqual(hashlib.sha256(package.substrate_path.read_bytes()).hexdigest(), before)

    def test_lexical_graph_and_vector_adapters_return_surface_native_shapes(self):
        from tests.test_cli import CliProvider
        requests = [
            {"operator": "lexical.terms", "field_class": "intrinsic", "field_name": "parsed_text", "target": "complete_value", "operand": ["source"]},
            {"operator": "lexical.phrase", "field_class": "intrinsic", "field_name": "parsed_text", "target": "complete_value", "operand": "source body"},
            {"operator": "graph.discovery.terms", "node_kind": "semantic_object", "dimension_name": "address_text", "operand": ["Target"]},
            {"operator": "graph.discovery.phrase", "node_kind": "semantic_object", "dimension_name": "address_text", "operand": "Target"},
            {"operator": "graph.relation_occurrence_lookup", "relation_class": "body_wikilink", "relation_name": "linked_to"},
            {"operator": "vector.semantic_similarity", "query": "source"},
        ]
        database, package, conformance_id = self.prepared("all-surfaces", requests)
        result = execute_retrieval(database, package, conformance_id, vector_provider=CliProvider())
        self.assertEqual(result.status, "succeeded")
        self.assertEqual([item.result["kind"] for item in result.requests], ["lexical", "lexical", "graph_discovery", "graph_discovery", "graph_relation_occurrences", "vector"])


if __name__ == "__main__":
    unittest.main()
