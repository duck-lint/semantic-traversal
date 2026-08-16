"""Provider-authoritative vector projection over the canonical substrate.

This module deliberately does not contain a tokenizer.  Ollama, with
``truncate`` disabled, is the authority for whether an input fits.  The
segmenter only tries ordered textual boundaries and verifies every accepted
prefix by making the provider request that produced its vector.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path, PurePosixPath
from typing import Any, Protocol

import numpy as np


VECTOR_DIMENSION = 1024
REQUESTED_MODEL = "qwen3-embedding:0.6b"
VECTOR_DTYPE = "float32"
NORMALIZATION_RULE = "l2"
SIMILARITY_METRIC = "cosine"


class VectorError(ValueError):
    """The vector contract or vector substrate is invalid."""


class EmbeddingProviderError(VectorError):
    """An embedding request failed at the provider boundary."""

    def __init__(self, message: str, *, capacity_exceeded: bool = False, status: int | None = None) -> None:
        super().__init__(message)
        self.capacity_exceeded = capacity_exceeded
        self.status = status


@dataclass(frozen=True)
class EmbeddingContract:
    requested_model: str
    resolved_model_identity: str
    embedding_dimension: int
    vector_dtype: str
    normalization_rule: str
    similarity_metric: str
    input_capacity: int
    capacity_mechanism: str = "ollama_provider_acceptance_truncate_false"
    tokenization_contract: str = "provider-opaque acceptance; no local tokenizer"


@dataclass(frozen=True)
class VectorTarget:
    target_kind: str
    target_identity: int | str
    input_text: str


@dataclass(frozen=True)
class VectorHit:
    target_kind: str
    target_identity: int | str
    score: float
    segment_ordinal: int


class EmbeddingProvider(Protocol):
    contract: EmbeddingContract

    def embed(self, text: str, *, truncate: bool = False) -> Any:
        """Return one provider embedding, rejecting truncation."""


def _normalized_vector(value: Any, dimension: int) -> np.ndarray:
    vector = np.asarray(value, dtype=np.float32)
    if vector.ndim != 1 or vector.shape[0] != dimension:
        raise VectorError(f"provider returned vector dimension {vector.shape}, expected ({dimension},)")
    if not np.isfinite(vector).all():
        raise VectorError("provider returned a non-finite embedding")
    norm = float(np.linalg.norm(vector))
    if not np.isfinite(norm) or norm == 0.0:
        raise VectorError("provider returned a zero-norm embedding")
    return (vector / norm).astype(np.float32, copy=False)


def _post_json(url: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read().decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            detail = {}
        message = str(detail.get("error", detail or exc.reason))
        raise EmbeddingProviderError(message, status=exc.code) from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise EmbeddingProviderError(f"embedding provider request failed: {exc}") from exc


class OllamaEmbeddingProvider:
    """Pinned Ollama provider whose acceptance boundary is authoritative."""

    def __init__(
        self,
        model: str = REQUESTED_MODEL,
        *,
        base_url: str = "http://127.0.0.1:11434",
        timeout: float = 120.0,
    ) -> None:
        if model != REQUESTED_MODEL:
            raise VectorError(f"the vector stage requires the pinned model {REQUESTED_MODEL!r}")
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.contract = self._load_contract()

    def _load_contract(self) -> EmbeddingContract:
        shown = _post_json(f"{self.base_url}/api/show", {"name": self.model}, self.timeout)
        info = shown.get("model_info", {})
        context_length = info.get("qwen3.context_length")
        dimension = info.get("qwen3.embedding_length")
        if not isinstance(context_length, int) or not isinstance(dimension, int):
            raise VectorError("Ollama model metadata does not expose qwen3 capacity and dimension")
        modelfile = str(shown.get("modelfile", ""))
        identity_match = re.search(r"sha256-[0-9a-f]{64}", modelfile, re.IGNORECASE)
        if identity_match is None:
            raise VectorError("Ollama model identity is not an immutable blob identity")
        return EmbeddingContract(
            self.model,
            identity_match.group(0).lower(),
            dimension,
            VECTOR_DTYPE,
            NORMALIZATION_RULE,
            SIMILARITY_METRIC,
            context_length,
        )

    def embed(self, text: str, *, truncate: bool = False) -> np.ndarray:
        if truncate:
            raise VectorError("provider truncation must remain disabled")
        try:
            response = _post_json(
                f"{self.base_url}/api/embed",
                {"model": self.model, "input": text, "truncate": False},
                self.timeout,
            )
        except EmbeddingProviderError as exc:
            # This is the specific Ollama response observed for truncate=false
            # overflow.  Other 400/500 responses remain provider failures.
            capacity = (
                exc.status == 400
                and str(exc).strip().lower() == "the input length exceeds the context length"
            )
            raise EmbeddingProviderError(str(exc), capacity_exceeded=capacity, status=exc.status) from exc
        embeddings = response.get("embeddings")
        if not isinstance(embeddings, list) or len(embeddings) != 1:
            raise EmbeddingProviderError("Ollama returned an unexpected embedding count")
        return _normalized_vector(embeddings[0], self.contract.embedding_dimension)


def _object_name(source_path: str) -> str:
    name = PurePosixPath(source_path).name
    return name[:-3] if name.lower().endswith(".md") else name


def vector_eligible_targets(connection: sqlite3.Connection) -> tuple[VectorTarget, ...]:
    """Return exact parsed-text units plus names for zero-unit objects."""
    unit_rows = connection.execute(
        "SELECT unit_id, source_object_uuid, parsed_text FROM canonical_units ORDER BY unit_id"
    ).fetchall()
    unit_counts: dict[str, int] = {}
    targets: list[VectorTarget] = []
    for unit_id, source_uuid, parsed_text in unit_rows:
        unit_counts[source_uuid] = unit_counts.get(source_uuid, 0) + 1
        if isinstance(parsed_text, str) and parsed_text.strip():
            targets.append(VectorTarget("semantic_unit", unit_id, parsed_text))
    for source_uuid, source_path in connection.execute(
        "SELECT source_object_uuid, source_path FROM canonical_objects ORDER BY canonical_ordinal"
    ):
        if unit_counts.get(source_uuid, 0) == 0:
            targets.append(VectorTarget("semantic_object", source_uuid, _object_name(source_path)))
    return tuple(targets)


def _accepted_prefix(provider: EmbeddingProvider, text: str) -> np.ndarray:
    try:
        return _normalized_vector(provider.embed(text, truncate=False), provider.contract.embedding_dimension)
    except EmbeddingProviderError:
        raise


def segment_and_embed(provider: EmbeddingProvider, text: str) -> tuple[tuple[str, np.ndarray], ...]:
    """Greedily segment by provider-confirmed textual boundaries."""
    if not isinstance(text, str) or not text:
        raise VectorError("vector input must be a non-empty string")
    cache: dict[str, np.ndarray] = {}

    def try_prefix(prefix: str) -> np.ndarray | None:
        if prefix in cache:
            return cache[prefix]
        try:
            value = _accepted_prefix(provider, prefix)
        except EmbeddingProviderError as exc:
            if exc.capacity_exceeded:
                return None
            raise
        cache[prefix] = value
        return value

    if (whole := try_prefix(text)) is not None:
        return ((text, whole),)

    output: list[tuple[str, np.ndarray]] = []
    remainder = text
    while remainder:
        # Each remainder is independently eligible to be one complete segment.
        remainder_vector = try_prefix(remainder)
        if remainder_vector is not None:
            output.append((remainder, remainder_vector))
            break
        candidates = [i + 1 for i, char in enumerate(remainder[:-1]) if char == "\n"]
        candidates.sort(reverse=True)
        candidates += sorted(
            (i + 1 for i, char in enumerate(remainder[:-1]) if char.isspace() and char != "\n"),
            reverse=True,
        )
        chosen: tuple[int, np.ndarray] | None = None
        for boundary in candidates:
            try:
                value = try_prefix(remainder[:boundary])
            except EmbeddingProviderError:
                raise
            if value is not None:
                chosen = (boundary, value)
                break
        if chosen is None:
            for boundary in range(len(remainder) - 1, 0, -1):
                value = try_prefix(remainder[:boundary])
                if value is None:
                    continue
                chosen = (boundary, value)
                break
            if chosen is None:
                raise VectorError("provider rejected every non-empty Unicode code-point prefix")
        boundary, value = chosen
        output.append((remainder[:boundary], value))
        remainder = remainder[boundary:]
    if "".join(segment for segment, _ in output) != text:
        raise VectorError("segmentation lost or altered input text")
    return tuple(output)


VECTOR_SCHEMA = """
CREATE TABLE vector_build_contract (
    contract_id INTEGER PRIMARY KEY CHECK (contract_id = 1),
    requested_model TEXT NOT NULL,
    resolved_model_identity TEXT NOT NULL,
    embedding_dimension INTEGER NOT NULL,
    vector_dtype TEXT NOT NULL,
    normalization_rule TEXT NOT NULL,
    similarity_metric TEXT NOT NULL,
    input_capacity INTEGER NOT NULL,
    capacity_mechanism TEXT NOT NULL,
    tokenization_contract TEXT NOT NULL,
    matrix_sha256 TEXT NOT NULL
);
CREATE TABLE vector_segments (
    matrix_row INTEGER PRIMARY KEY,
    target_kind TEXT NOT NULL CHECK (target_kind IN ('semantic_unit', 'semantic_object')),
    target_identity_json TEXT NOT NULL,
    segment_ordinal INTEGER NOT NULL,
    segment_text TEXT NOT NULL,
    unit_id INTEGER,
    source_object_uuid TEXT,
    UNIQUE (target_kind, target_identity_json, segment_ordinal),
    FOREIGN KEY (unit_id) REFERENCES canonical_units(unit_id),
    FOREIGN KEY (source_object_uuid) REFERENCES canonical_objects(source_object_uuid),
    CHECK ((target_kind = 'semantic_unit' AND unit_id IS NOT NULL AND source_object_uuid IS NULL)
        OR (target_kind = 'semantic_object' AND unit_id IS NULL AND source_object_uuid IS NOT NULL))
);
"""


def _require_production_contract(provider: EmbeddingProvider) -> None:
    contract = provider.contract
    if (
        contract.requested_model != REQUESTED_MODEL
        or not re.fullmatch(r"sha256-[0-9a-f]{64}", contract.resolved_model_identity, re.IGNORECASE)
        or contract.embedding_dimension != VECTOR_DIMENSION
        or contract.vector_dtype != VECTOR_DTYPE
        or contract.normalization_rule.lower() != NORMALIZATION_RULE
        or contract.similarity_metric.lower() != SIMILARITY_METRIC
        or contract.input_capacity <= 0
        or contract.capacity_mechanism != "ollama_provider_acceptance_truncate_false"
        or contract.tokenization_contract != "provider-opaque acceptance; no local tokenizer"
    ):
        raise VectorError("provider contract does not satisfy the pinned production vector contract")


def _write_temporary_matrix(directory: Path, matrix: np.ndarray) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(dir=directory, prefix=".vectors.", suffix=".npy")
    os.close(descriptor)
    path = Path(name)
    np.save(path, matrix, allow_pickle=False)
    return path


def _matrix_sha256(path: Path) -> str:
    import hashlib
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def build_vector_index(
    connection: sqlite3.Connection,
    matrix_path: str | os.PathLike[str],
    provider: EmbeddingProvider,
) -> None:
    """Embed every eligible canonical target and persist vectors plus mapping."""
    _require_production_contract(provider)
    targets = vector_eligible_targets(connection)
    rows: list[tuple[VectorTarget, int, str, np.ndarray]] = []
    vectors: list[np.ndarray] = []
    # Targets are independent.  Preserve target and segment order while
    # allowing the provider to serve independent requests concurrently.
    def embed_target(target: VectorTarget) -> tuple[tuple[str, np.ndarray], ...]:
        return segment_and_embed(provider, target.input_text)

    with ThreadPoolExecutor(max_workers=8) as executor:
        segmented_targets = tuple(executor.map(embed_target, targets))
    for target, segments in zip(targets, segmented_targets):
        for ordinal, (segment_text, vector) in enumerate(segments):
            rows.append((target, ordinal, segment_text, vector))
            vectors.append(vector)
    matrix = np.vstack(vectors).astype(np.float32, copy=False) if vectors else np.empty((0, VECTOR_DIMENSION), dtype=np.float32)
    active_path = Path(matrix_path)
    temporary_matrix = _write_temporary_matrix(active_path.parent, matrix)
    matrix_digest = _matrix_sha256(temporary_matrix)
    connection.execute("PRAGMA foreign_keys = ON")
    if connection.in_transaction:
        temporary_matrix.unlink(missing_ok=True)
        raise VectorError("vector build requires a connection with no active transaction")
    old_matrix: Path | None = None
    promoted = False
    try:
        connection.execute("BEGIN")
        connection.execute("DROP TABLE IF EXISTS vector_segments")
        connection.execute("DROP TABLE IF EXISTS vector_build_contract")
        for statement in VECTOR_SCHEMA.split(";"):
            if statement.strip():
                connection.execute(statement)
        c = provider.contract
        connection.execute(
            "INSERT INTO vector_build_contract VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (c.requested_model, c.resolved_model_identity, c.embedding_dimension, c.vector_dtype,
             c.normalization_rule, c.similarity_metric, c.input_capacity,
             c.capacity_mechanism, c.tokenization_contract, matrix_digest),
        )
        connection.executemany(
            """INSERT INTO vector_segments
            (matrix_row, target_kind, target_identity_json, segment_ordinal, segment_text, unit_id, source_object_uuid)
            VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                (row, target.target_kind, json.dumps(target.target_identity, ensure_ascii=False), ordinal,
                 segment_text, target.target_identity if target.target_kind == "semantic_unit" else None,
                 target.target_identity if target.target_kind == "semantic_object" else None)
                for row, (target, ordinal, segment_text, _) in enumerate(rows)
            ),
        )
        validate_vector_index(connection, temporary_matrix)
        active_path.parent.mkdir(parents=True, exist_ok=True)
        if active_path.exists():
            old_matrix = active_path.with_name(f".{active_path.name}.backup")
            old_matrix.unlink(missing_ok=True)
            os.replace(active_path, old_matrix)
        os.replace(temporary_matrix, active_path)
        promoted = True
        connection.commit()
        if old_matrix is not None:
            old_matrix.unlink(missing_ok=True)
    except sqlite3.IntegrityError as exc:
        connection.rollback()
        if promoted:
            active_path.unlink(missing_ok=True)
        if old_matrix is not None and old_matrix.exists():
            os.replace(old_matrix, active_path)
        temporary_matrix.unlink(missing_ok=True)
        raise VectorError(f"vector substrate integrity failure: {exc}") from exc
    except Exception:
        connection.rollback()
        if promoted:
            active_path.unlink(missing_ok=True)
        if old_matrix is not None and old_matrix.exists():
            os.replace(old_matrix, active_path)
        temporary_matrix.unlink(missing_ok=True)
        raise


def _stored_contract(connection: sqlite3.Connection) -> EmbeddingContract:
    row = connection.execute(
        """SELECT requested_model, resolved_model_identity, embedding_dimension, vector_dtype,
        normalization_rule, similarity_metric, input_capacity, capacity_mechanism,
        tokenization_contract FROM vector_build_contract WHERE contract_id = 1"""
    ).fetchone()
    if row is None:
        raise VectorError("vector build contract is absent")
    return EmbeddingContract(*row)


def validate_vector_index(connection: sqlite3.Connection, matrix_path: str | os.PathLike[str]) -> None:
    contract = _stored_contract(connection)
    matrix = np.load(matrix_path, allow_pickle=False)
    if matrix.dtype != np.float32 or matrix.ndim != 2 or matrix.shape[1] != contract.embedding_dimension:
        raise VectorError("vectors.npy does not match the persisted vector contract")
    if not np.isfinite(matrix).all() or (matrix.shape[0] and not np.allclose(np.linalg.norm(matrix, axis=1), 1.0, atol=1e-5)):
        raise VectorError("vectors.npy contains invalid or unnormalized vectors")
    digest = connection.execute("SELECT matrix_sha256 FROM vector_build_contract WHERE contract_id = 1").fetchone()[0]
    if _matrix_sha256(Path(matrix_path)) != digest:
        raise VectorError("vectors.npy does not match the persisted matrix identity")
    mapping_rows = connection.execute(
        "SELECT matrix_row, target_kind, target_identity_json, unit_id, source_object_uuid, segment_ordinal, segment_text FROM vector_segments"
    ).fetchall()
    count = len(mapping_rows)
    if count != matrix.shape[0]:
        raise VectorError("vector mapping count does not match vectors.npy")
    if sorted(row[0] for row in mapping_rows) != list(range(count)):
        raise VectorError("vector mapping matrix rows are not exactly dense")
    if connection.execute("PRAGMA foreign_key_check").fetchall():
        raise VectorError("vector substrate has foreign-key violations")
    for _, kind, identity_json, unit_id, source_uuid, _, _ in mapping_rows:
        identity = json.loads(identity_json)
        if kind == "semantic_unit":
            if not isinstance(identity, int) or isinstance(identity, bool) or unit_id != identity or source_uuid is not None:
                raise VectorError("inconsistent semantic-unit vector mapping")
            if connection.execute("SELECT 1 FROM canonical_units WHERE unit_id = ?", (unit_id,)).fetchone() is None:
                raise VectorError("vector mapping refers to an unknown canonical unit")
        elif kind == "semantic_object":
            if not isinstance(identity, str) or source_uuid != identity or unit_id is not None:
                raise VectorError("inconsistent semantic-object vector mapping")
            if connection.execute("SELECT 1 FROM canonical_objects WHERE source_object_uuid = ?", (source_uuid,)).fetchone() is None:
                raise VectorError("vector mapping refers to an unknown canonical object")
            if connection.execute("SELECT 1 FROM canonical_units WHERE source_object_uuid = ?", (source_uuid,)).fetchone() is not None:
                raise VectorError("semantic-object fallback mapping has canonical units")
        else:
            raise VectorError("unknown vector target kind")
    expected_targets = {
        (t.target_kind, json.dumps(t.target_identity, ensure_ascii=False)): t
        for t in vector_eligible_targets(connection)
    }
    grouped: dict[tuple[str, str], list[tuple[int, str]]] = {}
    for kind, identity, ordinal, segment_text in connection.execute(
        "SELECT target_kind, target_identity_json, segment_ordinal, segment_text FROM vector_segments ORDER BY matrix_row"
    ):
        grouped.setdefault((kind, identity), []).append((ordinal, segment_text))
    if set(expected_targets) != set(grouped):
        raise VectorError("vector mapping does not cover exactly the eligible canonical targets")
    for key, target in expected_targets.items():
        segments = grouped[key]
        if [ordinal for ordinal, _ in segments] != list(range(len(segments))):
            raise VectorError("vector segment ordinals are not dense and ordered")
        if "".join(text for _, text in segments) != target.input_text:
            raise VectorError("vector segment text does not reproduce the authorized input")


def vector_lookup(
    connection: sqlite3.Connection,
    matrix_path: str | os.PathLike[str],
    query_text: str,
    provider: EmbeddingProvider,
) -> tuple[VectorHit, ...]:
    """Return one exhaustive best-segment cosine hit per canonical target."""
    if not isinstance(query_text, str) or not query_text.strip():
        raise VectorError("vector query must be non-empty")
    validate_vector_index(connection, matrix_path)
    stored = _stored_contract(connection)
    if provider.contract != stored:
        raise VectorError("query provider contract does not match the persisted vector build")
    query = _normalized_vector(provider.embed(query_text, truncate=False), stored.embedding_dimension)
    matrix = np.load(matrix_path, allow_pickle=False)
    scores = matrix @ query
    best: dict[tuple[str, str], VectorHit] = {}
    for row, (kind, identity_json, ordinal) in enumerate(connection.execute(
        "SELECT target_kind, target_identity_json, segment_ordinal FROM vector_segments ORDER BY matrix_row"
    )):
        identity = json.loads(identity_json)
        key = (kind, identity_json)
        hit = VectorHit(kind, identity, float(scores[row]), ordinal)
        prior = best.get(key)
        if prior is None or hit.score > prior.score or (hit.score == prior.score and hit.segment_ordinal < prior.segment_ordinal):
            best[key] = hit
    return tuple(sorted(best.values(), key=lambda hit: (-hit.score, hit.target_kind, str(hit.target_identity))))
