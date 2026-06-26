from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
import sys
from pathlib import Path
from typing import Any

from .ingest import build_default_source_roots, default_data_root, run_ingest
from .hashing import sha256_json
from .llm import StubLLMBackend, resolve_llm_backend
from .runtime import run_thread_turn
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
        thread_id=None,
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
        thread_id=None,
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
    assert "Dream Recall" in labels
    assert "Y-Day Review" in labels
    assert "Daily Intent" in labels
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
    assert result.coverage_report["status"] == "minimal_pass", "expected retrieval to succeed"
    assert result.retrieval_packet["selected_chunks"], "expected at least one retrieval hit"
    assert any(chunk["source_root_label"] == "tests-fixtures" for chunk in result.retrieval_packet["selected_chunks"])
    assert result.synthesis_context_packet["approved_retrieval_packet"] is not None
    return {
        "probe": "probe_lexical_retrieval_fixture_hit",
        "status": "pass",
        "turn_id": result.turn_id,
        "coverage_status": result.coverage_report["status"],
        "query_intent": result.semantic_context_packet["query_analysis"]["query_intent"],
        "anchor_terms": result.semantic_context_packet["query_analysis"]["anchor_terms"],
        "support_terms": result.semantic_context_packet["query_analysis"]["support_terms"],
        "weak_question_terms": result.semantic_context_packet["query_analysis"]["weak_question_terms"],
        "selected_chunk_ids": [chunk["chunk_id"] for chunk in result.retrieval_packet["selected_chunks"]],
        "score_breakdowns": [chunk["score_breakdown"] for chunk in result.retrieval_packet["selected_chunks"]],
    }


def probe_lexical_retrieval_no_index(data_root: Path, repo_root: Path | None = None) -> dict[str, Any]:
    resolved_repo_root = (repo_root or Path(".")).resolve()
    result = run_thread_turn(
        repo_root=resolved_repo_root,
        data_root=data_root,
        user_input="Dream Recall with no ingestion index present.",
        llm_backend=StubLLMBackend(prefix="Probe stub response"),
    )
    assert result.coverage_report["status"] == "no_index", "expected no_index coverage status"
    assert result.retrieval_packet["selected_chunks"] == [], "expected an empty retrieval packet"
    return {
        "probe": "probe_lexical_retrieval_no_index",
        "status": "pass",
        "turn_id": result.turn_id,
        "coverage_status": result.coverage_report["status"],
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
    assert result.coverage_report["status"] == "no_matches", "expected no_matches coverage status"
    assert result.retrieval_packet["selected_chunks"] == [], "expected an empty retrieval packet"
    return {
        "probe": "probe_lexical_retrieval_no_match",
        "status": "pass",
        "turn_id": result.turn_id,
        "coverage_status": result.coverage_report["status"],
    }


def probe_lexical_retrieval_no_query_terms(data_root: Path, repo_root: Path | None = None) -> dict[str, Any]:
    resolved_repo_root = (repo_root or Path(".")).resolve()
    run_ingest(repo_root=resolved_repo_root, data_root=data_root, source_roots=build_default_source_roots(resolved_repo_root))
    result = run_thread_turn(
        repo_root=resolved_repo_root,
        data_root=data_root,
        user_input="   and the or   ",
        llm_backend=StubLLMBackend(prefix="Probe stub response"),
    )
    assert result.coverage_report["status"] == "no_query_terms", "expected no_query_terms coverage status"
    assert result.retrieval_packet["retrieval_status"] == "no_query_terms"
    assert result.retrieval_packet["selected_chunks"] == [], "expected an empty retrieval packet"
    return {
        "probe": "probe_lexical_retrieval_no_query_terms",
        "status": "pass",
        "turn_id": result.turn_id,
        "coverage_status": result.coverage_report["status"],
        "query_terms": result.semantic_context_packet["extracted_lexical_query_terms"],
    }


def probe_lexical_query_analysis_roles(data_root: Path, repo_root: Path | None = None) -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as repo_dir:
        probe_repo_root = Path(repo_dir)
        (probe_repo_root / "corpus").mkdir(parents=True, exist_ok=True)
        (probe_repo_root / "tests" / "fixtures" / "JOURNAL" / "2025-09").mkdir(parents=True, exist_ok=True)
        (probe_repo_root / "tests" / "fixtures" / "JOURNAL" / "2025-09" / "01_Monday.md").write_text(
            "# Weak Query Note\n"
            "What day usually day time.\n",
            encoding="utf-8",
        )
        (probe_repo_root / "corpus" / "Role Discipline.md").write_text(
            "# Anchor Query Note\n"
            "The orchard visit happens before bed. Orchard orchard orchard.\n",
            encoding="utf-8",
        )
        run_ingest(repo_root=probe_repo_root, data_root=data_root, source_roots=build_default_source_roots(probe_repo_root))
        result = run_thread_turn(
            repo_root=probe_repo_root,
            data_root=data_root,
            user_input='What day do I usually visit "orchard"?',
            llm_backend=StubLLMBackend(prefix="Probe stub response"),
        )
    analysis = result.semantic_context_packet["query_analysis"]
    assert "orchard" in analysis["anchor_terms"], "expected quoted anchor term to be classified as anchor"
    assert analysis["weak_question_terms"], "expected weak question terms to be classified"
    assert analysis["query_terms_for_retrieval"], "expected usable query terms"
    return {
        "probe": "probe_lexical_query_analysis_roles",
        "status": "pass",
        "coverage_status": result.coverage_report["status"],
        "query_intent": analysis["query_intent"],
        "anchor_terms": analysis["anchor_terms"],
        "support_terms": analysis["support_terms"],
        "weak_question_terms": analysis["weak_question_terms"],
        "ignored_instruction_terms": analysis["ignored_instruction_terms"],
        "selected_chunk_ids": [chunk["chunk_id"] for chunk in result.retrieval_packet["selected_chunks"]],
        "score_breakdowns": [chunk["score_breakdown"] for chunk in result.retrieval_packet["selected_chunks"]],
    }


def probe_anchor_term_retrieval_precedence(data_root: Path, repo_root: Path | None = None) -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as repo_dir:
        probe_repo_root = Path(repo_dir)
        (probe_repo_root / "corpus").mkdir(parents=True, exist_ok=True)
        (probe_repo_root / "tests" / "fixtures" / "JOURNAL" / "2025-09").mkdir(parents=True, exist_ok=True)
        (probe_repo_root / "tests" / "fixtures" / "JOURNAL" / "2025-09" / "01_Monday.md").write_text(
            "# Weak Terms Only\n"
            "What day usually what day usually.\n",
            encoding="utf-8",
        )
        (probe_repo_root / "corpus" / "Anchor Precedence.md").write_text(
            "# Anchor Terms\n"
            "The orchard visit is the target content.\n",
            encoding="utf-8",
        )
        run_ingest(repo_root=probe_repo_root, data_root=data_root, source_roots=build_default_source_roots(probe_repo_root))
        result = run_thread_turn(
            repo_root=probe_repo_root,
            data_root=data_root,
            user_input="What day do I usually visit orchard?",
            llm_backend=StubLLMBackend(prefix="Probe stub response"),
        )
    analysis = result.semantic_context_packet["query_analysis"]
    selected_chunks = result.retrieval_packet["selected_chunks"]
    assert selected_chunks, "expected retrieval hits"
    assert selected_chunks[0]["matched_anchor_terms"], "expected the top chunk to match the anchor term"
    assert any(
        "Anchor" in (chunk["note_title"] or "") or "Anchor" in (chunk["section_label"] or "")
        for chunk in selected_chunks
    ), "expected the anchor-bearing note to be selected"
    weak_only_chunks = [chunk for chunk in selected_chunks if not chunk["matched_anchor_terms"]]
    return {
        "probe": "probe_anchor_term_retrieval_precedence",
        "status": "pass",
        "coverage_status": result.coverage_report["status"],
        "query_intent": analysis["query_intent"],
        "anchor_terms": analysis["anchor_terms"],
        "support_terms": analysis["support_terms"],
        "weak_question_terms": analysis["weak_question_terms"],
        "selected_chunk_ids": [chunk["chunk_id"] for chunk in selected_chunks],
        "top_score_breakdown": selected_chunks[0]["score_breakdown"],
        "weak_only_selected_chunk_ids": [chunk["chunk_id"] for chunk in weak_only_chunks],
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
    semantic_context_packet = _load_manifest(result.semantic_context_packet_path)
    semantic_traversal_manifest = _load_manifest(result.semantic_traversal_manifest_path)
    retrieval_packet = _load_manifest(result.retrieval_packet_path)
    coverage_report = _load_manifest(result.coverage_report_path)
    synthesis_context_packet = _load_manifest(result.synthesis_context_packet_path)
    state_delta = _load_manifest(result.state_delta_path)
    thread_state = _load_manifest(result.thread_state_path)
    ledger_records = read_ledger(result.thread_ledger_path)
    ledger_record = ledger_records[-1]
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
        "turn_id": result.turn_id,
        "coverage_status": result.coverage_report["status"],
        "ledger_hashes": {
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
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(completed.stdout)
    artifact_paths = {
        key: Path(payload[key])
        for key in (
            "turn_root",
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
        "coverage_status": payload["coverage_status"],
        "artifact_paths": {key: str(path) for key, path in artifact_paths.items()},
        "latest_perturbation_hash": payload["latest_perturbation_hash"],
        "latest_thread_state_hash": payload["latest_thread_state_hash"],
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
            "probe_lexical_query_analysis_roles",
            "probe_anchor_term_retrieval_precedence",
            "probe_ledger_hash_artifact_integrity",
            "probe_turn_cli_artifact_paths",
        ),
    )
    parser.add_argument("--data-root", default=str(_default_probe_root()))
    parser.add_argument("--llm-mode", choices=("auto", "live", "stub"), default="stub")
    parser.add_argument("--model")
    parser.add_argument("--repo-root", default=".")
    args = parser.parse_args()

    data_root = Path(args.data_root).resolve()
    if args.probe == "probe_new_thread_minimal_turn":
        if args.llm_mode == "stub":
            backend = StubLLMBackend(prefix="Probe stub response")
        else:
            backend = resolve_llm_backend(repo_root=Path(".").resolve(), llm_mode=args.llm_mode, model_override=args.model)
        payload = probe_new_thread_minimal_turn(data_root=data_root, llm_backend=backend)
    elif args.probe == "probe_same_thread_continuation_turn":
        if args.llm_mode == "stub":
            backend = StubLLMBackend(prefix="Probe stub response")
        else:
            backend = resolve_llm_backend(repo_root=Path(".").resolve(), llm_mode=args.llm_mode, model_override=args.model)
        payload = probe_same_thread_continuation_turn(data_root=data_root, llm_backend=backend)
    elif args.probe == "probe_fixture_journal_section_paragraph_chunking":
        payload = probe_fixture_journal_section_paragraph_chunking(
            data_root=data_root,
            repo_root=Path(args.repo_root).resolve(),
        )
    elif args.probe == "probe_repo_corpus_journal_heading_section_resolution":
        payload = probe_repo_corpus_journal_heading_section_resolution(
            data_root=data_root,
            repo_root=Path(args.repo_root).resolve(),
        )
    elif args.probe == "probe_lexical_retrieval_fixture_hit":
        payload = probe_lexical_retrieval_fixture_hit(
            data_root=data_root,
            repo_root=Path(args.repo_root).resolve(),
        )
    elif args.probe == "probe_lexical_retrieval_no_index":
        payload = probe_lexical_retrieval_no_index(
            data_root=data_root,
            repo_root=Path(args.repo_root).resolve(),
        )
    elif args.probe == "probe_lexical_retrieval_no_query_terms":
        payload = probe_lexical_retrieval_no_query_terms(
            data_root=data_root,
            repo_root=Path(args.repo_root).resolve(),
        )
    elif args.probe == "probe_lexical_query_analysis_roles":
        payload = probe_lexical_query_analysis_roles(
            data_root=data_root,
            repo_root=Path(args.repo_root).resolve(),
        )
    elif args.probe == "probe_anchor_term_retrieval_precedence":
        payload = probe_anchor_term_retrieval_precedence(
            data_root=data_root,
            repo_root=Path(args.repo_root).resolve(),
        )
    elif args.probe == "probe_ledger_hash_artifact_integrity":
        payload = probe_ledger_hash_artifact_integrity(
            data_root=data_root,
            repo_root=Path(args.repo_root).resolve(),
        )
    elif args.probe == "probe_turn_cli_artifact_paths":
        payload = probe_turn_cli_artifact_paths(
            data_root=data_root,
            repo_root=Path(args.repo_root).resolve(),
        )
    else:
        payload = probe_lexical_retrieval_no_match(
            data_root=data_root,
            repo_root=Path(args.repo_root).resolve(),
        )
    print(json.dumps(payload, indent=2, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
