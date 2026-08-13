from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from .hashing import sha256_json
from .config import load_runtime_config
from .ingest import run_ingest
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
    parser.add_argument("--repo-root", default=".", help="Repo root used to resolve .env.local and runtime config.")
    parser.add_argument("--config", help="Checked-in YAML runtime config path.")
    return parser


def build_ingest_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Ingest the configured vault into SQLite plus JSON manifests.")
    parser.add_argument("--repo-root", default=".", help="Repo root used to resolve runtime config.")
    parser.add_argument("--config", help="Checked-in YAML runtime config path.")
    return parser


def build_hyperspace_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build the specification-defined semantic hyperspace artifacts.")
    parser.add_argument("--vault", required=True, help="Read-only Markdown vault root.")
    parser.add_argument("--output", required=True, help="Output directory for the published build artifacts.")
    parser.add_argument("--build-config", required=True, help="YAML build configuration containing admitted fields.")
    return parser


def run_hyperspace_cli(argv: Sequence[str] | None = None) -> int:
    from .semantic_hyperspace import BuildConfig, build_semantic_hyperspace
    args = build_hyperspace_parser().parse_args(argv)
    manifest = build_semantic_hyperspace(
        vault_root=Path(args.vault).resolve(),
        output_root=Path(args.output).resolve(),
        config=BuildConfig.from_yaml(Path(args.build_config).resolve()),
    )
    print(json.dumps(manifest, indent=2, ensure_ascii=True))
    return 0


def run_turn_cli(argv: Sequence[str] | None = None) -> int:
    args = build_turn_parser().parse_args(argv)
    repo_root = Path(args.repo_root).resolve()
    config = load_runtime_config(repo_root=repo_root, config_path=args.config)
    llm_backend = resolve_llm_backend(
        repo_root=repo_root,
        config=config,
        llm_mode="auto",
        model_override=None,
    )
    semantic_compiler_backend = resolve_semantic_compiler_backend(config=config)
    result = run_thread_turn(
        repo_root=repo_root,
        data_root=config.data_root,
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
        "semantic_compiler_response_status": result.semantic_compiler_diagnostic.get("semantic_compiler_response_status"),
        "conversation_thread_path": str(result.conversation_thread_path),
        "thread_state_path": str(result.thread_state_path),
        "thread_ledger_path": str(result.thread_ledger_path),
        "turn_root": str(result.turn_root),
        "semantic_compiler_packet_path": str(result.semantic_compiler_packet_path),
        "semantic_compiler_diagnostic_path": str(result.semantic_compiler_diagnostic_path),
        "semantic_traversal_manifest_path": str(result.semantic_traversal_manifest_path),
        "retrieval_packet_path": str(result.retrieval_packet_path),
        "coverage_report_path": str(result.coverage_report_path),
        "synthesis_context_packet_path": str(result.synthesis_context_packet_path),
        "state_delta_path": str(result.state_delta_path),
        "coverage_decision": result.coverage_report.get("decision"),
        "semantic_compiler_packet_hash": sha256_json(result.semantic_compiler_packet),
        "semantic_compiler_diagnostic_hash": sha256_json(result.semantic_compiler_diagnostic),
        "latest_thread_state_hash": result.next_thread_state["latest_thread_state_hash"],
        "latest_perturbation_hash": result.ledger_record["state_perturbation_hash"],
    }
    print(json.dumps(payload, indent=2, ensure_ascii=True))
    return 0 if result.runtime_outcome == "completed" else 1


def run_ingest_cli(argv: Sequence[str] | None = None) -> int:
    args = build_ingest_parser().parse_args(argv)
    repo_root = Path(args.repo_root).resolve()
    config = load_runtime_config(repo_root=repo_root, config_path=args.config)
    result = run_ingest(repo_root=repo_root, data_root=config.data_root, config=config)
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
    if args and args[0] == "hyperspace-build":
        return run_hyperspace_cli(args[1:])
    if args and args[0] == "normalize":
        from .normalize import main as normalize_main
        return normalize_main(args[1:])
    return run_turn_cli(args)


if __name__ == "__main__":
    raise SystemExit(main())
