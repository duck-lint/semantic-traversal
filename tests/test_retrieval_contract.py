from __future__ import annotations

import sqlite3
import unittest
from copy import deepcopy
from pathlib import Path

from semantic_traversal.config import RuntimeConfig, load_runtime_config
from semantic_traversal.runtime import _chunk_matches_scope, _coverage_report, _exact_candidates, _lexical_candidates, _merge_candidates, _select_retrieval_chunks, _vector_candidates


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

    def test_exact_counts_are_independent_from_returned_candidate_limit(self) -> None:
        rows = [
            {"chunk_id": f"c{i}", "note_id": f"n{i // 2}", "note_title": "Idea", "section_label": "Body", "relative_path": "idea.md", "paragraph_text": "alpha alpha"}
            for i in range(4)
        ]
        candidates, _, info = _exact_candidates(
            rows, [{"term": "alpha", "required": True, "match": "case_sensitive_substring"}],
            scope_filters={}, limit=1, return_total_count=True, config=self.config,
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(info["total_match_count"], 4)
        self.assertEqual(info["count_unit"], "matching_chunks")
        self.assertEqual(info["total_occurrence_count"], 8)
        self.assertEqual(info["matching_note_count"], 2)
        self.assertEqual(info["term_results"][0]["returned_candidate_count"], 1)

    def test_exact_count_reporting_false_is_explicitly_bounded(self) -> None:
        rows = [{"chunk_id": "c1", "note_id": "n1", "note_title": "Idea", "section_label": "Body", "relative_path": "idea.md", "paragraph_text": "alpha"}]
        _, _, info = _exact_candidates(rows, [{"term": "alpha"}], scope_filters={}, limit=1, return_total_count=False, config=self.config)
        self.assertTrue(info["search_exhaustive"])
        self.assertFalse(info["return_total_count_requested"])
        self.assertEqual(info["count_status"], "not_requested")
        self.assertIsNone(info["total_match_count"])
        self.assertEqual(info["returned_candidate_count"], 1)

    def test_exact_context_is_bounded_and_metadata_matches_use_their_field(self) -> None:
        row = {"chunk_id": "c1", "note_id": "n1", "note_title": "prefix alpha suffix", "section_label": "Body", "relative_path": "idea.md", "paragraph_text": "body"}
        candidates, _, info = _exact_candidates(row and [row], [{"term": "alpha", "match": "case_sensitive_substring"}], scope_filters={}, limit=1, return_total_count=True, config=self.config)
        self.assertEqual(info["term_results"][0]["total_occurrence_count"], 1)
        evidence = candidates[0]["exact_match_evidence"][0]
        self.assertEqual(evidence["matched_fields"], ["note_title"])
        self.assertEqual(evidence["representative_context"]["field"], "note_title")
        self.assertEqual(evidence["representative_context"]["excerpt"], "prefix alpha suffix")

    def test_exact_context_zero_is_deterministic(self) -> None:
        raw = deepcopy(self.config.raw)
        raw["retrieval"]["exact"]["context_chars"] = 0
        config = RuntimeConfig(repo_root=self.config.repo_root, config_path=self.config.config_path, raw=raw)
        rows = [{"chunk_id": "c1", "note_id": "n1", "note_title": "Idea", "section_label": "Body", "relative_path": "idea.md", "paragraph_text": "before alpha after"}]
        candidates, _, _ = _exact_candidates(rows, [{"term": "alpha"}], scope_filters={}, limit=1, return_total_count=True, config=config)
        self.assertEqual(candidates[0]["exact_match_evidence"][0]["representative_context"]["excerpt"], "alpha")

    def test_exact_context_offsets_remain_in_original_unicode_field(self) -> None:
        rows = [{"chunk_id": "c1", "note_id": "n1", "note_title": "Idea", "section_label": "Body", "relative_path": "idea.md", "paragraph_text": "naïve αlpha"}]
        candidates, _, _ = _exact_candidates(rows, [{"term": "ΑLPHA", "match": "case_insensitive_substring"}], scope_filters={}, limit=1, return_total_count=True, config=self.config)
        context = candidates[0]["exact_match_evidence"][0]["representative_context"]
        self.assertEqual(context["match_start"], rows[0]["paragraph_text"].index("αlpha"))
        matched = rows[0]["paragraph_text"][context["match_start"]:context["match_end"]]
        self.assertEqual(matched.casefold(), "αlpha".casefold())

    def test_required_exact_term_preserves_exact_provenance_through_merge_and_selection(self) -> None:
        exact = [{"chunk_id": "shared", "chunk_hash": "h", "selection_source": "exact", "source_layers": ["exact"], "score": 5, "exact_term_provenance": ["alpha"], "exact_match_evidence": [{"term": "alpha"}]}]
        lexical = [{"chunk_id": "shared", "chunk_hash": "h", "selection_source": "lexical", "score": 6}]
        merged = _merge_candidates(lexical, [], [], config=self.config, exact_candidates=exact)
        selected = _select_retrieval_chunks(merged_candidates=merged, max_chunks=1, required_exact_terms={"alpha"})
        self.assertEqual(selected[0]["selection_source"], "lexical")
        self.assertEqual(selected[0]["source_layers"], ["exact", "lexical"])
        self.assertEqual(selected[0]["exact_term_provenance"], ["alpha"])
        self.assertEqual(selected[0]["exact_match_evidence"], [{"term": "alpha"}])

    def test_required_absent_exact_term_is_completed_without_positive_evidence_requirement(self) -> None:
        rows = [{"chunk_id": "c1", "note_id": "n1", "note_title": "Idea", "section_label": "Body", "relative_path": "idea.md", "paragraph_text": "other"}]
        _, _, info = _exact_candidates(rows, [{"term": "missing", "required": True}], scope_filters={}, limit=1, return_total_count=True, config=self.config)
        self.assertEqual(info["status"], "completed_no_matches")
        self.assertTrue(info["term_results"][0]["adequate_contribution"])

    def test_required_positive_term_lost_to_selection_blocks_with_inadequate_diagnostic(self) -> None:
        manifest = {
            "bound_retrieval_plan": {
                "retrieval_layers": [{"operator": "exact_chunk_search", "required": True}],
                "semantic_queries": ["alpha"], "graph_seeds": [],
            },
            "layer_manifests": {"exact": {"status": "completed_with_matches", "term_results": [{
                "term": "alpha", "required": True, "status": "completed_with_matches", "adequate_contribution": False,
            }]}},
            "candidate_counts": {"exact": 1, "lexical": 0, "vector": 0, "graph": 0},
            "selection_notes": [],
            "coverage": {"exact_status": "completed_with_matches", "negative_claims_allowed": False},
        }
        report = _coverage_report(
            semantic_compiler_packet={"raw_user_input": "alpha", "intent": "search", "query": "alpha", "entities": [], "relations": [], "resolved_referents": [], "planner_retrieval_plan": {}, "limitations": []},
            semantic_compiler_status="parsed", semantic_compiler_diagnostic={}, traversal_manifest=manifest,
            retrieval_packet={"matched_chunk_count": 1},
        )
        self.assertEqual(report["decision"], "blocked")
        self.assertIn("inadequate selected contribution", " ".join(report["blocking_reasons"]))

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
