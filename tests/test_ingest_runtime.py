from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from semantic_traversal.hashing import sha256_json
from semantic_traversal.ingest import build_default_source_roots, run_ingest
from semantic_traversal.llm import StubLLMBackend
from semantic_traversal.runtime import run_thread_turn
from semantic_traversal.semantic_extraction import DisabledSemanticExtractorBackend, StubSemanticExtractorBackend
from semantic_traversal.storage import read_ledger


REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_NOTE = REPO_ROOT / "tests" / "fixtures" / "JOURNAL" / "2025-09" / "01_Monday.md"
CORPUS_JOURNAL_NOTE = (
    REPO_ROOT
    / "corpus"
    / "LAYER-1 PILLARS"
    / "PILLAR 2-DYNAMIC COHERENCE"
    / "JOURNAL"
    / "2025"
    / "2025-08"
    / "24_Sunday.md"
)
LONGFORM_NOTE = (
    REPO_ROOT
    / "corpus"
    / "LAYER-1 PILLARS"
    / "PILLAR 2-DYNAMIC COHERENCE"
    / "JOURNAL"
    / "Propositions & Models.md"
)


def _load_manifest(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _copy_note(source_path: Path, destination_root: Path, relative_path: str) -> None:
    destination_path = destination_root / Path(relative_path)
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_path, destination_path)


def _chunk_map(manifest: dict[str, object]) -> dict[str, dict[str, object]]:
    return {
        chunk["chunk_id"]: chunk
        for chunk in manifest["chunks"]  # type: ignore[index]
    }


def _chunks_for_note(manifest: dict[str, object], source_root_label: str, relative_path: str) -> list[dict[str, object]]:
    return [
        chunk
        for chunk in manifest["chunks"]  # type: ignore[index]
        if chunk["source_root_label"] == source_root_label and chunk["relative_path"] == relative_path
    ]


def _load_turn_artifact(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


class IngestRuntimeTests(unittest.TestCase):
    def test_cli_ingest_uses_authorized_default_roots(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            process = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "semantic_traversal",
                    "ingest",
                    "--repo-root",
                    str(REPO_ROOT),
                    "--data-root",
                    temp_dir,
                ],
                cwd=REPO_ROOT,
                check=True,
                capture_output=True,
                text=True,
            )
            payload = json.loads(process.stdout)
            self.assertEqual(payload["status"], "pass")
            self.assertTrue(Path(payload["database_path"]).exists())
            self.assertTrue(Path(payload["manifest_path"]).exists())
            self.assertEqual({entry["label"] for entry in payload["source_roots"]}, {"corpus", "tests-fixtures"})
            connection = sqlite3.connect(payload["database_path"])
            try:
                rows = connection.execute("SELECT DISTINCT source_root_label FROM notes").fetchall()
            finally:
                connection.close()
            self.assertEqual({row[0] for row in rows}, {"corpus", "tests-fixtures"})

    def test_markdown_headings_become_section_labels_for_paragraph_chunks(self) -> None:
        with tempfile.TemporaryDirectory() as repo_dir, tempfile.TemporaryDirectory() as data_dir:
            repo_root = Path(repo_dir)
            (repo_root / "corpus").mkdir(parents=True, exist_ok=True)
            (repo_root / "tests" / "fixtures").mkdir(parents=True, exist_ok=True)
            _copy_note(FIXTURE_NOTE, repo_root / "tests" / "fixtures", "JOURNAL/2025-09/01_Monday.md")

            result = run_ingest(
                repo_root=repo_root,
                data_root=Path(data_dir),
                source_roots=build_default_source_roots(repo_root),
            )
            manifest = _load_manifest(result.manifest_path)
            chunks = _chunks_for_note(manifest, "tests-fixtures", "JOURNAL/2025-09/01_Monday.md")

            self.assertEqual(len(chunks), 4)
            self.assertEqual(
                [chunk["section_label"] for chunk in chunks],
                [
                    "Fixture Alpha Section",
                    "Fixture Beta Section",
                    "Fixture Multi Paragraph Section",
                    "Fixture Multi Paragraph Section",
                ],
            )
            self.assertEqual([chunk["paragraph_ordinal"] for chunk in chunks], [1, 1, 1, 2])
            self.assertEqual(chunks[2]["section_id"], chunks[3]["section_id"])
            self.assertNotIn("September 01, 2025", {chunk["section_label"] for chunk in chunks})
            self.assertTrue(str(chunks[0]["chunk_id"]).startswith("tests-fixtures::JOURNAL/2025-09/01_Monday.md::"))

    def test_heading_sections_and_longform_paragraphs_survive_resolution(self) -> None:
        with tempfile.TemporaryDirectory() as repo_dir, tempfile.TemporaryDirectory() as data_dir:
            repo_root = Path(repo_dir)
            (repo_root / "tests" / "fixtures").mkdir(parents=True, exist_ok=True)
            _copy_note(
                CORPUS_JOURNAL_NOTE,
                repo_root / "corpus",
                "LAYER-1 PILLARS/PILLAR 2-DYNAMIC COHERENCE/JOURNAL/2025/2025-08/24_Sunday.md",
            )
            _copy_note(
                LONGFORM_NOTE,
                repo_root / "corpus",
                "LAYER-1 PILLARS/PILLAR 2-DYNAMIC COHERENCE/JOURNAL/Propositions & Models.md",
            )

            result = run_ingest(
                repo_root=repo_root,
                data_root=Path(data_dir),
                source_roots=build_default_source_roots(repo_root),
            )
            manifest = _load_manifest(result.manifest_path)

            journal_chunks = _chunks_for_note(
                manifest,
                "corpus",
                "LAYER-1 PILLARS/PILLAR 2-DYNAMIC COHERENCE/JOURNAL/2025/2025-08/24_Sunday.md",
            )
            self.assertIn("Dream Motif", {chunk["section_label"] for chunk in journal_chunks})
            self.assertIn("Y-Day Review", {chunk["section_label"] for chunk in journal_chunks})
            self.assertIn("Dream recall", {chunk["section_label"] for chunk in journal_chunks})
            self.assertIn("Yesterday", {chunk["section_label"] for chunk in journal_chunks})

            longform_chunks = _chunks_for_note(
                manifest,
                "corpus",
                "LAYER-1 PILLARS/PILLAR 2-DYNAMIC COHERENCE/JOURNAL/Propositions & Models.md",
            )
            self.assertGreater(len(longform_chunks), 8)
            premise_chunks = [
                chunk["paragraph_text"]
                for chunk in longform_chunks
                if chunk["section_label"] == "[[Compartmentalization]] is the Gateway Drug to Immorality"
            ]
            self.assertTrue(any("Premise 1" in text for text in premise_chunks))
            self.assertTrue(any("Premise 2" in text for text in premise_chunks))

    def test_reingest_unchanged_preserves_chunk_ids_and_hashes(self) -> None:
        with tempfile.TemporaryDirectory() as repo_dir, tempfile.TemporaryDirectory() as data_dir:
            repo_root = Path(repo_dir)
            (repo_root / "tests" / "fixtures").mkdir(parents=True, exist_ok=True)
            _copy_note(FIXTURE_NOTE, repo_root / "tests" / "fixtures", "JOURNAL/2025-09/01_Monday.md")
            _copy_note(
                LONGFORM_NOTE,
                repo_root / "corpus",
                "LAYER-1 PILLARS/PILLAR 2-DYNAMIC COHERENCE/JOURNAL/Propositions & Models.md",
            )

            first_result = run_ingest(repo_root=repo_root, data_root=Path(data_dir), source_roots=build_default_source_roots(repo_root))
            second_result = run_ingest(repo_root=repo_root, data_root=Path(data_dir), source_roots=build_default_source_roots(repo_root))

            first_manifest = _load_manifest(first_result.manifest_path)
            second_manifest = _load_manifest(second_result.manifest_path)
            first_chunks = {chunk_id: chunk["chunk_hash"] for chunk_id, chunk in _chunk_map(first_manifest).items()}
            second_chunks = {chunk_id: chunk["chunk_hash"] for chunk_id, chunk in _chunk_map(second_manifest).items()}

            self.assertEqual(first_chunks, second_chunks)
            self.assertEqual(second_result.updated_chunks, 0)
            self.assertEqual(second_result.deleted_chunks, 0)
            self.assertEqual(second_result.unchanged_chunks, second_result.chunk_count)

    def test_localized_paragraph_edit_changes_only_the_edited_chunk(self) -> None:
        with tempfile.TemporaryDirectory() as repo_dir, tempfile.TemporaryDirectory() as data_dir:
            repo_root = Path(repo_dir)
            (repo_root / "corpus").mkdir(parents=True, exist_ok=True)
            _copy_note(FIXTURE_NOTE, repo_root / "tests" / "fixtures", "JOURNAL/2025-09/01_Monday.md")
            fixture_copy = repo_root / "tests" / "fixtures" / "JOURNAL" / "2025-09" / "01_Monday.md"

            first_result = run_ingest(repo_root=repo_root, data_root=Path(data_dir), source_roots=build_default_source_roots(repo_root))
            first_manifest = _load_manifest(first_result.manifest_path)
            first_chunks = _chunk_map(first_manifest)

            original_text = fixture_copy.read_text(encoding="utf-8")
            updated_text = original_text.replace(
                "This is the second paragraph under the shared synthetic section. The important retrieval phrase for tests is candy snack food before bed, and that phrase should be easy to find through the lexical SQLite path.",
                "This is the second paragraph under the shared synthetic section. The important retrieval phrase for tests is candy snack food before bed, and that phrase should be easy to find through the lexical SQLite path while keeping the evening quieter.",
                1,
            )
            fixture_copy.write_text(updated_text, encoding="utf-8")

            second_result = run_ingest(repo_root=repo_root, data_root=Path(data_dir), source_roots=build_default_source_roots(repo_root))
            second_manifest = _load_manifest(second_result.manifest_path)
            second_chunks = _chunk_map(second_manifest)

            self.assertEqual(set(first_chunks), set(second_chunks))
            changed_chunk_ids = [
                chunk_id
                for chunk_id in first_chunks
                if first_chunks[chunk_id]["chunk_hash"] != second_chunks[chunk_id]["chunk_hash"]
            ]
            self.assertEqual(len(changed_chunk_ids), 1)
            changed_chunk = second_chunks[changed_chunk_ids[0]]
            self.assertEqual(changed_chunk["section_label"], "Fixture Multi Paragraph Section")
            self.assertEqual(changed_chunk["paragraph_ordinal"], 2)
            self.assertEqual(second_result.updated_chunks, 1)
            self.assertEqual(second_result.deleted_chunks, 0)

    def test_semantic_extraction_preserves_raw_user_input(self) -> None:
        with tempfile.TemporaryDirectory() as repo_dir, tempfile.TemporaryDirectory() as data_dir:
            repo_root = Path(repo_dir)
            (repo_root / "corpus").mkdir(parents=True, exist_ok=True)
            _copy_note(FIXTURE_NOTE, repo_root / "tests" / "fixtures", "JOURNAL/2025-09/01_Monday.md")
            run_ingest(repo_root=repo_root, data_root=Path(data_dir), source_roots=build_default_source_roots(repo_root))

            user_input = "Please retrieve the candy snack food before bed note."
            result = run_thread_turn(
                repo_root=repo_root,
                data_root=Path(data_dir),
                user_input=user_input,
                llm_backend=StubLLMBackend(prefix="Probe stub response"),
                semantic_extractor_backend=StubSemanticExtractorBackend(),
            )

            isolated_packet = _load_turn_artifact(result.isolated_semantic_extraction_packet_path)
            contextual_packet = _load_turn_artifact(result.contextual_semantic_extraction_packet_path)
            semantic_context_packet = _load_turn_artifact(result.semantic_context_packet_path)
            synthesis_context_packet = _load_turn_artifact(result.synthesis_context_packet_path)

            self.assertEqual(isolated_packet["raw_user_input"], user_input)
            self.assertEqual(isolated_packet["request_packet"]["raw_user_input"], user_input)
            self.assertEqual(isolated_packet["parsed_payload"]["raw_user_input"], user_input)
            self.assertEqual(contextual_packet["raw_user_input"], user_input)
            self.assertEqual(contextual_packet["request_packet"]["raw_user_input"], user_input)
            self.assertEqual(contextual_packet["parsed_payload"]["raw_user_input"], user_input)
            self.assertEqual(semantic_context_packet["raw_user_input"], user_input)
            self.assertEqual(synthesis_context_packet["raw_user_input"], user_input)

    def test_extraction_raw_user_input_mismatch_is_diagnostic(self) -> None:
        with tempfile.TemporaryDirectory() as repo_dir, tempfile.TemporaryDirectory() as data_dir:
            repo_root = Path(repo_dir)
            stub_backend = StubSemanticExtractorBackend(
                isolated_payload={
                    "raw_user_input": "WRONG RAW INPUT",
                    "probable_user_intent": "mismatch isolated pass",
                    "candidate_targets": ["candy"],
                    "candidate_relations": [],
                    "question_shape": None,
                    "explicit_user_constraints": [],
                    "implicit_needs_or_pressures": [],
                    "terms_or_phrases_not_to_discard": ["candy"],
                    "ambiguities": [],
                    "extraction_confidence": "low",
                    "limitations": ["model-generated extraction", "additive only", "not authoritative"],
                },
                contextual_payload={
                    "raw_user_input": "ALSO WRONG",
                    "contextual_user_intent": "mismatch contextual pass",
                    "thread_relevant_context": [],
                    "semantic_pressure": None,
                    "candidate_targets": ["candy"],
                    "candidate_relations": [],
                    "coverage_target": {"must_preserve": [], "should_include": [], "avoid_satisfying_with": []},
                    "activation_hints": {
                        "lexical_terms": ["candy"],
                        "phrases": [],
                        "conceptual_neighbors": [],
                        "relation_hints": [],
                        "temporal_hints": [],
                        "entity_hints": [],
                    },
                    "delta_from_isolated_read": {"added_by_context": [], "removed_or_deemphasized_by_context": [], "unchanged": []},
                    "ambiguities": [],
                    "extraction_confidence": "low",
                    "limitations": ["model-generated extraction", "additive only", "not authoritative"],
                },
            )
            result = run_thread_turn(
                repo_root=repo_root,
                data_root=Path(data_dir),
                user_input="Please retrieve the candy snack food before bed note.",
                llm_backend=StubLLMBackend(prefix="Probe stub response"),
                semantic_extractor_backend=stub_backend,
            )

            isolated_packet = _load_turn_artifact(result.isolated_semantic_extraction_packet_path)
            contextual_packet = _load_turn_artifact(result.contextual_semantic_extraction_packet_path)

            self.assertEqual(isolated_packet["parsed_payload"]["raw_user_input"], "Please retrieve the candy snack food before bed note.")
            self.assertTrue(isolated_packet["diagnostics"]["raw_user_input_validation"]["model_supplied_raw_user_input_present"])
            self.assertFalse(isolated_packet["diagnostics"]["raw_user_input_validation"]["model_supplied_raw_user_input_matches"])
            self.assertTrue(isolated_packet["diagnostics"]["raw_user_input_validation"]["raw_user_input_repaired"])
            self.assertEqual(contextual_packet["parsed_payload"]["raw_user_input"], "Please retrieve the candy snack food before bed note.")
            self.assertTrue(contextual_packet["diagnostics"]["raw_user_input_validation"]["raw_user_input_repaired"])

    def test_extraction_missing_raw_user_input_is_diagnostic(self) -> None:
        with tempfile.TemporaryDirectory() as repo_dir, tempfile.TemporaryDirectory() as data_dir:
            repo_root = Path(repo_dir)
            stub_backend = StubSemanticExtractorBackend(
                isolated_payload={
                    "probable_user_intent": "missing raw input isolated pass",
                    "candidate_targets": ["candy"],
                    "candidate_relations": [],
                    "question_shape": None,
                    "explicit_user_constraints": [],
                    "implicit_needs_or_pressures": [],
                    "terms_or_phrases_not_to_discard": ["candy"],
                    "ambiguities": [],
                    "extraction_confidence": "low",
                    "limitations": ["model-generated extraction", "additive only", "not authoritative"],
                },
                contextual_payload={
                    "contextual_user_intent": "missing raw input contextual pass",
                    "thread_relevant_context": [],
                    "semantic_pressure": None,
                    "candidate_targets": ["candy"],
                    "candidate_relations": [],
                    "coverage_target": {"must_preserve": [], "should_include": [], "avoid_satisfying_with": []},
                    "activation_hints": {
                        "lexical_terms": ["candy"],
                        "phrases": [],
                        "conceptual_neighbors": [],
                        "relation_hints": [],
                        "temporal_hints": [],
                        "entity_hints": [],
                    },
                    "delta_from_isolated_read": {"added_by_context": [], "removed_or_deemphasized_by_context": [], "unchanged": []},
                    "ambiguities": [],
                    "extraction_confidence": "low",
                    "limitations": ["model-generated extraction", "additive only", "not authoritative"],
                },
            )
            result = run_thread_turn(
                repo_root=repo_root,
                data_root=Path(data_dir),
                user_input="Please retrieve the candy snack food before bed note.",
                llm_backend=StubLLMBackend(prefix="Probe stub response"),
                semantic_extractor_backend=stub_backend,
            )

            isolated_packet = _load_turn_artifact(result.isolated_semantic_extraction_packet_path)
            contextual_packet = _load_turn_artifact(result.contextual_semantic_extraction_packet_path)

            self.assertEqual(isolated_packet["parsed_payload"]["raw_user_input"], "Please retrieve the candy snack food before bed note.")
            self.assertFalse(isolated_packet["diagnostics"]["raw_user_input_validation"]["model_supplied_raw_user_input_present"])
            self.assertTrue(isolated_packet["diagnostics"]["raw_user_input_validation"]["raw_user_input_repaired"])
            self.assertFalse(contextual_packet["diagnostics"]["raw_user_input_validation"]["model_supplied_raw_user_input_present"])
            self.assertTrue(contextual_packet["diagnostics"]["raw_user_input_validation"]["raw_user_input_repaired"])

    def test_semantic_extraction_is_additive_not_destructive(self) -> None:
        with tempfile.TemporaryDirectory() as repo_dir, tempfile.TemporaryDirectory() as data_dir:
            repo_root = Path(repo_dir)
            (repo_root / "corpus").mkdir(parents=True, exist_ok=True)
            _copy_note(FIXTURE_NOTE, repo_root / "tests" / "fixtures", "JOURNAL/2025-09/01_Monday.md")
            run_ingest(repo_root=repo_root, data_root=Path(data_dir), source_roots=build_default_source_roots(repo_root))

            stub_backend = StubSemanticExtractorBackend(
                isolated_payload={
                    "raw_user_input": "",
                    "probable_user_intent": "limited hint isolated pass",
                    "candidate_targets": ["candy"],
                    "candidate_relations": [],
                    "question_shape": None,
                    "explicit_user_constraints": [],
                    "implicit_needs_or_pressures": [],
                    "terms_or_phrases_not_to_discard": ["candy"],
                    "ambiguities": [],
                    "extraction_confidence": "low",
                    "limitations": ["model-generated extraction", "additive only", "not authoritative"],
                },
                contextual_payload={
                    "raw_user_input": "",
                    "contextual_user_intent": "limited hint contextual pass",
                    "thread_relevant_context": [],
                    "semantic_pressure": None,
                    "candidate_targets": ["candy"],
                    "candidate_relations": [],
                    "coverage_target": {"must_preserve": ["candy"], "should_include": [], "avoid_satisfying_with": []},
                    "activation_hints": {
                        "lexical_terms": ["candy"],
                        "phrases": [],
                        "conceptual_neighbors": [],
                        "relation_hints": [],
                        "temporal_hints": [],
                        "entity_hints": [],
                    },
                    "delta_from_isolated_read": {
                        "added_by_context": [],
                        "removed_or_deemphasized_by_context": [],
                        "unchanged": ["candy"],
                    },
                    "ambiguities": [],
                    "extraction_confidence": "low",
                    "limitations": ["model-generated extraction", "additive only", "not authoritative"],
                },
            )
            result = run_thread_turn(
                repo_root=repo_root,
                data_root=Path(data_dir),
                user_input="Please retrieve the candy snack food before bed note.",
                llm_backend=StubLLMBackend(prefix="Probe stub response"),
                semantic_extractor_backend=stub_backend,
            )

            retrieval_preparation = result.semantic_context_packet["retrieval_preparation"]
            self.assertIn("candy", retrieval_preparation["extraction_hint_terms"])
            self.assertIn("snack", retrieval_preparation["raw_lexical_terms"])
            self.assertIn("food", retrieval_preparation["raw_lexical_terms"])
            self.assertIn("before", retrieval_preparation["raw_lexical_terms"])
            self.assertIn("snack", retrieval_preparation["combined_candidate_terms"])
            self.assertIn("food", retrieval_preparation["combined_candidate_terms"])
            self.assertEqual(retrieval_preparation["candidate_term_sources"]["snack"], ["raw_user_input"])

    def test_extraction_hint_harvesting_is_pruned(self) -> None:
        with tempfile.TemporaryDirectory() as repo_dir, tempfile.TemporaryDirectory() as data_dir:
            repo_root = Path(repo_dir)
            stub_backend = StubSemanticExtractorBackend(
                isolated_payload={
                    "raw_user_input": "",
                    "probable_user_intent": "pruned isolated pass",
                    "candidate_targets": ["usefultarget"],
                    "candidate_relations": ["usefulrelation"],
                    "question_shape": None,
                    "explicit_user_constraints": [],
                    "implicit_needs_or_pressures": [],
                    "terms_or_phrases_not_to_discard": ["usefulkeep"],
                    "ambiguities": ["junkambiguity"],
                    "extraction_confidence": "low",
                    "limitations": ["junklimitation"],
                },
                contextual_payload={
                    "raw_user_input": "",
                    "contextual_user_intent": "pruned contextual pass",
                    "thread_relevant_context": ["junkcontext"],
                    "semantic_pressure": None,
                    "candidate_targets": ["ignoredcontexttarget"],
                    "candidate_relations": [],
                    "coverage_target": {
                        "must_preserve": ["junkmustpreserve"],
                        "should_include": ["junkshouldinclude"],
                        "avoid_satisfying_with": ["junkavoid"],
                    },
                    "activation_hints": {
                        "lexical_terms": ["helpfullexical"],
                        "phrases": ["helpfulphrase"],
                        "conceptual_neighbors": ["junkneighbor"],
                        "relation_hints": ["helpfulrelationhint"],
                        "temporal_hints": ["junktemporal"],
                        "entity_hints": ["helpfulentity"],
                    },
                    "delta_from_isolated_read": {
                        "added_by_context": ["junkdelta"],
                        "removed_or_deemphasized_by_context": ["junkremoved"],
                        "unchanged": ["junkunchanged"],
                    },
                    "ambiguities": ["junkambiguitycontext"],
                    "extraction_confidence": "low",
                    "limitations": ["junklimitationcontext"],
                },
            )
            result = run_thread_turn(
                repo_root=repo_root,
                data_root=Path(data_dir),
                user_input="Please retrieve targetanchor note.",
                llm_backend=StubLLMBackend(prefix="Probe stub response"),
                semantic_extractor_backend=stub_backend,
            )

            retrieval_preparation = result.semantic_context_packet["retrieval_preparation"]
            extraction_hint_terms = set(retrieval_preparation["extraction_hint_terms"])
            self.assertIn("usefultarget", extraction_hint_terms)
            self.assertIn("usefulrelation", extraction_hint_terms)
            self.assertIn("usefulkeep", extraction_hint_terms)
            self.assertIn("helpfullexical", extraction_hint_terms)
            self.assertIn("helpfulphrase", extraction_hint_terms)
            self.assertIn("helpfulrelationhint", extraction_hint_terms)
            self.assertIn("helpfulentity", extraction_hint_terms)
            self.assertNotIn("junklimitation", extraction_hint_terms)
            self.assertNotIn("junkambiguity", extraction_hint_terms)
            self.assertNotIn("junkdelta", extraction_hint_terms)
            self.assertNotIn("junkavoid", extraction_hint_terms)
            self.assertNotIn("junkmustpreserve", extraction_hint_terms)
            self.assertNotIn("junkneighbor", extraction_hint_terms)
            self.assertIn("targetanchor", retrieval_preparation["raw_lexical_terms"])
            self.assertIn("targetanchor", retrieval_preparation["combined_candidate_terms"])
            self.assertEqual(
                retrieval_preparation["candidate_term_sources"]["helpfullexical"],
                ["contextual.activation_hints.lexical_terms"],
            )

    def test_stub_semantic_extractor_artifacts_are_persisted_and_hashed(self) -> None:
        with tempfile.TemporaryDirectory() as repo_dir, tempfile.TemporaryDirectory() as data_dir:
            repo_root = Path(repo_dir)
            (repo_root / "corpus").mkdir(parents=True, exist_ok=True)
            _copy_note(FIXTURE_NOTE, repo_root / "tests" / "fixtures", "JOURNAL/2025-09/01_Monday.md")
            run_ingest(repo_root=repo_root, data_root=Path(data_dir), source_roots=build_default_source_roots(repo_root))

            result = run_thread_turn(
                repo_root=repo_root,
                data_root=Path(data_dir),
                user_input="Please retrieve the candy snack food before bed note.",
                llm_backend=StubLLMBackend(prefix="Probe stub response"),
                semantic_extractor_backend=StubSemanticExtractorBackend(),
            )

            ledger = read_ledger(result.thread_ledger_path)
            isolated_packet = _load_turn_artifact(result.isolated_semantic_extraction_packet_path)
            isolated_raw = _load_turn_artifact(result.isolated_semantic_extraction_raw_path)
            contextual_packet = _load_turn_artifact(result.contextual_semantic_extraction_packet_path)
            contextual_raw = _load_turn_artifact(result.contextual_semantic_extraction_raw_path)

            self.assertTrue(result.isolated_semantic_extraction_packet_path.exists())
            self.assertTrue(result.isolated_semantic_extraction_raw_path.exists())
            self.assertTrue(result.contextual_semantic_extraction_packet_path.exists())
            self.assertTrue(result.contextual_semantic_extraction_raw_path.exists())
            self.assertEqual(ledger[-1]["isolated_semantic_extraction_packet_hash"], sha256_json(isolated_packet))
            self.assertEqual(ledger[-1]["isolated_semantic_extraction_raw_hash"], sha256_json(isolated_raw))
            self.assertEqual(ledger[-1]["contextual_semantic_extraction_packet_hash"], sha256_json(contextual_packet))
            self.assertEqual(ledger[-1]["contextual_semantic_extraction_raw_hash"], sha256_json(contextual_raw))

    def test_extractor_disabled_falls_back_to_lexical_retrieval(self) -> None:
        with tempfile.TemporaryDirectory() as repo_dir, tempfile.TemporaryDirectory() as data_dir:
            repo_root = Path(repo_dir)
            (repo_root / "corpus").mkdir(parents=True, exist_ok=True)
            _copy_note(FIXTURE_NOTE, repo_root / "tests" / "fixtures", "JOURNAL/2025-09/01_Monday.md")
            run_ingest(repo_root=repo_root, data_root=Path(data_dir), source_roots=build_default_source_roots(repo_root))

            result = run_thread_turn(
                repo_root=repo_root,
                data_root=Path(data_dir),
                user_input="Please retrieve the candy snack food before bed note.",
                llm_backend=StubLLMBackend(prefix="Probe stub response"),
                semantic_extractor_backend=DisabledSemanticExtractorBackend(),
            )

            isolated_packet = _load_turn_artifact(result.isolated_semantic_extraction_packet_path)
            contextual_packet = _load_turn_artifact(result.contextual_semantic_extraction_packet_path)
            self.assertEqual(isolated_packet["status"], "disabled")
            self.assertEqual(contextual_packet["status"], "disabled")
            self.assertEqual(result.coverage_report["status"], "minimal_pass")
            self.assertTrue(result.retrieval_packet["selected_chunks"])

    def test_contextual_extraction_receives_prior_thread_state(self) -> None:
        with tempfile.TemporaryDirectory() as repo_dir, tempfile.TemporaryDirectory() as data_dir:
            repo_root = Path(repo_dir)
            first_turn = run_thread_turn(
                repo_root=repo_root,
                data_root=Path(data_dir),
                user_input="First turn to seed thread state.",
                llm_backend=StubLLMBackend(prefix="Probe stub response"),
                semantic_extractor_backend=StubSemanticExtractorBackend(),
            )
            second_turn = run_thread_turn(
                repo_root=repo_root,
                data_root=Path(data_dir),
                user_input="Second turn should receive prior thread state.",
                llm_backend=StubLLMBackend(prefix="Probe stub response"),
                thread_id=first_turn.thread_id,
                semantic_extractor_backend=StubSemanticExtractorBackend(),
            )

            contextual_packet = _load_turn_artifact(second_turn.contextual_semantic_extraction_packet_path)
            prior_thread_state = contextual_packet["request_packet"]["prior_thread_state"]
            self.assertEqual(prior_thread_state["latest_turn_id"], 1)
            self.assertEqual(prior_thread_state["latest_user_input"], "First turn to seed thread state.")

    def test_full_route_stub_turn_uses_stub_extractor_and_stub_llm(self) -> None:
        with tempfile.TemporaryDirectory() as repo_dir, tempfile.TemporaryDirectory() as data_dir:
            repo_root = Path(repo_dir)
            (repo_root / "corpus").mkdir(parents=True, exist_ok=True)
            _copy_note(FIXTURE_NOTE, repo_root / "tests" / "fixtures", "JOURNAL/2025-09/01_Monday.md")
            run_ingest(repo_root=repo_root, data_root=Path(data_dir), source_roots=build_default_source_roots(repo_root))

            result = run_thread_turn(
                repo_root=repo_root,
                data_root=Path(data_dir),
                user_input="Please retrieve the candy snack food before bed note.",
                llm_backend=StubLLMBackend(prefix="Probe stub response"),
                semantic_extractor_backend=StubSemanticExtractorBackend(),
            )

            self.assertEqual(result.isolated_semantic_extraction_packet["status"], "stub")
            self.assertEqual(result.contextual_semantic_extraction_packet["status"], "stub")
            self.assertEqual(result.llm_metadata["mode"], "stub")
            self.assertTrue(result.isolated_semantic_extraction_packet_path.exists())
            self.assertTrue(result.contextual_semantic_extraction_packet_path.exists())
            self.assertTrue(result.synthesis_context_packet_path.exists())
            self.assertEqual(result.coverage_report["status"], "minimal_pass")

    def test_lexical_retrieval_fixture_hit_persists_artifacts_and_hashes(self) -> None:
        with tempfile.TemporaryDirectory() as repo_dir, tempfile.TemporaryDirectory() as data_dir:
            repo_root = Path(repo_dir)
            (repo_root / "corpus").mkdir(parents=True, exist_ok=True)
            _copy_note(FIXTURE_NOTE, repo_root / "tests" / "fixtures", "JOURNAL/2025-09/01_Monday.md")
            _copy_note(
                CORPUS_JOURNAL_NOTE,
                repo_root / "corpus",
                "LAYER-1 PILLARS/PILLAR 2-DYNAMIC COHERENCE/JOURNAL/2025/2025-08/24_Sunday.md",
            )
            _copy_note(
                LONGFORM_NOTE,
                repo_root / "corpus",
                "LAYER-1 PILLARS/PILLAR 2-DYNAMIC COHERENCE/JOURNAL/Propositions & Models.md",
            )

            run_ingest(repo_root=repo_root, data_root=Path(data_dir), source_roots=build_default_source_roots(repo_root))
            result = run_thread_turn(
                repo_root=repo_root,
                data_root=Path(data_dir),
                user_input="Please retrieve the candy snack food before bed note.",
                llm_backend=StubLLMBackend(prefix="Probe stub response"),
            )

            semantic_context_packet = _load_turn_artifact(result.semantic_context_packet_path)
            semantic_traversal_manifest = _load_turn_artifact(result.semantic_traversal_manifest_path)
            retrieval_packet = _load_turn_artifact(result.retrieval_packet_path)
            coverage_report = _load_turn_artifact(result.coverage_report_path)
            synthesis_context_packet = _load_turn_artifact(result.synthesis_context_packet_path)
            state_delta = _load_turn_artifact(result.state_delta_path)
            thread_state = _load_turn_artifact(result.thread_state_path)
            ledger = read_ledger(result.thread_ledger_path)

            self.assertEqual(coverage_report["status"], "minimal_pass")
            self.assertGreater(len(retrieval_packet["selected_chunks"]), 0)
            self.assertTrue(synthesis_context_packet["approved_retrieval_packet"])
            self.assertTrue(any(chunk["source_root_label"] == "tests-fixtures" for chunk in retrieval_packet["selected_chunks"]))
            self.assertEqual(
                synthesis_context_packet["semantic_context_packet"]["retrieval_preparation"]["raw_lexical_terms"],
                semantic_context_packet["retrieval_preparation"]["raw_lexical_terms"],
            )
            self.assertGreater(len(semantic_traversal_manifest["selected_chunk_ids"]), 0)
            self.assertEqual(ledger[-1]["semantic_context_packet_hash"], sha256_json(semantic_context_packet))
            self.assertEqual(ledger[-1]["semantic_traversal_manifest_hash"], sha256_json(semantic_traversal_manifest))
            self.assertEqual(ledger[-1]["retrieval_packet_hash"], sha256_json(retrieval_packet))
            self.assertEqual(ledger[-1]["coverage_report_hash"], sha256_json(coverage_report))
            self.assertEqual(ledger[-1]["synthesis_context_packet_hash"], sha256_json(synthesis_context_packet))
            self.assertEqual(ledger[-1]["state_delta_hash"], sha256_json(state_delta))
            thread_state_without_hash = dict(thread_state)
            thread_state_without_hash.pop("latest_thread_state_hash", None)
            self.assertEqual(ledger[-1]["next_thread_state_hash"], sha256_json(thread_state_without_hash))

    def test_lexical_retrieval_no_index_is_explicit_and_non_crashing(self) -> None:
        with tempfile.TemporaryDirectory() as repo_dir, tempfile.TemporaryDirectory() as data_dir:
            repo_root = Path(repo_dir)
            (repo_root / "corpus").mkdir(parents=True, exist_ok=True)
            _copy_note(FIXTURE_NOTE, repo_root / "tests" / "fixtures", "JOURNAL/2025-09/01_Monday.md")

            result = run_thread_turn(
                repo_root=repo_root,
                data_root=Path(data_dir),
                user_input="Dream Recall without an index.",
                llm_backend=StubLLMBackend(prefix="Probe stub response"),
            )

            coverage_report = _load_turn_artifact(result.coverage_report_path)
            retrieval_packet = _load_turn_artifact(result.retrieval_packet_path)
            ledger = read_ledger(result.thread_ledger_path)

            self.assertEqual(coverage_report["status"], "no_index")
            self.assertEqual(retrieval_packet["selected_chunks"], [])
            self.assertEqual(result.semantic_traversal_manifest["selection_reasons"], ["ingestion SQLite database not found"])
            self.assertEqual(ledger[-1]["semantic_context_packet_hash"], sha256_json(_load_turn_artifact(result.semantic_context_packet_path)))
            self.assertEqual(ledger[-1]["semantic_traversal_manifest_hash"], sha256_json(_load_turn_artifact(result.semantic_traversal_manifest_path)))
            self.assertEqual(ledger[-1]["retrieval_packet_hash"], sha256_json(retrieval_packet))
            self.assertEqual(ledger[-1]["coverage_report_hash"], sha256_json(coverage_report))

    def test_lexical_retrieval_no_match_is_explicit_and_non_crashing(self) -> None:
        with tempfile.TemporaryDirectory() as repo_dir, tempfile.TemporaryDirectory() as data_dir:
            repo_root = Path(repo_dir)
            (repo_root / "corpus").mkdir(parents=True, exist_ok=True)
            _copy_note(FIXTURE_NOTE, repo_root / "tests" / "fixtures", "JOURNAL/2025-09/01_Monday.md")
            run_ingest(repo_root=repo_root, data_root=Path(data_dir), source_roots=build_default_source_roots(repo_root))

            result = run_thread_turn(
                repo_root=repo_root,
                data_root=Path(data_dir),
                user_input="qzxyv qzxyv qzxyv",
                llm_backend=StubLLMBackend(prefix="Probe stub response"),
            )

            coverage_report = _load_turn_artifact(result.coverage_report_path)
            retrieval_packet = _load_turn_artifact(result.retrieval_packet_path)
            ledger = read_ledger(result.thread_ledger_path)

            self.assertEqual(coverage_report["status"], "no_matches")
            self.assertEqual(retrieval_packet["selected_chunks"], [])
            self.assertEqual(ledger[-1]["semantic_context_packet_hash"], sha256_json(_load_turn_artifact(result.semantic_context_packet_path)))
            self.assertEqual(ledger[-1]["semantic_traversal_manifest_hash"], sha256_json(_load_turn_artifact(result.semantic_traversal_manifest_path)))
            self.assertEqual(ledger[-1]["retrieval_packet_hash"], sha256_json(retrieval_packet))
            self.assertEqual(ledger[-1]["coverage_report_hash"], sha256_json(coverage_report))

    def test_lexical_retrieval_no_query_terms_is_explicit_and_non_crashing(self) -> None:
        with tempfile.TemporaryDirectory() as repo_dir, tempfile.TemporaryDirectory() as data_dir:
            repo_root = Path(repo_dir)
            (repo_root / "corpus").mkdir(parents=True, exist_ok=True)
            _copy_note(FIXTURE_NOTE, repo_root / "tests" / "fixtures", "JOURNAL/2025-09/01_Monday.md")
            run_ingest(repo_root=repo_root, data_root=Path(data_dir), source_roots=build_default_source_roots(repo_root))

            result = run_thread_turn(
                repo_root=repo_root,
                data_root=Path(data_dir),
                user_input="   and the or   ",
                llm_backend=StubLLMBackend(prefix="Probe stub response"),
                semantic_extractor_backend=DisabledSemanticExtractorBackend(),
            )

            semantic_context_packet = _load_turn_artifact(result.semantic_context_packet_path)
            semantic_traversal_manifest = _load_turn_artifact(result.semantic_traversal_manifest_path)
            retrieval_packet = _load_turn_artifact(result.retrieval_packet_path)
            coverage_report = _load_turn_artifact(result.coverage_report_path)
            ledger = read_ledger(result.thread_ledger_path)

            self.assertEqual(result.semantic_context_packet["extracted_lexical_query_terms"], [])
            self.assertEqual(semantic_context_packet["extracted_lexical_query_terms"], [])
            self.assertFalse(semantic_traversal_manifest["query_terms_available"])
            self.assertEqual(semantic_traversal_manifest["selection_reasons"], ["no lexical or additive extraction candidate terms were available"])
            self.assertEqual(retrieval_packet["retrieval_status"], "no_query_terms")
            self.assertEqual(retrieval_packet["selected_chunks"], [])
            self.assertEqual(coverage_report["status"], "no_query_terms")
            self.assertEqual(ledger[-1]["semantic_context_packet_hash"], sha256_json(semantic_context_packet))
            self.assertEqual(ledger[-1]["semantic_traversal_manifest_hash"], sha256_json(semantic_traversal_manifest))
            self.assertEqual(ledger[-1]["retrieval_packet_hash"], sha256_json(retrieval_packet))
            self.assertEqual(ledger[-1]["coverage_report_hash"], sha256_json(coverage_report))

    def test_same_thread_continuation_preserves_parent_hash_with_retrieval(self) -> None:
        with tempfile.TemporaryDirectory() as repo_dir, tempfile.TemporaryDirectory() as data_dir:
            repo_root = Path(repo_dir)
            (repo_root / "corpus").mkdir(parents=True, exist_ok=True)
            _copy_note(FIXTURE_NOTE, repo_root / "tests" / "fixtures", "JOURNAL/2025-09/01_Monday.md")
            _copy_note(
                CORPUS_JOURNAL_NOTE,
                repo_root / "corpus",
                "LAYER-1 PILLARS/PILLAR 2-DYNAMIC COHERENCE/JOURNAL/2025/2025-08/24_Sunday.md",
            )
            run_ingest(repo_root=repo_root, data_root=Path(data_dir), source_roots=build_default_source_roots(repo_root))

            first_turn = run_thread_turn(
                repo_root=repo_root,
                data_root=Path(data_dir),
                user_input="Please retrieve the candy snack food before bed note.",
                llm_backend=StubLLMBackend(prefix="Probe stub response"),
            )
            before_records = read_ledger(first_turn.thread_ledger_path)
            second_turn = run_thread_turn(
                repo_root=repo_root,
                data_root=Path(data_dir),
                user_input="Please continue with Yesterday and Y-Day Review.",
                llm_backend=StubLLMBackend(prefix="Probe stub response"),
                thread_id=first_turn.thread_id,
            )
            after_records = read_ledger(second_turn.thread_ledger_path)

            self.assertEqual(len(after_records), len(before_records) + 1)
            self.assertEqual(after_records[-1]["parent_perturbation_hash"], before_records[-1]["state_perturbation_hash"])
            self.assertEqual(second_turn.coverage_report["status"], "minimal_pass")

    def test_turn_cli_reports_artifact_paths_and_hashes(self) -> None:
        with tempfile.TemporaryDirectory() as repo_dir, tempfile.TemporaryDirectory() as data_dir:
            repo_root = Path(repo_dir)
            (repo_root / "corpus").mkdir(parents=True, exist_ok=True)
            _copy_note(FIXTURE_NOTE, repo_root / "tests" / "fixtures", "JOURNAL/2025-09/01_Monday.md")
            run_ingest(repo_root=repo_root, data_root=Path(data_dir), source_roots=build_default_source_roots(repo_root))

            process = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "semantic_traversal",
                    "--message",
                    "Please retrieve the candy snack food before bed note.",
                    "--llm-mode",
                    "stub",
                    "--semantic-extractor-mode",
                    "stub",
                    "--repo-root",
                    str(repo_root),
                    "--data-root",
                    data_dir,
                ],
                cwd=REPO_ROOT,
                check=True,
                capture_output=True,
                text=True,
            )
            payload = json.loads(process.stdout)
            for key in (
                "turn_root",
                "isolated_semantic_extraction_packet_path",
                "isolated_semantic_extraction_raw_path",
                "contextual_semantic_extraction_packet_path",
                "contextual_semantic_extraction_raw_path",
                "semantic_context_packet_path",
                "semantic_traversal_manifest_path",
                "retrieval_packet_path",
                "coverage_report_path",
                "synthesis_context_packet_path",
                "state_delta_path",
            ):
                self.assertTrue(Path(payload[key]).exists())
            self.assertEqual(payload["semantic_extractor_mode"], "stub")
            self.assertIn(payload["isolated_extraction_status"], {"parsed", "stub", "disabled", "unavailable", "invalid_json"})
            self.assertIn(payload["contextual_extraction_status"], {"parsed", "stub", "disabled", "unavailable", "invalid_json"})
            self.assertIn(payload["coverage_status"], {"minimal_pass", "no_index", "no_query_terms", "no_matches"})
            self.assertTrue(payload["latest_perturbation_hash"])
            self.assertTrue(payload["latest_thread_state_hash"])


if __name__ == "__main__":
    unittest.main()
