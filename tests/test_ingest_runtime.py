from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

from semantic_traversal.config import load_runtime_config
from semantic_traversal.embeddings import EmbeddingResponse
from semantic_traversal.hashing import sha256_json
from semantic_traversal.ingest import IngestSourceRoot, run_ingest
from semantic_traversal.llm import StubLLMBackend
from semantic_traversal.runtime import run_thread_turn
from semantic_traversal.semantic_compiler import SemanticCompilerResponse, StubSemanticCompilerBackend
from semantic_traversal.storage import load_json, read_ledger


REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_ROOT = REPO_ROOT / "tests" / "fixtures" / "JOURNAL"


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
        return StubLLMBackend(prefix="Recorded assistant response").generate(synthesis_context_packet)


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
    temp_dir = tempfile.TemporaryDirectory()
    data_root = Path(temp_dir.name)
    _prepare_data_root._temp_dirs.append(temp_dir)  # type: ignore[attr-defined]
    run_ingest(
        repo_root=REPO_ROOT,
        data_root=data_root,
        source_roots=(IngestSourceRoot(label="tests-fixtures", path=FIXTURE_ROOT),),
        embedding_backend=FakeEmbeddingBackend(),
    )
    return data_root


_prepare_data_root._temp_dirs = []  # type: ignore[attr-defined]


def _prepare_graph_fixture_data_root() -> Path:
    temp_dir = tempfile.TemporaryDirectory()
    data_root = Path(temp_dir.name)
    _prepare_graph_fixture_data_root._temp_dirs.append(temp_dir)  # type: ignore[attr-defined]
    source_root = data_root / "graph-fixture"
    source_root.mkdir(parents=True, exist_ok=True)
    (source_root / "A.md").write_text(
        """
        # A

        Links to [[B]].

        Links to [[C|see alias]].

        Links to [[B#Sleep Section|sleep alias]].
        """.strip()
        + "\n",
        encoding="utf-8",
    )
    (source_root / "B.md").write_text(
        """
        # B

        ## Sleep Section

        B content paragraph.
        """.strip()
        + "\n",
        encoding="utf-8",
    )
    (source_root / "C.md").write_text(
        """
        # C

        C content paragraph.
        """.strip()
        + "\n",
        encoding="utf-8",
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


_prepare_graph_fixture_data_root._temp_dirs = []  # type: ignore[attr-defined]


def _turn_artifact(path: Path) -> dict[str, Any]:
    return load_json(path) or {}


def _graph_compiler_payload(raw_user_input: str, *, graph_seeds: list[str]) -> dict[str, Any]:
    return {
        "raw_user_input": raw_user_input,
        "intent": "fixture",
        "query": raw_user_input,
        "entities": [],
        "relations": [],
        "resolved_referents": [],
        "retrieval_terms": [],
        "vector_query": "",
        "graph_seeds": graph_seeds,
        "limitations": [],
    }


class ThesisRuntimeTests(unittest.TestCase):
    def test_first_turn_creates_thread_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            result = run_thread_turn(
                repo_root=REPO_ROOT,
                data_root=Path(temp_dir),
                user_input="Hello from the barebones runtime.",
                llm_backend=StubLLMBackend(),
                semantic_compiler_backend=StubSemanticCompilerBackend(),
            )
            self.assertEqual(result.turn_id, 1)
            self.assertTrue(result.conversation_thread_path.exists())
            self.assertTrue(result.thread_state_path.exists())
            self.assertTrue(result.thread_ledger_path.exists())
            self.assertTrue(result.semantic_compiler_packet_path.exists())
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
                llm_backend=StubLLMBackend(prefix="First assistant"),
                semantic_compiler_backend=StubSemanticCompilerBackend(),
            )
            second_turn = run_thread_turn(
                repo_root=REPO_ROOT,
                data_root=data_root,
                user_input="And what about that one again?",
                llm_backend=StubLLMBackend(prefix="Second assistant"),
                thread_id=first_turn.thread_id,
                semantic_compiler_backend=StubSemanticCompilerBackend(),
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
            semantic_compiler_backend=StubSemanticCompilerBackend(),
            embedding_backend=FakeEmbeddingBackend(),
        )
        self.assertEqual(result.runtime_outcome, "completed")
        self.assertEqual(result.next_thread_state["latest_turn_id"], 1)
        self.assertEqual(result.next_thread_state["active_focus"]["query"], "Please retrieve the candy snack food before bed note.")
        self.assertTrue(result.next_thread_state["active_focus"]["retrieval_terms"])
        self.assertTrue(result.next_thread_state["active_focus"]["selected_chunk_ids"])

    def test_second_turn_loads_prior_active_focus(self) -> None:
        data_root = _prepare_data_root()
        first_turn = run_thread_turn(
            repo_root=REPO_ROOT,
            data_root=data_root,
            user_input="Please retrieve the candy snack food before bed note.",
            llm_backend=RecordingLLMBackend(),
            semantic_compiler_backend=StubSemanticCompilerBackend(),
            embedding_backend=FakeEmbeddingBackend(),
        )
        second_turn = run_thread_turn(
            repo_root=REPO_ROOT,
            data_root=data_root,
            user_input="I wonder if there's anything specific about how it makes me feel?",
            llm_backend=RecordingLLMBackend(),
            thread_id=first_turn.thread_id,
            semantic_compiler_backend=StubSemanticCompilerBackend(),
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
            semantic_compiler_backend=RecordingCompilerBackend(_graph_compiler_payload("A", graph_seeds=["A"])),
            embedding_backend=UnavailableEmbeddingBackend(),
            config=config,
        )
        self.assertEqual(result.semantic_traversal_manifest["graph_traversal"]["enabled"], False)
        self.assertEqual(result.semantic_traversal_manifest["candidate_counts"]["graph"], 0)
        self.assertTrue(any("graph traversal disabled" in note for note in result.semantic_traversal_manifest["selection_notes"]))

    def test_graph_traversal_hop_limit_one_retrieves_directly_linked_note(self) -> None:
        data_root = _prepare_graph_fixture_data_root()
        config = load_runtime_config(repo_root=REPO_ROOT)
        config.raw["graph_traversal"]["hop_limit"] = 1
        result = run_thread_turn(
            repo_root=REPO_ROOT,
            data_root=data_root,
            user_input="A",
            llm_backend=RecordingLLMBackend(),
            semantic_compiler_backend=RecordingCompilerBackend(_graph_compiler_payload("A", graph_seeds=["A"])),
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
        config.raw["graph_traversal"]["hop_limit"] = 0
        result = run_thread_turn(
            repo_root=REPO_ROOT,
            data_root=data_root,
            user_input="A",
            llm_backend=RecordingLLMBackend(),
            semantic_compiler_backend=RecordingCompilerBackend(_graph_compiler_payload("A", graph_seeds=["A"])),
            embedding_backend=UnavailableEmbeddingBackend(),
            config=config,
        )
        selected_titles = [chunk["note_title"] for chunk in result.retrieval_packet["selected_chunks"]]
        self.assertIn("A", selected_titles)
        self.assertNotIn("B", selected_titles)
        self.assertNotIn("C", selected_titles)

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
        self.assertEqual(result.semantic_traversal_manifest["candidate_counts"]["graph"], len([chunk for chunk in result.retrieval_packet["selected_chunks"] if chunk["selection_reason"].startswith("graph")]))
        self.assertFalse(any("wikilink hop" in chunk["selection_reason"] for chunk in result.retrieval_packet["selected_chunks"]))

    def test_active_focus_can_supply_graph_seeds_on_referential_second_turn(self) -> None:
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
        self.assertIn("active_focus", second_turn.semantic_traversal_manifest["graph_traversal"]["seed_sources"])
        self.assertGreater(second_turn.semantic_traversal_manifest["graph_traversal"]["matched_seed_count"], 0)
        self.assertTrue(any("wikilink hop" in chunk["selection_reason"] for chunk in second_turn.retrieval_packet["selected_chunks"]))

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

    def test_referential_second_turn_augments_semantic_compiler_request_with_active_focus(self) -> None:
        data_root = _prepare_data_root()
        first_turn_payload = {
            "raw_user_input": "Please retrieve the candy snack food before bed note.",
            "intent": "fixture",
            "query": "candy snack food before bed",
            "entities": [],
            "relations": [],
            "resolved_referents": [],
            "retrieval_terms": ["candy", "snack", "food", "bed"],
            "vector_query": "candy snack food before bed",
            "graph_seeds": ["candy snack food before bed"],
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
            "retrieval_terms": ["feel"],
            "vector_query": "how does it make me feel",
            "graph_seeds": [],
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
        self.assertTrue(request_packet["active_focus"]["retrieval_terms"])
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
            semantic_compiler_backend=StubSemanticCompilerBackend(),
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
        retrieval_terms = second_turn.semantic_compiler_packet["retrieval_terms"]
        self.assertIn("candy", retrieval_terms)
        self.assertIn("bed", retrieval_terms)
        self.assertIn("candy snack food before bed", second_turn.semantic_compiler_packet["vector_query"])
        for junk_term in ("raw_user_input", "assistant_response_snippet", "selected_chunk_ids", "selected_note_titles", "{"):
            self.assertNotIn(junk_term, retrieval_terms)

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
                semantic_compiler_backend=StubSemanticCompilerBackend(),
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
            semantic_compiler_backend=StubSemanticCompilerBackend(),
            embedding_backend=FakeEmbeddingBackend(),
        )
        second_turn = run_thread_turn(
            repo_root=REPO_ROOT,
            data_root=data_root,
            user_input="How does it make me feel?",
            llm_backend=RecordingLLMBackend(),
            thread_id=first_turn.thread_id,
            semantic_compiler_backend=StubSemanticCompilerBackend(),
            embedding_backend=FakeEmbeddingBackend(),
        )
        synthesis_packet = _turn_artifact(second_turn.synthesis_context_packet_path)
        self.assertEqual(synthesis_packet["prior_thread_state"]["active_focus"]["query"], first_turn.next_thread_state["active_focus"]["query"])
        self.assertTrue(synthesis_packet["prior_thread_state"]["recent_semantic_turns"])
        self.assertNotIn("raw_response", synthesis_packet)

    def test_compiler_packet_preserves_raw_user_input(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            user_input = "Please retrieve the candy snack food before bed note."
            result = run_thread_turn(
                repo_root=REPO_ROOT,
                data_root=Path(temp_dir),
                user_input=user_input,
                llm_backend=StubLLMBackend(),
                semantic_compiler_backend=StubSemanticCompilerBackend(),
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
                llm_backend=StubLLMBackend(),
                semantic_compiler_backend=ExplodingCompilerBackend(),
            )
            self.assertEqual(result.semantic_compiler_status, "fallback")
            self.assertIn("deterministic lexical fallback used", result.semantic_compiler_packet["limitations"][0])

    def test_lexical_traversal_retrieves_ingested_fixture(self) -> None:
        data_root = _prepare_data_root()
        result = run_thread_turn(
            repo_root=REPO_ROOT,
            data_root=data_root,
            user_input="Please retrieve the candy snack food before bed note.",
            llm_backend=RecordingLLMBackend(),
            semantic_compiler_backend=StubSemanticCompilerBackend(),
            embedding_backend=FakeEmbeddingBackend(),
        )
        self.assertEqual(result.coverage_report["decision"], "approved")
        self.assertGreater(result.retrieval_packet["matched_chunk_count"], 0)
        self.assertTrue(any(chunk["source_root_label"] == "tests-fixtures" for chunk in result.retrieval_packet["selected_chunks"]))
        self.assertTrue(result.retrieval_packet["selected_chunks"][0]["selection_reason"])

    def test_retrieval_packet_contains_provenance(self) -> None:
        data_root = _prepare_data_root()
        result = run_thread_turn(
            repo_root=REPO_ROOT,
            data_root=data_root,
            user_input="Please retrieve the candy snack food before bed note.",
            llm_backend=RecordingLLMBackend(),
            semantic_compiler_backend=StubSemanticCompilerBackend(),
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
            "selection_reason",
        ):
            self.assertIn(field, chunk)

    def test_approved_coverage_calls_llm(self) -> None:
        data_root = _prepare_data_root()
        llm_backend = RecordingLLMBackend()
        result = run_thread_turn(
            repo_root=REPO_ROOT,
            data_root=data_root,
            user_input="Please retrieve the candy snack food before bed note.",
            llm_backend=llm_backend,
            semantic_compiler_backend=StubSemanticCompilerBackend(),
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
                semantic_compiler_backend=StubSemanticCompilerBackend(),
            )
        self.assertEqual(result.runtime_outcome, "blocked")
        self.assertEqual(len(llm_backend.calls), 0)
        self.assertIsNone(result.assistant_response)

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
                "retrieval_terms": ["candy", "snack", "food", "bed"],
                "vector_query": "candy snack food before bed",
                "graph_seeds": ["candy snack food before bed"],
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
            semantic_compiler_backend=StubSemanticCompilerBackend(),
            embedding_backend=FakeEmbeddingBackend(),
        )
        ledger = read_ledger(result.thread_ledger_path)
        self.assertEqual(len(ledger), 1)
        record = ledger[-1]
        semantic_compiler_packet = _turn_artifact(result.semantic_compiler_packet_path)
        semantic_traversal_manifest = _turn_artifact(result.semantic_traversal_manifest_path)
        retrieval_packet = _turn_artifact(result.retrieval_packet_path)
        coverage_report = _turn_artifact(result.coverage_report_path)
        synthesis_context_packet = _turn_artifact(result.synthesis_context_packet_path)
        state_delta = _turn_artifact(result.state_delta_path)
        self.assertEqual(record["semantic_compiler_packet_hash"], sha256_json(semantic_compiler_packet))
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


if __name__ == "__main__":
    unittest.main()
