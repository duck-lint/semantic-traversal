import contextlib
import io
import json
import os
import shutil
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from semantic_traversal.cli import main
from semantic_traversal.runtime.retrieval.package import load_retrieval_package
from semantic_traversal.runtime.retrieval.package_verification import (
    RetrievalPackageVerificationError,
    VERIFICATION_CONTRACT_VERSION,
    verify_retrieval_package,
)
from tests.test_cli import CliProvider


class RetrievalPackageVerificationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = TemporaryDirectory()
        root = Path(cls.directory.name)
        cls.build_a = cls._build(root / "build-a", "relation")
        cls.build_b = cls._build(root / "build-b", "other_relation")

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    @classmethod
    def _build(cls, root: Path, relation_name: str) -> Path:
        vault = root / "vault"
        vault.mkdir(parents=True)
        (vault / "Target.md").write_text("---\nuuid: target\n---\ntarget body\n", encoding="utf-8")
        (vault / "Zero.md").write_text("---\nuuid: zero\ntags: [empty]\n---\n", encoding="utf-8")
        (vault / "Source.md").write_text(
            f"---\nuuid: source\n{relation_name}: \"[[Target]]\"\n---\nsource body {relation_name}\n",
            encoding="utf-8",
        )
        config = root / "config.yaml"
        config.write_text(
            "vault_name: test\nuuid_field: uuid\nexcluded_folders: []\nsemantic_identifiers:\n"
            "  tags:\n    description: test-authored declaration\n"
            f"  {relation_name}:\n    description: test-authored declaration\n",
            encoding="utf-8",
        )
        output = root / "completed"
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch("semantic_traversal.cli.OllamaEmbeddingProvider", CliProvider):
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                code = main(["build", "--vault", str(vault), "--config", str(config), "--output", str(output)])
        if code != 0:
            raise AssertionError(stderr.getvalue())
        catalog = output / "capability_catalog.json"
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = main([
                "catalog", "generate", "--build", str(output), "--config", str(config),
                "--output", str(catalog), "--json",
            ])
        if code != 0:
            raise AssertionError(stderr.getvalue())
        return output

    def _copy(self, name: str, source: Path | None = None) -> Path:
        destination = Path(self.directory.name) / name
        shutil.copytree(source or self.build_a, destination)
        return destination

    def _catalog(self, build: Path) -> dict:
        return json.loads((build / "capability_catalog.json").read_text(encoding="utf-8"))

    def _write_catalog(self, build: Path, catalog: dict) -> None:
        (build / "capability_catalog.json").write_text(
            json.dumps(catalog, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _assert_fails(self, build: Path) -> None:
        with self.assertRaises(RetrievalPackageVerificationError):
            verify_retrieval_package(load_retrieval_package(build))

    def test_relative_load_stabilizes_artifact_paths(self):
        original = Path.cwd()
        try:
            os.chdir(Path(self.build_a).parent)
            package = load_retrieval_package(Path(self.build_a).name)
            os.chdir(Path(self.directory.name))
            self.assertTrue(package.substrate_path.is_absolute())
            self.assertTrue(package.vectors_path.is_absolute())
            self.assertTrue(package.capability_catalog_path.is_absolute())
            verified = verify_retrieval_package(package)
            self.assertEqual(verified.verification_contract_version, VERIFICATION_CONTRACT_VERSION)
        finally:
            os.chdir(original)

    def test_substrate_from_one_build_with_vectors_from_another_fails(self):
        build = self._copy("frankenstein-vectors")
        shutil.copyfile(self.build_b / "vectors.npy", build / "vectors.npy")
        self._assert_fails(build)

    def test_catalog_from_one_semantic_identifier_build_with_another_fails(self):
        build = self._copy("frankenstein-semantic-identifiers", self.build_b)
        shutil.copyfile(self.build_a / "capability_catalog.json", build / "capability_catalog.json")
        self._assert_fails(build)

    def test_catalog_omitted_actual_identifier_fails(self):
        build = self._copy("omitted-identifier")
        catalog = self._catalog(build)
        catalog["semantic_dimensions"] = [
            item for item in catalog["semantic_dimensions"]
            if not (item["field_class"] == "semantic_identifier" and item["field_name"] == "relation")
        ]
        self._write_catalog(build, catalog)
        self._assert_fails(build)

    def test_catalog_added_unrepresented_identifier_fails(self):
        build = self._copy("added-identifier")
        catalog = self._catalog(build)
        catalog["semantic_dimensions"].append({
            "field_class": "semantic_identifier",
            "field_name": "not_represented",
            "description": "not represented",
            "value": {"shapes": [{"shape": "scalar", "domains": ["string"]}]},
            "access": [
                {"operator": "exact.equals", "target": "complete_value", "domains": ["string"]},
                {"operator": "lexical.terms", "target": "complete_value", "domains": ["string"]},
                {"operator": "lexical.phrase", "target": "complete_value", "domains": ["string"]},
            ],
        })
        self._write_catalog(build, catalog)
        self._assert_fails(build)

    def test_catalog_graph_relation_set_mismatch_fails(self):
        build = self._copy("relation-mismatch")
        catalog = self._catalog(build)
        catalog["graph"]["relations"] = [
            item for item in catalog["graph"]["relations"]
            if not (item["relation_class"] == "semantic_identifier" and item["relation_name"] == "relation")
        ]
        self._write_catalog(build, catalog)
        self._assert_fails(build)

    def test_catalog_vector_target_kind_mismatch_fails(self):
        build = self._copy("vector-target-mismatch")
        catalog = self._catalog(build)
        catalog["vector"]["targets"] = [
            item for item in catalog["vector"]["targets"]
            if item["target_kind"] != "semantic_object"
        ]
        self._write_catalog(build, catalog)
        self._assert_fails(build)

    def test_fixed_prose_changes_fail_verification_after_catalog_reloads(self):
        mutations = {
            "dimension": lambda catalog: next(
                item for item in catalog["semantic_dimensions"]
                if (item["field_class"], item["field_name"]) == ("intrinsic", "parsed_text")
            ).__setitem__("description", "contradictory parsed text meaning"),
            "discovery": lambda catalog: next(
                item for item in catalog["graph"]["discovery"]
                if (item["node_kind"], item["dimension_name"]) == ("semantic_object", "address_text")
            ).__setitem__("description", "contradictory object discovery meaning"),
            "relation": lambda catalog: next(
                item for item in catalog["graph"]["relations"]
                if (item["relation_class"], item["relation_name"]) == ("structural", "contains_unit")
            ).__setitem__("description", "contradictory containment meaning"),
            "operator": lambda catalog: catalog["operators"]["vector.semantic_similarity"].__setitem__(
                "meaning", "vector similarity is exact equality"
            ),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                build = self._copy(f"fixed-prose-{name}")
                catalog = self._catalog(build)
                mutate(catalog)
                self._write_catalog(build, catalog)
                package = load_retrieval_package(build)
                original = load_retrieval_package(self.build_a)
                self.assertNotEqual(package.identity.capability_catalog_sha256, original.identity.capability_catalog_sha256)
                self.assertNotEqual(package.identity.package_id, original.identity.package_id)
                with self.assertRaises(RetrievalPackageVerificationError):
                    verify_retrieval_package(package)

    def test_authored_description_change_is_accepted_when_consistent(self):
        build = self._copy("authored-description-change")
        catalog = self._catalog(build)
        for item in catalog["semantic_dimensions"]:
            if item["field_class"] == "semantic_identifier" and item["field_name"] == "relation":
                item["description"] = "new authored historical meaning"
        for item in catalog["graph"]["relations"]:
            if item["relation_class"] == "semantic_identifier" and item["relation_name"] == "relation":
                item["description"] = "new authored historical meaning"
        self._write_catalog(build, catalog)
        package = load_retrieval_package(build)
        original = load_retrieval_package(self.build_a)
        self.assertNotEqual(package.identity.capability_catalog_sha256, original.identity.capability_catalog_sha256)
        verified = verify_retrieval_package(package)
        self.assertEqual(verified.package.identity, package.identity)

    def test_inconsistent_authored_descriptions_fail_verification(self):
        build = self._copy("inconsistent-authored-description")
        catalog = self._catalog(build)
        next(
            item for item in catalog["semantic_dimensions"]
            if item["field_class"] == "semantic_identifier" and item["field_name"] == "relation"
        )["description"] = "only on the dimension"
        self._write_catalog(build, catalog)
        package = load_retrieval_package(build)
        with self.assertRaises(RetrievalPackageVerificationError):
            verify_retrieval_package(package)

    def test_post_load_artifact_mutations_fail_without_rebinding(self):
        for name in ("substrate.sqlite3", "vectors.npy", "capability_catalog.json"):
            with self.subTest(name=name):
                build = self._copy(f"mutated-{name.replace('.', '-')}")
                package = load_retrieval_package(build)
                path = build / name
                original = path.read_bytes()
                path.write_bytes(original + b"changed")
                with self.assertRaises(RetrievalPackageVerificationError):
                    verify_retrieval_package(package)

        build = self._copy("deleted-catalog")
        package = load_retrieval_package(build)
        (build / "capability_catalog.json").unlink()
        with self.assertRaises(RetrievalPackageVerificationError):
            verify_retrieval_package(package)

    def test_valid_copies_and_formatting_only_catalog_difference_verify(self):
        first = self._copy("valid-first")
        second = self._copy("valid-second")
        left = load_retrieval_package(first)
        right = load_retrieval_package(second)
        self.assertEqual(left.identity.package_id, right.identity.package_id)
        self.assertEqual(verify_retrieval_package(left).package.identity, left.identity)
        self.assertEqual(verify_retrieval_package(right).package.identity, right.identity)

        formatted = json.dumps(self._catalog(first), ensure_ascii=False, indent=4) + "\n"
        (second / "capability_catalog.json").write_text(formatted, encoding="utf-8")
        reformatted = load_retrieval_package(second)
        self.assertNotEqual(left.identity.package_id, reformatted.identity.package_id)
        self.assertEqual(reformatted.capability_catalog.parsed, left.capability_catalog.parsed)
        self.assertEqual(verify_retrieval_package(reformatted).package.identity, reformatted.identity)


if __name__ == "__main__":
    unittest.main()
