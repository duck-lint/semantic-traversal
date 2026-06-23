from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from .llm import resolve_llm_backend
from .runtime import run_thread_turn


def default_data_root() -> Path:
    return Path(tempfile.gettempdir()) / "semantic-traversal"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Local CLI runner for the semantic-traversal first build target.")
    parser.add_argument("--message", required=True, help="The user input for the turn.")
    parser.add_argument("--thread-id", help="Existing thread id to continue. Omit to create a new thread.")
    parser.add_argument(
        "--data-root",
        default=str(default_data_root()),
        help="Directory for conversation_thread, thread_state, and thread_ledger artifacts.",
    )
    parser.add_argument(
        "--llm-mode",
        choices=("auto", "live", "stub"),
        default="auto",
        help="Use OpenAI when available, require it, or force a local stub.",
    )
    parser.add_argument("--model", help="Override the OpenAI model for live mode.")
    parser.add_argument("--repo-root", default=".", help="Repo root used to resolve .env.local.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    repo_root = Path(args.repo_root).resolve()
    data_root = Path(args.data_root).resolve()
    llm_backend = resolve_llm_backend(repo_root=repo_root, llm_mode=args.llm_mode, model_override=args.model)
    result = run_thread_turn(
        repo_root=repo_root,
        data_root=data_root,
        user_input=args.message,
        llm_backend=llm_backend,
        thread_id=args.thread_id,
    )
    payload = {
        "thread_id": result.thread_id,
        "turn_id": result.turn_id,
        "assistant_response": result.assistant_response,
        "llm_mode": result.llm_metadata.get("mode"),
        "conversation_thread_path": str(result.conversation_thread_path),
        "thread_state_path": str(result.thread_state_path),
        "thread_ledger_path": str(result.thread_ledger_path),
        "latest_thread_state_hash": result.next_thread_state["latest_thread_state_hash"],
        "latest_perturbation_hash": result.ledger_record["state_perturbation_hash"],
    }
    print(json.dumps(payload, indent=2, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
