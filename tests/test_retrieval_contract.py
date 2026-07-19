from __future__ import annotations

import sqlite3
import json
import unittest
from copy import deepcopy
from pathlib import Path

from semantic_traversal.config import RuntimeConfig, load_runtime_config
from semantic_traversal.embeddings import EmbeddingResponse, UnavailableEmbeddingBackend, embedding_identity_hash
from semantic_traversal.runtime import _chunk_matches_scope, _coverage_report, _exact_candidates, _lexical_candidates, _merge_candidates, _select_retrieval_chunks, _vector_candidates
from semantic_traversal.retrieval_resolver import bind_retrieval_plan


class _FakeEmbeddingBackend:
    mode_name = "test"

    def __init__(self, vectors: dict[str, list[float]]) -> None:
        self.vectors = vectors

    def embed_query_text(self, text: str):
        from semantic_traversal.embeddings import EmbeddingResponse

        vector = self.vectors.get(text)
        return EmbeddingResponse(vectors=[vector] if vector else None, metadata={}, status="embedded" if vector else "failed")


class _PartialEmbeddingBackend:
    mode_name = "test"

    def embed_query_text(self, text: str):
        if text == "good query":
            return EmbeddingResponse(vectors=[[1.0, 0.0]], metadata={}, status="embedded")
        return EmbeddingResponse(vectors=None, metadata={}, status="failed")


class RetrievalContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = load_runtime_config(repo_root=Path(__file__).resolve().parents[1])

    def _vector_connection(self, entries: list[tuple[str, str, list[float], dict[str, object] | None]]) -> sqlite3.Connection:
        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        connection.execute("CREATE TABLE chunks (chunk_id TEXT, note_id TEXT, source_root_label TEXT, source_root_path TEXT, relative_path TEXT, note_title TEXT, frontmatter_semantics_json TEXT, section_label TEXT, paragraph_text TEXT, chunk_hash TEXT)")
        connection.execute("CREATE TABLE chunk_vectors (chunk_id TEXT PRIMARY KEY, vector_json TEXT, vector_dimensions INTEGER, embedding_provider TEXT, embedding_model TEXT, embedding_identity_json TEXT, embedding_identity_hash TEXT, content_hash TEXT, last_indexed_run_id TEXT, updated_at TEXT)")
        default_identity = {"provider": "test", "model": self.config.embedding_model, "dimensions": 2, "normalize_embeddings": self.config.embedding_normalize_embeddings, "encoding_strategy": "chunk_embedding_text_v1"}
        for chunk_id, note_id, vector, identity_override in entries:
            identity = identity_override or default_identity
            connection.execute("INSERT INTO chunks VALUES (?, ?, 'vault', '', ?, ?, '{}', 'Body', ?, ?)", (chunk_id, note_id, f"{note_id}.md", note_id, note_id, chunk_id))
            connection.execute("INSERT INTO chunk_vectors VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (chunk_id, json.dumps(vector), len(vector), identity["provider"], identity["model"], json.dumps(identity), embedding_identity_hash(identity), chunk_id, "run", "now"))
        return connection

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
        connection.execute("CREATE TABLE chunk_vectors (chunk_id TEXT PRIMARY KEY, vector_json TEXT, vector_dimensions INTEGER, embedding_provider TEXT, embedding_model TEXT, embedding_identity_json TEXT, embedding_identity_hash TEXT, content_hash TEXT, last_indexed_run_id TEXT, updated_at TEXT)")
        connection.execute("INSERT INTO chunks VALUES ('c1','n1','vault','','idea.md','Idea','{}','Body','Semantic geometry','h1')")
        identity = {"provider": "test", "model": self.config.embedding_model, "dimensions": 2, "normalize_embeddings": self.config.embedding_normalize_embeddings, "encoding_strategy": "chunk_embedding_text_v1"}
        connection.execute("INSERT INTO chunk_vectors VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", ("c1", "[1.0, 0.0]", 2, "test", self.config.embedding_model, json.dumps(identity), embedding_identity_hash(identity), "h1", "run", "now"))
        backend = _FakeEmbeddingBackend({"first query": [1.0, 0.0], "second query": [1.0, 0.0]})
        candidates, _ = _vector_candidates(
            connection=connection, config=self.config, embedding_backend=backend,
            vector_query="first query", vector_queries=["first query", "second query"],
            scope_filters={}, limit=10,
        )
        self.assertEqual(candidates[0]["semantic_query_provenance"], ["first query", "second query"])

    def test_vector_threshold_is_inclusive_and_multi_query_allocation_is_diverse(self) -> None:
        raw = deepcopy(self.config.raw)
        raw["retrieval"]["vector"].update({"min_similarity": 0.8, "per_query_max_candidates": 3, "max_chunks_per_note": 1, "max_candidates": 4})
        config = RuntimeConfig(repo_root=self.config.repo_root, config_path=self.config.config_path, raw=raw)
        connection = self._vector_connection([
            ("c1", "n1", [1.0, 0.0], None),
            ("c2", "n1", [0.9, 0.1], None),
            ("c3", "n2", [0.0, 1.0], None),
            ("c4", "n3", [0.8, 0.6], None),
        ])
        backend = _FakeEmbeddingBackend({"alpha": [1.0, 0.0], "beta": [0.0, 1.0]})
        candidates, _, diagnostics = _vector_candidates(
            connection=connection, config=config, embedding_backend=backend,
            vector_query="alpha", vector_queries=["beta", "alpha"], scope_filters={}, limit=4,
            return_diagnostics=True,
        )
        self.assertEqual([item["chunk_id"] for item in candidates], ["c1", "c3", "c4"])
        self.assertEqual(max(sum(1 for item in candidates if item["note_id"] == note_id) for note_id in {item["note_id"] for item in candidates}), 1)
        self.assertEqual(diagnostics["effective_min_similarity"], 0.8)
        self.assertGreaterEqual(diagnostics["at_or_above_threshold_count"], 4)
        self.assertEqual(diagnostics["query_results"][0]["query"], "alpha")
        self.assertEqual({item["query"] for item in candidates[0]["vector_query_scores"]}, {"alpha"})
        self.assertTrue(any(item["query"] == "beta" for item in candidates[1]["vector_query_scores"]))

    def test_vector_identity_mismatch_blocks_comparison_and_malformed_rows_are_diagnosed(self) -> None:
        mismatched = {"provider": "other", "model": self.config.embedding_model, "dimensions": 2, "normalize_embeddings": self.config.embedding_normalize_embeddings, "encoding_strategy": "chunk_embedding_text_v1"}
        connection = self._vector_connection([("bad", "n1", [1.0, 0.0], mismatched), ("good", "n2", [1.0, 0.0], None), ("mismatch", "n3", [1.0, 0.0], mismatched)])
        connection.execute("UPDATE chunk_vectors SET vector_json = ? WHERE chunk_id = ?", ("not-json", "bad"))
        backend = _FakeEmbeddingBackend({"query": [1.0, 0.0]})
        candidates, _, diagnostics = _vector_candidates(
            connection=connection, config=self.config, embedding_backend=backend,
            vector_query="query", vector_queries=["query"], scope_filters={}, limit=10, return_diagnostics=True,
        )
        self.assertEqual([item["chunk_id"] for item in candidates], ["good"])
        self.assertEqual(diagnostics["invalid_json_count"], 1)
        self.assertGreaterEqual(diagnostics["identity_mismatch_count"], 0)
        self.assertEqual(diagnostics["status"], "partial_failure")

    def test_vector_query_order_and_vector_row_insertion_order_are_canonical(self) -> None:
        entries = [("c1", "n1", [1.0, 0.0], None), ("c2", "n2", [0.0, 1.0], None)]
        first = self._vector_connection(entries)
        second = self._vector_connection(list(reversed(entries)))
        backend = _FakeEmbeddingBackend({"zeta": [1.0, 0.0], "alpha": [0.0, 1.0]})
        first_result = _vector_candidates(connection=first, config=self.config, embedding_backend=backend, vector_query="zeta", vector_queries=["zeta", "alpha"], scope_filters={}, limit=10)
        second_result = _vector_candidates(connection=second, config=self.config, embedding_backend=backend, vector_query="alpha", vector_queries=["alpha", "zeta"], scope_filters={}, limit=10)
        self.assertEqual([item["chunk_id"] for item in first_result[0]], [item["chunk_id"] for item in second_result[0]])
        self.assertEqual(first_result[0][0]["semantic_query_provenance"], second_result[0][0]["semantic_query_provenance"])

    def test_vector_score_provenance_survives_merge_when_another_surface_wins(self) -> None:
        vector = [{"chunk_id": "shared", "chunk_hash": "h", "selection_source": "vector", "score": 2.0, "vector_query_scores": [{"query": "alpha", "similarity": 0.7}, {"query": "beta", "similarity": 0.6}], "semantic_query_provenance": ["alpha", "beta"], "vector_best_query": "alpha", "vector_similarity": 0.7}]
        lexical = [{"chunk_id": "shared", "chunk_hash": "h", "selection_source": "lexical", "score": 4.0}]
        merged = _merge_candidates(lexical, vector, [], config=self.config)
        self.assertEqual(merged[0]["selection_source"], "lexical")
        self.assertEqual(merged[0]["vector_best_query"], "alpha")
        self.assertEqual(merged[0]["semantic_query_provenance"], ["alpha", "beta"])
        self.assertEqual(merged[0]["vector_query_scores"], [{"query": "alpha", "similarity": 0.7}, {"query": "beta", "similarity": 0.6}])

    def test_lexical_manifest_statuses_distinguish_input_mode_and_index(self) -> None:
        rows = [{"chunk_id": "c1", "note_id": "n1", "source_root_label": "vault", "source_root_path": "", "relative_path": "idea.md", "note_title": "Idea", "frontmatter_semantics_json": "{}", "section_label": "Body", "paragraph_text": "alpha", "chunk_hash": "h"}]
        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        connection.execute("CREATE VIRTUAL TABLE chunks_fts USING fts5(chunk_id UNINDEXED, paragraph_text, note_title, section_label, relative_path, metadata)")
        connection.execute("INSERT INTO chunks_fts VALUES ('c1','alpha','Idea','Body','idea.md','{}')")
        _, _, completed = _lexical_candidates(rows, ["alpha"], connection=connection, limit=5, config=self.config, mode="any_tokens", return_diagnostics=True)
        _, _, empty = _lexical_candidates(rows, [], connection=connection, limit=5, config=self.config, mode="any_tokens", return_diagnostics=True)
        _, _, unsupported = _lexical_candidates(rows, ["alpha"], connection=connection, limit=5, config=self.config, mode="not_a_mode", return_diagnostics=True)
        missing_connection = sqlite3.connect(":memory:")
        _, _, unavailable = _lexical_candidates(rows, ["alpha"], connection=missing_connection, limit=5, config=self.config, mode="any_tokens", return_diagnostics=True)
        self.assertEqual(completed["status"], "completed_with_candidates")
        self.assertEqual(empty["status"], "skipped_no_input")
        self.assertEqual(unsupported["status"], "unsupported")
        self.assertEqual(unavailable["status"], "unavailable")

    def test_vector_manifest_statuses_distinguish_unavailable_no_input_and_partial_failure(self) -> None:
        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        connection.execute("CREATE TABLE chunk_vectors (chunk_id TEXT, vector_json TEXT)")
        connection.execute("CREATE TABLE chunks (chunk_id TEXT, note_id TEXT, source_root_label TEXT, source_root_path TEXT, relative_path TEXT, note_title TEXT, frontmatter_semantics_json TEXT, section_label TEXT, paragraph_text TEXT, chunk_hash TEXT)")
        _, _, unavailable = _vector_candidates(connection=connection, config=self.config, embedding_backend=UnavailableEmbeddingBackend(reason="test"), vector_query="q", vector_queries=["q"], limit=5, return_diagnostics=True)
        _, _, empty = _vector_candidates(connection=connection, config=self.config, embedding_backend=_PartialEmbeddingBackend(), vector_query="", vector_queries=[], limit=5, return_diagnostics=True)
        _, _, partial = _vector_candidates(connection=connection, config=self.config, embedding_backend=_PartialEmbeddingBackend(), vector_query="good query", vector_queries=["good query", "bad query"], limit=5, return_diagnostics=True)
        self.assertEqual(unavailable["status"], "unavailable")
        self.assertEqual(empty["status"], "skipped_no_input")
        self.assertEqual(partial["status"], "partial_failure")
        self.assertEqual([item["query"] for item in partial["query_results"]], ["bad query", "good query"])

    def test_required_layer_reservation_uses_source_layers_and_respects_zero_budget(self) -> None:
        candidates = [
            {"chunk_id": "lex", "chunk_hash": "lex", "selection_source": "lexical", "source_layers": ["lexical"], "score": 5},
            {"chunk_id": "multi", "chunk_hash": "multi", "selection_source": "lexical", "source_layers": ["lexical", "vector", "graph"], "score": 4},
        ]
        diagnostics: dict[str, object] = {}
        selected = _select_retrieval_chunks(
            merged_candidates=candidates, max_chunks=2,
            selection_policy={"preserve_required_layers": True, "budgets": {"exact": 0, "lexical": 2, "vector": 1, "graph": 1}},
            required_sources={"lexical", "vector", "graph"}, selection_diagnostics=diagnostics,
        )
        self.assertEqual([item["chunk_id"] for item in selected], ["multi", "lex"])
        self.assertEqual(diagnostics["preserved_sources"], ["graph", "lexical", "vector"])
        zero_diagnostics: dict[str, object] = {}
        zero_selected = _select_retrieval_chunks(
            merged_candidates=candidates, max_chunks=2,
            selection_policy={"preserve_required_layers": True, "budgets": {"exact": 0, "lexical": 2, "vector": 0, "graph": 0}},
            required_sources={"vector"}, selection_diagnostics=zero_diagnostics,
        )
        self.assertEqual([item["chunk_id"] for item in zero_selected], ["lex", "multi"])
        self.assertTrue(any(item["reason"] == "source budget is zero" for item in zero_diagnostics["inadequacies"]))

    def test_runtime_resolver_replaces_compiler_selection_and_claim_policy(self) -> None:
        plan = {
            "intent_type": "semantic_traversal", "scope_requests": [], "concepts": ["alpha"], "literal_terms": [],
            "semantic_queries": ["alpha"], "lexical_queries": ["alpha"], "graph_seeds": [],
            "retrieval_layers": [{"operator": "lexical_chunk_search", "required": False, "limit": 999}],
            "selection_policy": {"max_chunks": 999, "preserve_required_layers": False, "budgets": {"lexical": 0, "vector": 0, "graph": 0, "exact": 0}},
            "claim_policy": {"coverage_claims_allowed": True, "negative_claims_require_exact_layer": False},
        }
        bound, adjustments, _ = bind_retrieval_plan(planner_retrieval_plan=plan, inventory_summary={"frontmatter_facets": {"note_type": []}, "observed_source_labels": [], "path_topology": {"top_level": [], "second_level": []}}, config=self.config, raw_user_input="alpha")
        self.assertEqual(bound["selection_policy"]["max_chunks"], 24)
        self.assertTrue(bound["selection_policy"]["preserve_required_layers"])
        self.assertEqual(bound["selection_policy"]["budgets"], {"exact": 12, "lexical": 6, "vector": 6, "graph": 4})
        self.assertTrue(bound["claim_policy"]["negative_claims_require_exact_layer"])
        self.assertEqual(bound["retrieval_layers"][0]["effective_limit"], self.config.retrieval_lexical_max_candidates)
        self.assertEqual(bound["retrieval_layers"][0]["limit_adjustment"], "clamped_to_max")
        self.assertTrue(any(item["action"] == "runtime_policy_override" for item in adjustments))

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

    def test_lexical_diagnostics_distinguish_index_matches_scope_and_return_limit(self) -> None:
        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        connection.execute("CREATE VIRTUAL TABLE chunks_fts USING fts5(chunk_id UNINDEXED, paragraph_text, note_title, section_label, relative_path, metadata)")
        connection.executemany(
            "INSERT INTO chunks_fts VALUES (?, ?, ?, ?, ?, ?)",
            [
                ("c1", "alpha", "One", "Body", "JOURNAL/one.md", "{}"),
                ("c2", "alpha", "Two", "Body", "READING/two.md", "{}"),
                ("c3", "alpha", "Three", "Body", "READING/three.md", "{}"),
            ],
        )
        rows = [
            {"chunk_id": "c1", "note_id": "n1", "source_root_label": "vault", "source_root_path": "", "relative_path": "JOURNAL/one.md", "note_title": "One", "frontmatter_semantics_json": "{}", "section_label": "Body", "paragraph_text": "alpha", "chunk_hash": "h1"},
            {"chunk_id": "c2", "note_id": "n2", "source_root_label": "vault", "source_root_path": "", "relative_path": "READING/two.md", "note_title": "Two", "frontmatter_semantics_json": "{}", "section_label": "Body", "paragraph_text": "alpha", "chunk_hash": "h2"},
        ]
        candidates, _, diagnostics = _lexical_candidates(
            rows, ["alpha"], connection=connection, scope_filters={}, limit=1,
            config=self.config, mode="any_tokens", return_diagnostics=True,
        )
        self.assertEqual([item["chunk_id"] for item in candidates], ["c1"])
        self.assertEqual(diagnostics["index_row_count"], 3)
        self.assertEqual(diagnostics["raw_match_count"], 3)
        self.assertEqual(diagnostics["scoped_match_count"], 2)
        self.assertEqual(diagnostics["returned_candidate_count"], 1)
        self.assertEqual(diagnostics["candidate_count"], 1)


if __name__ == "__main__":
    unittest.main()
