from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from semantic_traversal.ingest import build_default_source_roots, run_ingest


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
            self.assertEqual(
                {entry["label"] for entry in payload["source_roots"]},
                {"corpus", "tests-fixtures"},
            )
            connection = sqlite3.connect(payload["database_path"])
            try:
                rows = connection.execute("SELECT DISTINCT source_root_label FROM notes").fetchall()
            finally:
                connection.close()
            self.assertEqual({row[0] for row in rows}, {"corpus", "tests-fixtures"})

    def test_fixture_journal_inline_labels_become_paragraph_chunks(self) -> None:
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
                ["Dream Recall", "Y-Day Review", "Daily Intent", "Daily Intent"],
            )
            self.assertEqual(
                [chunk["paragraph_ordinal"] for chunk in chunks],
                [1, 1, 1, 2],
            )
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

            first_result = run_ingest(
                repo_root=repo_root,
                data_root=Path(data_dir),
                source_roots=build_default_source_roots(repo_root),
            )
            second_result = run_ingest(
                repo_root=repo_root,
                data_root=Path(data_dir),
                source_roots=build_default_source_roots(repo_root),
            )

            first_manifest = _load_manifest(first_result.manifest_path)
            second_manifest = _load_manifest(second_result.manifest_path)
            first_chunks = {
                chunk_id: chunk["chunk_hash"]
                for chunk_id, chunk in _chunk_map(first_manifest).items()
            }
            second_chunks = {
                chunk_id: chunk["chunk_hash"]
                for chunk_id, chunk in _chunk_map(second_manifest).items()
            }

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

            first_result = run_ingest(
                repo_root=repo_root,
                data_root=Path(data_dir),
                source_roots=build_default_source_roots(repo_root),
            )
            first_manifest = _load_manifest(first_result.manifest_path)
            first_chunks = _chunk_map(first_manifest)

            original_text = fixture_copy.read_text(encoding="utf-8")
            updated_text = original_text.replace(
                "We're making a good choice in reduction of candy/snack food before bed :)",
                "We're making a good choice in reduction of candy/snack food before bed and keeping the evening quieter :)",
                1,
            )
            fixture_copy.write_text(updated_text, encoding="utf-8")

            second_result = run_ingest(
                repo_root=repo_root,
                data_root=Path(data_dir),
                source_roots=build_default_source_roots(repo_root),
            )
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
            self.assertEqual(changed_chunk["section_label"], "Daily Intent")
            self.assertEqual(changed_chunk["paragraph_ordinal"], 2)
            self.assertEqual(second_result.updated_chunks, 1)
            self.assertEqual(second_result.deleted_chunks, 0)


if __name__ == "__main__":
    unittest.main()
