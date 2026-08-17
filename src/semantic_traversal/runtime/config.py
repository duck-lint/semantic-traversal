"""Strict, secret-free configuration for implemented runtime execution."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.constructor import DuplicateKeyError
from ruamel.yaml.error import YAMLError


class RuntimeConfigError(ValueError):
    """The runtime configuration is absent, malformed, or unsupported."""


@dataclass(frozen=True)
class ModelConfig:
    provider: str
    model: str
    timeout_seconds: float
    prompt: str


@dataclass(frozen=True)
class PacketConfig:
    """Runtime-only retrieval-packet capacity policy."""

    max_occurrences: int

    def __post_init__(self) -> None:
        if isinstance(self.max_occurrences, bool) or not isinstance(self.max_occurrences, int):
            raise RuntimeConfigError("packet max_occurrences must be an integer")
        if self.max_occurrences <= 0:
            raise RuntimeConfigError("packet max_occurrences must be positive")


@dataclass(frozen=True)
class RuntimeConfig:
    router: ModelConfig
    retrieval_inference: ModelConfig
    packet: PacketConfig


def _load_values(path: str | Path) -> dict[str, Any]:
    yaml = YAML(typ="safe", pure=True)
    yaml.version = (1, 2)
    try:
        values = yaml.load(Path(path).read_text(encoding="utf-8")) or {}
    except DuplicateKeyError as exc:
        raise RuntimeConfigError("runtime configuration contains duplicate keys") from exc
    except (OSError, YAMLError) as exc:
        raise RuntimeConfigError(f"could not read runtime configuration: {exc}") from exc
    if not isinstance(values, dict):
        raise RuntimeConfigError("runtime configuration must contain a mapping")
    return dict(values)


def load_runtime_config(path: str | Path) -> RuntimeConfig:
    """Load the complete runtime configuration without reading credentials."""

    values = _load_values(path)
    if set(values) != {"router", "retrieval_inference", "packet"}:
        raise RuntimeConfigError(
            "runtime configuration must contain exactly router, retrieval_inference, and packet sections"
        )

    def parse_section(section_name: str) -> ModelConfig:
        raw_section = values[section_name]
        if not isinstance(raw_section, dict):
            raise RuntimeConfigError(f"runtime configuration {section_name} must be a mapping")
        expected_keys = {"provider", "model", "timeout_seconds", "prompt"}
        if set(raw_section) != expected_keys:
            raise RuntimeConfigError(
                f"runtime {section_name} configuration requires only provider, model, timeout_seconds, and prompt"
            )
        provider = raw_section["provider"]
        if provider != "openai":
            raise RuntimeConfigError(f"runtime {section_name} provider must be exactly 'openai'")
        model = raw_section["model"]
        if not isinstance(model, str) or not model.strip():
            raise RuntimeConfigError(f"runtime {section_name} model must be a non-empty explicit string")
        timeout_seconds = raw_section["timeout_seconds"]
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)):
            raise RuntimeConfigError(f"runtime {section_name} timeout_seconds must be numeric")
        if not math.isfinite(float(timeout_seconds)) or float(timeout_seconds) <= 0:
            raise RuntimeConfigError(f"runtime {section_name} timeout_seconds must be positive and finite")
        prompt = raw_section["prompt"]
        if not isinstance(prompt, str) or not prompt.strip():
            raise RuntimeConfigError(f"runtime {section_name} prompt must be non-empty text")
        return ModelConfig(provider, model, float(timeout_seconds), prompt)

    packet = values["packet"]
    if not isinstance(packet, dict) or set(packet) != {"max_occurrences"}:
        raise RuntimeConfigError("runtime packet configuration requires only max_occurrences")
    return RuntimeConfig(
        parse_section("router"),
        parse_section("retrieval_inference"),
        PacketConfig(packet["max_occurrences"]),
    )


__all__ = ["ModelConfig", "PacketConfig", "RuntimeConfig", "RuntimeConfigError", "load_runtime_config"]
