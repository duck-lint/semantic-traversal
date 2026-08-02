from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from semantic_traversal.config import load_runtime_config
from semantic_traversal.embeddings import EmbeddingResponse, UnavailableEmbeddingBackend, canonical_embedding_identity, embedding_identity_hash
from semantic_traversal.ingest import IngestSourceRoot, run_ingest
from semantic_traversal.runtime import _merge_candidates, _order_temporal_candidates, _semantic_traversal, _temporal_candidates
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
        latest, _, _ = _temporal_candidates(
            connection=connection, config=self.config, chunk_rows=rows,
            layer={"mode": "latest", "anchor_types": ["journal_entry"], "authorities": ["explicit_primary"], "limit": 10},
            lexical_queries=["alpha"], semantic_queries=[], literal_terms=[], scope_filters={}, embedding_backend=_UnavailableEmbedding(),
        )
        before, _, _ = _temporal_candidates(
            connection=connection, config=self.config, chunk_rows=rows,
            layer={"mode": "before", "before": "2021", "anchor_types": ["journal_entry"], "authorities": ["explicit_primary"], "limit": 10},
            lexical_queries=["alpha"], semantic_queries=[], literal_terms=[], scope_filters={}, embedding_backend=_UnavailableEmbedding(),
        )
        after, _, _ = _temporal_candidates(
            connection=connection, config=self.config, chunk_rows=rows,
            layer={"mode": "after", "after": "2021", "anchor_types": ["journal_entry"], "authorities": ["explicit_primary"], "limit": 10},
            lexical_queries=["alpha"], semantic_queries=[], literal_terms=[], scope_filters={}, embedding_backend=_UnavailableEmbedding(),
        )
        between, _, _ = _temporal_candidates(
            connection=connection, config=self.config, chunk_rows=rows,
            layer={"mode": "between", "start": "2020", "end": "2020-12-31T23:59:59.999999Z", "anchor_types": ["journal_entry"], "authorities": ["explicit_primary"], "limit": 10},
            lexical_queries=["alpha"], semantic_queries=[], literal_terms=[], scope_filters={}, embedding_backend=_UnavailableEmbedding(),
        )
        self.assertEqual(latest[0]["chunk_id"], "c-new")
        self.assertEqual([item["chunk_id"] for item in before], ["c-old"])
        self.assertEqual([item["chunk_id"] for item in after], ["c-new"])
        self.assertEqual([item["chunk_id"] for item in between], ["c-old"])
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

    def test_mode_specific_governing_anchor_preserves_all_qualifying_provenance(self) -> None:
        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        connection.executescript(
            """
            CREATE TABLE chunks (chunk_id TEXT, note_id TEXT, source_root_label TEXT, source_root_path TEXT, relative_path TEXT, note_title TEXT, frontmatter_semantics_json TEXT, section_label TEXT, paragraph_text TEXT, chunk_hash TEXT);
            CREATE VIRTUAL TABLE chunks_fts USING fts5(chunk_id UNINDEXED, paragraph_text, note_title, section_label, relative_path, metadata);
            CREATE TABLE temporal_anchors (anchor_id TEXT, note_id TEXT, chunk_id TEXT, anchor_type TEXT, canonical_start TEXT, canonical_end TEXT, precision TEXT, source_field TEXT, original_source_value TEXT, authority TEXT, parsing_status TEXT, conflict_group TEXT, unresolved INTEGER, diagnostic_reason TEXT);
            """
        )
        row = {"chunk_id": "c-multi", "note_id": "n-multi", "source_root_label": "vault", "source_root_path": "", "relative_path": "multi.md", "note_title": "Multi", "frontmatter_semantics_json": "{}", "section_label": "Body", "paragraph_text": "alpha", "chunk_hash": "h-multi"}
        connection.execute("INSERT INTO chunks VALUES (:chunk_id,:note_id,:source_root_label,:source_root_path,:relative_path,:note_title,:frontmatter_semantics_json,:section_label,:paragraph_text,:chunk_hash)", row)
        connection.execute("INSERT INTO chunks_fts VALUES (:chunk_id,:paragraph_text,:note_title,:section_label,:relative_path,:frontmatter_semantics_json)", row)
        connection.executemany(
            "INSERT INTO temporal_anchors VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                ("a-early", "n-multi", None, "journal_entry", "2020-01-01T00:00:00Z", "2020-12-31T23:59:59.999999Z", "year", "journal_entry_date", "2020", "explicit_primary", "valid", None, 0, None),
                ("a-late", "n-multi", None, "journal_entry", "2025-01-01T00:00:00Z", "2025-12-31T23:59:59.999999Z", "year", "journal_entry_date", "2025", "explicit_primary", "valid", None, 0, None),
            ],
        )

        def run(layer: dict[str, object]) -> dict[str, object]:
            candidates, _, _ = _temporal_candidates(
                connection=connection, config=self.config, chunk_rows=[row], layer=layer,
                lexical_queries=["alpha"], semantic_queries=[], literal_terms=[], scope_filters={},
                embedding_backend=_UnavailableEmbedding(),
            )
            return candidates[0]

        common = {"anchor_types": ["journal_entry"], "authorities": ["explicit_primary"], "limit": 10}
        earliest = run({**common, "mode": "earliest"})
        latest = run({**common, "mode": "latest"})
        ascending = run({**common, "mode": "ordered", "direction": "ascending"})
        descending = run({**common, "mode": "ordered", "direction": "descending"})
        before = run({**common, "mode": "before", "before": "2030"})
        after = run({**common, "mode": "after", "after": "2010"})
        between = run({**common, "mode": "between", "start": "2019", "end": "2026"})

        self.assertEqual(earliest["temporal_governing_anchor_id"], "a-early")
        self.assertEqual(latest["temporal_governing_anchor_id"], "a-late")
        self.assertEqual(ascending["temporal_governing_anchor_id"], "a-early")
        self.assertEqual(descending["temporal_governing_anchor_id"], "a-late")
        self.assertEqual(before["temporal_governing_anchor_id"], "a-late")
        self.assertEqual(after["temporal_governing_anchor_id"], "a-early")
        self.assertEqual(between["temporal_governing_anchor_id"], "a-early")
        self.assertEqual(earliest["temporal_anchor_ids"], ["a-early", "a-late"])
        self.assertEqual([item["anchor_id"] for item in latest["temporal_provenance"]], ["a-early", "a-late"])
        self.assertEqual(earliest["relation_status"], "definite")
        connection.close()

    def test_relation_modes_rank_ordinal_relevance_before_temporal_value(self) -> None:
        candidates = [
            {"chunk_id": "weak-early", "internal_ordinal_relevance": 0.1, "temporal_governing_canonical_start": "2020-01-01", "temporal_governing_canonical_end": "2020-12-31"},
            {"chunk_id": "strong-late", "internal_ordinal_relevance": 0.9, "temporal_governing_canonical_start": "2022-01-01", "temporal_governing_canonical_end": "2022-12-31"},
        ]
        self.assertEqual([item["chunk_id"] for item in _order_temporal_candidates(candidates, mode="before", direction="ascending")], ["strong-late", "weak-early"])
        self.assertEqual([item["chunk_id"] for item in _order_temporal_candidates(candidates, mode="between", direction="ascending")], ["strong-late", "weak-early"])
        reversed_strength = [dict(candidates[0], internal_ordinal_relevance=0.9), dict(candidates[1], internal_ordinal_relevance=0.1)]
        self.assertEqual([item["chunk_id"] for item in _order_temporal_candidates(reversed_strength, mode="after", direction="ascending")], ["weak-early", "strong-late"])
        equal = [dict(candidates[1], internal_ordinal_relevance=0.5), dict(candidates[0], internal_ordinal_relevance=0.5)]
        self.assertEqual([item["chunk_id"] for item in _order_temporal_candidates(equal, mode="between", direction="ascending")], ["weak-early", "strong-late"])

    def test_temporal_provenance_survives_merge_when_lexical_wins(self) -> None:
        temporal = {
            "chunk_id": "shared", "note_id": "n1", "chunk_hash": "h", "selection_source": "temporal",
            "source_layers": ["temporal"], "score": 1.0,
            "temporal_anchor_ids": ["a-early", "a-late"],
            "temporal_provenance": [{"anchor_id": "a-early"}, {"anchor_id": "a-late"}],
            "temporal_governing_anchor_id": "a-late",
            "temporal_governing_canonical_start": "2025-01-01T00:00:00Z",
            "temporal_governing_canonical_end": "2025-12-31T23:59:59.999999Z",
        }
        lexical = {
            "chunk_id": "shared", "note_id": "n1", "chunk_hash": "h", "selection_source": "lexical",
            "source_layers": ["lexical"], "score": 2.0,
        }
        merged = _merge_candidates([lexical], [], [], self.config, temporal_candidates=[temporal])
        self.assertEqual(merged[0]["selection_source"], "lexical")
        self.assertEqual(merged[0]["source_layers"], ["lexical", "temporal"])
        self.assertEqual(merged[0]["temporal_governing_anchor_id"], "a-late")
        self.assertEqual(merged[0]["temporal_anchor_ids"], ["a-early", "a-late"])

    def test_same_chunk_temporal_deduplication_retains_both_subject_provenances(self) -> None:
        base = {
            "chunk_id": "shared", "note_id": "n-shared", "chunk_hash": "h-shared", "selection_source": "temporal",
            "source_layers": ["temporal"], "score": 1.0, "temporal_mode": "earliest",
            "temporal_anchor_ids": ["anchor-shared"], "temporal_governing_anchor_id": "anchor-shared",
            "temporal_governing_canonical_start": "2020-01-01T00:00:00Z", "temporal_governing_canonical_end": "2020-12-31T23:59:59.999999Z",
        }
        subject_a = {**base, "temporal_subject": "amber", "temporal_subjects": ["amber"], "temporal_provenance": [{"anchor_id": "anchor-shared", "subject": "amber"}]}
        subject_b = {**base, "temporal_subject": "beryl", "temporal_subjects": ["beryl"], "temporal_provenance": [{"anchor_id": "anchor-shared", "subject": "beryl"}]}
        merged = _merge_candidates([], [], [], self.config, temporal_candidates=[subject_a, subject_b])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["temporal_subjects"], ["amber", "beryl"])
        self.assertEqual({item["subject"] for item in merged[0]["temporal_provenance"]}, {"amber", "beryl"})

    def test_compiler_to_selected_packet_preserves_subject_contexts(self) -> None:
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
            {"chunk_id": "c-unrelated", "note_id": "n-unrelated", "source_root_label": "fixture", "source_root_path": "", "relative_path": "unrelated.md", "note_title": "Unrelated", "frontmatter_semantics_json": "{}", "section_label": "Body", "paragraph_text": "unrelated global chronology", "chunk_hash": "h-unrelated"},
            {"chunk_id": "c-a", "note_id": "n-a", "source_root_label": "fixture", "source_root_path": "", "relative_path": "amber.md", "note_title": "Amber", "frontmatter_semantics_json": "{}", "section_label": "Body", "paragraph_text": "amber evidence", "chunk_hash": "h-a"},
            {"chunk_id": "c-b", "note_id": "n-b", "source_root_label": "fixture", "source_root_path": "", "relative_path": "beryl.md", "note_title": "Beryl", "frontmatter_semantics_json": "{}", "section_label": "Body", "paragraph_text": "beryl evidence", "chunk_hash": "h-b"},
        ]
        connection.executemany("INSERT INTO chunks VALUES (:chunk_id,:note_id,:source_root_label,:source_root_path,:relative_path,:note_title,:frontmatter_semantics_json,:section_label,:paragraph_text,:chunk_hash)", rows)
        connection.executemany("INSERT INTO chunks_fts VALUES (:chunk_id,:paragraph_text,:note_title,:section_label,:relative_path,:frontmatter_semantics_json)", rows)
        connection.executemany(
            "INSERT INTO temporal_anchors VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                ("a-unrelated", "n-unrelated", None, "journal_entry", "2000-01-01T00:00:00Z", "2000-12-31T23:59:59.999999Z", "year", "journal_entry_date", "2000", "explicit_primary", "valid", None, 0, None),
                ("a-a", "n-a", None, "journal_entry", "2010-01-01T00:00:00Z", "2010-12-31T23:59:59.999999Z", "year", "journal_entry_date", "2010", "explicit_primary", "valid", None, 0, None),
                ("a-b", "n-b", None, "journal_entry", "2020-01-01T00:00:00Z", "2020-12-31T23:59:59.999999Z", "year", "journal_entry_date", "2020", "explicit_primary", "valid", None, 0, None),
            ],
        )
        packet = {
            "raw_user_input": "compare amber and beryl development",
            "query": "compare amber and beryl development",
            "concepts": ["amber", "beryl"], "entities": [], "relations": [], "resolved_referents": ["amber", "beryl"], "limitations": [],
            "planner_diagnostics": {"plan_executability": {"status": "executable"}},
            "planner_retrieval_plan": {
                "intent_type": "semantic_traversal", "concepts": ["amber", "beryl"], "resolved_referents": ["amber", "beryl"],
                "literal_terms": [], "evidence_requirements": ["chronology"], "semantic_queries": ["amber development", "beryl development"],
                "lexical_queries": ["amber development", "beryl development"], "graph_seeds": [],
                "retrieval_layers": [{"operator": "temporal_retrieve", "required": True, "mode": "earliest", "limit": 1}],
            },
        }
        try:
            manifest, retrieval_packet = _semantic_traversal(
                connection=connection, config=self.config, semantic_compiler_packet=packet,
                prior_thread_state={}, embedding_backend=_UnavailableEmbedding(), resource_inventory_summary={},
            )
        finally:
            connection.close()
        bound = manifest["bound_retrieval_plan"]
        temporal = manifest["layer_manifests"]["temporal"]
        self.assertEqual(bound["resolved_referents"], ["amber", "beryl"])
        self.assertEqual(manifest["resolved_referent_propagation"]["status"], "preserved")
        temporal_diagnostics = temporal["diagnostics"]
        self.assertFalse(temporal_diagnostics["searches_full_temporal_projection"])
        self.assertEqual(temporal_diagnostics["context_source"], "semantic_closure")
        self.assertIn("lexical_chunk_search", [layer["operator"] for layer in manifest["bound_retrieval_plan"]["retrieval_layers"]])
        self.assertEqual(temporal_diagnostics["subject_count"], 2)
        self.assertEqual(temporal_diagnostics["satisfied_subject_count"], 2)
        self.assertEqual(temporal_diagnostics["missing_subject_count"], 0)
        self.assertTrue(temporal_diagnostics["one_per_subject_reservation_satisfied"])
        selected_ids = {item["chunk_id"] for item in retrieval_packet["selected_chunks"]}
        self.assertIn("c-a", selected_ids)
        self.assertIn("c-b", selected_ids)
        self.assertNotIn("c-unrelated", selected_ids)

    def test_query_level_temporal_context_does_not_claim_named_subject_coverage(self) -> None:
        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        connection.executescript(
            """
            CREATE TABLE chunks (chunk_id TEXT, note_id TEXT, source_root_label TEXT, source_root_path TEXT, relative_path TEXT, note_title TEXT, frontmatter_semantics_json TEXT, section_label TEXT, paragraph_text TEXT, chunk_hash TEXT);
            CREATE VIRTUAL TABLE chunks_fts USING fts5(chunk_id UNINDEXED, paragraph_text, note_title, section_label, relative_path, metadata);
            CREATE TABLE temporal_anchors (anchor_id TEXT, note_id TEXT, chunk_id TEXT, anchor_type TEXT, canonical_start TEXT, canonical_end TEXT, precision TEXT, source_field TEXT, original_source_value TEXT, authority TEXT, parsing_status TEXT, conflict_group TEXT, unresolved INTEGER, diagnostic_reason TEXT);
            """
        )
        row = {"chunk_id": "c-query", "note_id": "n-query", "source_root_label": "fixture", "source_root_path": "", "relative_path": "query.md", "note_title": "Query", "frontmatter_semantics_json": "{}", "section_label": "Body", "paragraph_text": "query context evidence", "chunk_hash": "h-query"}
        connection.execute("INSERT INTO chunks VALUES (:chunk_id,:note_id,:source_root_label,:source_root_path,:relative_path,:note_title,:frontmatter_semantics_json,:section_label,:paragraph_text,:chunk_hash)", row)
        connection.execute("INSERT INTO chunks_fts VALUES (:chunk_id,:paragraph_text,:note_title,:section_label,:relative_path,:frontmatter_semantics_json)", row)
        connection.execute("INSERT INTO temporal_anchors VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", ("a-query", "n-query", None, "journal_entry", "2020-01-01T00:00:00Z", "2020-12-31T23:59:59.999999Z", "year", "journal_entry_date", "2020", "explicit_primary", "valid", None, 0, None))
        candidates, _, diagnostics = _temporal_candidates(connection=connection, config=self.config, chunk_rows=[row], layer={"mode": "earliest", "anchor_types": ["journal_entry"], "authorities": ["explicit_primary"], "limit": 1}, lexical_queries=["query context"], semantic_queries=[], literal_terms=[], scope_filters={}, embedding_backend=_UnavailableEmbedding(), resolved_referents=[])
        self.assertEqual(len(candidates), 1)
        self.assertEqual(diagnostics["subject_count"], 0)
        self.assertEqual(diagnostics["query_context_count"], 1)
        self.assertEqual(diagnostics["satisfied_subject_count"], 0)
        self.assertEqual(diagnostics["missing_subject_count"], 0)
        self.assertFalse(diagnostics["one_per_subject_reservation_satisfied"])
        self.assertEqual(diagnostics["per_subject"][0]["subject"], None)
        connection.close()

    def test_typed_closure_executes_direct_note_graph_hop_and_temporal_path(self) -> None:
        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        connection.executescript(
            """
            CREATE TABLE chunks (chunk_id TEXT, note_id TEXT, source_root_label TEXT, source_root_path TEXT, relative_path TEXT, note_title TEXT, frontmatter_semantics_json TEXT, section_label TEXT, paragraph_text TEXT, chunk_hash TEXT);
            CREATE VIRTUAL TABLE chunks_fts USING fts5(chunk_id UNINDEXED, paragraph_text, note_title, section_label, relative_path, metadata);
            CREATE TABLE temporal_anchors (anchor_id TEXT, note_id TEXT, chunk_id TEXT, anchor_type TEXT, canonical_start TEXT, canonical_end TEXT, precision TEXT, source_field TEXT, original_source_value TEXT, authority TEXT, parsing_status TEXT, conflict_group TEXT, unresolved INTEGER, diagnostic_reason TEXT);
            CREATE TABLE graph_nodes (node_id TEXT, node_type TEXT, label TEXT, ref_id TEXT, metadata_json TEXT);
            CREATE TABLE graph_edges (source_node_id TEXT, target_node_id TEXT, edge_type TEXT, metadata_json TEXT);
            """
        )
        rows = [
            {"chunk_id": "c-seed", "note_id": "n-seed", "source_root_label": "fixture", "source_root_path": "", "relative_path": "seed.md", "note_title": "Seed", "frontmatter_semantics_json": "{}", "section_label": "Body", "paragraph_text": "amber signal", "chunk_hash": "h-seed"},
            {"chunk_id": "c-hop", "note_id": "n-hop", "source_root_label": "fixture", "source_root_path": "", "relative_path": "hop.md", "note_title": "Hop", "frontmatter_semantics_json": "{}", "section_label": "Body", "paragraph_text": "linked development", "chunk_hash": "h-hop"},
            {"chunk_id": "c-unrelated", "note_id": "n-old", "source_root_label": "fixture", "source_root_path": "", "relative_path": "old.md", "note_title": "Old", "frontmatter_semantics_json": "{}", "section_label": "Body", "paragraph_text": "unrelated chronology", "chunk_hash": "h-old"},
        ]
        connection.executemany("INSERT INTO chunks VALUES (:chunk_id,:note_id,:source_root_label,:source_root_path,:relative_path,:note_title,:frontmatter_semantics_json,:section_label,:paragraph_text,:chunk_hash)", rows)
        connection.executemany("INSERT INTO chunks_fts VALUES (:chunk_id,:paragraph_text,:note_title,:section_label,:relative_path,:frontmatter_semantics_json)", rows)
        connection.executemany("INSERT INTO graph_nodes VALUES (?,?,?,?,?)", [("note::n-seed", "note", "Seed", "n-seed", "{}"), ("note::n-hop", "note", "Hop", "n-hop", "{}")])
        connection.execute("INSERT INTO graph_edges VALUES (?,?,?,?)", ("note::n-seed", "note::n-hop", "note_links_note", "{}"))
        connection.executemany(
            "INSERT INTO temporal_anchors VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                ("a-seed", "n-seed", None, "journal_entry", "2020-01-01T00:00:00Z", "2020-12-31T23:59:59.999999Z", "year", "journal_entry_date", "2020", "explicit_primary", "valid", None, 0, None),
                ("a-hop", "n-hop", None, "journal_entry", "2010-01-01T00:00:00Z", "2010-12-31T23:59:59.999999Z", "year", "journal_entry_date", "2010", "explicit_primary", "valid", None, 0, None),
                ("a-old", "n-old", None, "journal_entry", "2000-01-01T00:00:00Z", "2000-12-31T23:59:59.999999Z", "year", "journal_entry_date", "2000", "explicit_primary", "valid", None, 0, None),
            ],
        )
        packet = {
            "raw_user_input": "amber chronology",
            "query": "amber chronology",
            "concepts": ["amber"], "entities": [], "relations": [], "resolved_referents": ["amber"], "limitations": [],
            "planner_diagnostics": {"plan_executability": {"status": "executable"}},
            "planner_retrieval_plan": {
                "intent_type": "semantic_traversal", "concepts": ["amber"], "resolved_referents": ["amber"],
                "literal_terms": [], "evidence_requirements": ["chronology"], "semantic_queries": ["amber"],
                "lexical_queries": ["amber"], "graph_seeds": [],
                "retrieval_layers": [{"operator": "temporal_retrieve", "required": True, "mode": "earliest", "limit": 1}],
            },
        }
        manifest, retrieval_packet = _semantic_traversal(
            connection=connection, config=self.config, semantic_compiler_packet=packet,
            prior_thread_state={}, embedding_backend=_UnavailableEmbedding(), resource_inventory_summary={},
        )
        graph = manifest["graph_traversal"]
        temporal = manifest["layer_manifests"]["temporal"]["diagnostics"]
        self.assertEqual(manifest["bound_retrieval_plan"]["resolved_referents"], ["amber"])
        self.assertEqual(graph["hydrated_seed_note_count"], 1)
        self.assertEqual(graph["expanded_note_count"], 1)
        self.assertTrue(any(hop["edge_type"] == "note_links_note" for hop in graph["traversal_hops"]))
        self.assertEqual(temporal["subject_count"], 1)
        self.assertEqual(temporal["satisfied_subject_count"], 1)
        self.assertEqual(temporal["per_subject"][0]["closure_candidate_count"], 2)
        self.assertIn("c-hop", manifest["layer_manifests"]["temporal"]["selected_chunk_ids"])
        self.assertNotIn("c-unrelated", {item["chunk_id"] for item in retrieval_packet["selected_chunks"]})
        connection.close()
