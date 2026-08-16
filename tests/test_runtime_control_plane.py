import contextlib
import copy
import hashlib
import io
import json
import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from semantic_traversal.cli import main
from tests.catalog_fixtures import minimum_catalog
from semantic_traversal.runtime.control_plane import (
    CATALOG_CONFORMANCE_CONTRACT_VERSION,
    RetrievalConformanceError,
    conform_retrieval,
)
from semantic_traversal.runtime.conversation import create_conversation, initialize_runtime
from semantic_traversal.runtime.retrieval_requests import canonicalize_retrieval_requests


class RuntimeControlPlaneTests(unittest.TestCase):
    def catalog(self):
        return minimum_catalog(include_tag=True)

    def requests(self):
        return [
            {
                "operator": "exact.equals",
                "field_class": "intrinsic",
                "field_name": "parsed_text",
                "target": "complete_value",
                "operand": {"shape": "scalar", "domain": "string", "value": "absent"},
            },
            {
                "operator": "exact.equals",
                "field_class": "semantic_identifier",
                "field_name": "tags",
                "target": "member",
                "operand": {"shape": "scalar", "domain": "string", "value": "book"},
            },
            {
                "operator": "exact.equals",
                "field_class": "region",
                "field_name": "region_path",
                "target": "complete_value",
                "operand": {"shape": "ordered_sequence", "member_domain": "string", "value": ["A", "B"]},
            },
            {
                "operator": "lexical.terms",
                "field_class": "intrinsic",
                "field_name": "parsed_text",
                "target": "complete_value",
                "operand": ["one"],
            },
            {
                "operator": "lexical.phrase",
                "field_class": "semantic_identifier",
                "field_name": "tags",
                "target": "member",
                "operand": "book notes",
            },
            {"operator": "vector.semantic_similarity", "query": "query"},
            {
                "operator": "graph.discovery.terms",
                "node_kind": "semantic_object",
                "dimension_name": "tag",
                "operand": ["book"],
            },
            {
                "operator": "graph.discovery.phrase",
                "node_kind": "semantic_region",
                "dimension_name": "address_text",
                "operand": "Daily Intent",
            },
            {
                "operator": "graph.relation_occurrence_lookup",
                "relation_class": "body_wikilink",
                "relation_name": "linked_to",
            },
        ]

    def prepared(self, directory, catalog=None, requests=None):
        catalog = copy.deepcopy(catalog or self.catalog())
        requests = list(requests if requests is not None else self.requests())
        database = Path(directory) / "runtime.sqlite3"
        catalog_path = Path(directory) / "capability_catalog.json"
        raw_catalog = json.dumps(catalog, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        catalog_path.write_bytes(raw_catalog)
        initialize_runtime(database)
        conversation = create_conversation(database)
        from semantic_traversal.runtime.conversation import append_message
        append_message(database, conversation.conversation_id, "user", "question")
        catalog_sha = "sha256:" + hashlib.sha256(raw_catalog).hexdigest()
        output_json = json.dumps({"requests": requests}, ensure_ascii=False, separators=(",", ":"))
        connection = sqlite3.connect(database)
        connection.execute(
            "INSERT INTO model_runs (run_id, conversation_id, trigger_message_id, run_kind, provider, model, prompt_version, status, started_at, output_json) VALUES ('router', ?, 1, 'router', 'openai', 'model', 'prompt', 'succeeded', 'started', '{\"route\":\"semantic_retrieval\"}')",
            (conversation.conversation_id,),
        )
        connection.execute(
            "INSERT INTO model_runs (run_id, conversation_id, trigger_message_id, run_kind, parent_run_id, capability_catalog_sha256, provider, model, prompt_version, status, started_at, output_json) VALUES ('retrieval', ?, 1, 'retrieval_inference', 'router', ?, 'openai', 'model', 'prompt', 'succeeded', 'started', ?)",
            (conversation.conversation_id, catalog_sha, output_json),
        )
        connection.commit()
        connection.close()
        return database, catalog_path, "retrieval", catalog, output_json

    def test_schema_v4_and_contract_table_are_exactly_present(self):
        with TemporaryDirectory() as directory:
            database, _, _, _, _ = self.prepared(directory, requests=[])
            connection = sqlite3.connect(database)
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 4)
            self.assertEqual(
                {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")},
                {"conversations", "messages", "model_runs", "retrieval_conformance"},
            )
            columns = tuple(row[1] for row in connection.execute("PRAGMA table_info(retrieval_conformance)"))
            self.assertEqual(
                columns,
                (
                    "conformance_id", "retrieval_run_id", "retrieval_proposal_sha256",
                    "capability_catalog_sha256", "contract_version", "status",
                    "checked_at", "result_json",
                ),
            )
            self.assertEqual(
                {(row[2], row[3], row[4]) for row in connection.execute("PRAGMA foreign_key_list(retrieval_conformance)")},
                {("model_runs", "retrieval_run_id", "run_id")},
            )
            sql = connection.execute("SELECT sql FROM sqlite_master WHERE name='retrieval_conformance'").fetchone()[0]
            self.assertIn("catalog-conformance-v1", sql)
            self.assertIn("status IN ('valid', 'invalid')", sql)
            connection.close()

    def test_all_legal_surfaces_order_duplicates_and_empty_proposals_conform(self):
        with TemporaryDirectory() as directory:
            database, catalog_path, run_id, _, _ = self.prepared(directory, requests=self.requests() + [self.requests()[0]])
            result = conform_retrieval(database, catalog_path, run_id)
            self.assertEqual(result.status, "valid")
            self.assertEqual([item.ordinal for item in result.requests], list(range(10)))
            self.assertTrue(all(item.status == "valid" and not item.violations for item in result.requests))
            self.assertEqual(result.contract_version, CATALOG_CONFORMANCE_CONTRACT_VERSION)
            connection = sqlite3.connect(database)
            stored = connection.execute("SELECT status, result_json FROM retrieval_conformance").fetchone()
            self.assertEqual(stored[0], "valid")
            self.assertEqual(json.loads(stored[1])["status"], "valid")
            connection.close()
            with TemporaryDirectory() as empty_directory:
                empty_db, empty_catalog, empty_run, _, _ = self.prepared(empty_directory, requests=[])
                empty = conform_retrieval(empty_db, empty_catalog, empty_run)
                self.assertEqual(empty.status, "valid")
                self.assertEqual(empty.requests, ())

    def test_invalid_catalog_conformance_collects_exact_mechanical_violations(self):
        cases = []
        catalog = self.catalog()
        intrinsic_request = self.requests()[0]
        cases.append(("field_not_advertised", catalog, [dict(intrinsic_request, field_class="semantic_identifier", field_name="made_up")]))
        cases.append(("field_access_not_advertised", catalog, [dict(intrinsic_request, target="member")]))
        custom_catalog = copy.deepcopy(catalog)
        custom_catalog["semantic_dimensions"].append({
            "field_class": "semantic_identifier",
            "field_name": "custom",
            "description": "custom",
            "value": {"shapes": [{"shape": "scalar", "domains": ["string"]}]},
            "access": [{"operator": "exact.equals", "target": "complete_value", "domains": ["string"]}],
        })
        custom_lexical = dict(self.requests()[3], field_class="semantic_identifier", field_name="custom", target="complete_value", operand=["one"])
        cases.append(("field_access_not_advertised", custom_catalog, [custom_lexical]))
        cases.append(("operand_domain_not_advertised", catalog, [dict(intrinsic_request, operand={"shape": "scalar", "domain": "integer", "value": 7})]))
        ordered_request = dict(self.requests()[2], field_class="intrinsic", field_name="parsed_text")
        cases.append(("operand_shape_not_advertised", catalog, [ordered_request]))
        for expected_code, case_catalog, case_requests in cases:
            with self.subTest(code=expected_code), TemporaryDirectory() as directory:
                database, catalog_path, run_id, _, _ = self.prepared(directory, catalog=case_catalog, requests=case_requests)
                result = conform_retrieval(database, catalog_path, run_id)
                self.assertEqual(result.status, "invalid")
                self.assertEqual(result.requests[0].status, "invalid")
                self.assertEqual(result.requests[0].violations[0]["code"], expected_code)

    def test_partial_approval_is_all_or_nothing_and_messages_model_runs_unchanged(self):
        requests = [self.requests()[0], dict(self.requests()[0], field_name="missing"), self.requests()[5]]
        with TemporaryDirectory() as directory:
            database, catalog_path, run_id, _, output_json = self.prepared(directory, requests=requests)
            before = sqlite3.connect(database)
            before_messages = tuple(before.execute("SELECT * FROM messages"))
            before_runs = tuple(before.execute("SELECT * FROM model_runs"))
            before.close()
            result = conform_retrieval(database, catalog_path, run_id)
            self.assertEqual(result.status, "invalid")
            self.assertEqual([item.status for item in result.requests], ["valid", "invalid", "valid"])
            connection = sqlite3.connect(database)
            self.assertEqual(tuple(connection.execute("SELECT * FROM messages")), before_messages)
            self.assertEqual(tuple(connection.execute("SELECT * FROM model_runs")), before_runs)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM retrieval_conformance").fetchone()[0], 1)
            connection.close()

    def test_wrong_catalog_identity_fails_before_persistence(self):
        with TemporaryDirectory() as directory:
            database, catalog_path, run_id, catalog, _ = self.prepared(directory, requests=[])
            other = copy.deepcopy(catalog)
            other["operators"]["exact.equals"]["meaning"] = "different"
            other_path = Path(directory) / "other.json"
            other_path.write_text(json.dumps(other, separators=(",", ":")), encoding="utf-8")
            with self.assertRaisesRegex(RetrievalConformanceError, "identity"):
                conform_retrieval(database, other_path, run_id)
            connection = sqlite3.connect(database)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM retrieval_conformance").fetchone()[0], 0)
            connection.close()

    def test_retrieval_run_preconditions_fail_before_persistence(self):
        mutations = ("missing", "wrong_kind", "running", "failed", "missing_output", "missing_catalog", "malformed", "structurally_invalid")
        for mutation in mutations:
            with self.subTest(mutation=mutation), TemporaryDirectory() as directory:
                database, catalog_path, run_id, _, _ = self.prepared(directory, requests=[])
                connection = sqlite3.connect(database)
                if mutation == "wrong_kind":
                    pass
                elif mutation == "running":
                    connection.execute("UPDATE model_runs SET status='running' WHERE run_id='retrieval'")
                elif mutation == "failed":
                    connection.execute("UPDATE model_runs SET status='failed' WHERE run_id='retrieval'")
                elif mutation == "missing_output":
                    connection.execute("UPDATE model_runs SET output_json=NULL WHERE run_id='retrieval'")
                elif mutation == "missing_catalog":
                    connection.execute("UPDATE model_runs SET capability_catalog_sha256='' WHERE run_id='retrieval'")
                elif mutation == "malformed":
                    connection.execute("UPDATE model_runs SET output_json='not-json' WHERE run_id='retrieval'")
                elif mutation == "structurally_invalid":
                    connection.execute("UPDATE model_runs SET output_json='{\"requests\":[{\"operator\":\"graph.inbound_traversal\"}]}' WHERE run_id='retrieval'")
                connection.commit()
                connection.close()
                target = "missing" if mutation == "missing" else "router" if mutation == "wrong_kind" else run_id
                with self.assertRaises(RetrievalConformanceError):
                    conform_retrieval(database, catalog_path, target)
                connection = sqlite3.connect(database)
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM retrieval_conformance").fetchone()[0], 0)
                connection.close()

    def test_idempotence_returns_same_evidence_and_conflict_fails_closed(self):
        with TemporaryDirectory() as directory:
            database, catalog_path, run_id, _, _ = self.prepared(directory, requests=[])
            first = conform_retrieval(database, catalog_path, run_id)
            second = conform_retrieval(database, catalog_path, run_id)
            self.assertEqual(first, second)
            connection = sqlite3.connect(database)
            connection.execute("UPDATE retrieval_conformance SET result_json='{\"status\":\"invalid\",\"requests\":[]}'")
            connection.commit()
            connection.close()
            with self.assertRaises(RetrievalConformanceError):
                conform_retrieval(database, catalog_path, run_id)

    def test_conformance_cli_is_local_and_has_no_model_or_surface_execution(self):
        with TemporaryDirectory() as directory:
            database, catalog_path, run_id, _, _ = self.prepared(directory, requests=[])
            stdout = io.StringIO()
            with patch("semantic_traversal.cli.load_dotenv") as dotenv, patch("semantic_traversal.runtime.openai_provider.openai.OpenAI") as client, contextlib.redirect_stdout(stdout):
                code = main([
                    "runtime", "control-plane", "conform",
                    "--database", str(database),
                    "--catalog", str(catalog_path),
                    "--retrieval-run-id", run_id,
                    "--json",
                ])
            self.assertEqual(code, 0)
            dotenv.assert_not_called()
            client.assert_not_called()
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["status"], "valid")
            with patch("semantic_traversal.projection.exact.exact_lookup", side_effect=AssertionError("execution")):
                self.assertEqual(conform_retrieval(database, catalog_path, run_id).status, "valid")


if __name__ == "__main__":
    unittest.main()