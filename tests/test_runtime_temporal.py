import hashlib
import json
import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from semantic_traversal.projection.temporal import TemporalProjectionError
from semantic_traversal.runtime.config import CandidateSelectionConfig, ModelConfig, RuntimeConfig
from semantic_traversal.runtime.conversation import append_message, create_conversation, initialize_runtime
from semantic_traversal.runtime.openai_provider import (
    OpenAIResponsesProvider,
    ProviderInference,
    ProviderUsage,
    RetrievalProviderInference,
)
from semantic_traversal.runtime.retrieval.control_plane import conform_retrieval
from semantic_traversal.runtime.retrieval.execution import execute_retrieval
from semantic_traversal.runtime.retrieval.inference import infer_retrieval
from semantic_traversal.runtime.retrieval.package import load_retrieval_package
from semantic_traversal.runtime.retrieval.requests import canonicalize_retrieval_request, retrieval_proposal_schema
from semantic_traversal.runtime.router import route_conversation
from tests.test_cli import CliProvider
from tests.test_temporal import TemporalProjectionTests


FIELD = {
    "field_class": "semantic_identifier",
    "field_name": "journal_entry_date",
    "target": "complete_value",
}


def temporal(operator: str, **fields: object) -> dict[str, object]:
    return {"operator": operator, **FIELD, **fields}


class TemporalRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.directory = TemporaryDirectory()
        root = Path(cls.directory.name) / "build-source"
        root.mkdir()
        cls.build = TemporalProjectionTests()._build(root)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.directory.cleanup()

    @staticmethod
    def config() -> RuntimeConfig:
        return RuntimeConfig(
            ModelConfig("openai", "router", 1.0, "router prompt"),
            ModelConfig("openai", "retrieval", 1.0, "retrieval prompt"),
            CandidateSelectionConfig(120, 0.20),
        )

    def _prepared(self, name: str, requests: list[dict[str, object]]):
        root = Path(self.directory.name) / name
        root.mkdir()
        database = root / "runtime.sqlite3"
        initialize_runtime(database)
        conversation = create_conversation(database)
        message = append_message(database, conversation.conversation_id, "user", "question")
        package = load_retrieval_package(self.build)
        output = json.dumps({"requests": requests}, separators=(",", ":"))
        connection = sqlite3.connect(database)
        connection.execute(
            "INSERT INTO model_runs "
            "(run_id, conversation_id, trigger_message_id, run_kind, provider, model, prompt_version, status, started_at, output_json) "
            "VALUES (?, ?, ?, 'router', 'p', 'm', 'p', 'succeeded', 't', ?)",
            ("router", conversation.conversation_id, message.message_id, '{"route":"semantic_retrieval"}'),
        )
        connection.execute(
            "INSERT INTO model_runs "
            "(run_id, conversation_id, trigger_message_id, run_kind, parent_run_id, capability_catalog_sha256, provider, model, prompt_version, status, started_at, output_json) "
            "VALUES (?, ?, ?, 'retrieval_inference', 'router', ?, 'p', 'm', 'p', 'succeeded', 't', ?)",
            ("retrieval", conversation.conversation_id, message.message_id, package.identity.capability_catalog_sha256, output),
        )
        connection.commit()
        connection.close()
        conformance = conform_retrieval(database, self.build / "capability_catalog.json", "retrieval")
        return database, package, conversation, conformance

    def test_provider_schema_is_closed_and_accepts_all_temporal_shapes(self):
        schema = retrieval_proposal_schema()
        self.assertFalse(schema["additionalProperties"])
        variants = schema["properties"]["requests"]["items"]["anyOf"]
        temporal_schemas = {
            item["properties"]["operator"]["enum"][0]: item
            for item in variants
            if item["properties"]["operator"]["enum"][0].startswith("temporal.")
        }
        self.assertEqual(set(temporal_schemas), {
            "temporal.earliest", "temporal.latest", "temporal.before", "temporal.after",
            "temporal.between", "temporal.ordered",
        })
        for item in temporal_schemas.values():
            self.assertFalse(item["additionalProperties"])
        date_shapes = []
        for item in temporal_schemas.values():
            for name in ("anchor", "start", "end"):
                if name in item["properties"]:
                    operand = item["properties"][name]
                    self.assertFalse(operand["additionalProperties"])
                    self.assertEqual(operand["properties"]["domain"]["enum"], ["date"])
                    self.assertEqual(operand["properties"]["value"]["pattern"], r"^\d{4}-\d{2}-\d{2}$")
                    date_shapes.append(name)
        self.assertEqual(sorted(date_shapes), ["anchor", "anchor", "end", "start"])
        requests = [
            temporal("temporal.earliest"), temporal("temporal.latest"),
            temporal("temporal.before", anchor={"domain": "date", "value": "2026-04-16"}),
            temporal("temporal.after", anchor={"domain": "date", "value": "2026-04-16"}),
            temporal("temporal.between", start={"domain": "date", "value": "2026-04-15"}, end={"domain": "date", "value": "2026-04-16"}),
            temporal("temporal.ordered", direction="ascending"),
            temporal("temporal.ordered", direction="descending"),
        ]
        self.assertEqual(tuple(canonicalize_retrieval_request(item)["operator"] for item in requests), tuple(item["operator"] for item in requests))

    def test_provider_accepts_all_temporal_requests_without_catalog_conformance(self):
        requests = [
            temporal("temporal.earliest"), temporal("temporal.latest"),
            temporal("temporal.before", anchor={"domain": "date", "value": "2026-04-16"}),
            temporal("temporal.after", anchor={"domain": "date", "value": "2026-04-16"}),
            temporal("temporal.between", start={"domain": "date", "value": "2026-04-15"}, end={"domain": "date", "value": "2026-04-16"}),
            temporal("temporal.ordered", direction="ascending"),
            temporal("temporal.ordered", direction="descending"),
        ]

        class Responses:
            def create(self, **kwargs):
                return type("Response", (), {"output_text": json.dumps({"requests": requests}), "id": "response", "usage": None})()

        class Client:
            def __init__(self, **kwargs):
                self.responses = Responses()

        with patch("semantic_traversal.runtime.openai_provider.openai.OpenAI", Client):
            result = OpenAIResponsesProvider().infer_retrieval(self.config().retrieval_inference, "catalog", ())
        self.assertEqual(result.requests, tuple(requests))
        self.assertEqual(json.loads(result.output_json)["requests"], requests)

    def test_mixed_temporal_conformance_preserves_request_order(self):
        requests = [
            {"operator": "lexical.terms", "field_class": "intrinsic", "field_name": "parsed_text", "target": "complete_value", "operand": ["unit"]},
            temporal("temporal.before", anchor={"domain": "date", "value": "2026-04-16"}),
            {"operator": "vector.semantic_similarity", "query": "unit"},
        ]
        _, _, _, result = self._prepared("mixed-conformance", requests)
        self.assertEqual(result.status, "valid")
        self.assertEqual([item.ordinal for item in result.requests], [0, 1, 2])

    def test_temporal_conformance_fails_closed_for_wrong_field_or_removed_access(self):
        wrong_database, _, _, _ = self._prepared("wrong-field", [temporal("temporal.earliest", field_name="headspace")])
        wrong_result = conform_retrieval(wrong_database, self.build / "capability_catalog.json", "retrieval")
        self.assertEqual(wrong_result.status, "invalid")

        requests = [temporal("temporal.earliest")]
        database, _, _, _ = self._prepared("removed-access", requests)
        catalog = json.loads((self.build / "capability_catalog.json").read_text(encoding="utf-8"))
        dimension = next(item for item in catalog["semantic_dimensions"] if item["field_name"] == "journal_entry_date")
        dimension["access"] = [item for item in dimension["access"] if item["operator"] != "temporal.earliest"]
        path = Path(self.directory.name) / "removed-access.json"
        raw = json.dumps(catalog, separators=(",", ":")).encode("utf-8")
        path.write_bytes(raw)
        connection = sqlite3.connect(database)
        connection.execute("DELETE FROM retrieval_conformance")
        connection.execute(
            "UPDATE model_runs SET capability_catalog_sha256 = ?, output_json = ? WHERE run_id = 'retrieval'",
            ("sha256:" + hashlib.sha256(raw).hexdigest(), json.dumps({"requests": requests}, separators=(",", ":"))),
        )
        connection.commit()
        connection.close()
        try:
            result = conform_retrieval(database, path, "retrieval")
        except ValueError:
            return
        self.assertEqual(result.status, "invalid")

    def test_all_temporal_operations_execute_to_native_temporal_facts(self):
        requests = [
            temporal("temporal.earliest"), temporal("temporal.latest"),
            temporal("temporal.before", anchor={"domain": "date", "value": "2026-04-16"}),
            temporal("temporal.after", anchor={"domain": "date", "value": "2026-04-16"}),
            temporal("temporal.between", start={"domain": "date", "value": "2026-04-15"}, end={"domain": "date", "value": "2026-04-16"}),
            temporal("temporal.ordered", direction="descending"),
        ]
        database, package, _, conformance = self._prepared("execution", requests)
        result = execute_retrieval(database, package, conformance.conformance_id)
        self.assertEqual(result.status, "succeeded")
        expected = ((1, 2), (7,), (3, 1, 2), (6, 7), (3, 4, 5), (7, 6, 4, 5, 3, 1, 2))
        for item, unit_ids in zip(result.requests, expected):
            self.assertEqual(item.result["kind"], "temporal")
            self.assertEqual([hit["unit_id"] for hit in item.result["hits"]], list(unit_ids))
            self.assertTrue(all(set(hit) == {"unit_id", "date"} for hit in item.result["hits"]))

    def test_temporal_projection_failure_fails_fast_without_retry(self):
        requests = [
            temporal("temporal.earliest"),
            temporal("temporal.before", anchor={"domain": "date", "value": "2026-04-16"}),
            temporal("temporal.latest"),
        ]
        database, package, _, conformance = self._prepared("fail-fast", requests)
        with patch("semantic_traversal.runtime.retrieval.execution.before", side_effect=TemporalProjectionError("forced temporal failure")) as before:
            result = execute_retrieval(database, package, conformance.conformance_id)
        self.assertEqual([item.status for item in result.requests], ["succeeded", "failed", "not_executed"])
        self.assertEqual(result.status, "failed")
        self.assertEqual(before.call_count, 1)

    def test_conversation_history_can_supply_a_temporal_anchor(self):
        root = Path(self.directory.name) / "conversation-anchor"
        root.mkdir()
        database = root / "runtime.sqlite3"
        initialize_runtime(database)
        conversation = create_conversation(database)
        append_message(database, conversation.conversation_id, "user", "When did I go vegan?")
        append_message(database, conversation.conversation_id, "synthesis", "You went vegan on April 16, 2026.")
        append_message(database, conversation.conversation_id, "user", "What led up to that?")
        router = route_conversation(
            database,
            self.config(),
            conversation.conversation_id,
            provider=type("Router", (), {"infer_router": lambda self, config, messages: ProviderInference("semantic_retrieval", '{"route":"semantic_retrieval"}', None, ProviderUsage())})(),
        )
        provider = type(
            "Retrieval",
            (),
            {"infer_retrieval": lambda self, config, catalog_text, messages: RetrievalProviderInference((temporal("temporal.before", anchor={"domain": "date", "value": "2026-04-16"}),), "id", None, ProviderUsage())},
        )()
        result = infer_retrieval(database, self.config(), self.build / "capability_catalog.json", router.run_id, provider=provider)
        self.assertEqual(result.requests[0]["anchor"], {"domain": "date", "value": "2026-04-16"})


if __name__ == "__main__":
    unittest.main()
