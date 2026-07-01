from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

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


def _turn_artifact(path: Path) -> dict[str, Any]:
    return load_json(path) or {}


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
