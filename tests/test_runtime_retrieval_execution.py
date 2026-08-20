import contextlib
import datetime as dt
import hashlib
import io
import json
import shutil
import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

from semantic_traversal.runtime.retrieval.control_plane import conform_retrieval
from semantic_traversal.runtime.conversation import (
    RuntimeConversationError, _create_retrieval_executions_table, append_message, create_conversation,
    initialize_runtime, migrate_runtime,
)
from semantic_traversal.runtime.retrieval.execution import (
    EXECUTION_CONTRACT_VERSION, RetrievalExecutionError, execute_retrieval,
)
from semantic_traversal.runtime.retrieval.package import RetrievalPackageError, load_retrieval_package
from semantic_traversal.projection.vector import EmbeddingProviderError


class RuntimeRetrievalExecutionTests(unittest.TestCase):
    def _downgrade_model_runs_to_v3(self, connection):
        connection.execute("DROP TABLE retrieval_executions")
        connection.execute("ALTER TABLE retrieval_conformance RENAME TO retrieval_conformance_original")
        connection.execute("ALTER TABLE model_runs RENAME TO model_runs_v6")
        connection.execute(
            """
            CREATE TABLE model_runs (
                run_id TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL,
                trigger_message_id INTEGER NOT NULL,
                run_kind TEXT NOT NULL CHECK (run_kind IN ('router', 'retrieval_inference')),
                parent_run_id TEXT,
                capability_catalog_sha256 TEXT,
                provider TEXT NOT NULL,
                model TEXT NOT NULL,
                prompt_version TEXT NOT NULL,
                status TEXT NOT NULL CHECK (status IN ('running', 'succeeded', 'failed')),
                started_at TEXT NOT NULL,
                completed_at TEXT,
                provider_response_id TEXT,
                output_json TEXT,
                error_type TEXT,
                error_message TEXT,
                input_tokens INTEGER,
                cached_input_tokens INTEGER,
                output_tokens INTEGER,
                reasoning_tokens INTEGER,
                total_tokens INTEGER,
                CHECK (
                    (run_kind = 'router' AND parent_run_id IS NULL AND capability_catalog_sha256 IS NULL)
                    OR
                    (run_kind = 'retrieval_inference' AND parent_run_id IS NOT NULL AND capability_catalog_sha256 IS NOT NULL)
                ),
                FOREIGN KEY (conversation_id) REFERENCES conversations(conversation_id),
                FOREIGN KEY (trigger_message_id) REFERENCES messages(message_id),
                FOREIGN KEY (parent_run_id) REFERENCES model_runs(run_id)
            )
            """
        )
        columns = (
            "run_id, conversation_id, trigger_message_id, run_kind, parent_run_id, "
            "capability_catalog_sha256, provider, model, prompt_version, status, started_at, "
            "completed_at, provider_response_id, output_json, error_type, error_message, "
            "input_tokens, cached_input_tokens, output_tokens, reasoning_tokens, total_tokens"
        )
        connection.execute(f"INSERT INTO model_runs ({columns}) SELECT {columns} FROM model_runs_v6")
        connection.execute("DROP TABLE model_runs_v6")
        connection.execute(
            """
            CREATE TABLE retrieval_conformance (
                conformance_id TEXT PRIMARY KEY,
                retrieval_run_id TEXT NOT NULL UNIQUE,
                retrieval_proposal_sha256 TEXT NOT NULL,
                capability_catalog_sha256 TEXT NOT NULL,
                contract_version TEXT NOT NULL CHECK (contract_version = 'catalog-conformance-v1'),
                status TEXT NOT NULL CHECK (status IN ('valid', 'invalid')),
                checked_at TEXT NOT NULL,
                result_json TEXT NOT NULL,
                FOREIGN KEY (retrieval_run_id) REFERENCES model_runs(run_id)
            )
            """
        )
        connection.execute(
            "INSERT INTO retrieval_conformance SELECT * FROM retrieval_conformance_original"
        )
        connection.execute("DROP TABLE retrieval_conformance_original")

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

    @staticmethod
    def rewrite_terminal_execution(database, statuses, failures, execution_failure):
        connection = sqlite3.connect(database)
        payload = json.loads(connection.execute("SELECT result_json FROM retrieval_executions").fetchone()[0])
        for item, status, failure in zip(payload["requests"], statuses, failures):
            item["status"] = status
            item["result"] = item["result"] if status == "succeeded" else None
            item["failure"] = None if status == "succeeded" else failure
        payload["execution_failure"] = execution_failure
        connection.execute(
            "UPDATE retrieval_executions SET status = 'failed', completed_at = 'done', result_json = ?",
            (json.dumps(payload, separators=(",", ":")),),
        )
        connection.commit()
        connection.close()

    def test_schema_v5_contains_only_the_execution_table_in_addition_to_v4(self):
        with TemporaryDirectory() as directory:
            database = Path(directory) / "runtime.sqlite3"
            initialize_runtime(database)
            connection = sqlite3.connect(database)
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 7)
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
            self._downgrade_model_runs_to_v3(connection)
            before = {
                table: tuple(connection.execute(f"SELECT * FROM {table} ORDER BY rowid"))
                for table in ("conversations", "messages", "model_runs", "retrieval_conformance")
            }
            connection.execute("PRAGMA user_version = 4")
            connection.commit()
            connection.close()
            migrate_runtime(database)
            connection = sqlite3.connect(database)
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 7)
            for table, rows in before.items():
                if table == "model_runs":
                    columns = "run_id, conversation_id, trigger_message_id, run_kind, parent_run_id, capability_catalog_sha256, provider, model, prompt_version, status, started_at, completed_at, provider_response_id, output_json, error_type, error_message, input_tokens, cached_input_tokens, output_tokens, reasoning_tokens, total_tokens"
                    actual = tuple(connection.execute(f"SELECT {columns} FROM {table} ORDER BY rowid"))
                else:
                    actual = tuple(connection.execute(f"SELECT * FROM {table} ORDER BY rowid"))
                self.assertEqual(actual, rows)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM retrieval_executions").fetchone()[0], 0)
            connection.close()

    def test_v5_to_v6_migration_adds_synthesis_columns_without_losing_rows(self):
        with TemporaryDirectory() as directory:
            database = Path(directory) / "runtime.sqlite3"
            initialize_runtime(database)
            connection = sqlite3.connect(database)
            self._downgrade_model_runs_to_v3(connection)
            _create_retrieval_executions_table(connection)
            before = tuple(connection.execute(
                "SELECT run_id, conversation_id, trigger_message_id, run_kind, parent_run_id, "
                "capability_catalog_sha256, provider, model, prompt_version, status, started_at, "
                "completed_at, provider_response_id, output_json, error_type, error_message, "
                "input_tokens, cached_input_tokens, output_tokens, reasoning_tokens, total_tokens "
                "FROM model_runs ORDER BY rowid"
            ))
            connection.execute("PRAGMA user_version = 5")
            connection.commit()
            connection.close()

            migrate_runtime(database)

            connection = sqlite3.connect(database)
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 7)
            actual = tuple(connection.execute(
                "SELECT run_id, conversation_id, trigger_message_id, run_kind, parent_run_id, "
                "capability_catalog_sha256, provider, model, prompt_version, status, started_at, "
                "completed_at, provider_response_id, output_json, error_type, error_message, "
                "input_tokens, cached_input_tokens, output_tokens, reasoning_tokens, total_tokens "
                "FROM model_runs ORDER BY rowid"
            ))
            self.assertEqual(actual, before)
            columns = tuple(row[1] for row in connection.execute("PRAGMA table_info(model_runs)"))
            for column in ("input_json", "input_sha256", "output_text", "produced_message_id"):
                self.assertIn(column, columns)
            connection.close()

    def test_v4_to_v5_candidate_validation_failure_rolls_back_without_execution_table(self):
        with TemporaryDirectory() as directory:
            database = Path(directory) / "runtime.sqlite3"
            initialize_runtime(database)
            connection = sqlite3.connect(database)
            self._downgrade_model_runs_to_v3(connection)
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
        with patch("semantic_traversal.runtime.retrieval.execution._surface_result", wraps=__import__("semantic_traversal.runtime.retrieval.execution", fromlist=["_surface_result"])._surface_result):
            result = execute_retrieval(database, package, conformance_id)
        self.assertEqual(result.status, "succeeded")
        self.assertEqual(dict(result.requests[0].result), {"kind": "exact", "unit_ids": ()})
        empty_db, empty_package, empty_conformance = self.prepared("empty", [])
        empty = execute_retrieval(empty_db, empty_package, empty_conformance)
        self.assertEqual(empty.status, "succeeded")
        self.assertEqual(empty.requests, ())
        self.assertIsNone(empty.execution_failure)

    def test_order_duplicates_and_fail_fast_preserve_factual_partial_results(self):
        requests = [self.exact("first"), {"operator": "lexical.terms", "field_class": "intrinsic", "field_name": "parsed_text", "target": "complete_value", "operand": ["two"]}, self.exact("later")]
        database, package, conformance_id = self.prepared("fail-fast", requests)
        with patch("semantic_traversal.runtime.retrieval.execution.lexical_lookup", side_effect=ValueError("tokenizer rejection")), patch("semantic_traversal.runtime.retrieval.execution.exact_lookup", wraps=__import__("semantic_traversal.runtime.retrieval.execution", fromlist=["exact_lookup"]).exact_lookup) as exact:
            result = execute_retrieval(database, package, conformance_id)
        self.assertEqual([item.status for item in result.requests], ["succeeded", "failed", "not_executed"])
        self.assertEqual(result.status, "failed")
        self.assertEqual(exact.call_count, 1)
        connection = sqlite3.connect(database)
        stored = connection.execute("SELECT status, result_json FROM retrieval_executions").fetchone()
        connection.close()
        self.assertEqual(stored[0], "failed")
        self.assertEqual([item["status"] for item in json.loads(stored[1])["requests"]], ["succeeded", "failed", "not_executed"])
        persisted = json.loads(stored[1])["requests"]
        self.assertEqual(persisted[1]["failure"]["kind"], "surface_error")
        self.assertEqual(persisted[2]["failure"], {"kind": "prior_request_failed", "failed_ordinal": 1})

    def test_package_identity_change_between_requests_does_not_fabricate_request_failure(self):
        database, package, conformance_id = self.prepared("package-change-between", [self.exact("first"), self.exact("second")])
        module = __import__("semantic_traversal.runtime.retrieval.execution", fromlist=["exact_lookup"])
        with patch.object(module, "exact_lookup", return_value=()) as exact, patch.object(
            module,
            "require_current_package_identity",
            side_effect=[None, None, RetrievalPackageError("package changed between requests")],
        ):
            result = execute_retrieval(database, package, conformance_id)
        self.assertEqual(result.status, "failed")
        self.assertEqual([item.status for item in result.requests], ["succeeded", "not_executed"])
        self.assertEqual(result.requests[0].result, {"kind": "exact", "unit_ids": ()})
        self.assertEqual(result.execution_failure["kind"], "package_identity_changed")
        self.assertEqual(result.requests[1].failure, {"kind": "package_identity_changed"})
        self.assertNotIn("failed_ordinal", result.requests[1].failure)
        self.assertEqual(exact.call_count, 1)
        connection = sqlite3.connect(database)
        status, result_json = connection.execute(
            "SELECT status, result_json FROM retrieval_executions"
        ).fetchone()
        connection.close()
        self.assertEqual(status, "failed")
        persisted = json.loads(result_json)
        self.assertEqual(persisted["requests"][0]["status"], "succeeded")
        self.assertEqual(persisted["requests"][1]["failure"], {"kind": "package_identity_changed"})

    def test_final_package_identity_change_cannot_commit_success(self):
        database, package, conformance_id = self.prepared("package-change-final", [self.exact("only")])
        module = __import__("semantic_traversal.runtime.retrieval.execution", fromlist=["exact_lookup"])
        with patch.object(module, "exact_lookup", return_value=()), patch.object(
            module,
            "require_current_package_identity",
            side_effect=[None, None, RetrievalPackageError("package changed before terminal commit")],
        ):
            result = execute_retrieval(database, package, conformance_id)
        self.assertEqual(result.status, "failed")
        self.assertEqual([item.status for item in result.requests], ["succeeded"])
        self.assertEqual(result.execution_failure["kind"], "package_identity_changed")
        connection = sqlite3.connect(database)
        status = connection.execute("SELECT status FROM retrieval_executions").fetchone()[0]
        connection.close()
        self.assertEqual(status, "failed")

    def test_persisted_prior_request_failure_requires_an_actual_failed_request(self):
        database, package, conformance_id = self.prepared("malformed-prior-failure", [self.exact("first"), self.exact("second")])
        execute_retrieval(database, package, conformance_id)
        connection = sqlite3.connect(database)
        payload = json.loads(connection.execute("SELECT result_json FROM retrieval_executions").fetchone()[0])
        payload["requests"][1] = {
            "ordinal": 1,
            "request": payload["requests"][1]["request"],
            "status": "not_executed",
            "result": None,
            "failure": {"kind": "prior_request_failed", "failed_ordinal": 1},
        }
        connection.execute(
            "UPDATE retrieval_executions SET status = 'failed', completed_at = 'done', result_json = ?",
            (json.dumps(payload, separators=(",", ":")),),
        )
        connection.commit()
        connection.close()
        with self.assertRaises(RetrievalExecutionError):
            execute_retrieval(database, package, conformance_id)

    def test_persisted_package_failure_rejects_succeeded_after_not_executed(self):
        database, package, conformance_id = self.prepared("malformed-package-order", [self.exact("first"), self.exact("second")])
        execute_retrieval(database, package, conformance_id)
        self.rewrite_terminal_execution(
            database,
            ["not_executed", "succeeded"],
            [{"kind": "package_identity_changed"}, None],
            {"kind": "package_identity_changed"},
        )
        module = __import__("semantic_traversal.runtime.retrieval.execution", fromlist=["exact_lookup"])
        with patch.object(module, "exact_lookup") as exact:
            with self.assertRaises(RetrievalExecutionError):
                execute_retrieval(database, package, conformance_id)
        exact.assert_not_called()

    def test_persisted_package_failure_rejects_interleaved_request_outcomes(self):
        requests = [self.exact("first"), self.exact("second"), self.exact("third")]
        database, package, conformance_id = self.prepared("malformed-package-interleave", requests)
        execute_retrieval(database, package, conformance_id)
        self.rewrite_terminal_execution(
            database,
            ["succeeded", "not_executed", "succeeded"],
            [None, {"kind": "package_identity_changed"}, None],
            {"kind": "package_identity_changed"},
        )
        with self.assertRaises(RetrievalExecutionError):
            execute_retrieval(database, package, conformance_id)

    def test_persisted_surface_failure_must_name_request_operator(self):
        database, package, conformance_id = self.prepared("malformed-surface-label", [self.exact("only")])
        execute_retrieval(database, package, conformance_id)
        self.rewrite_terminal_execution(
            database,
            ["failed"],
            [{
                "kind": "surface_error",
                "surface": "vector.semantic_similarity",
                "exception_type": "ValueError",
                "message": "wrong surface",
            }],
            None,
        )
        module = __import__("semantic_traversal.runtime.retrieval.execution", fromlist=["exact_lookup"])
        with patch.object(module, "exact_lookup") as exact:
            with self.assertRaises(RetrievalExecutionError):
                execute_retrieval(database, package, conformance_id)
        exact.assert_not_called()

    def test_unexpected_programming_error_leaves_running_evidence(self):
        database, package, conformance_id = self.prepared("unexpected-error", [self.exact("first"), self.exact("second")])
        module = __import__("semantic_traversal.runtime.retrieval.execution", fromlist=["exact_lookup"])
        with patch.object(module, "exact_lookup", side_effect=[(), RuntimeError("unexpected bug")]):
            with self.assertRaisesRegex(RuntimeError, "unexpected bug"):
                execute_retrieval(database, package, conformance_id)
        connection = sqlite3.connect(database)
        status, result_json = connection.execute(
            "SELECT status, result_json FROM retrieval_executions"
        ).fetchone()
        connection.close()
        self.assertEqual(status, "running")
        payload = json.loads(result_json)
        self.assertEqual([item["status"] for item in payload["requests"]], ["succeeded"])

    def test_duplicate_successful_requests_execute_twice_and_second_call_is_idempotent(self):
        database, package, conformance_id = self.prepared("duplicate", [self.exact(), self.exact()])
        module = __import__("semantic_traversal.runtime.retrieval.execution", fromlist=["exact_lookup"])
        with patch.object(module, "exact_lookup", wraps=module.exact_lookup) as exact:
            first = execute_retrieval(database, package, conformance_id)
            second = execute_retrieval(database, package, conformance_id)
        self.assertEqual(first, second)
        self.assertEqual(exact.call_count, 2)
        self.assertEqual(first.execution_contract_version, EXECUTION_CONTRACT_VERSION)

    def test_vector_provider_is_required_before_running_evidence(self):
        request = {"operator": "vector.semantic_similarity", "query": "query"}
        database, package, conformance_id = self.prepared("vector-precondition", [request])
        with patch("semantic_traversal.runtime.retrieval.execution.vector_lookup") as vector_lookup:
            with self.assertRaises(RetrievalExecutionError):
                execute_retrieval(database, package, conformance_id)
        vector_lookup.assert_not_called()
        connection = sqlite3.connect(database)
        self.assertEqual(connection.execute("SELECT COUNT(*) FROM retrieval_executions").fetchone()[0], 0)
        connection.close()

    def test_new_nonvector_execution_does_not_invoke_factory(self):
        database, package, conformance_id = self.prepared("lazy-nonvector", [self.exact("value")])
        provider_factory = MagicMock(side_effect=RuntimeError("non-vector factory must not be called"))
        result = execute_retrieval(database, package, conformance_id, vector_provider_factory=provider_factory)
        self.assertEqual(result.status, "succeeded")
        provider_factory.assert_not_called()

    def test_new_vector_execution_invokes_factory_once_and_uses_returned_provider(self):
        request = {"operator": "vector.semantic_similarity", "query": "query"}
        database, package, conformance_id = self.prepared("lazy-vector-new", [request])
        provider = object()
        provider_factory = MagicMock(return_value=provider)
        module = __import__("semantic_traversal.runtime.retrieval.execution", fromlist=["vector_lookup"])
        with patch.object(module, "vector_lookup", return_value=()) as vector_lookup:
            result = execute_retrieval(
                database, package, conformance_id, vector_provider_factory=provider_factory
            )
        self.assertEqual(result.status, "succeeded")
        provider_factory.assert_called_once_with()
        self.assertIs(vector_lookup.call_args.args[3], provider)

    def test_terminal_succeeded_vector_execution_replays_without_factory(self):
        request = {"operator": "vector.semantic_similarity", "query": "query"}
        database, package, conformance_id = self.prepared("lazy-vector-succeeded-replay", [request])
        module = __import__("semantic_traversal.runtime.retrieval.execution", fromlist=["vector_lookup"])
        with patch.object(module, "vector_lookup", return_value=()) as vector_lookup:
            first = execute_retrieval(database, package, conformance_id, vector_provider=object())
            provider_factory = MagicMock(side_effect=RuntimeError("replay factory must not be called"))
            second = execute_retrieval(
                database, package, conformance_id, vector_provider_factory=provider_factory
            )
        self.assertEqual(first, second)
        provider_factory.assert_not_called()
        self.assertEqual(vector_lookup.call_count, 1)

    def test_terminal_failed_vector_execution_replays_without_factory(self):
        request = {"operator": "vector.semantic_similarity", "query": "query"}
        database, package, conformance_id = self.prepared("lazy-vector-failed-replay", [request])
        module = __import__("semantic_traversal.runtime.retrieval.execution", fromlist=["vector_lookup"])
        failure = EmbeddingProviderError("embedding failed")
        with patch.object(module, "vector_lookup", side_effect=failure) as vector_lookup:
            first = execute_retrieval(database, package, conformance_id, vector_provider=object())
            provider_factory = MagicMock(side_effect=RuntimeError("replay factory must not be called"))
            second = execute_retrieval(
                database, package, conformance_id, vector_provider_factory=provider_factory
            )
        self.assertEqual(first.status, "failed")
        self.assertEqual(first, second)
        provider_factory.assert_not_called()
        self.assertEqual(vector_lookup.call_count, 1)

    def test_running_vector_execution_preserves_running_error_without_factory(self):
        request = {"operator": "vector.semantic_similarity", "query": "query"}
        database, package, conformance_id = self.prepared("lazy-vector-running", [request])
        module = __import__("semantic_traversal.runtime.retrieval.execution", fromlist=["_execute_surface"])
        with patch.object(module, "_execute_surface", side_effect=RuntimeError("simulated interruption")):
            with self.assertRaisesRegex(RuntimeError, "simulated interruption"):
                execute_retrieval(database, package, conformance_id, vector_provider=object())
        provider_factory = MagicMock(side_effect=RuntimeError("running replay factory must not be called"))
        with self.assertRaisesRegex(RetrievalExecutionError, "still running"):
            execute_retrieval(database, package, conformance_id, vector_provider_factory=provider_factory)
        provider_factory.assert_not_called()

    def test_new_vector_factory_failure_preserves_cause_before_surface_execution(self):
        request = {"operator": "vector.semantic_similarity", "query": "query"}
        database, package, conformance_id = self.prepared("lazy-vector-factory-failure", [request])
        failure = RuntimeError("factory construction failed")
        provider_factory = MagicMock(side_effect=failure)
        with self.assertRaises(RetrievalExecutionError) as raised:
            execute_retrieval(database, package, conformance_id, vector_provider_factory=provider_factory)
        self.assertIs(raised.exception.__cause__, failure)
        provider_factory.assert_called_once_with()
        connection = sqlite3.connect(database)
        self.assertEqual(connection.execute("SELECT COUNT(*) FROM retrieval_executions").fetchone()[0], 0)
        connection.close()

    def test_provider_and_factory_are_rejected_together(self):
        request = {"operator": "vector.semantic_similarity", "query": "query"}
        database, package, conformance_id = self.prepared("lazy-vector-both", [request])
        provider_factory = MagicMock()
        with self.assertRaisesRegex(RetrievalExecutionError, "cannot both be supplied"):
            execute_retrieval(
                database,
                package,
                conformance_id,
                vector_provider=object(),
                vector_provider_factory=provider_factory,
            )
        provider_factory.assert_not_called()

    def test_missing_conformance_and_package_mismatch_fail_before_surface_calls(self):
        database, package, conformance_id = self.prepared("authority", [self.exact()])
        with patch("semantic_traversal.runtime.retrieval.execution.exact_lookup") as exact:
            with self.assertRaises(RetrievalExecutionError):
                execute_retrieval(database, package, "missing")
            exact.assert_not_called()
        other = load_retrieval_package(self.other_build)
        with patch("semantic_traversal.runtime.retrieval.execution.exact_lookup") as exact:
            with self.assertRaises(RetrievalExecutionError):
                execute_retrieval(database, other, conformance_id)
            exact.assert_not_called()
        connection = sqlite3.connect(database)
        self.assertEqual(connection.execute("SELECT COUNT(*) FROM retrieval_executions").fetchone()[0], 0)
        connection.close()

    def test_non_tokenizable_terms_are_rejected_before_lexical_execution(self):
        request = {
            "operator": "lexical.terms", "field_class": "intrinsic", "field_name": "parsed_text",
            "target": "complete_value", "operand": ["plant-based"],
        }
        database, package, conformance_id = self.prepared("non-tokenizable-terms", [request])
        module = __import__("semantic_traversal.runtime.retrieval.execution", fromlist=["lexical_lookup"])
        with patch.object(module, "lexical_lookup") as lexical:
            with self.assertRaisesRegex(RetrievalExecutionError, "not executable"):
                execute_retrieval(database, package, conformance_id)
        lexical.assert_not_called()

    def test_non_tokenizable_graph_terms_are_rejected_before_graph_execution(self):
        request = {
            "operator": "graph.discovery.terms", "node_kind": "semantic_object",
            "dimension_name": "address_text", "operand": ["plant-based"],
        }
        database, package, conformance_id = self.prepared("non-tokenizable-graph-terms", [request])
        module = __import__("semantic_traversal.runtime.retrieval.execution", fromlist=["graph_discover"])
        with patch.object(module, "graph_discover") as graph_discover:
            with self.assertRaisesRegex(RetrievalExecutionError, "not executable"):
                execute_retrieval(database, package, conformance_id)
        graph_discover.assert_not_called()

    def test_partial_conformance_executes_only_valid_ordinals_and_skips_invalid_vector_provider(self):
        requests = [
            self.exact("source"),
            {"operator": "vector.semantic_similarity", "query": "invalid"},
            self.exact("body"),
        ]
        database, package, conformance_id = self.prepared("partial-valid-siblings", requests)
        connection = sqlite3.connect(database)
        connection.execute("UPDATE retrieval_conformance SET status = 'partial', result_json = ? WHERE conformance_id = ?", (
            json.dumps({"status": "partial", "requests": [
                {"ordinal": 0, "status": "valid", "violations": []},
                {"ordinal": 1, "status": "invalid", "violations": [{"code": "vector_operator_not_advertised"}]},
                {"ordinal": 2, "status": "valid", "violations": []},
            ]}, separators=(",", ":")), conformance_id,
        ))
        connection.commit(); connection.close()
        factory = MagicMock()
        result = execute_retrieval(database, package, conformance_id, vector_provider_factory=factory)
        self.assertEqual(result.status, "partial")
        self.assertEqual([item.status for item in result.requests], ["succeeded", "not_executed", "succeeded"])
        self.assertEqual(result.requests[1].failure, {"kind": "conformance_rejected"})
        factory.assert_not_called()

    def test_mutated_package_fails_before_execution_row(self):
        database, package, conformance_id = self.prepared("mutated-before", [self.exact()])
        original = package.substrate_path.read_bytes()
        package.substrate_path.write_bytes(original + b"changed")
        try:
            with patch("semantic_traversal.runtime.retrieval.execution.exact_lookup") as exact:
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
        module = __import__("semantic_traversal.runtime.retrieval.execution", fromlist=["exact_lookup"])
        with patch.object(module, "exact_lookup", return_value=()) as exact:
            result = execute_retrieval(database, package, conformance_id)
        self.assertEqual(result.status, "succeeded")
        self.assertIsInstance(exact.call_args.args[3], tuple)
        self.assertEqual(exact.call_args.args[3], ("A", "B"))

    def test_native_date_and_datetime_adapters_restore_python_types(self):
        module = __import__("semantic_traversal.runtime.retrieval.execution", fromlist=["_execute_surface", "exact_lookup"])
        package = load_retrieval_package(self.build)
        connection = sqlite3.connect(":memory:")
        try:
            date_request = {
                "operator": "exact.equals", "field_class": "semantic_identifier", "field_name": "date",
                "target": "complete_value",
                "operand": {"shape": "scalar", "domain": "date", "value": "2026-07-31"},
            }
            datetime_request = {
                "operator": "exact.equals", "field_class": "semantic_identifier", "field_name": "moment",
                "target": "complete_value",
                "operand": {"shape": "scalar", "domain": "datetime", "value": "2026-07-31T12:34:56Z"},
            }
            with patch.object(module, "exact_lookup", return_value=()) as exact:
                module._execute_surface(connection, package, date_request, None)
                self.assertIs(type(exact.call_args.args[3]), dt.date)
                self.assertEqual(exact.call_args.args[3], dt.date(2026, 7, 31))
                module._execute_surface(connection, package, datetime_request, None)
                self.assertIs(type(exact.call_args.args[3]), dt.datetime)
                self.assertEqual(exact.call_args.args[3], dt.datetime(2026, 7, 31, 12, 34, 56, tzinfo=dt.timezone.utc))
        finally:
            connection.close()

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
