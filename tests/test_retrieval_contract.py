from __future__ import annotations

import sqlite3
import unittest
from pathlib import Path

from semantic_traversal.config import load_runtime_config
from semantic_traversal.runtime import _chunk_matches_scope, _coverage_report, _exact_candidates, _lexical_candidates, _vector_candidates


class _FakeEmbeddingBackend:
    mode_name = "test"

    def __init__(self, vectors: dict[str, list[float]]) -> None:
        self.vectors = vectors

    def embed_query_text(self, text: str):
        from semantic_traversal.embeddings import EmbeddingResponse

        vector = self.vectors.get(text)
        return EmbeddingResponse(vectors=[vector] if vector else None, metadata={}, status="embedded" if vector else "failed")


class RetrievalContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = load_runtime_config(repo_root=Path(__file__).resolve().parents[1])

    def test_exact_status_distinguishes_no_terms_and_no_matches(self) -> None:
        rows = [
            {
                "chunk_id": "c1",
                "note_id": "n1",
                "note_title": "Idea",
                "section_label": "Body",
                "relative_path": "JOURNAL/idea.md",
                "paragraph_text": "Semantic Geometry.",
            }
        ]
        _, _, skipped = _exact_candidates(rows, [], scope_filters={}, limit=10, config=self.config)
        _, _, absent = _exact_candidates(rows, [{"term": "unrelated", "match": "case_sensitive_substring"}], scope_filters={}, limit=10, config=self.config)
        self.assertEqual(skipped["status"], "skipped_no_terms")
        self.assertEqual(absent["status"], "completed_no_matches")

    def test_exact_case_sensitive_mode_is_not_silently_coerced(self) -> None:
        rows = [{
            "chunk_id": "c1", "note_id": "n1", "note_title": "Idea", "section_label": "Body",
            "relative_path": "idea.md", "paragraph_text": "Semantic Geometry.",
        }]
        candidates, _, info = _exact_candidates(
            rows,
            [{"term": "semantic geometry", "match": "case_sensitive_substring"}],
            scope_filters={}, limit=10, config=self.config,
        )
        self.assertEqual(info["status"], "completed_no_matches")
        self.assertEqual(candidates, [])

    def test_vector_surface_executes_each_semantic_query_and_retains_provenance(self) -> None:
        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        connection.execute("CREATE TABLE chunks (chunk_id TEXT, note_id TEXT, source_root_label TEXT, source_root_path TEXT, relative_path TEXT, note_title TEXT, frontmatter_semantics_json TEXT, section_label TEXT, paragraph_text TEXT, chunk_hash TEXT)")
        connection.execute("CREATE TABLE chunk_vectors (chunk_id TEXT, vector_json TEXT)")
        connection.execute("INSERT INTO chunks VALUES ('c1','n1','vault','','idea.md','Idea','{}','Body','Semantic geometry','h1')")
        connection.execute("INSERT INTO chunk_vectors VALUES ('c1','[1.0, 0.0]')")
        backend = _FakeEmbeddingBackend({"first query": [1.0, 0.0], "second query": [1.0, 0.0]})
        candidates, _ = _vector_candidates(
            connection=connection, config=self.config, embedding_backend=backend,
            vector_query="first query", vector_queries=["first query", "second query"],
            scope_filters={}, limit=10,
        )
        self.assertEqual(candidates[0]["semantic_query_provenance"], ["first query", "second query"])

    def test_required_exact_layer_blocks_when_terms_were_skipped(self) -> None:
        packet = {
            "raw_user_input": "find it",
            "intent": "search",
            "query": "find it",
            "entities": [],
            "relations": [],
            "resolved_referents": [],
            "planner_retrieval_plan": {},
            "limitations": [],
        }
        manifest = {
            "bound_retrieval_plan": {
                "retrieval_layers": [{"operator": "exact_chunk_search", "required": True}],
                "semantic_queries": [],
                "graph_seeds": [],
            },
            "layer_manifests": {"exact": {"status": "skipped_no_terms"}},
            "candidate_counts": {"exact": 0, "lexical": 0, "vector": 0, "graph": 0},
            "selection_notes": [],
        }
        report = _coverage_report(
            semantic_compiler_packet=packet,
            semantic_compiler_status="parsed",
            semantic_compiler_diagnostic={},
            traversal_manifest=manifest,
            retrieval_packet={"matched_chunk_count": 0},
        )
        self.assertEqual(report["decision"], "blocked")
        self.assertIn("required exact layer is skipped_no_terms", report["blocking_reasons"])

    def test_scope_is_or_within_dimension_and_and_across_dimensions(self) -> None:
        journal_reading = {
            "source_root_label": "vault", "source_root_path": "C:/vault",
            "relative_path": "READING/idea.md", "frontmatter_semantics_json": '{"note_type":"reading_notes"}',
        }
        journal_daily = {
            "source_root_label": "vault", "source_root_path": "C:/vault",
            "relative_path": "JOURNAL/today.md", "frontmatter_semantics_json": '{"note_type":"journal_entry"}',
        }
        filters = {"source_label": "vault", "note_type": ["journal_entry", "reading_notes"], "path_contains": ["journal", "reading"]}
        self.assertTrue(_chunk_matches_scope(journal_reading, filters))
        self.assertTrue(_chunk_matches_scope(journal_daily, filters))
        self.assertFalse(_chunk_matches_scope({**journal_reading, "frontmatter_semantics_json": '{"note_type":"journal_entry"}'}, {"note_type": ["journal_entry"], "path_contains": ["journal"]}))

    def test_lexical_modes_use_fts5(self) -> None:
        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        connection.execute("CREATE VIRTUAL TABLE chunks_fts USING fts5(chunk_id UNINDEXED, paragraph_text, note_title, section_label, relative_path, metadata)")
        connection.execute("INSERT INTO chunks_fts VALUES ('c1','Semantic geometry is relational','Idea','Body','JOURNAL/idea.md','{}')")
        rows = [{
            "chunk_id": "c1", "note_id": "n1", "source_root_label": "vault", "source_root_path": "",
            "relative_path": "JOURNAL/idea.md", "note_title": "Idea", "frontmatter_semantics_json": "{}",
            "section_label": "Body", "paragraph_text": "Semantic geometry is relational", "chunk_hash": "h1",
        }]
        for mode in ("exact_phrase", "all_tokens", "any_tokens", "prefix", "ranked_fts"):
            candidates, notes = _lexical_candidates(
                rows, ["semantic geometry"], connection=connection, scope_filters={}, limit=10,
                config=self.config, mode=mode,
            )
            self.assertTrue(candidates, (mode, notes))
            self.assertEqual(candidates[0]["lexical_mode"], mode)


if __name__ == "__main__":
    unittest.main()
