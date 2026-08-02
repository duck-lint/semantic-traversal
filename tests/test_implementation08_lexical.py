from __future__ import annotations

import sqlite3
import unittest
from pathlib import Path

from semantic_traversal.config import load_runtime_config
from semantic_traversal.runtime import _lexical_candidates, _semantic_traversal


REPO_ROOT = Path(__file__).resolve().parents[1]


def _fixture_connection() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute(
        "CREATE VIRTUAL TABLE chunks_fts USING fts5(chunk_id UNINDEXED, paragraph_text, note_title, section_label, relative_path, metadata)"
    )
    connection.execute(
        "INSERT INTO chunks_fts VALUES (?, ?, ?, ?, ?, ?)",
        (
            "c1",
            "I've said I'm testing it's an author's concept; that's a curly apostrophe: it’s a \"quoted\" (parenthesized) hyphen-token: OR AND NOT NEAR *.",
            "Punctuation",
            "Body",
            "fixture.md",
            "{}",
        ),
    )
    return connection


def _fixture_rows() -> list[dict[str, str]]:
    return [{
        "chunk_id": "c1",
        "note_id": "n1",
        "source_root_label": "fixture",
        "source_root_path": "",
        "relative_path": "fixture.md",
        "note_title": "Punctuation",
        "frontmatter_semantics_json": "{}",
        "section_label": "Body",
        "paragraph_text": "I've said I'm testing it's an author's concept; that's a curly apostrophe: it’s a \"quoted\" (parenthesized) hyphen-token: OR AND NOT NEAR *.",
        "chunk_hash": "h1",
    }]


class Implementation08LexicalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = load_runtime_config(repo_root=REPO_ROOT)

    def _run(self, queries: list[str], mode: str) -> tuple[list[dict[str, object]], list[str], dict[str, object]]:
        connection = _fixture_connection()
        try:
            return _lexical_candidates(
                _fixture_rows(), queries, connection=connection, scope_filters={}, limit=10,
                config=self.config, mode=mode, return_diagnostics=True,
            )
        finally:
            connection.close()

    def test_repaired_apostrophe_query_is_a_literal_candidate(self) -> None:
        """The independent pre-repair failure is recorded in the Seam-0 report."""
        candidates, notes, diagnostics = self._run(["I'm"], "any_tokens")
        self.assertEqual([item["chunk_id"] for item in candidates], ["c1"])
        self.assertEqual(diagnostics["effective_query"], '"i\'m"')
        self.assertEqual(diagnostics["status"], "completed_with_candidates")
        self.assertFalse(any("FTS5 index error" in note for note in notes))

    def test_apostrophes_curly_apostrophes_and_punctuation_are_literal_candidates(self) -> None:
        for query in ("I've", "I'm", "it's", "that's", "author's concept", "it’s", "parenthesized", "hyphen-token", "apostrophe:"):
            with self.subTest(query=query):
                candidates, _, diagnostics = self._run([query], "any_tokens")
                self.assertEqual([item["chunk_id"] for item in candidates], ["c1"])
                self.assertEqual(diagnostics["status"], "completed_with_candidates")
                self.assertTrue(str(diagnostics["effective_query"]).startswith('"'))

        _, _, asterisk = self._run(["*"], "any_tokens")
        self.assertEqual(asterisk["status"], "skipped_no_input")

    def test_embedded_double_quotes_are_doubled_in_the_fts_expression(self) -> None:
        candidates, _, diagnostics = self._run(['a "quoted"'], "exact_phrase")
        self.assertEqual([item["chunk_id"] for item in candidates], ["c1"])
        self.assertEqual(diagnostics["effective_query"], '"a ""quoted"""')

    def test_operator_like_tokens_are_quoted_and_not_operators(self) -> None:
        candidates, _, diagnostics = self._run(["OR AND NOT NEAR"], "all_tokens")
        self.assertEqual([item["chunk_id"] for item in candidates], ["c1"])
        self.assertEqual(diagnostics["effective_query"], '"or" AND "and" AND "not" AND "near"')

    def test_every_supported_mode_is_exercised(self) -> None:
        for mode in ("exact_phrase", "all_tokens", "any_tokens", "prefix", "ranked_fts"):
            with self.subTest(mode=mode):
                candidates, _, diagnostics = self._run(["author's concept"], mode)
                self.assertEqual([item["chunk_id"] for item in candidates], ["c1"])
                self.assertEqual(diagnostics["effective_mode"], mode)
                self.assertEqual(diagnostics["status"], "completed_with_candidates")
        _, _, prefix_diagnostics = self._run(["author"], "prefix")
        self.assertEqual(prefix_diagnostics["effective_query"], '"author"*')

    def test_candidate_zero_skip_and_structured_failure_are_distinct(self) -> None:
        _, _, zero = self._run(["missing-literal"], "any_tokens")
        self.assertEqual(zero["status"], "completed_no_candidates")
        self.assertEqual(zero["candidate_count"], 0)

        _, _, skipped_empty = self._run([], "any_tokens")
        _, _, skipped_punctuation = self._run(["()***::"], "any_tokens")
        self.assertEqual(skipped_empty["status"], "skipped_no_input")
        self.assertEqual(skipped_punctuation["status"], "skipped_no_input")

        connection = sqlite3.connect(":memory:")
        try:
            candidates, notes, failed = _lexical_candidates(
                _fixture_rows(), ["I'm"], connection=connection, scope_filters={}, limit=10,
                config=self.config, mode="any_tokens", return_diagnostics=True,
            )
        finally:
            connection.close()
        self.assertEqual(candidates, [])
        self.assertEqual(failed["status"], "unavailable")
        self.assertEqual(failed["candidate_count"], 0)
        self.assertTrue(any("FTS5 index error" in note for note in notes))

    def test_multiple_query_streams_and_repeated_execution_are_deterministic(self) -> None:
        first = self._run(["author's", "concept", "author's"], "any_tokens")
        second = self._run(["author's", "concept", "author's"], "any_tokens")
        self.assertEqual(first[2], second[2])
        self.assertEqual(first[0], second[0])
        self.assertEqual(first[2]["original_query"], ["author's", "concept", "author's"])

    def test_runtime_traversal_records_lexical_failure_while_optional_exact_grounding_runs(self) -> None:
        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        connection.execute("CREATE TABLE chunks (chunk_id TEXT, note_id TEXT, source_root_label TEXT, source_root_path TEXT, relative_path TEXT, note_title TEXT, frontmatter_semantics_json TEXT, section_label TEXT, paragraph_text TEXT, chunk_hash TEXT)")
        connection.execute("INSERT INTO chunks VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", ("c1", "n1", "fixture", "", "fixture.md", "Punctuation", "{}", "Body", "I'm here", "h1"))
        plan = {
            "intent_type": "semantic_traversal", "scope_requests": [], "concepts": ["I'm"],
            "resolved_referents": [], "literal_terms": [], "evidence_requirements": ["lexical_relevance"],
            "semantic_queries": ["I'm"], "lexical_queries": ["I'm"], "graph_seeds": [],
            "retrieval_layers": [{"operator": "lexical_chunk_search", "required": False, "limit": 10, "mode": "any_tokens"}],
        }
        packet = {"raw_user_input": "I'm", "query": "I'm", "concepts": ["I'm"], "entities": [], "relations": [], "resolved_referents": [], "limitations": [], "planner_retrieval_plan": plan, "planner_diagnostics": {}}
        inventory = {"scope_aliases": {}, "configured_source_labels": ["fixture"], "observed_source_labels": ["fixture"], "frontmatter_facets": {"note_type": []}, "path_topology": {"top_level": [], "second_level": []}, "graph_capabilities": {}, "inventory_diagnostics": {"status": "unavailable"}}
        try:
            manifest, traversal_packet = _semantic_traversal(
                connection=connection, config=self.config, semantic_compiler_packet=packet,
                prior_thread_state={}, embedding_backend=object(), resource_inventory_summary=inventory,
            )
        finally:
            connection.close()
        self.assertEqual([item["chunk_id"] for item in traversal_packet["selected_chunks"]], ["c1"])
        self.assertEqual(manifest["layer_manifests"]["lexical"]["status"], "unavailable")
        self.assertEqual(manifest["candidate_counts"]["lexical"], 0)
        self.assertFalse(manifest["coverage"]["coverage_claims_allowed"])
        self.assertFalse(traversal_packet.get("synthesis_called", False))


if __name__ == "__main__":
    unittest.main()
