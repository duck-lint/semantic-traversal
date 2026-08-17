import json
import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from semantic_traversal.cli import main
from semantic_traversal import hydrate_object, hydrate_region, hydrate_unit
from semantic_traversal.runtime.retrieval.control_plane import conform_retrieval
from semantic_traversal.runtime.conversation import (
    append_message,
    create_conversation,
    initialize_runtime,
)
from semantic_traversal.runtime.retrieval.execution import execute_retrieval
from semantic_traversal.runtime.retrieval.hydration import (
    HydratedExactResult,
    HydratedGraphDiscoveryResult,
    HydratedGraphRelationResult,
    HydratedLexicalResult,
    HydratedRetrievalResult,
    HydratedVectorResult,
    RetrievalHydrationError,
    hydrate_retrieval_execution,
)
from semantic_traversal.runtime.retrieval.package import (
    RetrievalPackageError,
    load_retrieval_package,
)
from tests.test_cli import CliProvider


class RuntimeRetrievalHydrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tests.test_retrieval_package_verification import RetrievalPackageVerificationTests

        cls.fixture = RetrievalPackageVerificationTests
        cls.fixture.setUpClass()
        cls.build = cls.fixture.build_a
        cls.other_build = cls.fixture.build_b
        cls.root = Path(cls.fixture.directory.name) / "hydration-tests"
        cls.root.mkdir()
        cls.region_build = cls._build_region_package(cls.root / "region-package")

    @classmethod
    def tearDownClass(cls):
        cls.fixture.tearDownClass()

    @staticmethod
    def _build_region_package(root):
        vault = root / "vault"
        vault.mkdir(parents=True)
        (vault / "Target.md").write_text(
            "---\nuuid: target\n---\n# Inner Target\ntarget body\n",
            encoding="utf-8",
        )
        (vault / "Zero.md").write_text(
            "---\nuuid: zero\ntags: [empty]\n---\n",
            encoding="utf-8",
        )
        (vault / "Source.md").write_text(
            "---\nuuid: source\nrelation: \"[[Target]]\"\n---\n"
            "# Outer\nouter source [[Target]]\n## Inner\ninner source\n",
            encoding="utf-8",
        )
        config = root / "config.yaml"
        config.write_text(
            "vault_name: test\nuuid_field: uuid\nexcluded_folders: []\nsemantic_identifiers:\n"
            "  tags:\n    description: test-authored declaration\n"
            "  relation:\n    description: test-authored declaration\n",
            encoding="utf-8",
        )
        output = root / "completed"
        with patch("semantic_traversal.cli.OllamaEmbeddingProvider", CliProvider):
            code = main([
                "build", "--vault", str(vault), "--config", str(config),
                "--output", str(output),
            ])
        if code != 0:
            raise AssertionError("region hydration fixture build failed")
        catalog = output / "capability_catalog.json"
        code = main([
            "catalog", "generate", "--build", str(output), "--config", str(config),
            "--output", str(catalog), "--json",
        ])
        if code != 0:
            raise AssertionError("region hydration fixture catalog failed")
        return output

    def prepared(self, name, requests, build=None):
        root = self.root / name
        root.mkdir()
        database = root / "runtime.sqlite3"
        initialize_runtime(database)
        conversation = create_conversation(database)
        message = append_message(database, conversation.conversation_id, "user", "question")
        package = load_retrieval_package(build or self.build)
        output = json.dumps({"requests": requests}, ensure_ascii=False, separators=(",", ":"))
        connection = sqlite3.connect(database)
        connection.execute(
            "INSERT INTO model_runs (run_id, conversation_id, trigger_message_id, run_kind, provider, model, "
            "prompt_version, status, started_at, output_json) VALUES (?, ?, ?, 'router', 'p', 'm', 'p', 'succeeded', 't', ?)",
            (f"router-{name}", conversation.conversation_id, message.message_id, '{"route":"semantic_retrieval"}'),
        )
        connection.execute(
            "INSERT INTO model_runs (run_id, conversation_id, trigger_message_id, run_kind, parent_run_id, "
            "capability_catalog_sha256, provider, model, prompt_version, status, started_at, output_json) "
            "VALUES (?, ?, ?, 'retrieval_inference', ?, ?, 'p', 'm', 'p', 'succeeded', 't', ?)",
            (
                f"retrieval-{name}",
                conversation.conversation_id,
                message.message_id,
                f"router-{name}",
                package.identity.capability_catalog_sha256,
                output,
            ),
        )
        connection.commit()
        connection.close()
        conformance = conform_retrieval(database, (build or self.build) / "capability_catalog.json", f"retrieval-{name}")
        return database, package, conformance.conformance_id

    @staticmethod
    def exact(value="missing"):
        return {
            "operator": "exact.equals",
            "field_class": "intrinsic",
            "field_name": "parsed_text",
            "target": "complete_value",
            "operand": {"shape": "scalar", "domain": "string", "value": value},
        }

    @staticmethod
    def lexical():
        return {
            "operator": "lexical.terms",
            "field_class": "intrinsic",
            "field_name": "parsed_text",
            "target": "complete_value",
            "operand": ["source"],
        }

    def identities(self, package):
        connection = sqlite3.connect(package.substrate_path)
        try:
            unit_id, source_uuid = connection.execute(
                "SELECT unit_id, source_object_uuid FROM canonical_units ORDER BY unit_id LIMIT 1"
            ).fetchone()
            region = connection.execute(
                "SELECT source_object_uuid, region_path_json FROM canonical_regions ORDER BY canonical_ordinal LIMIT 1"
            ).fetchone()
            if region is None:
                return unit_id, source_uuid, None, None
            region_uuid, region_json = region
            return unit_id, source_uuid, region_uuid, tuple(json.loads(region_json))
        finally:
            connection.close()

    @staticmethod
    def rewrite_results(database, results, *, execution_status="succeeded", execution_failure=None):
        connection = sqlite3.connect(database)
        row = connection.execute(
            "SELECT result_json FROM retrieval_executions"
        ).fetchone()
        payload = json.loads(row[0])
        for item, result in zip(payload["requests"], results):
            if result is not None:
                item["status"] = "succeeded"
                item["result"] = result
                item["failure"] = None
        payload["execution_failure"] = execution_failure
        connection.execute(
            "UPDATE retrieval_executions SET status = ?, completed_at = 'done', result_json = ?",
            (execution_status, json.dumps(payload, separators=(",", ":"))),
        )
        connection.commit()
        execution_id = connection.execute(
            "SELECT execution_id FROM retrieval_executions"
        ).fetchone()[0]
        connection.close()
        return execution_id

    def test_exact_lexical_order_scores_and_duplicates_remain_occurrences(self):
        database, package, conformance_id = self.prepared(
            "exact-lexical",
            [self.exact(), self.lexical(), self.exact("second")],
        )
        execution = execute_retrieval(database, package, conformance_id)
        unit_id, _, _, _ = self.identities(package)
        execution_id = self.rewrite_results(
            database,
            [
                {"kind": "exact", "unit_ids": [unit_id, unit_id]},
                {"kind": "lexical", "hits": [{"unit_id": unit_id, "score": -1.25}]},
                {"kind": "exact", "unit_ids": [unit_id]},
            ],
        )
        hydrated = hydrate_retrieval_execution(database, package, execution_id)
        substrate = sqlite3.connect(package.substrate_path)
        expected_unit = hydrate_unit(substrate, unit_id)
        expected_object = hydrate_object(substrate, expected_unit.source_object_uuid)
        substrate.close()
        self.assertEqual(hydrated.execution_id, execution.execution_id)
        self.assertEqual([item.ordinal for item in hydrated.requests], [0, 1, 2])
        self.assertIsInstance(hydrated.requests[0].result, HydratedExactResult)
        self.assertEqual(len(hydrated.requests[0].result.hits), 2)
        self.assertEqual(hydrated.requests[0].result.hits[0], hydrated.requests[0].result.hits[1])
        self.assertEqual(hydrated.requests[0].result.hits[0].target.canonical_unit, expected_unit)
        self.assertEqual(hydrated.requests[0].result.hits[0].target.owning_object, expected_object)
        self.assertIsInstance(hydrated.requests[1].result, HydratedLexicalResult)
        self.assertEqual(hydrated.requests[1].result.hits[0].score, -1.25)
        self.assertEqual(hydrated.requests[0].request["operand"]["value"], "missing")
        self.assertEqual(hydrated.requests[2].request["operand"]["value"], "second")

    def test_zero_hit_and_empty_execution_hydrate_successfully(self):
        database, package, conformance_id = self.prepared("zero-hit", [self.exact()])
        execution = execute_retrieval(database, package, conformance_id)
        hydrated = hydrate_retrieval_execution(database, package, execution.execution_id)
        self.assertEqual(hydrated.status, "succeeded")
        self.assertIsInstance(hydrated.requests[0].result, HydratedExactResult)
        self.assertEqual(hydrated.requests[0].result.hits, ())

        empty_database, empty_package, empty_conformance = self.prepared("empty", [])
        empty_execution = execute_retrieval(empty_database, empty_package, empty_conformance)
        empty = hydrate_retrieval_execution(empty_database, empty_package, empty_execution.execution_id)
        self.assertEqual(empty.requests, ())

    def test_vector_object_and_unit_hydration_preserve_native_provenance(self):
        request = {"operator": "vector.semantic_similarity", "query": "source"}
        database, package, conformance_id = self.prepared("vector", [request])
        execution = execute_retrieval(database, package, conformance_id, vector_provider=CliProvider())
        unit_id, source_uuid, _, _ = self.identities(package)
        execution_id = self.rewrite_results(
            database,
            [{
                "kind": "vector",
                "hits": [
                    {
                        "target_kind": "semantic_unit",
                        "target_identity": unit_id,
                        "score": 0.25,
                        "segment_ordinal": 3,
                    },
                    {
                        "target_kind": "semantic_object",
                        "target_identity": "zero",
                        "score": 0.5,
                        "segment_ordinal": 0,
                    },
                ],
            }],
        )
        hydrated = hydrate_retrieval_execution(database, package, execution_id)
        result = hydrated.requests[0].result
        self.assertIsInstance(result, HydratedVectorResult)
        self.assertEqual(result.hits[0].segment_ordinal, 3)
        self.assertEqual(result.hits[0].target.canonical_unit.source_object_uuid, source_uuid)
        self.assertTrue(result.hits[0].target.canonical_unit.parsed_text)
        self.assertEqual(result.hits[1].target_kind, "semantic_object")
        self.assertEqual(result.hits[1].target.canonical_object.source_object_uuid, "zero")
        self.assertEqual(result.hits[1].target.canonical_object.regions, ())

    def test_graph_discovery_region_scope_and_relation_endpoints_hydrate_without_traversal(self):
        requests = [
            {"operator": "graph.discovery.terms", "node_kind": "semantic_object", "dimension_name": "address_text", "operand": ["target"]},
            {"operator": "graph.discovery.terms", "node_kind": "semantic_region", "dimension_name": "address_text", "operand": ["inner"]},
            {"operator": "graph.relation_occurrence_lookup", "relation_class": "body_wikilink", "relation_name": "linked_to"},
        ]
        database, package, conformance_id = self.prepared("graph", requests, self.region_build)
        execution = execute_retrieval(database, package, conformance_id)
        unit_id, source_uuid, region_uuid, region_path = self.identities(package)
        execution_id = self.rewrite_results(
            database,
            [
                {
                    "kind": "graph_discovery",
                    "hits": [
                        {"node": {"node_kind": "semantic_object", "identity": [source_uuid]}, "score": 1.0},
                    ],
                },
                {
                    "kind": "graph_discovery",
                    "hits": [
                        {"node": {"node_kind": "semantic_region", "identity": [region_uuid, list(region_path)]}, "score": 3.0},
                    ],
                },
                {
                    "kind": "graph_relation_occurrences",
                    "occurrences": [
                        {
                            "edge_id": 41,
                            "relation_class": "body_wikilink",
                            "relation_name": "linked_to",
                            "source": {"node_kind": "semantic_unit", "identity": [unit_id]},
                            "target": {"node_kind": "semantic_object", "identity": ["zero"]},
                        },
                        {
                            "edge_id": 42,
                            "relation_class": "body_wikilink",
                            "relation_name": "linked_to",
                            "source": {"node_kind": "scope", "identity": [["A"]]},
                            "target": {"node_kind": "semantic_object", "identity": ["zero"]},
                        },
                        {
                            "edge_id": 41,
                            "relation_class": "body_wikilink",
                            "relation_name": "linked_to",
                            "source": {"node_kind": "semantic_unit", "identity": [unit_id]},
                            "target": {"node_kind": "semantic_object", "identity": ["zero"]},
                        },
                    ],
                },
            ],
        )
        hydrated = hydrate_retrieval_execution(database, package, execution_id)
        discovery = hydrated.requests[0].result
        self.assertIsInstance(discovery, HydratedGraphDiscoveryResult)
        self.assertEqual(discovery.hits[0].target.canonical_object.source_object_uuid, source_uuid)
        substrate = sqlite3.connect(package.substrate_path)
        expected_region = hydrate_region(substrate, region_uuid, region_path)
        expected_region_owner = hydrate_object(substrate, region_uuid)
        substrate.close()
        region = hydrated.requests[1].result.hits[0].target
        self.assertEqual(region.canonical_region, expected_region)
        self.assertEqual(region.owning_object, expected_region_owner)
        self.assertEqual(region.canonical_region.reference.region_path, region_path)
        self.assertEqual(region.owning_object.source_object_uuid, region_uuid)
        relation = hydrated.requests[2].result
        self.assertIsInstance(relation, HydratedGraphRelationResult)
        self.assertEqual(len(relation.occurrences), 3)
        self.assertEqual(relation.occurrences[0].edge_id, relation.occurrences[2].edge_id)
        self.assertEqual(relation.occurrences[0].source.canonical_unit.unit_id, unit_id)
        self.assertEqual(relation.occurrences[0].target.canonical_object.source_object_uuid, "zero")
        self.assertEqual(relation.occurrences[1].source.graph_handle.node_kind, "scope")
        self.assertIsNone(relation.occurrences[1].source.canonical_object)

    def test_graph_discovery_result_node_kind_mismatch_fails_without_retrieval(self):
        requests = [{
            "operator": "graph.discovery.terms",
            "node_kind": "semantic_object",
            "dimension_name": "address_text",
            "operand": ["target"],
        }]
        database, package, conformance_id = self.prepared("discovery-mismatch", requests, self.region_build)
        execution = execute_retrieval(database, package, conformance_id)
        _, _, region_uuid, region_path = self.identities(package)
        execution_id = self.rewrite_results(
            database,
            [{
                "kind": "graph_discovery",
                "hits": [{
                    "node": {"node_kind": "semantic_region", "identity": [region_uuid, list(region_path)]},
                    "score": 1.0,
                }],
            }],
        )
        execution_module = __import__("semantic_traversal.runtime.retrieval.execution", fromlist=["graph_discover"])
        with patch.object(execution_module, "graph_discover") as graph_discover:
            with self.assertRaises(RetrievalHydrationError):
                hydrate_retrieval_execution(database, package, execution_id or execution.execution_id)
            graph_discover.assert_not_called()

    def test_graph_relation_result_identity_mismatch_fails_without_retrieval(self):
        requests = [{
            "operator": "graph.relation_occurrence_lookup",
            "relation_class": "body_wikilink",
            "relation_name": "linked_to",
        }]
        database, package, conformance_id = self.prepared("relation-mismatch", requests)
        unit_id, _, _, _ = self.identities(package)
        execution = execute_retrieval(database, package, conformance_id)
        execution_id = self.rewrite_results(
            database,
            [{
                "kind": "graph_relation_occurrences",
                "occurrences": [{
                    "edge_id": 1,
                    "relation_class": "structural",
                    "relation_name": "contains_unit",
                    "source": {"node_kind": "semantic_unit", "identity": [unit_id]},
                    "target": {"node_kind": "semantic_object", "identity": ["zero"]},
                }],
            }],
        )
        execution_module = __import__("semantic_traversal.runtime.retrieval.execution", fromlist=["graph_relation_lookup"])
        with patch.object(execution_module, "graph_relation_lookup") as graph_relation_lookup:
            with self.assertRaises(RetrievalHydrationError):
                hydrate_retrieval_execution(database, package, execution_id or execution.execution_id)
            graph_relation_lookup.assert_not_called()

    def test_failed_execution_hydrates_prior_success_and_retains_failures(self):
        requests = [self.exact(), self.lexical()]
        database, package, conformance_id = self.prepared("failed", requests)
        module = __import__("semantic_traversal.runtime.retrieval.execution", fromlist=["lexical_lookup"])
        with patch.object(module, "lexical_lookup", side_effect=ValueError("lexical failure")):
            execution = execute_retrieval(database, package, conformance_id)
        unit_id, _, _, _ = self.identities(package)
        connection = sqlite3.connect(database)
        payload = json.loads(connection.execute("SELECT result_json FROM retrieval_executions").fetchone()[0])
        payload["requests"][0]["result"] = {"kind": "exact", "unit_ids": [unit_id]}
        connection.execute(
            "UPDATE retrieval_executions SET result_json = ?",
            (json.dumps(payload, separators=(",", ":")),),
        )
        connection.commit()
        connection.close()
        hydrated = hydrate_retrieval_execution(database, package, execution.execution_id)
        self.assertEqual(hydrated.status, "failed")
        self.assertIsInstance(hydrated.requests[0].result, HydratedExactResult)
        self.assertEqual(hydrated.requests[1].status, "failed")
        self.assertIsNone(hydrated.requests[1].result)
        self.assertEqual(hydrated.requests[1].failure["kind"], "surface_error")

    def test_malformed_target_identity_fails_closed(self):
        database, package, conformance_id = self.prepared("unknown-unit", [self.exact()])
        execution = execute_retrieval(database, package, conformance_id)
        connection = sqlite3.connect(database)
        payload = json.loads(connection.execute("SELECT result_json FROM retrieval_executions").fetchone()[0])
        payload["requests"][0]["result"] = {"kind": "exact", "unit_ids": [999999]}
        connection.execute(
            "UPDATE retrieval_executions SET result_json = ?",
            (json.dumps(payload, separators=(",", ":")),),
        )
        connection.commit()
        connection.close()
        with self.assertRaises(RetrievalHydrationError):
            hydrate_retrieval_execution(database, package, execution.execution_id)

    def test_package_lineage_and_mutation_fail_before_or_during_hydration(self):
        database, package, conformance_id = self.prepared("lineage", [self.exact()])
        execution = execute_retrieval(database, package, conformance_id)
        other = load_retrieval_package(self.other_build)
        with self.assertRaises(RetrievalHydrationError):
            hydrate_retrieval_execution(database, other, execution.execution_id)

        module = __import__("semantic_traversal.runtime.retrieval.hydration", fromlist=["hydrate_unit"])
        with patch.object(module, "require_current_package_identity", side_effect=[
            None,
            None,
            RetrievalPackageError("package changed during hydration"),
        ]):
            with self.assertRaises(RetrievalHydrationError):
                hydrate_retrieval_execution(database, package, execution.execution_id)

    def test_hydrated_outer_evidence_is_immutable(self):
        database, package, conformance_id = self.prepared("immutable", [self.exact()])
        execution = execute_retrieval(database, package, conformance_id)
        hydrated = hydrate_retrieval_execution(database, package, execution.execution_id)
        with self.assertRaises(AttributeError):
            hydrated.status = "failed"
        with self.assertRaises(TypeError):
            hydrated.requests[0].request["operator"] = "changed"
        self.assertIsInstance(hydrated, HydratedRetrievalResult)


if __name__ == "__main__":
    unittest.main()
