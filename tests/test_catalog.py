import json
import contextlib
import copy
import io
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from semantic_traversal.projection.catalog import CatalogGenerationError, generate_catalog
from semantic_traversal.cli import main


class CatalogTests(unittest.TestCase):
    def _facts(self):
        return {
            "field_capabilities": [
                {
                    "field_class": "intrinsic",
                    "field_name": "parsed_text",
                    "canonical_shapes": ["string"],
                    "surfaces": {
                        "exact": {"operators": ["equals"]},
                        "lexical": {"operators": ["terms", "phrase"]},
                    },
                },
                {
                    "field_class": "region",
                    "field_name": "region_path",
                    "canonical_shapes": ["sequence"],
                    "surfaces": {"exact": {"operators": ["equals"]}},
                },
                {
                    "field_class": "semantic_path",
                    "field_name": "path_hierarchy",
                    "canonical_shapes": ["sequence"],
                    "surfaces": {"exact": {"operators": ["equals"]}},
                },
                {
                    "field_class": "semantic_identifier",
                    "field_name": "scalar_integer",
                    "canonical_shapes": ["integer"],
                    "scalar_domains": ["integer"],
                    "surfaces": {"exact": {"operators": ["equals"]}},
                },
                {
                    "field_class": "semantic_identifier",
                    "field_name": "sequence_string",
                    "canonical_shapes": ["sequence"],
                    "scalar_domains": [],
                    "sequence_member_domains": ["string"],
                    "surfaces": {
                        "exact": {"operators": ["equals"], "sequence_behavior": "member_equality"},
                        "lexical": {"operators": ["terms", "phrase"], "operand_domains": ["string"]},
                    },
                },
                {
                    "field_class": "semantic_identifier",
                    "field_name": "mixed_integer_string",
                    "canonical_shapes": ["integer", "sequence"],
                    "scalar_domains": ["integer"],
                    "sequence_member_domains": ["string"],
                    "surfaces": {"exact": {"operators": ["equals"]}, "lexical": {"operators": ["terms", "phrase"]}},
                },
            ],
            "graph_discovery_capabilities": [
                {"node_kind": "semantic_object", "dimension_name": "tag", "operators": ["terms", "phrase"]},
            ],
            "graph_relation_capabilities": [
                {
                    "relation_class": "semantic_identifier",
                    "relation_name": "sequence_string",
                    "source_kinds": ["semantic_object"],
                    "target_kinds": ["semantic_object", "semantic_region"],
                    "operations": ["relation_occurrence_lookup", "inbound_traversal", "outbound_traversal"],
                },
                {
                    "relation_class": "semantic_identifier",
                    "relation_name": "graph_only",
                    "source_kinds": ["semantic_object"],
                    "target_kinds": ["semantic_object", "semantic_region"],
                    "operations": ["relation_occurrence_lookup", "inbound_traversal", "outbound_traversal"],
                },
                {
                    "relation_class": "structural",
                    "relation_name": "contains_scope",
                    "source_kinds": ["scope"],
                    "target_kinds": ["scope"],
                    "operations": ["relation_occurrence_lookup", "inbound_traversal", "outbound_traversal"],
                },
            ],
            "graph_node_classes": ["scope", "semantic_object", "semantic_region", "semantic_unit"],
            "vector_capability": {
                "operations": ["semantic_similarity"],
                "query_operand": {
                    "shape": "string",
                    "requirement": "exactly one non-empty string",
                    "segmentation": False,
                    "truncation": False,
                    "deterministic_enrichment": False,
                },
                "represented_target_kinds": [
                    {"target_kind": "semantic_unit", "input_dimension": "exact_canonical_parsed_text"},
                    {"target_kind": "semantic_object", "input_dimension": "canonical_authored_object_name_for_zero_unit_object"},
                ],
            },
            "surface_operation_grammar": {
                "exact": ["equals"],
                "lexical": ["terms", "phrase"],
                "vector": ["semantic_similarity"],
                "graph": ["node_discovery", "relation_occurrence_lookup", "inbound_traversal", "outbound_traversal"],
                "temporal": ["earliest", "latest", "before", "after", "between", "ordered"],
            },
        }

    def _config(self, root: Path, descriptions: dict[str, str]) -> Path:
        path = root / "build-config.yaml"
        lines = ["vault_name: test", "uuid_field: uuid", "excluded_folders: []", "semantic_identifiers:"]
        for name, description in descriptions.items():
            lines.extend([f"  {name}:", f"    description: {description!r}"])
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    def test_success_presents_value_shapes_access_and_reused_authored_description(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            description = "  authored meaning, preserved exactly  "
            descriptions = {
                "sequence_string": description,
                "graph_only": "graph-only meaning",
                "mixed_integer_string": "mixed meaning",
                "scalar_integer": "integer meaning",
            }
            output = root / "capability-catalog.json"
            catalog = generate_catalog(self._facts(), descriptions, output)
            self.assertEqual(catalog, json.loads(output.read_text(encoding="utf-8")))
            self.assertEqual(list(catalog), ["catalog_schema_version", "semantic_dimensions", "graph", "vector", "operators"])

            dimensions = {(item["field_class"], item["field_name"]): item for item in catalog["semantic_dimensions"]}
            sequence = dimensions[("semantic_identifier", "sequence_string")]
            self.assertEqual(sequence["value"], {"shapes": [{"shape": "sequence", "member_domains": ["string"]}]})
            self.assertEqual(sequence["description"], description)
            self.assertEqual([(item["operator"], item["target"]) for item in sequence["access"]], [
                ("exact.equals", "member"), ("lexical.terms", "member"), ("lexical.phrase", "member")
            ])
            self.assertTrue(all("sequence_behavior" not in item for item in sequence["access"]))

            mixed = dimensions[("semantic_identifier", "mixed_integer_string")]
            self.assertEqual(mixed["value"], {"shapes": [
                {"shape": "scalar", "domains": ["integer"]},
                {"shape": "sequence", "member_domains": ["string"]},
            ]})
            self.assertEqual([(item["operator"], item["target"], item["domains"]) for item in mixed["access"]], [
                ("exact.equals", "complete_value", ["integer"]),
                ("exact.equals", "member", ["string"]),
                ("lexical.terms", "member", ["string"]),
                ("lexical.phrase", "member", ["string"]),
            ])
            self.assertEqual(
                dimensions[("semantic_path", "path_hierarchy")]["value"],
                {"shapes": [{"shape": "ordered_sequence", "domains": ["string"]}]},
            )
            for key in (("region", "region_path"), ("semantic_path", "path_hierarchy")):
                ordered_access = dimensions[key]["access"]
                self.assertEqual(len(ordered_access), 1)
                self.assertEqual(ordered_access[0], {
                    "operator": "exact.equals",
                    "target": "complete_value",
                    "operand": {"shape": "ordered_sequence", "member_domains": ["string"]},
                })
                self.assertNotIn("domains", ordered_access[0])

            parsed_text = dimensions[("intrinsic", "parsed_text")]
            self.assertEqual(
                [(item["operator"], item["target"], item["domains"]) for item in parsed_text["access"]],
                [
                    ("exact.equals", "complete_value", ["string"]),
                    ("lexical.terms", "complete_value", ["string"]),
                    ("lexical.phrase", "complete_value", ["string"]),
                    ("vector.semantic_similarity", "complete_value", ["string"]),
                ],
            )

            relations = {(item["relation_class"], item["relation_name"]): item for item in catalog["graph"]["relations"]}
            self.assertEqual(relations[("semantic_identifier", "sequence_string")]["description"], description)
            self.assertEqual(relations[("semantic_identifier", "graph_only")]["description"], "graph-only meaning")
            self.assertNotIn("scalar_domains", json.dumps(catalog))
            self.assertNotIn("sequence_member_domains", json.dumps(catalog))
            self.assertNotIn("operand_domains", json.dumps(catalog))
            self.assertNotIn("sequence_behavior", json.dumps(catalog))
            self.assertNotIn("unit_id", json.dumps(catalog))

    def test_parsed_text_vector_access_requires_represented_unit_input(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            facts = self._facts()
            facts["vector_capability"]["represented_target_kinds"] = [
                {"target_kind": "semantic_object", "input_dimension": "canonical_authored_object_name_for_zero_unit_object"},
            ]
            catalog = generate_catalog(
                facts,
                {
                    "sequence_string": "sequence",
                    "graph_only": "relation",
                    "mixed_integer_string": "mixed",
                    "scalar_integer": "integer",
                },
                root / "catalog.json",
            )
            parsed_text = next(
                item for item in catalog["semantic_dimensions"]
                if (item["field_class"], item["field_name"]) == ("intrinsic", "parsed_text")
            )
            self.assertFalse(any(item["operator"] == "vector.semantic_similarity" for item in parsed_text["access"]))

    def test_mapping_domain_is_not_generated_as_exact_capability(self):
        with TemporaryDirectory() as directory:
            facts = copy.deepcopy(self._facts())
            field = next(item for item in facts["field_capabilities"] if item["field_name"] == "scalar_integer")
            field["field_name"] = "mapping_field"
            field["canonical_shapes"] = ["mapping"]
            field["scalar_domains"] = ["mapping"]
            with self.assertRaises(CatalogGenerationError):
                generate_catalog(
                    facts,
                    {
                        "mapping_field": "mapping",
                        "sequence_string": "sequence",
                        "graph_only": "relation",
                        "mixed_integer_string": "mixed",
                    },
                    Path(directory) / "catalog.json",
                )
    def test_inconsistent_vector_target_fails_closed(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            facts = copy.deepcopy(self._facts())
            facts["vector_capability"]["represented_target_kinds"][0]["input_dimension"] = "unsupported"
            with self.assertRaises(CatalogGenerationError):
                generate_catalog(
                    facts,
                    {
                        "sequence_string": "sequence",
                        "graph_only": "relation",
                        "mixed_integer_string": "mixed",
                        "scalar_integer": "integer",
                    },
                    root / "catalog.json",
                )

    def test_missing_descriptions_preserve_existing_catalog_without_artifact(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            descriptions = {"sequence_string": "ok"}
            output = root / "capability-catalog.json"
            output.write_text("sentinel", encoding="utf-8")
            with self.assertRaisesRegex(CatalogGenerationError, "graph_only, mixed_integer_string, scalar_integer"):
                generate_catalog(self._facts(), descriptions, output)
            self.assertEqual(output.read_text(encoding="utf-8"), "sentinel")

    def test_blank_description_is_missing_and_unknown_config_key_does_not_create_dimension(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            descriptions = {"sequence_string": " ", "unknown": "not represented"}
            with self.assertRaisesRegex(CatalogGenerationError, "sequence_string"):
                generate_catalog(self._facts(), descriptions, root / "catalog.json")
            complete = {
                "sequence_string": "sequence",
                "graph_only": "relation",
                "mixed_integer_string": "mixed",
                "scalar_integer": "integer",
            }
            catalog = generate_catalog(self._facts(), complete, root / "catalog.json")
            names = {(item["field_class"], item["field_name"]) for item in catalog["semantic_dimensions"]}
            self.assertNotIn(("semantic_identifier", "unknown"), names)
            self.assertNotIn("unknown", json.dumps(catalog))

    def test_catalog_bytes_are_deterministic(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            facts = self._facts()
            first = root / "first.json"
            second = root / "second.json"
            generate_catalog(facts, {"graph_only": "g", "sequence_string": "s", "mixed_integer_string": "m", "scalar_integer": "i"}, first)
            generate_catalog(facts, {"scalar_integer": "i", "mixed_integer_string": "m", "sequence_string": "s", "graph_only": "g"}, second)
            self.assertEqual(first.read_bytes(), second.read_bytes())

    def test_cli_catalog_generate_delegates_to_accepted_observer(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            descriptions = {
                "sequence_string": "sequence",
                "graph_only": "relation",
                "mixed_integer_string": "mixed",
                "scalar_integer": "integer",
            }
            config = self._config(root, descriptions)
            output = root / "catalog.json"
            stdout, stderr = io.StringIO(), io.StringIO()
            with patch("semantic_traversal.cli._connection"), patch("semantic_traversal.cli.observe_capability_facts", return_value=self._facts()):
                with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                    code = main([
                        "catalog", "generate", "--build", str(root / "completed-build"),
                        "--config", str(config), "--output", str(output), "--json",
                    ])
            self.assertEqual(code, 0, stderr.getvalue())
            self.assertEqual(json.loads(stdout.getvalue()), {"catalog": str(output.resolve())})
            self.assertTrue(output.is_file())

    def test_catalog_generate_help_has_no_homework_option(self):
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout), self.assertRaises(SystemExit) as raised:
            main(["catalog", "generate", "--help"])
        self.assertEqual(raised.exception.code, 0)
        self.assertNotIn("missing-output", stdout.getvalue())
        self.assertNotIn("homework", stdout.getvalue().lower())

    def test_cli_incomplete_config_reports_all_missing_keys_before_observation(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            config = self._config(root, {"z_missing": "", "a_missing": ""})
            output = root / "catalog.json"
            output.write_text("sentinel", encoding="utf-8")
            stderr = io.StringIO()
            with patch("semantic_traversal.cli.observe_capability_facts", side_effect=AssertionError("must not observe incomplete config")):
                with contextlib.redirect_stderr(stderr):
                    code = main([
                        "catalog", "generate", "--build", str(root / "completed-build"),
                        "--config", str(config), "--output", str(output),
                    ])
            self.assertNotEqual(code, 0)
            self.assertEqual(output.read_text(encoding="utf-8"), "sentinel")
            diagnostic = stderr.getvalue()
            self.assertLess(diagnostic.index("a_missing"), diagnostic.index("z_missing"))
            self.assertNotIn("homework", diagnostic.lower())
            self.assertEqual(list(root.glob("*.yaml")), [config])

    def test_unsupported_observer_facts_fail_closed(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            descriptions = {
                "sequence_string": "sequence",
                "graph_only": "relation",
                "mixed_integer_string": "mixed",
                "scalar_integer": "integer",
            }
            facts = copy.deepcopy(self._facts())
            facts["graph_discovery_capabilities"][0]["dimension_name"] = "unsupported"
            with self.assertRaises(CatalogGenerationError):
                generate_catalog(facts, descriptions, root / "catalog.json")


if __name__ == "__main__":
    unittest.main()
