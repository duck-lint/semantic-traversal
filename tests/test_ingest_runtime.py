from __future__ import annotations

import gc
import shutil
import sqlite3
import tempfile
import time
import warnings
import unittest
from contextlib import closing
from pathlib import Path
from typing import Any

from semantic_traversal.cli import build_ingest_parser, build_turn_parser
from semantic_traversal.config import load_runtime_config
from semantic_traversal.embeddings import EmbeddingResponse
from semantic_traversal.hashing import sha256_json
from semantic_traversal.ingest import IngestFrontmatterError, IngestSourceRoot, run_ingest
from semantic_traversal.llm import LLMResponse
from semantic_traversal.runtime import (
    _apply_retrieval_candidate_hygiene,
    _candidate_source_layers,
    _count_selected_by_layer,
    _merge_candidates,
    _select_retrieval_chunks,
    run_thread_turn,
)
from semantic_traversal.retrieval_plan import build_default_retrieval_plan, scope_requests_from_text
from semantic_traversal.retrieval_plan import _focus_carry_terms
from semantic_traversal.semantic_compiler import SemanticCompilerResponse, collect_compiler_terms
from semantic_traversal.storage import load_json, read_ledger


REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_ROOT = REPO_ROOT / "tests" / "fixtures" / "JOURNAL"
TEMP_DATA_ROOTS: list[Path] = []


def _register_temp_data_root() -> Path:
    temp_root = Path(tempfile.mkdtemp())
    TEMP_DATA_ROOTS.append(temp_root)
    return temp_root


def _cleanup_temp_dir(path: Path) -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", ResourceWarning)
        gc.collect()
    for attempt in range(12):
        try:
            shutil.rmtree(path)
            return
        except PermissionError:
            if attempt == 11:
                raise
            time.sleep(0.1)


def tearDownModule() -> None:  # noqa: N802
    while TEMP_DATA_ROOTS:
        path = TEMP_DATA_ROOTS.pop()
        if path.exists():
            _cleanup_temp_dir(path)


class TestLLMBackend:
    mode_name = "test"

    def __init__(self, prefix: str = "Test assistant response") -> None:
        self._prefix = prefix

    def generate(self, synthesis_context_packet: dict[str, Any]) -> LLMResponse:
        user_input = synthesis_context_packet["raw_user_input"]
        turn_id = synthesis_context_packet["turn_id"]
        return LLMResponse(
            assistant_response=f"{self._prefix} for turn {turn_id}: {user_input}",
            metadata={"mode": self.mode_name, "provider": "test", "model": "test"},
        )


class TestSemanticCompilerBackend:
    mode_name = "test"

    def __init__(self) -> None:
        self._planner_defaults = load_runtime_config(repo_root=REPO_ROOT).retrieval_planner_defaults

    def compile_turn(self, packet: dict[str, Any]) -> SemanticCompilerResponse:
        raw_user_input = str(packet.get("raw_user_input") or "")
        query = raw_user_input.strip()
        terms = collect_compiler_terms(raw_user_input)
        planner_retrieval_plan = build_default_retrieval_plan(
            raw_user_input=raw_user_input,
            query=query,
            concepts=terms,
            scope_requests=scope_requests_from_text(raw_user_input),
            graph_seeds=[query] if query else [],
            resolved_referents=[],
            planner_defaults=self._planner_defaults,
        )
        return SemanticCompilerResponse(
            parsed_payload={
                "raw_user_input": raw_user_input,
                "intent": "test semantic compiler output",
                "query": query,
                "entities": [],
                "relations": [],
                "resolved_referents": [],
                "planner_retrieval_plan": planner_retrieval_plan,
                "limitations": ["test compiler backend used"],
            },
            raw_response=None,
            metadata={"backend_mode": self.mode_name},
            diagnostics={},
            status="parsed",
        )


class FakeEmbeddingBackend:
    mode_name = "fake"

    def embed_texts(self, texts: list[str]) -> EmbeddingResponse:
        vectors = [self._vector(text) for text in texts]
        return EmbeddingResponse(vectors=vectors, metadata={"backend_mode": self.mode_name}, status="embedded")

    def embed_query_text(self, text: str) -> EmbeddingResponse:
        return self.embed_texts([text])

    def _vector(self, text: str) -> list[float]:
        lowered = text.lower()
        return [
            float(len(lowered)),
            float(lowered.count("candy")),
            float(lowered.count("dream")),
            float(lowered.count("bed")),
        ]


class UnavailableEmbeddingBackend:
    mode_name = "unavailable"

    def embed_texts(self, texts: list[str]) -> EmbeddingResponse:
        return EmbeddingResponse(vectors=None, metadata={"backend_mode": self.mode_name}, status="unavailable")

    def embed_query_text(self, text: str) -> EmbeddingResponse:
        return self.embed_texts([text])


class RecordingLLMBackend:
    mode_name = "recording"

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def generate(self, synthesis_context_packet: dict[str, Any]):
        self.calls.append(synthesis_context_packet)
        return TestLLMBackend(prefix="Recorded assistant response").generate(synthesis_context_packet)


class ExplodingCompilerBackend:
    mode_name = "exploding"

    def compile_turn(self, packet: dict[str, Any]) -> SemanticCompilerResponse:
        raise RuntimeError("compiler backend exploded")


class ResponseCompilerBackend:
    mode_name = "response"

    def __init__(self, payload: dict[str, Any], raw_response: str) -> None:
        self.payload = payload
        self.raw_response = raw_response

    def compile_turn(self, packet: dict[str, Any]) -> SemanticCompilerResponse:
        return SemanticCompilerResponse(
            parsed_payload=self.payload,
            raw_response=self.raw_response,
            metadata={"backend_mode": self.mode_name},
            diagnostics={"source": "fixture"},
            status="parsed",
        )


class SequenceCompilerBackend:
    mode_name = "sequence"

    def __init__(self, payloads: list[dict[str, Any]]) -> None:
        self.payloads = payloads
        self.calls: list[dict[str, Any]] = []

    def compile_turn(self, packet: dict[str, Any]) -> SemanticCompilerResponse:
        self.calls.append(packet)
        payload = self.payloads[min(len(self.calls) - 1, len(self.payloads) - 1)]
        return SemanticCompilerResponse(
            parsed_payload=payload,
            raw_response="sequence raw response",
            metadata={"backend_mode": self.mode_name},
            diagnostics={},
            status="parsed",
        )


class RecordingCompilerBackend:
    mode_name = "recording"

    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.calls: list[dict[str, Any]] = []

    def compile_turn(self, packet: dict[str, Any]) -> SemanticCompilerResponse:
        self.calls.append(packet)
        return SemanticCompilerResponse(
            parsed_payload=self.payload,
            raw_response="recorded raw compiler response",
            metadata={"backend_mode": self.mode_name},
            diagnostics={"source": "recording"},
            status="parsed",
        )


def _prepare_data_root() -> Path:
    data_root = _register_temp_data_root()
    run_ingest(
        repo_root=REPO_ROOT,
        data_root=data_root,
        source_roots=(IngestSourceRoot(label="tests-fixtures", path=FIXTURE_ROOT),),
        embedding_backend=FakeEmbeddingBackend(),
    )
    return data_root


def _prepare_graph_fixture_data_root() -> Path:
    data_root = _register_temp_data_root()
    source_root = data_root / "graph-fixture"
    source_root.mkdir(parents=True, exist_ok=True)
    _write_markdown_note(
        source_root,
        "A.md",
        """
        # A

        Links to [[B]].

        Links to [[C|see alias]].

        Links to [[B#Sleep Section|sleep alias]].
        """,
        uuid_value="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
    )
    _write_markdown_note(
        source_root,
        "B.md",
        """
        # B

        ## Sleep Section

        B content paragraph.
        """,
        uuid_value="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
    )
    _write_markdown_note(
        source_root,
        "C.md",
        """
        # C

        C content paragraph.
        """,
        uuid_value="cccccccc-cccc-4ccc-8ccc-cccccccccccc",
    )
    config = load_runtime_config(repo_root=REPO_ROOT)
    run_ingest(
        repo_root=REPO_ROOT,
        data_root=data_root,
        source_roots=(IngestSourceRoot(label="graph-fixture", path=source_root),),
        embedding_backend=FakeEmbeddingBackend(),
        config=config,
    )
    return data_root


def _prepare_preferred_scope_graph_data_root() -> Path:
    data_root = _register_temp_data_root()
    source_root = data_root / "preferred-scope-graph-fixture"
    source_root.mkdir(parents=True, exist_ok=True)
    _write_markdown_note(
        source_root,
        "Journal.md",
        """
        # Journal

        The idea developed through an early geometry insight.

        Links to [[Concept]].
        """,
        uuid_value="11111111-1111-4111-8111-111111111111",
        frontmatter={"note_type": "journal_entry"},
    )
    _write_markdown_note(
        source_root,
        "Concept.md",
        """
        # Concept

        A conceptual precursor describes relational structure.
        """,
        uuid_value="22222222-2222-4222-8222-222222222222",
        frontmatter={"note_type": "concept"},
    )
    _write_markdown_note(
        source_root,
        "Reading.md",
        """
        # Reading

        A later reading reinforced the same structural analogy.
        """,
        uuid_value="33333333-3333-4333-8333-333333333333",
        frontmatter={"note_type": "reading_notes"},
    )
    run_ingest(
        repo_root=REPO_ROOT,
        data_root=data_root,
        source_roots=(IngestSourceRoot(label="scope-fixture", path=source_root),),
        embedding_backend=FakeEmbeddingBackend(),
    )
    return data_root


def _prepare_directed_chain_data_root(*, reverse_edge_rows: bool = False) -> Path:
    data_root = _register_temp_data_root()
    source_root = data_root / "directed-chain-fixture"
    source_root.mkdir(parents=True, exist_ok=True)
    _write_markdown_note(
        source_root,
        "Journal.md",
        "# Journal\n\nJournal evidence.\n\nLinks to [[Concept]].",
        uuid_value="44444444-4444-4444-8444-444444444444",
        frontmatter={"note_type": "journal_entry"},
    )
    _write_markdown_note(
        source_root,
        "Concept.md",
        "# Concept\n\nConcept evidence.\n\nLinks to [[Reading]].",
        uuid_value="55555555-5555-4555-8555-555555555555",
        frontmatter={"note_type": "concept"},
    )
    _write_markdown_note(
        source_root,
        "Reading.md",
        "# Reading\n\nReading evidence.",
        uuid_value="66666666-6666-4666-8666-666666666666",
        frontmatter={"note_type": "reading_notes"},
    )
    run_ingest(
        repo_root=REPO_ROOT,
        data_root=data_root,
        source_roots=(IngestSourceRoot(label="directed-chain", path=source_root),),
        embedding_backend=FakeEmbeddingBackend(),
    )
    if reverse_edge_rows:
        connection = sqlite3.connect(data_root / "ingestion" / "latent_space.sqlite3")
        config = load_runtime_config(repo_root=REPO_ROOT)
        rows = connection.execute(
            f"SELECT edge_id, source_node_id, target_node_id, edge_type, metadata_json, last_ingested_run_id, updated_at FROM {config.graph_edges_table} ORDER BY edge_id DESC"
        ).fetchall()
        connection.execute(f"DELETE FROM {config.graph_edges_table}")
        connection.executemany(
            f"INSERT INTO {config.graph_edges_table} (edge_id, source_node_id, target_node_id, edge_type, metadata_json, last_ingested_run_id, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        connection.commit()
        connection.close()
    return data_root


def _graph_direction_payload(seed: str, *, depth: int = 1) -> dict[str, Any]:
    return _graph_compiler_payload(seed, graph_seeds=[seed], graph_depth=depth)


def _turn_artifact(path: Path) -> dict[str, Any]:
    return load_json(path) or {}


def _write_markdown_note(
    root: Path,
    relative_path: str,
    body: str,
    *,
    uuid_value: str | None = None,
    frontmatter: dict[str, Any] | None = None,
) -> Path:
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["---"]
    if uuid_value is not None:
        lines.append(f"uuid: {uuid_value}")
    if frontmatter:
        for key, value in frontmatter.items():
            lines.append(f"{key}: {value}")
    lines.append("---")
    lines.append("")
    lines.append(body.strip())
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _graph_compiler_payload(
    raw_user_input: str,
    *,
    graph_seeds: list[str],
    graph_depth: int | None = 1,
    scope_requests: list[str] | None = None,
    intent_type: str = "semantic_traversal",
) -> dict[str, Any]:
    graph_layer = {"operator": "graph_expand", "required": False}
    if graph_depth is not None:
        graph_layer["depth"] = graph_depth
    return {
        "raw_user_input": raw_user_input,
        "intent": "fixture",
        "query": raw_user_input,
        "entities": [],
        "relations": [],
        "resolved_referents": [],
        "planner_retrieval_plan": {
            "intent_type": intent_type,
            "scope_requests": list(scope_requests or []),
            "concepts": [],
            "resolved_referents": [],
            "literal_terms": [],
            "semantic_queries": [raw_user_input] if raw_user_input else [],
            "lexical_queries": [],
            "graph_seeds": graph_seeds,
            "retrieval_layers": [
                {"operator": "lexical_chunk_search", "required": False, "limit": 50},
                {"operator": "vector_search", "required": False, "limit": 24},
                graph_layer,
            ],
            "selection_policy": {"max_chunks": 24, "preserve_required_layers": True, "budgets": {"exact": 12, "lexical": 6, "vector": 6, "graph": 4}},
            "claim_policy": {"coverage_claims_allowed": False, "negative_claims_require_exact_layer": True},
        },
        "limitations": [],
    }


def _controlled_layer_payload(
    raw_user_input: str,
    *,
    layers: list[dict[str, Any]],
    semantic_queries: list[str] | None = None,
    lexical_queries: list[str] | None = None,
    graph_seeds: list[str] | None = None,
    selection_policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "raw_user_input": raw_user_input,
        "intent": "controlled required-layer fixture",
        "query": raw_user_input,
        "entities": [],
        "relations": [],
        "resolved_referents": [],
        "planner_retrieval_plan": {
            "intent_type": "semantic_traversal",
            "scope_requests": [],
            "concepts": [],
            "resolved_referents": [],
            "literal_terms": [],
            "semantic_queries": list(semantic_queries or []),
            "lexical_queries": list(lexical_queries or []),
            "graph_seeds": list(graph_seeds or []),
            "retrieval_layers": layers,
            "selection_policy": selection_policy or {"max_chunks": 24, "preserve_required_layers": True, "budgets": {"exact": 12, "lexical": 6, "vector": 6, "graph": 4}},
            "claim_policy": {"coverage_claims_allowed": False, "negative_claims_require_exact_layer": True},
        },
        "limitations": [],
    }


def _prepare_multi_chunk_inventory_data_root() -> Path:
    data_root = _register_temp_data_root()
    source_root = data_root / "multi-chunk-fixture"
    source_root.mkdir(parents=True, exist_ok=True)
    _write_markdown_note(
        source_root,
        "Journal.md",
        """
        # Journal

        First paragraph about candy.

        Second paragraph about sleep.

        Third paragraph about bed.
        """,
        uuid_value="dddddddd-dddd-4ddd-8ddd-dddddddddddd",
        frontmatter={"note_type": "journal_entry"},
    )
    config = load_runtime_config(repo_root=REPO_ROOT)
    run_ingest(
        repo_root=REPO_ROOT,
        data_root=data_root,
        source_roots=(IngestSourceRoot(label="tests-fixtures", path=source_root),),
        embedding_backend=FakeEmbeddingBackend(),
    )
    return data_root


def _prepare_apparatus_graph_fixture_data_root() -> Path:
    data_root = _register_temp_data_root()
    source_root = data_root / "apparatus-graph-fixture"
    source_root.mkdir(parents=True, exist_ok=True)
    _write_markdown_note(
        source_root,
        "A.md",
        """
        # A

        Links to [[The_Parmenidean_Ascent]].
        """,
        uuid_value="eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee",
    )
    _write_markdown_note(
        source_root,
        "The_Parmenidean_Ascent.md",
        """
        # The Parmenidean Ascent

        The Parmenidean Ascent

        MICHAEL DELLA ROCCA

        OXFORD

        Oxford University Press.

        The Parmenidean Ascent. Michael Della Rocca, Oxford University Press (2020).

        First, take explanatory demands seriously and let them be your guide.

        In this longer discussion, Oxford University Press is mentioned as the publisher, but the argument itself concerns explanatory demands and metaphysical interpretation.
        """,
        uuid_value="ffffffff-ffff-4fff-8fff-ffffffffffff",
        frontmatter={"note_type": "reading_notes"},
    )
    config = load_runtime_config(repo_root=REPO_ROOT)
    run_ingest(
        repo_root=REPO_ROOT,
        data_root=data_root,
        source_roots=(IngestSourceRoot(label="apparatus-graph-fixture", path=source_root),),
        embedding_backend=FakeEmbeddingBackend(),
        config=config,
    )
    return data_root


class ThesisRuntimeTests(unittest.TestCase):
    def test_cli_runtime_control_is_config_only(self) -> None:
        ingest_options = {action.dest for action in build_ingest_parser()._actions}
        turn_options = {action.dest for action in build_turn_parser()._actions}
        for removed_option in ("source_root", "vault_root", "data_root", "model", "llm_mode"):
            self.assertNotIn(removed_option, ingest_options)
            self.assertNotIn(removed_option, turn_options)

    def test_runtime_config_does_not_reference_test_fixtures_as_vault_root(self) -> None:
        config = load_runtime_config(repo_root=REPO_ROOT)
        self.assertNotIn("tests/fixtures", config.vault_root.as_posix())

    def test_ingest_rejects_missing_or_invalid_uuid_and_writes_failure_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            data_root = Path(temp_dir)
            source_root = data_root / "source"
            _write_markdown_note(source_root, "Missing.md", "Missing uuid body.")
            _write_markdown_note(source_root, "Invalid.md", "Invalid uuid body.", uuid_value="not-a-uuid")

            with self.assertRaises(IngestFrontmatterError) as exc_info:
                run_ingest(
                    repo_root=REPO_ROOT,
                    data_root=data_root,
                    source_roots=(IngestSourceRoot(label="source", path=source_root),),
                    embedding_backend=FakeEmbeddingBackend(),
                )

            self.assertIn("failure manifest", str(exc_info.exception))
            manifest_path = exc_info.exception.manifest_path
            self.assertTrue(manifest_path.exists())
            manifest = _turn_artifact(manifest_path)
            self.assertEqual(manifest["status"], "failed")
            self.assertEqual(manifest["summary"]["validation_issue_count"], 2)
            offending_paths = {issue["relative_path"] for issue in manifest["validation_issues"]}
            self.assertEqual(offending_paths, {"Missing.md", "Invalid.md"})
            self.assertFalse((data_root / "ingestion" / "latent_space.sqlite3").exists())

    def test_ingest_rejects_duplicate_source_uuid_and_writes_failure_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            data_root = Path(temp_dir)
            source_root = data_root / "source"
            duplicate_uuid = "77777777-7777-4777-8777-777777777777"
            _write_markdown_note(source_root, "One.md", "# One\n\nFirst copy.", uuid_value=duplicate_uuid)
            _write_markdown_note(source_root, "Two.md", "# Two\n\nSecond copy.", uuid_value=duplicate_uuid)

            with self.assertRaises(IngestFrontmatterError) as exc_info:
                run_ingest(
                    repo_root=REPO_ROOT,
                    data_root=data_root,
                    source_roots=(IngestSourceRoot(label="source", path=source_root),),
                    embedding_backend=FakeEmbeddingBackend(),
                )

            manifest = _turn_artifact(exc_info.exception.manifest_path)
            self.assertEqual(manifest["status"], "failed")
            self.assertEqual(manifest["summary"]["validation_issue_count"], 2)
            offending_paths = {issue["relative_path"] for issue in manifest["validation_issues"]}
            self.assertEqual(offending_paths, {"One.md", "Two.md"})
            self.assertTrue(
                all("duplicate UUID" in issue["issue"] for issue in manifest["validation_issues"])
            )
            self.assertFalse((data_root / "ingestion" / "latent_space.sqlite3").exists())

    def test_ingest_skips_configured_excluded_markdown_before_uuid_validation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            data_root = Path(temp_dir)
            source_root = data_root / "source"
            source_root.mkdir(parents=True)
            _write_markdown_note(source_root, ".obsidian/Plugin.md", "Plugin support markdown without uuid.")
            _write_markdown_note(
                source_root,
                "Good.md",
                "# Good\n\nThis note should ingest.",
                uuid_value="66666666-6666-4666-8666-666666666666",
            )
            config = load_runtime_config(repo_root=REPO_ROOT)
            config.raw["paths"]["vault_exclude_globs"] = [".obsidian/**"]

            result = run_ingest(
                repo_root=REPO_ROOT,
                data_root=data_root,
                source_roots=(IngestSourceRoot(label="source", path=source_root),),
                embedding_backend=FakeEmbeddingBackend(),
                config=config,
            )

            manifest = _turn_artifact(result.manifest_path)
            self.assertEqual(manifest["summary"]["skipped_source_count"], 1)
            self.assertEqual(manifest["skipped_sources"][0]["relative_path"], ".obsidian/Plugin.md")
            self.assertEqual(manifest["skipped_sources"][0]["matched_exclude_glob"], ".obsidian/**")
            self.assertEqual(result.note_count, 1)

    def test_ingest_still_rejects_non_excluded_missing_uuid_when_exclusions_exist(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            data_root = Path(temp_dir)
            source_root = data_root / "source"
            source_root.mkdir(parents=True)
            _write_markdown_note(source_root, ".obsidian/Plugin.md", "Plugin support markdown without uuid.")
            _write_markdown_note(source_root, "Missing.md", "This non-excluded file must still fail.")
            config = load_runtime_config(repo_root=REPO_ROOT)
            config.raw["paths"]["vault_exclude_globs"] = [".obsidian/**"]

            with self.assertRaises(IngestFrontmatterError) as exc_info:
                run_ingest(
                    repo_root=REPO_ROOT,
                    data_root=data_root,
                    source_roots=(IngestSourceRoot(label="source", path=source_root),),
                    embedding_backend=FakeEmbeddingBackend(),
                    config=config,
                )

            manifest = _turn_artifact(exc_info.exception.manifest_path)
            self.assertEqual(manifest["summary"]["validation_issue_count"], 1)
            self.assertEqual(manifest["summary"]["skipped_source_count"], 1)
            self.assertEqual(manifest["validation_issues"][0]["relative_path"], "Missing.md")
            self.assertEqual(manifest["skipped_sources"][0]["relative_path"], ".obsidian/Plugin.md")

    def test_ingest_omits_standalone_classical_reference_lines(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            data_root = Path(temp_dir)
            source_root = data_root / "source"
            source_root.mkdir(parents=True)
            _write_markdown_note(
                source_root,
                "Theaetetus.md",
                """
                # Theaetetus

                This paragraph should survive ingestion.

                142a b

                143a b с

                146а b

                e

                12

                This inline 144e reference should remain because it is part of prose.
                """,
                uuid_value="11111111-1111-1111-1111-111111111111",
            )

            result = run_ingest(
                repo_root=REPO_ROOT,
                data_root=data_root,
                source_roots=(IngestSourceRoot(label="source", path=source_root),),
                embedding_backend=FakeEmbeddingBackend(),
            )

            with closing(sqlite3.connect(result.database_path)) as connection:
                rows = [
                    row[0]
                    for row in connection.execute("SELECT paragraph_text FROM chunks ORDER BY chunk_id").fetchall()
                ]

            self.assertEqual(result.chunk_count, 2)
            self.assertIn("This paragraph should survive ingestion.", rows)
            self.assertIn("This inline 144e reference should remain because it is part of prose.", rows)
            for omitted_text in ("142a b", "143a b с", "146а b", "e", "12"):
                self.assertNotIn(omitted_text, rows)

    def test_code_fence_table_and_list_are_atomic_units(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            data_root = Path(temp_dir)
            source_root = data_root / "source"
            source_root.mkdir(parents=True)
            _write_markdown_note(
                source_root,
                "Atomic.md",
                """
                # Atomic

                ```python
                line one

                line two
                ```

                | a | b |
                |---|---|
                | 1 | 2 |
                | 3 | 4 |

                - first
                - second
                  continuation
                - third
                """,
                uuid_value="22222222-2222-2222-2222-222222222222",
            )

            result = run_ingest(
                repo_root=REPO_ROOT,
                data_root=data_root,
                source_roots=(IngestSourceRoot(label="source", path=source_root),),
                embedding_backend=FakeEmbeddingBackend(),
            )

            with closing(sqlite3.connect(result.database_path)) as connection:
                connection.row_factory = sqlite3.Row
                rows = connection.execute(
                    "SELECT semantic_unit_kind, paragraph_text, chunking_warnings_json FROM chunks ORDER BY chunk_id"
                ).fetchall()

            self.assertEqual(result.chunk_count, 3)
            self.assertEqual([row["semantic_unit_kind"] for row in rows], ["code_block", "table", "list"])
            self.assertIn("line two", rows[0]["paragraph_text"])
            self.assertIn("| 3 | 4 |", rows[1]["paragraph_text"])
            self.assertIn("continuation", rows[2]["paragraph_text"])

    def test_oversized_prose_paragraph_splits_on_sentence_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            data_root = Path(temp_dir)
            source_root = data_root / "source"
            source_root.mkdir(parents=True)
            prefix = "A" * 1980
            paragraph = f"{prefix}. Second sentence stays after the split boundary and should be separate."
            _write_markdown_note(
                source_root,
                "Split.md",
                paragraph,
                uuid_value="33333333-3333-3333-3333-333333333333",
            )

            result = run_ingest(
                repo_root=REPO_ROOT,
                data_root=data_root,
                source_roots=(IngestSourceRoot(label="source", path=source_root),),
                embedding_backend=FakeEmbeddingBackend(),
            )

            with closing(sqlite3.connect(result.database_path)) as connection:
                connection.row_factory = sqlite3.Row
                rows = connection.execute(
                    "SELECT paragraph_ordinal, split_ordinal, paragraph_text, chunking_warnings_json FROM chunks ORDER BY chunk_id"
                ).fetchall()

            self.assertEqual(len(rows), 2)
            self.assertEqual([row["paragraph_ordinal"] for row in rows], [1, 1])
            self.assertEqual([row["split_ordinal"] for row in rows], [1, 2])
            self.assertTrue(rows[0]["paragraph_text"].endswith("."))
            self.assertIn("Second sentence", rows[1]["paragraph_text"])

    def test_embedding_text_changes_when_section_path_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            data_root = Path(temp_dir)
            source_root = data_root / "source"
            source_root.mkdir(parents=True)
            note_path = _write_markdown_note(
                source_root,
                "Context.md",
                """
                # Context

                ## First

                Same text.

                ## Second

                Same text.
                """,
                uuid_value="44444444-4444-4444-4444-444444444444",
            )

            result = run_ingest(
                repo_root=REPO_ROOT,
                data_root=data_root,
                source_roots=(IngestSourceRoot(label="source", path=source_root),),
                embedding_backend=FakeEmbeddingBackend(),
            )

            with closing(sqlite3.connect(result.database_path)) as connection:
                connection.row_factory = sqlite3.Row
                rows = connection.execute(
                    """
                    SELECT section_label, paragraph_text, chunk_hash, embedding_text
                    FROM chunks
                    WHERE note_path = ?
                    ORDER BY section_label
                    """,
                    (str(note_path),),
                ).fetchall()

            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["paragraph_text"], rows[1]["paragraph_text"])
            self.assertNotEqual(rows[0]["chunk_hash"], rows[1]["chunk_hash"])
            self.assertNotEqual(rows[0]["embedding_text"], rows[1]["embedding_text"])
            self.assertIn("First", rows[0]["embedding_text"])
            self.assertIn("Second", rows[1]["embedding_text"])

    def test_retrieval_display_text_stays_raw_semantic_unit_text(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            data_root = Path(temp_dir)
            source_root = data_root / "source"
            source_root.mkdir(parents=True)
            _write_markdown_note(
                source_root,
                "Display.md",
                """
                # Display

                ## Heading

                Plain retrieval text.
                """,
                uuid_value="55555555-5555-5555-5555-555555555555",
            )

            result = run_ingest(
                repo_root=REPO_ROOT,
                data_root=data_root,
                source_roots=(IngestSourceRoot(label="source", path=source_root),),
                embedding_backend=FakeEmbeddingBackend(),
            )

            with closing(sqlite3.connect(result.database_path)) as connection:
                connection.row_factory = sqlite3.Row
                row = connection.execute("SELECT paragraph_text, embedding_text FROM chunks LIMIT 1").fetchone()

            self.assertEqual(row["paragraph_text"], "Plain retrieval text.")
            self.assertIn("Heading", row["embedding_text"])
            self.assertIn("Plain retrieval text.", row["embedding_text"])

    def test_first_turn_creates_thread_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            result = run_thread_turn(
                repo_root=REPO_ROOT,
                data_root=Path(temp_dir),
                user_input="Hello from the barebones runtime.",
                llm_backend=TestLLMBackend(),
                semantic_compiler_backend=TestSemanticCompilerBackend(),
            )
            self.assertEqual(result.turn_id, 1)
            self.assertTrue(result.conversation_thread_path.exists())
            self.assertTrue(result.thread_state_path.exists())
            self.assertTrue(result.thread_ledger_path.exists())
            self.assertTrue(result.semantic_compiler_packet_path.exists())
            self.assertTrue(result.semantic_compiler_diagnostic_path.exists())
            self.assertTrue(result.semantic_traversal_manifest_path.exists())
            self.assertTrue(result.retrieval_packet_path.exists())
            self.assertTrue(result.coverage_report_path.exists())
            self.assertTrue(result.synthesis_context_packet_path.exists())
            self.assertTrue(result.state_delta_path.exists())
            ledger = read_ledger(result.thread_ledger_path)
            self.assertEqual(len(ledger), 1)

    def test_second_turn_loads_prior_thread_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            data_root = Path(temp_dir)
            first_turn = run_thread_turn(
                repo_root=REPO_ROOT,
                data_root=data_root,
                user_input="Please retrieve the candy snack food before bed note.",
                llm_backend=TestLLMBackend(prefix="First assistant"),
                semantic_compiler_backend=TestSemanticCompilerBackend(),
            )
            second_turn = run_thread_turn(
                repo_root=REPO_ROOT,
                data_root=data_root,
                user_input="And what about that one again?",
                llm_backend=TestLLMBackend(prefix="Second assistant"),
                thread_id=first_turn.thread_id,
                semantic_compiler_backend=TestSemanticCompilerBackend(),
            )
            self.assertEqual(second_turn.prior_thread_state["latest_turn_id"], 1)
            self.assertEqual(second_turn.next_thread_state["latest_turn_id"], 2)
            self.assertEqual(second_turn.prior_thread_state["latest_assistant_response"], first_turn.assistant_response)

    def test_first_turn_writes_active_focus_into_thread_state(self) -> None:
        data_root = _prepare_data_root()
        result = run_thread_turn(
            repo_root=REPO_ROOT,
            data_root=data_root,
            user_input="Please retrieve the candy snack food before bed note.",
            llm_backend=RecordingLLMBackend(),
            semantic_compiler_backend=TestSemanticCompilerBackend(),
            embedding_backend=FakeEmbeddingBackend(),
        )
        self.assertEqual(result.runtime_outcome, "completed")
        self.assertEqual(result.next_thread_state["latest_turn_id"], 1)
        self.assertEqual(result.next_thread_state["active_focus"]["query"], "Please retrieve the candy snack food before bed note.")
        self.assertTrue(result.next_thread_state["active_focus"]["concepts"])
        self.assertTrue(result.next_thread_state["active_focus"]["selected_chunk_ids"])

    def test_second_turn_loads_prior_active_focus(self) -> None:
        data_root = _prepare_data_root()
        first_turn = run_thread_turn(
            repo_root=REPO_ROOT,
            data_root=data_root,
            user_input="Please retrieve the candy snack food before bed note.",
            llm_backend=RecordingLLMBackend(),
            semantic_compiler_backend=TestSemanticCompilerBackend(),
            embedding_backend=FakeEmbeddingBackend(),
        )
        second_turn = run_thread_turn(
            repo_root=REPO_ROOT,
            data_root=data_root,
            user_input="I wonder if there's anything specific about how it makes me feel?",
            llm_backend=RecordingLLMBackend(),
            thread_id=first_turn.thread_id,
            semantic_compiler_backend=TestSemanticCompilerBackend(),
            embedding_backend=FakeEmbeddingBackend(),
        )
        self.assertEqual(second_turn.prior_thread_state["active_focus"]["query"], first_turn.next_thread_state["active_focus"]["query"])
        self.assertEqual(second_turn.prior_thread_state["active_focus"]["selected_note_titles"], first_turn.next_thread_state["active_focus"]["selected_note_titles"])

    def test_graph_traversal_disabled_is_skipped(self) -> None:
        data_root = _prepare_graph_fixture_data_root()
        config = load_runtime_config(repo_root=REPO_ROOT)
        config.raw["graph_traversal"]["enabled"] = False
        result = run_thread_turn(
            repo_root=REPO_ROOT,
            data_root=data_root,
            user_input="A",
            llm_backend=RecordingLLMBackend(),
            semantic_compiler_backend=RecordingCompilerBackend(_graph_compiler_payload("A", graph_seeds=["A"], graph_depth=0)),
            embedding_backend=UnavailableEmbeddingBackend(),
            config=config,
        )
        self.assertEqual(result.semantic_traversal_manifest["graph_traversal"]["enabled"], False)
        self.assertEqual(result.semantic_traversal_manifest["candidate_counts"]["graph"], 0)
        self.assertTrue(any("graph traversal disabled" in note for note in result.semantic_traversal_manifest["selection_notes"]))

    def test_graph_traversal_hop_limit_one_retrieves_directly_linked_note(self) -> None:
        data_root = _prepare_graph_fixture_data_root()
        config = load_runtime_config(repo_root=REPO_ROOT)
        result = run_thread_turn(
            repo_root=REPO_ROOT,
            data_root=data_root,
            user_input="A",
            llm_backend=RecordingLLMBackend(),
            semantic_compiler_backend=RecordingCompilerBackend(_graph_compiler_payload("A", graph_seeds=["A"], graph_depth=1)),
            embedding_backend=UnavailableEmbeddingBackend(),
            config=config,
        )
        selected_titles = [chunk["note_title"] for chunk in result.retrieval_packet["selected_chunks"]]
        self.assertIn("A", selected_titles)
        self.assertIn("B", selected_titles)
        self.assertIn("C", selected_titles)
        self.assertTrue(any("wikilink hop 1" in chunk["selection_reason"] for chunk in result.retrieval_packet["selected_chunks"]))

    def test_graph_traversal_hop_limit_zero_does_not_expand_linked_note(self) -> None:
        data_root = _prepare_graph_fixture_data_root()
        config = load_runtime_config(repo_root=REPO_ROOT)
        result = run_thread_turn(
            repo_root=REPO_ROOT,
            data_root=data_root,
            user_input="A",
            llm_backend=RecordingLLMBackend(),
            semantic_compiler_backend=RecordingCompilerBackend(_graph_compiler_payload("A", graph_seeds=["A"], graph_depth=0)),
            embedding_backend=UnavailableEmbeddingBackend(),
            config=config,
        )
        self.assertEqual(result.semantic_traversal_manifest["graph_traversal"]["expanded_note_count"], 0)
        selected_titles = [chunk["note_title"] for chunk in result.retrieval_packet["selected_chunks"]]
        self.assertIn("A", selected_titles)
        self.assertFalse(any("wikilink hop 1" in chunk["selection_reason"] for chunk in result.retrieval_packet["selected_chunks"]))

    def test_alias_wikilink_still_resolves_canonical_target_note(self) -> None:
        data_root = _prepare_graph_fixture_data_root()
        result = run_thread_turn(
            repo_root=REPO_ROOT,
            data_root=data_root,
            user_input="A",
            llm_backend=RecordingLLMBackend(),
            semantic_compiler_backend=RecordingCompilerBackend(_graph_compiler_payload("A", graph_seeds=["A"])),
            embedding_backend=UnavailableEmbeddingBackend(),
        )
        selected_titles = [chunk["note_title"] for chunk in result.retrieval_packet["selected_chunks"]]
        self.assertIn("C", selected_titles)
        self.assertTrue(any("wikilink hop" in chunk["selection_reason"] for chunk in result.retrieval_packet["selected_chunks"] if chunk["note_title"] == "C"))

    def test_edge_type_allowlist_controls_traversal(self) -> None:
        data_root = _prepare_graph_fixture_data_root()
        config = load_runtime_config(repo_root=REPO_ROOT)
        config.raw["graph_traversal"]["edge_type_allowlist"] = ["nonexistent_edge_type"]
        result = run_thread_turn(
            repo_root=REPO_ROOT,
            data_root=data_root,
            user_input="A",
            llm_backend=RecordingLLMBackend(),
            semantic_compiler_backend=RecordingCompilerBackend(_graph_compiler_payload("A", graph_seeds=["A"])),
            embedding_backend=UnavailableEmbeddingBackend(),
            config=config,
        )
        self.assertEqual(result.semantic_traversal_manifest["graph_traversal"]["edge_types_used"], [])
        self.assertEqual(result.semantic_traversal_manifest["graph_traversal"]["expanded_note_count"], 0)
        self.assertFalse(any("wikilink hop" in chunk["selection_reason"] for chunk in result.retrieval_packet["selected_chunks"]))

    def test_active_focus_cannot_supply_graph_seeds_on_referential_second_turn(self) -> None:
        data_root = _prepare_graph_fixture_data_root()
        first_turn = run_thread_turn(
            repo_root=REPO_ROOT,
            data_root=data_root,
            user_input="A",
            llm_backend=RecordingLLMBackend(),
            semantic_compiler_backend=RecordingCompilerBackend(_graph_compiler_payload("A", graph_seeds=["A"])),
            embedding_backend=UnavailableEmbeddingBackend(),
        )
        second_turn = run_thread_turn(
            repo_root=REPO_ROOT,
            data_root=data_root,
            user_input="What about it?",
            llm_backend=RecordingLLMBackend(),
            thread_id=first_turn.thread_id,
            semantic_compiler_backend=RecordingCompilerBackend(_graph_compiler_payload("What about it?", graph_seeds=[])),
            embedding_backend=UnavailableEmbeddingBackend(),
        )
        self.assertEqual(second_turn.semantic_traversal_manifest["graph_traversal"]["enabled"], True)
        self.assertNotIn("active_focus", second_turn.semantic_traversal_manifest["graph_traversal"]["seed_sources"])
        self.assertEqual(second_turn.semantic_traversal_manifest["graph_traversal"]["matched_seed_count"], 0)
        self.assertFalse(any("active_focus" in str(seed) for seed in second_turn.semantic_traversal_manifest["graph_traversal"]["submitted_seeds"]))

    def test_graph_traversal_notes_appear_in_manifest(self) -> None:
        data_root = _prepare_graph_fixture_data_root()
        result = run_thread_turn(
            repo_root=REPO_ROOT,
            data_root=data_root,
            user_input="A",
            llm_backend=RecordingLLMBackend(),
            semantic_compiler_backend=RecordingCompilerBackend(_graph_compiler_payload("A", graph_seeds=["A"])),
            embedding_backend=UnavailableEmbeddingBackend(),
        )
        self.assertIn("graph_traversal", result.semantic_traversal_manifest)
        self.assertTrue(result.semantic_traversal_manifest["graph_traversal"]["seed_sources"])
        self.assertTrue(any(note.startswith("graph traversal enabled=") or note == "graph traversal disabled" for note in result.semantic_traversal_manifest["selection_notes"]))

    def test_graph_seed_sources_keep_literal_labels(self) -> None:
        data_root = _prepare_graph_fixture_data_root()
        compiler_backend = ResponseCompilerBackend(
            payload={
                "raw_user_input": "A and B",
                "intent": "fixture response",
                "query": "A and B",
                "entities": [],
                "relations": [],
                "resolved_referents": [],
                "planner_retrieval_plan": {
                    "intent_type": "semantic_traversal",
                    "scope_requests": [],
                    "concepts": ["A", "B"],
                    "resolved_referents": [],
                    "literal_terms": [],
                    "semantic_queries": ["B"],
                    "lexical_queries": ["A", "B"],
                    "graph_seeds": ["A"],
                    "retrieval_layers": [
                        {"operator": "lexical_chunk_search", "required": False, "limit": 50},
                        {"operator": "vector_search", "required": False, "limit": 24},
                        {"operator": "graph_expand", "required": False, "depth": 1},
                    ],
                    "selection_policy": {"max_chunks": 24, "preserve_required_layers": True, "budgets": {"exact": 12, "lexical": 6, "vector": 6, "graph": 4}},
                    "claim_policy": {"coverage_claims_allowed": False, "negative_claims_require_exact_layer": True},
                },
                "limitations": [],
            },
            raw_response="raw response",
        )
        result = run_thread_turn(
            repo_root=REPO_ROOT,
            data_root=data_root,
            user_input="A and B",
            llm_backend=RecordingLLMBackend(),
            semantic_compiler_backend=compiler_backend,
            embedding_backend=UnavailableEmbeddingBackend(),
        )
        reasons_by_title = {chunk["note_title"]: chunk["selection_reason"] for chunk in result.retrieval_packet["selected_chunks"]}
        self.assertIn("graph_seeds graph seed matched note", reasons_by_title.get("A", ""))
        self.assertIn("semantic_queries graph seed matched note", reasons_by_title.get("B", ""))
        self.assertNotIn("graph_seeds graph seed matched note", reasons_by_title.get("B", ""))

    def test_referential_second_turn_augments_semantic_compiler_request_with_active_focus(self) -> None:
        data_root = _prepare_data_root()
        first_turn_payload = {
            "raw_user_input": "Please retrieve the candy snack food before bed note.",
            "intent": "fixture",
            "query": "candy snack food before bed",
            "entities": [],
            "relations": [],
            "resolved_referents": [],
            "planner_retrieval_plan": {
                "intent_type": "semantic_traversal",
                "scope_requests": [],
                "concepts": ["candy", "snack", "food", "bed"],
                "resolved_referents": [],
                "literal_terms": ["candy", "snack", "food", "bed"],
                "semantic_queries": ["candy snack food before bed"],
                "lexical_queries": ["candy", "snack", "food", "bed"],
                "graph_seeds": ["candy snack food before bed"],
                "retrieval_layers": [
                    {"operator": "lexical_chunk_search", "required": False, "limit": 50},
                    {"operator": "vector_search", "required": False, "limit": 24},
                    {"operator": "graph_expand", "required": False, "depth": 1},
                ],
                "selection_policy": {"max_chunks": 24, "preserve_required_layers": True, "budgets": {"exact": 12, "lexical": 6, "vector": 6, "graph": 4}},
                "claim_policy": {"coverage_claims_allowed": False, "negative_claims_require_exact_layer": True},
            },
            "limitations": [],
        }
        first_turn = run_thread_turn(
            repo_root=REPO_ROOT,
            data_root=data_root,
            user_input="Please retrieve the candy snack food before bed note.",
            llm_backend=RecordingLLMBackend(),
            semantic_compiler_backend=RecordingCompilerBackend(first_turn_payload),
            embedding_backend=FakeEmbeddingBackend(),
        )
        second_turn_payload = {
            "raw_user_input": "I wonder if there's anything specific about how it makes me feel?",
            "intent": "fixture follow-up",
            "query": "how does it make me feel",
            "entities": [],
            "relations": [],
            "resolved_referents": [],
            "planner_retrieval_plan": {
                "intent_type": "semantic_traversal",
                "scope_requests": [],
                "concepts": ["feel"],
                "resolved_referents": [],
                "literal_terms": [],
                "semantic_queries": ["how does it make me feel"],
                "lexical_queries": ["feel"],
                "graph_seeds": [],
                "retrieval_layers": [
                    {"operator": "lexical_chunk_search", "required": False, "limit": 50},
                    {"operator": "vector_search", "required": False, "limit": 24},
                    {"operator": "graph_expand", "required": False, "depth": 1},
                ],
                "selection_policy": {"max_chunks": 24, "preserve_required_layers": True, "budgets": {"exact": 12, "lexical": 6, "vector": 6, "graph": 4}},
                "claim_policy": {"coverage_claims_allowed": False, "negative_claims_require_exact_layer": True},
            },
            "limitations": [],
        }
        compiler_backend = RecordingCompilerBackend(second_turn_payload)
        second_turn = run_thread_turn(
            repo_root=REPO_ROOT,
            data_root=data_root,
            user_input="I wonder if there's anything specific about how it makes me feel?",
            llm_backend=RecordingLLMBackend(),
            thread_id=first_turn.thread_id,
            semantic_compiler_backend=compiler_backend,
            embedding_backend=FakeEmbeddingBackend(),
        )
        self.assertEqual(len(compiler_backend.calls), 1)
        request_packet = compiler_backend.calls[0]
        self.assertIn("active_focus", request_packet)
        self.assertTrue(request_packet["active_focus"]["concepts"])
        self.assertTrue(request_packet["recent_semantic_turns"])
        self.assertEqual(request_packet["active_focus"]["query"], first_turn.next_thread_state["active_focus"]["query"])
        self.assertEqual(second_turn.prior_thread_state["active_focus"]["query"], first_turn.next_thread_state["active_focus"]["query"])

    def test_deterministic_fallback_uses_active_focus_terms_on_referential_second_turn(self) -> None:
        data_root = _prepare_data_root()
        first_turn = run_thread_turn(
            repo_root=REPO_ROOT,
            data_root=data_root,
            user_input="Please retrieve the candy snack food before bed note.",
            llm_backend=RecordingLLMBackend(),
            semantic_compiler_backend=TestSemanticCompilerBackend(),
            embedding_backend=FakeEmbeddingBackend(),
        )
        second_turn = run_thread_turn(
            repo_root=REPO_ROOT,
            data_root=data_root,
            user_input="How does it make me feel?",
            llm_backend=RecordingLLMBackend(),
            thread_id=first_turn.thread_id,
            semantic_compiler_backend=ExplodingCompilerBackend(),
            embedding_backend=FakeEmbeddingBackend(),
        )
        self.assertEqual(second_turn.semantic_compiler_status, "fallback")
        planner_plan = second_turn.semantic_compiler_packet["planner_retrieval_plan"]
        self.assertIn("candy", planner_plan["concepts"])
        self.assertIn("bed", planner_plan["concepts"])
        self.assertTrue(any("candy snack food before bed" in query for query in planner_plan["semantic_queries"]))
        for junk_term in ("raw_user_input", "assistant_response_snippet", "selected_chunk_ids", "selected_note_titles", "{"):
            self.assertNotIn(junk_term, planner_plan["concepts"])

    def test_compiler_canonicalization_does_not_inject_prior_focus(self) -> None:
        data_root = _prepare_data_root()
        first_turn = run_thread_turn(
            repo_root=REPO_ROOT,
            data_root=data_root,
            user_input="Please retrieve the candy snack food before bed note.",
            llm_backend=RecordingLLMBackend(),
            semantic_compiler_backend=TestSemanticCompilerBackend(),
            embedding_backend=FakeEmbeddingBackend(),
        )
        compiler_backend = ResponseCompilerBackend(
            payload={
                "raw_user_input": "How does it make me feel?",
                "intent": "fixture response",
                "query": "how does it make me feel",
                "entities": [],
                "relations": [],
                "resolved_referents": [],
                "planner_retrieval_plan": {
                    "intent_type": "semantic_traversal",
                    "scope_requests": [],
                    "concepts": ["feelings"],
                    "resolved_referents": [],
                    "literal_terms": [],
                    "semantic_queries": ["how does it make me feel"],
                    "lexical_queries": ["feelings"],
                    "graph_seeds": ["how does it make me feel"],
                    "retrieval_layers": [
                        {"operator": "lexical_chunk_search", "required": False, "limit": 50},
                        {"operator": "vector_search", "required": False, "limit": 24},
                        {"operator": "graph_expand", "required": False, "depth": 1},
                    ],
                    "selection_policy": {"max_chunks": 24, "preserve_required_layers": True, "budgets": {"exact": 12, "lexical": 6, "vector": 6, "graph": 4}},
                    "claim_policy": {"coverage_claims_allowed": False, "negative_claims_require_exact_layer": True},
                },
                "limitations": [],
            },
            raw_response="raw response",
        )
        second_turn = run_thread_turn(
            repo_root=REPO_ROOT,
            data_root=data_root,
            user_input="How does it make me feel?",
            llm_backend=RecordingLLMBackend(),
            thread_id=first_turn.thread_id,
            semantic_compiler_backend=compiler_backend,
            embedding_backend=FakeEmbeddingBackend(),
        )
        resolved_referents = second_turn.semantic_compiler_packet["planner_retrieval_plan"]["resolved_referents"]
        self.assertEqual(resolved_referents, [])
        self.assertNotIn("candy", " ".join(second_turn.semantic_compiler_packet["planner_retrieval_plan"]["semantic_queries"]))

    def test_comparison_plan_uses_only_current_subjects(self) -> None:
        data_root = _prepare_data_root()
        first_turn = run_thread_turn(
            repo_root=REPO_ROOT,
            data_root=data_root,
            user_input="Please retrieve the Schopenhauer candy vision color note.",
            llm_backend=RecordingLLMBackend(),
            semantic_compiler_backend=TestSemanticCompilerBackend(),
            embedding_backend=FakeEmbeddingBackend(),
        )
        compiler_backend = ResponseCompilerBackend(
            payload={
                "raw_user_input": "How does della rocca and parmenidean ascent relate and contrast?",
                "intent": "fixture response",
                "query": "how does della rocca and parmenidean ascent relate and contrast",
                "entities": [],
                "relations": [],
                "resolved_referents": ["della rocca"],
                "planner_retrieval_plan": {
                    "intent_type": "semantic_traversal",
                    "scope_requests": [],
                    "concepts": ["della", "rocca", "parmenidean", "ascent", "relate", "contrast"],
                    "resolved_referents": ["della rocca"],
                    "literal_terms": ["della rocca", "relate", "contrast"],
                    "semantic_queries": ["how does della rocca and parmenidean ascent relate and contrast"],
                    "lexical_queries": ["della rocca", "relate", "contrast"],
                    "graph_seeds": ["how does della rocca and parmenidean ascent relate and contrast"],
                    "retrieval_layers": [
                        {"operator": "lexical_chunk_search", "required": False, "limit": 50},
                        {"operator": "vector_search", "required": False, "limit": 24},
                        {"operator": "graph_expand", "required": False, "depth": 1},
                    ],
                    "selection_policy": {"max_chunks": 24, "preserve_required_layers": True, "budgets": {"exact": 12, "lexical": 6, "vector": 6, "graph": 4}},
                    "claim_policy": {"coverage_claims_allowed": False, "negative_claims_require_exact_layer": True},
                },
                "limitations": [],
            },
            raw_response="raw response",
        )
        second_turn = run_thread_turn(
            repo_root=REPO_ROOT,
            data_root=data_root,
            user_input="How does della rocca and parmenidean ascent relate and contrast?",
            llm_backend=RecordingLLMBackend(),
            thread_id=first_turn.thread_id,
            semantic_compiler_backend=compiler_backend,
            embedding_backend=FakeEmbeddingBackend(),
        )
        planner_plan = second_turn.semantic_compiler_packet["planner_retrieval_plan"]
        joined_referents = " ".join(planner_plan["resolved_referents"]).lower()
        joined_semantic_queries = " ".join(planner_plan["semantic_queries"]).lower()
        self.assertNotIn("schopenhauer", joined_referents)
        self.assertNotIn("schopenhauer", joined_semantic_queries)
        self.assertLessEqual(len(planner_plan["resolved_referents"]), 12)
        self.assertNotIn("relate", [entry["term"] for entry in planner_plan["literal_terms"]])
        self.assertNotIn("contrast", [entry["term"] for entry in planner_plan["literal_terms"]])
        self.assertNotIn("relate", [query.lower() for query in planner_plan["lexical_queries"]])
        self.assertNotIn("contrast", [query.lower() for query in planner_plan["lexical_queries"]])

    def test_compact_focus_carry_terms_filters_noise_and_caps_terms(self) -> None:
        active_focus = {
            "query": "schopenhauer vision color vanity of existence",
            "entities": [],
            "relations": [],
            "concepts": ["schopenhauer", "vision", "color"],
            "scope_requests": [],
            "resolved_referents": ["schopenhauer", "vanity of existence"],
            "literal_terms": ["schopenhauer", "vision", "color", "vanity of existence"],
            "semantic_queries": ["schopenhauer vision color vanity of existence"],
            "lexical_queries": ["schopenhauer", "vision", "color", "vanity of existence"],
            "graph_seeds": ["schopenhauer vision color vanity of existence"],
            "retrieval_layers": [],
            "selection_policy": {},
            "claim_policy": {},
            "selected_chunk_ids": ["chunk-1", "chunk-2"],
            "selected_note_titles": ["2024-05-01", "assistant_response_snippet", "A Very Long Note Title That Should Not Be Carried Into Referential Context Because It Is Just Noise"],
            "selected_section_labels": ["page 12", "this", "that", "thinking", "2024-05-01"],
        }
        recent_semantic_turns = [
            {
                "turn_id": 1,
                "raw_user_input": "ignored raw input",
                "assistant_response_snippet": "ignored assistant snippet",
                "query": "schopenhauer vision color vanity of existence",
                "entities": [],
                "relations": [],
                "concepts": ["schopenhauer", "vision", "color"],
                "scope_requests": [],
                "resolved_referents": ["schopenhauer", "vanity of existence"],
                "literal_terms": ["schopenhauer", "vision", "color", "vanity of existence"],
                "semantic_queries": ["schopenhauer vision color vanity of existence"],
                "lexical_queries": ["schopenhauer", "vision", "color", "vanity of existence"],
                "graph_seeds": ["schopenhauer vision color vanity of existence"],
                "retrieval_layers": [],
                "selection_policy": {},
                "claim_policy": {},
                "selected_chunk_ids": ["chunk-9"],
                "selected_note_titles": ["selected_chunk_ids"],
                "selected_section_labels": ["2024-06-01", "page 9"],
            }
        ]
        carried = _focus_carry_terms(active_focus=active_focus, recent_semantic_turns=recent_semantic_turns)
        self.assertLessEqual(len(carried), 8)
        self.assertIn("schopenhauer", carried)
        self.assertIn("vision", carried)
        self.assertIn("color", carried)
        self.assertIn("vanity of existence", carried)
        self.assertNotIn("selected_chunk_ids", carried)
        self.assertNotIn("assistant_response_snippet", carried)
        self.assertNotIn("this", carried)
        self.assertNotIn("that", carried)
        self.assertNotIn("thinking", carried)
        self.assertFalse(any(term.startswith("2024-") for term in carried))

    def test_quoted_discourse_operator_terms_remain_searchable(self) -> None:
        data_root = _prepare_data_root()
        compiler_backend = ResponseCompilerBackend(
            payload={
                "raw_user_input": 'search journal for "contrast"',
                "intent": "fixture response",
                "query": 'search journal for "contrast"',
                "entities": [],
                "relations": [],
                "resolved_referents": [],
                "planner_retrieval_plan": {
                    "intent_type": "scoped_exact_search",
                    "scope_requests": ["journal"],
                    "concepts": ["contrast"],
                    "resolved_referents": [],
                    "literal_terms": ["contrast"],
                    "semantic_queries": ['search journal for "contrast"'],
                    "lexical_queries": ["contrast"],
                    "graph_seeds": [],
                    "retrieval_layers": [
                        {"operator": "exact_chunk_search", "required": True, "limit": 200, "return_total_count": True},
                        {"operator": "lexical_chunk_search", "required": False, "limit": 50},
                        {"operator": "vector_search", "required": False, "limit": 24},
                        {"operator": "graph_expand", "required": False, "depth": 1},
                    ],
                    "selection_policy": {"max_chunks": 24, "preserve_required_layers": True, "budgets": {"exact": 12, "lexical": 6, "vector": 6, "graph": 4}},
                    "claim_policy": {"coverage_claims_allowed": True, "negative_claims_require_exact_layer": True},
                },
                "limitations": [],
            },
            raw_response="raw response",
        )
        result = run_thread_turn(
            repo_root=REPO_ROOT,
            data_root=data_root,
            user_input='search journal for "contrast"',
            llm_backend=RecordingLLMBackend(),
            semantic_compiler_backend=compiler_backend,
            embedding_backend=FakeEmbeddingBackend(),
        )
        planner_plan = result.semantic_compiler_packet["planner_retrieval_plan"]
        self.assertIn("contrast", [entry["term"] for entry in planner_plan["literal_terms"]])
        self.assertIn("contrast", [query.lower() for query in planner_plan["lexical_queries"]])

    def test_recent_semantic_turns_are_capped_to_small_tail(self) -> None:
        data_root = _prepare_data_root()
        thread_id: str | None = None
        last_result = None
        for turn_number in range(1, 9):
            last_result = run_thread_turn(
                repo_root=REPO_ROOT,
                data_root=data_root,
                user_input=f"Please retrieve the candy snack food before bed note {turn_number}.",
                llm_backend=RecordingLLMBackend(),
                thread_id=thread_id,
                semantic_compiler_backend=TestSemanticCompilerBackend(),
                embedding_backend=FakeEmbeddingBackend(),
            )
            thread_id = last_result.thread_id
        self.assertIsNotNone(last_result)
        self.assertLessEqual(len(last_result.next_thread_state["recent_semantic_turns"]), 6)
        self.assertEqual(last_result.next_thread_state["recent_semantic_turns"][-1]["turn_id"], 8)

    def test_synthesis_packet_includes_improved_prior_thread_state(self) -> None:
        data_root = _prepare_data_root()
        first_turn = run_thread_turn(
            repo_root=REPO_ROOT,
            data_root=data_root,
            user_input="Please retrieve the candy snack food before bed note.",
            llm_backend=RecordingLLMBackend(),
            semantic_compiler_backend=TestSemanticCompilerBackend(),
            embedding_backend=FakeEmbeddingBackend(),
        )
        second_turn = run_thread_turn(
            repo_root=REPO_ROOT,
            data_root=data_root,
            user_input="How does it make me feel?",
            llm_backend=RecordingLLMBackend(),
            thread_id=first_turn.thread_id,
            semantic_compiler_backend=TestSemanticCompilerBackend(),
            embedding_backend=FakeEmbeddingBackend(),
        )
        synthesis_packet = _turn_artifact(second_turn.synthesis_context_packet_path)
        self.assertEqual(synthesis_packet["prior_thread_state"]["active_focus"]["query"], first_turn.next_thread_state["active_focus"]["query"])
        self.assertTrue(synthesis_packet["prior_thread_state"]["recent_semantic_turns"])
        self.assertNotIn("raw_response", synthesis_packet)
        self.assertIn("coverage-approved retrieval packet", synthesis_packet["output_requirements"][0])
        self.assertTrue(any("Do not describe retrieved notes" in requirement for requirement in synthesis_packet["output_requirements"]))

    def test_retrieval_selection_deduplicates_matching_chunk_hashes(self) -> None:
        candidates = [
            {
                "chunk_id": "chunk-a",
                "chunk_hash": "same-content",
                "selection_reason": "vector similarity 0.9",
                "sources": ["vector"],
            },
            {
                "chunk_id": "chunk-b",
                "chunk_hash": "same-content",
                "selection_reason": "vector similarity 0.8",
                "sources": ["vector"],
            },
            {
                "chunk_id": "chunk-c",
                "chunk_hash": "unique-content",
                "selection_reason": "vector similarity 0.7",
                "sources": ["vector"],
            },
        ]
        selected = _select_retrieval_chunks(merged_candidates=candidates, max_chunks=3)
        self.assertEqual([chunk["chunk_id"] for chunk in selected], ["chunk-a", "chunk-c"])

    def test_selected_multi_surface_provenance_survives_merge_and_selection(self) -> None:
        candidates = [
            {"chunk_id": "shared", "chunk_hash": "shared-hash", "selection_source": "lexical", "score": 3.0, "selection_reason": "lexical"},
            {"chunk_id": "shared", "chunk_hash": "shared-hash", "selection_source": "vector", "score": 2.0, "selection_reason": "vector"},
            {"chunk_id": "shared", "chunk_hash": "shared-hash", "selection_source": "graph", "score": 1.0, "selection_reason": "graph"},
        ]
        merged = _merge_candidates(candidates[:1], candidates[1:2], candidates[2:], config=load_runtime_config(repo_root=REPO_ROOT))
        selected = _select_retrieval_chunks(merged_candidates=merged, max_chunks=1)
        self.assertEqual(selected[0]["selection_source"], "lexical")
        self.assertEqual(selected[0]["source_layers"], ["lexical", "vector", "graph"])
        self.assertNotIn("sources", selected[0])
        self.assertEqual(_candidate_source_layers(selected[0]), ["lexical", "vector", "graph"])
        self.assertEqual(_count_selected_by_layer(selected), {"exact": 0, "lexical": 1, "vector": 1, "graph": 1})

    def test_source_layer_order_and_duplicates_are_deterministic(self) -> None:
        candidate = {"source_layers": ["graph", "vector", "graph", "lexical", "bogus", "exact"]}
        self.assertEqual(_candidate_source_layers(candidate), ["exact", "lexical", "vector", "graph"])

    def test_graph_materialization_is_round_robin_across_notes(self) -> None:
        data_root = _prepare_graph_fixture_data_root()
        result = run_thread_turn(
            repo_root=REPO_ROOT,
            data_root=data_root,
            user_input="A",
            llm_backend=RecordingLLMBackend(),
            semantic_compiler_backend=RecordingCompilerBackend(_graph_compiler_payload("A", graph_seeds=["A"], graph_depth=1)),
            embedding_backend=UnavailableEmbeddingBackend(),
        )
        graph_manifest = result.semantic_traversal_manifest["graph_traversal"]
        self.assertEqual(graph_manifest["candidate_note_ids"][:3], [
            "graph-fixture::uuid::aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            "graph-fixture::uuid::bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
            "graph-fixture::uuid::cccccccc-cccc-4ccc-8ccc-cccccccccccc",
        ])
        self.assertTrue(all(chunk["graph_provenance"] for chunk in result.retrieval_packet["selected_chunks"] if "graph" in chunk["source_layers"]))

    def test_graph_depth_is_runtime_canonicalized_and_hop_limit_is_not_authoritative(self) -> None:
        data_root = _prepare_graph_fixture_data_root()
        config = load_runtime_config(repo_root=REPO_ROOT)
        result = run_thread_turn(
            repo_root=REPO_ROOT,
            data_root=data_root,
            user_input="A",
            llm_backend=RecordingLLMBackend(),
            semantic_compiler_backend=RecordingCompilerBackend(_graph_compiler_payload("A", graph_seeds=["A"], graph_depth=1)),
            embedding_backend=UnavailableEmbeddingBackend(),
            config=config,
        )
        graph = result.semantic_traversal_manifest["graph_traversal"]
        self.assertEqual(graph["requested_depth"], 1)
        self.assertEqual(graph["effective_depth"], 1)
        self.assertEqual(graph["depth_adjustment"], "none")
        self.assertEqual(graph["expanded_note_count"], 2)

    def test_graph_depth_two_and_clamping_are_visible(self) -> None:
        data_root = _prepare_graph_fixture_data_root()
        for requested, expected, adjustment in ((2, 2, "none"), (99, 2, "clamped_to_max")):
            result = run_thread_turn(
                repo_root=REPO_ROOT,
                data_root=data_root,
                user_input="A",
                llm_backend=RecordingLLMBackend(),
                semantic_compiler_backend=RecordingCompilerBackend(_graph_compiler_payload("A", graph_seeds=["A"], graph_depth=requested)),
                embedding_backend=UnavailableEmbeddingBackend(),
            )
            graph = result.semantic_traversal_manifest["graph_traversal"]
            self.assertEqual(graph["requested_depth"], requested)
            self.assertEqual(graph["effective_depth"], expected)
            self.assertEqual(graph["depth_adjustment"], adjustment)
            self.assertTrue(any("graph_expand.depth" in str(item.get("field")) for item in result.semantic_traversal_manifest["resolver_adjustments"]))

    def test_graph_direction_combines_with_depth(self) -> None:
        for direction, expects_a in (("outbound", False), ("inbound", True), ("both", True)):
            data_root = _prepare_graph_fixture_data_root()
            config = load_runtime_config(repo_root=REPO_ROOT)
            config.raw["graph_traversal"]["direction"] = direction
            result = run_thread_turn(
                repo_root=REPO_ROOT,
                data_root=data_root,
                user_input="B",
                llm_backend=RecordingLLMBackend(),
                semantic_compiler_backend=RecordingCompilerBackend(_graph_compiler_payload("B", graph_seeds=["B"], graph_depth=1)),
                embedding_backend=UnavailableEmbeddingBackend(),
                config=config,
            )
            note_ids = result.semantic_traversal_manifest["graph_traversal"]["candidate_note_ids"]
            self.assertEqual(any("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa" in note_id for note_id in note_ids), expects_a)
            self.assertEqual(result.semantic_traversal_manifest["graph_traversal"]["direction"], direction)

    def test_directed_chain_outbound_inbound_and_both_are_true_unions(self) -> None:
        expected = {
            "Journal": "directed-chain::uuid::44444444-4444-4444-8444-444444444444",
            "Concept": "directed-chain::uuid::55555555-5555-4555-8555-555555555555",
            "Reading": "directed-chain::uuid::66666666-6666-4666-8666-666666666666",
        }
        for direction, expected_neighbors in (
            ("outbound", {expected["Reading"]}),
            ("inbound", {expected["Journal"]}),
            ("both", {expected["Journal"], expected["Reading"]}),
        ):
            data_root = _prepare_directed_chain_data_root()
            config = load_runtime_config(repo_root=REPO_ROOT)
            config.raw["graph_traversal"]["direction"] = direction
            result = run_thread_turn(
                repo_root=REPO_ROOT,
                data_root=data_root,
                user_input="Concept",
                llm_backend=RecordingLLMBackend(),
                semantic_compiler_backend=RecordingCompilerBackend(_graph_direction_payload("Concept")),
                embedding_backend=UnavailableEmbeddingBackend(),
                config=config,
            )
            graph = result.semantic_traversal_manifest["graph_traversal"]
            note_ids = set(graph["candidate_note_ids"])
            self.assertIn(expected["Concept"], note_ids)
            self.assertEqual(note_ids - {expected["Concept"]}, expected_neighbors)
            self.assertEqual(graph["direction"], direction)
            self.assertEqual(len(graph["candidate_unique_note_ids"]), len(note_ids))
            for chunk in result.retrieval_packet["selected_chunks"]:
                if "graph" in chunk["source_layers"]:
                    self.assertEqual(chunk["graph_direction"], direction)
                    if chunk["note_id"] != expected["Concept"]:
                        self.assertTrue(chunk["graph_hop_provenance"])

    def test_directed_chain_depth_two_reaches_only_licensed_end(self) -> None:
        for direction, seed, expected_notes in (
            ("outbound", "Journal", {"directed-chain::uuid::44444444-4444-4444-8444-444444444444", "directed-chain::uuid::55555555-5555-4555-8555-555555555555", "directed-chain::uuid::66666666-6666-4666-8666-666666666666"}),
            ("inbound", "Reading", {"directed-chain::uuid::66666666-6666-4666-8666-666666666666", "directed-chain::uuid::55555555-5555-4555-8555-555555555555", "directed-chain::uuid::44444444-4444-4444-8444-444444444444"}),
        ):
            data_root = _prepare_directed_chain_data_root()
            config = load_runtime_config(repo_root=REPO_ROOT)
            config.raw["graph_traversal"]["direction"] = direction
            result = run_thread_turn(
                repo_root=REPO_ROOT,
                data_root=data_root,
                user_input=seed,
                llm_backend=RecordingLLMBackend(),
                semantic_compiler_backend=RecordingCompilerBackend(_graph_direction_payload(seed, depth=2)),
                embedding_backend=UnavailableEmbeddingBackend(),
                config=config,
            )
            graph = result.semantic_traversal_manifest["graph_traversal"]
            self.assertEqual(set(graph["candidate_note_ids"]), expected_notes)
            self.assertEqual(graph["effective_depth"], 2)

    def test_graph_direction_is_deterministic_independent_of_edge_insertion_order(self) -> None:
        manifests = []
        packets = []
        for reverse_edge_rows in (False, True, False):
            data_root = _prepare_directed_chain_data_root(reverse_edge_rows=reverse_edge_rows)
            config = load_runtime_config(repo_root=REPO_ROOT)
            config.raw["graph_traversal"]["direction"] = "both"
            result = run_thread_turn(
                repo_root=REPO_ROOT,
                data_root=data_root,
                user_input="Concept",
                llm_backend=RecordingLLMBackend(),
                semantic_compiler_backend=RecordingCompilerBackend(_graph_direction_payload("Concept")),
                embedding_backend=UnavailableEmbeddingBackend(),
                config=config,
            )
            graph = result.semantic_traversal_manifest["graph_traversal"]
            manifests.append(graph)
            packets.append(result.retrieval_packet["selected_chunks"])
        self.assertEqual(manifests[0], manifests[1])
        self.assertEqual(manifests[1], manifests[2])
        self.assertEqual(packets[0], packets[1])
        self.assertEqual(packets[1], packets[2])

    def test_inbound_provenance_separates_stored_edge_from_traversal_step(self) -> None:
        data_root = _prepare_directed_chain_data_root()
        config = load_runtime_config(repo_root=REPO_ROOT)
        config.raw["graph_traversal"]["direction"] = "inbound"
        result = run_thread_turn(
            repo_root=REPO_ROOT,
            data_root=data_root,
            user_input="Concept",
            llm_backend=RecordingLLMBackend(),
            semantic_compiler_backend=RecordingCompilerBackend(_graph_direction_payload("Concept")),
            embedding_backend=UnavailableEmbeddingBackend(),
            config=config,
        )
        journal_id = "directed-chain::uuid::44444444-4444-4444-8444-444444444444"
        concept_id = "directed-chain::uuid::55555555-5555-4555-8555-555555555555"
        journal_chunks = [chunk for chunk in result.retrieval_packet["selected_chunks"] if chunk["note_id"] == journal_id]
        self.assertTrue(journal_chunks)
        hop = journal_chunks[0]["graph_hop_provenance"][0]
        self.assertEqual(hop["traversal_direction"], "inbound")
        self.assertEqual(hop["edge_source_note_id"], journal_id)
        self.assertEqual(hop["edge_target_note_id"], concept_id)
        self.assertEqual(hop["from_note_id"], concept_id)
        self.assertEqual(hop["to_note_id"], journal_id)

    def test_graph_cycles_reciprocal_links_and_self_links_remain_bounded(self) -> None:
        data_root = _prepare_directed_chain_data_root()
        db_path = data_root / "ingestion" / "latent_space.sqlite3"
        connection = sqlite3.connect(db_path)
        config = load_runtime_config(repo_root=REPO_ROOT)
        connection.executemany(
            f"INSERT INTO {config.graph_edges_table} (edge_id, source_node_id, target_node_id, edge_type, metadata_json, last_ingested_run_id, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                ("extra-reciprocal", "note::55555555-5555-4555-8555-555555555555", "note::44444444-4444-4444-8444-444444444444", "note_links_note", "{}", "test", "test"),
                ("extra-self", "note::55555555-5555-4555-8555-555555555555", "note::55555555-5555-4555-8555-555555555555", "note_links_note", "{}", "test", "test"),
            ],
        )
        connection.commit()
        connection.close()
        config.raw["graph_traversal"]["direction"] = "both"
        result = run_thread_turn(
            repo_root=REPO_ROOT,
            data_root=data_root,
            user_input="Concept",
            llm_backend=RecordingLLMBackend(),
            semantic_compiler_backend=RecordingCompilerBackend(_graph_direction_payload("Concept", depth=2)),
            embedding_backend=UnavailableEmbeddingBackend(),
            config=config,
        )
        graph = result.semantic_traversal_manifest["graph_traversal"]
        self.assertEqual(len(graph["candidate_unique_note_ids"]), len(set(graph["candidate_unique_note_ids"])))
        self.assertLessEqual(graph["expanded_note_count"], 2)
        self.assertTrue(all(len(chunk.get("graph_hop_provenance", [])) <= 8 for chunk in result.retrieval_packet["selected_chunks"] if "graph" in chunk["source_layers"]))

    def test_missing_graph_depth_uses_yaml_default_with_diagnostic(self) -> None:
        data_root = _prepare_graph_fixture_data_root()
        result = run_thread_turn(
            repo_root=REPO_ROOT,
            data_root=data_root,
            user_input="A",
            llm_backend=RecordingLLMBackend(),
            semantic_compiler_backend=RecordingCompilerBackend(_graph_compiler_payload("A", graph_seeds=["A"], graph_depth=None)),
            embedding_backend=UnavailableEmbeddingBackend(),
        )
        graph = result.semantic_traversal_manifest["graph_traversal"]
        self.assertIsNone(graph["requested_depth"])
        self.assertEqual(graph["effective_depth"], graph["default_depth"])
        self.assertEqual(graph["depth_adjustment"], "defaulted")

    def test_preferred_scope_keeps_nonjournal_graph_evidence_and_ranks_journal(self) -> None:
        data_root = _prepare_preferred_scope_graph_data_root()
        result = run_thread_turn(
            repo_root=REPO_ROOT,
            data_root=data_root,
            user_input="Journal",
            llm_backend=RecordingLLMBackend(),
            semantic_compiler_backend=RecordingCompilerBackend(
                _graph_compiler_payload(
                    "Journal",
                    graph_seeds=["Journal"],
                    scope_requests=["journal"],
                )
            ),
            embedding_backend=UnavailableEmbeddingBackend(),
        )
        bound = result.semantic_traversal_manifest["bound_retrieval_plan"]
        self.assertEqual(bound["scope_filters"]["note_type"], [])
        self.assertIn("journal_entry", bound["preferred_scope_filters"]["note_type"])
        self.assertIn("journal", bound["scope_resolution"]["preferred"])
        selected = result.retrieval_packet["selected_chunks"]
        self.assertTrue(any(chunk["note_title"] == "Journal" and chunk["preferred_scope_match"] for chunk in selected))
        self.assertTrue(any(chunk["note_title"] == "Concept" for chunk in selected))
        self.assertEqual(selected[0]["note_title"], "Journal")
        self.assertIn("preferred_scope_match", selected[0])
        self.assertIn("scope_resolution", result.semantic_traversal_manifest)

    def test_hard_scope_still_excludes_nonjournal_graph_candidates(self) -> None:
        data_root = _prepare_preferred_scope_graph_data_root()
        result = run_thread_turn(
            repo_root=REPO_ROOT,
            data_root=data_root,
            user_input="search Journal",
            llm_backend=RecordingLLMBackend(),
            semantic_compiler_backend=RecordingCompilerBackend(
                _graph_compiler_payload(
                    "Journal",
                    graph_seeds=["Journal"],
                    scope_requests=["journal"],
                    intent_type="scoped_exact_search",
                )
            ),
            embedding_backend=UnavailableEmbeddingBackend(),
        )
        bound = result.semantic_traversal_manifest["bound_retrieval_plan"]
        self.assertIn("journal_entry", bound["scope_filters"]["note_type"])
        self.assertEqual(bound["preferred_scope_filters"]["note_type"], [])
        self.assertTrue(all(chunk["note_title"] == "Journal" for chunk in result.retrieval_packet["selected_chunks"]))
        self.assertEqual(result.semantic_traversal_manifest["candidate_counts"]["graph"], 2)

    def test_preferred_scope_order_is_deterministic(self) -> None:
        orders = []
        for _ in range(2):
            data_root = _prepare_preferred_scope_graph_data_root()
            result = run_thread_turn(
                repo_root=REPO_ROOT,
                data_root=data_root,
                user_input="Journal",
                llm_backend=RecordingLLMBackend(),
                semantic_compiler_backend=RecordingCompilerBackend(
                    _graph_compiler_payload("Journal", graph_seeds=["Journal"], scope_requests=["journal"])
                ),
                embedding_backend=UnavailableEmbeddingBackend(),
            )
            orders.append([chunk["chunk_id"] for chunk in result.retrieval_packet["selected_chunks"]])
        self.assertEqual(orders[0], orders[1])

    def test_template_boilerplate_is_demoted_for_non_template_queries(self) -> None:
        semantic_compiler_packet = {
            "raw_user_input": "I want journal anecdotes and isomorphic bridges for the 4-Fold Root video.",
            "intent": "find journal anecdotes",
            "query": "journal anecdotes and bridges",
            "planner_retrieval_plan": {
                "intent_type": "semantic_traversal",
                "scope_requests": ["journal"],
                "concepts": ["anecdotes", "bridges"],
                "resolved_referents": [],
                "literal_terms": [],
                "semantic_queries": ["journal anecdotes and bridges"],
                "lexical_queries": ["journal", "anecdotes", "bridges"],
                "graph_seeds": ["journal entries"],
                "retrieval_layers": [
                    {"operator": "lexical_chunk_search", "required": False, "limit": 50},
                    {"operator": "vector_search", "required": False, "limit": 24},
                    {"operator": "graph_expand", "required": False, "depth": 1},
                ],
                "selection_policy": {"max_chunks": 24, "preserve_required_layers": True, "budgets": {"exact": 12, "lexical": 6, "vector": 6, "graph": 4}},
                "claim_policy": {"coverage_claims_allowed": False, "negative_claims_require_exact_layer": True},
            },
        }
        journal_candidate = {
            "chunk_id": "journal",
            "chunk_hash": "journal-hash",
            "relative_path": "JOURNAL/2025/12/17_Wednesday.md",
            "note_title": "December 17, 2025",
            "section_label": "Y-Day Review",
            "paragraph_text": "4-Fold Root as a tool",
            "selection_reason": "vector similarity 0.4",
            "selection_source": "vector",
            "score": 1.4,
        }
        template_candidate = {
            "chunk_id": "template",
            "chunk_hash": "template-hash",
            "relative_path": "VAULT DESIGN/(.)MD TEMPLATES/(w.YAML) Inferential Bridge Template.md",
            "note_title": "Inferential Bridge Template",
            "section_label": "Preserves / Breaks",
            "paragraph_text": "- Preserves (`bridge_preservation`):",
            "selection_reason": "vector similarity 0.9",
            "selection_source": "vector",
            "score": 1.9,
        }
        adjusted = _apply_retrieval_candidate_hygiene(
            candidates=[template_candidate, journal_candidate],
            semantic_compiler_packet=semantic_compiler_packet,
            config=load_runtime_config(repo_root=REPO_ROOT),
        )
        ranked = _merge_candidates([], adjusted, [], config=load_runtime_config(repo_root=REPO_ROOT))
        selected = _select_retrieval_chunks(merged_candidates=ranked, max_chunks=2)
        self.assertEqual([chunk["chunk_id"] for chunk in selected], ["journal", "template"])
        self.assertIn("template boilerplate demoted", selected[1]["selection_reason"])

    def test_template_boilerplate_is_not_demoted_for_template_queries(self) -> None:
        semantic_compiler_packet = {
            "raw_user_input": "Review the inferential bridge template YAML schema.",
            "intent": "review template",
            "query": "inferential bridge template YAML schema",
            "planner_retrieval_plan": {
                "intent_type": "semantic_traversal",
                "scope_requests": [],
                "concepts": ["template", "yaml", "schema"],
                "resolved_referents": [],
                "literal_terms": [],
                "semantic_queries": ["inferential bridge template YAML schema"],
                "lexical_queries": ["template", "yaml", "schema"],
                "graph_seeds": ["Inferential Bridge Template"],
                "retrieval_layers": [
                    {"operator": "lexical_chunk_search", "required": False, "limit": 50},
                    {"operator": "vector_search", "required": False, "limit": 24},
                    {"operator": "graph_expand", "required": False, "depth": 1},
                ],
                "selection_policy": {"max_chunks": 24, "preserve_required_layers": True, "budgets": {"exact": 12, "lexical": 6, "vector": 6, "graph": 4}},
                "claim_policy": {"coverage_claims_allowed": False, "negative_claims_require_exact_layer": True},
            },
        }
        template_candidate = {
            "chunk_id": "template",
            "chunk_hash": "template-hash",
            "relative_path": "VAULT DESIGN/(.)MD TEMPLATES/(w.YAML) Inferential Bridge Template.md",
            "note_title": "Inferential Bridge Template",
            "section_label": "Preserves / Breaks",
            "paragraph_text": "- Preserves (`bridge_preservation`):",
            "selection_reason": "vector similarity 0.9",
            "selection_source": "vector",
            "score": 1.9,
        }
        adjusted = _apply_retrieval_candidate_hygiene(
            candidates=[template_candidate],
            semantic_compiler_packet=semantic_compiler_packet,
            config=load_runtime_config(repo_root=REPO_ROOT),
        )
        self.assertNotIn("template boilerplate demoted", adjusted[0]["selection_reason"])

    def test_compiler_packet_preserves_raw_user_input(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            user_input = "Please retrieve the candy snack food before bed note."
            result = run_thread_turn(
                repo_root=REPO_ROOT,
                data_root=Path(temp_dir),
                user_input=user_input,
                llm_backend=TestLLMBackend(),
                semantic_compiler_backend=TestSemanticCompilerBackend(),
            )
            compiler_packet = _turn_artifact(result.semantic_compiler_packet_path)
            self.assertEqual(compiler_packet["raw_user_input"], user_input)
            self.assertEqual(result.semantic_compiler_packet["raw_user_input"], user_input)

    def test_compiler_backend_failure_uses_deterministic_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            result = run_thread_turn(
                repo_root=REPO_ROOT,
                data_root=Path(temp_dir),
                user_input="Please retrieve the candy snack food before bed note.",
                llm_backend=TestLLMBackend(),
                semantic_compiler_backend=ExplodingCompilerBackend(),
            )
            self.assertEqual(result.semantic_compiler_status, "fallback")
            self.assertIn("deterministic lexical fallback used", result.semantic_compiler_packet["limitations"][0])
            diagnostic = _turn_artifact(result.semantic_compiler_diagnostic_path)
            self.assertEqual(diagnostic["semantic_compiler_response_status"], "unavailable")
            self.assertEqual(diagnostic["canonical_semantic_compiler_status"], "fallback")
            self.assertIn("compiler backend exploded", diagnostic["metadata"]["error"])
            self.assertEqual(result.coverage_report["semantic_compiler_response_status"], "unavailable")

    def test_fallback_graph_seeds_do_not_include_assistant_response_text(self) -> None:
        data_root = _prepare_data_root()
        first_turn = run_thread_turn(
            repo_root=REPO_ROOT,
            data_root=data_root,
            user_input="thinking about doing a problem space enthusiast video on 4-Fold Root",
            llm_backend=RecordingLLMBackend(),
            semantic_compiler_backend=TestSemanticCompilerBackend(),
            embedding_backend=FakeEmbeddingBackend(),
        )
        second_turn = run_thread_turn(
            repo_root=REPO_ROOT,
            data_root=data_root,
            user_input="I think some good anecdotes from the journal would be really good to tie in here, additionally some isomorphic bridges",
            llm_backend=RecordingLLMBackend(),
            thread_id=first_turn.thread_id,
            semantic_compiler_backend=ExplodingCompilerBackend(),
            embedding_backend=FakeEmbeddingBackend(),
        )
        self.assertEqual(second_turn.semantic_compiler_status, "fallback")
        joined_graph_seeds = "\n".join(second_turn.semantic_compiler_packet["planner_retrieval_plan"]["graph_seeds"])
        self.assertNotIn("Recorded assistant response", joined_graph_seeds)
        self.assertNotIn("assistant response", joined_graph_seeds.lower())

    def test_lexical_traversal_retrieves_ingested_fixture(self) -> None:
        data_root = _prepare_data_root()
        result = run_thread_turn(
            repo_root=REPO_ROOT,
            data_root=data_root,
            user_input="Please retrieve the candy snack food before bed note.",
            llm_backend=RecordingLLMBackend(),
            semantic_compiler_backend=TestSemanticCompilerBackend(),
            embedding_backend=FakeEmbeddingBackend(),
        )
        self.assertEqual(result.coverage_report["decision"], "approved")
        self.assertGreater(result.retrieval_packet["matched_chunk_count"], 0)
        self.assertTrue(any(chunk["source_root_label"] == "tests-fixtures" for chunk in result.retrieval_packet["selected_chunks"]))
        self.assertTrue(result.retrieval_packet["selected_chunks"][0]["selection_reason"])

    def test_search_journal_uses_scoped_exact_retrieval_plan(self) -> None:
        data_root = _prepare_data_root()
        result = run_thread_turn(
            repo_root=REPO_ROOT,
            data_root=data_root,
            user_input="search journal for candy",
            llm_backend=RecordingLLMBackend(),
            semantic_compiler_backend=TestSemanticCompilerBackend(),
            embedding_backend=FakeEmbeddingBackend(),
        )

        retrieval_plan = result.semantic_compiler_packet["planner_retrieval_plan"]
        self.assertEqual(retrieval_plan["intent_type"], "scoped_exact_search")
        self.assertEqual([entry["term"] for entry in retrieval_plan["literal_terms"]], ["candy"])
        self.assertIn("journal", retrieval_plan["scope_requests"])
        self.assertNotIn("scope_filters", retrieval_plan)
        self.assertIn("exact_chunk_search", result.semantic_traversal_manifest["execution"]["layers_executed"])
        self.assertIn("journal_entry", result.semantic_traversal_manifest["bound_retrieval_plan"]["scope_filters"]["note_type"])
        self.assertTrue(result.semantic_traversal_manifest["resource_inventory_summary"]["frontmatter_facets"]["note_type"])
        self.assertIn("planner_retrieval_plan", result.semantic_traversal_manifest)
        self.assertIn("bound_retrieval_plan", result.semantic_traversal_manifest)
        self.assertIn("resolver_adjustments", result.semantic_traversal_manifest)
        self.assertIn("resource_inventory_summary", result.semantic_traversal_manifest)
        self.assertGreater(result.semantic_traversal_manifest["coverage"]["total_exact_matches"], 0)
        self.assertTrue(result.semantic_traversal_manifest["coverage"]["exact_search_performed"])
        self.assertTrue(any("exact" in chunk["source_layers"] for chunk in result.retrieval_packet["selected_chunks"]))

    def test_unknown_scope_request_remains_non_authoritative(self) -> None:
        data_root = _prepare_data_root()
        compiler_backend = ResponseCompilerBackend(
            payload={
                "raw_user_input": "What about philosophy and influence?",
                "intent": "fixture response",
                "query": "what about philosophy and influence",
                "entities": [],
                "relations": [],
                "resolved_referents": [],
                "planner_retrieval_plan": {
                    "intent_type": "semantic_traversal",
                    "scope_requests": ["philosophy"],
                    "concepts": ["influence", "Schopenhauer"],
                    "resolved_referents": [],
                    "literal_terms": [],
                    "semantic_queries": ["what about philosophy and influence"],
                    "lexical_queries": ["influence", "Schopenhauer"],
                    "graph_seeds": [],
                    "retrieval_layers": [
                        {"operator": "lexical_chunk_search", "required": False, "limit": 50},
                        {"operator": "vector_search", "required": False, "limit": 24},
                        {"operator": "graph_expand", "required": False, "depth": 1},
                    ],
                    "selection_policy": {"max_chunks": 24, "preserve_required_layers": True, "budgets": {"exact": 12, "lexical": 6, "vector": 6, "graph": 4}},
                    "claim_policy": {"coverage_claims_allowed": False, "negative_claims_require_exact_layer": True},
                },
                "limitations": [],
            },
            raw_response="raw response",
        )
        result = run_thread_turn(
            repo_root=REPO_ROOT,
            data_root=data_root,
            user_input="What about philosophy and influence?",
            llm_backend=RecordingLLMBackend(),
            semantic_compiler_backend=compiler_backend,
            embedding_backend=FakeEmbeddingBackend(),
        )
        self.assertNotIn("philosophy", result.semantic_traversal_manifest["bound_retrieval_plan"]["scope_filters"]["note_type"])
        self.assertTrue(result.semantic_traversal_manifest["resolver_adjustments"])
        self.assertEqual(result.semantic_traversal_manifest["resolver_adjustments"][0]["action"], "unavailable_scope_alias")
        self.assertIn("philosophy", result.semantic_traversal_manifest["planner_retrieval_plan"]["scope_requests"])
        self.assertIn("influence", result.semantic_traversal_manifest["planner_retrieval_plan"]["concepts"])

    def test_planner_cannot_emit_hard_scope_filters(self) -> None:
        data_root = _prepare_data_root()
        compiler_backend = ResponseCompilerBackend(
            payload={
                "raw_user_input": "search philosophy",
                "intent": "fixture response",
                "query": "search philosophy",
                "entities": [],
                "relations": [],
                "resolved_referents": [],
                "planner_retrieval_plan": {
                    "intent_type": "semantic_traversal",
                    "scope_requests": ["philosophy"],
                    "scope_filters": {"note_type": ["philosophy"]},
                    "concepts": ["philosophy"],
                    "resolved_referents": [],
                    "literal_terms": [],
                    "semantic_queries": ["search philosophy"],
                    "lexical_queries": ["philosophy"],
                    "graph_seeds": [],
                    "retrieval_layers": [
                        {"operator": "lexical_chunk_search", "required": False, "limit": 50},
                        {"operator": "vector_search", "required": False, "limit": 24},
                        {"operator": "graph_expand", "required": False, "depth": 1},
                    ],
                    "selection_policy": {"max_chunks": 24, "preserve_required_layers": True, "budgets": {"exact": 12, "lexical": 6, "vector": 6, "graph": 4}},
                    "claim_policy": {"coverage_claims_allowed": False, "negative_claims_require_exact_layer": True},
                },
                "limitations": [],
            },
            raw_response="raw response",
        )
        result = run_thread_turn(
            repo_root=REPO_ROOT,
            data_root=data_root,
            user_input="search philosophy",
            llm_backend=RecordingLLMBackend(),
            semantic_compiler_backend=compiler_backend,
            embedding_backend=FakeEmbeddingBackend(),
        )
        self.assertNotIn("scope_filters", result.semantic_compiler_packet["planner_retrieval_plan"])
        self.assertNotIn("philosophy", result.semantic_traversal_manifest["bound_retrieval_plan"]["scope_filters"]["note_type"])
        self.assertIn("scope_filters", result.semantic_compiler_packet["planner_diagnostics"]["ignored_planner_fields"])

    def test_planner_diagnostics_echo_is_not_counted_as_ignored_planner_field(self) -> None:
        data_root = _prepare_data_root()
        compiler_backend = ResponseCompilerBackend(
            payload={
                "raw_user_input": "search journal for candy",
                "intent": "fixture response",
                "query": "search journal for candy",
                "entities": [],
                "relations": [],
                "resolved_referents": [],
                "planner_diagnostics": {"source": "model echo", "ignored_planner_fields": ["made_up"]},
                "unexpected_surface": "still ignored",
                "planner_retrieval_plan": {
                    "intent_type": "scoped_exact_search",
                    "scope_requests": ["journal"],
                    "concepts": ["candy"],
                    "resolved_referents": [],
                    "literal_terms": ["candy"],
                    "semantic_queries": ["search journal for candy"],
                    "lexical_queries": ["candy"],
                    "graph_seeds": [],
                    "retrieval_layers": [
                        {"operator": "exact_chunk_search", "required": True, "limit": 200, "return_total_count": True},
                        {"operator": "lexical_chunk_search", "required": False, "limit": 50},
                        {"operator": "vector_search", "required": False, "limit": 24},
                        {"operator": "graph_expand", "required": False, "depth": 1},
                    ],
                    "selection_policy": {"max_chunks": 24, "preserve_required_layers": True, "budgets": {"exact": 12, "lexical": 6, "vector": 6, "graph": 4}},
                    "claim_policy": {"coverage_claims_allowed": True, "negative_claims_require_exact_layer": True},
                },
                "limitations": [],
            },
            raw_response="raw response",
        )
        result = run_thread_turn(
            repo_root=REPO_ROOT,
            data_root=data_root,
            user_input="search journal for candy",
            llm_backend=RecordingLLMBackend(),
            semantic_compiler_backend=compiler_backend,
            embedding_backend=FakeEmbeddingBackend(),
        )
        ignored_fields = result.semantic_compiler_packet["planner_diagnostics"]["ignored_planner_fields"]
        self.assertNotIn("planner_diagnostics", ignored_fields)
        self.assertIn("unexpected_surface", ignored_fields)
        self.assertNotIn("source", result.semantic_compiler_packet["planner_diagnostics"])

    def test_no_database_path_still_resolves_scope_aliases(self) -> None:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        data_root = Path(temp_dir.name)
        config = load_runtime_config(repo_root=REPO_ROOT)
        compiler_backend = ResponseCompilerBackend(
            payload={
                "raw_user_input": "search journal",
                "intent": "fixture response",
                "query": "search journal",
                "entities": [],
                "relations": [],
                "resolved_referents": [],
                "planner_retrieval_plan": {
                    "intent_type": "semantic_traversal",
                    "scope_requests": ["journal"],
                    "scope_filters": {"note_type": ["philosophy"]},
                    "concepts": ["journal"],
                    "resolved_referents": [],
                    "literal_terms": [],
                    "semantic_queries": ["search journal"],
                    "lexical_queries": ["journal"],
                    "graph_seeds": [],
                    "retrieval_layers": [
                        {"operator": "lexical_chunk_search", "required": False, "limit": 50},
                        {"operator": "vector_search", "required": False, "limit": 24},
                        {"operator": "graph_expand", "required": False, "depth": 1},
                    ],
                    "selection_policy": {"max_chunks": 24, "preserve_required_layers": True, "budgets": {"exact": 12, "lexical": 6, "vector": 6, "graph": 4}},
                    "claim_policy": {"coverage_claims_allowed": False, "negative_claims_require_exact_layer": True},
                },
                "limitations": [],
            },
            raw_response="raw response",
        )
        result = run_thread_turn(
            repo_root=REPO_ROOT,
            data_root=data_root,
            user_input="search journal",
            llm_backend=RecordingLLMBackend(),
            config=config,
            semantic_compiler_backend=compiler_backend,
            embedding_backend=FakeEmbeddingBackend(),
        )
        self.assertNotIn("scope_filters", result.semantic_compiler_packet["planner_retrieval_plan"])
        self.assertIn("journal_entry", result.semantic_traversal_manifest["bound_retrieval_plan"]["scope_filters"]["note_type"])
        self.assertEqual(result.semantic_traversal_manifest["resolver_adjustments"][0]["action"], "bound_to_alias")
        self.assertTrue(any(adjustment["action"] == "bound_to_alias_unobserved_in_inventory" for adjustment in result.semantic_traversal_manifest["resolver_adjustments"]))

    def test_resolver_rejects_observed_note_type_without_alias(self) -> None:
        data_root = _prepare_data_root()
        compiler_backend = ResponseCompilerBackend(
            payload={
                "raw_user_input": "search journal_entry",
                "intent": "fixture response",
                "query": "search journal_entry",
                "entities": [],
                "relations": [],
                "resolved_referents": [],
                "planner_retrieval_plan": {
                    "intent_type": "semantic_traversal",
                    "scope_requests": ["journal_entry"],
                    "concepts": ["journal_entry"],
                    "resolved_referents": [],
                    "literal_terms": [],
                    "semantic_queries": ["search journal_entry"],
                    "lexical_queries": ["journal_entry"],
                    "graph_seeds": [],
                    "retrieval_layers": [
                        {"operator": "lexical_chunk_search", "required": False, "limit": 50},
                        {"operator": "vector_search", "required": False, "limit": 24},
                        {"operator": "graph_expand", "required": False, "depth": 1},
                    ],
                    "selection_policy": {"max_chunks": 24, "preserve_required_layers": True, "budgets": {"exact": 12, "lexical": 6, "vector": 6, "graph": 4}},
                    "claim_policy": {"coverage_claims_allowed": False, "negative_claims_require_exact_layer": True},
                },
                "limitations": [],
            },
            raw_response="raw response",
        )
        result = run_thread_turn(
            repo_root=REPO_ROOT,
            data_root=data_root,
            user_input="search journal_entry",
            llm_backend=RecordingLLMBackend(),
            semantic_compiler_backend=compiler_backend,
            embedding_backend=FakeEmbeddingBackend(),
        )
        self.assertNotIn("journal_entry", result.semantic_traversal_manifest["bound_retrieval_plan"]["scope_filters"]["note_type"])
        self.assertEqual(result.semantic_traversal_manifest["resolver_adjustments"][0]["action"], "unauthorized_inventory_scope")

    def test_alias_bound_unobserved_inventory_values_are_explicit(self) -> None:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        data_root = Path(temp_dir.name)
        config = load_runtime_config(repo_root=REPO_ROOT)
        compiler_backend = ResponseCompilerBackend(
            payload={
                "raw_user_input": "search journal",
                "intent": "fixture response",
                "query": "search journal",
                "entities": [],
                "relations": [],
                "resolved_referents": [],
                "planner_retrieval_plan": {
                    "intent_type": "semantic_traversal",
                    "scope_requests": ["journal"],
                    "concepts": ["journal"],
                    "resolved_referents": [],
                    "literal_terms": [],
                    "semantic_queries": ["search journal"],
                    "lexical_queries": ["journal"],
                    "graph_seeds": [],
                    "retrieval_layers": [
                        {"operator": "lexical_chunk_search", "required": False, "limit": 50},
                        {"operator": "vector_search", "required": False, "limit": 24},
                        {"operator": "graph_expand", "required": False, "depth": 1},
                    ],
                    "selection_policy": {"max_chunks": 24, "preserve_required_layers": True, "budgets": {"exact": 12, "lexical": 6, "vector": 6, "graph": 4}},
                    "claim_policy": {"coverage_claims_allowed": False, "negative_claims_require_exact_layer": True},
                },
                "limitations": [],
            },
            raw_response="raw response",
        )
        result = run_thread_turn(
            repo_root=REPO_ROOT,
            data_root=data_root,
            user_input="search journal",
            llm_backend=RecordingLLMBackend(),
            config=config,
            semantic_compiler_backend=compiler_backend,
            embedding_backend=FakeEmbeddingBackend(),
        )
        self.assertIn("journal_entry", result.semantic_traversal_manifest["bound_retrieval_plan"]["scope_filters"]["note_type"])
        self.assertTrue(any(adjustment["action"] == "bound_to_alias_unobserved_in_inventory" for adjustment in result.semantic_traversal_manifest["resolver_adjustments"]))

    def test_resource_inventory_observes_frontmatter_note_type(self) -> None:
        data_root = _prepare_data_root()
        result = run_thread_turn(
            repo_root=REPO_ROOT,
            data_root=data_root,
            user_input="search journal for candy",
            llm_backend=RecordingLLMBackend(),
            semantic_compiler_backend=TestSemanticCompilerBackend(),
            embedding_backend=FakeEmbeddingBackend(),
        )
        facets = result.semantic_traversal_manifest["resource_inventory_summary"]["frontmatter_facets"]["note_type"]
        self.assertTrue(any(entry["value"] == "journal_entry" and entry["count"] > 0 for entry in facets))

    def test_resource_inventory_counts_notes_not_chunks(self) -> None:
        data_root = _prepare_multi_chunk_inventory_data_root()
        result = run_thread_turn(
            repo_root=REPO_ROOT,
            data_root=data_root,
            user_input="search journal for candy",
            llm_backend=RecordingLLMBackend(),
            semantic_compiler_backend=TestSemanticCompilerBackend(),
            embedding_backend=FakeEmbeddingBackend(),
        )
        facets = result.semantic_traversal_manifest["resource_inventory_summary"]["frontmatter_facets"]["note_type"]
        self.assertTrue(any(entry["value"] == "journal_entry" and entry["count"] == 1 for entry in facets))
        source_labels = result.semantic_traversal_manifest["resource_inventory_summary"]["observed_source_labels"]
        self.assertTrue(any(entry["count"] == 1 for entry in source_labels))

    def test_compiler_payload_without_limitations_does_not_inherit_fallback_limitations(self) -> None:
        data_root = _prepare_data_root()
        compiler_backend = ResponseCompilerBackend(
            payload={
                "raw_user_input": "Please retrieve the candy snack food before bed note.",
                "intent": "fixture response",
                "query": "candy snack food before bed",
                "entities": [],
                "relations": [],
                "resolved_referents": [],
                "planner_retrieval_plan": {
                    "intent_type": "semantic_traversal",
                    "scope_requests": [],
                    "concepts": ["candy", "snack", "food", "bed"],
                    "resolved_referents": [],
                    "literal_terms": [],
                    "semantic_queries": ["candy snack food before bed"],
                    "lexical_queries": ["candy", "snack", "food", "bed"],
                    "graph_seeds": ["candy snack food before bed"],
                    "retrieval_layers": [
                        {"operator": "lexical_chunk_search", "required": False, "limit": 50},
                        {"operator": "vector_search", "required": False, "limit": 24},
                        {"operator": "graph_expand", "required": False, "depth": 1},
                    ],
                    "selection_policy": {"max_chunks": 24, "preserve_required_layers": True, "budgets": {"exact": 12, "lexical": 6, "vector": 6, "graph": 4}},
                    "claim_policy": {"coverage_claims_allowed": False, "negative_claims_require_exact_layer": True},
                },
            },
            raw_response="raw response",
        )
        result = run_thread_turn(
            repo_root=REPO_ROOT,
            data_root=data_root,
            user_input="Please retrieve the candy snack food before bed note.",
            llm_backend=RecordingLLMBackend(),
            semantic_compiler_backend=compiler_backend,
            embedding_backend=FakeEmbeddingBackend(),
        )
        self.assertEqual(result.semantic_compiler_packet["limitations"], [])
        self.assertNotIn("deterministic compiler packet used", result.semantic_compiler_packet["limitations"])

    def test_ingest_skips_apparatus_only_chunks_but_keeps_meaningful_prose(self) -> None:
        data_root = _prepare_apparatus_graph_fixture_data_root()
        config = load_runtime_config(repo_root=REPO_ROOT)
        database_path = data_root / config.storage_ingestion_root / config.storage_ingestion_database_filename
        with closing(sqlite3.connect(database_path)) as connection:
            rows = connection.execute(
                "SELECT paragraph_text FROM chunks WHERE note_title = ? ORDER BY rowid",
                ("The Parmenidean Ascent",),
            ).fetchall()
        chunk_texts = [str(row[0]) for row in rows]
        joined_chunks = "\n".join(chunk_texts)
        self.assertIn("First, take explanatory demands seriously and let them be your guide.", joined_chunks)
        self.assertIn("Oxford University Press is mentioned as the publisher", joined_chunks)
        for forbidden in (
            "The Parmenidean Ascent",
            "MICHAEL DELLA ROCCA",
            "OXFORD",
            "Oxford University Press.",
            "The Parmenidean Ascent. Michael Della Rocca, Oxford University Press (2020).",
        ):
            self.assertNotIn(forbidden, joined_chunks)

    def test_compiler_request_packet_includes_compact_resource_inventory(self) -> None:
        data_root = _prepare_data_root()
        compiler_backend = RecordingCompilerBackend(
            {
                "raw_user_input": "Please retrieve the candy snack food before bed note.",
                "intent": "fixture response",
                "query": "candy snack food before bed",
                "entities": [],
                "relations": [],
                "resolved_referents": [],
                "planner_retrieval_plan": {
                    "intent_type": "semantic_traversal",
                    "scope_requests": [],
                    "concepts": ["candy", "snack", "food", "bed"],
                    "resolved_referents": [],
                    "literal_terms": [],
                    "semantic_queries": ["candy snack food before bed"],
                    "lexical_queries": ["candy", "snack", "food", "bed"],
                    "graph_seeds": ["candy snack food before bed"],
                    "retrieval_layers": [
                        {"operator": "lexical_chunk_search", "required": False, "limit": 50},
                        {"operator": "vector_search", "required": False, "limit": 24},
                        {"operator": "graph_expand", "required": False, "depth": 1},
                    ],
                    "selection_policy": {"max_chunks": 24, "preserve_required_layers": True, "budgets": {"exact": 12, "lexical": 6, "vector": 6, "graph": 4}},
                    "claim_policy": {"coverage_claims_allowed": False, "negative_claims_require_exact_layer": True},
                },
                "limitations": [],
            }
        )
        run_thread_turn(
            repo_root=REPO_ROOT,
            data_root=data_root,
            user_input="Please retrieve the candy snack food before bed note.",
            llm_backend=RecordingLLMBackend(),
            semantic_compiler_backend=compiler_backend,
            embedding_backend=FakeEmbeddingBackend(),
        )
        request_packet = compiler_backend.calls[0]
        self.assertIn("resource_inventory_summary", request_packet)
        self.assertIn("scope_aliases", request_packet)
        self.assertIn("frontmatter_facets", request_packet["resource_inventory_summary"])
        self.assertNotIn("paragraph_text", request_packet["resource_inventory_summary"])

    def test_graph_representative_chunks_skip_apparatus_before_meaningful_body(self) -> None:
        data_root = _prepare_apparatus_graph_fixture_data_root()
        result = run_thread_turn(
            repo_root=REPO_ROOT,
            data_root=data_root,
            user_input="A",
            llm_backend=RecordingLLMBackend(),
            semantic_compiler_backend=RecordingCompilerBackend(_graph_compiler_payload("A", graph_seeds=["A"], graph_depth=1)),
            embedding_backend=UnavailableEmbeddingBackend(),
        )
        selected_for_book = [chunk for chunk in result.retrieval_packet["selected_chunks"] if chunk["note_title"] == "The Parmenidean Ascent"]
        self.assertTrue(selected_for_book)
        self.assertTrue(any("First, take explanatory demands seriously" in chunk["paragraph_text"] for chunk in selected_for_book))
        self.assertFalse(any(chunk["paragraph_text"].strip() in {"OXFORD", "MICHAEL DELLA ROCCA", "The Parmenidean Ascent"} for chunk in selected_for_book))

    def test_vector_only_retrieval_forbids_corpus_wide_negative_claims(self) -> None:
        data_root = _prepare_data_root()
        result = run_thread_turn(
            repo_root=REPO_ROOT,
            data_root=data_root,
            user_input="How does candy relate to dreams?",
            llm_backend=RecordingLLMBackend(),
            semantic_compiler_backend=TestSemanticCompilerBackend(),
            embedding_backend=FakeEmbeddingBackend(),
        )

        self.assertFalse(result.semantic_traversal_manifest["coverage"]["exact_search_performed"])
        self.assertFalse(result.semantic_traversal_manifest["coverage"]["negative_claims_allowed"])
        self.assertTrue(any("corpus-wide negative" in requirement for requirement in result.synthesis_context_packet["output_requirements"]))

    def test_retrieval_packet_contains_provenance(self) -> None:
        data_root = _prepare_data_root()
        result = run_thread_turn(
            repo_root=REPO_ROOT,
            data_root=data_root,
            user_input="Please retrieve the candy snack food before bed note.",
            llm_backend=RecordingLLMBackend(),
            semantic_compiler_backend=TestSemanticCompilerBackend(),
            embedding_backend=FakeEmbeddingBackend(),
        )
        chunk = result.retrieval_packet["selected_chunks"][0]
        for field in (
            "chunk_id",
            "note_id",
            "source_root_label",
            "relative_path",
            "note_title",
            "section_label",
            "paragraph_text",
            "chunk_hash",
            "source_layers",
            "selection_source",
            "selection_reason",
        ):
            self.assertIn(field, chunk)
        self.assertIsInstance(chunk["source_layers"], list)
        self.assertIsInstance(chunk["selection_source"], str)
        vector_chunks = [item for item in result.retrieval_packet["selected_chunks"] if "vector" in item["source_layers"]]
        if vector_chunks:
            self.assertIn("vector_query_scores", vector_chunks[0])
            self.assertIn("vector_best_query", vector_chunks[0])

    def test_required_lexical_layer_uses_structured_status_and_selected_contribution(self) -> None:
        data_root = _prepare_data_root()
        payload = _controlled_layer_payload(
            "alpha",
            layers=[{"operator": "lexical_chunk_search", "required": True, "limit": 50}],
            lexical_queries=["candy"],
        )
        result = run_thread_turn(
            repo_root=REPO_ROOT, data_root=data_root, user_input="alpha",
            llm_backend=RecordingLLMBackend(), semantic_compiler_backend=RecordingCompilerBackend(payload), embedding_backend=UnavailableEmbeddingBackend(),
        )
        manifest = result.semantic_traversal_manifest["layer_manifests"]["lexical"]
        self.assertEqual(manifest["status"], "completed_with_candidates")
        self.assertGreater(manifest["candidate_count"], 0)
        self.assertGreater(manifest["selected_contribution_count"], 0)
        self.assertTrue(manifest["adequate_contribution"])
        self.assertEqual(result.semantic_traversal_manifest["coverage"]["required_layer_results"][0]["adequate_contribution"], True)

    def test_required_lexical_no_candidates_blocks_without_absence_permission(self) -> None:
        data_root = _prepare_data_root()
        payload = _controlled_layer_payload(
            "absent",
            layers=[{"operator": "lexical_chunk_search", "required": True, "limit": 50}],
            lexical_queries=["qxvplmno"],
        )
        result = run_thread_turn(
            repo_root=REPO_ROOT, data_root=data_root, user_input="absent",
            llm_backend=RecordingLLMBackend(), semantic_compiler_backend=RecordingCompilerBackend(payload), embedding_backend=UnavailableEmbeddingBackend(),
        )
        manifest = result.semantic_traversal_manifest["layer_manifests"]["lexical"]
        self.assertEqual(manifest["status"], "completed_no_candidates")
        self.assertFalse(manifest["adequate_contribution"])
        self.assertEqual(result.coverage_report["decision"], "blocked")
        self.assertFalse(result.semantic_traversal_manifest["coverage"]["negative_claims_allowed"])

    def test_required_vector_and_graph_manifests_record_selected_surface_contribution(self) -> None:
        data_root = _prepare_graph_fixture_data_root()
        payload = _controlled_layer_payload(
            "A",
            layers=[
                {"operator": "vector_search", "required": True, "limit": 24},
                {"operator": "graph_expand", "required": True, "limit": 24, "depth": 1},
            ],
            semantic_queries=["A"], graph_seeds=["A"],
        )
        result = run_thread_turn(
            repo_root=REPO_ROOT, data_root=data_root, user_input="A",
            llm_backend=RecordingLLMBackend(), semantic_compiler_backend=RecordingCompilerBackend(payload), embedding_backend=FakeEmbeddingBackend(),
        )
        vector = result.semantic_traversal_manifest["layer_manifests"]["vector"]
        graph = result.semantic_traversal_manifest["layer_manifests"]["graph"]
        self.assertIn(vector["status"], {"completed_with_candidates", "completed_no_candidates"})
        self.assertEqual(graph["status"], "completed_with_candidates")
        self.assertTrue(graph["adequate_contribution"])
        self.assertTrue(any("graph" in chunk["source_layers"] for chunk in result.retrieval_packet["selected_chunks"]))

    def test_required_unknown_operator_is_preserved_and_blocks(self) -> None:
        data_root = _prepare_data_root()
        payload = _controlled_layer_payload(
            "unknown",
            layers=[{"operator": "future_surface", "required": True, "limit": 5}],
            lexical_queries=[], semantic_queries=[],
        )
        result = run_thread_turn(
            repo_root=REPO_ROOT, data_root=data_root, user_input="unknown",
            llm_backend=RecordingLLMBackend(), semantic_compiler_backend=RecordingCompilerBackend(payload), embedding_backend=UnavailableEmbeddingBackend(),
        )
        self.assertEqual(result.semantic_traversal_manifest["unsupported_layer_requests"][0]["operator"], "future_surface")
        self.assertEqual(result.coverage_report["decision"], "blocked")
        self.assertIn("unsupported", " ".join(result.coverage_report["blocking_reasons"]))

    def test_approved_coverage_calls_llm(self) -> None:
        data_root = _prepare_data_root()
        llm_backend = RecordingLLMBackend()
        result = run_thread_turn(
            repo_root=REPO_ROOT,
            data_root=data_root,
            user_input="Please retrieve the candy snack food before bed note.",
            llm_backend=llm_backend,
            semantic_compiler_backend=TestSemanticCompilerBackend(),
            embedding_backend=FakeEmbeddingBackend(),
        )
        self.assertEqual(result.runtime_outcome, "completed")
        self.assertEqual(len(llm_backend.calls), 1)
        self.assertEqual(result.assistant_response, "Recorded assistant response for turn 1: Please retrieve the candy snack food before bed note.")

    def test_blocked_coverage_does_not_call_llm(self) -> None:
        class FailingLLMBackend(RecordingLLMBackend):
            def generate(self, synthesis_context_packet: dict[str, Any]):
                raise AssertionError("LLM should not be called when coverage blocks")

        llm_backend = FailingLLMBackend()
        with tempfile.TemporaryDirectory() as temp_dir:
            result = run_thread_turn(
                repo_root=REPO_ROOT,
                data_root=Path(temp_dir),
                user_input="qzxyv qzxyv qzxyv",
                llm_backend=llm_backend,
                semantic_compiler_backend=TestSemanticCompilerBackend(),
            )
            ledger_record = read_ledger(result.thread_ledger_path)[-1]
        self.assertEqual(result.runtime_outcome, "blocked")
        self.assertEqual(len(llm_backend.calls), 0)
        self.assertIsNotNone(result.assistant_response)
        self.assertIn("couldn't", result.assistant_response.lower())
        self.assertEqual(
            ledger_record["llm_call_metadata"],
            {
                "synthesis_status": "not_attempted",
                "provider": None,
                "model": None,
                "response_id": None,
                "reasoning_effort": None,
                "usage": None,
                "error": None,
            },
        )

    def test_ledger_records_per_synthesis_llm_telemetry(self) -> None:
        class TelemetryLLMBackend(RecordingLLMBackend):
            def generate(self, synthesis_context_packet: dict[str, Any]) -> LLMResponse:
                self.calls.append(synthesis_context_packet)
                return LLMResponse(
                    assistant_response="Telemetry response",
                    metadata={
                        "mode": self.mode_name,
                        "provider": "openai",
                        "model": "gpt-5.6-luna",
                        "response_id": "resp_telemetry",
                        "reasoning_effort": "medium",
                        "usage": {"input_tokens": 123, "output_tokens": 45, "cached_tokens": 67},
                    },
                )

        result = run_thread_turn(
            repo_root=REPO_ROOT,
            data_root=_prepare_data_root(),
            user_input="Please retrieve the candy snack food before bed note.",
            llm_backend=TelemetryLLMBackend(),
            semantic_compiler_backend=TestSemanticCompilerBackend(),
            embedding_backend=FakeEmbeddingBackend(),
        )
        ledger = read_ledger(result.thread_ledger_path)
        self.assertEqual(
            ledger[-1]["llm_call_metadata"],
            {
                "synthesis_status": "completed",
                "provider": "openai",
                "model": "gpt-5.6-luna",
                "response_id": "resp_telemetry",
                "reasoning_effort": "medium",
                "usage": {"input_tokens": 123, "output_tokens": 45, "cached_tokens": 67},
                "error": None,
            },
        )

    def test_frontier_failure_persists_blocked_turn_and_user_facing_response(self) -> None:
        class FailingLLMBackend(RecordingLLMBackend):
            def describe_call(self) -> dict[str, Any]:
                return {
                    "mode": "live",
                    "provider": "openai",
                    "model": "gpt-5.6-luna",
                    "reasoning_effort": "medium",
                }

            def generate(self, synthesis_context_packet: dict[str, Any]):
                raise RuntimeError("provider timed out")

        data_root = _prepare_data_root()
        result = run_thread_turn(
            repo_root=REPO_ROOT,
            data_root=data_root,
            user_input="Please retrieve the candy snack food before bed note.",
            llm_backend=FailingLLMBackend(),
            semantic_compiler_backend=TestSemanticCompilerBackend(),
            embedding_backend=FakeEmbeddingBackend(),
        )
        self.assertEqual(result.runtime_outcome, "blocked")
        self.assertIsNotNone(result.assistant_response)
        self.assertIn("frontier agent was unavailable", result.assistant_response.lower())
        self.assertTrue(result.state_delta_path.exists())
        self.assertIn("frontier LLM failed", result.blocking_reasons[-1])
        persisted_thread = load_json(result.conversation_thread_path)
        persisted_state_delta = load_json(result.state_delta_path)
        persisted_ledger = read_ledger(result.thread_ledger_path)
        self.assertIsNotNone(persisted_thread)
        self.assertEqual(persisted_thread["messages"][-1]["role"], "assistant")
        self.assertEqual(persisted_thread["messages"][-1]["content"], result.assistant_response)
        self.assertIsNotNone(persisted_state_delta)
        self.assertEqual(persisted_state_delta["runtime_outcome"], "blocked")
        self.assertIn("frontier LLM failed", persisted_state_delta["blocking_reasons"][-1])
        self.assertEqual(persisted_ledger[-1]["runtime_outcome"], "blocked")
        self.assertEqual(persisted_ledger[-1]["llm_call_metadata"]["synthesis_status"], "failed")
        self.assertEqual(persisted_ledger[-1]["llm_call_metadata"]["provider"], "openai")
        self.assertEqual(persisted_ledger[-1]["llm_call_metadata"]["model"], "gpt-5.6-luna")
        self.assertEqual(persisted_ledger[-1]["llm_call_metadata"]["reasoning_effort"], "medium")
        self.assertEqual(persisted_ledger[-1]["llm_call_metadata"]["error"], "RuntimeError: provider timed out")

    def test_synthesis_packet_hides_raw_compiler_backend_response(self) -> None:
        data_root = _prepare_data_root()
        compiler_backend = ResponseCompilerBackend(
            payload={
                "raw_user_input": "Please retrieve the candy snack food before bed note.",
                "intent": "fixture response",
                "query": "candy snack food before bed",
                "entities": [],
                "relations": [],
                "resolved_referents": [],
                "planner_retrieval_plan": {
                    "intent_type": "semantic_traversal",
                    "scope_requests": [],
                    "concepts": ["candy", "snack", "food", "bed"],
                    "resolved_referents": [],
                    "literal_terms": [],
                    "semantic_queries": ["candy snack food before bed"],
                    "lexical_queries": ["candy", "snack", "food", "bed"],
                    "graph_seeds": ["candy snack food before bed"],
                    "retrieval_layers": [
                        {"operator": "lexical_chunk_search", "required": False, "limit": 50},
                        {"operator": "vector_search", "required": False, "limit": 24},
                        {"operator": "graph_expand", "required": False, "depth": 1},
                    ],
                    "selection_policy": {"max_chunks": 24, "preserve_required_layers": True, "budgets": {"exact": 12, "lexical": 6, "vector": 6, "graph": 4}},
                    "claim_policy": {"coverage_claims_allowed": False, "negative_claims_require_exact_layer": True},
                },
                "limitations": [],
            },
            raw_response="secret raw compiler response",
        )
        result = run_thread_turn(
            repo_root=REPO_ROOT,
            data_root=data_root,
            user_input="Please retrieve the candy snack food before bed note.",
            llm_backend=RecordingLLMBackend(),
            semantic_compiler_backend=compiler_backend,
            embedding_backend=FakeEmbeddingBackend(),
        )
        synthesis_packet = _turn_artifact(result.synthesis_context_packet_path)
        self.assertNotIn("raw_response", synthesis_packet)
        self.assertNotIn("parsed_payload", synthesis_packet)
        self.assertNotIn("diagnostics", synthesis_packet)
        self.assertNotIn("metadata", synthesis_packet)

    def test_ledger_records_hashes_for_canonical_artifacts(self) -> None:
        data_root = _prepare_data_root()
        result = run_thread_turn(
            repo_root=REPO_ROOT,
            data_root=data_root,
            user_input="Please retrieve the candy snack food before bed note.",
            llm_backend=RecordingLLMBackend(),
            semantic_compiler_backend=TestSemanticCompilerBackend(),
            embedding_backend=FakeEmbeddingBackend(),
        )
        ledger = read_ledger(result.thread_ledger_path)
        self.assertEqual(len(ledger), 1)
        record = ledger[-1]
        semantic_compiler_packet = _turn_artifact(result.semantic_compiler_packet_path)
        semantic_compiler_diagnostic = _turn_artifact(result.semantic_compiler_diagnostic_path)
        semantic_traversal_manifest = _turn_artifact(result.semantic_traversal_manifest_path)
        retrieval_packet = _turn_artifact(result.retrieval_packet_path)
        coverage_report = _turn_artifact(result.coverage_report_path)
        synthesis_context_packet = _turn_artifact(result.synthesis_context_packet_path)
        state_delta = _turn_artifact(result.state_delta_path)
        self.assertEqual(record["semantic_compiler_packet_hash"], sha256_json(semantic_compiler_packet))
        self.assertEqual(record["semantic_compiler_diagnostic_hash"], sha256_json(semantic_compiler_diagnostic))
        self.assertEqual(record["semantic_traversal_manifest_hash"], sha256_json(semantic_traversal_manifest))
        self.assertEqual(record["retrieval_packet_hash"], sha256_json(retrieval_packet))
        self.assertEqual(record["coverage_report_hash"], sha256_json(coverage_report))
        self.assertEqual(record["synthesis_context_packet_hash"], sha256_json(synthesis_context_packet))
        self.assertEqual(record["state_delta_hash"], sha256_json(state_delta))

    def test_guard_against_retired_runtime_vocabulary(self) -> None:
        # Build the retired terms from pieces so the guard itself does not trip on its own source text.
        banned_terms = [
            "isolated" + " compiler packet",
            "contextual" + " compiler packet",
            "isolated_" + "semantic_" + "compiler_" + "packet",
            "contextual_" + "semantic_" + "compiler_" + "packet",
            "compiler_" + "stage_" + "diagnostics",
            "compiler_" + "stage_" + "summary",
            "semantic_" + "contract_" + "validation",
            "turn_" + "compilation_" + "packet",
            "must_" + "preserve",
            "should_" + "include",
            "avoid_" + "satisfying_" + "with",
            "coverage_" + "target",
            "activation_" + "hints",
            "semantic_" + "extraction",
            "semantic_" + "context_" + "packet",
            "compatibility_" + "target",
            "legacy_" + "semantic",
        ]
        guarded_files = [
            REPO_ROOT / "semantic_traversal" / "runtime.py",
            REPO_ROOT / "semantic_traversal" / "semantic_compiler.py",
            REPO_ROOT / "tests" / "test_ingest_runtime.py",
        ]
        for path in guarded_files:
            text = path.read_text(encoding="utf-8").lower()
            for term in banned_terms:
                self.assertNotIn(term, text, f"retired vocabulary leaked into {path.name}: {term}")


    def test_incomplete_declared_requirement_triggers_one_bounded_repair(self) -> None:
        data_root = _prepare_data_root()
        base = {
            "raw_user_input": "Where did concept X develop?",
            "intent": "development",
            "query": "development of concept X",
            "entities": [], "relations": [], "resolved_referents": [], "limitations": [],
        }
        incomplete = {
            **base,
            "planner_retrieval_plan": {
                "intent_type": "semantic_traversal", "scope_requests": [], "concepts": ["concept X"],
                "resolved_referents": [], "literal_terms": [], "evidence_requirements": ["chronology"],
                "semantic_queries": ["development of concept X"], "lexical_queries": ["development of concept X"],
                "graph_seeds": [], "retrieval_layers": [{"operator": "lexical_chunk_search", "required": False, "limit": 50}],
            },
        }
        repaired = {
            **base,
            "planner_retrieval_plan": {
                **incomplete["planner_retrieval_plan"],
                "retrieval_layers": [
                    {"operator": "lexical_chunk_search", "required": False, "limit": 50},
                    {"operator": "temporal_retrieve", "required": False, "limit": 10, "mode": "ordered"},
                ],
            },
        }
        compiler = SequenceCompilerBackend([incomplete, repaired])
        result = run_thread_turn(
            repo_root=REPO_ROOT, data_root=data_root, user_input=base["raw_user_input"],
            llm_backend=RecordingLLMBackend(), semantic_compiler_backend=compiler,
            embedding_backend=UnavailableEmbeddingBackend(),
        )
        self.assertEqual(len(compiler.calls), 2)
        self.assertIn("repair_context", compiler.calls[1])
        self.assertEqual(result.semantic_compiler_packet["planner_diagnostics"]["plan_completeness"]["status"], "complete")
        self.assertEqual(result.semantic_compiler_packet["planner_diagnostics"]["plan_repair"]["outcome"], "complete")
        self.assertEqual(result.semantic_traversal_manifest["plan_completeness"]["status"], "complete")
        self.assertTrue(any(layer["operator"] == "temporal_retrieve" for layer in result.semantic_traversal_manifest["bound_retrieval_plan"]["retrieval_layers"]))

    def test_failed_plan_repair_blocks_without_executing_retrieval(self) -> None:
        data_root = _prepare_data_root()
        payload = {
            "raw_user_input": "Where did concept X develop?",
            "intent": "development",
            "query": "development of concept X",
            "entities": [], "relations": [], "resolved_referents": [], "limitations": [],
            "planner_retrieval_plan": {
                "intent_type": "semantic_traversal", "scope_requests": [], "concepts": ["concept X"],
                "resolved_referents": [], "literal_terms": [], "evidence_requirements": ["chronology"],
                "semantic_queries": ["development of concept X"], "lexical_queries": ["development of concept X"],
                "graph_seeds": [], "retrieval_layers": [{"operator": "lexical_chunk_search", "required": False, "limit": 50}],
            },
        }
        compiler = SequenceCompilerBackend([payload, payload])
        result = run_thread_turn(
            repo_root=REPO_ROOT, data_root=data_root, user_input=payload["raw_user_input"],
            llm_backend=RecordingLLMBackend(), semantic_compiler_backend=compiler,
            embedding_backend=UnavailableEmbeddingBackend(),
        )
        self.assertEqual(len(compiler.calls), 2)
        self.assertEqual(result.coverage_report["decision"], "blocked")
        self.assertIn("compiler-declared evidence requirements are incomplete", result.coverage_report["blocking_reasons"])
        self.assertEqual(result.semantic_traversal_manifest["candidate_counts"]["lexical"], 0)
        self.assertEqual(result.semantic_traversal_manifest["execution"]["layers_executed"], [])


if __name__ == "__main__":
    unittest.main()
