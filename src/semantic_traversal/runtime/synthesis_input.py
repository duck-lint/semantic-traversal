"""Canonical provider-neutral input for a future synthesis boundary.

This module stops at the deterministic semantic input artifact.  It does not
choose a provider, map roles to provider transport roles, persist a run, or
transform ``EvidenceProjection`` into another evidence representation.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from .conversation import Conversation
from .retrieval.evidence_projection import (
    EVIDENCE_PROJECTION_CONTRACT_VERSION,
    EvidenceProjection,
    EvidenceProjectionError,
    evidence_projection_json,
)


SYNTHESIS_INPUT_CONTRACT_VERSION = "synthesis-input-v1"
LEGAL_SYNTHESIS_ROUTES = frozenset({"direct", "semantic_retrieval"})
_LEGAL_MESSAGE_ROLES = frozenset({"user", "synthesis"})


class SynthesisInputError(ValueError):
    """A synthesis-input contract or canonical-serialization violation."""


@dataclass(frozen=True)
class SynthesisMessage:
    """Immutable dialogue state without persistence or runtime lineage."""

    ordinal: int
    role: str
    content: str

    def __post_init__(self) -> None:
        if isinstance(self.ordinal, bool) or not isinstance(self.ordinal, int) or self.ordinal < 0:
            raise SynthesisInputError("synthesis message ordinal must be a non-negative integer")
        if not isinstance(self.role, str) or self.role not in _LEGAL_MESSAGE_ROLES:
            raise SynthesisInputError("synthesis message role must be 'user' or 'synthesis'")
        if not isinstance(self.content, str) or not self.content.strip():
            raise SynthesisInputError("synthesis message content must be nonblank text")


def _validate_conversation(messages: tuple[SynthesisMessage, ...]) -> None:
    if not isinstance(messages, tuple):
        raise SynthesisInputError("synthesis conversation must be an immutable tuple")
    if not messages:
        raise SynthesisInputError("synthesis conversation must not be empty")
    for ordinal, message in enumerate(messages):
        if not isinstance(message, SynthesisMessage):
            raise SynthesisInputError("synthesis conversation contains an invalid message")
        if message.ordinal != ordinal:
            raise SynthesisInputError("synthesis conversation ordinals must be contiguous from zero")
    if messages[-1].role != "user":
        raise SynthesisInputError("synthesis conversation must end with a user message")


@dataclass(frozen=True)
class SynthesisInput:
    """The canonical semantic input artifact consumed by a later model seam."""

    contract_version: str
    route: str
    conversation: tuple[SynthesisMessage, ...]
    evidence: EvidenceProjection | None

    def __post_init__(self) -> None:
        if self.contract_version != SYNTHESIS_INPUT_CONTRACT_VERSION:
            raise SynthesisInputError("unsupported synthesis-input contract version")
        if not isinstance(self.route, str) or self.route not in LEGAL_SYNTHESIS_ROUTES:
            raise SynthesisInputError(f"unsupported synthesis route: {self.route!r}")
        _validate_conversation(self.conversation)
        if self.route == "direct":
            if self.evidence is not None:
                raise SynthesisInputError("direct synthesis input cannot contain evidence")
            return
        if not isinstance(self.evidence, EvidenceProjection):
            raise SynthesisInputError("semantic-retrieval synthesis input requires EvidenceProjection")
        if self.evidence.contract_version != EVIDENCE_PROJECTION_CONTRACT_VERSION:
            raise SynthesisInputError("unsupported EvidenceProjection contract")


def snapshot_synthesis_conversation(conversation: Conversation) -> tuple[SynthesisMessage, ...]:
    """Copy only canonical dialogue meaning from an already materialized conversation."""

    if not isinstance(conversation, Conversation):
        raise SynthesisInputError("conversation must be a Conversation")
    snapshot = tuple(
        SynthesisMessage(message.ordinal, message.role, message.content)
        for message in conversation.messages
    )
    _validate_conversation(snapshot)
    return snapshot


def build_synthesis_input(
    conversation: Conversation,
    route: str,
    evidence: EvidenceProjection | None,
) -> SynthesisInput:
    """Build one validated provider-neutral synthesis input without I/O."""

    return SynthesisInput(
        SYNTHESIS_INPUT_CONTRACT_VERSION,
        route,
        snapshot_synthesis_conversation(conversation),
        evidence,
    )


def _conversation_json(messages: tuple[SynthesisMessage, ...]) -> list[dict[str, object]]:
    return [
        {"ordinal": message.ordinal, "role": message.role, "content": message.content}
        for message in messages
    ]


def synthesis_input_json(synthesis_input: SynthesisInput) -> dict[str, object]:
    """Return the structured canonical JSON value, excluding internal source state."""

    if not isinstance(synthesis_input, SynthesisInput):
        raise SynthesisInputError("synthesis input must be SynthesisInput")
    return {
        "contract_version": synthesis_input.contract_version,
        "route": synthesis_input.route,
        "conversation": _conversation_json(synthesis_input.conversation),
        "evidence": None if synthesis_input.evidence is None else evidence_projection_json(synthesis_input.evidence),
    }


def serialize_synthesis_input(synthesis_input: SynthesisInput) -> str:
    """Serialize exact canonical provider-neutral input bytes as compact JSON."""

    try:
        return json.dumps(
            synthesis_input_json(synthesis_input),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (SynthesisInputError, EvidenceProjectionError):
        raise
    except Exception as exc:
        raise SynthesisInputError(f"could not serialize synthesis input: {exc}") from exc


def synthesis_input_sha256(synthesis_input: SynthesisInput) -> str:
    """Return exact canonical-input content identity, not semantic identity."""

    if not isinstance(synthesis_input, SynthesisInput):
        raise SynthesisInputError("synthesis input must be SynthesisInput")
    serialized = serialize_synthesis_input(synthesis_input)
    return "sha256:" + hashlib.sha256(serialized.encode("utf-8")).hexdigest()


__all__ = [
    "LEGAL_SYNTHESIS_ROUTES",
    "SYNTHESIS_INPUT_CONTRACT_VERSION",
    "SynthesisInput",
    "SynthesisInputError",
    "SynthesisMessage",
    "build_synthesis_input",
    "serialize_synthesis_input",
    "snapshot_synthesis_conversation",
    "synthesis_input_json",
    "synthesis_input_sha256",
]
