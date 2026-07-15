"""Optional Qdrant export and query adapter for the local SQLite embedding cache.

Qdrant is deliberately an optional sink.  The core index and hybrid search
remain dependency-free; this module only imports ``qdrant-client`` when an
export is explicitly requested.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

from .core import CodeSearchIndex
from .semantic import _cached_vectors, _filtered_rows, create_embedding_backend


def inspect_qdrant() -> dict[str, Any]:
    """Report whether qdrant-client is installed without opening a client."""
    try:
        installed = importlib.util.find_spec("qdrant_client") is not None
    except (ImportError, ModuleNotFoundError):
        installed = False
    return {
        "ok": True,
        "available": installed,
        "backend": "qdrant",
        "message": (
            "qdrant-client is installed; export and semantic query are available on explicit request."
            if installed
            else "qdrant-client is not installed; install the optional 'qdrant' extra."
        ),
    }


def _load_qdrant() -> tuple[Any, Any]:
    try:
        from qdrant_client import QdrantClient, models
    except ImportError as exc:  # pragma: no cover - depends on optional environment
        raise ValueError("qdrant-client is not installed; install the optional 'qdrant' extra") from exc
    return QdrantClient, models


def _json_list(value: Any) -> list[Any]:
    try:
        parsed = json.loads(value or "[]")
    except (TypeError, json.JSONDecodeError):
        return []
    return parsed if isinstance(parsed, list) else []


def export_qdrant(
    index: CodeSearchIndex,
    *,
    path: str | Path | None = None,
    url: str | None = None,
    collection: str = "br_code_search",
    backend: str = "hashing",
    model: str | None = None,
    dimension: int = 256,
    project: str | None = None,
    origin: str | None = None,
    language: str | None = None,
    max_documents: int = 50000,
    batch_size: int = 256,
    recreate: bool = False,
) -> dict[str, Any]:
    """Export cached vectors and B&R metadata to a local or remote Qdrant collection."""
    if path and url:
        raise ValueError("path and url are mutually exclusive")
    if not path and not url:
        path = index.database_path.parent / "qdrant"
    collection = collection.strip()
    if not collection:
        raise ValueError("collection must not be empty")
    QdrantClient, models = _load_qdrant()
    embedding = create_embedding_backend(backend, model=model, dimension=dimension)
    rows = _filtered_rows(
        index,
        project=project,
        origin=origin,
        language=language,
        as_version=None,
        ar_version=None,
        cpu_model=None,
        library=None,
        library_version=None,
        quality=None,
        verified_only=False,
        include_deprecated=False,
        max_documents=max_documents,
    )
    vectors, cache_hits, encoded_count = _cached_vectors(index, embedding, rows)
    client = None
    storage = str(url) if url else str(Path(path).expanduser().resolve())
    try:
        if url:
            client = QdrantClient(url=url)
        else:
            local_path = Path(path).expanduser().resolve()
            local_path.mkdir(parents=True, exist_ok=True)
            client = QdrantClient(path=str(local_path))
        if recreate:
            try:
                client.delete_collection(collection_name=collection)
            except Exception:
                pass
        try:
            client.get_collection(collection_name=collection)
        except Exception:
            client.create_collection(
                collection_name=collection,
                vectors_config=models.VectorParams(size=embedding.dimension, distance=models.Distance.COSINE),
            )
        safe_batch = max(1, min(int(batch_size), 2048))
        points_written = 0
        for start in range(0, len(rows), safe_batch):
            batch = []
            for row in rows[start : start + safe_batch]:
                batch.append(
                    models.PointStruct(
                        id=int(row["id"]),
                        vector=vectors[int(row["id"])],
                        payload={
                            "document_id": int(row["id"]),
                            "project": row["project_name"],
                            "path": row["relative_path"],
                            "language": row["language"],
                            "origin": row["origin"],
                            "symbol": row["symbol_name"],
                            "symbol_type": row["symbol_type"],
                            "start_line": int(row["start_line"]),
                            "end_line": int(row["end_line"]),
                            "quality": row["quality"],
                            "verified": bool(row["verified"]),
                            "deprecated": bool(row["deprecated"]),
                            "do_not_copy": bool(row["do_not_copy"]),
                            "as_version": row["as_version"],
                            "project_version": row["project_version"],
                            "ar_versions": _json_list(row["automation_runtime_versions"]),
                            "cpu_models": _json_list(row["cpu_models"]),
                            "target_cpu_models": _json_list(row["target_cpu_models"]),
                            "target_ar_versions": _json_list(row["target_ar_versions"]),
                            "target_configurations": _json_list(row["target_configurations"]),
                        },
                    )
                )
            if batch:
                client.upsert(collection_name=collection, points=batch, wait=True)
                points_written += len(batch)
    finally:
        if client is not None and hasattr(client, "close"):
            client.close()
    return {
        "ok": True,
        "backend": "qdrant",
        "storage": storage,
        "collection": collection,
        "embedding_backend": embedding.key,
        "candidate_count": len(rows),
        "points_written": points_written,
        "embedding_cache_hits": cache_hits,
        "embedding_documents_encoded": encoded_count,
        "recreate": bool(recreate),
        "payload_fields": [
            "project", "path", "language", "origin", "symbol", "symbol_type",
            "quality", "verified", "deprecated", "do_not_copy",
            "target_cpu_models", "target_ar_versions", "target_configurations",
        ],
        "note": "Qdrant stores vectors and metadata; fetch source text from SQLite by document_id to avoid duplicating the reference repository.",
    }


def _open_client(QdrantClient: Any, *, path: str | Path | None, url: str | None) -> tuple[Any, str]:
    if path and url:
        raise ValueError("path and url are mutually exclusive")
    if not path and not url:
        raise ValueError("path or url is required")
    if url:
        return QdrantClient(url=url), str(url)
    local_path = Path(path).expanduser().resolve()
    if not local_path.exists():
        raise ValueError(f"Qdrant path does not exist: {local_path}")
    return QdrantClient(path=str(local_path)), str(local_path)


def _qdrant_filter(models: Any, *, project: str | None, origin: str | None, language: str | None) -> Any:
    conditions = []
    for key, value in (("project", project), ("origin", origin if origin and origin != "all" else None), ("language", language)):
        if value:
            conditions.append(models.FieldCondition(key=key, match=models.MatchValue(value=value)))
    return models.Filter(must=conditions) if conditions else None


def search_qdrant(
    index: CodeSearchIndex,
    query: str,
    *,
    path: str | Path | None = None,
    url: str | None = None,
    collection: str = "br_code_search",
    backend: str = "hashing",
    model: str | None = None,
    dimension: int = 256,
    project: str | None = None,
    origin: str | None = None,
    language: str | None = None,
    quality: str | None = None,
    verified_only: bool = False,
    include_deprecated: bool = False,
    limit: int = 10,
    include_source: bool = True,
    max_chars_per_result: int = 4000,
    score_threshold: float | None = None,
) -> dict[str, Any]:
    """Query an explicitly configured Qdrant collection and hydrate source from SQLite."""
    query = query.strip()
    if not query:
        raise ValueError("query must not be empty")
    collection = collection.strip()
    if not collection:
        raise ValueError("collection must not be empty")
    safe_limit = max(1, min(int(limit), 50))
    safe_chars = max(200, min(int(max_chars_per_result), 30000))
    QdrantClient, models = _load_qdrant()
    embedding = create_embedding_backend(backend, model=model, dimension=dimension)
    if not path and not url:
        path = index.database_path.parent / "qdrant"
    client, storage = _open_client(QdrantClient, path=path, url=url)
    try:
        try:
            client.get_collection(collection_name=collection)
        except Exception as exc:
            raise ValueError(f"Qdrant collection does not exist: {collection}") from exc
        query_filter = _qdrant_filter(models, project=project, origin=origin, language=language)
        # Fetch extra candidates so SQLite-side quality/deprecation filters do not
        # hide valid results when Qdrant stores older payload schemas.
        candidate_limit = max(safe_limit, min(500, safe_limit * 5))
        vector = embedding.encode([query])[0]
        if hasattr(client, "query_points"):
            response = client.query_points(
                collection_name=collection,
                query=vector,
                query_filter=query_filter,
                limit=candidate_limit,
                with_payload=True,
                with_vectors=False,
                score_threshold=score_threshold,
            )
            points = list(getattr(response, "points", []) or [])
        else:  # pragma: no cover - compatibility with older qdrant-client
            points = list(
                client.search(
                    collection_name=collection,
                    query_vector=vector,
                    query_filter=query_filter,
                    limit=candidate_limit,
                    with_payload=True,
                    score_threshold=score_threshold,
                )
            )
        results: list[dict[str, Any]] = []
        for rank, point in enumerate(points, start=1):
            payload = dict(getattr(point, "payload", None) or {})
            document_id = payload.get("document_id", getattr(point, "id", None))
            try:
                document_id = int(document_id)
            except (TypeError, ValueError):
                continue
            try:
                hydrated = index.get_symbol(document_id, max_chars=safe_chars)["result"]
            except ValueError:
                continue
            if project and str(hydrated.get("project", "")).casefold() != project.casefold():
                continue
            if origin and origin != "all" and hydrated.get("origin") != origin:
                continue
            if language and hydrated.get("language") != language:
                continue
            if quality and hydrated.get("quality") != quality:
                continue
            if verified_only and not hydrated.get("verified"):
                continue
            if not include_deprecated and hydrated.get("quality") == "deprecated":
                continue
            if not include_deprecated and (hydrated.get("deprecated") or hydrated.get("do_not_copy")):
                continue
            if not include_source:
                hydrated.pop("source", None)
                hydrated.pop("source_truncated", None)
            hydrated["qdrant_score"] = float(getattr(point, "score", 0.0) or 0.0)
            hydrated["qdrant_rank"] = rank
            hydrated["qdrant_payload"] = {
                key: payload.get(key)
                for key in ("project", "path", "language", "origin", "symbol", "symbol_type", "target_cpu_models", "target_ar_versions", "target_configurations")
                if key in payload
            }
            results.append(hydrated)
            if len(results) >= safe_limit:
                break
    finally:
        if hasattr(client, "close"):
            client.close()
    return {
        "ok": True,
        "mode": "qdrant",
        "query": query,
        "storage": storage,
        "collection": collection,
        "embedding_backend": embedding.key,
        "filters": {
            "project": project,
            "origin": origin,
            "language": language,
            "quality": quality,
            "verified_only": verified_only,
            "include_deprecated": include_deprecated,
        },
        "count": len(results),
        "results": results,
        "note": "Vectors and metadata come from Qdrant; authoritative source text and validation metadata are hydrated from SQLite.",
    }
