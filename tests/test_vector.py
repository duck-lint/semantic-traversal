import sqlite3
import unittest
from unittest.mock import patch
import os
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np

from ugh_parser import (
    EmbeddingContract,
    EmbeddingProviderError,
    OllamaEmbeddingProvider,
    VectorError,
    VectorTarget,
    build_vector_index,
    segment_and_embed,
    validate_vector_index,
    vector_eligible_targets,
    vector_lookup,
)
from ugh_parser.substrate import SCHEMA


class ProviderDouble:
    def __init__(self, capacity=5, identity="sha256-" + "a" * 64):
        self.contract = EmbeddingContract("qwen3-embedding:0.6b", identity, 1024, "float32", "l2", "cosine", capacity)
        self.calls = []

    def embed(self, text, *, truncate=False):
        self.calls.append((text, truncate))
        if truncate:
            raise AssertionError("tests must not request provider truncation")
        if len(text) > self.contract.input_capacity:
            raise EmbeddingProviderError("provider context capacity", capacity_exceeded=True)
        vector = np.zeros(1024, dtype=np.float32)
        vector[hash(text) % 1023] = 1.0
        vector[1023] = 1.0
        return vector


class NonMonotonicProvider(ProviderDouble):
    def embed(self, text, *, truncate=False):
        self.calls.append((text, truncate))
        # Deliberately violate monotonicity: abc is rejected while abcde fits.
        if text in {"abcdef", "abc", "ab", "a"}:
            raise EmbeddingProviderError("provider rejected this candidate", capacity_exceeded=True)
        return super().embed(text, truncate=truncate)


class VectorProjectionTests(unittest.TestCase):
    def _connection(self):
        connection = sqlite3.connect(":memory:")
        connection.executescript(SCHEMA)
        connection.executemany(
            "INSERT INTO canonical_objects VALUES (?, ?, ?)",
            [("unit-object", "Unit.md", 0), ("zero-object", "folder/Zero.md", 1), ("empty-object", "Empty.md", 2)],
        )
        connection.execute(
            "INSERT INTO canonical_units VALUES (?, ?, ?, ?, ?, ?)",
            (1, "unit-object", "Unit.md", 1, "raw", "alpha unit text"),
        )
        connection.execute(
            "INSERT INTO canonical_units VALUES (?, ?, ?, ?, ?, ?)",
            (2, "empty-object", "Empty.md", 1, "raw", ""),
        )
        connection.commit()
        return connection

    def test_targets_are_exact_units_or_zero_unit_object_names(self):
        connection = self._connection()
        self.assertEqual(
            vector_eligible_targets(connection),
            (
                VectorTarget("semantic_unit", 1, "alpha unit text"),
                VectorTarget("semantic_object", "zero-object", "Zero"),
            ),
        )

    def test_provider_confirmed_fallback_preserves_code_points(self):
        provider = ProviderDouble(capacity=3)
        source = "abcdefgh"
        segments = segment_and_embed(provider, source)
        self.assertEqual("".join(text for text, _ in segments), source)
        self.assertEqual([text for text, _ in segments], ["abc", "def", "gh"])
        self.assertTrue(all(truncate is False for _, truncate in provider.calls))

    def test_fallback_checks_descending_boundaries_without_monotonicity(self):
        provider = NonMonotonicProvider(capacity=5)
        with self.assertRaises(EmbeddingProviderError):
            provider.embed("abc", truncate=False)
        provider.embed("abcde", truncate=False)
        segments = segment_and_embed(provider, "abcdef")
        self.assertEqual([text for text, _ in segments], ["abcde", "f"])

        # The former binary-search assumption tests abc, then discards every
        # longer boundary after that failure; it cannot find this fit.
        binary_probe = NonMonotonicProvider(capacity=5)
        low, high = 1, 5
        accepted = None
        while low <= high:
            middle = (low + high) // 2
            try:
                binary_probe.embed("abcdef"[:middle], truncate=False)
            except EmbeddingProviderError:
                high = middle - 1
            else:
                accepted = middle
                low = middle + 1
        self.assertIsNone(accepted)

    def test_build_persists_float32_vectors_and_reopens(self):
        connection = self._connection()
        provider = ProviderDouble(capacity=100)
        with TemporaryDirectory() as directory:
            matrix_path = Path(directory) / "vectors.npy"
            build_vector_index(connection, matrix_path, provider)
            validate_vector_index(connection, matrix_path)
            matrix = np.load(matrix_path, allow_pickle=False)
            self.assertEqual(matrix.dtype, np.float32)
            self.assertEqual(matrix.shape, (2, 1024))
            self.assertEqual(
                connection.execute("SELECT target_kind, target_identity_json FROM vector_segments ORDER BY matrix_row").fetchall(),
                [("semantic_unit", "1"), ("semantic_object", '"zero-object"')],
            )
            reopened = sqlite3.connect(":memory:")
            connection.backup(reopened)
            validate_vector_index(reopened, matrix_path)
            hits = vector_lookup(reopened, matrix_path, "query", provider)
            self.assertEqual({(hit.target_kind, hit.target_identity) for hit in hits}, {("semantic_unit", 1), ("semantic_object", "zero-object")})

    def test_corrupt_mapping_rows_are_rejected(self):
        connection = self._connection()
        provider = ProviderDouble(capacity=100)
        with TemporaryDirectory() as directory:
            matrix_path = Path(directory) / "vectors.npy"
            build_vector_index(connection, matrix_path, provider)
            connection.execute("UPDATE vector_segments SET matrix_row = 2 WHERE matrix_row = 1")
            with self.assertRaises(VectorError):
                validate_vector_index(connection, matrix_path)
            connection.execute("UPDATE vector_segments SET matrix_row = 1 WHERE matrix_row = 2")
            connection.execute("UPDATE vector_segments SET unit_id = 2 WHERE target_kind = 'semantic_unit'")
            with self.assertRaises(VectorError):
                validate_vector_index(connection, matrix_path)

    def test_semantic_object_identity_mismatch_is_rejected(self):
        connection = self._connection()
        with TemporaryDirectory() as directory:
            matrix_path = Path(directory) / "vectors.npy"
            build_vector_index(connection, matrix_path, ProviderDouble(capacity=100))
            connection.execute(
                "UPDATE vector_segments SET source_object_uuid = 'empty-object' WHERE target_kind = 'semantic_object'"
            )
            with self.assertRaisesRegex(VectorError, "semantic-object"):
                validate_vector_index(connection, matrix_path)

    def test_impossible_typed_target_columns_are_rejected(self):
        connection = self._connection()
        with TemporaryDirectory() as directory:
            matrix_path = Path(directory) / "vectors.npy"
            build_vector_index(connection, matrix_path, ProviderDouble(capacity=100))
            self._replace_vector_segments_without_checks(
                connection, source_object_uuid="unit-object", unit_id=1, rewrite_object=False
            )
            with self.assertRaisesRegex(VectorError, "semantic-unit"):
                validate_vector_index(connection, matrix_path)

    def test_foreign_key_violation_is_rejected(self):
        connection = self._connection()
        with TemporaryDirectory() as directory:
            matrix_path = Path(directory) / "vectors.npy"
            build_vector_index(connection, matrix_path, ProviderDouble(capacity=100))
            self._replace_vector_segments_without_checks(connection, source_object_uuid="missing-object")
            with self.assertRaisesRegex(VectorError, "foreign-key"):
                validate_vector_index(connection, matrix_path)

    @staticmethod
    def _replace_vector_segments_without_checks(connection, *, source_object_uuid, unit_id=None, rewrite_object=True):
        rows = connection.execute(
            "SELECT matrix_row, target_kind, target_identity_json, segment_ordinal, segment_text, unit_id, source_object_uuid FROM vector_segments"
        ).fetchall()
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute("DROP TABLE vector_segments")
        connection.execute(
            """CREATE TABLE vector_segments (
                matrix_row INTEGER PRIMARY KEY,
                target_kind TEXT NOT NULL,
                target_identity_json TEXT NOT NULL,
                segment_ordinal INTEGER NOT NULL,
                segment_text TEXT NOT NULL,
                unit_id INTEGER,
                source_object_uuid TEXT,
                UNIQUE (target_kind, target_identity_json, segment_ordinal),
                FOREIGN KEY (unit_id) REFERENCES canonical_units(unit_id),
                FOREIGN KEY (source_object_uuid) REFERENCES canonical_objects(source_object_uuid)
            )"""
        )
        rewritten = []
        for row in rows:
            if row[1] == "semantic_object" and rewrite_object:
                row = (*row[:5], row[5], source_object_uuid)
            elif row[1] == "semantic_unit" and unit_id is not None:
                row = (*row[:5], unit_id, source_object_uuid)
            rewritten.append(row)
        connection.executemany("INSERT INTO vector_segments VALUES (?, ?, ?, ?, ?, ?, ?)", rewritten)
        connection.commit()
        connection.execute("PRAGMA foreign_keys = ON")

    def test_nonproduction_provider_cannot_publish_completed_state(self):
        connection = self._connection()
        provider = ProviderDouble(capacity=100)
        provider.contract = EmbeddingContract("test-model", "test-provider", 1024, "float32", "l2", "cosine", 100)
        with TemporaryDirectory() as directory:
            with self.assertRaises(VectorError):
                build_vector_index(connection, Path(directory) / "vectors.npy", provider)

    def test_ollama_capacity_classification_requires_exact_response(self):
        provider = OllamaEmbeddingProvider.__new__(OllamaEmbeddingProvider)
        provider.model = "qwen3-embedding:0.6b"
        provider.base_url = "http://unused"
        provider.timeout = 1
        provider.contract = ProviderDouble().contract
        cases = [
            ("the input length exceeds the context length", True, 400),
            ("input length is malformed", False, 400),
            ("token length failed", False, 500),
        ]
        for message, expected_capacity, status in cases:
            with self.subTest(message=message):
                with patch("ugh_parser.vector._post_json", side_effect=EmbeddingProviderError(message, status=status)):
                    with self.assertRaises(EmbeddingProviderError) as raised:
                        provider.embed("x", truncate=False)
                self.assertEqual(raised.exception.capacity_exceeded, expected_capacity)

    def test_provider_contract_mismatch_is_rejected(self):
        connection = self._connection()
        provider = ProviderDouble(capacity=100)
        with TemporaryDirectory() as directory:
            matrix_path = Path(directory) / "vectors.npy"
            build_vector_index(connection, matrix_path, provider)
            mismatch = ProviderDouble(capacity=100, identity="different-provider")
            with self.assertRaises(VectorError):
                vector_lookup(connection, matrix_path, "query", mismatch)

    def test_matrix_promotion_failure_restores_previous_completed_pair(self):
        connection = self._connection()
        provider = ProviderDouble(capacity=100)
        with TemporaryDirectory() as directory:
            matrix_path = Path(directory) / "vectors.npy"
            build_vector_index(connection, matrix_path, provider)
            old_bytes = matrix_path.read_bytes()
            old_digest = connection.execute(
                "SELECT matrix_sha256 FROM vector_build_contract WHERE contract_id = 1"
            ).fetchone()[0]
            real_replace = os.replace
            failed = False

            def fail_new_promotion(source, destination):
                nonlocal failed
                if Path(source).name.startswith(".vectors.") and Path(destination) == matrix_path and not failed:
                    failed = True
                    raise OSError("injected matrix promotion failure")
                return real_replace(source, destination)

            with patch("ugh_parser.vector.os.replace", side_effect=fail_new_promotion):
                with self.assertRaises(OSError):
                    build_vector_index(connection, matrix_path, provider)
            self.assertEqual(matrix_path.read_bytes(), old_bytes)
            self.assertEqual(
                connection.execute("SELECT matrix_sha256 FROM vector_build_contract WHERE contract_id = 1").fetchone()[0],
                old_digest,
            )
            validate_vector_index(connection, matrix_path)
