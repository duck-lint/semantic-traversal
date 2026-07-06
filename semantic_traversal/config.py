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
    "retrieval": {
        "max_chunks": int,
        "exact": {
            "max_matches": int,
            "context_chars": int,
        },
        "lexical": {
            "max_candidates": int,
        },
        "vector": {
            "max_candidates": int,
        },
        "graph": {
            "default_depth": int,
            "max_depth": int,
            "max_candidates": int,
        },
        "scope_aliases": dict,
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
        "vault_root": str,
        "data_root": str,
        "vault_source_label": str,
        "vault_exclude_globs": [str],
    },
    "prompts": {
        "semantic_compiler": {
            "template": str,
        },
        "frontier_synthesis": {
            "instructions": str,
        },
    },
    "chunking": {
        "required_uuid_field": str,
        "max_chunk_chars": int,
        "semantic_frontmatter_fields": [str],
        "low_signal_apparatus": {
            "enabled": bool,
            "skip_during_ingest": bool,
            "skip_during_graph_representatives": bool,
            "exact_lines": [str],
            "prefixes": [str],
            "contains": [str],
            "short_all_caps_max_chars": int,
        },
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
class RuntimeConfig:
    repo_root: Path
    config_path: Path
    raw: dict[str, Any]

    @property
    def data_root(self) -> Path:
        raw_data_root = Path(str(self.raw["paths"]["data_root"]))
        if raw_data_root.is_absolute():
            return raw_data_root.resolve()
        return (self.vault_root / raw_data_root).resolve()

    @property
    def max_retrieval_chunks(self) -> int:
        return int(self.raw["retrieval"]["max_chunks"])

    @property
    def retrieval_exact_max_matches(self) -> int:
        return int(self.raw["retrieval"]["exact"]["max_matches"])

    @property
    def retrieval_exact_context_chars(self) -> int:
        return int(self.raw["retrieval"]["exact"]["context_chars"])

    @property
    def retrieval_lexical_max_candidates(self) -> int:
        return int(self.raw["retrieval"]["lexical"]["max_candidates"])

    @property
    def retrieval_vector_max_candidates(self) -> int:
        return int(self.raw["retrieval"]["vector"]["max_candidates"])

    @property
    def retrieval_graph_default_depth(self) -> int:
        return int(self.raw["retrieval"]["graph"]["default_depth"])

    @property
    def retrieval_graph_max_depth(self) -> int:
        return int(self.raw["retrieval"]["graph"]["max_depth"])

    @property
    def retrieval_graph_max_candidates(self) -> int:
        return int(self.raw["retrieval"]["graph"]["max_candidates"])

    @property
    def retrieval_scope_aliases(self) -> dict[str, dict[str, tuple[str, ...] | str | None]]:
        aliases: dict[str, dict[str, tuple[str, ...] | str | None]] = {}
        raw_aliases = self.raw["retrieval"].get("scope_aliases", {})
        if not isinstance(raw_aliases, dict):
            return aliases
        for alias, payload in raw_aliases.items():
            if not isinstance(payload, dict):
                continue
            aliases[str(alias)] = {
                "source_label": str(payload.get("source_label") or "").strip() or None,
                "note_type": tuple(str(value) for value in payload.get("note_type", []) if str(value).strip())
                if isinstance(payload.get("note_type"), list)
                else (),
                "path_contains": tuple(str(value) for value in payload.get("path_contains", []) if str(value).strip())
                if isinstance(payload.get("path_contains"), list)
                else (),
            }
        return aliases

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
    def vault_root(self) -> Path:
        return self.resolve_path(str(self.raw["paths"]["vault_root"]))

    @property
    def vault_source_label(self) -> str:
        return str(self.raw["paths"]["vault_source_label"])

    @property
    def vault_exclude_globs(self) -> tuple[str, ...]:
        return tuple(str(value) for value in self.raw["paths"]["vault_exclude_globs"])

    @property
    def semantic_compiler_prompt_template(self) -> str:
        return str(self.raw["prompts"]["semantic_compiler"]["template"])

    @property
    def frontier_synthesis_instructions(self) -> str:
        return str(self.raw["prompts"]["frontier_synthesis"]["instructions"])

    @property
    def chunking_required_uuid_field(self) -> str:
        return str(self.raw["chunking"]["required_uuid_field"])

    @property
    def chunking_max_chunk_chars(self) -> int:
        return int(self.raw["chunking"]["max_chunk_chars"])

    @property
    def chunking_semantic_frontmatter_fields(self) -> tuple[str, ...]:
        return tuple(str(value) for value in self.raw["chunking"]["semantic_frontmatter_fields"])

    @property
    def chunking_low_signal_apparatus_enabled(self) -> bool:
        return bool(self.raw["chunking"]["low_signal_apparatus"]["enabled"])

    @property
    def chunking_low_signal_apparatus_skip_during_ingest(self) -> bool:
        return bool(self.raw["chunking"]["low_signal_apparatus"]["skip_during_ingest"])

    @property
    def chunking_low_signal_apparatus_skip_during_graph_representatives(self) -> bool:
        return bool(self.raw["chunking"]["low_signal_apparatus"]["skip_during_graph_representatives"])

    @property
    def chunking_low_signal_apparatus_exact_lines(self) -> tuple[str, ...]:
        return tuple(str(value) for value in self.raw["chunking"]["low_signal_apparatus"]["exact_lines"])

    @property
    def chunking_low_signal_apparatus_prefixes(self) -> tuple[str, ...]:
        return tuple(str(value) for value in self.raw["chunking"]["low_signal_apparatus"]["prefixes"])

    @property
    def chunking_low_signal_apparatus_contains(self) -> tuple[str, ...]:
        return tuple(str(value) for value in self.raw["chunking"]["low_signal_apparatus"]["contains"])

    @property
    def chunking_low_signal_apparatus_short_all_caps_max_chars(self) -> int:
        return int(self.raw["chunking"]["low_signal_apparatus"]["short_all_caps_max_chars"])

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


def _validate_prompt_text(value: str, *, field: str, required_marker: str | None = None) -> None:
    if not value.strip():
        raise ConfigError(f"Runtime config prompt field must not be blank: {field}")
    if required_marker is not None and required_marker not in value:
        raise ConfigError(f"Runtime config prompt field {field} must include {required_marker}")

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
    if not str(parsed["paths"]["vault_root"]).strip():
        raise ConfigError("Runtime config field paths.vault_root must not be blank")
    if not str(parsed["paths"]["vault_source_label"]).strip():
        raise ConfigError("Runtime config field paths.vault_source_label must not be blank")
    if not str(parsed["paths"]["data_root"]).strip():
        raise ConfigError("Runtime config field paths.data_root must not be blank")
    if int(parsed["retrieval"]["max_chunks"]) <= 0:
        raise ConfigError("Runtime config field retrieval.max_chunks must be greater than zero")
    raw_scope_aliases = parsed["retrieval"].get("scope_aliases", {})
    if not isinstance(raw_scope_aliases, dict):
        raise ConfigError("Runtime config field retrieval.scope_aliases must be a mapping")
    for alias, alias_payload in raw_scope_aliases.items():
        if not isinstance(alias_payload, dict):
            raise ConfigError(f"Runtime config field retrieval.scope_aliases.{alias} must be a mapping")
        for field in ("note_type", "path_contains"):
            if field in alias_payload and not isinstance(alias_payload[field], list):
                raise ConfigError(f"Runtime config field retrieval.scope_aliases.{alias}.{field} must be a list")
        if "source_label" in alias_payload and not isinstance(alias_payload["source_label"], (str, type(None))):
            raise ConfigError(f"Runtime config field retrieval.scope_aliases.{alias}.source_label must be a string or null")
    for field in (
        "exact.max_matches",
        "exact.context_chars",
        "lexical.max_candidates",
        "vector.max_candidates",
        "graph.default_depth",
        "graph.max_depth",
        "graph.max_candidates",
    ):
        current: Any = parsed["retrieval"]
        for part in field.split("."):
            current = current[part]
        if int(current) < 0:
            raise ConfigError(f"Runtime config field retrieval.{field} must be non-negative")
    _validate_prompt_text(
        str(parsed["prompts"]["semantic_compiler"]["template"]),
        field="prompts.semantic_compiler.template",
        required_marker="{packet}",
    )
    _validate_prompt_text(
        str(parsed["prompts"]["frontier_synthesis"]["instructions"]),
        field="prompts.frontier_synthesis.instructions",
    )
    low_signal_apparatus = parsed["chunking"].get("low_signal_apparatus", {})
    if not isinstance(low_signal_apparatus, dict):
        raise ConfigError("Runtime config field chunking.low_signal_apparatus must be a mapping")
    for field in ("exact_lines", "prefixes", "contains"):
        if field in low_signal_apparatus and not isinstance(low_signal_apparatus[field], list):
            raise ConfigError(f"Runtime config field chunking.low_signal_apparatus.{field} must be a list")
    for field in ("enabled", "skip_during_ingest", "skip_during_graph_representatives"):
        if field in low_signal_apparatus and not isinstance(low_signal_apparatus[field], bool):
            raise ConfigError(f"Runtime config field chunking.low_signal_apparatus.{field} must be a boolean")
    if "short_all_caps_max_chars" in low_signal_apparatus and int(low_signal_apparatus["short_all_caps_max_chars"]) < 0:
        raise ConfigError("Runtime config field chunking.low_signal_apparatus.short_all_caps_max_chars must be non-negative")
    validate_sql_identifier(str(parsed["indexes"]["vector_table"]), "indexes.vector_table")
    validate_sql_identifier(str(parsed["indexes"]["graph_nodes_table"]), "indexes.graph_nodes_table")
    validate_sql_identifier(str(parsed["indexes"]["graph_edges_table"]), "indexes.graph_edges_table")
    return RuntimeConfig(repo_root=resolved_repo_root, config_path=resolved_config_path, raw=parsed)
