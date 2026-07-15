"""Optional Qdrant export for the local SQLite embedding cache.

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
            "qdrant-client is installed; export is available on explicit request."
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
            "target_cpu_models", "target_ar_versions", "target_configurations",
        ],
        "note": "Qdrant stores vectors and metadata; fetch source text from SQLite by document_id to avoid duplicating the reference repository.",
    }
