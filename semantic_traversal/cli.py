from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from .hashing import sha256_json
from .config import load_runtime_config
from .ingest import (
    build_default_source_roots,
    parse_source_root_argument,
    run_ingest,
)
from .llm import resolve_llm_backend
from .semantic_compiler import resolve_semantic_compiler_backend
from .runtime import run_thread_turn


def build_turn_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Local CLI runner for semantic-traversal turns.",
        allow_abbrev=False,
    )
    parser.add_argument("--message", required=True, help="The user input for the turn.")
    parser.add_argument("--thread-id", help="Existing thread id to continue. Omit to create a new thread.")
    parser.add_argument(
        "--data-root",
        help="Directory for conversation_thread, thread_state, and thread_ledger artifacts.",
    )
    parser.add_argument(
        "--llm-mode",
        choices=("auto", "live"),
        default="auto",
        help="Use OpenAI when available or require live OpenAI execution.",
    )
    parser.add_argument("--model", help="Override the OpenAI model for live mode.")
    parser.add_argument("--repo-root", default=".", help="Repo root used to resolve .env.local.")
    parser.add_argument("--config", help="Checked-in YAML runtime config path.")
    return parser


def build_ingest_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Ingest authorized Markdown corpus roots into SQLite plus JSON manifests.")
    parser.add_argument(
        "--data-root",
        help="Directory for ingestion SQLite and manifest artifacts.",
    )
    parser.add_argument("--repo-root", default=".", help="Repo root used to resolve default corpus roots.")
    parser.add_argument("--config", help="Checked-in YAML runtime config path.")
    parser.add_argument(
        "--source-root",
        action="append",
        default=[],
        help="Override source roots with repeated label=path values.",
    )
    return parser


def run_turn_cli(argv: Sequence[str] | None = None) -> int:
    args = build_turn_parser().parse_args(argv)
    repo_root = Path(args.repo_root).resolve()
    config = load_runtime_config(repo_root=repo_root, config_path=args.config)
    data_root = Path(args.data_root).resolve() if args.data_root else config.data_root
    llm_backend = resolve_llm_backend(repo_root=repo_root, config=config, llm_mode=args.llm_mode, model_override=args.model)
    semantic_compiler_backend = resolve_semantic_compiler_backend(config=config)
    result = run_thread_turn(
        repo_root=repo_root,
        data_root=data_root,
        user_input=args.message,
        llm_backend=llm_backend,
        thread_id=args.thread_id,
        config=config,
        semantic_compiler_backend=semantic_compiler_backend,
    )
    payload = {
        "thread_id": result.thread_id,
        "turn_id": result.turn_id,
        "assistant_response": result.assistant_response,
        "runtime_outcome": result.runtime_outcome,
        "blocking_reasons": result.blocking_reasons,
        "llm_mode": result.llm_metadata.get("mode"),
        "semantic_compiler_status": result.semantic_compiler_status,
        "conversation_thread_path": str(result.conversation_thread_path),
        "thread_state_path": str(result.thread_state_path),
        "thread_ledger_path": str(result.thread_ledger_path),
        "turn_root": str(result.turn_root),
        "semantic_compiler_packet_path": str(result.semantic_compiler_packet_path),
        "semantic_traversal_manifest_path": str(result.semantic_traversal_manifest_path),
        "retrieval_packet_path": str(result.retrieval_packet_path),
        "coverage_report_path": str(result.coverage_report_path),
        "synthesis_context_packet_path": str(result.synthesis_context_packet_path),
        "state_delta_path": str(result.state_delta_path),
        "coverage_decision": result.coverage_report.get("decision"),
        "semantic_compiler_packet_hash": sha256_json(result.semantic_compiler_packet),
        "latest_thread_state_hash": result.next_thread_state["latest_thread_state_hash"],
        "latest_perturbation_hash": result.ledger_record["state_perturbation_hash"],
    }
    print(json.dumps(payload, indent=2, ensure_ascii=True))
    return 0 if result.runtime_outcome == "completed" else 1


def run_ingest_cli(argv: Sequence[str] | None = None) -> int:
    args = build_ingest_parser().parse_args(argv)
    repo_root = Path(args.repo_root).resolve()
    config = load_runtime_config(repo_root=repo_root, config_path=args.config)
    data_root = Path(args.data_root).resolve() if args.data_root else config.data_root
    source_roots = (
        tuple(parse_source_root_argument(raw=raw_root, repo_root=repo_root) for raw_root in args.source_root)
        if args.source_root
        else build_default_source_roots(repo_root, config=config)
    )
    result = run_ingest(repo_root=repo_root, data_root=data_root, source_roots=source_roots, config=config)
    payload = {
        "status": "pass",
        "run_id": result.run_id,
        "generated_at": result.generated_at,
        "data_root": str(result.data_root),
        "database_path": str(result.database_path),
        "manifest_path": str(result.manifest_path),
        "source_roots": [{"label": root.label, "path": str(root.path)} for root in result.source_roots],
        "note_count": result.note_count,
        "chunk_count": result.chunk_count,
        "inserted_chunks": result.inserted_chunks,
        "updated_chunks": result.updated_chunks,
        "unchanged_chunks": result.unchanged_chunks,
        "deleted_chunks": result.deleted_chunks,
        "deleted_notes": result.deleted_notes,
    }
    print(json.dumps(payload, indent=2, ensure_ascii=True))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == "ingest":
        return run_ingest_cli(args[1:])
    return run_turn_cli(args)


if __name__ == "__main__":
    raise SystemExit(main())
