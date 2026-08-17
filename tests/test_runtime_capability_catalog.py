import copy
import hashlib
import json
import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from tests.catalog_fixtures import minimum_catalog
from semantic_traversal.runtime.retrieval.capability_catalog import CapabilityCatalogError, load_capability_catalog
from semantic_traversal.runtime.config import ModelConfig, PacketConfig, RuntimeConfig
from semantic_traversal.runtime.retrieval.control_plane import RetrievalConformanceError, conform_retrieval
from semantic_traversal.runtime.conversation import append_message, create_conversation, initialize_runtime
from semantic_traversal.runtime.openai_provider import ProviderUsage, RetrievalProviderInference
from semantic_traversal.runtime.retrieval.inference import RuntimeRetrievalError, infer_retrieval


class CatalogAdmissionTests(unittest.TestCase):
    def catalog(self):
        return minimum_catalog(semantic_unit_target=True)

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
        unknown_class = copy.deepcopy(base)
        unknown_class["semantic_dimensions"][0]["field_class"] = "unknown"
        cases["unknown field class"] = json.dumps(unknown_class, separators=(",", ":"))
        for field_class, label in (("intrinsic", "intrinsic invented field"), ("region", "region invented field"), ("semantic_path", "semantic path invented field")):
            invented = copy.deepcopy(base)
            invented["semantic_dimensions"][0]["field_class"] = field_class
            invented["semantic_dimensions"][0]["field_name"] = "invented_field"
            cases[label] = json.dumps(invented, separators=(",", ":"))
        semantic_ordered = copy.deepcopy(base)
        semantic_ordered["semantic_dimensions"][0]["field_class"] = "semantic_identifier"
        semantic_ordered["semantic_dimensions"][0]["field_name"] = "authored"
        semantic_ordered["semantic_dimensions"][0]["value"] = {"shapes": [{"shape": "ordered_sequence", "domains": ["string"]}]}
        cases["semantic identifier ordered sequence"] = json.dumps(semantic_ordered, separators=(",", ":"))
        fixed_sequence = copy.deepcopy(base)
        fixed_sequence["semantic_dimensions"][0]["value"] = {"shapes": [{"shape": "sequence", "member_domains": ["string"]}]}
        cases["fixed field sequence shape"] = json.dumps(fixed_sequence, separators=(",", ":"))
        fixed_ordered = copy.deepcopy(base)
        fixed_ordered["semantic_dimensions"][0]["value"] = {"shapes": [{"shape": "ordered_sequence", "domains": ["string"]}]}
        cases["fixed scalar ordered shape"] = json.dumps(fixed_ordered, separators=(",", ":"))
        semantic_vector = copy.deepcopy(base)
        semantic_vector["semantic_dimensions"].append({"field_class": "semantic_identifier", "field_name": "title", "description": "title", "value": {"shapes": [{"shape": "scalar", "domains": ["string"]}]}, "access": [{"operator": "vector.semantic_similarity", "target": "complete_value", "domains": ["string"]}]})
        cases["vector access semantic identifier"] = json.dumps(semantic_vector, separators=(",", ":"))
        region_vector = copy.deepcopy(base)
        region_vector["semantic_dimensions"].append({"field_class": "region", "field_name": "region_text", "description": "region text", "value": {"shapes": [{"shape": "scalar", "domains": ["string"]}]}, "access": [{"operator": "vector.semantic_similarity", "target": "complete_value", "domains": ["string"]}]})
        cases["vector access region text"] = json.dumps(region_vector, separators=(",", ":"))
        unsupported_discovery_identity = copy.deepcopy(base)
        unsupported_discovery_identity["graph"]["discovery"][0]["dimension_name"] = "arbitrary"
        cases["unsupported discovery identity"] = json.dumps(unsupported_discovery_identity, separators=(",", ":"))
        missing_discovery_operation = copy.deepcopy(base)
        missing_discovery_operation["graph"]["discovery"][0]["operators"] = ["graph.discovery.terms"]
        cases["missing discovery operation"] = json.dumps(missing_discovery_operation, separators=(",", ":"))
        unknown_relation_class = copy.deepcopy(base)
        unknown_relation_class["graph"]["relations"][0]["relation_class"] = "unknown"
        cases["unknown relation class"] = json.dumps(unknown_relation_class, separators=(",", ":"))
        invented_body_relation = copy.deepcopy(base)
        invented_body_relation["graph"]["relations"][0]["relation_name"] = "invented_relation"
        cases["invented body relation"] = json.dumps(invented_body_relation, separators=(",", ":"))
        invented_structural_relation = copy.deepcopy(base)
        invented_structural_relation["graph"]["relations"][0]["relation_class"] = "structural"
        invented_structural_relation["graph"]["relations"][0]["relation_name"] = "invented_relation"
        cases["invented structural relation"] = json.dumps(invented_structural_relation, separators=(",", ":"))
        tags_relation = copy.deepcopy(base)
        tags_relation["graph"]["relations"][0]["relation_class"] = "semantic_identifier"
        tags_relation["graph"]["relations"][0]["relation_name"] = "tags"
        cases["semantic identifier tags relation"] = json.dumps(tags_relation, separators=(",", ":"))
        wrong_body_source = copy.deepcopy(base)
        wrong_body_source["graph"]["relations"][0]["source_kinds"] = ["semantic_object"]
        cases["wrong body relation source"] = json.dumps(wrong_body_source, separators=(",", ":"))
        wrong_structural_target = copy.deepcopy(base)
        wrong_structural_target["graph"]["relations"][0]["relation_class"] = "structural"
        wrong_structural_target["graph"]["relations"][0]["relation_name"] = "contains_scope"
        wrong_structural_target["graph"]["relations"][0]["source_kinds"] = ["scope"]
        wrong_structural_target["graph"]["relations"][0]["target_kinds"] = ["semantic_object"]
        cases["wrong structural relation target"] = json.dumps(wrong_structural_target, separators=(",", ":"))
        wrong_semantic_identifier_endpoints = copy.deepcopy(base)
        wrong_semantic_identifier_endpoints["graph"]["relations"][0]["relation_class"] = "semantic_identifier"
        wrong_semantic_identifier_endpoints["graph"]["relations"][0]["relation_name"] = "arbitrary_relation"
        wrong_semantic_identifier_endpoints["graph"]["relations"][0]["source_kinds"] = ["semantic_unit"]
        cases["wrong semantic identifier endpoints"] = json.dumps(wrong_semantic_identifier_endpoints, separators=(",", ":"))
        missing_relation_operation = copy.deepcopy(base)
        missing_relation_operation["graph"]["relations"][0]["operations"] = ["graph.relation_occurrence_lookup", "graph.inbound_traversal"]
        cases["missing relation operation"] = json.dumps(missing_relation_operation, separators=(",", ":"))
        missing_global_operator = copy.deepcopy(base)
        del missing_global_operator["operators"]["graph.outbound_traversal"]
        cases["missing global operator"] = json.dumps(missing_global_operator, separators=(",", ":"))
        for identity in (
            ("intrinsic", "raw_markdown"),
            ("intrinsic", "parsed_text"),
            ("region", "region_path"),
            ("semantic_path", "path_hierarchy"),
            ("semantic_path", "path_component"),
        ):
            missing_dimension = copy.deepcopy(base)
            missing_dimension["semantic_dimensions"] = [
                item for item in missing_dimension["semantic_dimensions"]
                if (item["field_class"], item["field_name"]) != identity
            ]
            cases[f"constitutive dimension missing: {identity}"] = json.dumps(missing_dimension, separators=(",", ":"))
        missing_node = copy.deepcopy(base)
        missing_node["graph"]["node_kinds"].remove("scope")
        cases["constitutive graph node missing"] = json.dumps(missing_node, separators=(",", ":"))
        missing_relation = copy.deepcopy(base)
        missing_relation["graph"]["relations"] = missing_relation["graph"]["relations"][1:]
        cases["constitutive graph relation missing"] = json.dumps(missing_relation, separators=(",", ":"))
        missing_discovery = copy.deepcopy(base)
        missing_discovery["graph"]["discovery"] = missing_discovery["graph"]["discovery"][1:]
        cases["constitutive graph discovery missing"] = json.dumps(missing_discovery, separators=(",", ":"))
        missing_vector_access = copy.deepcopy(base)
        parsed_text = next(item for item in missing_vector_access["semantic_dimensions"] if item["field_name"] == "parsed_text")
        parsed_text["access"] = [access for access in parsed_text["access"] if access["operator"] != "vector.semantic_similarity"]
        cases["constitutive vector coupling missing access"] = json.dumps(missing_vector_access, separators=(",", ":"))
        missing_vector_target = copy.deepcopy(base)
        missing_vector_target["vector"]["targets"] = []
        cases["constitutive vector coupling missing target"] = json.dumps(missing_vector_target, separators=(",", ":"))
        malformed_scalar = copy.deepcopy(base)
        malformed_scalar["semantic_dimensions"].append({
            "field_class": "semantic_identifier",
            "field_name": "malformed_scalar",
            "description": "malformed scalar",
            "value": {"shapes": [{"shape": "scalar", "domains": ["string"]}]},
            "access": [
                {"operator": "exact.equals", "target": "complete_value", "domains": ["string"]},
                {"operator": "lexical.phrase", "target": "complete_value", "domains": ["string"]},
            ],
        })
        cases["semantic identifier scalar lexical access incomplete"] = json.dumps(malformed_scalar, separators=(",", ":"))
        malformed_sequence = copy.deepcopy(base)
        malformed_sequence["semantic_dimensions"].append({
            "field_class": "semantic_identifier",
            "field_name": "malformed_sequence",
            "description": "malformed sequence",
            "value": {"shapes": [{"shape": "sequence", "member_domains": ["string"]}]},
            "access": [
                {"operator": "lexical.terms", "target": "member", "domains": ["string"]},
                {"operator": "lexical.phrase", "target": "member", "domains": ["string"]},
            ],
        })
        cases["semantic identifier sequence exact access missing"] = json.dumps(malformed_sequence, separators=(",", ":"))
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

    def test_semantic_identifier_access_is_derived_from_value_models(self):
        cases = [
            (
                "arbitrary_date",
                {"shapes": [{"shape": "scalar", "domains": ["date"]}]},
                [{"operator": "exact.equals", "target": "complete_value", "domains": ["date"]}],
            ),
            (
                "arbitrary_string",
                {"shapes": [{"shape": "scalar", "domains": ["string"]}]},
                [
                    {"operator": "exact.equals", "target": "complete_value", "domains": ["string"]},
                    {"operator": "lexical.terms", "target": "complete_value", "domains": ["string"]},
                    {"operator": "lexical.phrase", "target": "complete_value", "domains": ["string"]},
                ],
            ),
            (
                "arbitrary_sequence_string",
                {"shapes": [{"shape": "sequence", "member_domains": ["string"]}]},
                [
                    {"operator": "exact.equals", "target": "member", "domains": ["string"]},
                    {"operator": "lexical.terms", "target": "member", "domains": ["string"]},
                    {"operator": "lexical.phrase", "target": "member", "domains": ["string"]},
                ],
            ),
            (
                "arbitrary_sequence_mixed",
                {"shapes": [{"shape": "sequence", "member_domains": ["string", "integer"]}]},
                [
                    {"operator": "exact.equals", "target": "member", "domains": ["string", "integer"]},
                    {"operator": "lexical.terms", "target": "member", "domains": ["string"]},
                    {"operator": "lexical.phrase", "target": "member", "domains": ["string"]},
                ],
            ),
            (
                "arbitrary_sequence_permuted",
                {"shapes": [{"shape": "sequence", "member_domains": ["string", "integer"]}]},
                [
                    {"operator": "exact.equals", "target": "member", "domains": ["integer", "string"]},
                    {"operator": "lexical.terms", "target": "member", "domains": ["string"]},
                    {"operator": "lexical.phrase", "target": "member", "domains": ["string"]},
                ],
            ),
            (
                "arbitrary_scalar_permuted",
                {"shapes": [{"shape": "scalar", "domains": ["date", "string"]}]},
                [
                    {"operator": "exact.equals", "target": "complete_value", "domains": ["string", "date"]},
                    {"operator": "lexical.terms", "target": "complete_value", "domains": ["string"]},
                    {"operator": "lexical.phrase", "target": "complete_value", "domains": ["string"]},
                ],
            ),
            (
                "arbitrary_mixed_permuted",
                {
                    "shapes": [
                        {"shape": "scalar", "domains": ["date", "string"]},
                        {"shape": "sequence", "member_domains": ["string", "integer"]},
                    ]
                },
                [
                    {"operator": "exact.equals", "target": "complete_value", "domains": ["string", "date"]},
                    {"operator": "exact.equals", "target": "member", "domains": ["integer", "string"]},
                    {"operator": "lexical.terms", "target": "complete_value", "domains": ["string"]},
                    {"operator": "lexical.phrase", "target": "complete_value", "domains": ["string"]},
                    {"operator": "lexical.terms", "target": "member", "domains": ["string"]},
                    {"operator": "lexical.phrase", "target": "member", "domains": ["string"]},
                ],
            ),
        ]
        for field_name, value, access in cases:
            with self.subTest(field_name=field_name), TemporaryDirectory() as directory:
                catalog = minimum_catalog()
                catalog["semantic_dimensions"].append({
                    "field_class": "semantic_identifier",
                    "field_name": field_name,
                    "description": field_name,
                    "value": value,
                    "access": access,
                })
                text = json.dumps(catalog, separators=(",", ":"))
                load_capability_catalog(self._write(directory, text))

    def test_semantic_identifier_access_erasure_fails_closed(self):
        cases = [
            (
                "mapping_scalar_rejected",
                {"shapes": [{"shape": "scalar", "domains": ["mapping"]}]},
                [{"operator": "exact.equals", "target": "complete_value", "domains": ["mapping"]}],
            ),
            (
                "mapping_member_rejected",
                {"shapes": [{"shape": "sequence", "member_domains": ["mapping"]}]},
                [{"operator": "exact.equals", "target": "member", "domains": ["mapping"]}],
            ),
            (
                "scalar_exact_omitted",
                {"shapes": [{"shape": "scalar", "domains": ["date"]}]},
                [],
            ),
            (
                "sequence_exact_omitted",
                {"shapes": [{"shape": "sequence", "member_domains": ["string"]}]},
                [
                    {"operator": "lexical.terms", "target": "member", "domains": ["string"]},
                    {"operator": "lexical.phrase", "target": "member", "domains": ["string"]},
                ],
            ),
            (
                "scalar_terms_omitted",
                {"shapes": [{"shape": "scalar", "domains": ["string"]}]},
                [
                    {"operator": "exact.equals", "target": "complete_value", "domains": ["string"]},
                    {"operator": "lexical.phrase", "target": "complete_value", "domains": ["string"]},
                ],
            ),
            (
                "scalar_phrase_omitted",
                {"shapes": [{"shape": "scalar", "domains": ["string"]}]},
                [
                    {"operator": "exact.equals", "target": "complete_value", "domains": ["string"]},
                    {"operator": "lexical.terms", "target": "complete_value", "domains": ["string"]},
                ],
            ),
            (
                "sequence_terms_omitted",
                {"shapes": [{"shape": "sequence", "member_domains": ["string"]}]},
                [
                    {"operator": "exact.equals", "target": "member", "domains": ["string"]},
                    {"operator": "lexical.phrase", "target": "member", "domains": ["string"]},
                ],
            ),
            (
                "sequence_phrase_omitted",
                {"shapes": [{"shape": "sequence", "member_domains": ["string"]}]},
                [
                    {"operator": "exact.equals", "target": "member", "domains": ["string"]},
                    {"operator": "lexical.terms", "target": "member", "domains": ["string"]},
                ],
            ),
            (
                "date_lexical",
                {"shapes": [{"shape": "scalar", "domains": ["date"]}]},
                [
                    {"operator": "exact.equals", "target": "complete_value", "domains": ["date"]},
                    {"operator": "lexical.terms", "target": "complete_value", "domains": ["string"]},
                    {"operator": "lexical.phrase", "target": "complete_value", "domains": ["string"]},
                ],
            ),
            (
                "complete_without_scalar",
                {"shapes": [{"shape": "sequence", "member_domains": ["string"]}]},
                [{"operator": "exact.equals", "target": "complete_value", "domains": ["string"]}],
            ),
            (
                "member_without_sequence",
                {"shapes": [{"shape": "scalar", "domains": ["string"]}]},
                [{"operator": "exact.equals", "target": "member", "domains": ["string"]}],
            ),
            (
                "missing_exact_domain",
                {"shapes": [{"shape": "sequence", "member_domains": ["string", "integer"]}]},
                [
                    {"operator": "exact.equals", "target": "member", "domains": ["string"]},
                    {"operator": "lexical.terms", "target": "member", "domains": ["string"]},
                    {"operator": "lexical.phrase", "target": "member", "domains": ["string"]},
                ],
            ),
            (
                "extra_exact_domain",
                {"shapes": [{"shape": "scalar", "domains": ["string"]}]},
                [
                    {"operator": "exact.equals", "target": "complete_value", "domains": ["string", "integer"]},
                    {"operator": "lexical.terms", "target": "complete_value", "domains": ["string"]},
                    {"operator": "lexical.phrase", "target": "complete_value", "domains": ["string"]},
                ],
            ),
            (
                "duplicate_value_domain",
                {"shapes": [{"shape": "scalar", "domains": ["string", "string"]}]},
                [{"operator": "exact.equals", "target": "complete_value", "domains": ["string"]}],
            ),
            (
                "vector_access",
                {"shapes": [{"shape": "scalar", "domains": ["string"]}]},
                [{"operator": "vector.semantic_similarity", "target": "complete_value", "domains": ["string"]}],
            ),
        ]
        for field_name, value, access in cases:
            with self.subTest(field_name=field_name), TemporaryDirectory() as directory:
                catalog = minimum_catalog()
                catalog["semantic_dimensions"].append({
                    "field_class": "semantic_identifier",
                    "field_name": f"malformed_{field_name}",
                    "description": field_name,
                    "value": value,
                    "access": access,
                })
                text = json.dumps(catalog, separators=(",", ":"))
                with self.assertRaises(CapabilityCatalogError):
                    load_capability_catalog(self._write(directory, text))

    def test_authored_semantic_identifier_names_remain_open(self):
        with TemporaryDirectory() as directory:
            catalog = copy.deepcopy(self.catalog())
            catalog["semantic_dimensions"].extend([
                {
                    "field_class": "semantic_identifier",
                    "field_name": "arbitrary_authored_field_a",
                    "description": "authored A",
                    "value": {"shapes": [{"shape": "scalar", "domains": ["date"]}]},
                    "access": [{"operator": "exact.equals", "target": "complete_value", "domains": ["date"]}],
                },
                {
                    "field_class": "semantic_identifier",
                    "field_name": "arbitrary_authored_field_b",
                    "description": "authored B",
                    "value": {"shapes": [{"shape": "sequence", "member_domains": ["string"]}]},
                    "access": [{"operator": "exact.equals", "target": "member", "domains": ["string"]}, {"operator": "lexical.terms", "target": "member", "domains": ["string"]}, {"operator": "lexical.phrase", "target": "member", "domains": ["string"]}],
                },
            ])
            catalog["graph"]["relations"].append({
                "relation_class": "semantic_identifier",
                "relation_name": "arbitrary_relation_name",
                "description": "authored relation",
                "source_kinds": ["semantic_object"],
                "target_kinds": ["semantic_object", "semantic_region"],
                "operations": ["graph.relation_occurrence_lookup", "graph.inbound_traversal", "graph.outbound_traversal"],
            })
            path = self._write(directory, json.dumps(catalog, separators=(",", ":")))
            load_capability_catalog(path)
    def test_minimum_producer_fixture_preserves_optional_absence(self):
        with TemporaryDirectory() as directory:
            catalog = minimum_catalog()
            text = json.dumps(catalog, separators=(",", ":"))
            load_capability_catalog(self._write(directory, text))
            self.assertNotIn(("semantic_identifier", "tags"), {
                (item["field_class"], item["field_name"])
                for item in catalog["semantic_dimensions"]
            })
            self.assertNotIn(("region", "region_text"), {
                (item["field_class"], item["field_name"])
                for item in catalog["semantic_dimensions"]
            })
            self.assertEqual(catalog["vector"]["targets"], [])

    def test_constitutive_presence_and_coupling_fail_closed(self):
        base = minimum_catalog()
        cases = {}
        for identity in (
            ("intrinsic", "raw_markdown"),
            ("intrinsic", "parsed_text"),
            ("region", "region_path"),
            ("semantic_path", "path_hierarchy"),
            ("semantic_path", "path_component"),
        ):
            catalog = copy.deepcopy(base)
            catalog["semantic_dimensions"] = [
                item for item in catalog["semantic_dimensions"]
                if (item["field_class"], item["field_name"]) != identity
            ]
            cases[f"missing dimension {identity}"] = catalog

        for identity in (
            ("intrinsic", "raw_markdown"),
            ("intrinsic", "parsed_text"),
            ("region", "region_path"),
            ("semantic_path", "path_hierarchy"),
            ("semantic_path", "path_component"),
        ):
            catalog = copy.deepcopy(base)
            item = next(
                item for item in catalog["semantic_dimensions"]
                if (item["field_class"], item["field_name"]) == identity
            )
            item["access"] = []
            cases[f"missing exact access {identity}"] = catalog

        for operator in ("lexical.terms", "lexical.phrase"):
            catalog = copy.deepcopy(base)
            item = next(item for item in catalog["semantic_dimensions"] if item["field_name"] == "parsed_text")
            item["access"] = [access for access in item["access"] if access["operator"] != operator]
            cases[f"parsed text missing {operator}"] = catalog

        raw_lexical = copy.deepcopy(base)
        raw = next(item for item in raw_lexical["semantic_dimensions"] if item["field_name"] == "raw_markdown")
        raw["access"].append({"operator": "lexical.terms", "target": "complete_value", "domains": ["string"]})
        cases["raw markdown lexical access"] = raw_lexical

        region_exact = copy.deepcopy(base)
        region_text = {
            "field_class": "region",
            "field_name": "region_text",
            "description": "region text",
            "value": {"shapes": [{"shape": "scalar", "domains": ["string"]}]},
            "access": [{"operator": "exact.equals", "target": "complete_value", "domains": ["string"]}],
        }
        region_exact["semantic_dimensions"].append(region_text)
        cases["region text exact access"] = region_exact

        region_partial = copy.deepcopy(base)
        region_text = copy.deepcopy(region_text)
        region_text["access"] = [{"operator": "lexical.terms", "target": "complete_value", "domains": ["string"]}]
        region_partial["semantic_dimensions"].append(region_text)
        cases["region text partial lexical access"] = region_partial

        path_partial = copy.deepcopy(base)
        path_component = next(item for item in path_partial["semantic_dimensions"] if item["field_name"] == "path_component")
        path_component["access"].append({"operator": "lexical.terms", "target": "complete_value", "domains": ["string"]})
        cases["path component partial lexical access"] = path_partial

        missing_node = copy.deepcopy(base)
        missing_node["graph"]["node_kinds"].remove("scope")
        cases["missing graph node kind"] = missing_node

        for relation in (
            ("structural", "contains_scope"),
            ("structural", "contains_object"),
            ("structural", "contains_region"),
            ("structural", "contains_unit"),
            ("body_wikilink", "linked_to"),
        ):
            catalog = copy.deepcopy(base)
            catalog["graph"]["relations"] = [
                item for item in catalog["graph"]["relations"]
                if (item["relation_class"], item["relation_name"]) != relation
            ]
            cases[f"missing relation {relation}"] = catalog

        for discovery in (("semantic_object", "address_text"), ("semantic_region", "address_text")):
            catalog = copy.deepcopy(base)
            catalog["graph"]["discovery"] = [
                item for item in catalog["graph"]["discovery"]
                if (item["node_kind"], item["dimension_name"]) != discovery
            ]
            cases[f"missing discovery {discovery}"] = catalog

        with_unit = minimum_catalog(semantic_unit_target=True)
        parsed_text = next(item for item in with_unit["semantic_dimensions"] if item["field_name"] == "parsed_text")
        parsed_text["access"] = [
            access for access in parsed_text["access"]
            if access["operator"] != "vector.semantic_similarity"
        ]
        cases["semantic unit target without parsed text vector access"] = with_unit

        without_unit = minimum_catalog()
        parsed_text = next(item for item in without_unit["semantic_dimensions"] if item["field_name"] == "parsed_text")
        parsed_text["access"].append(
            {"operator": "vector.semantic_similarity", "target": "complete_value", "domains": ["string"]}
        )
        cases["parsed text vector access without semantic unit target"] = without_unit

        for label, catalog in cases.items():
            with self.subTest(label=label), TemporaryDirectory() as directory:
                text = json.dumps(catalog, separators=(",", ":"))
                with self.assertRaises(CapabilityCatalogError):
                    load_capability_catalog(self._write(directory, text))
    def test_malformed_catalogs_fail_at_shared_admission(self):
        for label, text in self.malformed_catalogs().items():
            with self.subTest(label=label), TemporaryDirectory() as directory:
                with self.assertRaises(CapabilityCatalogError):
                    load_capability_catalog(self._write(directory, text))

    def test_malformed_catalogs_fail_before_retrieval_provider_and_run_insert(self):
        config = RuntimeConfig(ModelConfig("openai", "router", 1.0, "router"), ModelConfig("openai", "retrieval", 1.0, "retrieval"), PacketConfig(32), ModelConfig("openai", "synthesis", 1.0, "synthesis"))

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
