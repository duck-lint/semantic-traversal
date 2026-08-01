from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from semantic_traversal.embeddings import EmbeddingResponse
from semantic_traversal.ingest import IngestSourceRoot, _build_embedding_text_for_chunk, run_ingest
from semantic_traversal.runtime import _load_chunk_rows


class RecordingEmbeddingBackend:
    mode_name = "sentence_transformers"

    @property
    def identity_metadata(self) -> dict[str, object]:
        return {
            "backend_mode": self.mode_name,
            "model": "sentence-transformers/all-MiniLM-L6-v2",
            "dimensions": 2,
            "normalize_embeddings": True,
            "encoding_strategy": "chunk_embedding_text_v1",
        }

    def __init__(self) -> None:
        self.texts: list[str] = []

    def embed_texts(self, texts: list[str]) -> EmbeddingResponse:
        self.texts.extend(texts)
        return EmbeddingResponse(vectors=[[1.0, 0.0] for _ in texts], metadata=self.identity_metadata, status="embedded")

    def embed_query_text(self, text: str) -> EmbeddingResponse:
        return EmbeddingResponse(vectors=[[1.0, 0.0]], metadata=self.identity_metadata, status="embedded")


class SemanticChunkAlignmentTests(unittest.TestCase):
    def test_canonical_surface_is_stable_and_excludes_unadmitted_fields(self) -> None:
        first = _build_embedding_text_for_chunk(note_title="Entry", relative_path="Journal/entry.md", section_path=("Body",), frontmatter_semantics={"z": ["b", "a"], "a": True}, semantic_unit_text="Body text")
        second = _build_embedding_text_for_chunk(note_title="Entry", relative_path="Journal/entry.md", section_path=("Body",), frontmatter_semantics={"a": True, "z": ["b", "a"]}, semantic_unit_text="Body text")
        self.assertEqual(first, second)
        self.assertIn('"a":true', first)
        self.assertIn('"z":["b","a"]', first)
        self.assertNotIn("uuid", first)
        self.assertIn("Content:\nBody text", first)

    def test_admitted_metadata_changes_chunk_identity_and_embedding_input(self) -> None:
        with tempfile.TemporaryDirectory() as source_text, tempfile.TemporaryDirectory() as data_text:
            source = Path(source_text)
            note = source / "Journal" / "entry.md"
            note.parent.mkdir(parents=True)
            note.write_text("---\nuuid: 019bc983-8065-7611-b57c-b9ef76d7b848\nnote_type: journal_entry\nbook_read_today: false\n---\n\nBody text.\n", encoding="utf-8")
            backend = RecordingEmbeddingBackend()
            repo_root = Path(__file__).resolve().parents[1]
            first = run_ingest(repo_root=repo_root, data_root=Path(data_text), source_roots=(IngestSourceRoot(label="fixture", path=source),), embedding_backend=backend)
            connection = sqlite3.connect(first.database_path)
            first_row = connection.execute("SELECT chunk_hash, embedding_text FROM chunks").fetchone()
            connection.close()
            note.write_text(note.read_text(encoding="utf-8").replace("false", "true"), encoding="utf-8")
            second = run_ingest(repo_root=repo_root, data_root=Path(data_text), source_roots=(IngestSourceRoot(label="fixture", path=source),), embedding_backend=backend)
            connection = sqlite3.connect(second.database_path)
            second_row = connection.execute("SELECT chunk_hash, embedding_text FROM chunks").fetchone()
            connection.close()
            self.assertNotEqual(first_row[0], second_row[0])
            self.assertNotEqual(first_row[1], second_row[1])
            self.assertTrue(any('"book_read_today":false' in text for text in backend.texts))
            self.assertTrue(any('"book_read_today":true' in text for text in backend.texts))
            self.assertEqual(second.updated_chunks, 1)

    def test_non_admitted_metadata_does_not_change_chunk_identity(self) -> None:
        with tempfile.TemporaryDirectory() as source_text, tempfile.TemporaryDirectory() as data_text:
            source = Path(source_text)
            note = source / "entry.md"
            note.write_text("---\nuuid: 019bc983-8065-7611-b57c-b9ef76d7b848\nnote_type: journal_entry\nprivate_field: one\n---\n\nBody text.\n", encoding="utf-8")
            repo_root = Path(__file__).resolve().parents[1]
            first = run_ingest(repo_root=repo_root, data_root=Path(data_text), source_roots=(IngestSourceRoot(label="fixture", path=source),), embedding_backend=RecordingEmbeddingBackend())
            connection = sqlite3.connect(first.database_path)
            first_hash = connection.execute("SELECT chunk_hash FROM chunks").fetchone()[0]
            connection.close()
            note.write_text(note.read_text(encoding="utf-8").replace("private_field: one", "private_field: two"), encoding="utf-8")
            second = run_ingest(repo_root=repo_root, data_root=Path(data_text), source_roots=(IngestSourceRoot(label="fixture", path=source),), embedding_backend=RecordingEmbeddingBackend())
            connection = sqlite3.connect(second.database_path)
            second_hash = connection.execute("SELECT chunk_hash FROM chunks").fetchone()[0]
            connection.close()
            self.assertEqual(first_hash, second_hash)
            self.assertEqual(second.unchanged_chunks, 1)

    def test_selected_surface_is_structured_and_inventory_is_generic(self) -> None:
        with tempfile.TemporaryDirectory() as source_text, tempfile.TemporaryDirectory() as data_text:
            source = Path(source_text)
            note = source / "entry.md"
            note.write_text("---\nuuid: 019bc983-8065-7611-b57c-b9ef76d7b848\nnote_type: journal_entry\nprivate_field: secret\n---\n\nBody text.\n", encoding="utf-8")
            result = run_ingest(repo_root=Path(__file__).resolve().parents[1], data_root=Path(data_text), source_roots=(IngestSourceRoot(label="fixture", path=source),), embedding_backend=RecordingEmbeddingBackend())
            connection = sqlite3.connect(result.database_path)
            connection.row_factory = sqlite3.Row
            row = _load_chunk_rows(connection)[0]
            inventory = json.loads(connection.execute("SELECT payload_json FROM resource_inventory_snapshots").fetchone()[0])
            connection.close()
        self.assertEqual(
            json.loads(row["frontmatter_semantics_json"]),
            {"note_type": "journal_entry", "uuid": "019bc983-8065-7611-b57c-b9ef76d7b848"},
        )
        self.assertIn("semantic_chunk_surface", inventory)
        self.assertNotIn("private_field", json.dumps(inventory))


if __name__ == "__main__":
    unittest.main()
