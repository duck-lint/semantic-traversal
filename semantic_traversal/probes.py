from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path
from typing import Any

from .llm import StubLLMBackend, resolve_llm_backend
from .runtime import run_thread_turn
from .storage import load_json, read_ledger


def _default_probe_root() -> Path:
    return Path(tempfile.gettempdir()) / "semantic-traversal-probes"


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


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the named semantic-traversal first-target probes.")
    parser.add_argument("probe", choices=("probe_new_thread_minimal_turn", "probe_same_thread_continuation_turn"))
    parser.add_argument("--data-root", default=str(_default_probe_root()))
    parser.add_argument("--llm-mode", choices=("auto", "live", "stub"), default="stub")
    parser.add_argument("--model")
    args = parser.parse_args()

    data_root = Path(args.data_root).resolve()
    if args.llm_mode == "stub":
        backend = StubLLMBackend(prefix="Probe stub response")
    else:
        backend = resolve_llm_backend(repo_root=Path(".").resolve(), llm_mode=args.llm_mode, model_override=args.model)

    if args.probe == "probe_new_thread_minimal_turn":
        payload = probe_new_thread_minimal_turn(data_root=data_root, llm_backend=backend)
    else:
        payload = probe_same_thread_continuation_turn(data_root=data_root, llm_backend=backend)
    print(json.dumps(payload, indent=2, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
