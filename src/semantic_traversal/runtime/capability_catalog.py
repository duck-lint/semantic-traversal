"""Single immutable loader for the completed capability-catalog artifact."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping


class CapabilityCatalogError(ValueError):
    """The supplied capability catalog artifact is not a supported artifact."""


@dataclass(frozen=True)
class CapabilityCatalogArtifact:
    text: str
    parsed: Mapping[str, Any]
    sha256: str


def _freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def load_capability_catalog(path: str | Path) -> CapabilityCatalogArtifact:
    try:
        raw = Path(path).read_bytes()
    except OSError as exc:
        raise CapabilityCatalogError(f"could not read capability catalog: {exc}") from exc
    digest = "sha256:" + hashlib.sha256(raw).hexdigest()
    try:
        text = raw.decode("utf-8")
        parsed = json.loads(text)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CapabilityCatalogError(f"capability catalog is not valid UTF-8 JSON: {exc}") from exc
    if not isinstance(parsed, dict) or parsed.get("catalog_schema_version") != "1":
        raise CapabilityCatalogError("capability catalog schema version is unsupported")
    required = {"semantic_dimensions", "graph", "vector", "operators"}
    if not required.issubset(parsed):
        raise CapabilityCatalogError("capability catalog envelope is incomplete")
    return CapabilityCatalogArtifact(text, _freeze(parsed), digest)


__all__ = ["CapabilityCatalogArtifact", "CapabilityCatalogError", "load_capability_catalog"]