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
import types
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from unittest.mock import patch

import yaml

from semantic_traversal.config import ConfigError, load_runtime_config
from semantic_traversal.embeddings import resolve_embedding_backend
from semantic_traversal.hashing import sha256_json
from semantic_traversal.ingest import IngestSourceRoot, build_default_source_roots, run_ingest
from semantic_traversal.cli import build_turn_parser
from semantic_traversal.llm import StubLLMBackend, resolve_openai_settings
from semantic_traversal.runtime import (
    _build_contextual_extraction_request,
    _evaluate_retrieval_coverage,
    _evaluate_semantic_target_coverage,
    _query_vector_candidates,
    run_thread_turn,
)
from semantic_traversal.semantic_extraction import (
    _build_ollama_prompt,
    _detect_followup_signals,
    DisabledSemanticExtractorBackend,
    SemanticExtractionResponse,
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


def _minimal_retrieval_coverage_inputs(selected_chunk_ids: list[str], retrieved_chunk_ids: list[str]) -> dict[str, Any]:
    return {
        "semantic_context_packet": {
            "semantic_extraction": {
                "statuses": {
                    "backend_mode": "parsed",
                    "isolated_status": "parsed",
                    "contextual_status": "parsed",
                }
            },
            "semantic_contract_validation": {"valid": True, "reasons": []},
            "semantic_coverage_target": {
                "must_preserve": [],
                "should_include": [],
                "avoid_satisfying_with": [],
                "query_text": "retrieval equality check",
                "allow_no_retrieval_needed": False,
            },
        },
        "activated_semantic_regions": {
            "activation_surfaces": [
                {"surface": "lexical_index_surface", "status": "activated"},
                {"surface": "primary_corpus", "status": "activated"},
                {"surface": "vector_index_surface", "status": "activated"},
                {"surface": "graph_layer", "status": "activated"},
            ]
        },
        "semantic_traversal_manifest": {
            "selected_chunk_ids": selected_chunk_ids,
            "manifest_validity": {"valid": True, "reasons": []},
            "surface_contributions": {
                "lexical_index_surface": True,
                "primary_corpus": True,
                "vector_index_surface": True,
                "graph_layer": True,
            },
        },
        "retrieval_packet": {
            "assembled_from_traversal_manifest": True,
            "retrieval_observation": "matched_chunks",
            "selected_chunks": [{"chunk_id": chunk_id} for chunk_id in retrieved_chunk_ids],
        },
        "config": load_runtime_config(repo_root=REPO_ROOT, config_path=str(DEFAULT_CONFIG_SOURCE)),
        "semantic_traversal_manifest_hash": "manifest-hash",
        "retrieval_packet_hash": "retrieval-hash",
    }


class _FakeSentenceTransformer:
    def __init__(self, model_name: str, device: str | None = None, **kwargs: Any) -> None:
        self.model_name = model_name
        self.device = device

    def encode(self, texts: list[str] | str, **kwargs: Any) -> list[list[float]]:
        if isinstance(texts, str):
            texts = [texts]
        keywords = ("candy", "yesterday", "dream", "bed", "fixture", "beta")
        return [[1.0 if keyword in text.lower() else 0.0 for keyword in keywords] for text in texts]

    def encode_document(self, texts: list[str] | str, **kwargs: Any) -> list[list[float]]:
        return self.encode(texts, **kwargs)

    def encode_query(self, texts: list[str] | str, **kwargs: Any) -> list[list[float]]:
        return self.encode(texts, **kwargs)


class _InvalidSentenceTransformer:
    def __init__(self, model_name: str, device: str | None = None, **kwargs: Any) -> None:
        self.model_name = model_name
        self.device = device

    def encode(self, texts: list[str] | str, **kwargs: Any) -> list[str]:
        if isinstance(texts, str):
            texts = [texts]
        return ["not-a-vector" for _ in texts]


class _TestEmbeddingBackend:
    mode_name = "sentence_transformers"

    def embed_texts(self, texts: list[str]) -> Any:
        return types.SimpleNamespace(
            status="embedded",
            vectors=[[1.0, 0.0] for _ in texts],
            metadata={"backend_mode": self.mode_name, "model": "test-fake-sentence-transformer", "vector_count": len(texts)},
        )

    def embed_query_text(self, text: str) -> Any:
        return types.SimpleNamespace(
            status="embedded",
            vectors=[[1.0, 0.0]],
            metadata={"backend_mode": self.mode_name, "model": "test-fake-sentence-transformer", "vector_count": 1},
        )


class _ParsedSemanticExtractorBackend:
    mode_name = "parsed"

    def __init__(
        self,
        *,
        isolated_payload: dict[str, Any] | None = None,
        contextual_payload: dict[str, Any] | None = None,
    ) -> None:
        self._delegate = StubSemanticExtractorBackend(
            isolated_payload=isolated_payload,
            contextual_payload=contextual_payload,
        )

    def _parsed_response(self, response: SemanticExtractionResponse) -> SemanticExtractionResponse:
        metadata = dict(response.metadata)
        metadata["backend_mode"] = self.mode_name
        return SemanticExtractionResponse(
            parsed_payload=response.parsed_payload,
            raw_response=response.raw_response,
            metadata=metadata,
            diagnostics=response.diagnostics,
            status="parsed",
        )

    def extract_isolated(self, packet: dict[str, Any]) -> SemanticExtractionResponse:
        return self._parsed_response(self._delegate.extract_isolated(packet))

    def extract_contextual(self, packet: dict[str, Any]) -> SemanticExtractionResponse:
        return self._parsed_response(self._delegate.extract_contextual(packet))


def _fake_sentence_transformers_import_module(name: str):
    if name == "sentence_transformers":
        return types.SimpleNamespace(SentenceTransformer=_FakeSentenceTransformer)
    raise ModuleNotFoundError(name)


def _invalid_sentence_transformers_import_module(name: str):
    if name == "sentence_transformers":
        return types.SimpleNamespace(SentenceTransformer=_InvalidSentenceTransformer)
    raise ModuleNotFoundError(name)


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
    last_generate_payload: dict[str, Any] | None = None

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
        type(self).last_generate_payload = payload
        _OllamaFixtureHandler.last_generate_payload = payload
        prompt = str(payload.get("prompt") or "")
        if self.response_mode == "invalid":
            return {"response": "{\"raw_user_input\":\"broken\"}"}
        packet_json = prompt.split("Packet:\n", 1)[1] if "Packet:\n" in prompt else "{}"
        packet = json.loads(packet_json)
        raw_user_input = str(packet.get("raw_user_input") or "")
        extractor_thread_context = packet.get("extractor_thread_context") or packet.get("prior_thread_state") or {}
        recent_messages = list(extractor_thread_context.get("recent_user_messages") or [])
        deterministic_referent_candidates = list(packet.get("deterministic_resolved_referent_candidates") or [])
        terms = ["candy", "snack", "food", "bed"]
        followup_detection = {
            "is_referential_followup": "it" in raw_user_input.lower() or "that" in raw_user_input.lower(),
            "requires_referent_resolution": bool(recent_messages) and ("it" in raw_user_input.lower() or "that" in raw_user_input.lower()),
            "signals": ["deictic:it"] if "it" in raw_user_input.lower() else [],
            "surface_forms": ["it"] if "it" in raw_user_input.lower() else [],
        }
        resolved_referents = []
        if deterministic_referent_candidates:
            resolved_referents = deterministic_referent_candidates
        elif followup_detection["requires_referent_resolution"]:
            resolved_referents = [
                {
                    "surface_form": "it",
                    "resolved_to": "candy snack food before bed",
                    "source": "prior_thread_state.recent_messages",
                    "confidence": "high",
                    "required_for_target": True,
                }
            ]
        if self.response_mode == "malformed" and packet.get("mode") == "contextual":
            response_payload = {
                "raw_user_input": raw_user_input,
                "perturbation_nodes": [{"id": f"term:{term}", "label": term, "kind": "lexical_term"} for term in terms],
                "contextual_salt_nodes": [],
                "perturbation_semantic_graph": {
                    "nodes": [{"id": f"term:{term}", "label": term, "kind": "lexical_term"} for term in terms],
                    "edges": [{"source": "term:candy", "target": "term:bed", "kind": "association"}],
                },
                "semantic_coverage_target": "candy snack food before bed",
                "activation_hints": ["candy", "snack", "food", "bed"],
                "limitations": ["model-generated extraction", "additive only", "not authoritative"],
            }
            return {"response": json.dumps(response_payload)}
        response_payload = {
            "raw_user_input": raw_user_input,
            "followup_detection": followup_detection,
            "resolved_referents": resolved_referents,
            "perturbation_nodes": [{"id": f"term:{term}", "label": term, "kind": "lexical_term"} for term in terms],
            "contextual_salt_nodes": [
                {"id": f"context:{index}", "label": message, "kind": "recent_message"}
                for index, message in enumerate(recent_messages[-2:], start=1)
            ],
            "perturbation_semantic_graph": {
                "nodes": [{"id": f"term:{term}", "label": term, "kind": "lexical_term"} for term in terms],
                "edges": [{"source": "term:candy", "target": "term:bed", "kind": "association"}],
            },
            "semantic_coverage_target": {
                "must_preserve": [referent["resolved_to"] for referent in resolved_referents] or ["candy snack food before bed"],
                "should_include": ["yesterday", "dream"],
                "avoid_satisfying_with": [],
                "query_text": raw_user_input,
                "allow_no_retrieval_needed": False,
            },
            "activation_hints": {
                "lexical_terms": terms + ["yesterday", "dream"],
                "phrases": [referent["resolved_to"] for referent in resolved_referents] or ["candy snack food before bed"],
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
    @classmethod
    def setUpClass(cls) -> None:
        cls._sentence_transformers_patcher = patch(
            "semantic_traversal.embeddings.import_module",
            side_effect=_fake_sentence_transformers_import_module,
        )
        cls._sentence_transformers_patcher.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls._sentence_transformers_patcher.stop()

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
            self.assertEqual(default_config.embedding_provider, "sentence_transformers")
            self.assertEqual(default_config.embedding_model, "sentence-transformers/all-MiniLM-L6-v2")
            self.assertEqual(default_config.embedding_batch_size, 32)
            self.assertTrue(default_config.embedding_normalize_embeddings)
            self.assertIsNone(default_config.embedding_device)
            self.assertTrue(default_config.coverage_require_surface_contributions["vector_index_surface"])
            self.assertNotIn("OPENAI_API_KEY", explicit_config_path.read_text(encoding="utf-8"))

    def test_readme_and_dependency_manifest_document_runtime_authority_dependencies(self) -> None:
        readme_text = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        requirements_text = (REPO_ROOT / "requirements.txt").read_text(encoding="utf-8")

        self.assertIn("[`semantic_traversal.runtime.yaml`](semantic_traversal.runtime.yaml)", readme_text)
        self.assertNotIn("/F:/PROJECT-REPOS", readme_text)
        self.assertIn("PyYAML", requirements_text)
        self.assertIn("sentence-transformers", requirements_text)

    def test_default_config_has_valid_sql_identifiers(self) -> None:
        config = load_runtime_config(repo_root=REPO_ROOT, config_path=str(DEFAULT_CONFIG_SOURCE))
        self.assertEqual(config.vector_table, "chunk_vectors")
        self.assertEqual(config.graph_nodes_table, "graph_nodes")
        self.assertEqual(config.graph_edges_table, "graph_edges")

    def test_custom_sql_identifiers_load_when_valid(self) -> None:
        with tempfile.TemporaryDirectory() as repo_dir:
            repo_root = Path(repo_dir)
            _write_runtime_config(
                repo_root,
                {
                    "indexes": {
                        "vector_table": "_chunk_vectors",
                        "graph_nodes_table": "graph_nodes_v2",
                        "graph_edges_table": "graph_edges_2026",
                    }
                },
            )
            config = load_runtime_config(repo_root=repo_root)
            self.assertEqual(config.vector_table, "_chunk_vectors")
            self.assertEqual(config.graph_nodes_table, "graph_nodes_v2")
            self.assertEqual(config.graph_edges_table, "graph_edges_2026")

    def test_invalid_vector_table_sql_identifier_blocks_at_config_load(self) -> None:
        for invalid_value in ("chunk-vectors", "chunk_vectors; DROP TABLE chunks", ""):
            with self.subTest(invalid_value=invalid_value), tempfile.TemporaryDirectory() as repo_dir:
                repo_root = Path(repo_dir)
                _write_runtime_config(repo_root, {"indexes": {"vector_table": invalid_value}})
                with self.assertRaisesRegex(ConfigError, r"Invalid SQL identifier for indexes\.vector_table"):
                    load_runtime_config(repo_root=repo_root)

    def test_invalid_graph_nodes_table_sql_identifier_blocks_at_config_load(self) -> None:
        for invalid_value in ("graph nodes", "graph_nodes)", ""):
            with self.subTest(invalid_value=invalid_value), tempfile.TemporaryDirectory() as repo_dir:
                repo_root = Path(repo_dir)
                _write_runtime_config(repo_root, {"indexes": {"graph_nodes_table": invalid_value}})
                with self.assertRaisesRegex(ConfigError, r"Invalid SQL identifier for indexes\.graph_nodes_table"):
                    load_runtime_config(repo_root=repo_root)

    def test_invalid_graph_edges_table_sql_identifier_blocks_at_config_load(self) -> None:
        for invalid_value in ("1_graph_edges", "graph-edges", ""):
            with self.subTest(invalid_value=invalid_value), tempfile.TemporaryDirectory() as repo_dir:
                repo_root = Path(repo_dir)
                _write_runtime_config(repo_root, {"indexes": {"graph_edges_table": invalid_value}})
                with self.assertRaisesRegex(ConfigError, r"Invalid SQL identifier for indexes\.graph_edges_table"):
                    load_runtime_config(repo_root=repo_root)

    def test_wrong_type_sql_identifier_field_still_uses_type_validation(self) -> None:
        with tempfile.TemporaryDirectory() as repo_dir:
            repo_root = Path(repo_dir)
            _write_runtime_config(repo_root, {"indexes": {"vector_table": 123}})
            with self.assertRaisesRegex(ConfigError, r"Invalid runtime config field type: root\.indexes\.vector_table expected str"):
                load_runtime_config(repo_root=repo_root)

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
            self.assertEqual(config.max_retrieval_chunks, 18)

    def test_env_local_does_not_redefine_provider_identity(self) -> None:
        with tempfile.TemporaryDirectory() as repo_dir:
            repo_root = Path(repo_dir)
            _write_runtime_config(
                repo_root,
                {
                    "semantic_extraction": {"model": "fixture-semantic"},
                },
            )
            (repo_root / ".env.local").write_text(
                "OPENAI_API_KEY=test-secret-key\nSEMANTIC_EXTRACTION_PROVIDER=disabled\nEMBEDDING_PROVIDER=disabled\n",
                encoding="utf-8",
            )
            config = load_runtime_config(repo_root=repo_root)
            semantic_backend = resolve_semantic_extractor_backend(repo_root=repo_root, config=config)

            self.assertEqual(config.semantic_extraction_provider, "ollama")
            self.assertEqual(config.embedding_provider, "sentence_transformers")
            self.assertEqual(config.embedding_model, "sentence-transformers/all-MiniLM-L6-v2")
            self.assertEqual(semantic_backend.mode_name, "ollama")

    def test_sentence_transformers_backend_returns_numeric_vectors(self) -> None:
        with tempfile.TemporaryDirectory() as repo_dir:
            repo_root = Path(repo_dir)
            config = load_runtime_config(repo_root=REPO_ROOT, config_path=str(DEFAULT_CONFIG_SOURCE))
            with patch("semantic_traversal.embeddings.import_module", side_effect=_fake_sentence_transformers_import_module):
                backend = resolve_embedding_backend(config)

            response = backend.embed_texts(["alpha", "beta"])
            query_response = backend.embed_query_text("gamma")

            self.assertEqual(response.status, "embedded")
            self.assertEqual(query_response.status, "embedded")
            self.assertEqual(len(response.vectors or []), 2)
            self.assertEqual(len(query_response.vectors or []), 1)
            self.assertTrue(all(isinstance(vector, list) and vector for vector in response.vectors or []))
            self.assertTrue(all(isinstance(value, float) for vector in response.vectors or [] for value in vector))
            self.assertEqual(response.metadata["backend_mode"], "sentence_transformers")
            self.assertEqual(response.metadata["vector_count"], 2)

    def test_sentence_transformers_backend_rejects_invalid_vector_payloads(self) -> None:
        with tempfile.TemporaryDirectory() as repo_dir:
            repo_root = Path(repo_dir)
            _write_runtime_config(
                repo_root,
                {
                    "embeddings": {
                        "provider": "sentence_transformers",
                        "model": "sentence-transformers/test-invalid-payload-model",
                        "base_url": None,
                        "batch_size": 32,
                        "normalize_embeddings": True,
                        "device": None,
                        "request_timeout_seconds": 20,
                    }
                },
            )
            config = load_runtime_config(repo_root=repo_root)
            with patch("semantic_traversal.embeddings.import_module", side_effect=_invalid_sentence_transformers_import_module):
                backend = resolve_embedding_backend(config)

            response = backend.embed_texts(["alpha"])

            self.assertEqual(response.status, "invalid_payload")
            self.assertIsNone(response.vectors)

    def test_vector_surface_reports_no_indexed_vectors_when_vectors_are_missing(self) -> None:
        with tempfile.TemporaryDirectory() as repo_dir, tempfile.TemporaryDirectory() as data_dir:
            repo_root = _prepared_repo_root(repo_dir)
            _write_synthetic_corpus_journal_note(repo_root)
            run_ingest(
                repo_root=repo_root,
                data_root=Path(data_dir),
                source_roots=(IngestSourceRoot(label="corpus", path=repo_root / "corpus"),),
            )
            connection = sqlite3.connect(Path(data_dir) / "ingestion" / "latent_space.sqlite3")
            try:
                connection.execute("DELETE FROM chunk_vectors")
                connection.commit()
            finally:
                connection.close()

            result = run_thread_turn(
                repo_root=repo_root,
                data_root=Path(data_dir),
                user_input="fixture corpus alpha",
                llm_backend=StubLLMBackend(prefix="Probe stub response"),
                embedding_backend=_TestEmbeddingBackend(),
            )

            coverage_report = _load_turn_artifact(result.coverage_report_path)
            semantic_traversal_manifest = _load_turn_artifact(result.semantic_traversal_manifest_path)

            self.assertEqual(result.runtime_outcome, "blocked")
            self.assertEqual(coverage_report["decision"], "blocked")
            self.assertIn("vector_index_surface unavailable or missing configured embeddings", coverage_report["blocking_reasons"])
            vector_surface = next(surface for surface in semantic_traversal_manifest["activation_surfaces"] if surface["surface"] == "vector_index_surface")
            self.assertEqual(vector_surface["status"], "no_indexed_vectors")
            self.assertEqual(vector_surface["reason"], "no_indexed_vectors")

    def test_vector_surface_reports_no_valid_vectors_when_rows_are_invalid(self) -> None:
        with tempfile.TemporaryDirectory() as repo_dir:
            repo_root = _prepared_repo_root(repo_dir)
            config = load_runtime_config(repo_root=repo_root)
            connection = sqlite3.connect(":memory:")
            try:
                connection.row_factory = sqlite3.Row
                connection.execute(
                    """
                    CREATE TABLE chunks (
                        chunk_id TEXT PRIMARY KEY,
                        note_id TEXT NOT NULL,
                        source_root_label TEXT NOT NULL,
                        relative_path TEXT NOT NULL,
                        note_path TEXT NOT NULL,
                        note_title TEXT NOT NULL,
                        section_id TEXT NOT NULL,
                        section_label TEXT NOT NULL,
                        section_path_json TEXT NOT NULL,
                        paragraph_ordinal INTEGER NOT NULL,
                        paragraph_text TEXT NOT NULL,
                        chunk_hash TEXT NOT NULL
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE TABLE chunk_vectors (
                        chunk_id TEXT PRIMARY KEY,
                        vector_json TEXT NOT NULL
                    )
                    """
                )
                connection.execute(
                    """
                    INSERT INTO chunks VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "chunk-1",
                        "note-1",
                        "corpus",
                        "fixture.md",
                        str(repo_root / "corpus" / "fixture.md"),
                        "Fixture Note",
                        "section-1",
                        "Fixture Section",
                        json.dumps(["Fixture Section"]),
                        1,
                        "Fixture text",
                        "hash-1",
                    ),
                )
                connection.execute(
                    "INSERT INTO chunk_vectors VALUES (?, ?)",
                    ("chunk-1", json.dumps("invalid")),
                )
                connection.commit()

                candidates, status = _query_vector_candidates(
                    connection,
                    query_text="fixture",
                    embedding_backend=_TestEmbeddingBackend(),
                    config=config,
                )
            finally:
                connection.close()

            self.assertEqual(candidates, [])
            self.assertEqual(status, "no_valid_vectors")

    def test_unsupported_provider_configuration_fails_closed_for_semantic_extraction(self) -> None:
        with tempfile.TemporaryDirectory() as repo_dir:
            repo_root = Path(repo_dir)
            _write_runtime_config(
                repo_root,
                {"semantic_extraction": {"provider": "unsupported", "model": "fixture-semantic"}},
            )
            config = load_runtime_config(repo_root=repo_root)
            backend = resolve_semantic_extractor_backend(repo_root=repo_root, config=config)

            response = backend.extract_contextual({"raw_user_input": "hello", "instruction": "test"})
            self.assertEqual(response.status, "unavailable")
            self.assertIn("unsupported semantic extraction provider", response.metadata.get("reason", ""))

    def test_unsupported_provider_configuration_fails_closed_for_embeddings(self) -> None:
        with tempfile.TemporaryDirectory() as repo_dir:
            repo_root = Path(repo_dir)
            _write_runtime_config(
                repo_root,
                {
                    "embeddings": {"provider": "unsupported", "model": "fixture-embed"},
                },
            )
            config = load_runtime_config(repo_root=repo_root)
            backend = resolve_embedding_backend(config)

            response = backend.embed_texts(["hello"])
            self.assertEqual(response.status, "unavailable")
            self.assertIn("unsupported embedding provider", response.metadata.get("reason", ""))

    def test_missing_semantic_extraction_provider_raises_config_error(self) -> None:
        with tempfile.TemporaryDirectory() as repo_dir:
            repo_root = Path(repo_dir)
            raw_config = json.loads(json.dumps(load_runtime_config(repo_root=REPO_ROOT, config_path=str(DEFAULT_CONFIG_SOURCE)).raw))
            raw_config["semantic_extraction"].pop("provider")
            _write_runtime_config_document(repo_root, raw_config)

            with self.assertRaisesRegex(ConfigError, r"Missing required runtime config field: root\.semantic_extraction\.provider"):
                load_runtime_config(repo_root=repo_root)

    def test_missing_embeddings_provider_raises_config_error(self) -> None:
        with tempfile.TemporaryDirectory() as repo_dir:
            repo_root = Path(repo_dir)
            raw_config = json.loads(json.dumps(load_runtime_config(repo_root=REPO_ROOT, config_path=str(DEFAULT_CONFIG_SOURCE)).raw))
            raw_config["embeddings"].pop("provider")
            _write_runtime_config_document(repo_root, raw_config)

            with self.assertRaisesRegex(ConfigError, r"Missing required runtime config field: root\.embeddings\.provider"):
                load_runtime_config(repo_root=repo_root)

    def test_missing_embeddings_model_raises_config_error(self) -> None:
        with tempfile.TemporaryDirectory() as repo_dir:
            repo_root = Path(repo_dir)
            raw_config = json.loads(json.dumps(load_runtime_config(repo_root=REPO_ROOT, config_path=str(DEFAULT_CONFIG_SOURCE)).raw))
            raw_config["embeddings"].pop("model")
            _write_runtime_config_document(repo_root, raw_config)

            with self.assertRaisesRegex(ConfigError, r"Missing required runtime config field: root\.embeddings\.model"):
                load_runtime_config(repo_root=repo_root)

    def test_missing_storage_field_raises_config_error(self) -> None:
        with tempfile.TemporaryDirectory() as repo_dir:
            repo_root = Path(repo_dir)
            raw_config = json.loads(json.dumps(load_runtime_config(repo_root=REPO_ROOT, config_path=str(DEFAULT_CONFIG_SOURCE)).raw))
            raw_config["storage"].pop("ingestion_root")
            _write_runtime_config_document(repo_root, raw_config)

            with self.assertRaisesRegex(ConfigError, r"Missing required runtime config field: root\.storage\.ingestion_root"):
                load_runtime_config(repo_root=repo_root)

    def test_unknown_storage_field_raises_config_error(self) -> None:
        with tempfile.TemporaryDirectory() as repo_dir:
            repo_root = Path(repo_dir)
            raw_config = json.loads(json.dumps(load_runtime_config(repo_root=REPO_ROOT, config_path=str(DEFAULT_CONFIG_SOURCE)).raw))
            raw_config["storage"]["unexpected_layout_field"] = "nope"
            _write_runtime_config_document(repo_root, raw_config)

            with self.assertRaisesRegex(ConfigError, r"Unknown runtime config field: root\.storage\.unexpected_layout_field"):
                load_runtime_config(repo_root=repo_root)

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

    def test_storage_config_controls_ingest_artifact_layout(self) -> None:
        with tempfile.TemporaryDirectory() as repo_dir, tempfile.TemporaryDirectory() as data_dir:
            repo_root = Path(repo_dir)
            _write_runtime_config(
                repo_root,
                {
                    "storage": {
                        "ingestion_root": "custom-ingestion",
                        "ingestion_database_filename": "custom-latent.sqlite3",
                        "ingestion_manifests_root": "custom-ingestion/manifests-v2",
                        "latest_ingest_manifest_filename": "latest-ingest.json",
                    },
                },
            )
            (repo_root / "corpus").mkdir(parents=True, exist_ok=True)
            _copy_note(FIXTURE_NOTE, repo_root / "tests" / "fixtures", "JOURNAL/2025-09/01_Monday.md")
            config = load_runtime_config(repo_root=repo_root)
            result = run_ingest(
                repo_root=repo_root,
                data_root=Path(data_dir),
                source_roots=build_default_source_roots(repo_root, config=config),
                config=config,
            )

            self.assertEqual(result.database_path, (Path(data_dir) / "custom-ingestion" / "custom-latent.sqlite3").resolve())
            self.assertEqual(result.manifest_path, (Path(data_dir) / "custom-ingestion" / "manifests-v2" / f"{result.run_id}.json").resolve())
            self.assertTrue((Path(data_dir) / "custom-ingestion" / "manifests-v2" / "latest-ingest.json").exists())

    def test_storage_config_controls_thread_artifact_layout(self) -> None:
        with tempfile.TemporaryDirectory() as repo_dir, tempfile.TemporaryDirectory() as data_dir:
            repo_root = Path(repo_dir)
            _write_runtime_config(
                repo_root,
                {
                    "storage": {
                        "threads_root": "thread-store",
                        "turns_root": "turn-batches",
                        "turn_directory_prefix": "phase-",
                        "conversation_thread_filename": "conversation.json",
                        "thread_state_filename": "state.json",
                        "thread_ledger_filename": "ledger.jsonl",
                    },
                },
            )
            config = load_runtime_config(repo_root=repo_root)

            result = run_thread_turn(
                repo_root=repo_root,
                data_root=Path(data_dir),
                user_input="Please retrieve the candy snack food before bed note.",
                llm_backend=StubLLMBackend(prefix="Probe stub response"),
                semantic_extractor_backend=DisabledSemanticExtractorBackend(),
                config=config,
            )

            self.assertEqual(result.thread_root.name, result.thread_id)
            self.assertEqual(result.conversation_thread_path.name, "conversation.json")
            self.assertEqual(result.thread_state_path.name, "state.json")
            self.assertEqual(result.thread_ledger_path.name, "ledger.jsonl")
            self.assertTrue(result.turn_root.name.startswith("phase-"))

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

    def test_malformed_activation_hints_blocks_without_crashing(self) -> None:
        with tempfile.TemporaryDirectory() as repo_dir, tempfile.TemporaryDirectory() as data_dir:
            repo_root = _prepared_repo_root(repo_dir)
            with _ollama_fixture_server(response_mode="malformed") as base_url:
                _write_runtime_config(
                    repo_root,
                    {
                        "semantic_extraction": {"model": "fixture-semantic", "base_url": base_url},
                    },
                )
                _copy_note(FIXTURE_NOTE, repo_root / "tests" / "fixtures", "JOURNAL/2025-09/01_Monday.md")
                _write_synthetic_corpus_journal_note(repo_root)
                run_ingest(repo_root=repo_root, data_root=Path(data_dir), source_roots=build_default_source_roots(repo_root))

                result = run_thread_turn(
                    repo_root=repo_root,
                    data_root=Path(data_dir),
                    user_input="What do I think about candy snack food before bed?",
                    llm_backend=StubLLMBackend(prefix="Probe stub response"),
                )

            self.assertEqual(result.runtime_outcome, "blocked")
            self.assertEqual(result.coverage_report["decision"], "blocked")
            self.assertIsInstance(result.semantic_context_packet["activation_hints"], dict)
            self.assertEqual(result.semantic_context_packet["activation_hints"], {})
            self.assertIsNone(result.semantic_context_packet["semantic_coverage_target"])
            self.assertFalse(result.semantic_context_packet["semantic_contract_validation"]["valid"])
            self.assertIn(
                "activation_hints expected dict, got list",
                result.semantic_context_packet["semantic_contract_validation"]["reasons"],
            )
            self.assertIn(
                "semantic_coverage_target expected dict, got str",
                result.semantic_context_packet["semantic_contract_validation"]["reasons"],
            )
            self.assertIn("activation_hints expected dict, got list", result.coverage_report["blocking_reasons"])
            self.assertIn("semantic_coverage_target missing or invalid", result.coverage_report["blocking_reasons"])
            self.assertIn("semantic extraction parsed but failed contract validation", result.coverage_report["blocking_reasons"])
            self.assertEqual(result.llm_metadata["mode"], "not_called")

    def test_followup_semantic_target_blocks_when_resolved_referent_is_lost(self) -> None:
        with tempfile.TemporaryDirectory() as repo_dir, tempfile.TemporaryDirectory() as data_dir:
            repo_root = _prepared_repo_root(repo_dir)
            (repo_root / "corpus").mkdir(parents=True, exist_ok=True)
            _copy_note(FIXTURE_NOTE, repo_root / "tests" / "fixtures", "JOURNAL/2025-09/01_Monday.md")
            run_ingest(repo_root=repo_root, data_root=Path(data_dir), source_roots=build_default_source_roots(repo_root))

            first_turn = run_thread_turn(
                repo_root=repo_root,
                data_root=Path(data_dir),
                user_input="What do I think about candy snack food before bed?",
                llm_backend=StubLLMBackend(prefix="Probe stub response"),
                semantic_extractor_backend=StubSemanticExtractorBackend(),
            )
            parsed_backend = _ParsedSemanticExtractorBackend(
                contextual_payload={
                    "raw_user_input": "I wonder if there's anything specific about how it makes me feel?",
                    "contextual_user_intent": "follow-up under-anchored",
                    "thread_relevant_context": ["What do I think about candy snack food before bed?"],
                    "semantic_pressure": None,
                    "perturbation_nodes": [{"id": "node:feel", "label": "feel", "kind": "lexical_term"}],
                    "contextual_salt_nodes": [],
                    "perturbation_semantic_graph": {"nodes": [], "edges": []},
                    "semantic_coverage_target": {
                        "must_preserve": ["feelings", "specific about how it makes me feel"],
                        "should_include": ["specific"],
                        "avoid_satisfying_with": [],
                        "query_text": "I wonder if there's anything specific about how it makes me feel?",
                        "allow_no_retrieval_needed": False,
                    },
                    "activation_hints": {
                        "lexical_terms": ["specific", "feel", "feelings"],
                        "phrases": ["how it makes me feel"],
                        "conceptual_neighbors": [],
                        "relation_hints": [],
                        "temporal_hints": [],
                        "entity_hints": [],
                    },
                    "limitations": ["model-generated extraction", "additive only", "not authoritative"],
                }
            )
            result = run_thread_turn(
                repo_root=repo_root,
                data_root=Path(data_dir),
                user_input="I wonder if there's anything specific about how it makes me feel?",
                llm_backend=StubLLMBackend(prefix="Probe stub response"),
                thread_id=first_turn.thread_id,
                semantic_extractor_backend=parsed_backend,
            )

            self.assertEqual(result.runtime_outcome, "blocked")
            self.assertEqual(result.llm_metadata["mode"], "not_called")
            self.assertIn(
                "follow-up semantic target missing resolved referent",
                result.coverage_report["blocking_reasons"],
            )

    def test_contextual_request_includes_deterministic_resolved_referent_candidates(self) -> None:
        request_packet = _build_contextual_extraction_request(
            user_input="I wonder if there's anything specific about how it makes me feel?",
            prior_thread_state={
                "latest_turn_id": 1,
                "latest_user_input": "What do I think about candy snack food before bed?",
                "recent_messages": [
                    {"role": "user", "content": "What do I think about candy snack food before bed?"},
                    {"role": "assistant", "content": "You have mixed feelings about late candy snacks."},
                ],
                "recent_semantic_trajectory": [
                    "What do I think about candy snack food before bed?",
                    "You have mixed feelings about late candy snacks.",
                ],
            },
            isolated_semantic_extraction={},
        )

        self.assertIn("deterministic_followup_detection", request_packet)
        self.assertIn("deterministic_resolved_referent_candidates", request_packet)
        self.assertEqual(
            request_packet["deterministic_resolved_referent_candidates"][0]["resolved_to"],
            "candy snack food before bed",
        )

    def test_non_referential_contextual_request_does_not_include_referent_candidate_pressure(self) -> None:
        request_packet = _build_contextual_extraction_request(
            user_input="What do I think about candy snack food before bed?",
            prior_thread_state={
                "latest_turn_id": 0,
                "recent_messages": [],
                "recent_semantic_trajectory": [],
            },
            isolated_semantic_extraction={},
        )

        self.assertIn("prior_thread_state", request_packet)
        self.assertNotIn("extractor_thread_context", request_packet)
        self.assertNotIn("deterministic_resolved_referent_candidates", request_packet)
        self.assertNotIn("Use deterministic_resolved_referent_candidates", request_packet["instruction"])

    def test_contextual_request_uses_compact_extractor_thread_context_without_duplicate_assistant_prose_for_referential_followup(self) -> None:
        request_packet = _build_contextual_extraction_request(
            user_input="I wonder if there's anything specific about how it makes me feel?",
            prior_thread_state={
                "latest_turn_id": 1,
                "latest_user_input": "What do I think about candy snack food before bed?",
                "conversation_summary": "Assistant summary about candy snack food before bed.",
                "recent_messages": [
                    {"role": "user", "content": "What do I think about candy snack food before bed?"},
                    {"role": "assistant", "content": "Assistant summary about candy snack food before bed."},
                ],
                "current_user_goals": ["Understand bedtime candy feelings"],
                "open_questions": ["Does it affect sleep?"],
                "active_constraints": ["Keep retrieval lexical"],
                "recent_semantic_trajectory": [
                    "What do I think about candy snack food before bed?",
                    "Assistant summary about candy snack food before bed.",
                ],
                "latest_assistant_response": "Assistant summary about candy snack food before bed.",
            },
            isolated_semantic_extraction={},
        )

        self.assertNotIn("prior_thread_state", request_packet)
        extractor_thread_context = request_packet["extractor_thread_context"]
        self.assertEqual(extractor_thread_context["latest_turn_id"], 1)
        self.assertEqual(
            extractor_thread_context["recent_user_messages"],
            ["What do I think about candy snack food before bed?"],
        )
        self.assertNotIn("latest_assistant_response", extractor_thread_context)
        self.assertEqual(
            extractor_thread_context["recent_semantic_trajectory"],
            ["What do I think about candy snack food before bed?"],
        )

    def test_non_referential_contextual_prompt_omits_referent_candidate_instruction_text(self) -> None:
        prompt = _build_ollama_prompt(
            packet=_build_contextual_extraction_request(
                user_input="What do I think about candy snack food before bed?",
                prior_thread_state={
                    "latest_turn_id": 0,
                    "recent_messages": [],
                    "recent_semantic_trajectory": [],
                },
                isolated_semantic_extraction={},
            )
        )
        self.assertNotIn("Use deterministic_resolved_referent_candidates", prompt)

    def test_referential_followup_prompt_includes_referent_candidate_instruction_text(self) -> None:
        prompt = _build_ollama_prompt(
            packet=_build_contextual_extraction_request(
                user_input="I wonder if there's anything specific about how it makes me feel?",
                prior_thread_state={
                    "latest_turn_id": 1,
                    "latest_user_input": "What do I think about candy snack food before bed?",
                    "recent_messages": [
                        {"role": "user", "content": "What do I think about candy snack food before bed?"},
                    ],
                    "recent_semantic_trajectory": ["What do I think about candy snack food before bed?"],
                },
                isolated_semantic_extraction={},
            )
        )
        self.assertIn("Use deterministic_resolved_referent_candidates", prompt)

    def test_expletive_possible_question_does_not_require_referent_resolution(self) -> None:
        detection = _detect_followup_signals(
            "Is it possible to use qwen3:4b here?",
            {"recent_messages": [{"role": "user", "content": "What do I think about candy snack food before bed?"}]},
        )
        self.assertTrue(detection["is_referential_followup"])
        self.assertFalse(detection["requires_referent_resolution"])
        self.assertIn("short_followup_question", detection["signals"])
        self.assertNotIn("deictic:it", detection["referential_signals"])

    def test_expletive_worth_question_does_not_require_referent_resolution(self) -> None:
        detection = _detect_followup_signals(
            "Is it worth changing the schema?",
            {"recent_messages": [{"role": "user", "content": "What do I think about candy snack food before bed?"}]},
        )
        self.assertTrue(detection["is_referential_followup"])
        self.assertFalse(detection["requires_referent_resolution"])
        self.assertIn("short_followup_question", detection["signals"])
        self.assertNotIn("deictic:it", detection["referential_signals"])

    def test_referential_followup_still_requires_referent_resolution(self) -> None:
        detection = _detect_followup_signals(
            "I wonder if there's anything specific about how it makes me feel?",
            {"recent_messages": [{"role": "user", "content": "What do I think about candy snack food before bed?"}]},
        )
        self.assertTrue(detection["is_referential_followup"])
        self.assertTrue(detection["requires_referent_resolution"])
        self.assertIn("how it makes me feel", detection["referential_signals"])

    def test_followup_semantic_target_contract_validation_passes_when_resolved_referent_is_anchored(self) -> None:
        with tempfile.TemporaryDirectory() as repo_dir, tempfile.TemporaryDirectory() as data_dir:
            repo_root = _prepared_repo_root(repo_dir)
            (repo_root / "corpus").mkdir(parents=True, exist_ok=True)
            _copy_note(FIXTURE_NOTE, repo_root / "tests" / "fixtures", "JOURNAL/2025-09/01_Monday.md")
            _write_synthetic_corpus_journal_note(repo_root)
            run_ingest(repo_root=repo_root, data_root=Path(data_dir), source_roots=build_default_source_roots(repo_root))

            first_turn = run_thread_turn(
                repo_root=repo_root,
                data_root=Path(data_dir),
                user_input="What do I think about candy snack food before bed?",
                llm_backend=StubLLMBackend(prefix="Probe stub response"),
                semantic_extractor_backend=StubSemanticExtractorBackend(),
            )
            parsed_backend = _ParsedSemanticExtractorBackend(
                contextual_payload={
                    "raw_user_input": "I wonder if there's anything specific about how it makes me feel?",
                    "contextual_user_intent": "follow-up anchored",
                    "thread_relevant_context": ["What do I think about candy snack food before bed?"],
                    "semantic_pressure": None,
                    "resolved_referents": [
                        {
                            "surface_form": "it",
                            "resolved_to": "candy snack food before bed",
                            "source": "prior_thread_state.recent_messages",
                            "confidence": "high",
                            "required_for_target": True,
                        }
                    ],
                    "perturbation_nodes": [{"id": "node:feel", "label": "feel", "kind": "lexical_term"}],
                    "contextual_salt_nodes": [],
                    "perturbation_semantic_graph": {"nodes": [], "edges": []},
                    "semantic_coverage_target": {
                        "must_preserve": ["how candy snack food before bed makes me feel"],
                        "should_include": ["specific"],
                        "avoid_satisfying_with": [],
                        "query_text": "I wonder if there's anything specific about how it makes me feel?",
                        "allow_no_retrieval_needed": False,
                    },
                    "activation_hints": {
                        "lexical_terms": ["specific", "feel", "candy", "snack", "food", "bed"],
                        "phrases": ["how candy snack food before bed makes me feel"],
                        "conceptual_neighbors": [],
                        "relation_hints": [],
                        "temporal_hints": [],
                        "entity_hints": ["candy snack food before bed"],
                    },
                    "followup_detection": {
                        "is_referential_followup": True,
                        "requires_referent_resolution": True,
                        "signals": ["how it makes me feel"],
                        "surface_forms": ["it"],
                    },
                    "limitations": ["model-generated extraction", "additive only", "not authoritative"],
                }
            )
            result = run_thread_turn(
                repo_root=repo_root,
                data_root=Path(data_dir),
                user_input="I wonder if there's anything specific about how it makes me feel?",
                llm_backend=StubLLMBackend(prefix="Probe stub response"),
                thread_id=first_turn.thread_id,
                semantic_extractor_backend=parsed_backend,
            )

            semantic_context_packet = _load_turn_artifact(result.semantic_context_packet_path)
            self.assertTrue(semantic_context_packet["semantic_contract_validation"]["valid"])
            self.assertEqual(semantic_context_packet["resolved_referents"][0]["resolved_to"], "candy snack food before bed")
            self.assertNotIn(
                "semantic_coverage_target must_preserve does not include required resolved referent: candy snack food before bed",
                result.coverage_report["blocking_reasons"],
            )

    def test_non_followup_malformed_resolved_referents_blocks_closed_without_crashing(self) -> None:
        with tempfile.TemporaryDirectory() as repo_dir, tempfile.TemporaryDirectory() as data_dir:
            repo_root = _prepared_repo_root(repo_dir)
            (repo_root / "corpus").mkdir(parents=True, exist_ok=True)
            _copy_note(FIXTURE_NOTE, repo_root / "tests" / "fixtures", "JOURNAL/2025-09/01_Monday.md")
            run_ingest(repo_root=repo_root, data_root=Path(data_dir), source_roots=build_default_source_roots(repo_root))

            parsed_backend = _ParsedSemanticExtractorBackend(
                contextual_payload={
                    "raw_user_input": "Please retrieve the fixture note.",
                    "contextual_user_intent": "malformed referents",
                    "thread_relevant_context": [],
                    "semantic_pressure": None,
                    "resolved_referents": "it",
                    "perturbation_nodes": [{"id": "node:feel", "label": "feel", "kind": "lexical_term"}],
                    "contextual_salt_nodes": [],
                    "perturbation_semantic_graph": {"nodes": [], "edges": []},
                    "semantic_coverage_target": {
                        "must_preserve": ["feelings"],
                        "should_include": ["specific"],
                        "avoid_satisfying_with": [],
                        "query_text": "Please retrieve the fixture note.",
                        "allow_no_retrieval_needed": False,
                    },
                    "activation_hints": {
                        "lexical_terms": ["specific", "feel"],
                        "phrases": ["how it makes me feel"],
                        "conceptual_neighbors": [],
                        "relation_hints": [],
                        "temporal_hints": [],
                        "entity_hints": [],
                    },
                    "followup_detection": {
                        "is_referential_followup": True,
                        "requires_referent_resolution": True,
                        "signals": ["how it makes me feel"],
                        "surface_forms": ["it"],
                    },
                    "limitations": ["model-generated extraction", "additive only", "not authoritative"],
                }
            )
            result = run_thread_turn(
                repo_root=repo_root,
                data_root=Path(data_dir),
                user_input="Please retrieve the fixture note.",
                llm_backend=StubLLMBackend(prefix="Probe stub response"),
                semantic_extractor_backend=parsed_backend,
            )

            self.assertEqual(result.runtime_outcome, "blocked")
            self.assertEqual(result.llm_metadata["mode"], "not_called")
            self.assertIn("resolved_referents expected list, got str", result.semantic_context_packet["semantic_contract_validation"]["reasons"])

    def test_malformed_resolved_referent_item_fields_block_closed_without_crashing(self) -> None:
        with tempfile.TemporaryDirectory() as repo_dir, tempfile.TemporaryDirectory() as data_dir:
            repo_root = _prepared_repo_root(repo_dir)
            (repo_root / "corpus").mkdir(parents=True, exist_ok=True)
            _copy_note(FIXTURE_NOTE, repo_root / "tests" / "fixtures", "JOURNAL/2025-09/01_Monday.md")
            run_ingest(repo_root=repo_root, data_root=Path(data_dir), source_roots=build_default_source_roots(repo_root))

            first_turn = run_thread_turn(
                repo_root=repo_root,
                data_root=Path(data_dir),
                user_input="What do I think about candy snack food before bed?",
                llm_backend=StubLLMBackend(prefix="Probe stub response"),
                semantic_extractor_backend=StubSemanticExtractorBackend(),
            )
            parsed_backend = _ParsedSemanticExtractorBackend(
                contextual_payload={
                    "raw_user_input": "I wonder if there's anything specific about how it makes me feel?",
                    "contextual_user_intent": "malformed referent item fields",
                    "thread_relevant_context": ["What do I think about candy snack food before bed?"],
                    "semantic_pressure": None,
                    "resolved_referents": [
                        {
                            "surface_form": 123,
                            "resolved_to": [],
                            "source": {"note": "bad"},
                            "confidence": "certain",
                            "required_for_target": "yes",
                        }
                    ],
                    "perturbation_nodes": [{"id": "node:feel", "label": "feel", "kind": "lexical_term"}],
                    "contextual_salt_nodes": [],
                    "perturbation_semantic_graph": {"nodes": [], "edges": []},
                    "semantic_coverage_target": {
                        "must_preserve": ["feelings"],
                        "should_include": ["specific"],
                        "avoid_satisfying_with": [],
                        "query_text": "I wonder if there's anything specific about how it makes me feel?",
                        "allow_no_retrieval_needed": False,
                    },
                    "activation_hints": {
                        "lexical_terms": ["specific", "feel"],
                        "phrases": ["how it makes me feel"],
                        "conceptual_neighbors": [],
                        "relation_hints": [],
                        "temporal_hints": [],
                        "entity_hints": [],
                    },
                    "followup_detection": {
                        "is_referential_followup": True,
                        "requires_referent_resolution": True,
                        "signals": ["how it makes me feel"],
                        "surface_forms": ["it"],
                    },
                    "limitations": ["model-generated extraction", "additive only", "not authoritative"],
                }
            )
            result = run_thread_turn(
                repo_root=repo_root,
                data_root=Path(data_dir),
                user_input="I wonder if there's anything specific about how it makes me feel?",
                llm_backend=StubLLMBackend(prefix="Probe stub response"),
                thread_id=first_turn.thread_id,
                semantic_extractor_backend=parsed_backend,
            )

            self.assertEqual(result.runtime_outcome, "blocked")
            self.assertEqual(result.llm_metadata["mode"], "not_called")
            reasons = result.semantic_context_packet["semantic_contract_validation"]["reasons"]
            self.assertIn("resolved_referents[0].surface_form expected str, got int", reasons)
            self.assertIn("resolved_referents[0].resolved_to expected str, got list", reasons)
            self.assertIn("resolved_referents[0].source expected str, got dict", reasons)
            self.assertIn("resolved_referents[0].confidence expected one of high, medium, low, got str", reasons)
            self.assertIn("resolved_referents[0].required_for_target expected bool, got str", reasons)

    def test_ollama_generate_payload_omits_format_and_preserves_resolved_referents(self) -> None:
        with tempfile.TemporaryDirectory() as repo_dir, tempfile.TemporaryDirectory() as data_dir:
            repo_root = Path(repo_dir)
            with _ollama_fixture_server() as base_url:
                _write_runtime_config(
                    repo_root,
                    {
                        "semantic_extraction": {"model": "fixture-semantic", "base_url": base_url},
                    },
                )
                (repo_root / "corpus").mkdir(parents=True, exist_ok=True)
                _copy_note(FIXTURE_NOTE, repo_root / "tests" / "fixtures", "JOURNAL/2025-09/01_Monday.md")
                run_ingest(repo_root=repo_root, data_root=Path(data_dir), source_roots=build_default_source_roots(repo_root))

                first_turn = run_thread_turn(
                    repo_root=repo_root,
                    data_root=Path(data_dir),
                    user_input="What do I think about candy snack food before bed?",
                    llm_backend=StubLLMBackend(prefix="Probe stub response"),
                )
                result = run_thread_turn(
                    repo_root=repo_root,
                    data_root=Path(data_dir),
                    user_input="I wonder if there's anything specific about how it makes me feel?",
                    llm_backend=StubLLMBackend(prefix="Probe stub response"),
                    thread_id=first_turn.thread_id,
                )

            semantic_context_packet = _load_turn_artifact(result.semantic_context_packet_path)
            contextual_packet = _load_turn_artifact(result.contextual_semantic_extraction_packet_path)
            self.assertTrue(semantic_context_packet["semantic_contract_validation"]["valid"])
            self.assertTrue(semantic_context_packet["resolved_referents"])
            self.assertEqual(semantic_context_packet["resolved_referents"][0]["resolved_to"], "candy snack food before bed")
            self.assertIsNotNone(_OllamaFixtureHandler.last_generate_payload)
            self.assertNotIn("format", _OllamaFixtureHandler.last_generate_payload)
            self.assertEqual(
                contextual_packet["request_packet"]["deterministic_resolved_referent_candidates"][0]["resolved_to"],
                "candy snack food before bed",
            )

    def test_followup_coverage_blocks_when_only_generic_feelings_are_satisfied(self) -> None:
        with tempfile.TemporaryDirectory() as repo_dir, tempfile.TemporaryDirectory() as data_dir:
            repo_root = _prepared_repo_root(repo_dir)
            _write_markdown_fixture(
                repo_root / "corpus",
                "coverage/generic_feelings_fixture.md",
                """
                ---
                note_type: coverage_fixture
                ---

                # Generic Feelings Fixture

                ## Fixture Coverage Section

                This paragraph mentions feelings felt anxiety urgency context influence and a specific sense of concern.
                """,
            )
            _copy_note(FIXTURE_NOTE, repo_root / "tests" / "fixtures", "JOURNAL/2025-09/01_Monday.md")
            run_ingest(repo_root=repo_root, data_root=Path(data_dir), source_roots=(IngestSourceRoot(label="corpus", path=repo_root / "corpus"),))

            first_turn = run_thread_turn(
                repo_root=repo_root,
                data_root=Path(data_dir),
                user_input="What do I think about candy snack food before bed?",
                llm_backend=StubLLMBackend(prefix="Probe stub response"),
                semantic_extractor_backend=StubSemanticExtractorBackend(),
            )
            parsed_backend = _ParsedSemanticExtractorBackend(
                contextual_payload={
                    "raw_user_input": "I wonder if there's anything specific about how it makes me feel?",
                    "contextual_user_intent": "generic feelings only",
                    "thread_relevant_context": ["What do I think about candy snack food before bed?"],
                    "semantic_pressure": None,
                    "perturbation_nodes": [{"id": "node:feel", "label": "feel", "kind": "lexical_term"}],
                    "contextual_salt_nodes": [],
                    "perturbation_semantic_graph": {"nodes": [], "edges": []},
                    "semantic_coverage_target": {
                        "must_preserve": ["feelings"],
                        "should_include": ["specific"],
                        "avoid_satisfying_with": ["feelings", "felt", "anxiety", "urgency", "context", "influence"],
                        "query_text": "I wonder if there's anything specific about how it makes me feel?",
                        "allow_no_retrieval_needed": False,
                    },
                    "activation_hints": {
                        "lexical_terms": ["specific", "feel", "feelings", "anxiety", "urgency"],
                        "phrases": ["how it makes me feel"],
                        "conceptual_neighbors": [],
                        "relation_hints": [],
                        "temporal_hints": [],
                        "entity_hints": [],
                    },
                    "followup_detection": {
                        "is_referential_followup": True,
                        "requires_referent_resolution": True,
                        "signals": ["how it makes me feel"],
                        "surface_forms": ["it"],
                    },
                    "resolved_referents": [
                        {
                            "surface_form": "it",
                            "resolved_to": "candy snack food before bed",
                            "source": "prior_thread_state.recent_messages",
                            "confidence": "high",
                            "required_for_target": True,
                        }
                    ],
                    "limitations": ["model-generated extraction", "additive only", "not authoritative"],
                }
            )
            result = run_thread_turn(
                repo_root=repo_root,
                data_root=Path(data_dir),
                user_input="I wonder if there's anything specific about how it makes me feel?",
                llm_backend=StubLLMBackend(prefix="Probe stub response"),
                thread_id=first_turn.thread_id,
                semantic_extractor_backend=parsed_backend,
            )

            self.assertEqual(result.runtime_outcome, "blocked")
            self.assertEqual(result.llm_metadata["mode"], "not_called")
            self.assertIn(
                "semantic_coverage_target must_preserve does not include required resolved referent: candy snack food before bed",
                result.coverage_report["blocking_reasons"],
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

    def test_non_referential_contextual_extraction_request_packet_preserves_prior_thread_state(self) -> None:
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
            request_packet = contextual_packet["request_packet"]
            self.assertIn("prior_thread_state", request_packet)
            self.assertNotIn("extractor_thread_context", request_packet)
            self.assertNotIn("deterministic_resolved_referent_candidates", request_packet)
            prior_thread_state = request_packet["prior_thread_state"]
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
            semantic_traversal_manifest = _load_turn_artifact(result.semantic_traversal_manifest_path)
            retrieval_packet = _load_turn_artifact(result.retrieval_packet_path)
            ledger = read_ledger(result.thread_ledger_path)

            self.assertEqual(result.runtime_outcome, "blocked")
            self.assertEqual(coverage_report["decision"], "blocked")
            self.assertEqual(retrieval_packet["retrieval_observation"], "matched_chunks")
            self.assertTrue(retrieval_packet["selected_chunks"])
            self.assertTrue(semantic_traversal_manifest["surface_contributions"]["vector_index_surface"])
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
            self.assertEqual(retrieval_packet["retrieval_observation"], "matched_chunks")
            self.assertTrue(retrieval_packet["selected_chunks"])
            self.assertTrue(semantic_traversal_manifest["surface_contributions"]["vector_index_surface"])
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
            self.assertTrue(result.coverage_report["semantic_target_coverage"]["covered"])
            self.assertTrue(result.coverage_report["semantic_target_coverage"]["must_preserve"][0]["covered"])
            self.assertEqual(result.coverage_report["semantic_target_coverage"]["must_preserve"][0]["evidence"][0]["field"], "paragraph_text")
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

            graph_candidates = result.semantic_traversal_manifest["candidate_regions"]["graph_layer"]
            graph_candidate = next((candidate for candidate in graph_candidates if candidate.get("graph_path")), None)
            self.assertIsNotNone(graph_candidate)
            self.assertEqual(graph_candidate["hop_count"], 1)
            self.assertTrue(graph_candidate["edge_types"])
            self.assertTrue(set(graph_candidate["edge_types"]).intersection({"sibling", "wikilink"}))
            self.assertTrue(graph_candidate["graph_path"])

            graph_selected_chunk = next(
                (chunk for chunk in result.retrieval_packet["selected_chunks"] if "graph_layer" in chunk["surface_contributions"]),
                None,
            )
            self.assertIsNotNone(graph_selected_chunk)
            self.assertEqual(graph_selected_chunk["hop_count"], 1)
            self.assertTrue(graph_selected_chunk["edge_types"])
            self.assertTrue(set(graph_selected_chunk["edge_types"]).intersection({"sibling", "wikilink"}))
            self.assertTrue(graph_selected_chunk["graph_path"])

            selected_surface_contributions = {
                surface_name
                for candidate in result.semantic_traversal_manifest["selected_candidates"]
                for surface_name in list(candidate.get("surface_contributions") or [])
            }
            self.assertIn("lexical_index_surface", selected_surface_contributions)
            self.assertIn("vector_index_surface", selected_surface_contributions)
            self.assertIn("primary_corpus", selected_surface_contributions)
            self.assertIn("graph_layer", selected_surface_contributions)

            for surface_name in ("lexical_index_surface", "primary_corpus", "vector_index_surface", "synthetic_nodes"):
                for candidate in result.semantic_traversal_manifest["candidate_regions"][surface_name]:
                    self.assertNotIn("graph_path", candidate)
                    self.assertNotIn("hop_count", candidate)
                    self.assertNotIn("edge_types", candidate)

    def test_semantic_target_coverage_blocks_when_must_preserve_evidence_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as repo_dir, tempfile.TemporaryDirectory() as data_dir:
            repo_root = _prepared_repo_root(repo_dir)
            (repo_root / "corpus").mkdir(parents=True, exist_ok=True)
            _copy_note(FIXTURE_NOTE, repo_root / "tests" / "fixtures", "JOURNAL/2025-09/01_Monday.md")
            run_ingest(repo_root=repo_root, data_root=Path(data_dir), source_roots=build_default_source_roots(repo_root))

            parsed_backend = _ParsedSemanticExtractorBackend(
                contextual_payload={
                    "raw_user_input": "Please retrieve the candy snack food before bed note and check the unseen phrase.",
                    "contextual_user_intent": "coverage miss",
                    "thread_relevant_context": [],
                    "semantic_pressure": None,
                    "perturbation_nodes": [{"id": "node:candy", "label": "candy", "kind": "lexical_term"}],
                    "contextual_salt_nodes": [],
                    "perturbation_semantic_graph": {"nodes": [], "edges": []},
                    "candidate_targets": ["candy", "unseen"],
                    "candidate_relations": [],
                    "semantic_coverage_target": {
                        "must_preserve": ["unseen phrase alpha"],
                        "should_include": ["midnight orchard"],
                        "avoid_satisfying_with": [],
                        "query_text": "Please retrieve the candy snack food before bed note and check the unseen phrase.",
                        "allow_no_retrieval_needed": False,
                    },
                    "activation_hints": {
                        "lexical_terms": ["candy", "snack", "food", "bed"],
                        "phrases": ["candy snack food before bed"],
                        "conceptual_neighbors": [],
                        "relation_hints": [],
                        "temporal_hints": [],
                        "entity_hints": [],
                    },
                    "limitations": ["model-generated extraction", "additive only", "not authoritative"],
                }
            )
            result = run_thread_turn(
                repo_root=repo_root,
                data_root=Path(data_dir),
                user_input="Please retrieve the candy snack food before bed note and check the unseen phrase.",
                llm_backend=StubLLMBackend(prefix="Probe stub response"),
                semantic_extractor_backend=parsed_backend,
            )

            coverage_report = _load_turn_artifact(result.coverage_report_path)

            self.assertEqual(result.runtime_outcome, "blocked")
            self.assertEqual(coverage_report["decision"], "blocked")
            self.assertFalse(coverage_report["semantic_target_coverage"]["covered"])
            self.assertIn("unseen phrase alpha", coverage_report["semantic_target_coverage"]["missing_must_preserve"])
            self.assertIn("semantic coverage target missing required evidence: unseen phrase alpha", coverage_report["blocking_reasons"])
            self.assertIsNone(result.synthesis_context_packet["approved_retrieval_packet"])
            self.assertEqual(result.llm_metadata["mode"], "not_called")

    def test_semantic_target_coverage_blocks_when_avoid_target_is_present(self) -> None:
        with tempfile.TemporaryDirectory() as repo_dir, tempfile.TemporaryDirectory() as data_dir:
            repo_root = _prepared_repo_root(repo_dir)
            _write_markdown_fixture(
                repo_root / "corpus",
                "coverage/avoid_fixture.md",
                """
                ---
                note_type: coverage_fixture
                ---

                # Avoid Fixture

                ## Fixture Coverage Section

                This paragraph contains candy snack food before bed and dream recall together so the avoided phrase is present in the retrieved evidence.
                """,
            )
            _write_graph_fixture_notes(repo_root)
            run_ingest(
                repo_root=repo_root,
                data_root=Path(data_dir),
                source_roots=(IngestSourceRoot(label="corpus", path=repo_root / "corpus"),),
            )

            parsed_backend = _ParsedSemanticExtractorBackend(
                contextual_payload={
                    "raw_user_input": "Please retrieve the candy snack food before bed note and dream recall.",
                    "contextual_user_intent": "avoid coverage",
                    "thread_relevant_context": [],
                    "semantic_pressure": None,
                    "perturbation_nodes": [{"id": "node:candy", "label": "candy", "kind": "lexical_term"}],
                    "contextual_salt_nodes": [],
                    "perturbation_semantic_graph": {"nodes": [], "edges": []},
                    "candidate_targets": ["candy", "dream"],
                    "candidate_relations": [],
                    "semantic_coverage_target": {
                        "must_preserve": ["candy snack food before bed"],
                        "should_include": [],
                        "avoid_satisfying_with": ["dream recall"],
                        "query_text": "Please retrieve the candy snack food before bed note and dream recall.",
                        "allow_no_retrieval_needed": False,
                    },
                    "activation_hints": {
                        "lexical_terms": ["candy", "snack", "food", "bed", "dream", "recall"],
                        "phrases": ["candy snack food before bed", "dream recall"],
                        "conceptual_neighbors": [],
                        "relation_hints": [],
                        "temporal_hints": [],
                        "entity_hints": [],
                    },
                    "limitations": ["model-generated extraction", "additive only", "not authoritative"],
                }
            )
            result = run_thread_turn(
                repo_root=repo_root,
                data_root=Path(data_dir),
                user_input="Please retrieve the candy snack food before bed note and dream recall.",
                llm_backend=StubLLMBackend(prefix="Probe stub response"),
                semantic_extractor_backend=parsed_backend,
            )

            coverage_report = _load_turn_artifact(result.coverage_report_path)

            self.assertEqual(result.runtime_outcome, "blocked")
            self.assertEqual(coverage_report["decision"], "blocked")
            self.assertIn("dream recall", coverage_report["semantic_target_coverage"]["present_avoid_satisfying_with"])
            self.assertIn("semantic coverage target matched avoided evidence: dream recall", coverage_report["blocking_reasons"])
            self.assertEqual(result.llm_metadata["mode"], "not_called")

    def test_semantic_target_coverage_reports_metadata_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as repo_dir, tempfile.TemporaryDirectory() as data_dir:
            repo_root = _prepared_repo_root(repo_dir)
            _write_markdown_fixture(
                repo_root / "corpus",
                "coverage/metadata_fixture.md",
                """
                ---
                note_type: coverage_fixture
                ---

                # Metadata Fixture

                ## Fixture Corpus Beta

                This paragraph exists so deterministic coverage can match the section label as retrieved evidence.
                """,
            )
            _write_graph_fixture_notes(repo_root)
            run_ingest(
                repo_root=repo_root,
                data_root=Path(data_dir),
                source_roots=(IngestSourceRoot(label="corpus", path=repo_root / "corpus"),),
            )

            parsed_backend = _ParsedSemanticExtractorBackend(
                contextual_payload={
                    "raw_user_input": "Please retrieve the fixture corpus beta note.",
                    "contextual_user_intent": "metadata evidence",
                    "thread_relevant_context": [],
                    "semantic_pressure": None,
                    "perturbation_nodes": [{"id": "node:fixture", "label": "fixture", "kind": "lexical_term"}],
                    "contextual_salt_nodes": [],
                    "perturbation_semantic_graph": {"nodes": [], "edges": []},
                    "candidate_targets": ["fixture", "beta"],
                    "candidate_relations": [],
                    "semantic_coverage_target": {
                        "must_preserve": ["Fixture Corpus Beta"],
                        "should_include": ["midnight orchard"],
                        "avoid_satisfying_with": [],
                        "query_text": "Please retrieve the fixture corpus beta note.",
                        "allow_no_retrieval_needed": False,
                    },
                    "activation_hints": {
                        "lexical_terms": ["fixture", "corpus", "beta"],
                        "phrases": ["Fixture Corpus Beta"],
                        "conceptual_neighbors": [],
                        "relation_hints": [],
                        "temporal_hints": [],
                        "entity_hints": [],
                    },
                    "limitations": ["model-generated extraction", "additive only", "not authoritative"],
                }
            )
            result = run_thread_turn(
                repo_root=repo_root,
                data_root=Path(data_dir),
                user_input="Please retrieve the fixture corpus beta note.",
                llm_backend=StubLLMBackend(prefix="Probe stub response"),
                semantic_extractor_backend=parsed_backend,
            )

            coverage_target = result.coverage_report["semantic_target_coverage"]

            self.assertEqual(result.runtime_outcome, "completed")
            self.assertEqual(result.coverage_report["decision"], "approved")
            self.assertTrue(coverage_target["must_preserve"][0]["covered"])
            self.assertEqual(coverage_target["must_preserve"][0]["evidence"][0]["field"], "section_label")
            self.assertIn("midnight orchard", coverage_target["missing_should_include"])

    def test_semantic_target_coverage_returns_false_for_empty_retrieval(self) -> None:
        report = _evaluate_semantic_target_coverage(
            semantic_context_packet={
                "semantic_coverage_target": {
                    "must_preserve": ["candy snack food before bed"],
                    "should_include": ["dream"],
                    "avoid_satisfying_with": ["avoid this"],
                    "query_text": "candy snack food before bed",
                    "allow_no_retrieval_needed": False,
                }
            },
            semantic_traversal_manifest={},
            retrieval_packet={"selected_chunks": []},
        )
        self.assertFalse(report["covered"])
        self.assertEqual(report["missing_must_preserve"], ["candy snack food before bed"])
        self.assertEqual(report["missing_should_include"], ["dream"])

    def test_semantic_target_coverage_does_not_count_runtime_annotations_as_evidence(self) -> None:
        report = _evaluate_semantic_target_coverage(
            semantic_context_packet={
                "semantic_coverage_target": {
                    "must_preserve": ["unseen phrase alpha"],
                    "should_include": [],
                    "avoid_satisfying_with": [],
                    "query_text": "unseen phrase alpha",
                    "allow_no_retrieval_needed": False,
                }
            },
            semantic_traversal_manifest={},
            retrieval_packet={
                "selected_chunks": [
                    {
                        "chunk_id": "chunk-1",
                        "paragraph_text": "This chunk does not contain the required phrase.",
                        "note_title": "Fixture",
                        "section_label": "Section",
                        "relative_path": "fixture.md",
                        "note_path": "fixture.md",
                        "section_path": [],
                        "frontmatter": {},
                        "selection_reasons": ["runtime annotation says unseen phrase alpha"],
                        "matched_terms": ["unseen", "phrase", "alpha"],
                        "surface_contributions": ["lexical_index_surface"],
                    }
                ]
            },
        )
        self.assertFalse(report["covered"])
        self.assertIn("unseen phrase alpha", report["missing_must_preserve"])
        self.assertFalse(report["must_preserve"][0]["covered"])

    def test_semantic_target_coverage_does_not_count_query_text_as_evidence(self) -> None:
        report = _evaluate_semantic_target_coverage(
            semantic_context_packet={
                "semantic_coverage_target": {
                    "must_preserve": ["query text only target"],
                    "should_include": [],
                    "avoid_satisfying_with": [],
                    "query_text": "query text only target",
                    "allow_no_retrieval_needed": False,
                }
            },
            semantic_traversal_manifest={},
            retrieval_packet={
                "selected_chunks": [
                    {
                        "chunk_id": "chunk-1",
                        "paragraph_text": "This chunk contains unrelated retrieved evidence.",
                        "note_title": "Fixture",
                        "section_label": "Section",
                        "relative_path": "fixture.md",
                        "note_path": "fixture.md",
                        "section_path": [],
                        "frontmatter": {},
                    }
                ]
            },
        )
        self.assertFalse(report["covered"])
        self.assertIn("query text only target", report["missing_must_preserve"])

    def test_semantic_target_coverage_rejects_invalid_target_shape(self) -> None:
        invalid_targets = [
            {
                "semantic_coverage_target": {
                    "should_include": [],
                    "avoid_satisfying_with": [],
                    "query_text": "alpha",
                    "allow_no_retrieval_needed": False,
                }
            },
            {
                "semantic_coverage_target": {
                    "must_preserve": "alpha",
                    "should_include": [],
                    "avoid_satisfying_with": [],
                    "query_text": "alpha",
                    "allow_no_retrieval_needed": False,
                }
            },
            {
                "semantic_coverage_target": {
                    "must_preserve": [],
                    "should_include": [],
                    "avoid_satisfying_with": [],
                    "query_text": 123,
                    "allow_no_retrieval_needed": False,
                }
            },
            {
                "semantic_coverage_target": {
                    "must_preserve": [],
                    "should_include": [],
                    "avoid_satisfying_with": [],
                    "query_text": "alpha",
                    "allow_no_retrieval_needed": "no",
                }
            },
        ]
        for semantic_context_packet in invalid_targets:
            with self.subTest(semantic_context_packet=semantic_context_packet):
                report = _evaluate_semantic_target_coverage(
                    semantic_context_packet=semantic_context_packet,
                    semantic_traversal_manifest={},
                    retrieval_packet={"selected_chunks": []},
                )
                self.assertFalse(report["target_valid"])
                self.assertFalse(report["covered"])

    def test_retrieval_coverage_blocks_when_selected_chunk_ids_are_truncated(self) -> None:
        inputs = _minimal_retrieval_coverage_inputs(
            selected_chunk_ids=["chunk-1", "chunk-2", "chunk-3"],
            retrieved_chunk_ids=["chunk-1", "chunk-2"],
        )
        report = _evaluate_retrieval_coverage(**inputs)
        self.assertEqual(report["decision"], "blocked")
        self.assertIn(
            "retrieval_packet selected chunks do not exactly match traversal selected IDs",
            report["blocking_reasons"],
        )

    def test_retrieval_coverage_blocks_when_selected_chunk_ids_have_extras(self) -> None:
        inputs = _minimal_retrieval_coverage_inputs(
            selected_chunk_ids=["chunk-1", "chunk-2"],
            retrieved_chunk_ids=["chunk-1", "chunk-2", "chunk-3"],
        )
        report = _evaluate_retrieval_coverage(**inputs)
        self.assertEqual(report["decision"], "blocked")
        self.assertIn(
            "retrieval_packet selected chunks do not exactly match traversal selected IDs",
            report["blocking_reasons"],
        )

    def test_retrieval_coverage_blocks_when_selected_chunk_ids_are_reordered(self) -> None:
        inputs = _minimal_retrieval_coverage_inputs(
            selected_chunk_ids=["chunk-1", "chunk-2"],
            retrieved_chunk_ids=["chunk-2", "chunk-1"],
        )
        report = _evaluate_retrieval_coverage(**inputs)
        self.assertEqual(report["decision"], "blocked")
        self.assertIn(
            "retrieval_packet selected chunks do not exactly match traversal selected IDs",
            report["blocking_reasons"],
        )

    def test_retrieval_coverage_exact_match_does_not_emit_id_mismatch_reason(self) -> None:
        inputs = _minimal_retrieval_coverage_inputs(
            selected_chunk_ids=["chunk-1", "chunk-2"],
            retrieved_chunk_ids=["chunk-1", "chunk-2"],
        )
        report = _evaluate_retrieval_coverage(**inputs)
        self.assertNotIn(
            "retrieval_packet selected chunks do not exactly match traversal selected IDs",
            report["blocking_reasons"],
        )

    def test_runtime_blocks_when_semantic_coverage_target_shape_is_invalid(self) -> None:
        with tempfile.TemporaryDirectory() as repo_dir, tempfile.TemporaryDirectory() as data_dir:
            repo_root = _prepared_repo_root(repo_dir)
            _write_markdown_fixture(
                repo_root / "corpus",
                "coverage/invalid_target_fixture.md",
                """
                ---
                note_type: coverage_fixture
                ---

                # Invalid Target Fixture

                ## Fixture Section

                This paragraph exists so the runtime has selected retrieval evidence.
                """,
            )
            run_ingest(
                repo_root=repo_root,
                data_root=Path(data_dir),
                source_roots=(IngestSourceRoot(label="corpus", path=repo_root / "corpus"),),
            )

            parsed_backend = _ParsedSemanticExtractorBackend(
                contextual_payload={
                    "raw_user_input": "Please retrieve the invalid target fixture.",
                    "contextual_user_intent": "invalid target",
                    "thread_relevant_context": [],
                    "semantic_pressure": None,
                    "perturbation_nodes": [{"id": "node:invalid", "label": "invalid", "kind": "lexical_term"}],
                    "contextual_salt_nodes": [],
                    "perturbation_semantic_graph": {"nodes": [], "edges": []},
                    "candidate_targets": ["invalid"],
                    "candidate_relations": [],
                    "semantic_coverage_target": {
                        "must_preserve": ["invalid target fixture"],
                        "should_include": [],
                        "avoid_satisfying_with": [],
                    },
                    "activation_hints": {
                        "lexical_terms": ["invalid", "target", "fixture"],
                        "phrases": ["invalid target fixture"],
                        "conceptual_neighbors": [],
                        "relation_hints": [],
                        "temporal_hints": [],
                        "entity_hints": [],
                    },
                    "limitations": ["model-generated extraction", "additive only", "not authoritative"],
                }
            )
            result = run_thread_turn(
                repo_root=repo_root,
                data_root=Path(data_dir),
                user_input="Please retrieve the invalid target fixture.",
                llm_backend=StubLLMBackend(prefix="Probe stub response"),
                semantic_extractor_backend=parsed_backend,
            )

            self.assertEqual(result.runtime_outcome, "blocked")
            self.assertEqual(result.coverage_report["decision"], "blocked")
            self.assertFalse(result.coverage_report["semantic_target_coverage"]["target_valid"])
            self.assertIn("semantic_coverage_target missing or invalid", result.coverage_report["blocking_reasons"])
            self.assertEqual(result.llm_metadata["mode"], "not_called")

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
            self.assertEqual(backend.mode_name, "ollama")

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

