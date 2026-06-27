from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
import sys
from pathlib import Path
from typing import Any

from .hashing import sha256_json
from .ingest import build_default_source_roots, run_ingest
from .llm import StubLLMBackend, resolve_llm_backend
from .runtime import run_thread_turn
from .semantic_extraction import DisabledSemanticExtractorBackend, StubSemanticExtractorBackend
from .storage import load_json, read_ledger


def _default_probe_root() -> Path:
    return Path(tempfile.gettempdir()) / "semantic-traversal-probes"


def _load_manifest(path: Path) -> dict[str, Any]:
    payload = load_json(path)
    assert isinstance(payload, dict), f"expected JSON object manifest at {path}"
    return payload


def probe_new_thread_minimal_turn(data_root: Path, llm_backend: Any | None = None) -> dict[str, Any]:
    backend = llm_backend or StubLLMBackend(prefix="Probe stub response")
    result = run_thread_turn(
        repo_root=Path(".").resolve(),
        data_root=data_root,
        user_input="Start a new thread and answer minimally.",
        llm_backend=backend,
    )
    thread_document = load_json(result.conversation_thread_path)
    thread_state = load_json(result.thread_state_path)
    ledger_records = read_ledger(result.thread_ledger_path)
    assert thread_document is not None, "conversation_thread.json was not created"
    assert thread_state is not None, "thread_state.json was not created"
    assert len(ledger_records) == 1, "expected exactly one ledger record"
    assert ledger_records[0]["parent_perturbation_hash"] is None, "expected a root ledger record"
    assert thread_document["thread_id"] == result.thread_id
    assert thread_document["ledger_record_count"] == 1
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
    backend = llm_backend or StubLLMBackend(prefix="Probe stub response")
    first_turn = run_thread_turn(
        repo_root=Path(".").resolve(),
        data_root=data_root,
        user_input="First turn for continuation probe.",
        llm_backend=backend,
    )
    before_records = read_ledger(first_turn.thread_ledger_path)
    second_turn = run_thread_turn(
        repo_root=Path(".").resolve(),
        data_root=data_root,
        user_input="Second turn should continue the same thread.",
        llm_backend=backend,
        thread_id=first_turn.thread_id,
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


def probe_fixture_journal_section_paragraph_chunking(data_root: Path, repo_root: Path | None = None) -> dict[str, Any]:
    resolved_repo_root = (repo_root or Path(".")).resolve()
    result = run_ingest(repo_root=resolved_repo_root, data_root=data_root, source_roots=build_default_source_roots(resolved_repo_root))
    manifest = _load_manifest(result.manifest_path)
    chunks = [
        chunk
        for chunk in manifest["chunks"]
        if chunk["source_root_label"] == "tests-fixtures" and chunk["relative_path"] == "JOURNAL/2025-09/01_Monday.md"
    ]
    assert chunks, "expected fixture journal chunks"
    labels = {chunk["section_label"] for chunk in chunks}
    assert "Fixture Alpha Section" in labels
    assert "Fixture Beta Section" in labels
    assert "Fixture Multi Paragraph Section" in labels
    assert "September 01, 2025" not in labels
    return {
        "probe": "probe_fixture_journal_section_paragraph_chunking",
        "status": "pass",
        "chunk_count": len(chunks),
        "labels": sorted(labels),
        "manifest_path": str(result.manifest_path),
    }


def probe_repo_corpus_journal_heading_section_resolution(data_root: Path, repo_root: Path | None = None) -> dict[str, Any]:
    resolved_repo_root = (repo_root or Path(".")).resolve()
    result = run_ingest(repo_root=resolved_repo_root, data_root=data_root, source_roots=build_default_source_roots(resolved_repo_root))
    manifest = _load_manifest(result.manifest_path)
    chunks = [
        chunk
        for chunk in manifest["chunks"]
        if chunk["source_root_label"] == "corpus"
        and chunk["relative_path"] == "LAYER-1 PILLARS/PILLAR 2-DYNAMIC COHERENCE/JOURNAL/2025/2025-08/24_Sunday.md"
    ]
    assert chunks, "expected corpus journal chunks"
    labels = {chunk["section_label"] for chunk in chunks}
    assert "Dream Motif" in labels
    assert "Y-Day Review" in labels
    assert "Dream recall" in labels
    assert "Yesterday" in labels
    return {
        "probe": "probe_repo_corpus_journal_heading_section_resolution",
        "status": "pass",
        "chunk_count": len(chunks),
        "labels": sorted(labels),
        "manifest_path": str(result.manifest_path),
    }


def probe_sqlite_manifest_materialization(data_root: Path, repo_root: Path | None = None) -> dict[str, Any]:
    resolved_repo_root = (repo_root or Path(".")).resolve()
    result = run_ingest(repo_root=resolved_repo_root, data_root=data_root, source_roots=build_default_source_roots(resolved_repo_root))
    manifest = _load_manifest(result.manifest_path)
    assert result.database_path.exists(), "expected SQLite database to exist"
    assert manifest["database_path"] == str(result.database_path)
    return {
        "probe": "probe_sqlite_manifest_materialization",
        "status": "pass",
        "database_path": str(result.database_path),
        "manifest_path": str(result.manifest_path),
        "chunk_count": result.chunk_count,
    }


def probe_lexical_retrieval_fixture_hit(data_root: Path, repo_root: Path | None = None) -> dict[str, Any]:
    resolved_repo_root = (repo_root or Path(".")).resolve()
    run_ingest(repo_root=resolved_repo_root, data_root=data_root, source_roots=build_default_source_roots(resolved_repo_root))
    result = run_thread_turn(
        repo_root=resolved_repo_root,
        data_root=data_root,
        user_input="Please retrieve the candy snack food before bed note.",
        llm_backend=StubLLMBackend(prefix="Probe stub response"),
    )
    assert result.coverage_report["decision"] == "blocked", "expected blocked runtime coverage decision"
    assert result.retrieval_packet["selected_chunks"], "expected at least one retrieval hit"
    assert any(chunk["source_root_label"] == "tests-fixtures" for chunk in result.retrieval_packet["selected_chunks"])
    assert result.synthesis_context_packet["approved_retrieval_packet"] is None
    return {
        "probe": "probe_lexical_retrieval_fixture_hit",
        "status": "pass",
        "runtime_outcome": result.runtime_outcome,
        "turn_id": result.turn_id,
        "coverage_decision": result.coverage_report["decision"],
        "selected_chunk_ids": [chunk["chunk_id"] for chunk in result.retrieval_packet["selected_chunks"]],
    }


def probe_lexical_retrieval_no_index(data_root: Path, repo_root: Path | None = None) -> dict[str, Any]:
    resolved_repo_root = (repo_root or Path(".")).resolve()
    result = run_thread_turn(
        repo_root=resolved_repo_root,
        data_root=data_root,
        user_input="Dream Recall with no ingestion index present.",
        llm_backend=StubLLMBackend(prefix="Probe stub response"),
    )
    assert result.coverage_report["decision"] == "blocked", "expected blocked coverage decision"
    assert result.retrieval_packet["selected_chunks"] == [], "expected an empty retrieval packet"
    return {
        "probe": "probe_lexical_retrieval_no_index",
        "status": "pass",
        "runtime_outcome": result.runtime_outcome,
        "turn_id": result.turn_id,
        "coverage_decision": result.coverage_report["decision"],
    }


def probe_lexical_retrieval_no_match(data_root: Path, repo_root: Path | None = None) -> dict[str, Any]:
    resolved_repo_root = (repo_root or Path(".")).resolve()
    run_ingest(repo_root=resolved_repo_root, data_root=data_root, source_roots=build_default_source_roots(resolved_repo_root))
    result = run_thread_turn(
        repo_root=resolved_repo_root,
        data_root=data_root,
        user_input="qzxyv qzxyv qzxyv",
        llm_backend=StubLLMBackend(prefix="Probe stub response"),
    )
    assert result.coverage_report["decision"] == "blocked", "expected blocked coverage decision"
    assert result.retrieval_packet["selected_chunks"] == [], "expected an empty retrieval packet"
    return {
        "probe": "probe_lexical_retrieval_no_match",
        "status": "pass",
        "runtime_outcome": result.runtime_outcome,
        "turn_id": result.turn_id,
        "coverage_decision": result.coverage_report["decision"],
    }


def probe_lexical_retrieval_no_query_terms(data_root: Path, repo_root: Path | None = None) -> dict[str, Any]:
    resolved_repo_root = (repo_root or Path(".")).resolve()
    run_ingest(repo_root=resolved_repo_root, data_root=data_root, source_roots=build_default_source_roots(resolved_repo_root))
    result = run_thread_turn(
        repo_root=resolved_repo_root,
        data_root=data_root,
        user_input="   and the or   ",
        llm_backend=StubLLMBackend(prefix="Probe stub response"),
        semantic_extractor_backend=DisabledSemanticExtractorBackend(),
    )
    assert result.coverage_report["decision"] == "blocked", "expected blocked coverage decision"
    assert result.retrieval_packet["retrieval_observation"] == "no_query_terms"
    assert result.retrieval_packet["selected_chunks"] == [], "expected an empty retrieval packet"
    return {
        "probe": "probe_lexical_retrieval_no_query_terms",
        "status": "pass",
        "runtime_outcome": result.runtime_outcome,
        "turn_id": result.turn_id,
        "coverage_decision": result.coverage_report["decision"],
        "query_terms": result.semantic_context_packet["extracted_lexical_query_terms"],
    }


def probe_ledger_hash_artifact_integrity(data_root: Path, repo_root: Path | None = None) -> dict[str, Any]:
    resolved_repo_root = (repo_root or Path(".")).resolve()
    run_ingest(repo_root=resolved_repo_root, data_root=data_root, source_roots=build_default_source_roots(resolved_repo_root))
    result = run_thread_turn(
        repo_root=resolved_repo_root,
        data_root=data_root,
        user_input="Please retrieve the candy snack food before bed note.",
        llm_backend=StubLLMBackend(prefix="Probe stub response"),
    )
    isolated_packet = _load_manifest(result.isolated_semantic_extraction_packet_path)
    isolated_raw = _load_manifest(result.isolated_semantic_extraction_raw_path)
    contextual_packet = _load_manifest(result.contextual_semantic_extraction_packet_path)
    contextual_raw = _load_manifest(result.contextual_semantic_extraction_raw_path)
    semantic_context_packet = _load_manifest(result.semantic_context_packet_path)
    semantic_traversal_manifest = _load_manifest(result.semantic_traversal_manifest_path)
    retrieval_packet = _load_manifest(result.retrieval_packet_path)
    coverage_report = _load_manifest(result.coverage_report_path)
    synthesis_context_packet = _load_manifest(result.synthesis_context_packet_path)
    state_delta = _load_manifest(result.state_delta_path)
    thread_state = _load_manifest(result.thread_state_path)
    ledger_records = read_ledger(result.thread_ledger_path)
    ledger_record = ledger_records[-1]
    assert ledger_record["isolated_semantic_extraction_packet_hash"] == sha256_json(isolated_packet)
    assert ledger_record["isolated_semantic_extraction_raw_hash"] == sha256_json(isolated_raw)
    assert ledger_record["contextual_semantic_extraction_packet_hash"] == sha256_json(contextual_packet)
    assert ledger_record["contextual_semantic_extraction_raw_hash"] == sha256_json(contextual_raw)
    assert ledger_record["semantic_context_packet_hash"] == sha256_json(semantic_context_packet)
    assert ledger_record["semantic_traversal_manifest_hash"] == sha256_json(semantic_traversal_manifest)
    assert ledger_record["retrieval_packet_hash"] == sha256_json(retrieval_packet)
    assert ledger_record["coverage_report_hash"] == sha256_json(coverage_report)
    assert ledger_record["synthesis_context_packet_hash"] == sha256_json(synthesis_context_packet)
    thread_state_without_hash = dict(thread_state)
    thread_state_without_hash.pop("latest_thread_state_hash", None)
    assert ledger_record["next_thread_state_hash"] == sha256_json(thread_state_without_hash)
    assert ledger_record["state_delta_hash"] == sha256_json(state_delta)
    return {
        "probe": "probe_ledger_hash_artifact_integrity",
        "status": "pass",
        "runtime_outcome": result.runtime_outcome,
        "turn_id": result.turn_id,
        "coverage_decision": result.coverage_report["decision"],
        "ledger_hashes": {
            "isolated_semantic_extraction_packet_hash": ledger_record["isolated_semantic_extraction_packet_hash"],
            "isolated_semantic_extraction_raw_hash": ledger_record["isolated_semantic_extraction_raw_hash"],
            "contextual_semantic_extraction_packet_hash": ledger_record["contextual_semantic_extraction_packet_hash"],
            "contextual_semantic_extraction_raw_hash": ledger_record["contextual_semantic_extraction_raw_hash"],
            "semantic_context_packet_hash": ledger_record["semantic_context_packet_hash"],
            "semantic_traversal_manifest_hash": ledger_record["semantic_traversal_manifest_hash"],
            "retrieval_packet_hash": ledger_record["retrieval_packet_hash"],
            "coverage_report_hash": ledger_record["coverage_report_hash"],
            "synthesis_context_packet_hash": ledger_record["synthesis_context_packet_hash"],
            "state_delta_hash": ledger_record["state_delta_hash"],
            "next_thread_state_hash": ledger_record["next_thread_state_hash"],
        },
    }


def probe_turn_cli_artifact_paths(data_root: Path, repo_root: Path | None = None) -> dict[str, Any]:
    resolved_repo_root = (repo_root or Path(".")).resolve()
    workspace_root = Path(__file__).resolve().parent.parent
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "semantic_traversal",
            "--message",
            "Please retrieve the candy snack food before bed note.",
            "--llm-mode",
            "stub",
            "--repo-root",
            str(resolved_repo_root),
            "--data-root",
            str(data_root),
        ],
        cwd=workspace_root,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 1, "expected blocked normal CLI exit"
    payload = json.loads(completed.stdout)
    artifact_paths = {
        key: Path(payload[key])
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
        )
    }
    for path in artifact_paths.values():
        assert path.exists(), f"expected CLI artifact path to exist: {path}"
    return {
        "probe": "probe_turn_cli_artifact_paths",
        "status": "pass",
        "runtime_outcome": payload["runtime_outcome"],
        "coverage_decision": payload["coverage_decision"],
        "isolated_extraction_status": payload["isolated_extraction_status"],
        "contextual_extraction_status": payload["contextual_extraction_status"],
        "artifact_paths": {key: str(path) for key, path in artifact_paths.items()},
        "latest_perturbation_hash": payload["latest_perturbation_hash"],
        "latest_thread_state_hash": payload["latest_thread_state_hash"],
    }


def probe_semantic_extraction_stub_packets(data_root: Path, repo_root: Path | None = None) -> dict[str, Any]:
    resolved_repo_root = (repo_root or Path(".")).resolve()
    run_ingest(repo_root=resolved_repo_root, data_root=data_root, source_roots=build_default_source_roots(resolved_repo_root))
    result = run_thread_turn(
        repo_root=resolved_repo_root,
        data_root=data_root,
        user_input="Please retrieve the candy snack food before bed note.",
        llm_backend=StubLLMBackend(prefix="Probe stub response"),
        semantic_extractor_backend=StubSemanticExtractorBackend(),
    )
    isolated_packet = _load_manifest(result.isolated_semantic_extraction_packet_path)
    contextual_packet = _load_manifest(result.contextual_semantic_extraction_packet_path)
    assert isolated_packet["status"] == "stub"
    assert contextual_packet["status"] == "stub"
    assert isolated_packet["raw_user_input"] == "Please retrieve the candy snack food before bed note."
    assert contextual_packet["raw_user_input"] == "Please retrieve the candy snack food before bed note."
    return {
        "probe": "probe_semantic_extraction_stub_packets",
        "status": "pass",
        "isolated_status": isolated_packet["status"],
        "contextual_status": contextual_packet["status"],
    }


def probe_blocked_runtime_with_disabled_extraction(data_root: Path, repo_root: Path | None = None) -> dict[str, Any]:
    resolved_repo_root = (repo_root or Path(".")).resolve()
    run_ingest(repo_root=resolved_repo_root, data_root=data_root, source_roots=build_default_source_roots(resolved_repo_root))
    result = run_thread_turn(
        repo_root=resolved_repo_root,
        data_root=data_root,
        user_input="Please retrieve the candy snack food before bed note.",
        llm_backend=StubLLMBackend(prefix="Probe stub response"),
        semantic_extractor_backend=DisabledSemanticExtractorBackend(),
    )
    assert result.isolated_semantic_extraction_packet["status"] == "disabled"
    assert result.contextual_semantic_extraction_packet["status"] == "disabled"
    assert result.coverage_report["decision"] == "blocked"
    assert result.retrieval_packet["selected_chunks"]
    return {
        "probe": "probe_blocked_runtime_with_disabled_extraction",
        "status": "pass",
        "runtime_outcome": result.runtime_outcome,
        "coverage_decision": result.coverage_report["decision"],
        "isolated_status": result.isolated_semantic_extraction_packet["status"],
        "contextual_status": result.contextual_semantic_extraction_packet["status"],
    }


def probe_semantic_extraction_contextual_thread_state(data_root: Path, repo_root: Path | None = None) -> dict[str, Any]:
    resolved_repo_root = (repo_root or Path(".")).resolve()
    first_turn = run_thread_turn(
        repo_root=resolved_repo_root,
        data_root=data_root,
        user_input="First turn to seed thread state.",
        llm_backend=StubLLMBackend(prefix="Probe stub response"),
        semantic_extractor_backend=StubSemanticExtractorBackend(),
    )
    second_turn = run_thread_turn(
        repo_root=resolved_repo_root,
        data_root=data_root,
        user_input="Second turn should receive prior thread state.",
        llm_backend=StubLLMBackend(prefix="Probe stub response"),
        thread_id=first_turn.thread_id,
        semantic_extractor_backend=StubSemanticExtractorBackend(),
    )
    contextual_packet = _load_manifest(second_turn.contextual_semantic_extraction_packet_path)
    prior_thread_state = contextual_packet["request_packet"]["prior_thread_state"]
    assert prior_thread_state["latest_turn_id"] == 1
    assert prior_thread_state["latest_user_input"] == "First turn to seed thread state."
    return {
        "probe": "probe_semantic_extraction_contextual_thread_state",
        "status": "pass",
        "thread_id": first_turn.thread_id,
        "prior_latest_turn_id": prior_thread_state["latest_turn_id"],
    }


def probe_semantic_extraction_hash_integrity(data_root: Path, repo_root: Path | None = None) -> dict[str, Any]:
    resolved_repo_root = (repo_root or Path(".")).resolve()
    run_ingest(repo_root=resolved_repo_root, data_root=data_root, source_roots=build_default_source_roots(resolved_repo_root))
    result = run_thread_turn(
        repo_root=resolved_repo_root,
        data_root=data_root,
        user_input="Please retrieve the candy snack food before bed note.",
        llm_backend=StubLLMBackend(prefix="Probe stub response"),
        semantic_extractor_backend=StubSemanticExtractorBackend(),
    )
    ledger_record = read_ledger(result.thread_ledger_path)[-1]
    assert ledger_record["isolated_semantic_extraction_packet_hash"] == sha256_json(_load_manifest(result.isolated_semantic_extraction_packet_path))
    assert ledger_record["contextual_semantic_extraction_packet_hash"] == sha256_json(_load_manifest(result.contextual_semantic_extraction_packet_path))
    assert ledger_record["isolated_semantic_extraction_raw_hash"] == sha256_json(_load_manifest(result.isolated_semantic_extraction_raw_path))
    assert ledger_record["contextual_semantic_extraction_raw_hash"] == sha256_json(_load_manifest(result.contextual_semantic_extraction_raw_path))
    return {
        "probe": "probe_semantic_extraction_hash_integrity",
        "status": "pass",
        "runtime_outcome": result.runtime_outcome,
        "isolated_packet_hash": ledger_record["isolated_semantic_extraction_packet_hash"],
        "contextual_packet_hash": ledger_record["contextual_semantic_extraction_packet_hash"],
    }


def probe_blocked_runtime_with_stub_extraction(data_root: Path, repo_root: Path | None = None) -> dict[str, Any]:
    resolved_repo_root = (repo_root or Path(".")).resolve()
    run_ingest(repo_root=resolved_repo_root, data_root=data_root, source_roots=build_default_source_roots(resolved_repo_root))
    result = run_thread_turn(
        repo_root=resolved_repo_root,
        data_root=data_root,
        user_input="Please retrieve the candy snack food before bed note.",
        llm_backend=StubLLMBackend(prefix="Probe stub response"),
        semantic_extractor_backend=StubSemanticExtractorBackend(),
    )
    assert result.isolated_semantic_extraction_packet["status"] == "stub"
    assert result.contextual_semantic_extraction_packet["status"] == "stub"
    assert result.llm_metadata["mode"] == "not_called"
    for path in (
        result.isolated_semantic_extraction_packet_path,
        result.contextual_semantic_extraction_packet_path,
        result.semantic_context_packet_path,
        result.retrieval_packet_path,
        result.synthesis_context_packet_path,
        result.state_delta_path,
    ):
        assert path.exists(), f"expected full-route stub artifact to exist: {path}"
    assert result.coverage_report["decision"] == "blocked"
    assert result.ledger_record["isolated_semantic_extraction_packet_hash"]
    assert result.ledger_record["contextual_semantic_extraction_packet_hash"]
    return {
        "probe": "probe_blocked_runtime_with_stub_extraction",
        "status": "pass",
        "llm_mode": result.llm_metadata["mode"],
        "runtime_outcome": result.runtime_outcome,
        "isolated_status": result.isolated_semantic_extraction_packet["status"],
        "contextual_status": result.contextual_semantic_extraction_packet["status"],
        "coverage_decision": result.coverage_report["decision"],
        "turn_root": str(result.turn_root),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the named semantic-traversal first-target probes.")
    parser.add_argument(
        "probe",
        choices=(
            "probe_new_thread_minimal_turn",
            "probe_same_thread_continuation_turn",
            "probe_fixture_journal_section_paragraph_chunking",
            "probe_repo_corpus_journal_heading_section_resolution",
            "probe_sqlite_manifest_materialization",
            "probe_lexical_retrieval_fixture_hit",
            "probe_lexical_retrieval_no_index",
            "probe_lexical_retrieval_no_match",
            "probe_lexical_retrieval_no_query_terms",
            "probe_ledger_hash_artifact_integrity",
            "probe_turn_cli_artifact_paths",
            "probe_semantic_extraction_stub_packets",
            "probe_blocked_runtime_with_disabled_extraction",
            "probe_semantic_extraction_contextual_thread_state",
            "probe_semantic_extraction_hash_integrity",
            "probe_blocked_runtime_with_stub_extraction",
        ),
    )
    parser.add_argument("--data-root", default=str(_default_probe_root()))
    parser.add_argument("--llm-mode", choices=("auto", "live", "stub"), default="stub")
    parser.add_argument("--model")
    parser.add_argument("--repo-root", default=".")
    args = parser.parse_args()

    data_root = Path(args.data_root).resolve()
    resolved_repo_root = Path(args.repo_root).resolve()
    if args.llm_mode == "stub":
        backend = StubLLMBackend(prefix="Probe stub response")
    else:
        backend = resolve_llm_backend(repo_root=resolved_repo_root, llm_mode=args.llm_mode, model_override=args.model)

    if args.probe == "probe_new_thread_minimal_turn":
        payload = probe_new_thread_minimal_turn(data_root=data_root, llm_backend=backend)
    elif args.probe == "probe_same_thread_continuation_turn":
        payload = probe_same_thread_continuation_turn(data_root=data_root, llm_backend=backend)
    elif args.probe == "probe_fixture_journal_section_paragraph_chunking":
        payload = probe_fixture_journal_section_paragraph_chunking(data_root=data_root, repo_root=resolved_repo_root)
    elif args.probe == "probe_repo_corpus_journal_heading_section_resolution":
        payload = probe_repo_corpus_journal_heading_section_resolution(data_root=data_root, repo_root=resolved_repo_root)
    elif args.probe == "probe_sqlite_manifest_materialization":
        payload = probe_sqlite_manifest_materialization(data_root=data_root, repo_root=resolved_repo_root)
    elif args.probe == "probe_lexical_retrieval_fixture_hit":
        payload = probe_lexical_retrieval_fixture_hit(data_root=data_root, repo_root=resolved_repo_root)
    elif args.probe == "probe_lexical_retrieval_no_index":
        payload = probe_lexical_retrieval_no_index(data_root=data_root, repo_root=resolved_repo_root)
    elif args.probe == "probe_lexical_retrieval_no_match":
        payload = probe_lexical_retrieval_no_match(data_root=data_root, repo_root=resolved_repo_root)
    elif args.probe == "probe_lexical_retrieval_no_query_terms":
        payload = probe_lexical_retrieval_no_query_terms(data_root=data_root, repo_root=resolved_repo_root)
    elif args.probe == "probe_ledger_hash_artifact_integrity":
        payload = probe_ledger_hash_artifact_integrity(data_root=data_root, repo_root=resolved_repo_root)
    elif args.probe == "probe_turn_cli_artifact_paths":
        payload = probe_turn_cli_artifact_paths(data_root=data_root, repo_root=resolved_repo_root)
    elif args.probe == "probe_semantic_extraction_stub_packets":
        payload = probe_semantic_extraction_stub_packets(data_root=data_root, repo_root=resolved_repo_root)
    elif args.probe == "probe_blocked_runtime_with_disabled_extraction":
        payload = probe_blocked_runtime_with_disabled_extraction(data_root=data_root, repo_root=resolved_repo_root)
    elif args.probe == "probe_semantic_extraction_contextual_thread_state":
        payload = probe_semantic_extraction_contextual_thread_state(data_root=data_root, repo_root=resolved_repo_root)
    elif args.probe == "probe_blocked_runtime_with_stub_extraction":
        payload = probe_blocked_runtime_with_stub_extraction(data_root=data_root, repo_root=resolved_repo_root)
    else:
        payload = probe_semantic_extraction_hash_integrity(data_root=data_root, repo_root=resolved_repo_root)
    print(json.dumps(payload, indent=2, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
