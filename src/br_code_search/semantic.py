"""Optional local embeddings and hybrid retrieval.

The standard-library hashing backend keeps the feature usable offline.  A
SentenceTransformers backend can be selected explicitly when a local model is
installed; no model download is triggered by the default path.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import re
from contextlib import closing
from dataclasses import dataclass
from typing import Any, Callable, Protocol, Sequence

from .core import IDENTIFIER_RE, CodeSearchIndex, add_project_version_filters, utc_now


TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|[0-9]+|[\u3400-\u9fff]+")
MAJOR_SYMBOL_TYPES = {"program", "action", "function_block", "function", "c_function", "source_file"}

# Small, explainable B&R control vocabulary expansion for the dependency-free
# fallback.  It improves queries such as "reset alarm" vs. "fault restart"
# without pretending to replace a trained language model.
CONCEPT_GROUPS = {
    "alarm": {"alarm", "alarms", "warning", "warnings", "fault", "faults", "error", "errors"},
    "reset": {"reset", "restart", "reinitialize", "reinit", "clear", "cleared"},
    "timeout": {"timeout", "timedout", "expired", "watchdog", "deadline"},
    "motion": {"axis", "axes", "motion", "drive", "drives", "servo", "homing"},
    "sequence": {"sequence", "step", "steps", "state", "states", "cycle", "cyclic"},
    "cam": {"cam", "cams", "profile", "profiles", "camdata", "camtable"},
    "communication": {"opcua", "opc", "ua", "network", "socket", "tcp", "udp", "communication"},
}
CONCEPT_BY_TOKEN = {
    token: group for group, tokens in CONCEPT_GROUPS.items() for token in tokens
}
BackendFactory = Callable[[str | None, int], "EmbeddingBackend"]
_BACKEND_FACTORIES: dict[str, BackendFactory] = {}


class EmbeddingBackend(Protocol):
    """Minimal backend contract used by the SQLite vector cache."""

    @property
    def key(self) -> str: ...

    @property
    def dimension(self) -> int: ...

    def encode(self, texts: Sequence[str]) -> list[list[float]]: ...


@dataclass(slots=True)
class HashEmbeddingBackend:
    """Deterministic offline vectorizer used when no ML runtime is installed."""

    dimension: int = 256

    @property
    def key(self) -> str:
        return f"hashing:{self.dimension}"

    @staticmethod
    def _features(text: str) -> list[tuple[str, float]]:
        tokens = [token.casefold() for token in TOKEN_RE.findall(text)]
        features: list[tuple[str, float]] = [(token, 1.0) for token in tokens]
        for token in tokens:
            concept = CONCEPT_BY_TOKEN.get(token)
            if concept:
                features.append((f"concept:{concept}", 0.75))
            if len(token) >= 4:
                for index in range(len(token) - 2):
                    features.append((f"tri:{token[index:index + 3]}", 0.2))
        return features

    def encode(self, texts: Sequence[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in texts:
            vector = [0.0] * self.dimension
            for feature, weight in self._features(text):
                digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
                bucket = int.from_bytes(digest[:4], "big") % self.dimension
                sign = 1.0 if digest[4] & 1 else -1.0
                vector[bucket] += sign * weight
            norm = math.sqrt(sum(value * value for value in vector))
            if norm:
                vector = [value / norm for value in vector]
            vectors.append(vector)
        return vectors


class SentenceTransformersBackend:
    """Adapter for an explicitly installed local SentenceTransformers model."""

    def __init__(self, model_name: str):
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore[import-not-found]
        except ImportError as exc:
            raise ValueError(
                "sentence-transformers is not installed; install the optional 'semantic' extra "
                "or use backend='hashing'"
            ) from exc
        if not model_name.strip():
            raise ValueError("A model name/path is required for backend='sentence_transformers'")
        self.model_name = model_name.strip()
        self._model = SentenceTransformer(self.model_name)
        probe = self._model.encode([""], normalize_embeddings=True, show_progress_bar=False)
        self._dimension = len(probe[0])

    @property
    def key(self) -> str:
        return f"sentence_transformers:{self.model_name}"

    @property
    def dimension(self) -> int:
        return self._dimension

    def encode(self, texts: Sequence[str]) -> list[list[float]]:
        values = self._model.encode(
            list(texts), normalize_embeddings=True, show_progress_bar=False
        )
        return [[float(value) for value in vector] for vector in values]


def register_embedding_backend(name: str, factory: BackendFactory) -> None:
    """Register an application-provided backend factory before hybrid search."""
    normalized = name.strip().casefold()
    if not normalized or normalized in {"hashing", "offline", "fallback", "auto", "sentence_transformers", "sentence-transformers", "st"}:
        raise ValueError("custom embedding backend name must be non-empty and not a built-in name")
    _BACKEND_FACTORIES[normalized] = factory


def create_embedding_backend(
    name: str = "hashing",
    *,
    model: str | None = None,
    dimension: int = 256,
) -> EmbeddingBackend:
    normalized = (name or "hashing").strip().casefold()
    custom_factory = _BACKEND_FACTORIES.get(normalized)
    if custom_factory is not None:
        return custom_factory(model, int(dimension))
    if normalized in {"hashing", "offline", "fallback"}:
        return HashEmbeddingBackend(dimension=max(32, min(int(dimension), 4096)))
    if normalized in {"sentence_transformers", "sentence-transformers", "st"}:
        return SentenceTransformersBackend(model or "sentence-transformers/all-MiniLM-L6-v2")
    if normalized == "auto":
        if model:
            try:
                return SentenceTransformersBackend(model)
            except ValueError:
                pass
        return HashEmbeddingBackend(dimension=max(32, min(int(dimension), 4096)))
    raise ValueError("embedding backend must be one of: hashing, sentence_transformers, auto")


def inspect_embedding_backend(
    name: str = "hashing",
    *,
    model: str | None = None,
    dimension: int = 256,
) -> dict[str, Any]:
    """Report backend availability without loading or downloading a model."""
    normalized = (name or "hashing").strip().casefold()
    safe_dimension = max(32, min(int(dimension), 4096))
    if normalized in {"hashing", "offline", "fallback"}:
        return {
            "ok": True,
            "available": True,
            "requested_backend": name,
            "backend": f"hashing:{safe_dimension}",
            "backend_kind": "offline_hashing_fallback",
            "dimension": safe_dimension,
            "model": None,
            "message": "Dependency-free deterministic fallback is available.",
        }
    if normalized in {"sentence_transformers", "sentence-transformers", "st"}:
        installed = importlib.util.find_spec("sentence_transformers") is not None
        selected_model = model or "sentence-transformers/all-MiniLM-L6-v2"
        return {
            "ok": True,
            "available": installed,
            "requested_backend": name,
            "backend": f"sentence_transformers:{selected_model}",
            "backend_kind": "trained_local_model",
            "dimension": None,
            "model": selected_model,
            "message": (
                "sentence-transformers is installed; model loading is deferred until hybrid search."
                if installed
                else "sentence-transformers is not installed; install the optional 'semantic' extra."
            ),
        }
    if normalized == "auto":
        if model and importlib.util.find_spec("sentence_transformers") is not None:
            return {
                "ok": True,
                "available": True,
                "requested_backend": name,
                "backend": f"sentence_transformers:{model}",
                "backend_kind": "trained_local_model",
                "dimension": None,
                "model": model,
                "message": "Auto mode will use the selected local SentenceTransformers model.",
            }
        result = inspect_embedding_backend("hashing", dimension=safe_dimension)
        result.update({"requested_backend": name, "message": "Auto mode will use the offline hashing fallback."})
        return result
    if normalized in _BACKEND_FACTORIES:
        return {
            "ok": True,
            "available": True,
            "requested_backend": name,
            "backend": normalized,
            "backend_kind": "custom_backend",
            "dimension": safe_dimension,
            "model": model,
            "message": "A registered custom backend factory is available.",
        }
    raise ValueError("embedding backend must be one of: hashing, sentence_transformers, auto, or a registered backend")


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right) or not left or not right:
        return 0.0
    numerator = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if not left_norm or not right_norm:
        return 0.0
    return numerator / (left_norm * right_norm)


def _cached_vectors(
    index: CodeSearchIndex,
    backend: EmbeddingBackend,
    rows: Sequence[Any],
    *,
    batch_size: int = 32,
) -> tuple[dict[int, list[float]], int, int]:
    """Load or create vectors; return vectors, cache hits and newly encoded count."""
    with closing(index.connect()) as connection, connection:
        # Upgrade an existing v0.4.x database lazily when hybrid search is the
        # first command used after installing v0.5.
        index._initialize(connection)
        if not rows:
            return {}, 0, 0
        cached = {
            int(row["document_id"]): row
            for row in connection.execute(
                """SELECT document_id, content_hash, dimension, vector_json
                FROM document_embeddings WHERE backend_key=?""",
                (backend.key,),
            )
        }
        vectors: dict[int, list[float]] = {}
        missing: list[Any] = []
        cache_hits = 0
        for row in rows:
            item = cached.get(int(row["id"]))
            if item is not None and item["content_hash"] == row["content_hash"] and int(item["dimension"]) == backend.dimension:
                try:
                    value = json.loads(item["vector_json"])
                    if isinstance(value, list) and len(value) == backend.dimension:
                        vectors[int(row["id"])] = [float(number) for number in value]
                        cache_hits += 1
                        continue
                except (TypeError, ValueError, json.JSONDecodeError):
                    pass
            missing.append(row)
        encoded_count = 0
        for start in range(0, len(missing), max(1, batch_size)):
            batch = missing[start:start + max(1, batch_size)]
            encoded = backend.encode([str(row["content"]) for row in batch])
            if len(encoded) != len(batch):
                raise ValueError("Embedding backend returned an unexpected batch length")
            records = []
            for row, vector in zip(batch, encoded):
                if len(vector) != backend.dimension:
                    raise ValueError(
                        f"Embedding backend dimension mismatch: expected {backend.dimension}, got {len(vector)}"
                    )
                document_id = int(row["id"])
                vectors[document_id] = vector
                records.append(
                    (
                        document_id,
                        backend.key,
                        row["content_hash"],
                        backend.dimension,
                        json.dumps(vector, separators=(",", ":")),
                        utc_now(),
                    )
                )
            connection.executemany(
                """INSERT OR REPLACE INTO document_embeddings
                (document_id, backend_key, content_hash, dimension, vector_json, indexed_at)
                VALUES (?, ?, ?, ?, ?, ?)""",
                records,
            )
            encoded_count += len(batch)
    return vectors, cache_hits, encoded_count


def _filtered_rows(
    index: CodeSearchIndex,
    *,
    project: str | None,
    origin: str | None,
    language: str | None,
    as_version: str | None,
    ar_version: str | None,
    cpu_model: str | None,
    library: str | None,
    library_version: str | None,
    quality: str | None,
    verified_only: bool,
    include_deprecated: bool,
    max_documents: int,
) -> list[Any]:
    filters: list[str] = []
    parameters: list[Any] = []
    if project:
        filters.append("p.name = ? COLLATE NOCASE")
        parameters.append(project)
    if origin and origin != "all":
        filters.append("d.origin = ?")
        parameters.append(origin)
    if language:
        filters.append("d.language = ?")
        parameters.append(language)
    add_project_version_filters(
        filters,
        parameters,
        as_version=as_version,
        ar_version=ar_version,
        cpu_model=cpu_model,
        library=library,
        library_version=library_version,
    )
    if quality:
        filters.append("p.quality = ?")
        parameters.append(quality)
    if verified_only:
        filters.append("p.verified = 1")
    if not include_deprecated and quality != "deprecated":
        filters.append("p.deprecated = 0 AND p.do_not_copy = 0")
    where = " AND ".join(filters) or "1=1"
    limit = max(1, min(int(max_documents), 50000))
    with closing(index.connect()) as connection, connection:
        return connection.execute(
            f"""SELECT d.id, p.name AS project_name, d.relative_path, d.language, d.origin,
            d.symbol_name, d.symbol_type, d.start_line, d.end_line, d.encoding, d.content,
            d.content_hash, d.target_cpu_models, d.target_ar_versions, d.target_configurations,
            p.as_version, p.project_version, p.automation_runtime_versions, p.cpu_models, p.metadata_json,
            p.quality, p.verified, p.deprecated, p.do_not_copy, p.notes
            FROM documents d JOIN projects p ON p.id=d.project_id
            WHERE {where}
            ORDER BY CASE p.quality WHEN 'gold' THEN 0 WHEN 'normal' THEN 1 ELSE 2 END,
                     p.verified DESC,
                     CASE d.origin WHEN 'user' THEN 0 WHEN 'library' THEN 1 ELSE 2 END,
                     d.id
            LIMIT ?""",
            [*parameters, limit],
        ).fetchall()


def _lexical_score(query_tokens: set[str], row: Any) -> float:
    content_tokens = {token.casefold() for token in IDENTIFIER_RE.findall(row["content"]) if len(token) > 1}
    if not query_tokens or not content_tokens:
        return 0.0
    overlap = len(query_tokens & content_tokens)
    score = overlap / max(1, len(query_tokens))
    symbol = str(row["symbol_name"] or "").casefold()
    if symbol and any(token in symbol for token in query_tokens):
        score += 0.35
    path = str(row["relative_path"]).casefold()
    if any(token in path for token in query_tokens):
        score += 0.15
    return min(1.0, score)


def _structural_score(row: Any) -> float:
    score = 0.0
    if row["symbol_type"] in MAJOR_SYMBOL_TYPES:
        score += 0.55
    elif row["symbol_type"] in {"variable_block", "data_type"}:
        score += 0.25
    if str(row["language"]).startswith("structured_text"):
        score += 0.2
    if row["origin"] == "user":
        score += 0.15
    if row["quality"] == "gold":
        score += 0.1
    if row["verified"]:
        score += 0.1
    return min(1.0, score)


def hybrid_search(
    index: CodeSearchIndex,
    query: str,
    *,
    project: str | None = None,
    origin: str | None = None,
    language: str | None = None,
    as_version: str | None = None,
    ar_version: str | None = None,
    cpu_model: str | None = None,
    library: str | None = None,
    library_version: str | None = None,
    quality: str | None = None,
    verified_only: bool = False,
    include_deprecated: bool = False,
    limit: int = 10,
    include_source: bool = True,
    max_chars_per_result: int = 4000,
    aggregate_files: bool = False,
    backend: str = "hashing",
    model: str | None = None,
    dimension: int = 256,
    semantic_weight: float = 0.5,
    lexical_weight: float = 0.35,
    structural_weight: float = 0.15,
    max_documents: int = 50000,
) -> dict[str, Any]:
    """Search all eligible units with hybrid semantic/lexical/structural scoring."""
    index._ensure_index()
    with closing(index.connect()) as connection, connection:
        index._initialize(connection)
    query = query.strip()
    if not query:
        raise ValueError("query must not be empty")
    limit = max(1, min(int(limit), 50))
    weights = [max(0.0, float(semantic_weight)), max(0.0, float(lexical_weight)), max(0.0, float(structural_weight))]
    total = sum(weights)
    if total <= 0:
        raise ValueError("at least one hybrid weight must be positive")
    semantic_weight, lexical_weight, structural_weight = [weight / total for weight in weights]
    embedding = create_embedding_backend(backend, model=model, dimension=dimension)
    rows = _filtered_rows(
        index,
        project=project,
        origin=origin,
        language=language,
        as_version=as_version,
        ar_version=ar_version,
        cpu_model=cpu_model,
        library=library,
        library_version=library_version,
        quality=quality,
        verified_only=verified_only,
        include_deprecated=include_deprecated,
        max_documents=max_documents,
    )
    query_vector = embedding.encode([query])[0]
    vectors, cache_hits, encoded_count = _cached_vectors(index, embedding, rows)
    query_tokens = {token.casefold() for token in IDENTIFIER_RE.findall(query) if len(token) > 1}
    scored: list[tuple[float, float, float, float, Any]] = []
    for row in rows:
        semantic_score = max(0.0, min(1.0, (_cosine(query_vector, vectors[int(row["id"])]) + 1.0) / 2.0))
        lexical_score = _lexical_score(query_tokens, row)
        structural_score = _structural_score(row)
        score = (
            semantic_weight * semantic_score
            + lexical_weight * lexical_score
            + structural_weight * structural_score
        )
        scored.append((score, semantic_score, lexical_score, structural_score, row))
    scored.sort(key=lambda item: (-item[0], item[4]["project_name"], item[4]["relative_path"], item[4]["start_line"]))
    candidate_count = len(scored)
    ranked = scored[: max(limit, limit * 5 if aggregate_files else limit)]
    if aggregate_files:
        rows_for_aggregation = [item[4] for item in ranked]
        results = index._aggregate_file_rows(
            rows_for_aggregation,
            include_source=include_source,
            max_chars_per_result=max_chars_per_result,
            limit=limit,
        )
        score_by_path: dict[tuple[str, str], tuple[float, float, float, float]] = {}
        for score, semantic_score, lexical_score, structural_score, row in ranked:
            score_by_path.setdefault(
                (row["project_name"], row["relative_path"]),
                (score, semantic_score, lexical_score, structural_score),
            )
        for payload in results:
            components = score_by_path.get((payload["project"], payload["path"]))
            if components:
                payload.update(
                    {
                        "hybrid_score": round(components[0], 6),
                        "semantic_score": round(components[1], 6),
                        "lexical_score": round(components[2], 6),
                        "structural_score": round(components[3], 6),
                    }
                )
    else:
        results = []
        for score, semantic_score, lexical_score, structural_score, row in ranked[:limit]:
            payload = index._row_payload(row, include_source=include_source, max_chars=max_chars_per_result)
            payload.update(
                {
                    "hybrid_score": round(score, 6),
                    "semantic_score": round(semantic_score, 6),
                    "lexical_score": round(lexical_score, 6),
                    "structural_score": round(structural_score, 6),
                }
            )
            results.append(payload)
    backend_kind = (
        "trained_local_model"
        if isinstance(embedding, SentenceTransformersBackend)
        else "offline_hashing_fallback"
        if isinstance(embedding, HashEmbeddingBackend)
        else "custom_backend"
    )
    return {
        "ok": True,
        "mode": "hybrid",
        "query": query,
        "backend": embedding.key,
        "backend_kind": backend_kind,
        "filters": {
            "project": project,
            "origin": origin,
            "language": language,
            "as_version": as_version,
            "ar_version": ar_version,
            "cpu_model": cpu_model,
            "library": library,
            "library_version": library_version,
            "quality": quality,
            "verified_only": verified_only,
            "include_deprecated": include_deprecated,
            "aggregate_files": aggregate_files,
        },
        "weights": {
            "semantic": round(semantic_weight, 6),
            "lexical": round(lexical_weight, 6),
            "structural": round(structural_weight, 6),
        },
        "candidate_count": candidate_count,
        "embedding_cache_hits": cache_hits,
        "embedding_documents_encoded": encoded_count,
        "count": len(results),
        "results": results,
        "note": (
            "Hashing fallback is deterministic and offline but is not a trained language model; "
            "select backend='sentence_transformers' with a local model for true semantic embeddings."
            if isinstance(embedding, HashEmbeddingBackend)
            else "Semantic scores come from the selected local SentenceTransformers model."
            if isinstance(embedding, SentenceTransformersBackend)
            else "Semantic scores come from a registered custom embedding backend."
        ),
    }
