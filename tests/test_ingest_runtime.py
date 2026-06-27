from __future__ import annotations

import contextlib
import io
import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
import textwrap
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import yaml

from semantic_traversal.config import ConfigError, load_runtime_config
from semantic_traversal.hashing import sha256_json
from semantic_traversal.ingest import build_default_source_roots, run_ingest
from semantic_traversal.cli import build_turn_parser
from semantic_traversal.llm import StubLLMBackend, resolve_openai_settings
from semantic_traversal.runtime import run_thread_turn
from semantic_traversal.semantic_extraction import (
    DisabledSemanticExtractorBackend,
    StubSemanticExtractorBackend,
    resolve_semantic_extractor_backend,
)
from semantic_traversal.storage import read_ledger


REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_NOTE = REPO_ROOT / "tests" / "fixtures" / "JOURNAL" / "2025-09" / "01_Monday.md"
DEFAULT_CONFIG_SOURCE = REPO_ROOT / "semantic_traversal.runtime.yaml"


def _load_manifest(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _copy_note(source_path: Path, destination_root: Path, relative_path: str) -> None:
    destination_path = destination_root / Path(relative_path)
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_path, destination_path)


def _write_markdown_fixture(destination_root: Path, relative_path: str, content: str) -> Path:
    destination_path = destination_root / Path(relative_path)
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    destination_path.write_text(textwrap.dedent(content).strip() + "\n", encoding="utf-8")
    return destination_path


def _write_synthetic_corpus_journal_note(repo_root: Path) -> Path:
    return _write_markdown_fixture(
        repo_root / "corpus",
        "SYNTHETIC/JOURNAL/fixture_corpus_journal.md",
        """
        ---
        journal_entry_date: 2025-09-01
        note_type: synthetic_corpus_fixture
        ---

        # September 01, 2025

        ## Fixture Corpus Alpha

        This synthetic corpus paragraph exists to test heading resolution.

        ## Fixture Corpus Beta

        This synthetic corpus paragraph contains Yesterday and Y-Day Review terms for lexical retrieval continuity tests, plus the candy snack food before bed phrase.

        ## Fixture Corpus Gamma

        Dream recall appears here as generic retrieval text, not as a required private journal schema.
        """,
    )


def _write_synthetic_longform_note(repo_root: Path) -> Path:
    return _write_markdown_fixture(
        repo_root / "corpus",
        "SYNTHETIC/LONGFORM/fixture_longform.md",
        """
        ---
        journal_entry_date: 2025-09-01
        note_type: synthetic_longform_fixture
        ---

        # September 01, 2025

        ## Fixture Proposition Section

        This paragraph covers Premise 1 for synthetic paragraph chunking.

        This paragraph covers Premise 2 for synthetic paragraph chunking.

        This paragraph covers Premise 3 for synthetic paragraph chunking.

        This paragraph covers Premise 4 for synthetic paragraph chunking.

        This paragraph covers Premise 5 for synthetic paragraph chunking.

        ## Fixture Nested Section

        This paragraph covers Premise 6 for synthetic nested paragraph chunking.

        This paragraph covers Premise 7 for synthetic nested paragraph chunking.

        This paragraph covers Premise 8 for synthetic nested paragraph chunking.

        This paragraph covers Premise 9 for synthetic nested paragraph chunking.
        """,
    )


def _write_graph_fixture_notes(repo_root: Path) -> tuple[Path, Path]:
    first = _write_markdown_fixture(
        repo_root / "corpus",
        "SYNTHETIC/GRAPH/source_note.md",
        """
        ---
        tags:
          - fixture-tag
        ---

        # Source Note

        ## Fixture Link Section

        This note links to [[target note]] and mentions candy snack food before bed plus yesterday dream recall so it becomes a graph seed.
        """,
    )
    second = _write_markdown_fixture(
        repo_root / "corpus",
        "SYNTHETIC/GRAPH/target note.md",
        """
        ---
        tags:
          - fixture-tag
        ---

        # Target Note

        ## Fixture Target Section

        This target note exists only to be reached through the wikilink graph expansion.
        """,
    )
    return first, second


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


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(dict(merged[key]), value)
        else:
            merged[key] = value
    return merged


def _write_runtime_config(repo_root: Path, overrides: dict[str, Any] | None = None, *, relative_path: str = "semantic_traversal.runtime.yaml") -> Path:
    base_config = json.loads(json.dumps(load_runtime_config(repo_root=REPO_ROOT, config_path=str(DEFAULT_CONFIG_SOURCE)).raw))
    merged = _deep_merge(base_config, overrides or {})
    destination = repo_root / relative_path
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(yaml.safe_dump(merged, sort_keys=False), encoding="utf-8")
    return destination


def _write_runtime_config_document(repo_root: Path, raw_config: dict[str, Any], *, relative_path: str = "semantic_traversal.runtime.yaml") -> Path:
    destination = repo_root / relative_path
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(yaml.safe_dump(raw_config, sort_keys=False), encoding="utf-8")
    return destination


def _prepared_repo_root(repo_dir: str | Path, overrides: dict[str, Any] | None = None) -> Path:
    repo_root = Path(repo_dir)
    _write_runtime_config(repo_root, overrides)
    return repo_root


class _OllamaFixtureHandler(BaseHTTPRequestHandler):
    response_mode = "valid"

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        raw_body = self.rfile.read(length).decode("utf-8")
        payload = json.loads(raw_body or "{}")
        if self.path == "/api/generate":
            response = self._build_generate_response(payload)
        elif self.path == "/api/embeddings":
            response = self._build_embeddings_response(payload)
        else:
            self.send_response(404)
            self.end_headers()
            return
        body = json.dumps(response).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A003
        return

    def _build_generate_response(self, payload: dict[str, Any]) -> dict[str, Any]:
        prompt = str(payload.get("prompt") or "")
        if self.response_mode == "invalid":
            return {"response": "{\"raw_user_input\":\"broken\"}"}
        packet_json = prompt.split("Packet:\n", 1)[1] if "Packet:\n" in prompt else "{}"
        packet = json.loads(packet_json)
        raw_user_input = str(packet.get("raw_user_input") or "")
        prior_thread_state = packet.get("prior_thread_state") or {}
        recent_messages = list(prior_thread_state.get("recent_messages") or [])
        terms = ["candy", "snack", "food", "bed"]
        response_payload = {
            "raw_user_input": raw_user_input,
            "perturbation_nodes": [{"id": f"term:{term}", "label": term, "kind": "lexical_term"} for term in terms],
            "contextual_salt_nodes": [
                {"id": f"context:{index}", "label": message.get("content", ""), "kind": "recent_message"}
                for index, message in enumerate(recent_messages[-2:], start=1)
            ],
            "perturbation_semantic_graph": {
                "nodes": [{"id": f"term:{term}", "label": term, "kind": "lexical_term"} for term in terms],
                "edges": [{"source": "term:candy", "target": "term:bed", "kind": "association"}],
            },
            "semantic_coverage_target": {
                "must_preserve": ["candy snack food before bed"],
                "should_include": ["yesterday", "dream"],
                "avoid_satisfying_with": [],
                "query_text": raw_user_input,
                "allow_no_retrieval_needed": False,
            },
            "activation_hints": {
                "lexical_terms": terms + ["yesterday", "dream"],
                "phrases": ["candy snack food before bed"],
                "conceptual_neighbors": ["night routine"],
                "relation_hints": ["before bed"],
                "temporal_hints": ["yesterday"],
                "entity_hints": ["dream recall"],
            },
            "limitations": ["model-generated extraction", "additive only", "not authoritative"],
        }
        return {"response": json.dumps(response_payload)}

    def _build_embeddings_response(self, payload: dict[str, Any]) -> dict[str, Any]:
        prompt = str(payload.get("prompt") or "")
        lowered = prompt.lower()
        vector = [
            1.0 if "candy" in lowered else 0.0,
            1.0 if "yesterday" in lowered else 0.0,
            1.0 if "dream" in lowered else 0.0,
            1.0 if "bed" in lowered else 0.0,
        ]
        return {"embedding": vector}


@contextlib.contextmanager
def _ollama_fixture_server(*, response_mode: str = "valid"):
    handler_class = type("TestOllamaFixtureHandler", (_OllamaFixtureHandler,), {"response_mode": response_mode})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_class)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


class FailingLLMBackend:
    def generate(self, synthesis_context_packet: dict[str, object]) -> object:
        raise AssertionError("LLM backend should not be called for blocked runtime execution")


class IngestRuntimeTests(unittest.TestCase):
    def test_runtime_config_loads_default_and_explicit_paths_without_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as repo_dir:
            repo_root = Path(repo_dir)
            default_config_path = _write_runtime_config(repo_root, {"runtime": {"data_root": ".custom-data"}})
            explicit_config_path = _write_runtime_config(
                repo_root,
                {"runtime": {"data_root": ".explicit-data"}},
                relative_path="configs/runtime.yaml",
            )
            default_config = load_runtime_config(repo_root=repo_root)
            explicit_config = load_runtime_config(repo_root=repo_root, config_path="configs/runtime.yaml")

            self.assertEqual(default_config.config_path, default_config_path.resolve())
            self.assertEqual(default_config.data_root, (repo_root / ".custom-data").resolve())
            self.assertEqual(explicit_config.config_path, explicit_config_path.resolve())
            self.assertEqual(explicit_config.data_root, (repo_root / ".explicit-data").resolve())
            self.assertNotIn("OPENAI_API_KEY", explicit_config_path.read_text(encoding="utf-8"))

    def test_missing_runtime_config_field_raises_config_error_without_code_default_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as repo_dir:
            repo_root = Path(repo_dir)
            raw_config = json.loads(json.dumps(load_runtime_config(repo_root=REPO_ROOT, config_path=str(DEFAULT_CONFIG_SOURCE)).raw))
            raw_config["runtime"].pop("max_retrieval_chunks")
            _write_runtime_config_document(repo_root, raw_config)

            with self.assertRaisesRegex(ConfigError, r"Missing required runtime config field: root\.runtime\.max_retrieval_chunks"):
                load_runtime_config(repo_root=repo_root)

    def test_wrong_type_runtime_config_field_raises_config_error(self) -> None:
        with tempfile.TemporaryDirectory() as repo_dir:
            repo_root = Path(repo_dir)
            raw_config = json.loads(json.dumps(load_runtime_config(repo_root=REPO_ROOT, config_path=str(DEFAULT_CONFIG_SOURCE)).raw))
            raw_config["runtime"]["max_retrieval_chunks"] = "two"
            _write_runtime_config_document(repo_root, raw_config)

            with self.assertRaisesRegex(ConfigError, r"Invalid runtime config field type: root\.runtime\.max_retrieval_chunks expected int"):
                load_runtime_config(repo_root=repo_root)

    def test_unknown_runtime_config_field_raises_config_error(self) -> None:
        with tempfile.TemporaryDirectory() as repo_dir:
            repo_root = Path(repo_dir)
            raw_config = json.loads(json.dumps(load_runtime_config(repo_root=REPO_ROOT, config_path=str(DEFAULT_CONFIG_SOURCE)).raw))
            raw_config["runtime"]["unexpected_field"] = True
            _write_runtime_config_document(repo_root, raw_config)

            with self.assertRaisesRegex(ConfigError, r"Unknown runtime config field: root\.runtime\.unexpected_field"):
                load_runtime_config(repo_root=repo_root)

    def test_env_local_api_key_is_separate_from_yaml_config(self) -> None:
        with tempfile.TemporaryDirectory() as repo_dir:
            repo_root = Path(repo_dir)
            _write_runtime_config(repo_root, {"llm": {"model": "fixture-llm-model"}})
            (repo_root / ".env.local").write_text(
                "OPENAI_API_KEY=test-secret-key\nMAX_RETRIEVAL_CHUNKS=999\n",
                encoding="utf-8",
            )
            config = load_runtime_config(repo_root=repo_root)
            api_key, model, _ = resolve_openai_settings(repo_root=repo_root, config=config)

            self.assertEqual(api_key, "test-secret-key")
            self.assertEqual(model, "fixture-llm-model")
            self.assertEqual(config.max_retrieval_chunks, 6)

    def test_yaml_max_retrieval_chunks_controls_manifest_limit(self) -> None:
        with tempfile.TemporaryDirectory() as repo_dir, tempfile.TemporaryDirectory() as data_dir:
            repo_root = _prepared_repo_root(repo_dir, {"runtime": {"max_retrieval_chunks": 2}})
            (repo_root / "corpus").mkdir(parents=True, exist_ok=True)
            _copy_note(FIXTURE_NOTE, repo_root / "tests" / "fixtures", "JOURNAL/2025-09/01_Monday.md")
            _write_synthetic_corpus_journal_note(repo_root)
            run_ingest(repo_root=repo_root, data_root=Path(data_dir), source_roots=build_default_source_roots(repo_root))

            result = run_thread_turn(
                repo_root=repo_root,
                data_root=Path(data_dir),
                user_input="Please retrieve the candy snack food before bed note.",
                llm_backend=StubLLMBackend(prefix="Probe stub response"),
                semantic_extractor_backend=DisabledSemanticExtractorBackend(),
            )

            manifest = _load_turn_artifact(result.semantic_traversal_manifest_path)
            self.assertEqual(manifest["limits"]["max_selected_chunks"], 2)
            self.assertLessEqual(len(manifest["selected_chunk_ids"]), 2)

    def test_cli_ingest_uses_authorized_default_roots(self) -> None:
        with tempfile.TemporaryDirectory() as repo_dir, tempfile.TemporaryDirectory() as temp_dir:
            repo_root = _prepared_repo_root(repo_dir)
            (repo_root / "tests" / "fixtures").mkdir(parents=True, exist_ok=True)
            _copy_note(FIXTURE_NOTE, repo_root / "tests" / "fixtures", "JOURNAL/2025-09/01_Monday.md")
            _write_synthetic_corpus_journal_note(repo_root)

            process = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "semantic_traversal",
                    "ingest",
                    "--repo-root",
                    str(repo_root),
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
            repo_root = _prepared_repo_root(repo_dir)
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
            repo_root = _prepared_repo_root(repo_dir)
            (repo_root / "tests" / "fixtures").mkdir(parents=True, exist_ok=True)
            _write_synthetic_corpus_journal_note(repo_root)
            _write_synthetic_longform_note(repo_root)

            result = run_ingest(
                repo_root=repo_root,
                data_root=Path(data_dir),
                source_roots=build_default_source_roots(repo_root),
            )
            manifest = _load_manifest(result.manifest_path)

            journal_chunks = _chunks_for_note(
                manifest,
                "corpus",
                "SYNTHETIC/JOURNAL/fixture_corpus_journal.md",
            )
            self.assertIn("Fixture Corpus Alpha", {chunk["section_label"] for chunk in journal_chunks})
            self.assertIn("Fixture Corpus Beta", {chunk["section_label"] for chunk in journal_chunks})
            self.assertIn("Fixture Corpus Gamma", {chunk["section_label"] for chunk in journal_chunks})

            longform_chunks = _chunks_for_note(
                manifest,
                "corpus",
                "SYNTHETIC/LONGFORM/fixture_longform.md",
            )
            self.assertGreater(len(longform_chunks), 8)
            premise_chunks = [
                chunk["paragraph_text"]
                for chunk in longform_chunks
                if chunk["section_label"] == "Fixture Proposition Section"
            ]
            self.assertTrue(any("Premise 1" in text for text in premise_chunks))
            self.assertTrue(any("Premise 2" in text for text in premise_chunks))

    def test_reingest_unchanged_preserves_chunk_ids_and_hashes(self) -> None:
        with tempfile.TemporaryDirectory() as repo_dir, tempfile.TemporaryDirectory() as data_dir:
            repo_root = _prepared_repo_root(repo_dir)
            (repo_root / "tests" / "fixtures").mkdir(parents=True, exist_ok=True)
            _copy_note(FIXTURE_NOTE, repo_root / "tests" / "fixtures", "JOURNAL/2025-09/01_Monday.md")
            _write_synthetic_corpus_journal_note(repo_root)
            _write_synthetic_longform_note(repo_root)

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

    def test_ingest_creates_graph_tables_and_vector_rows_when_embeddings_are_configured(self) -> None:
        with tempfile.TemporaryDirectory() as repo_dir, tempfile.TemporaryDirectory() as data_dir:
            repo_root = Path(repo_dir)
            with _ollama_fixture_server() as base_url:
                _write_runtime_config(
                    repo_root,
                    {
                        "semantic_extraction": {"model": "fixture-semantic", "base_url": base_url},
                        "embeddings": {"model": "fixture-embed", "base_url": base_url},
                    },
                )
                (repo_root / "tests" / "fixtures").mkdir(parents=True, exist_ok=True)
                _write_graph_fixture_notes(repo_root)
                config = load_runtime_config(repo_root=repo_root)

                result = run_ingest(
                    repo_root=repo_root,
                    data_root=Path(data_dir),
                    source_roots=build_default_source_roots(repo_root, config=config),
                    config=config,
                )

            connection = sqlite3.connect(result.database_path)
            try:
                edge_types = {
                    row[0]
                    for row in connection.execute("SELECT DISTINCT edge_type FROM graph_edges").fetchall()
                }
                vector_count = connection.execute("SELECT COUNT(*) FROM chunk_vectors").fetchone()[0]
            finally:
                connection.close()

            self.assertIn("note_contains_chunk", edge_types)
            self.assertIn("chunk_derived_from_note", edge_types)
            self.assertIn("wikilink", edge_types)
            self.assertIn("note_has_tag", edge_types)
            self.assertGreater(vector_count, 0)

    def test_localized_paragraph_edit_changes_only_the_edited_chunk(self) -> None:
        with tempfile.TemporaryDirectory() as repo_dir, tempfile.TemporaryDirectory() as data_dir:
            repo_root = _prepared_repo_root(repo_dir)
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
            repo_root = _prepared_repo_root(repo_dir)
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
            repo_root = _prepared_repo_root(repo_dir)
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
            repo_root = _prepared_repo_root(repo_dir)
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
            repo_root = _prepared_repo_root(repo_dir)
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
            repo_root = _prepared_repo_root(repo_dir)
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
            repo_root = _prepared_repo_root(repo_dir)
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
            self.assertEqual(result.runtime_outcome, "blocked")
            self.assertEqual(ledger[-1]["isolated_semantic_extraction_packet_hash"], sha256_json(isolated_packet))
            self.assertEqual(ledger[-1]["isolated_semantic_extraction_raw_hash"], sha256_json(isolated_raw))
            self.assertEqual(ledger[-1]["contextual_semantic_extraction_packet_hash"], sha256_json(contextual_packet))
            self.assertEqual(ledger[-1]["contextual_semantic_extraction_raw_hash"], sha256_json(contextual_raw))

    def test_disabled_semantic_extraction_blocks_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as repo_dir, tempfile.TemporaryDirectory() as data_dir:
            repo_root = _prepared_repo_root(repo_dir)
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
            self.assertEqual(result.runtime_outcome, "blocked")
            self.assertEqual(result.coverage_report["decision"], "blocked")
            self.assertIsNone(result.synthesis_context_packet["approved_retrieval_packet"])
            self.assertIsNone(result.assistant_response)
            self.assertEqual(result.llm_metadata["mode"], "not_called")
            self.assertTrue(result.retrieval_packet["selected_chunks"])
            self.assertTrue(any("disabled" in reason for reason in result.blocking_reasons))

    def test_contextual_extraction_receives_prior_thread_state(self) -> None:
        with tempfile.TemporaryDirectory() as repo_dir, tempfile.TemporaryDirectory() as data_dir:
            repo_root = _prepared_repo_root(repo_dir)
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

    def test_stub_semantic_extraction_blocks_normal_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as repo_dir, tempfile.TemporaryDirectory() as data_dir:
            repo_root = _prepared_repo_root(repo_dir)
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
            self.assertEqual(result.runtime_outcome, "blocked")
            self.assertEqual(result.llm_metadata["mode"], "not_called")
            self.assertTrue(result.isolated_semantic_extraction_packet_path.exists())
            self.assertTrue(result.contextual_semantic_extraction_packet_path.exists())
            self.assertTrue(result.synthesis_context_packet_path.exists())
            self.assertEqual(result.coverage_report["decision"], "blocked")
            self.assertIsNone(result.synthesis_context_packet["approved_retrieval_packet"])
            self.assertTrue(any("stub" in reason for reason in result.blocking_reasons))

    def test_lexical_retrieval_fixture_hit_persists_artifacts_and_hashes(self) -> None:
        with tempfile.TemporaryDirectory() as repo_dir, tempfile.TemporaryDirectory() as data_dir:
            repo_root = _prepared_repo_root(repo_dir)
            (repo_root / "corpus").mkdir(parents=True, exist_ok=True)
            _copy_note(FIXTURE_NOTE, repo_root / "tests" / "fixtures", "JOURNAL/2025-09/01_Monday.md")
            _write_synthetic_corpus_journal_note(repo_root)
            _write_synthetic_longform_note(repo_root)

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

            self.assertEqual(result.runtime_outcome, "blocked")
            self.assertEqual(coverage_report["decision"], "blocked")
            self.assertGreater(len(retrieval_packet["selected_chunks"]), 0)
            self.assertIsNone(synthesis_context_packet["approved_retrieval_packet"])
            self.assertTrue(any(chunk["source_root_label"] == "tests-fixtures" for chunk in retrieval_packet["selected_chunks"]))
            self.assertEqual(
                synthesis_context_packet["semantic_context_packet"]["retrieval_preparation"]["raw_lexical_terms"],
                semantic_context_packet["retrieval_preparation"]["raw_lexical_terms"],
            )
            self.assertGreater(len(semantic_traversal_manifest["selected_chunk_ids"]), 0)
            self.assertIn("matched_chunks", coverage_report["limits"]["diagnostic_retrieval_observation"])
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
            repo_root = _prepared_repo_root(repo_dir)
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

            self.assertEqual(result.runtime_outcome, "blocked")
            self.assertEqual(coverage_report["decision"], "blocked")
            self.assertEqual(retrieval_packet["selected_chunks"], [])
            self.assertEqual(retrieval_packet["retrieval_observation"], "index_missing")
            self.assertEqual(result.semantic_traversal_manifest["selection_reasons"], ["ingestion SQLite database not found"])
            self.assertEqual(ledger[-1]["semantic_context_packet_hash"], sha256_json(_load_turn_artifact(result.semantic_context_packet_path)))
            self.assertEqual(ledger[-1]["semantic_traversal_manifest_hash"], sha256_json(_load_turn_artifact(result.semantic_traversal_manifest_path)))
            self.assertEqual(ledger[-1]["retrieval_packet_hash"], sha256_json(retrieval_packet))
            self.assertEqual(ledger[-1]["coverage_report_hash"], sha256_json(coverage_report))

    def test_lexical_retrieval_no_match_is_explicit_and_non_crashing(self) -> None:
        with tempfile.TemporaryDirectory() as repo_dir, tempfile.TemporaryDirectory() as data_dir:
            repo_root = _prepared_repo_root(repo_dir)
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

            self.assertEqual(result.runtime_outcome, "blocked")
            self.assertEqual(coverage_report["decision"], "blocked")
            self.assertEqual(retrieval_packet["selected_chunks"], [])
            self.assertEqual(retrieval_packet["retrieval_observation"], "no_matches")
            self.assertEqual(ledger[-1]["semantic_context_packet_hash"], sha256_json(_load_turn_artifact(result.semantic_context_packet_path)))
            self.assertEqual(ledger[-1]["semantic_traversal_manifest_hash"], sha256_json(_load_turn_artifact(result.semantic_traversal_manifest_path)))
            self.assertEqual(ledger[-1]["retrieval_packet_hash"], sha256_json(retrieval_packet))
            self.assertEqual(ledger[-1]["coverage_report_hash"], sha256_json(coverage_report))

    def test_lexical_retrieval_no_query_terms_is_explicit_and_non_crashing(self) -> None:
        with tempfile.TemporaryDirectory() as repo_dir, tempfile.TemporaryDirectory() as data_dir:
            repo_root = _prepared_repo_root(repo_dir)
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
            self.assertEqual(retrieval_packet["retrieval_observation"], "no_query_terms")
            self.assertEqual(retrieval_packet["selected_chunks"], [])
            self.assertEqual(coverage_report["decision"], "blocked")
            self.assertEqual(ledger[-1]["semantic_context_packet_hash"], sha256_json(semantic_context_packet))
            self.assertEqual(ledger[-1]["semantic_traversal_manifest_hash"], sha256_json(semantic_traversal_manifest))
            self.assertEqual(ledger[-1]["retrieval_packet_hash"], sha256_json(retrieval_packet))
            self.assertEqual(ledger[-1]["coverage_report_hash"], sha256_json(coverage_report))

    def test_same_thread_continuation_preserves_parent_hash_with_retrieval(self) -> None:
        with tempfile.TemporaryDirectory() as repo_dir, tempfile.TemporaryDirectory() as data_dir:
            repo_root = _prepared_repo_root(repo_dir)
            (repo_root / "corpus").mkdir(parents=True, exist_ok=True)
            _copy_note(FIXTURE_NOTE, repo_root / "tests" / "fixtures", "JOURNAL/2025-09/01_Monday.md")
            _write_synthetic_corpus_journal_note(repo_root)
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
            self.assertEqual(second_turn.coverage_report["decision"], "blocked")

    def test_completed_runtime_uses_real_activation_traversal_and_coverage_chain(self) -> None:
        with tempfile.TemporaryDirectory() as repo_dir, tempfile.TemporaryDirectory() as data_dir:
            repo_root = Path(repo_dir)
            with _ollama_fixture_server() as base_url:
                _write_runtime_config(
                    repo_root,
                    {
                        "semantic_extraction": {"model": "fixture-semantic", "base_url": base_url},
                        "embeddings": {"model": "fixture-embed", "base_url": base_url},
                        "coverage": {
                            "graph_expansion_hop_limit": 2,
                            "require_surface_contributions": {
                                "lexical_index_surface": True,
                                "vector_index_surface": True,
                                "graph_layer": True,
                                "primary_corpus": True,
                                "synthetic_nodes": False,
                            }
                        },
                    },
                )
                (repo_root / "corpus").mkdir(parents=True, exist_ok=True)
                _copy_note(FIXTURE_NOTE, repo_root / "tests" / "fixtures", "JOURNAL/2025-09/01_Monday.md")
                _write_synthetic_corpus_journal_note(repo_root)
                _write_graph_fixture_notes(repo_root)
                config = load_runtime_config(repo_root=repo_root)
                run_ingest(
                    repo_root=repo_root,
                    data_root=Path(data_dir),
                    source_roots=build_default_source_roots(repo_root, config=config),
                    config=config,
                )

                result = run_thread_turn(
                    repo_root=repo_root,
                    data_root=Path(data_dir),
                    user_input="Please retrieve the candy snack food before bed note and relate it to yesterday dream recall.",
                    llm_backend=StubLLMBackend(prefix="Approved stub response"),
                    config=config,
                )

            self.assertEqual(result.runtime_outcome, "completed")
            self.assertEqual(result.coverage_report["decision"], "approved")
            self.assertEqual(result.llm_metadata["mode"], "stub")
            self.assertIsNotNone(result.assistant_response)
            self.assertTrue(result.semantic_traversal_manifest["selected_chunk_ids"])
            self.assertEqual(
                [chunk["chunk_id"] for chunk in result.retrieval_packet["selected_chunks"]],
                result.semantic_traversal_manifest["selected_chunk_ids"],
            )
            self.assertTrue(result.semantic_traversal_manifest["surface_contributions"]["lexical_index_surface"])
            self.assertTrue(result.semantic_traversal_manifest["surface_contributions"]["primary_corpus"])
            self.assertTrue(result.semantic_traversal_manifest["surface_contributions"]["vector_index_surface"])
            self.assertTrue(result.semantic_traversal_manifest["surface_contributions"]["graph_layer"])
            self.assertIsNotNone(result.synthesis_context_packet["approved_retrieval_packet"])

    def test_turn_cli_reports_artifact_paths_and_hashes(self) -> None:
        with tempfile.TemporaryDirectory() as repo_dir, tempfile.TemporaryDirectory() as data_dir:
            repo_root = _prepared_repo_root(repo_dir)
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
                    "--repo-root",
                    str(repo_root),
                    "--data-root",
                    data_dir,
                ],
                cwd=REPO_ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(process.returncode, 1)
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
            self.assertIn(payload["isolated_extraction_status"], {"parsed", "stub", "disabled", "unavailable", "invalid_json"})
            self.assertIn(payload["contextual_extraction_status"], {"parsed", "stub", "disabled", "unavailable", "invalid_json"})
            self.assertEqual(payload["runtime_outcome"], "blocked")
            self.assertEqual(payload["coverage_decision"], "blocked")
            self.assertIsNone(payload["assistant_response"])
            self.assertTrue(payload["latest_perturbation_hash"])
            self.assertTrue(payload["latest_thread_state_hash"])

    def test_turn_cli_parser_rejects_semantic_extractor_mode_flag(self) -> None:
        parser = build_turn_parser()
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                parser.parse_args(["--message", "hello", "--semantic-extractor-mode", "stub"])

    def test_runtime_semantic_extractor_resolver_fails_closed_without_configured_backend(self) -> None:
        with tempfile.TemporaryDirectory() as repo_dir:
            repo_root = _prepared_repo_root(repo_dir)
            backend = resolve_semantic_extractor_backend(repo_root=repo_root, config=load_runtime_config(repo_root=repo_root))
            self.assertEqual(backend.mode_name, "unavailable")

    def test_blocked_runtime_does_not_call_llm_backend(self) -> None:
        with tempfile.TemporaryDirectory() as repo_dir, tempfile.TemporaryDirectory() as data_dir:
            repo_root = _prepared_repo_root(repo_dir)
            result = run_thread_turn(
                repo_root=repo_root,
                data_root=Path(data_dir),
                user_input="This should block before the llm call boundary.",
                llm_backend=FailingLLMBackend(),
                semantic_extractor_backend=DisabledSemanticExtractorBackend(),
            )
            self.assertEqual(result.runtime_outcome, "blocked")
            self.assertEqual(result.llm_metadata["mode"], "not_called")
            self.assertIsNone(result.assistant_response)

    def test_stub_semantic_extraction_cannot_produce_thesis_valid_state_perturbation(self) -> None:
        with tempfile.TemporaryDirectory() as repo_dir, tempfile.TemporaryDirectory() as data_dir:
            repo_root = _prepared_repo_root(repo_dir)
            (repo_root / "corpus").mkdir(parents=True, exist_ok=True)
            _copy_note(FIXTURE_NOTE, repo_root / "tests" / "fixtures", "JOURNAL/2025-09/01_Monday.md")
            run_ingest(repo_root=repo_root, data_root=Path(data_dir), source_roots=build_default_source_roots(repo_root))

            result = run_thread_turn(
                repo_root=repo_root,
                data_root=Path(data_dir),
                user_input="Please retrieve the candy snack food before bed note.",
                llm_backend=FailingLLMBackend(),
                semantic_extractor_backend=StubSemanticExtractorBackend(),
            )

            self.assertEqual(result.coverage_report["decision"], "blocked")
            self.assertTrue(any("stub" in reason for reason in result.blocking_reasons))
            self.assertIsNone(result.synthesis_context_packet["approved_retrieval_packet"])

    def test_forbidden_runtime_vocabulary_is_absent_from_runtime_outputs(self) -> None:
        forbidden_terms = {
            "minimal_pass",
            "partial_pass",
            "partial_implementation",
            "degraded_success",
            "fallback_success",
            "best_effort_success",
            "lexical_success",
            "stub_success",
        }
        with tempfile.TemporaryDirectory() as repo_dir, tempfile.TemporaryDirectory() as data_dir:
            repo_root = _prepared_repo_root(repo_dir)
            (repo_root / "corpus").mkdir(parents=True, exist_ok=True)
            _copy_note(FIXTURE_NOTE, repo_root / "tests" / "fixtures", "JOURNAL/2025-09/01_Monday.md")
            run_ingest(repo_root=repo_root, data_root=Path(data_dir), source_roots=build_default_source_roots(repo_root))

            result = run_thread_turn(
                repo_root=repo_root,
                data_root=Path(data_dir),
                user_input="Please retrieve the candy snack food before bed note.",
                llm_backend=FailingLLMBackend(),
                semantic_extractor_backend=DisabledSemanticExtractorBackend(),
            )

            serialized = json.dumps(
                {
                    "coverage_report": result.coverage_report,
                    "synthesis_context_packet": result.synthesis_context_packet,
                    "runtime_outcome": result.runtime_outcome,
                    "blocking_reasons": result.blocking_reasons,
                },
                ensure_ascii=True,
            )
            for forbidden_term in forbidden_terms:
                self.assertNotIn(forbidden_term, serialized)


if __name__ == "__main__":
    unittest.main()

