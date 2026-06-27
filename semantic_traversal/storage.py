from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import RuntimeConfig


@dataclass(frozen=True)
class ThreadPaths:
    data_root: Path
    thread_id: str
    config: RuntimeConfig

    @property
    def threads_root(self) -> Path:
        return _resolve_runtime_storage_path(self.data_root, self.config.storage_threads_root)

    @property
    def thread_root(self) -> Path:
        return self.threads_root / self.thread_id

    @property
    def turns_root(self) -> Path:
        return self.thread_root / self.config.storage_turns_root

    @property
    def conversation_thread_path(self) -> Path:
        return self.thread_root / self.config.storage_conversation_thread_filename

    @property
    def thread_state_path(self) -> Path:
        return self.thread_root / self.config.storage_thread_state_filename

    @property
    def thread_ledger_path(self) -> Path:
        return self.thread_root / self.config.storage_thread_ledger_filename

    def turn_root(self, turn_id: int) -> Path:
        return self.turns_root / f"{self.config.storage_turn_directory_prefix}{turn_id:06d}"


def _resolve_runtime_storage_path(data_root: Path, raw_path: Path) -> Path:
    if raw_path.is_absolute():
        return raw_path.resolve()
    return (data_root / raw_path).resolve()


def ensure_data_root(data_root: Path, *, config: RuntimeConfig) -> Path:
    data_root.mkdir(parents=True, exist_ok=True)
    _resolve_runtime_storage_path(data_root, config.storage_threads_root).mkdir(parents=True, exist_ok=True)
    return data_root


def create_thread_paths(data_root: Path, *, config: RuntimeConfig, thread_id: str | None = None) -> ThreadPaths:
    ensure_data_root(data_root, config=config)
    resolved_thread_id = thread_id or f"thread-{uuid.uuid4().hex[:12]}"
    paths = ThreadPaths(data_root=data_root, thread_id=resolved_thread_id, config=config)
    paths.thread_root.mkdir(parents=True, exist_ok=True)
    paths.turns_root.mkdir(parents=True, exist_ok=True)
    return paths


def load_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")


def read_ledger(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            records.append(json.loads(line))
    return records


def append_ledger_record(path: Path, record: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=True) + "\n")
