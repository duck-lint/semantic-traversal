"""Content-addressed identities for authored runtime prompts."""

from __future__ import annotations

import hashlib


def prompt_version(prompt: str) -> str:
    if not isinstance(prompt, str):
        raise TypeError("prompt must be text")
    return "sha256:" + hashlib.sha256(prompt.encode("utf-8")).hexdigest()


__all__ = ["prompt_version"]
