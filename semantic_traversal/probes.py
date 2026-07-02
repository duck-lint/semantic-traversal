from __future__ import annotations

import argparse
import tempfile
from pathlib import Path
from typing import Any

from .ingest import IngestSourceRoot, run_ingest
from .llm import LLMResponse
from .runtime import run_thread_turn
from .semantic_compiler import SemanticCompilerResponse, collect_compiler_terms
from .storage import load_json, read_ledger


REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_ROOT = REPO_ROOT / "tests" / "fixtures" / "JOURNAL"


class ProbeLLMBackend:
    mode_name = "probe"

    def __init__(self, prefix: str = "Probe assistant response") -> None:
        self._prefix = prefix

    def generate(self, synthesis_context_packet: dict[str, Any]) -> LLMResponse:
        user_input = synthesis_context_packet["raw_user_input"]
        turn_id = synthesis_context_packet["turn_id"]
        return LLMResponse(
            assistant_response=f"{self._prefix} for turn {turn_id}: {user_input}",
            metadata={"mode": self.mode_name, "provider": "probe", "model": "probe"},
        )


class ProbeSemanticCompilerBackend:
    mode_name = "probe"

    def compile_turn(self, packet: dict[str, Any]) -> SemanticCompilerResponse:
        raw_user_input = str(packet.get("raw_user_input") or "")
        query = raw_user_input.strip()
        terms = collect_compiler_terms(raw_user_input)
        return SemanticCompilerResponse(
            parsed_payload={
                "raw_user_input": raw_user_input,
                "intent": "probe semantic compiler output",
                "query": query,
                "entities": [],
                "relations": [],
                "resolved_referents": [],
                "retrieval_terms": terms,
                "vector_query": query,
                "graph_seeds": [query] if terms else [],
                "limitations": ["probe compiler backend used"],
            },
            raw_response=None,
            metadata={"backend_mode": self.mode_name},
            diagnostics={},
            status="parsed",
        )


class FakeEmbeddingBackend:
    mode_name = "fake"

    def embed_texts(self, texts: list[str]):
        from .embeddings import EmbeddingResponse

        vectors = [[float(len(text)), float(text.lower().count("candy")), float(text.lower().count("bed"))] for text in texts]
        return EmbeddingResponse(vectors=vectors, metadata={"backend_mode": self.mode_name}, status="embedded")

    def embed_query_text(self, text: str):
        return self.embed_texts([text])


def _default_probe_root() -> Path:
    return Path(tempfile.gettempdir()) / "semantic-traversal-probes"


def _ensure_fixture_ingest(data_root: Path) -> None:
    run_ingest(
        repo_root=REPO_ROOT,
        data_root=data_root,
        source_roots=(IngestSourceRoot(label="tests-fixtures", path=FIXTURE_ROOT),),
        embedding_backend=FakeEmbeddingBackend(),
    )


def probe_new_thread_minimal_turn(data_root: Path, llm_backend: Any | None = None) -> dict[str, Any]:
    backend = llm_backend or ProbeLLMBackend(prefix="Probe assistant response")
    result = run_thread_turn(
        repo_root=REPO_ROOT,
        data_root=data_root,
        user_input="Start a new thread and answer minimally.",
        llm_backend=backend,
        semantic_compiler_backend=ProbeSemanticCompilerBackend(),
    )
    thread_document = load_json(result.conversation_thread_path)
    thread_state = load_json(result.thread_state_path)
    ledger_records = read_ledger(result.thread_ledger_path)
    assert thread_document is not None, "conversation_thread.json was not created"
    assert thread_state is not None, "thread_state.json was not created"
    assert len(ledger_records) == 1, "expected exactly one ledger record"
    assert thread_document["thread_id"] == result.thread_id
    assert thread_state["latest_turn_id"] == 1
    return {
        "probe": "probe_new_thread_minimal_turn",
        "status": "pass",
        "runtime_outcome": result.runtime_outcome,
        "thread_id": result.thread_id,
        "ledger_count": len(ledger_records),
        "llm_mode": result.llm_metadata.get("mode"),
        "latest_perturbation_hash": ledger_records[0]["state_perturbation_hash"],
    }


def probe_same_thread_continuation_turn(data_root: Path, llm_backend: Any | None = None) -> dict[str, Any]:
    backend = llm_backend or ProbeLLMBackend(prefix="Probe assistant response")
    first_turn = run_thread_turn(
        repo_root=REPO_ROOT,
        data_root=data_root,
        user_input="First turn for continuation probe.",
        llm_backend=backend,
        semantic_compiler_backend=ProbeSemanticCompilerBackend(),
    )
    before_records = read_ledger(first_turn.thread_ledger_path)
    second_turn = run_thread_turn(
        repo_root=REPO_ROOT,
        data_root=data_root,
        user_input="Second turn should continue the same thread.",
        llm_backend=backend,
        thread_id=first_turn.thread_id,
        semantic_compiler_backend=ProbeSemanticCompilerBackend(),
    )
    after_records = read_ledger(second_turn.thread_ledger_path)
    assert len(after_records) == len(before_records) + 1, "expected exactly one new ledger record"
    assert after_records[-1]["parent_perturbation_hash"] == before_records[-1]["state_perturbation_hash"]
    assert second_turn.prior_thread_state["latest_turn_id"] == 1
    assert second_turn.next_thread_state["latest_turn_id"] == 2
    return {
        "probe": "probe_same_thread_continuation_turn",
        "status": "pass",
        "runtime_outcome": second_turn.runtime_outcome,
        "thread_id": first_turn.thread_id,
        "ledger_count_before": len(before_records),
        "ledger_count_after": len(after_records),
        "parent_hash": after_records[-1]["parent_perturbation_hash"],
        "previous_hash": before_records[-1]["state_perturbation_hash"],
        "llm_mode": second_turn.llm_metadata.get("mode"),
    }


def probe_fixture_lexical_retrieval_hit(data_root: Path) -> dict[str, Any]:
    _ensure_fixture_ingest(data_root)
    result = run_thread_turn(
        repo_root=REPO_ROOT,
        data_root=data_root,
        user_input="Please retrieve the candy snack food before bed note.",
        llm_backend=ProbeLLMBackend(prefix="Probe assistant response"),
        semantic_compiler_backend=ProbeSemanticCompilerBackend(),
        embedding_backend=FakeEmbeddingBackend(),
    )
    assert result.coverage_report["decision"] == "approved"
    assert result.retrieval_packet["selected_chunks"], "expected at least one retrieval hit"
    return {
        "probe": "probe_fixture_lexical_retrieval_hit",
        "status": "pass",
        "runtime_outcome": result.runtime_outcome,
        "turn_id": result.turn_id,
        "coverage_decision": result.coverage_report["decision"],
        "selected_chunk_ids": [chunk["chunk_id"] for chunk in result.retrieval_packet["selected_chunks"]],
    }


def build_probe_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run tiny semantic-traversal probes.")
    parser.add_argument("--data-root", default=str(_default_probe_root()))
    parser.add_argument("probe", choices=("new-thread", "continue-thread", "fixture-lexical-hit"))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_probe_parser().parse_args(argv)
    data_root = Path(args.data_root).resolve()
    if args.probe == "new-thread":
        payload = probe_new_thread_minimal_turn(data_root=data_root)
    elif args.probe == "continue-thread":
        payload = probe_same_thread_continuation_turn(data_root=data_root)
    else:
        payload = probe_fixture_lexical_retrieval_hit(data_root=data_root)
    print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
