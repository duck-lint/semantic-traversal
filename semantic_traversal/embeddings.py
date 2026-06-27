from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol
from urllib import error, request

from .config import RuntimeConfig


@dataclass(frozen=True)
class EmbeddingResponse:
    vectors: list[list[float]] | None
    metadata: dict[str, Any]
    status: str


class EmbeddingBackend(Protocol):
    mode_name: str

    def embed_texts(self, texts: list[str]) -> EmbeddingResponse:
        ...


class UnavailableEmbeddingBackend:
    mode_name = "unavailable"

    def __init__(self, *, reason: str) -> None:
        self._reason = reason

    def embed_texts(self, texts: list[str]) -> EmbeddingResponse:
        return EmbeddingResponse(
            vectors=None,
            metadata={"backend_mode": self.mode_name, "reason": self._reason},
            status="unavailable",
        )


class OllamaEmbeddingBackend:
    mode_name = "ollama"

    def __init__(self, *, model: str, base_url: str, timeout_seconds: int) -> None:
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds

    def embed_texts(self, texts: list[str]) -> EmbeddingResponse:
        vectors: list[list[float]] = []
        for text in texts:
            payload = {"model": self._model, "prompt": text}
            try:
                http_request = request.Request(
                    f"{self._base_url}/api/embeddings",
                    data=json.dumps(payload).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with request.urlopen(http_request, timeout=self._timeout_seconds) as response:
                    envelope = json.loads(response.read().decode("utf-8"))
            except (error.HTTPError, error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
                return EmbeddingResponse(
                    vectors=None,
                    metadata={
                        "backend_mode": self.mode_name,
                        "model": self._model,
                        "base_url": self._base_url,
                        "error": str(exc),
                    },
                    status="unavailable",
                )
            embedding = envelope.get("embedding")
            if not isinstance(embedding, list) or not all(isinstance(value, (int, float)) for value in embedding):
                return EmbeddingResponse(
                    vectors=None,
                    metadata={
                        "backend_mode": self.mode_name,
                        "model": self._model,
                        "base_url": self._base_url,
                        "error": "invalid embedding payload",
                    },
                    status="invalid_payload",
                )
            vectors.append([float(value) for value in embedding])
        return EmbeddingResponse(
            vectors=vectors,
            metadata={
                "backend_mode": self.mode_name,
                "model": self._model,
                "base_url": self._base_url,
                "vector_count": len(vectors),
            },
            status="embedded",
        )


def resolve_embedding_backend(config: RuntimeConfig) -> EmbeddingBackend:
    model = config.embedding_model
    base_url = config.embedding_base_url
    timeout_seconds = config.embedding_request_timeout_seconds
    if not isinstance(model, str) or not model.strip():
        return UnavailableEmbeddingBackend(reason="embedding model is not configured")
    return OllamaEmbeddingBackend(model=model.strip(), base_url=base_url, timeout_seconds=timeout_seconds)
