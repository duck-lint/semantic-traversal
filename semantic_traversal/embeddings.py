from __future__ import annotations

import json
from importlib import import_module
from dataclasses import dataclass
from typing import Any, Protocol
from urllib import error, request

from .config import RuntimeConfig


_SENTENCE_TRANSFORMER_MODEL_CACHE: dict[tuple[str, str | None], Any] = {}


@dataclass(frozen=True)
class EmbeddingResponse:
    vectors: list[list[float]] | None
    metadata: dict[str, Any]
    status: str


class EmbeddingBackend(Protocol):
    mode_name: str

    def embed_texts(self, texts: list[str]) -> EmbeddingResponse:
        ...

    def embed_query_text(self, text: str) -> EmbeddingResponse:
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

    def embed_query_text(self, text: str) -> EmbeddingResponse:
        return self.embed_texts([text])


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

    def embed_query_text(self, text: str) -> EmbeddingResponse:
        return self.embed_texts([text])


class SentenceTransformersEmbeddingBackend:
    mode_name = "sentence_transformers"

    def __init__(
        self,
        *,
        model: str,
        batch_size: int,
        normalize_embeddings: bool,
        device: str | None,
    ) -> None:
        sentence_transformers_module = import_module("sentence_transformers")
        sentence_transformer_class = getattr(sentence_transformers_module, "SentenceTransformer", None)
        if sentence_transformer_class is None:
            raise RuntimeError("sentence_transformers.SentenceTransformer is unavailable")
        self._model = self._load_model(sentence_transformer_class, model=model, device=device)
        self._model_name = model
        self._batch_size = batch_size
        self._normalize_embeddings = normalize_embeddings
        self._device = device

    def _load_model(self, sentence_transformer_class: Any, *, model: str, device: str | None) -> Any:
        cache_key = (model, device)
        if cache_key in _SENTENCE_TRANSFORMER_MODEL_CACHE:
            return _SENTENCE_TRANSFORMER_MODEL_CACHE[cache_key]
        attempts: list[dict[str, Any]] = []
        if device is not None:
            attempts.append({"device": device, "local_files_only": False})
        else:
            attempts.append({"local_files_only": False})
        attempts.append({"device": device, "local_files_only": True} if device is not None else {"local_files_only": True})
        last_error: Exception | None = None
        for kwargs in attempts:
            try:
                if kwargs.get("device") is None:
                    kwargs = {key: value for key, value in kwargs.items() if key != "device"}
                loaded_model = sentence_transformer_class(model, **kwargs)
                _SENTENCE_TRANSFORMER_MODEL_CACHE[cache_key] = loaded_model
                return loaded_model
            except Exception as exc:  # noqa: BLE001
                last_error = exc
        if last_error is not None:
            raise last_error
        raise RuntimeError("unable to load sentence-transformers model")

    def embed_texts(self, texts: list[str]) -> EmbeddingResponse:
        return self._embed(texts, kind="document")

    def embed_query_text(self, text: str) -> EmbeddingResponse:
        return self._embed([text], kind="query")

    def _embed(self, texts: list[str], *, kind: str) -> EmbeddingResponse:
        if not texts:
            return EmbeddingResponse(
                vectors=[],
                metadata={
                    "backend_mode": self.mode_name,
                    "model": self._model_name,
                    "batch_size": self._batch_size,
                    "normalize_embeddings": self._normalize_embeddings,
                    "device": self._device,
                    "vector_count": 0,
                },
                status="embedded",
            )
        encode_method = getattr(self._model, "encode_query" if kind == "query" and hasattr(self._model, "encode_query") else "encode_document" if kind == "document" and hasattr(self._model, "encode_document") else "encode", None)
        if encode_method is None:
            return EmbeddingResponse(
                vectors=None,
                metadata={
                    "backend_mode": self.mode_name,
                    "model": self._model_name,
                    "batch_size": self._batch_size,
                    "normalize_embeddings": self._normalize_embeddings,
                    "device": self._device,
                    "error": "SentenceTransformer model does not expose an encode method",
                },
                status="unavailable",
            )
        try:
            raw_vectors = encode_method(
                texts,
                batch_size=self._batch_size,
                normalize_embeddings=self._normalize_embeddings,
                show_progress_bar=False,
                device=self._device,
            )
        except TypeError:
            raw_vectors = encode_method(
                texts,
                batch_size=self._batch_size,
                normalize_embeddings=self._normalize_embeddings,
                show_progress_bar=False,
            )
        except Exception as exc:
            return EmbeddingResponse(
                vectors=None,
                metadata={
                    "backend_mode": self.mode_name,
                    "model": self._model_name,
                    "batch_size": self._batch_size,
                    "normalize_embeddings": self._normalize_embeddings,
                    "device": self._device,
                    "error": str(exc),
                },
                status="unavailable",
            )
        vectors = _coerce_vector_rows(raw_vectors, expected_count=len(texts))
        if vectors is None:
            return EmbeddingResponse(
                vectors=None,
                metadata={
                    "backend_mode": self.mode_name,
                    "model": self._model_name,
                    "batch_size": self._batch_size,
                    "normalize_embeddings": self._normalize_embeddings,
                    "device": self._device,
                    "error": "invalid vector payload",
                },
                status="invalid_payload",
            )
        return EmbeddingResponse(
            vectors=vectors,
            metadata={
                "backend_mode": self.mode_name,
                "model": self._model_name,
                "batch_size": self._batch_size,
                "normalize_embeddings": self._normalize_embeddings,
                "device": self._device,
                "vector_count": len(vectors),
            },
            status="embedded",
        )


def resolve_embedding_backend(config: RuntimeConfig) -> EmbeddingBackend:
    provider = config.embedding_provider.strip().lower()
    model = config.embedding_model
    base_url = config.embedding_base_url
    timeout_seconds = config.embedding_request_timeout_seconds
    if provider == "sentence_transformers":
        if not isinstance(model, str) or not model.strip():
            return UnavailableEmbeddingBackend(reason="embedding model is not configured")
        try:
            return SentenceTransformersEmbeddingBackend(
                model=model.strip(),
                batch_size=config.embedding_batch_size,
                normalize_embeddings=config.embedding_normalize_embeddings,
                device=config.embedding_device,
            )
        except ModuleNotFoundError as exc:
            return UnavailableEmbeddingBackend(reason=f"sentence-transformers package is not installed: {exc}")
        except Exception as exc:
            return UnavailableEmbeddingBackend(reason=f"failed to load sentence-transformers model {model.strip()}: {exc}")
    if provider == "ollama":
        if not isinstance(model, str) or not model.strip():
            return UnavailableEmbeddingBackend(reason="embedding model is not configured")
        if not isinstance(base_url, str) or not base_url.strip():
            return UnavailableEmbeddingBackend(reason="embedding base_url is not configured")
        return OllamaEmbeddingBackend(model=model.strip(), base_url=base_url.strip(), timeout_seconds=timeout_seconds)
    return UnavailableEmbeddingBackend(reason=f"unsupported embedding provider: {provider}")


def _coerce_vector_rows(raw_vectors: Any, *, expected_count: int) -> list[list[float]] | None:
    if hasattr(raw_vectors, "tolist"):
        raw_vectors = raw_vectors.tolist()
    if isinstance(raw_vectors, tuple):
        raw_vectors = list(raw_vectors)
    if not isinstance(raw_vectors, list):
        raw_vectors = [raw_vectors]
    if raw_vectors and all(isinstance(value, (int, float)) for value in raw_vectors):
        raw_vectors = [raw_vectors]
    if len(raw_vectors) != expected_count:
        return None
    normalized_vectors: list[list[float]] = []
    for vector in raw_vectors:
        if hasattr(vector, "tolist"):
            vector = vector.tolist()
        if isinstance(vector, tuple):
            vector = list(vector)
        if not isinstance(vector, list) or not vector or not all(isinstance(value, (int, float)) for value in vector):
            return None
        normalized_vectors.append([float(value) for value in vector])
    return normalized_vectors
