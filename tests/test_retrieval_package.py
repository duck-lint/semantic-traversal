import hashlib
import json
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path
from tempfile import TemporaryDirectory

from tests.catalog_fixtures import minimum_catalog
from semantic_traversal.runtime.retrieval_package import (
    IDENTITY_VERSION,
    RetrievalPackageError,
    load_retrieval_package,
    require_catalog_binding,
)


class RetrievalPackageTests(unittest.TestCase):
    def _write_package(self, root: Path, *, substrate=b"substrate", vectors=b"vectors", catalog_text=None) -> Path:
        root.mkdir(parents=True, exist_ok=True)
        (root / "substrate.sqlite3").write_bytes(substrate)
        (root / "vectors.npy").write_bytes(vectors)
        if catalog_text is None:
            catalog_text = json.dumps(minimum_catalog(), separators=(",", ":"), ensure_ascii=False)
        (root / "capability_catalog.json").write_text(catalog_text, encoding="utf-8")
        return root

    def test_identity_is_derived_from_exact_bytes_and_is_path_independent(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            first = self._write_package(root / "first")
            second = self._write_package(root / "second")
            left = load_retrieval_package(first)
            right = load_retrieval_package(second)
            self.assertEqual(left.identity, right.identity)
            self.assertEqual(left.identity.substrate_sha256, "sha256:" + hashlib.sha256(b"substrate").hexdigest())
            self.assertEqual(left.identity.vectors_sha256, "sha256:" + hashlib.sha256(b"vectors").hexdigest())
            self.assertEqual(
                left.identity.capability_catalog_sha256,
                left.capability_catalog.sha256,
            )
            material = {
                "capability_catalog_sha256": left.identity.capability_catalog_sha256,
                "identity_version": IDENTITY_VERSION,
                "substrate_sha256": left.identity.substrate_sha256,
                "vectors_sha256": left.identity.vectors_sha256,
            }
            canonical = json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
            self.assertEqual(left.identity.package_id, "sha256:" + hashlib.sha256(canonical).hexdigest())
            self.assertNotIn(str(first), left.identity.package_id)
            self.assertNotIn("timestamp", left.identity.package_id)

    def test_one_byte_substrate_change_changes_package_identity(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            first = self._write_package(root / "first")
            second = self._write_package(root / "second", substrate=b"substratE")
            left = load_retrieval_package(first)
            right = load_retrieval_package(second)
            self.assertNotEqual(left.identity.substrate_sha256, right.identity.substrate_sha256)
            self.assertNotEqual(left.identity.package_id, right.identity.package_id)

    def test_one_byte_vectors_change_changes_package_identity(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            first = self._write_package(root / "first")
            second = self._write_package(root / "second", vectors=b"vectorS")
            left = load_retrieval_package(first)
            right = load_retrieval_package(second)
            self.assertNotEqual(left.identity.vectors_sha256, right.identity.vectors_sha256)
            self.assertNotEqual(left.identity.package_id, right.identity.package_id)

    def test_valid_catalog_byte_formatting_change_changes_identity_not_meaning(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            compact = json.dumps(minimum_catalog(), separators=(",", ":"), ensure_ascii=False)
            formatted = json.dumps(minimum_catalog(), indent=2, ensure_ascii=False) + "\n"
            first = self._write_package(root / "first", catalog_text=compact)
            second = self._write_package(root / "second", catalog_text=formatted)
            left = load_retrieval_package(first)
            right = load_retrieval_package(second)
            self.assertNotEqual(left.identity.capability_catalog_sha256, right.identity.capability_catalog_sha256)
            self.assertNotEqual(left.identity.package_id, right.identity.package_id)
            self.assertEqual(left.capability_catalog.parsed, right.capability_catalog.parsed)

    def test_catalog_binding_requires_exact_catalog_sha(self):
        with TemporaryDirectory() as directory:
            package = load_retrieval_package(self._write_package(Path(directory)))
            require_catalog_binding(package, package.identity.capability_catalog_sha256)
            with self.assertRaises(RetrievalPackageError):
                require_catalog_binding(package, "sha256:" + "0" * 64)

    def test_package_and_admitted_catalog_are_immutable(self):
        with TemporaryDirectory() as directory:
            package = load_retrieval_package(self._write_package(Path(directory)))
            with self.assertRaises(FrozenInstanceError):
                package.identity.package_id = "changed"
            with self.assertRaises(FrozenInstanceError):
                package.identity.substrate_sha256 = "changed"
            with self.assertRaises(TypeError):
                package.capability_catalog.parsed["graph"] = {}
            with self.assertRaises(TypeError):
                package.capability_catalog.parsed["semantic_dimensions"][0]["field_name"] = "changed"
            with self.assertRaises(TypeError):
                package.capability_catalog.parsed["semantic_dimensions"] += ()

    def test_missing_or_malformed_required_artifacts_fail_closed(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_package(root)
            (root / "vectors.npy").unlink()
            with self.assertRaises(RetrievalPackageError):
                load_retrieval_package(root)

            malformed = Path(directory) / "malformed"
            self._write_package(malformed, catalog_text="{}")
            with self.assertRaises(RetrievalPackageError):
                load_retrieval_package(malformed)


if __name__ == "__main__":
    unittest.main()