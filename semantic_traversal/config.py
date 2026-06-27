from __future__ import annotations

import copy
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


DEFAULT_CONFIG_FILENAME = "semantic_traversal.runtime.yaml"
_SECRET_KEY_EXACT = {"api_key", "apikey", "secret", "password", "bearer_token", "access_token", "refresh_token", "credential", "credentials"}
_SECRET_VALUE_PREFIXES = ("sk-", "Bearer ")

_DEFAULT_CONFIG: dict[str, Any] = {
    "runtime": {
        "data_root": ".semantic-traversal-data",
        "max_retrieval_chunks": 6,
        "require_semantic_extraction": True,
        "require_vector_surface": False,
        "require_graph_surface": True,
        "require_lexical_surface": True,
        "require_primary_corpus_surface": True,
        "require_synthetic_node_surface": False,
    },
    "llm": {
        "provider": "openai",
        "mode": "auto",
        "model": "gpt-5.4-mini",
        "max_output_tokens": 400,
        "runtime_budget": {
            "max_input_tokens": None,
            "max_output_tokens": 400,
            "max_total_tokens": None,
            "timeout_seconds": None,
        },
    },
    "semantic_extraction": {
        "provider": "ollama",
        "model": None,
        "base_url": "http://localhost:11434",
        "request_timeout_seconds": 20,
    },
    "embeddings": {
        "provider": "ollama",
        "model": None,
        "base_url": "http://localhost:11434",
        "request_timeout_seconds": 20,
        "vector_dimensions": None,
    },
    "paths": {
        "corpus_roots": [
            {"label": "corpus", "path": "corpus"},
            {"label": "tests-fixtures", "path": "tests/fixtures"},
        ],
        "synthetic_nodes_root": "corpus/SYNTHETIC_NODES",
    },
    "indexes": {
        "sqlite_path": None,
        "vector_table": "chunk_vectors",
        "graph_nodes_table": "graph_nodes",
        "graph_edges_table": "graph_edges",
    },
    "indexing": {
        "embedding_batch_size": 16,
        "rebuild_policy": "missing_or_stale",
        "dirty_index_strategy": "block",
        "missing_vectors": "block",
        "store_embedding_vectors_as_json": True,
    },
    "coverage": {
        "min_selected_chunks": 1,
        "max_selected_chunks": 6,
        "require_surface_contributions": {
            "lexical_index_surface": True,
            "vector_index_surface": False,
            "graph_layer": True,
            "primary_corpus": True,
            "synthetic_nodes": False,
        },
        "graph_expansion_hop_limit": 1,
        "allow_no_retrieval_needed": False,
    },
}


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class ConfiguredSourceRoot:
    label: str
    path: Path


@dataclass(frozen=True)
class RuntimeConfig:
    repo_root: Path
    config_path: Path
    raw: dict[str, Any]

    @property
    def data_root(self) -> Path:
        return self.resolve_path(str(self.raw["runtime"]["data_root"]))

    @property
    def max_retrieval_chunks(self) -> int:
        return int(self.raw["runtime"]["max_retrieval_chunks"])

    @property
    def llm_model(self) -> str:
        return str(self.raw["llm"]["model"])

    @property
    def llm_max_output_tokens(self) -> int:
        return int(self.raw["llm"]["max_output_tokens"])

    def resolve_path(self, raw_path: str | None) -> Path:
        if not raw_path:
            return self.repo_root
        candidate = Path(raw_path)
        if not candidate.is_absolute():
            candidate = (self.repo_root / candidate).resolve()
        else:
            candidate = candidate.resolve()
        return candidate

    def source_roots(self) -> tuple[ConfiguredSourceRoot, ...]:
        roots: list[ConfiguredSourceRoot] = []
        for entry in list(self.raw["paths"]["corpus_roots"]):
            roots.append(
                ConfiguredSourceRoot(
                    label=str(entry["label"]),
                    path=self.resolve_path(str(entry["path"])),
                )
            )
        return tuple(roots)

    @property
    def synthetic_nodes_root(self) -> Path:
        return self.resolve_path(str(self.raw["paths"]["synthetic_nodes_root"]))

    @property
    def sqlite_path(self) -> Path | None:
        raw_path = self.raw["indexes"].get("sqlite_path")
        if raw_path in {None, ""}:
            return None
        return self.resolve_path(str(raw_path))


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def _assert_no_secrets(payload: Any, *, path: str = "root") -> None:
    if isinstance(payload, dict):
        for key, value in payload.items():
            lowered_key = str(key).lower()
            if lowered_key in _SECRET_KEY_EXACT or lowered_key.endswith("_api_key") or lowered_key.endswith("_token") or lowered_key.endswith("_secret"):
                raise ConfigError(f"Config YAML must not contain secrets: {path}.{key}")
            _assert_no_secrets(value, path=f"{path}.{key}")
        return
    if isinstance(payload, list):
        for index, value in enumerate(payload):
            _assert_no_secrets(value, path=f"{path}[{index}]")
        return
    if isinstance(payload, str):
        if payload.startswith(_SECRET_VALUE_PREFIXES):
            raise ConfigError(f"Config YAML appears to contain a secret value at {path}")


def _resolve_config_path(repo_root: Path, explicit_config_path: str | None = None) -> Path:
    raw_path = explicit_config_path or os.environ.get("SEMANTIC_TRAVERSAL_CONFIG") or DEFAULT_CONFIG_FILENAME
    candidate = Path(raw_path)
    if not candidate.is_absolute():
        candidate = (repo_root / candidate).resolve()
    else:
        candidate = candidate.resolve()
    return candidate


def load_runtime_config(*, repo_root: Path, config_path: str | None = None) -> RuntimeConfig:
    resolved_repo_root = repo_root.resolve()
    resolved_config_path = _resolve_config_path(resolved_repo_root, config_path)
    if not resolved_config_path.exists():
        raise FileNotFoundError(f"Runtime config not found: {resolved_config_path}")
    raw_text = resolved_config_path.read_text(encoding="utf-8")
    parsed = yaml.safe_load(raw_text) or {}
    if not isinstance(parsed, dict):
        raise ConfigError(f"Runtime config must parse to a mapping: {resolved_config_path}")
    _assert_no_secrets(parsed)
    merged = _deep_merge(_DEFAULT_CONFIG, parsed)
    return RuntimeConfig(repo_root=resolved_repo_root, config_path=resolved_config_path, raw=merged)
