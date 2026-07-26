from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
import uuid
from contextlib import closing
from pathlib import Path
from typing import Any
from unittest.mock import patch

from copy import deepcopy

from semantic_traversal.config import RuntimeConfig, load_runtime_config
from semantic_traversal.embeddings import EmbeddingResponse
from semantic_traversal.ingest import IngestSourceRoot, _validate_lexical_index, _validate_vector_index, run_ingest


REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_ROOT = REPO_ROOT / "tests" / "fixtures" / "JOURNAL"


class FakeEmbeddingBackend:
    mode_name = "fake"

    def embed_texts(self, texts: list[str]) -> EmbeddingResponse:
        vectors = [[float(len(text)), float(text.lower().count("note")), float(text.lower().count("link"))] for text in texts]
        return EmbeddingResponse(vectors=vectors, metadata={"backend_mode": self.mode_name}, status="embedded")

    def embed_query_text(self, text: str) -> EmbeddingResponse:
        return self.embed_texts([text])


class CountingEmbeddingBackend(FakeEmbeddingBackend):
    def __init__(self) -> None:
        self.embed_texts_calls = 0

    def embed_texts(self, texts: list[str]) -> EmbeddingResponse:
        self.embed_texts_calls += 1
        return super().embed_texts(texts)


def _write_note(root: Path, relative_path: str, content: str) -> Path:
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    if content.lstrip().startswith("---"):
        path.write_text(content.strip() + "\n", encoding="utf-8")
    else:
        source_uuid = uuid.uuid5(uuid.NAMESPACE_URL, relative_path)
        path.write_text(f"---\nuuid: {source_uuid}\n---\n\n{content.strip()}\n", encoding="utf-8")
    return path


def _load_rows(connection: sqlite3.Connection, query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    connection.row_factory = sqlite3.Row
    rows = connection.execute(query, params).fetchall()
    return [dict(row) for row in rows]


class IngestDataLayerTests(unittest.TestCase):
    def test_success_manifest_reports_complete_fts_projection(self) -> None:
        config = load_runtime_config(repo_root=REPO_ROOT)
        with tempfile.TemporaryDirectory() as temp_dir:
            data_root = Path(temp_dir)
            source_root = data_root / "source"
            _write_note(source_root, "Fresh.md", "# Fresh\n\ninitial-token",)
            result = run_ingest(
                repo_root=REPO_ROOT,
                data_root=data_root,
                source_roots=(IngestSourceRoot(label="source", path=source_root),),
                embedding_backend=FakeEmbeddingBackend(),
                config=config,
            )
            manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
            lexical = manifest["lexical_index"]
            self.assertEqual(lexical["status"], "valid")
            self.assertEqual(lexical["canonical_chunk_count"], lexical["fts_row_count"])
            self.assertEqual(lexical["missing_chunk_count"], 0)
            self.assertEqual(lexical["orphan_row_count"], 0)
            self.assertEqual(lexical["duplicate_chunk_id_count"], 0)
            self.assertEqual(lexical["field_mismatch_count"], 0)
            self.assertEqual(lexical["query_probe_status"], "passed")
            vector = manifest["vector_index"]
            self.assertEqual(vector["status"], "valid")
            self.assertEqual(vector["canonical_chunk_count"], vector["compatible_vector_count"])
            self.assertEqual(vector["missing_vector_count"], 0)
            self.assertEqual(vector["identity_mismatch_count"], 0)

    def test_vector_reuse_requires_content_and_embedding_identity(self) -> None:
        config = load_runtime_config(repo_root=REPO_ROOT)
        with tempfile.TemporaryDirectory() as temp_dir:
            data_root = Path(temp_dir)
            source_root = data_root / "source"
            _write_note(source_root, "Stable.md", "# Stable\n\nidentity-token")
            first_backend = CountingEmbeddingBackend()
            first = run_ingest(repo_root=REPO_ROOT, data_root=data_root, source_roots=(IngestSourceRoot(label="source", path=source_root),), embedding_backend=first_backend, config=config)
            self.assertEqual(first_backend.embed_texts_calls, 1)
            second_backend = CountingEmbeddingBackend()
            run_ingest(repo_root=REPO_ROOT, data_root=data_root, source_roots=(IngestSourceRoot(label="source", path=source_root),), embedding_backend=second_backend, config=config)
            self.assertEqual(second_backend.embed_texts_calls, 0)
            changed_raw = deepcopy(config.raw)
            changed_raw["embeddings"]["model"] = "changed-model"
            changed_config = RuntimeConfig(repo_root=config.repo_root, config_path=config.config_path, raw=changed_raw)
            third_backend = CountingEmbeddingBackend()
            run_ingest(repo_root=REPO_ROOT, data_root=data_root, source_roots=(IngestSourceRoot(label="source", path=source_root),), embedding_backend=third_backend, config=changed_config)
            self.assertEqual(third_backend.embed_texts_calls, 1)
            with closing(sqlite3.connect(first.database_path)) as connection:
                connection.row_factory = sqlite3.Row
                row = connection.execute(f"SELECT embedding_identity_json, embedding_identity_hash FROM {config.vector_table}").fetchone()
                self.assertIn("encoding_strategy", row["embedding_identity_json"])
                self.assertTrue(row["embedding_identity_hash"])

    def test_vector_validator_counts_malformed_and_incompatible_rows(self) -> None:
        config = load_runtime_config(repo_root=REPO_ROOT)
        with tempfile.TemporaryDirectory() as temp_dir:
            data_root = Path(temp_dir)
            source_root = data_root / "source"
            _write_note(source_root, "Stable.md", "# Stable\n\nidentity-token")
            result = run_ingest(repo_root=REPO_ROOT, data_root=data_root, source_roots=(IngestSourceRoot(label="source", path=source_root),), embedding_backend=FakeEmbeddingBackend(), config=config)
            with closing(sqlite3.connect(result.database_path)) as connection:
                connection.row_factory = sqlite3.Row
                connection.execute(f"UPDATE {config.vector_table} SET vector_json = ?", ("broken-json",))
                connection.commit()
                validation = _validate_vector_index(connection=connection, config=config, embedding_backend=FakeEmbeddingBackend())
            self.assertEqual(validation["status"], "invalid")
            self.assertEqual(validation["invalid_json_count"], 1)

    def test_fts_freshness_tracks_update_metadata_rename_delete_and_unchanged_reingest(self) -> None:
        config = load_runtime_config(repo_root=REPO_ROOT)
        with tempfile.TemporaryDirectory() as temp_dir:
            data_root = Path(temp_dir)
            source_root = data_root / "source"
            source_uuid = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
            note = _write_note(
                source_root,
                "Fresh.md",
                f"---\nuuid: {source_uuid}\nnote_type: old-marker\n---\n\n# Old Title\n\nold-token",
            )
            first = run_ingest(
                repo_root=REPO_ROOT,
                data_root=data_root,
                source_roots=(IngestSourceRoot(label="source", path=source_root),),
                embedding_backend=FakeEmbeddingBackend(),
                config=config,
            )
            with closing(sqlite3.connect(first.database_path)) as connection:
                first_ids = [row[0] for row in connection.execute("SELECT chunk_id FROM chunks_fts ORDER BY chunk_id")]
                self.assertEqual(connection.execute("SELECT count(*) FROM chunks_fts WHERE chunks_fts MATCH '\"old-token\"'").fetchone()[0], 1)

            note.write_text(
                f"---\nuuid: {source_uuid}\nnote_type: new-marker\n---\n\n# New Title\n\nnew-token",
                encoding="utf-8",
            )
            second = run_ingest(
                repo_root=REPO_ROOT,
                data_root=data_root,
                source_roots=(IngestSourceRoot(label="source", path=source_root),),
                embedding_backend=FakeEmbeddingBackend(),
                config=config,
            )
            with closing(sqlite3.connect(second.database_path)) as connection:
                self.assertEqual(connection.execute("SELECT count(*) FROM chunks_fts WHERE chunks_fts MATCH '\"old-token\"'").fetchone()[0], 0)
                self.assertEqual(connection.execute("SELECT count(*) FROM chunks_fts WHERE chunks_fts MATCH '\"new-token\"'").fetchone()[0], 1)
                row = connection.execute("SELECT note_title, relative_path, metadata FROM chunks_fts ORDER BY chunk_id").fetchone()
                self.assertEqual(row[0], "Fresh")
                self.assertEqual(row[1], "Fresh.md")
                self.assertIn("new-marker", row[2])
                self.assertEqual(connection.execute("SELECT count(*) FROM chunks_fts").fetchone()[0], len(first_ids))

            renamed = source_root / "Renamed.md"
            note.rename(renamed)
            third = run_ingest(
                repo_root=REPO_ROOT,
                data_root=data_root,
                source_roots=(IngestSourceRoot(label="source", path=source_root),),
                embedding_backend=FakeEmbeddingBackend(),
                config=config,
            )
            with closing(sqlite3.connect(third.database_path)) as connection:
                row = connection.execute("SELECT relative_path, note_title FROM chunks_fts ORDER BY chunk_id").fetchone()
                self.assertEqual(row[0], "Renamed.md")
                self.assertEqual(row[1], "Renamed")

            fourth = run_ingest(
                repo_root=REPO_ROOT,
                data_root=data_root,
                source_roots=(IngestSourceRoot(label="source", path=source_root),),
                embedding_backend=FakeEmbeddingBackend(),
                config=config,
            )
            self.assertEqual(fourth.unchanged_chunks, fourth.chunk_count)
            with closing(sqlite3.connect(fourth.database_path)) as connection:
                self.assertEqual(connection.execute("SELECT count(*) FROM chunks_fts").fetchone()[0], fourth.chunk_count)
                self.assertEqual(connection.execute("SELECT count(DISTINCT chunk_id) FROM chunks_fts").fetchone()[0], fourth.chunk_count)

            renamed.unlink()
            fifth = run_ingest(
                repo_root=REPO_ROOT,
                data_root=data_root,
                source_roots=(IngestSourceRoot(label="source", path=source_root),),
                embedding_backend=FakeEmbeddingBackend(),
                config=config,
            )
            self.assertEqual(fifth.chunk_count, 0)
            with closing(sqlite3.connect(fifth.database_path)) as connection:
                self.assertEqual(connection.execute("SELECT count(*) FROM chunks").fetchone()[0], 0)
                self.assertEqual(connection.execute("SELECT count(*) FROM chunks_fts").fetchone()[0], 0)
                self.assertEqual(connection.execute("SELECT count(*) FROM chunks_fts WHERE chunks_fts MATCH '\"new-token\"'").fetchone()[0], 0)

    def test_fts_validator_detects_duplicate_or_orphan_or_mismatched_rows(self) -> None:
        config = load_runtime_config(repo_root=REPO_ROOT)
        with tempfile.TemporaryDirectory() as temp_dir:
            data_root = Path(temp_dir)
            source_root = data_root / "source"
            _write_note(source_root, "Fresh.md", "# Fresh\n\nvalidator-token")
            result = run_ingest(
                repo_root=REPO_ROOT,
                data_root=data_root,
                source_roots=(IngestSourceRoot(label="source", path=source_root),),
                embedding_backend=FakeEmbeddingBackend(),
                config=config,
            )
            with closing(sqlite3.connect(result.database_path)) as connection:
                connection.row_factory = sqlite3.Row
                row = connection.execute("SELECT * FROM chunks_fts ORDER BY chunk_id LIMIT 1").fetchone()
                connection.execute(
                    "INSERT INTO chunks_fts (chunk_id, paragraph_text, note_title, section_label, relative_path, metadata) VALUES (?, ?, ?, ?, ?, ?)",
                    (row["chunk_id"], "mismatch", row["note_title"], row["section_label"], row["relative_path"], row["metadata"]),
                )
                connection.execute(
                    "INSERT INTO chunks_fts (chunk_id, paragraph_text, note_title, section_label, relative_path, metadata) VALUES (?, ?, ?, ?, ?, ?)",
                    ("orphan-id", "orphan", "orphan", "orphan", "orphan.md", "{}"),
                )
                validation = _validate_lexical_index(connection=connection, config=config)
            self.assertEqual(validation["status"], "invalid")
            self.assertGreater(validation["duplicate_chunk_id_count"], 0)
            self.assertGreater(validation["orphan_row_count"], 0)
            self.assertGreater(validation["field_mismatch_count"], 0)

    def test_failed_initial_ingest_leaves_no_active_database_or_candidate(self) -> None:
        config = load_runtime_config(repo_root=REPO_ROOT)
        with tempfile.TemporaryDirectory() as temp_dir:
            data_root = Path(temp_dir)
            source_root = data_root / "source"
            _write_note(source_root, "Fresh.md", "# Fresh\n\ninitial-failure-token")
            with patch("semantic_traversal.ingest._materialize_records", side_effect=RuntimeError("injected materialization failure")):
                with self.assertRaisesRegex(RuntimeError, "injected materialization failure"):
                    run_ingest(
                        repo_root=REPO_ROOT,
                        data_root=data_root,
                        source_roots=(IngestSourceRoot(label="source", path=source_root),),
                        embedding_backend=FakeEmbeddingBackend(),
                        config=config,
                    )
            ingest_root = data_root / "ingestion"
            self.assertFalse((ingest_root / "latent_space.sqlite3").exists())
            self.assertFalse(list(ingest_root.glob("latent_space.sqlite3.candidate-*")))
            manifest = json.loads((ingest_root / "manifests" / "latest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["status"], "failed")
            self.assertFalse(manifest["active_database_replaced"])
            self.assertTrue(manifest["candidate_database_cleaned"])
            self.assertFalse((ingest_root / "manifests" / "latest-success.json").exists())

    def test_failed_schema_initialization_leaves_no_active_database(self) -> None:
        config = load_runtime_config(repo_root=REPO_ROOT)
        with tempfile.TemporaryDirectory() as temp_dir:
            data_root = Path(temp_dir)
            source_root = data_root / "source"
            _write_note(source_root, "Fresh.md", "# Fresh\n\nschema-failure-token")
            with patch("semantic_traversal.ingest._initialize_schema", side_effect=RuntimeError("injected schema failure")):
                with self.assertRaisesRegex(RuntimeError, "injected schema failure"):
                    run_ingest(
                        repo_root=REPO_ROOT,
                        data_root=data_root,
                        source_roots=(IngestSourceRoot(label="source", path=source_root),),
                        embedding_backend=FakeEmbeddingBackend(),
                        config=config,
                    )
            ingest_root = data_root / "ingestion"
            self.assertFalse((ingest_root / "latent_space.sqlite3").exists())
            self.assertFalse(list(ingest_root.glob("latent_space.sqlite3.candidate-*")))
            failure = json.loads((ingest_root / "manifests" / "latest.json").read_text(encoding="utf-8"))
            self.assertEqual(failure["failure_stage"], "schema_initialization")
            self.assertTrue(failure["candidate_database_cleaned"])

    def test_failed_refresh_preserves_prior_active_database_and_latest_success(self) -> None:
        config = load_runtime_config(repo_root=REPO_ROOT)
        with tempfile.TemporaryDirectory() as temp_dir:
            data_root = Path(temp_dir)
            source_root = data_root / "source"
            note = _write_note(source_root, "Fresh.md", "# Fresh\n\nprior-token")
            first = run_ingest(
                repo_root=REPO_ROOT,
                data_root=data_root,
                source_roots=(IngestSourceRoot(label="source", path=source_root),),
                embedding_backend=FakeEmbeddingBackend(),
                config=config,
            )
            before = first.database_path.read_bytes()
            success_manifest = (data_root / "ingestion" / "manifests" / "latest-success.json").read_text(encoding="utf-8")
            note.write_text(note.read_text(encoding="utf-8").replace("prior-token", "changed-token"), encoding="utf-8")
            with patch("semantic_traversal.ingest._refresh_lexical_index", side_effect=RuntimeError("injected FTS failure")):
                with self.assertRaisesRegex(RuntimeError, "injected FTS failure"):
                    run_ingest(
                        repo_root=REPO_ROOT,
                        data_root=data_root,
                        source_roots=(IngestSourceRoot(label="source", path=source_root),),
                        embedding_backend=FakeEmbeddingBackend(),
                        config=config,
                    )
            self.assertEqual(first.database_path.read_bytes(), before)
            self.assertEqual((data_root / "ingestion" / "manifests" / "latest-success.json").read_text(encoding="utf-8"), success_manifest)
            failure = json.loads((data_root / "ingestion" / "manifests" / "latest.json").read_text(encoding="utf-8"))
            self.assertEqual(failure["status"], "failed")
            self.assertEqual(failure["failure_stage"], "lexical_index_refresh")
            self.assertTrue(failure["prior_active_database_preserved"])
            self.assertFalse(failure["active_database_replaced"])
            self.assertTrue(failure["candidate_database_cleaned"])
            self.assertFalse(list((data_root / "ingestion").glob("latent_space.sqlite3.candidate-*") ))

    def test_failed_candidate_validation_and_activation_preserve_prior_database(self) -> None:
        config = load_runtime_config(repo_root=REPO_ROOT)
        for patch_target, expected_stage, message in (
            ("semantic_traversal.ingest._validate_lexical_index", "candidate_validation", "invalid candidate"),
            ("semantic_traversal.ingest.os.replace", "activation", "activation failed"),
        ):
            with self.subTest(expected_stage=expected_stage), tempfile.TemporaryDirectory() as temp_dir:
                data_root = Path(temp_dir)
                source_root = data_root / "source"
                note = _write_note(source_root, "Fresh.md", "# Fresh\n\nstable-token")
                first = run_ingest(
                    repo_root=REPO_ROOT,
                    data_root=data_root,
                    source_roots=(IngestSourceRoot(label="source", path=source_root),),
                    embedding_backend=FakeEmbeddingBackend(),
                    config=config,
                )
                before = first.database_path.read_bytes()
                note.write_text(note.read_text(encoding="utf-8").replace("stable-token", "new-token"), encoding="utf-8")
                side_effect = {"status": "invalid", "failure_reason": "injected candidate validation"} if expected_stage == "candidate_validation" else OSError(message)
                with patch(patch_target, return_value=side_effect) if expected_stage == "candidate_validation" else patch(patch_target, side_effect=side_effect):
                    with self.assertRaises(Exception):
                        run_ingest(
                            repo_root=REPO_ROOT,
                            data_root=data_root,
                            source_roots=(IngestSourceRoot(label="source", path=source_root),),
                            embedding_backend=FakeEmbeddingBackend(),
                            config=config,
                        )
                self.assertEqual(first.database_path.read_bytes(), before)
                failure = json.loads((data_root / "ingestion" / "manifests" / "latest.json").read_text(encoding="utf-8"))
                self.assertEqual(failure["failure_stage"], expected_stage)
                self.assertTrue(failure["prior_active_database_preserved"])
                self.assertFalse(failure["active_database_replaced"])

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
