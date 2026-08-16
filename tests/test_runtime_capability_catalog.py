import copy
import hashlib
import json
import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from semantic_traversal.runtime.capability_catalog import CapabilityCatalogError, load_capability_catalog
from semantic_traversal.runtime.config import ModelConfig, RuntimeConfig
from semantic_traversal.runtime.control_plane import RetrievalConformanceError, conform_retrieval
from semantic_traversal.runtime.conversation import append_message, create_conversation, initialize_runtime
from semantic_traversal.runtime.openai_provider import ProviderUsage, RetrievalProviderInference
from semantic_traversal.runtime.retrieval import RuntimeRetrievalError, infer_retrieval


class CatalogAdmissionTests(unittest.TestCase):
    def catalog(self):
        return {
            "catalog_schema_version": "1",
            "semantic_dimensions": [{
                "field_class": "intrinsic",
                "field_name": "parsed_text",
                "description": "parsed text",
                "value": {"shapes": [{"shape": "scalar", "domains": ["string"]}]},
                "access": [{"operator": "exact.equals", "target": "complete_value", "domains": ["string"]}],
            }],
            "graph": {
                "node_kinds": ["semantic_object", "semantic_region", "semantic_unit"],
                "discovery": [{"node_kind": "semantic_object", "dimension_name": "tag", "description": "tag", "operators": ["graph.discovery.terms"], "result": "opaque_graph_handle"}],
                "relations": [{"relation_class": "body_wikilink", "relation_name": "linked_to", "description": "body link", "source_kinds": ["semantic_unit"], "target_kinds": ["semantic_object"], "operations": ["graph.relation_occurrence_lookup"]}],
            },
            "vector": {
                "operator": "vector.semantic_similarity",
                "query": {"shape": "string", "requirement": "exactly one non-empty string", "segmentation": False, "truncation": False, "deterministic_enrichment": False},
                "targets": [{"target_kind": "semantic_unit", "input": "exact canonical parsed_text"}],
            },
            "operators": {
                "exact.equals": {"surface": "exact", "meaning": "typed exact equality"},
                "graph.discovery.terms": {"surface": "graph", "meaning": "graph discovery"},
                "graph.relation_occurrence_lookup": {"surface": "graph", "meaning": "relation lookup"},
                "vector.semantic_similarity": {"surface": "vector", "meaning": "similarity"},
            },
        }

    def malformed_catalogs(self):
        base = self.catalog()
        cases = {}
        unknown = copy.deepcopy(base)
        unknown["unknown"] = True
        cases["unknown top-level key"] = json.dumps(unknown, separators=(",", ":"))
        duplicate_top = json.dumps(base, separators=(",", ":")).replace('"catalog_schema_version":"1"', '"catalog_schema_version":"1","catalog_schema_version":"1"', 1)
        cases["duplicate top-level key"] = duplicate_top
        duplicate_nested = json.dumps(base, separators=(",", ":")).replace('"operator":"exact.equals"', '"operator":"exact.equals","operator":"exact.equals"', 1)
        cases["duplicate nested key"] = duplicate_nested
        wrong_dimensions = copy.deepcopy(base)
        wrong_dimensions["semantic_dimensions"] = {}
        cases["semantic dimensions wrong type"] = json.dumps(wrong_dimensions, separators=(",", ":"))
        malformed_dimension = copy.deepcopy(base)
        del malformed_dimension["semantic_dimensions"][0]["description"]
        cases["malformed dimension"] = json.dumps(malformed_dimension, separators=(",", ":"))
        malformed_value = copy.deepcopy(base)
        malformed_value["semantic_dimensions"][0]["value"]["shapes"][0]["extra"] = True
        cases["malformed value shape"] = json.dumps(malformed_value, separators=(",", ":"))
        malformed_access = copy.deepcopy(base)
        malformed_access["semantic_dimensions"][0]["access"][0]["extra"] = True
        cases["malformed access"] = json.dumps(malformed_access, separators=(",", ":"))
        duplicate_dimension = copy.deepcopy(base)
        duplicate_dimension["semantic_dimensions"].append(copy.deepcopy(duplicate_dimension["semantic_dimensions"][0]))
        cases["duplicate dimension identity"] = json.dumps(duplicate_dimension, separators=(",", ":"))
        duplicate_access = copy.deepcopy(base)
        duplicate_access["semantic_dimensions"][0]["access"].append(copy.deepcopy(duplicate_access["semantic_dimensions"][0]["access"][0]))
        cases["duplicate access identity"] = json.dumps(duplicate_access, separators=(",", ":"))
        malformed_graph = copy.deepcopy(base)
        del malformed_graph["graph"]["node_kinds"]
        cases["malformed graph envelope"] = json.dumps(malformed_graph, separators=(",", ":"))
        duplicate_discovery = copy.deepcopy(base)
        duplicate_discovery["graph"]["discovery"].append(copy.deepcopy(duplicate_discovery["graph"]["discovery"][0]))
        cases["duplicate discovery identity"] = json.dumps(duplicate_discovery, separators=(",", ":"))
        duplicate_relation = copy.deepcopy(base)
        duplicate_relation["graph"]["relations"].append(copy.deepcopy(duplicate_relation["graph"]["relations"][0]))
        cases["duplicate relation identity"] = json.dumps(duplicate_relation, separators=(",", ":"))
        malformed_vector = copy.deepcopy(base)
        del malformed_vector["vector"]["targets"]
        cases["malformed vector envelope"] = json.dumps(malformed_vector, separators=(",", ":"))
        duplicate_target = copy.deepcopy(base)
        duplicate_target["vector"]["targets"].append(copy.deepcopy(duplicate_target["vector"]["targets"][0]))
        cases["duplicate vector target"] = json.dumps(duplicate_target, separators=(",", ":"))
        malformed_operator = copy.deepcopy(base)
        del malformed_operator["operators"]["exact.equals"]["meaning"]
        cases["malformed operator description"] = json.dumps(malformed_operator, separators=(",", ":"))
        unsupported_domain = copy.deepcopy(base)
        unsupported_domain["semantic_dimensions"][0]["value"]["shapes"][0]["domains"] = ["unsupported"]
        cases["unsupported value domain"] = json.dumps(unsupported_domain, separators=(",", ":"))
        empty_shapes = copy.deepcopy(base)
        empty_shapes["semantic_dimensions"][0]["value"]["shapes"] = []
        cases["empty value shapes"] = json.dumps(empty_shapes, separators=(",", ":"))
        unsupported_access = copy.deepcopy(base)
        unsupported_access["semantic_dimensions"][0]["access"][0]["operator"] = "exact.contains"
        cases["unsupported access operator"] = json.dumps(unsupported_access, separators=(",", ":"))
        illegal_access = copy.deepcopy(base)
        illegal_access["semantic_dimensions"][0]["access"][0] = {"operator": "lexical.terms", "target": "complete_value", "operand": {"shape": "ordered_sequence", "member_domains": ["string"]}}
        cases["illegal access operand"] = json.dumps(illegal_access, separators=(",", ":"))
        inconsistent_access = copy.deepcopy(base)
        inconsistent_access["semantic_dimensions"][0]["access"][0]["domains"] = ["integer"]
        cases["value access inconsistency"] = json.dumps(inconsistent_access, separators=(",", ":"))
        wrong_discovery_result = copy.deepcopy(base)
        wrong_discovery_result["graph"]["discovery"][0]["result"] = "canonical_uuid"
        cases["wrong graph discovery result"] = json.dumps(wrong_discovery_result, separators=(",", ":"))
        unsupported_discovery_operator = copy.deepcopy(base)
        unsupported_discovery_operator["graph"]["discovery"][0]["operators"] = ["graph.discovery.other"]
        cases["unsupported graph discovery operator"] = json.dumps(unsupported_discovery_operator, separators=(",", ":"))
        unsupported_relation_operation = copy.deepcopy(base)
        unsupported_relation_operation["graph"]["relations"][0]["operations"] = ["graph.relation_other"]
        cases["unsupported graph relation operation"] = json.dumps(unsupported_relation_operation, separators=(",", ":"))
        missing_discovery_node = copy.deepcopy(base)
        missing_discovery_node["graph"]["discovery"][0]["node_kind"] = "scope"
        cases["discovery node absent"] = json.dumps(missing_discovery_node, separators=(",", ":"))
        missing_relation_endpoint = copy.deepcopy(base)
        missing_relation_endpoint["graph"]["relations"][0]["target_kinds"] = ["scope"]
        cases["relation endpoint absent"] = json.dumps(missing_relation_endpoint, separators=(",", ":"))
        wrong_vector_shape = copy.deepcopy(base)
        wrong_vector_shape["vector"]["query"]["shape"] = "integer"
        cases["wrong vector query shape"] = json.dumps(wrong_vector_shape, separators=(",", ":"))
        vector_segmentation = copy.deepcopy(base)
        vector_segmentation["vector"]["query"]["segmentation"] = True
        cases["vector segmentation enabled"] = json.dumps(vector_segmentation, separators=(",", ":"))
        vector_truncation = copy.deepcopy(base)
        vector_truncation["vector"]["query"]["truncation"] = True
        cases["vector truncation enabled"] = json.dumps(vector_truncation, separators=(",", ":"))
        vector_enrichment = copy.deepcopy(base)
        vector_enrichment["vector"]["query"]["deterministic_enrichment"] = True
        cases["vector enrichment enabled"] = json.dumps(vector_enrichment, separators=(",", ":"))
        wrong_vector_target = copy.deepcopy(base)
        wrong_vector_target["vector"]["targets"][0]["input"] = "canonical authored object name for a zero-unit object"
        cases["wrong vector target input"] = json.dumps(wrong_vector_target, separators=(",", ":"))
        unsupported_global_operator = copy.deepcopy(base)
        unsupported_global_operator["operators"]["unsupported.operator"] = {"surface": "unsupported", "meaning": "unsupported"}
        cases["unsupported global operator"] = json.dumps(unsupported_global_operator, separators=(",", ":"))
        surface_mismatch = copy.deepcopy(base)
        surface_mismatch["operators"]["exact.equals"]["surface"] = "lexical"
        cases["operator surface mismatch"] = json.dumps(surface_mismatch, separators=(",", ":"))
        missing_referenced_operator = copy.deepcopy(base)
        del missing_referenced_operator["operators"]["graph.relation_occurrence_lookup"]
        cases["referenced operator absent"] = json.dumps(missing_referenced_operator, separators=(",", ":"))
        return cases
    def _write(self, directory, text):
        path = Path(directory) / "capability_catalog.json"
        path.write_text(text, encoding="utf-8")
        return path

    def _runtime(self, directory, catalog_path, catalog_text, *, empty_proposal=False, include_retrieval=False):
        database = Path(directory) / "runtime.sqlite3"
        initialize_runtime(database)
        conversation = create_conversation(database)
        append_message(database, conversation.conversation_id, "user", "question")
        digest = "sha256:" + hashlib.sha256(catalog_text.encode("utf-8")).hexdigest()
        connection = sqlite3.connect(database)
        connection.execute(
            "INSERT INTO model_runs (run_id, conversation_id, trigger_message_id, run_kind, provider, model, prompt_version, status, started_at, output_json) VALUES ('router', ?, 1, 'router', 'openai', 'model', 'prompt', 'succeeded', 'started', '{\"route\":\"semantic_retrieval\"}')",
            (conversation.conversation_id,),
        )
        if include_retrieval:
            proposal = '{"requests":[]}' if empty_proposal else '{"requests":[{"operator":"exact.equals","field_class":"intrinsic","field_name":"parsed_text","target":"complete_value","operand":{"shape":"scalar","domain":"string","value":"x"}}]}'
            connection.execute(
                "INSERT INTO model_runs (run_id, conversation_id, trigger_message_id, run_kind, parent_run_id, capability_catalog_sha256, provider, model, prompt_version, status, started_at, output_json) VALUES ('retrieval', ?, 1, 'retrieval_inference', 'router', ?, 'openai', 'model', 'prompt', 'succeeded', 'started', ?)",
                (conversation.conversation_id, digest, proposal),
            )
        connection.commit()
        connection.close()
        return database, conversation

    def test_repository_schema_fixture_is_admitted_byte_for_byte(self):
        with TemporaryDirectory() as directory:
            text = json.dumps(self.catalog(), separators=(",", ":"))
            path = self._write(directory, text)
            artifact = load_capability_catalog(path)
            self.assertEqual(artifact.sha256, "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest())
            self.assertEqual(artifact.text, text)

    def test_malformed_catalogs_fail_at_shared_admission(self):
        for label, text in self.malformed_catalogs().items():
            with self.subTest(label=label), TemporaryDirectory() as directory:
                with self.assertRaises(CapabilityCatalogError):
                    load_capability_catalog(self._write(directory, text))

    def test_malformed_catalogs_fail_before_retrieval_provider_and_run_insert(self):
        config = RuntimeConfig(ModelConfig("openai", "router", 1.0, "router"), ModelConfig("openai", "retrieval", 1.0, "retrieval"))

        class Provider:
            def __init__(self):
                self.calls = 0

            def infer_retrieval(self, config, catalog_text, messages):
                self.calls += 1
                return RetrievalProviderInference((), "ignored", None, ProviderUsage())

        for label, text in self.malformed_catalogs().items():
            with self.subTest(label=label), TemporaryDirectory() as directory:
                catalog_path = self._write(directory, text)
                database, conversation = self._runtime(directory, catalog_path, text)
                provider = Provider()
                with self.assertRaises(RuntimeRetrievalError):
                    infer_retrieval(database, config, catalog_path, "router", provider=provider)
                self.assertEqual(provider.calls, 0)
                connection = sqlite3.connect(database)
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM model_runs WHERE run_kind='retrieval_inference'").fetchone()[0], 0)
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM messages WHERE conversation_id=?", (conversation.conversation_id,)).fetchone()[0], 1)
                connection.close()

    def test_malformed_catalogs_fail_before_empty_proposal_conformance(self):
        for label, text in self.malformed_catalogs().items():
            with self.subTest(label=label), TemporaryDirectory() as directory:
                catalog_path = self._write(directory, text)
                database, _ = self._runtime(directory, catalog_path, text, empty_proposal=True, include_retrieval=True)
                with self.assertRaises(RetrievalConformanceError):
                    conform_retrieval(database, catalog_path, "retrieval")
                connection = sqlite3.connect(database)
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM retrieval_conformance").fetchone()[0], 0)
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 1)
                connection.close()

    def test_empty_proposal_with_valid_catalog_remains_valid(self):
        with TemporaryDirectory() as directory:
            text = json.dumps(self.catalog(), separators=(",", ":"))
            catalog_path = self._write(directory, text)
            database, _ = self._runtime(directory, catalog_path, text, empty_proposal=True, include_retrieval=True)
            result = conform_retrieval(database, catalog_path, "retrieval")
            self.assertEqual(result.status, "valid")
            self.assertEqual(result.requests, ())


if __name__ == "__main__":
    unittest.main()