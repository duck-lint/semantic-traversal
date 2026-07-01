from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


DEFAULT_CONFIG_FILENAME = "semantic_traversal.runtime.yaml"
SQL_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SECRET_KEY_EXACT = {
    "api_key",
    "apikey",
    "secret",
    "password",
    "bearer_token",
    "access_token",
    "refresh_token",
    "credential",
    "credentials",
}
_SECRET_VALUE_PREFIXES = ("sk-", "Bearer ")

_EXPECTED_CONFIG_SCHEMA: dict[str, Any] = {
    "runtime": {
        "data_root": str,
        "max_retrieval_chunks": int,
    },
    "graph_traversal": {
        "enabled": bool,
        "hop_limit": int,
        "max_candidates": int,
        "seed_sources": [str],
        "edge_type_allowlist": [str],
        "node_type_allowlist": [str],
        "match_mode": str,
        "min_token_overlap": int,
    },
    "llm": {
        "model": str,
        "max_output_tokens": int,
    },
    "semantic_compiler": {
        "provider": str,
        "model": (str, type(None)),
        "base_url": str,
        "request_timeout_seconds": int,
    },
    "embeddings": {
        "provider": str,
        "model": str,
        "base_url": (str, type(None)),
        "batch_size": int,
        "normalize_embeddings": bool,
        "device": (str, type(None)),
        "request_timeout_seconds": int,
    },
    "paths": {
        "corpus_roots": [
            {
                "label": str,
                "path": str,
            },
        ],
        "synthetic_nodes_root": str,
    },
    "indexes": {
        "vector_table": str,
        "graph_nodes_table": str,
        "graph_edges_table": str,
    },
    "storage": {
        "threads_root": str,
        "turns_root": str,
        "turn_directory_prefix": str,
        "conversation_thread_filename": str,
        "thread_state_filename": str,
        "thread_ledger_filename": str,
        "ingestion_root": str,
        "ingestion_database_filename": str,
        "ingestion_manifests_root": str,
        "latest_ingest_manifest_filename": str,
    },
}


class ConfigError(ValueError):
    pass


def validate_sql_identifier(value: str, field: str) -> str:
    if not SQL_IDENTIFIER_RE.fullmatch(value):
        raise ConfigError(f"Invalid SQL identifier for {field}: {value}")
    return value


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
        return self.resolve_path(self.raw["runtime"]["data_root"])

    @property
    def max_retrieval_chunks(self) -> int:
        return int(self.raw["runtime"]["max_retrieval_chunks"])

    @property
    def graph_traversal_enabled(self) -> bool:
        return bool(self.raw["graph_traversal"]["enabled"])

    @property
    def graph_traversal_hop_limit(self) -> int:
        return int(self.raw["graph_traversal"]["hop_limit"])

    @property
    def graph_traversal_max_candidates(self) -> int:
        return int(self.raw["graph_traversal"]["max_candidates"])

    @property
    def graph_traversal_seed_sources(self) -> tuple[str, ...]:
        return tuple(str(value) for value in self.raw["graph_traversal"]["seed_sources"])

    @property
    def graph_traversal_edge_type_allowlist(self) -> tuple[str, ...]:
        return tuple(str(value) for value in self.raw["graph_traversal"]["edge_type_allowlist"])

    @property
    def graph_traversal_node_type_allowlist(self) -> tuple[str, ...]:
        return tuple(str(value) for value in self.raw["graph_traversal"]["node_type_allowlist"])

    @property
    def graph_traversal_match_mode(self) -> str:
        return str(self.raw["graph_traversal"]["match_mode"])

    @property
    def graph_traversal_min_token_overlap(self) -> int:
        return int(self.raw["graph_traversal"]["min_token_overlap"])

    @property
    def llm_model(self) -> str:
        return str(self.raw["llm"]["model"])

    @property
    def llm_max_output_tokens(self) -> int:
        return int(self.raw["llm"]["max_output_tokens"])

    @property
    def semantic_compiler_model(self) -> str | None:
        value = self.raw["semantic_compiler"]["model"]
        return None if value is None else str(value)

    @property
    def semantic_compiler_provider(self) -> str:
        return str(self.raw["semantic_compiler"]["provider"])

    @property
    def semantic_compiler_base_url(self) -> str:
        return str(self.raw["semantic_compiler"]["base_url"])

    @property
    def semantic_compiler_request_timeout_seconds(self) -> int:
        return int(self.raw["semantic_compiler"]["request_timeout_seconds"])

    @property
    def embedding_model(self) -> str:
        return str(self.raw["embeddings"]["model"])

    @property
    def embedding_provider(self) -> str:
        return str(self.raw["embeddings"]["provider"])

    @property
    def embedding_base_url(self) -> str | None:
        value = self.raw["embeddings"]["base_url"]
        return None if value is None else str(value)

    @property
    def embedding_batch_size(self) -> int:
        return int(self.raw["embeddings"]["batch_size"])

    @property
    def embedding_normalize_embeddings(self) -> bool:
        return bool(self.raw["embeddings"]["normalize_embeddings"])

    @property
    def embedding_device(self) -> str | None:
        value = self.raw["embeddings"]["device"]
        return None if value is None else str(value)

    @property
    def embedding_request_timeout_seconds(self) -> int:
        return int(self.raw["embeddings"]["request_timeout_seconds"])

    @property
    def corpus_roots(self) -> tuple[ConfiguredSourceRoot, ...]:
        roots: list[ConfiguredSourceRoot] = []
        for entry in self.raw["paths"]["corpus_roots"]:
            roots.append(
                ConfiguredSourceRoot(
                    label=str(entry["label"]),
                    path=self.resolve_path(str(entry["path"])),
                )
            )
        return tuple(roots)

    @property
    def synthetic_nodes_root(self) -> Path:
        return self.resolve_path(self.raw["paths"]["synthetic_nodes_root"])

    @property
    def vector_table(self) -> str:
        return str(self.raw["indexes"]["vector_table"])

    @property
    def graph_nodes_table(self) -> str:
        return str(self.raw["indexes"]["graph_nodes_table"])

    @property
    def graph_edges_table(self) -> str:
        return str(self.raw["indexes"]["graph_edges_table"])

    @property
    def storage_threads_root(self) -> Path:
        return Path(self.raw["storage"]["threads_root"])

    @property
    def storage_turns_root(self) -> Path:
        return Path(self.raw["storage"]["turns_root"])

    @property
    def storage_turn_directory_prefix(self) -> str:
        return str(self.raw["storage"]["turn_directory_prefix"])

    @property
    def storage_conversation_thread_filename(self) -> str:
        return str(self.raw["storage"]["conversation_thread_filename"])

    @property
    def storage_thread_state_filename(self) -> str:
        return str(self.raw["storage"]["thread_state_filename"])

    @property
    def storage_thread_ledger_filename(self) -> str:
        return str(self.raw["storage"]["thread_ledger_filename"])

    @property
    def storage_ingestion_root(self) -> Path:
        return Path(self.raw["storage"]["ingestion_root"])

    @property
    def storage_ingestion_database_filename(self) -> str:
        return str(self.raw["storage"]["ingestion_database_filename"])

    @property
    def storage_ingestion_manifests_root(self) -> Path:
        return Path(self.raw["storage"]["ingestion_manifests_root"])

    @property
    def storage_latest_ingest_manifest_filename(self) -> str:
        return str(self.raw["storage"]["latest_ingest_manifest_filename"])

    def resolve_path(self, raw_path: str | Path) -> Path:
        candidate = Path(raw_path)
        if not candidate.is_absolute():
            candidate = (self.repo_root / candidate).resolve()
        else:
            candidate = candidate.resolve()
        return candidate



def _resolve_config_path(repo_root: Path, explicit_config_path: str | None = None) -> Path:
    raw_path = explicit_config_path or os.environ.get("SEMANTIC_TRAVERSAL_CONFIG") or DEFAULT_CONFIG_FILENAME
    candidate = Path(raw_path)
    if not candidate.is_absolute():
        candidate = (repo_root / candidate).resolve()
    else:
        candidate = candidate.resolve()
    return candidate


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
    if isinstance(payload, str) and payload.startswith(_SECRET_VALUE_PREFIXES):
        raise ConfigError(f"Config YAML appears to contain a secret value at {path}")


def _validate_type(value: Any, expected_type: Any, *, path: str) -> None:
    if isinstance(expected_type, dict):
        if not isinstance(value, dict):
            raise ConfigError(f"Invalid runtime config field type: {path} expected mapping")
        _validate_mapping(value, expected_type, path=path)
        return
    if isinstance(expected_type, list):
        if not isinstance(value, list):
            raise ConfigError(f"Invalid runtime config field type: {path} expected list")
        if len(expected_type) != 1:
            raise ConfigError("Internal config schema error: list schema must have one item schema")
        item_schema = expected_type[0]
        for index, item in enumerate(value):
            _validate_type(item, item_schema, path=f"{path}[{index}]")
        return
    if isinstance(expected_type, tuple):
        if not any(_matches_exact_type(value, item_type) for item_type in expected_type):
            expected_name = " or ".join(_describe_type(t) for t in expected_type)
            raise ConfigError(f"Invalid runtime config field type: {path} expected {expected_name}")
        return
    if not _matches_exact_type(value, expected_type):
        raise ConfigError(f"Invalid runtime config field type: {path} expected {_describe_type(expected_type)}")


def _matches_exact_type(value: Any, expected_type: Any) -> bool:
    if expected_type is str:
        return type(value) is str
    if expected_type is int:
        return type(value) is int
    if expected_type is bool:
        return type(value) is bool
    if expected_type is type(None):
        return value is None
    return isinstance(value, expected_type)


def _describe_type(expected_type: Any) -> str:
    if expected_type is str:
        return "str"
    if expected_type is int:
        return "int"
    if expected_type is bool:
        return "bool"
    if expected_type is type(None):
        return "None"
    return getattr(expected_type, "__name__", str(expected_type))


def _validate_mapping(payload: dict[str, Any], schema: dict[str, Any], *, path: str) -> None:
    unknown_fields = sorted(set(payload) - set(schema))
    if unknown_fields:
        raise ConfigError(f"Unknown runtime config field: {path}.{unknown_fields[0]}")
    for key, expected_type in schema.items():
        if key not in payload:
            raise ConfigError(f"Missing required runtime config field: {path}.{key}")
        _validate_type(payload[key], expected_type, path=f"{path}.{key}")


def load_runtime_config(*, repo_root: Path, config_path: str | None = None) -> RuntimeConfig:
    resolved_repo_root = repo_root.resolve()
    resolved_config_path = _resolve_config_path(resolved_repo_root, config_path)
    if not resolved_config_path.exists():
        raise FileNotFoundError(f"Runtime config not found: {resolved_config_path}")
    raw_text = resolved_config_path.read_text(encoding="utf-8")
    parsed = yaml.safe_load(raw_text)
    if not isinstance(parsed, dict):
        raise ConfigError(f"Runtime config must parse to a mapping: {resolved_config_path}")
    _assert_no_secrets(parsed)
    _validate_mapping(parsed, _EXPECTED_CONFIG_SCHEMA, path="root")
    validate_sql_identifier(str(parsed["indexes"]["vector_table"]), "indexes.vector_table")
    validate_sql_identifier(str(parsed["indexes"]["graph_nodes_table"]), "indexes.graph_nodes_table")
    validate_sql_identifier(str(parsed["indexes"]["graph_edges_table"]), "indexes.graph_edges_table")
    return RuntimeConfig(repo_root=resolved_repo_root, config_path=resolved_config_path, raw=parsed)
