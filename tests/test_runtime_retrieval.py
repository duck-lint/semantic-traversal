import hashlib
import json
import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from tests.catalog_fixtures import minimum_catalog
from semantic_traversal.runtime.config import ModelConfig, RuntimeConfig, RuntimeConfigError, load_runtime_config
from semantic_traversal.runtime.conversation import append_message, create_conversation, initialize_runtime
from semantic_traversal.runtime.openai_provider import (
    OpenAIProviderError,
    OpenAIResponsesProvider,
    ProviderInference,
    ProviderUsage,
    RetrievalProviderInference,
)
from semantic_traversal.runtime.prompts import prompt_version
from semantic_traversal.runtime.retrieval.inference import RuntimeRetrievalError, infer_retrieval
from semantic_traversal.runtime.retrieval.requests import RetrievalRequestError, canonicalize_retrieval_request
from semantic_traversal.runtime.router import route_conversation


class RetrievalProviderDouble:
    def __init__(self, inference):
        self.inference = inference
        self.calls = []

    def infer_retrieval(self, config, catalog_text, messages):
        self.calls.append((config, catalog_text, tuple(messages)))
        return self.inference


class RuntimeRetrievalTests(unittest.TestCase):
    def config(self):
        return RuntimeConfig(
            ModelConfig("openai", "router-model", 3.0, "router prompt\n"),
            ModelConfig("openai", "retrieval-model", 4.0, "retrieval prompt\n"),
        )

    def catalog(self, directory):
        path = Path(directory) / "capability_catalog.json"
        content = json.dumps(minimum_catalog(), separators=(",", ":"))
        path.write_bytes(content.encode("utf-8"))
        return path, content

    def prepared(self, directory, route="semantic_retrieval"):
        database = Path(directory) / "runtime.sqlite3"
        initialize_runtime(database)
        conversation = create_conversation(database)
        append_message(database, conversation.conversation_id, "user", "first")
        router_provider = type("RouterProvider", (), {"infer_router": lambda self, config, messages: ProviderInference(route, json.dumps({"route": route}), None, ProviderUsage())})()
        router = route_conversation(database, self.config(), conversation.conversation_id, provider=router_provider)
        return database, conversation, router

    def test_mapping_is_not_a_legal_exact_request_domain(self):
        with self.assertRaises(RetrievalRequestError):
            canonicalize_retrieval_request({
                "operator": "exact.equals",
                "field_class": "semantic_identifier",
                "field_name": "mapping_field",
                "target": "complete_value",
                "operand": {"shape": "scalar", "domain": "mapping", "value": {"label": "value"}},
            })
    def test_config_has_two_exact_model_sections_and_hashes_exact_prompt_bytes(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "runtime.yaml"
            path.write_text(
                "router:\n  provider: openai\n  model: m\n  timeout_seconds: 1\n  prompt: |\n    line  \nretrieval_inference:\n  provider: openai\n  model: m2\n  timeout_seconds: 2\n  prompt: retrieval\npacket:\n  max_occurrences: 32\n",
                encoding="utf-8",
            )
            config = load_runtime_config(path)
            self.assertEqual(config.router.prompt, "line  \n")
            self.assertEqual(prompt_version(config.router.prompt), "sha256:" + hashlib.sha256("line  \n".encode()).hexdigest())
            self.assertEqual(set(config.router.__dataclass_fields__), {"provider", "model", "timeout_seconds", "prompt"})
            path.write_text(
                "router:\n  provider: openai\n  model: m\n  timeout_seconds: 1\n  prompt: p\n",
                encoding="utf-8",
            )
            with self.assertRaises(RuntimeConfigError):
                load_runtime_config(path)

    def test_success_preserves_catalog_bytes_order_and_lineage(self):
        with TemporaryDirectory() as directory:
            database, conversation, router = self.prepared(directory)
            catalog_path, catalog_text = self.catalog(directory)
            request = {"operator": "exact.equals", "field_class": "semantic_identifier", "field_name": "made_up", "target": "complete_value", "operand": {"shape": "scalar", "domain": "string", "value": "unchanged"}}
            provider = RetrievalProviderDouble(RetrievalProviderInference((request, request), "ignored", "retrieval-response", ProviderUsage(9, 1, 2, 3, 12)))
            result = infer_retrieval(database, self.config(), catalog_path, router.run_id, provider=provider)
            self.assertEqual(result.requests, (request, request))
            self.assertEqual(result.prompt_version, prompt_version("retrieval prompt\n"))
            self.assertEqual(result.capability_catalog_sha256, "sha256:" + hashlib.sha256(catalog_text.encode()).hexdigest())
            self.assertEqual(len(provider.calls), 1)
            self.assertEqual(provider.calls[0][1], catalog_text)
            self.assertEqual([m.content for m in provider.calls[0][2]], ["first"])
            connection = sqlite3.connect(database)
            row = connection.execute("SELECT run_kind, parent_run_id, capability_catalog_sha256, output_json, status FROM model_runs WHERE run_id = ?", (result.run_id,)).fetchone()
            connection.close()
            self.assertEqual(row, ("retrieval_inference", router.run_id, result.capability_catalog_sha256, '{"requests":[{"operator":"exact.equals","field_class":"semantic_identifier","field_name":"made_up","target":"complete_value","operand":{"shape":"scalar","domain":"string","value":"unchanged"}},{"operator":"exact.equals","field_class":"semantic_identifier","field_name":"made_up","target":"complete_value","operand":{"shape":"scalar","domain":"string","value":"unchanged"}}]}', "succeeded"))

    def test_empty_proposal_is_valid_and_parent_preconditions_fail_without_provider_contact(self):
        with TemporaryDirectory() as directory:
            database, conversation, router = self.prepared(directory)
            catalog_path, _ = self.catalog(directory)
            provider = RetrievalProviderDouble(RetrievalProviderInference((), '{"requests":[]}', None, ProviderUsage()))
            self.assertEqual(infer_retrieval(database, self.config(), catalog_path, router.run_id, provider=provider).requests, ())
            direct_db, direct_conversation, direct_router = self.prepared(directory + "-direct", route="direct") if False else (None, None, None)
            with self.assertRaises(RuntimeRetrievalError):
                infer_retrieval(database, self.config(), catalog_path, "missing", provider=provider)
            self.assertEqual(len(provider.calls), 1)
    def test_stale_router_trigger_is_rejected_before_provider_contact(self):
        with TemporaryDirectory() as directory:
            database, conversation, router = self.prepared(directory)
            catalog_path, _ = self.catalog(directory)
            append_message(database, conversation.conversation_id, "user", "new trigger")
            provider = RetrievalProviderDouble(RetrievalProviderInference((), '{"requests":[]}', None, ProviderUsage()))
            with self.assertRaises(RuntimeRetrievalError):
                infer_retrieval(database, self.config(), catalog_path, router.run_id, provider=provider)
            self.assertEqual(provider.calls, [])
            connection = sqlite3.connect(database)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM model_runs WHERE run_kind='retrieval_inference'").fetchone()[0], 0)
            connection.close()

    def test_provider_rejects_traversal_and_unknown_roles_before_contact(self):
        class Responses:
            def create(self, **kwargs):
                return type("Response", (), {"output_text": '{"requests":[{"operator":"graph.inbound_traversal"}]}', "id": "x", "usage": None})()
        class Client:
            def __init__(self, **kwargs):
                self.responses = Responses()
        messages = (type("Message", (), {"role": "router", "content": "bad"})(),)
        config = self.config()
        with patch("semantic_traversal.runtime.openai_provider.openai.OpenAI") as opened:
            with self.assertRaises(OpenAIProviderError) as error:
                OpenAIResponsesProvider().infer_router(config.router, messages)
            self.assertEqual(error.exception.error_type, "runtime_validation")
            opened.assert_not_called()
        messages = ()
        with patch("semantic_traversal.runtime.openai_provider.openai.OpenAI", Client):
            with self.assertRaises(OpenAIProviderError) as error:
                OpenAIResponsesProvider().infer_retrieval(config.retrieval_inference, "catalog", messages)
            self.assertEqual(error.exception.error_type, "runtime_validation")


    def test_provider_accepts_all_first_stage_request_shapes_without_catalog_conformance(self):
        requests = [
            {"operator": "exact.equals", "field_class": "semantic_identifier", "field_name": "invented", "target": "complete_value", "operand": {"shape": "scalar", "domain": "integer", "value": 7}},
            {"operator": "lexical.terms", "field_class": "intrinsic", "field_name": "parsed_text", "target": "complete_value", "operand": ["one"]},
            {"operator": "lexical.phrase", "field_class": "intrinsic", "field_name": "parsed_text", "target": "complete_value", "operand": "one phrase"},
            {"operator": "vector.semantic_similarity", "query": "query"},
            {"operator": "graph.discovery.terms", "node_kind": "semantic_object", "dimension_name": "tag", "operand": ["book"]},
            {"operator": "graph.discovery.phrase", "node_kind": "semantic_object", "dimension_name": "tag", "operand": "book notes"},
            {"operator": "graph.relation_occurrence_lookup", "relation_class": "body_wikilink", "relation_name": "linked_to"},
        ]
        class Responses:
            def create(self, **kwargs):
                return type("Response", (), {"output_text": json.dumps({"requests": requests}), "id": "response", "usage": None})()
        class Client:
            def __init__(self, **kwargs):
                self.responses = Responses()
        with patch("semantic_traversal.runtime.openai_provider.openai.OpenAI", Client):
            result = OpenAIResponsesProvider().infer_retrieval(self.config().retrieval_inference, "catalog-bytes", ())
        self.assertEqual([request["operator"] for request in result.requests], [request["operator"] for request in requests])
        self.assertEqual(json.loads(result.output_json)["requests"], requests)
    def test_provider_double_output_is_revalidated_and_failed_durably(self):
        invalid_requests = [
            {"operator": "graph.inbound_traversal"},
            {"operator": "graph.outbound_traversal"},
            {"operator": "unknown"},
            {"operator": "exact.equals", "field_class": "intrinsic", "field_name": "parsed_text", "target": "complete_value", "operand": {"shape": "scalar", "domain": "integer", "value": "wrong"}},
            {"operator": "lexical.terms", "field_class": "intrinsic", "field_name": "parsed_text", "target": "complete_value", "operand": []},
            {"operator": "graph.discovery.terms", "node_kind": "semantic_object", "dimension_name": "tag", "operand": []},
            {"operator": "vector.semantic_similarity", "query": "query", "unexpected": True},
        ]
        for request in invalid_requests:
            with self.subTest(operator=request["operator"]), TemporaryDirectory() as directory:
                database, conversation, router = self.prepared(directory)
                catalog_path, _ = self.catalog(directory)
                provider = RetrievalProviderDouble(RetrievalProviderInference((request,), "provider-output", None, ProviderUsage()))
                with self.assertRaisesRegex(RuntimeRetrievalError, "runtime_validation"):
                    infer_retrieval(database, self.config(), catalog_path, router.run_id, provider=provider)
                connection = sqlite3.connect(database)
                row = connection.execute(
                    "SELECT status, output_json, error_type FROM model_runs WHERE run_kind='retrieval_inference'"
                ).fetchone()
                self.assertEqual(row, ("failed", None, "runtime_validation"))
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 1)
                connection.close()

    def test_provider_double_accepts_all_first_stage_shapes_in_order_without_catalog_authorization(self):
        requests = (
            {"operator": "exact.equals", "field_class": "semantic_identifier", "field_name": "made_up", "target": "complete_value", "operand": {"shape": "scalar", "domain": "integer", "value": 7}},
            {"operator": "lexical.terms", "field_class": "intrinsic", "field_name": "parsed_text", "target": "complete_value", "operand": ["one"]},
            {"operator": "lexical.phrase", "field_class": "intrinsic", "field_name": "parsed_text", "target": "complete_value", "operand": "one phrase"},
            {"operator": "vector.semantic_similarity", "query": "query"},
            {"operator": "graph.discovery.terms", "node_kind": "semantic_object", "dimension_name": "tag", "operand": ["book"]},
            {"operator": "graph.discovery.phrase", "node_kind": "semantic_object", "dimension_name": "tag", "operand": "book notes"},
            {"operator": "graph.relation_occurrence_lookup", "relation_class": "body_wikilink", "relation_name": "linked_to"},
        )
        with TemporaryDirectory() as directory:
            database, conversation, router = self.prepared(directory)
            catalog_path, _ = self.catalog(directory)
            provider = RetrievalProviderDouble(RetrievalProviderInference(requests + (requests[0],), "ignored", None, ProviderUsage()))
            result = infer_retrieval(database, self.config(), catalog_path, router.run_id, provider=provider)
            self.assertEqual(result.requests, requests + (requests[0],))
            self.assertEqual(len(provider.calls), 1)
            connection = sqlite3.connect(database)
            self.assertEqual(connection.execute("SELECT status FROM model_runs WHERE run_id=?", (result.run_id,)).fetchone()[0], "succeeded")
            connection.close()

    def test_numeric_catalog_schema_version_is_rejected_before_provider_contact(self):
        with TemporaryDirectory() as directory:
            database, conversation, router = self.prepared(directory)
            path = Path(directory) / "capability_catalog.json"
            path.write_text('{"catalog_schema_version":1,"semantic_dimensions":[],"graph":{},"vector":{},"operators":{}}', encoding="utf-8")
            provider = RetrievalProviderDouble(RetrievalProviderInference((), "ignored", None, ProviderUsage()))
            with self.assertRaises(RuntimeRetrievalError):
                infer_retrieval(database, self.config(), path, router.run_id, provider=provider)
            self.assertEqual(provider.calls, [])

    def test_parent_preconditions_fail_closed_without_retrieval_provider_contact(self):
        cases = ("missing", "wrong_kind", "running", "failed", "direct", "stale", "latest_synthesis")
        for case in cases:
            with self.subTest(case=case), TemporaryDirectory() as directory:
                database, conversation, router = self.prepared(directory, route="direct" if case == "direct" else "semantic_retrieval")
                catalog_path, _ = self.catalog(directory)
                parent_id = router.run_id
                connection = sqlite3.connect(database)
                if case == "wrong_kind":
                    parent_id = "wrong-kind"
                    connection.execute("INSERT INTO model_runs (run_id, conversation_id, trigger_message_id, run_kind, parent_run_id, capability_catalog_sha256, provider, model, prompt_version, status, started_at) VALUES (?, ?, ?, 'retrieval_inference', ?, 'sha256:x', 'openai', 'm', 'p', 'running', 't')", (parent_id, conversation.conversation_id, 1, router.run_id))
                    connection.commit()
                elif case in {"running", "failed"}:
                    parent_id = case
                    connection.execute("INSERT INTO model_runs (run_id, conversation_id, trigger_message_id, run_kind, provider, model, prompt_version, status, started_at, error_type) VALUES (?, ?, ?, 'router', 'openai', 'm', 'p', ?, 't', ?)", (parent_id, conversation.conversation_id, 1, case, "injected" if case == "failed" else None))
                    connection.commit()
                connection.close()
                if case == "stale":
                    append_message(database, conversation.conversation_id, "user", "new")
                elif case == "latest_synthesis":
                    append_message(database, conversation.conversation_id, "synthesis", "late")
                provider = RetrievalProviderDouble(RetrievalProviderInference((), "ignored", None, ProviderUsage()))
                with self.assertRaises(RuntimeRetrievalError):
                    infer_retrieval(database, self.config(), catalog_path, "missing" if case == "missing" else parent_id, provider=provider)
                self.assertEqual(provider.calls, [])
    def test_v2_migration_hashes_historical_router_prompt_and_preserves_rows(self):
        with TemporaryDirectory() as directory:
            database = Path(directory) / "runtime.sqlite3"
            connection = sqlite3.connect(database)
            connection.executescript(
                """
                CREATE TABLE conversations (conversation_id TEXT PRIMARY KEY, created_at TEXT NOT NULL);
                CREATE TABLE messages (
                    message_id INTEGER PRIMARY KEY,
                    conversation_id TEXT NOT NULL,
                    ordinal INTEGER NOT NULL,
                    role TEXT NOT NULL CHECK (role IN ('user', 'synthesis')),
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (conversation_id) REFERENCES conversations(conversation_id),
                    UNIQUE (conversation_id, ordinal)
                );
                CREATE TABLE model_runs (
                    run_id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL,
                    trigger_message_id INTEGER NOT NULL,
                    run_kind TEXT NOT NULL CHECK (run_kind IN ('router')),
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
                    FOREIGN KEY (conversation_id) REFERENCES conversations(conversation_id),
                    FOREIGN KEY (trigger_message_id) REFERENCES messages(message_id)
                );
                PRAGMA user_version = 2;
                """
            )
            connection.execute("INSERT INTO conversations VALUES ('c', 'created')")
            connection.execute("INSERT INTO messages(conversation_id, ordinal, role, content, created_at) VALUES ('c', 0, 'user', 'hello', 'created')")
            connection.execute("INSERT INTO model_runs(run_id, conversation_id, trigger_message_id, run_kind, provider, model, prompt_version, status, started_at) VALUES ('r', 'c', 1, 'router', 'openai', 'm', 'router-v1', 'succeeded', 'started')")
            before = tuple(connection.execute("SELECT * FROM model_runs"))
            connection.commit()
            connection.close()
            from semantic_traversal.runtime.conversation import migrate_runtime
            migrate_runtime(database)
            connection = sqlite3.connect(database)
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 5)
            self.assertEqual(connection.execute("SELECT prompt_version, parent_run_id, capability_catalog_sha256 FROM model_runs").fetchone(), ("sha256:32abaecb56571dd80b6915ac2b6c01a0e02cbbec0487dc3aa1e6aeb74d0ca352", None, None))
            self.assertEqual(connection.execute("SELECT run_id, conversation_id, trigger_message_id, run_kind, provider, model, status, started_at FROM model_runs").fetchone(), (before[0][0], before[0][1], before[0][2], before[0][3], before[0][4], before[0][5], before[0][7], before[0][8]))
            connection.close()
if __name__ == "__main__":
    unittest.main()
