from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from typing import Any

from semantic_traversal.config import load_runtime_config
from semantic_traversal.embeddings import EmbeddingResponse
from semantic_traversal.ingest import IngestSourceRoot, run_ingest


REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_ROOT = REPO_ROOT / "tests" / "fixtures" / "JOURNAL"


class FakeEmbeddingBackend:
    mode_name = "fake"

    def embed_texts(self, texts: list[str]) -> EmbeddingResponse:
        vectors = [[float(len(text)), float(text.lower().count("note")), float(text.lower().count("link"))] for text in texts]
        return EmbeddingResponse(vectors=vectors, metadata={"backend_mode": self.mode_name}, status="embedded")

    def embed_query_text(self, text: str) -> EmbeddingResponse:
        return self.embed_texts([text])


def _write_note(root: Path, relative_path: str, content: str) -> Path:
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content.strip() + "\n", encoding="utf-8")
    return path


def _load_rows(connection: sqlite3.Connection, query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    connection.row_factory = sqlite3.Row
    rows = connection.execute(query, params).fetchall()
    return [dict(row) for row in rows]


class IngestDataLayerTests(unittest.TestCase):
    def test_ingest_materializes_wikilink_graph_edges(self) -> None:
        config = load_runtime_config(repo_root=REPO_ROOT)
        with tempfile.TemporaryDirectory() as temp_dir:
            data_root = Path(temp_dir)
            source_root = data_root / "synthetic"
            _write_note(
                source_root,
                "A.md",
                """
                # A

                Links to [[B]].

                Links to [[C|see alias]].

                Links to [[B#Sleep Section|sleep alias]].
                """,
            )
            _write_note(
                source_root,
                "B.md",
                """
                # B

                ## Sleep Section

                B content paragraph.
                """,
            )
            _write_note(
                source_root,
                "C.md",
                """
                # C

                C content paragraph.
                """,
            )

            result = run_ingest(
                repo_root=REPO_ROOT,
                data_root=data_root,
                source_roots=(IngestSourceRoot(label="synthetic", path=source_root),),
                embedding_backend=FakeEmbeddingBackend(),
                config=config,
            )

            self.assertTrue(result.manifest_path.exists())
            self.assertTrue(result.database_path.exists())

            manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
            self.assertGreaterEqual(manifest["summary"]["note_count"], 3)
            self.assertGreaterEqual(manifest["summary"]["chunk_count"], 3)
            self.assertEqual(manifest["source_roots"][0]["label"], "synthetic")

            connection = sqlite3.connect(result.database_path)
            try:
                chunks = _load_rows(
                    connection,
                    """
                    SELECT chunk_id, note_id, source_root_label, relative_path, note_title, section_label, paragraph_text, chunk_hash
                    FROM chunks
                    ORDER BY chunk_id
                    """,
                )
                self.assertTrue(chunks)
                first_chunk = next(chunk for chunk in chunks if chunk["relative_path"] == "A.md")
                for field in (
                    "chunk_id",
                    "note_id",
                    "source_root_label",
                    "relative_path",
                    "note_title",
                    "section_label",
                    "paragraph_text",
                    "chunk_hash",
                ):
                    self.assertIn(field, first_chunk)

                vector_rows = _load_rows(connection, f"SELECT chunk_id, vector_json FROM {config.vector_table}")
                self.assertGreater(len(vector_rows), 0)

                note_nodes = _load_rows(
                    connection,
                    f"SELECT node_id, node_type, label, ref_id, metadata_json FROM {config.graph_nodes_table} WHERE node_type = 'note' ORDER BY label",
                )
                self.assertEqual([row["label"] for row in note_nodes], ["A", "B", "C"])

                edges = _load_rows(
                    connection,
                    f"SELECT source_node_id, target_node_id, edge_type, metadata_json FROM {config.graph_edges_table} WHERE edge_type = 'note_links_note' ORDER BY metadata_json",
                )
                self.assertGreaterEqual(len(edges), 3)

                edge_metadata = [json.loads(edge["metadata_json"]) for edge in edges]
                self.assertTrue(any(meta["target_note"] == "B" and meta["resolved"] for meta in edge_metadata))
                self.assertTrue(any(meta["target_note"] == "C" and meta["alias"] == "see alias" for meta in edge_metadata))
                self.assertTrue(
                    any(
                        meta["target_note"] == "B"
                        and meta["target_heading"] == "Sleep Section"
                        and meta["alias"] == "sleep alias"
                        for meta in edge_metadata
                    )
                )
            finally:
                connection.close()

    def test_existing_fixture_ingestion_smoke(self) -> None:
        config = load_runtime_config(repo_root=REPO_ROOT)
        with tempfile.TemporaryDirectory() as temp_dir:
            data_root = Path(temp_dir)
            result = run_ingest(
                repo_root=REPO_ROOT,
                data_root=data_root,
                source_roots=(IngestSourceRoot(label="tests-fixtures", path=FIXTURE_ROOT),),
                embedding_backend=FakeEmbeddingBackend(),
                config=config,
            )
            self.assertTrue(result.manifest_path.exists())
            self.assertTrue(result.database_path.exists())
            manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
            self.assertGreater(manifest["summary"]["note_count"], 0)
            self.assertGreater(manifest["summary"]["chunk_count"], 0)


if __name__ == "__main__":
    unittest.main()
