import datetime as dt
import hashlib
import json
import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from tests.test_temporal import TemporalProjectionTests
from tests.test_cli import CliProvider
from semantic_traversal.runtime.config import ModelConfig, PacketConfig, RuntimeConfig
from semantic_traversal.runtime.conversation import append_message, create_conversation, initialize_runtime
from semantic_traversal.runtime.openai_provider import ProviderInference, ProviderUsage, RetrievalProviderInference
from semantic_traversal.runtime.retrieval.control_plane import conform_retrieval
from semantic_traversal.runtime.retrieval.execution import execute_retrieval
from semantic_traversal.projection.temporal import TemporalProjectionError
from semantic_traversal.runtime.retrieval.hydration import RetrievalHydrationError, hydrate_retrieval_execution
from semantic_traversal.runtime.retrieval.package import load_retrieval_package
from semantic_traversal.runtime.retrieval.requests import canonicalize_retrieval_request, retrieval_proposal_schema
from semantic_traversal.runtime.synthesis import SynthesisProviderResult, SynthesisUsage
from semantic_traversal.runtime.synthesis import SynthesisInput, SynthesisMessage, SYNTHESIS_INPUT_CONTRACT_VERSION, serialize_synthesis_input, synthesis_input_sha256
from semantic_traversal.runtime.retrieval.packet import assemble_retrieval_packet
from semantic_traversal.runtime.turn import execute_turn
from semantic_traversal.runtime.router import route_conversation
from semantic_traversal.runtime.retrieval.inference import infer_retrieval


FIELD = {"field_class": "semantic_identifier", "field_name": "journal_entry_date", "target": "complete_value"}


def temporal(operator, **fields):
    return {"operator": operator, **FIELD, **fields}


class TemporalRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = TemporaryDirectory()
        root = Path(cls.directory.name) / "build"
        root.mkdir()
        cls.build = TemporalProjectionTests()._build(root)

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def prepared(self, name, requests):
        root = Path(self.directory.name) / name
        root.mkdir()
        database = root / "runtime.sqlite3"
        initialize_runtime(database)
        conversation = create_conversation(database)
        message = append_message(database, conversation.conversation_id, "user", "question")
        package = load_retrieval_package(self.build)
        output = json.dumps({"requests": requests}, separators=(",", ":"))
        connection = sqlite3.connect(database)
        connection.execute("INSERT INTO model_runs (run_id, conversation_id, trigger_message_id, run_kind, provider, model, prompt_version, status, started_at, output_json) VALUES (?, ?, ?, 'router', 'p', 'm', 'p', 'succeeded', 't', ?)", ("router", conversation.conversation_id, message.message_id, '{"route":"semantic_retrieval"}'))
        connection.execute("INSERT INTO model_runs (run_id, conversation_id, trigger_message_id, run_kind, parent_run_id, capability_catalog_sha256, provider, model, prompt_version, status, started_at, output_json) VALUES (?, ?, ?, 'retrieval_inference', 'router', ?, 'p', 'm', 'p', 'succeeded', 't', ?)", ("retrieval", conversation.conversation_id, message.message_id, package.identity.capability_catalog_sha256, output))
        connection.commit()
        connection.close()
        conformance = conform_retrieval(database, self.build / "capability_catalog.json", "retrieval")
        return database, package, conversation, conformance

    def test_provider_schema_and_all_temporal_request_shapes(self):
        schema = retrieval_proposal_schema()
        variants = schema["properties"]["requests"]["items"]["anyOf"]
        operators = {item["properties"]["operator"]["enum"][0] for item in variants}
        self.assertTrue({"temporal.earliest", "temporal.latest", "temporal.before", "temporal.after", "temporal.between", "temporal.ordered"} <= operators)
        temporal_schemas = [item for item in variants if item["properties"]["operator"].get("enum", [""])[0].startswith("temporal.")]
        for item in temporal_schemas:
            self.assertFalse(item["additionalProperties"])
        literal = next(item for item in temporal_schemas if "anchor" in item["properties"])["properties"]["anchor"]
        self.assertEqual(literal["properties"]["domain"]["enum"], ["date"])
        self.assertEqual(literal["properties"]["value"]["pattern"], r"^\d{4}-\d{2}-\d{2}$")
        valid = [
            temporal("temporal.earliest"), temporal("temporal.latest"),
            temporal("temporal.before", anchor={"domain": "date", "value": "2026-04-16"}),
            temporal("temporal.after", anchor={"domain": "date", "value": "2026-04-16"}),
            temporal("temporal.between", start={"domain": "date", "value": "2026-04-15"}, end={"domain": "date", "value": "2026-04-16"}),
            temporal("temporal.ordered", direction="ascending"), temporal("temporal.ordered", direction="descending"),
        ]
        self.assertEqual([canonicalize_retrieval_request(item)["operator"] for item in valid], [item["operator"] for item in valid])
        invalid = [
            {"operator": "temporal.earliest", **{k: v for k, v in FIELD.items() if k != "target"}},
            temporal("temporal.latest", target="member"),
            temporal("temporal.before", anchor={"domain": "date", "value": "2026-02-30"}),
            temporal("temporal.before", anchor={"domain": "string", "value": "2026-04-16"}),
            temporal("temporal.before", anchor={"domain": "date", "value": "2026-04-16T00:00:00"}),
            temporal("temporal.between", start={"domain": "date", "value": "2026-04-17"}, end={"domain": "date", "value": "2026-04-16"}),
            temporal("temporal.ordered"),
            temporal("temporal.ordered", direction="nearest"),
            {**temporal("temporal.latest"), "extra": True},
        ]
        for item in invalid:
            with self.subTest(item=item), self.assertRaises(ValueError):
                canonicalize_retrieval_request(item)

    def test_temporal_conformance_and_mixed_model_order(self):
        requests = [
            {"operator": "lexical.terms", "field_class": "intrinsic", "field_name": "parsed_text", "target": "complete_value", "operand": ["unit"]},
            temporal("temporal.before", anchor={"domain": "date", "value": "2026-04-16"}),
            {"operator": "vector.semantic_similarity", "query": "unit"},
        ]
        _, _, _, result = self.prepared("conformance", requests)
        self.assertEqual(result.status, "valid")
        self.assertEqual([item.ordinal for item in result.requests], [0, 1, 2])

    def test_temporal_conformance_rejects_wrong_field_and_removed_access(self):
        request = temporal("temporal.earliest")
        for mutation in ("wrong-field", "removed-access"):
            database, _, _, _ = self.prepared("bad-conformance-" + mutation, [request])
            catalog = json.loads((self.build / "capability_catalog.json").read_text(encoding="utf-8"))
            if mutation == "wrong-field":
                request = temporal("temporal.earliest", field_name="headspace")
            else:
                request = temporal("temporal.earliest")
                dimension = next(item for item in catalog["semantic_dimensions"] if item["field_name"] == "journal_entry_date")
                dimension["access"] = [item for item in dimension["access"] if item["operator"] != "temporal.earliest"]
            path = Path(self.directory.name) / f"{mutation}.json"
            raw = json.dumps(catalog, separators=(",", ":")).encode("utf-8")
            path.write_bytes(raw)
            connection = sqlite3.connect(database)
            connection.execute("DELETE FROM retrieval_conformance")
            connection.execute("UPDATE model_runs SET capability_catalog_sha256 = ?, output_json = ? WHERE run_id = 'retrieval'", ("sha256:" + hashlib.sha256(raw).hexdigest(), json.dumps({"requests": [request]}, separators=(",", ":"))))
            connection.commit()
            connection.close()
            try:
                result = conform_retrieval(database, path, "retrieval")
            except ValueError:
                continue
            self.assertEqual(result.status, "invalid")

    def test_all_temporal_operations_persist_exact_hits_without_scores(self):
        requests = [
            temporal("temporal.earliest"), temporal("temporal.latest"),
            temporal("temporal.before", anchor={"domain": "date", "value": "2026-04-16"}),
            temporal("temporal.after", anchor={"domain": "date", "value": "2026-04-16"}),
            temporal("temporal.between", start={"domain": "date", "value": "2026-04-15"}, end={"domain": "date", "value": "2026-04-16"}),
            temporal("temporal.ordered", direction="descending"),
        ]
        database, package, _, conformance = self.prepared("execution", requests)
        result = execute_retrieval(database, package, conformance.conformance_id)
        self.assertEqual(result.status, "succeeded")
        expected = ((1, 2), (7,), (3, 1, 2), (6, 7), (3, 4, 5), (7, 6, 4, 5, 3, 1, 2))
        for item, unit_ids in zip(result.requests, expected):
            self.assertEqual(item.status, "succeeded")
            self.assertEqual(item.result["kind"], "temporal")
            self.assertEqual([hit["unit_id"] for hit in item.result["hits"]], list(unit_ids))
            self.assertTrue(all(set(hit) == {"unit_id", "date"} for hit in item.result["hits"]))
        hydrated = hydrate_retrieval_execution(database, package, result.execution_id)
        self.assertIsInstance(hydrated.requests[2].result.hits[0].date, dt.date)
        packet = assemble_retrieval_packet(hydrated, PacketConfig(32)).packet
        synthesis_input = SynthesisInput(SYNTHESIS_INPUT_CONTRACT_VERSION, "semantic_retrieval", (SynthesisMessage(0, "user", "question"),), packet)
        first = serialize_synthesis_input(synthesis_input)
        self.assertEqual(first, serialize_synthesis_input(synthesis_input))
        self.assertEqual(synthesis_input_sha256(first), synthesis_input_sha256(serialize_synthesis_input(synthesis_input)))
        self.assertIn('"type":"date"', first)

    def test_temporal_projection_error_fails_fast_without_retry(self):
        requests = [
            temporal("temporal.earliest"),
            temporal("temporal.before", anchor={"domain": "date", "value": "2026-04-16"}),
            temporal("temporal.latest"),
        ]
        database, package, _, conformance = self.prepared("fail-fast", requests)
        with patch("semantic_traversal.runtime.retrieval.execution.before", side_effect=TemporalProjectionError("forced temporal failure")) as before:
            result = execute_retrieval(database, package, conformance.conformance_id)
        self.assertEqual([item.status for item in result.requests], ["succeeded", "failed", "not_executed"])
        self.assertEqual(result.status, "failed")
        self.assertEqual(before.call_count, 1)

    def test_hydration_rejects_wrong_temporal_dates(self):
        request = temporal("temporal.before", anchor={"domain": "date", "value": "2026-04-16"})
        for replacement in ("2026-04-17", "2026-04-14", "not-a-date"):
            with self.subTest(replacement=replacement):
                database, package, _, conformance = self.prepared("hydrate-" + replacement.replace("-", ""), [request])
                execution = execute_retrieval(database, package, conformance.conformance_id)
                connection = sqlite3.connect(database)
                payload = json.loads(connection.execute("SELECT result_json FROM retrieval_executions").fetchone()[0])
                payload["requests"][0]["result"]["hits"][0]["date"] = replacement
                connection.execute("UPDATE retrieval_executions SET result_json = ?", (json.dumps(payload, separators=(",", ":")),))
                connection.commit()
                connection.close()
                with self.assertRaises(RetrievalHydrationError):
                    hydrate_retrieval_execution(database, package, execution.execution_id)

    def test_temporal_only_turn_never_constructs_vector_provider(self):
        root = Path(self.directory.name) / "turn"
        root.mkdir()
        database = root / "runtime.sqlite3"
        initialize_runtime(database)
        conversation = create_conversation(database)
        append_message(database, conversation.conversation_id, "user", "What led up to that?")
        calls = []

        class Router:
            def infer_router(self, config, messages):
                calls.append("router")
                return ProviderInference("semantic_retrieval", '{"route":"semantic_retrieval"}', None, ProviderUsage())

        class Retrieval:
            def infer_retrieval(self, config, catalog_text, messages):
                calls.append("retrieval")
                return RetrievalProviderInference((temporal("temporal.before", anchor={"domain": "date", "value": "2026-04-16"}),), "id", None, ProviderUsage())

        class Synthesis:
            def synthesize(self, config, synthesis_input):
                calls.append("synthesis")
                return SynthesisProviderResult("answer", None, SynthesisUsage())

        runtime_config = RuntimeConfig(ModelConfig("openai", "r", 1, "r"), ModelConfig("openai", "i", 1, "i"), PacketConfig(32), ModelConfig("openai", "s", 1, "s"))
        with patch("semantic_traversal.runtime.turn.OllamaEmbeddingProvider", side_effect=AssertionError("temporal-only turn constructed vector provider")):
            result = execute_turn(database, runtime_config, conversation.conversation_id, retrieval_build=self.build, router_provider=Router(), retrieval_provider=Retrieval(), synthesis_provider=Synthesis())
        self.assertEqual(calls, ["router", "retrieval", "synthesis"])
        self.assertEqual(result.conformance_result.status, "valid")
        self.assertEqual(result.execution_result.status, "succeeded")
        self.assertEqual(result.hydrated_result.requests[0].status, "succeeded")
        self.assertEqual(type(result.packet_assembly.packet.selected_occurrences[0]).__name__, "TemporalOccurrence")

    def test_conversational_date_anchor_is_accepted_without_runtime_anchor_state(self):
        root = Path(self.directory.name) / "anchor"
        root.mkdir()
        database = root / "runtime.sqlite3"
        initialize_runtime(database)
        conversation = create_conversation(database)
        append_message(database, conversation.conversation_id, "user", "When did I go vegan?")
        append_message(database, conversation.conversation_id, "synthesis", "You went vegan on April 16, 2026.")
        append_message(database, conversation.conversation_id, "user", "What led up to that?")
        runtime_config = RuntimeConfig(ModelConfig("openai", "r", 1, "r"), ModelConfig("openai", "i", 1, "i"), PacketConfig(32), ModelConfig("openai", "s", 1, "s"))
        router = route_conversation(database, runtime_config, conversation.conversation_id, provider=type("Router", (), {"infer_router": lambda self, config, messages: ProviderInference("semantic_retrieval", '{"route":"semantic_retrieval"}', None, ProviderUsage())})())
        provider = type("Retrieval", (), {"infer_retrieval": lambda self, config, catalog_text, messages: RetrievalProviderInference((temporal("temporal.before", anchor={"domain": "date", "value": "2026-04-16"}),), "id", None, ProviderUsage())})()
        result = infer_retrieval(database, runtime_config, self.build / "capability_catalog.json", router.run_id, provider=provider)
        self.assertEqual(result.requests[0]["anchor"], {"domain": "date", "value": "2026-04-16"})


if __name__ == "__main__":
    unittest.main()
