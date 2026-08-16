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
class RouterConfig:
    provider: str
    model: str
    timeout_seconds: float


@dataclass(frozen=True)
class RuntimeConfig:
    router: RouterConfig


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
    if set(values) != {"router"}:
        raise RuntimeConfigError("runtime configuration must contain only the router section")
    raw_router = values["router"]
    if not isinstance(raw_router, dict):
        raise RuntimeConfigError("runtime configuration router must be a mapping")
    if set(raw_router) != {"provider", "model", "timeout_seconds"}:
        raise RuntimeConfigError("runtime router configuration requires only provider, model, and timeout_seconds")

    provider = raw_router["provider"]
    if provider != "openai":
        raise RuntimeConfigError("runtime router provider must be exactly 'openai'")
    model = raw_router["model"]
    if not isinstance(model, str) or not model.strip():
        raise RuntimeConfigError("runtime router model must be a non-empty explicit string")
    timeout_seconds = raw_router["timeout_seconds"]
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)):
        raise RuntimeConfigError("runtime router timeout_seconds must be numeric")
    if not math.isfinite(float(timeout_seconds)) or float(timeout_seconds) <= 0:
        raise RuntimeConfigError("runtime router timeout_seconds must be positive and finite")
    return RuntimeConfig(RouterConfig(provider, model, float(timeout_seconds)))


__all__ = ["RouterConfig", "RuntimeConfig", "RuntimeConfigError", "load_runtime_config"]
