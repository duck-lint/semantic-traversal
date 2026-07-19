from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from semantic_traversal.config import load_runtime_config
from semantic_traversal.embeddings import EmbeddingResponse, UnavailableEmbeddingBackend, canonical_embedding_identity, embedding_identity_hash
from semantic_traversal.ingest import IngestSourceRoot, run_ingest
from semantic_traversal.runtime import _temporal_candidates
from semantic_traversal.temporal import build_temporal_anchors, parse_temporal_value


class _UnavailableEmbedding:
    mode_name = "unavailable"

    def embed_query_text(self, query: str):  # pragma: no cover - temporal lexical tests do not call it
        raise AssertionError(query)


class _VectorEmbedding:
    mode_name = "test"
    identity_metadata = {"backend_mode": "test", "model": "test-model", "dimensions": 2, "normalize_embeddings": True, "encoding_strategy": "chunk_embedding_text_v1"}

    def embed_query_text(self, query: str) -> EmbeddingResponse:
        return EmbeddingResponse(vectors=[[1.0, 0.0]], metadata=self.identity_metadata, status="embedded")


class TemporalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = load_runtime_config(repo_root=Path(__file__).resolve().parents[1])

    def test_strict_precision_and_intervals(self) -> None:
        year = parse_temporal_value("2025")
        month = parse_temporal_value("2025-03")
        day = parse_temporal_value("2025-03-04")
        instant = parse_temporal_value("2025-03-04T12:00:00-07:00")
        self.assertEqual(year[2], "year")
        self.assertEqual(month[2], "month")
        self.assertEqual(day[2], "day")
        self.assertEqual(instant[2], "datetime")
        self.assertTrue(instant[0].endswith("Z"))
        with self.assertRaises(ValueError):
            parse_temporal_value("03/04/25")

    def test_multiple_meanings_and_same_type_conflicts_remain_visible(self) -> None:
        anchors, issues = build_temporal_anchors(
            note_id="vault::uuid::n1",
            frontmatter={"entry": "2025-01-01", "published": "2025-02", "entry_later": "2025-03-01"},
            mappings={
                "entry": {"anchor_type": "journal_entry", "authority": "explicit_primary"},
                "entry_later": {"anchor_type": "journal_entry", "authority": "explicit_primary"},
                "published": {"anchor_type": "publication", "authority": "explicit_secondary"},
            },
            ingest_run_id="run-1",
        )
        self.assertEqual(issues, [])
        self.assertEqual({anchor.anchor_type for anchor in anchors}, {"journal_entry", "publication"})
        self.assertEqual(len({anchor.conflict_group for anchor in anchors if anchor.conflict_group}), 1)
        self.assertEqual(len([anchor for anchor in anchors if anchor.anchor_type == "publication"]), 1)

    def test_temporal_earliest_requires_lexical_relevance_and_uses_full_temporal_pool(self) -> None:
        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        connection.executescript(
            """
            CREATE TABLE chunks (chunk_id TEXT, note_id TEXT, source_root_label TEXT, source_root_path TEXT, relative_path TEXT, note_title TEXT, frontmatter_semantics_json TEXT, section_label TEXT, paragraph_text TEXT, chunk_hash TEXT);
            CREATE VIRTUAL TABLE chunks_fts USING fts5(chunk_id UNINDEXED, paragraph_text, note_title, section_label, relative_path, metadata);
            CREATE TABLE temporal_anchors (anchor_id TEXT, note_id TEXT, chunk_id TEXT, anchor_type TEXT, canonical_start TEXT, canonical_end TEXT, precision TEXT, source_field TEXT, original_source_value TEXT, authority TEXT, parsing_status TEXT, conflict_group TEXT, unresolved INTEGER, diagnostic_reason TEXT);
            """
        )
        rows = [
            {"chunk_id": "c-old", "note_id": "n-old", "source_root_label": "vault", "source_root_path": "", "relative_path": "old.md", "note_title": "Old", "frontmatter_semantics_json": "{}", "section_label": "Body", "paragraph_text": "alpha origin", "chunk_hash": "h-old"},
            {"chunk_id": "c-new", "note_id": "n-new", "source_root_label": "vault", "source_root_path": "", "relative_path": "new.md", "note_title": "New", "frontmatter_semantics_json": "{}", "section_label": "Body", "paragraph_text": "alpha reinforcement", "chunk_hash": "h-new"},
            {"chunk_id": "c-noise", "note_id": "n-noise", "source_root_label": "vault", "source_root_path": "", "relative_path": "noise.md", "note_title": "Noise", "frontmatter_semantics_json": "{}", "section_label": "Body", "paragraph_text": "unrelated", "chunk_hash": "h-noise"},
        ]
        connection.executemany("INSERT INTO chunks VALUES (:chunk_id,:note_id,:source_root_label,:source_root_path,:relative_path,:note_title,:frontmatter_semantics_json,:section_label,:paragraph_text,:chunk_hash)", rows)
        connection.executemany("INSERT INTO chunks_fts VALUES (:chunk_id,:paragraph_text,:note_title,:section_label,:relative_path,:frontmatter_semantics_json)", rows)
        connection.executemany(
            "INSERT INTO temporal_anchors VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                ("a-old", "n-old", None, "journal_entry", "2020-01-01T00:00:00Z", "2020-12-31T23:59:59.999999Z", "year", "journal_entry_date", "2020", "explicit_primary", "valid", None, 0, None),
                ("a-new", "n-new", None, "journal_entry", "2025-01-01T00:00:00Z", "2025-12-31T23:59:59.999999Z", "year", "journal_entry_date", "2025", "explicit_primary", "valid", None, 0, None),
            ],
        )
        candidates, _, diagnostics = _temporal_candidates(
            connection=connection,
            config=self.config,
            chunk_rows=rows,
            layer={"mode": "earliest", "anchor_types": ["journal_entry"], "authorities": ["explicit_primary"], "limit": 10},
            lexical_queries=["alpha"], semantic_queries=[], literal_terms=[], scope_filters={}, embedding_backend=_UnavailableEmbedding(),
        )
        self.assertEqual([candidate["chunk_id"] for candidate in candidates], ["c-old", "c-new"])
        self.assertTrue(diagnostics["searches_full_temporal_projection"])
        self.assertEqual(candidates[0]["temporal_provenance"][0]["anchor_id"], "a-old")
        connection.close()

    def test_ingest_persists_temporal_projection_and_manifest_diagnostics(self) -> None:
        with tempfile.TemporaryDirectory(prefix="temporal-fixture-") as root_text:
            root = Path(root_text)
            (root / "entry.md").write_text(
                "---\nuuid: 019bc983-8065-7611-b57c-b9ef76d7b848\nnote_type: journal_entry\njournal_entry_date: 2025-09\n---\n\nA dated entry.\n",
                encoding="utf-8",
            )
            data_root = root / "data"
            result = run_ingest(
                repo_root=Path(__file__).resolve().parents[1],
                data_root=data_root,
                source_roots=(IngestSourceRoot(label="fixture", path=root),),
                embedding_backend=UnavailableEmbeddingBackend(reason="test"),
            )
            connection = sqlite3.connect(result.database_path)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM temporal_anchors").fetchone()[0], 1)
            row = connection.execute("SELECT anchor_type, precision, canonical_start, canonical_end, authority FROM temporal_anchors").fetchone()
            self.assertEqual(tuple(row), ("journal_entry", "month", "2025-09-01T00:00:00Z", "2025-09-30T23:59:59.999999Z", "explicit_primary"))
            connection.close()
            manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["temporal_index"]["valid_count"], 1)
            self.assertEqual(manifest["temporal_index"]["counts_by_precision"], {"month": 1})

    def test_temporal_vector_admission_preserves_identity_and_relevance_provenance(self) -> None:
        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        connection.executescript(
            """
            CREATE TABLE chunks (chunk_id TEXT, note_id TEXT, source_root_label TEXT, source_root_path TEXT, relative_path TEXT, note_title TEXT, frontmatter_semantics_json TEXT, section_label TEXT, paragraph_text TEXT, chunk_hash TEXT);
            CREATE VIRTUAL TABLE chunks_fts USING fts5(chunk_id UNINDEXED, paragraph_text, note_title, section_label, relative_path, metadata);
            CREATE TABLE chunk_vectors (chunk_id TEXT PRIMARY KEY, vector_json TEXT, vector_dimensions INTEGER, embedding_provider TEXT, embedding_model TEXT, embedding_identity_json TEXT, embedding_identity_hash TEXT, content_hash TEXT, last_indexed_run_id TEXT, updated_at TEXT);
            CREATE TABLE temporal_anchors (anchor_id TEXT, note_id TEXT, chunk_id TEXT, anchor_type TEXT, canonical_start TEXT, canonical_end TEXT, precision TEXT, source_field TEXT, original_source_value TEXT, authority TEXT, parsing_status TEXT, conflict_group TEXT, unresolved INTEGER, diagnostic_reason TEXT);
            """
        )
        row = {"chunk_id": "c-vector", "note_id": "n-vector", "source_root_label": "vault", "source_root_path": "", "relative_path": "vector.md", "note_title": "Vector", "frontmatter_semantics_json": "{}", "section_label": "Body", "paragraph_text": "", "chunk_hash": "h-vector"}
        connection.execute("INSERT INTO chunks VALUES (:chunk_id,:note_id,:source_root_label,:source_root_path,:relative_path,:note_title,:frontmatter_semantics_json,:section_label,:paragraph_text,:chunk_hash)", row)
        identity = canonical_embedding_identity(provider="test", model="test-model", dimensions=2, normalize_embeddings=True)
        connection.execute("INSERT INTO chunk_vectors VALUES (?,?,?,?,?,?,?,?,?,?)", ("c-vector", "[1.0, 0.0]", 2, "test", "test-model", json.dumps(identity), embedding_identity_hash(identity), "h-vector", "run", "now"))
        connection.execute("INSERT INTO temporal_anchors VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", ("a-vector", "n-vector", None, "journal_entry", "2025-01-01T00:00:00Z", "2025-12-31T23:59:59.999999Z", "year", "journal_entry_date", "2025", "explicit_primary", "valid", None, 0, None))
        candidates, _, diagnostics = _temporal_candidates(
            connection=connection, config=self.config, chunk_rows=[row],
            layer={"mode": "latest", "anchor_types": ["journal_entry"], "authorities": ["explicit_primary"], "limit": 5},
            lexical_queries=[], semantic_queries=["semantic"], literal_terms=[], scope_filters={}, embedding_backend=_VectorEmbedding(),
        )
        self.assertEqual([item["chunk_id"] for item in candidates], ["c-vector"])
        self.assertEqual(candidates[0]["internal_vector_rank"], 1)
        self.assertEqual(diagnostics["internal_surface_counts"]["vector"], 1)
        connection.close()
