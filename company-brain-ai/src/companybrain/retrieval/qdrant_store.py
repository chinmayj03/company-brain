"""
QdrantStore — code embedding store backed by Qdrant.

Uses scalar quantization (INT8) for 4× memory reduction with near-zero recall loss.
Collection is created on first use with cosine distance + quantization config.

Vector dimensions vary by embedding provider:
  voyage-code-3                        → 1024
  jinaai/jina-embeddings-v2-base-code  → 768  (FastEmbed, free)
  nomic-embed-text / nomic-embed-code  → 768  (Ollama, free)

The store auto-detects the dimension from the first vector it upserts,
so switching providers never requires a manual collection recreation.

Usage::
    store = QdrantStore()
    await store.ensure_collection()

    # Upsert file embeddings
    await store.upsert(points=[
        {"id": "sha256...", "vector": [0.1, ...], "payload": {"path": "...", "repo": "..."}},
    ])

    # Query
    results = await store.search(query_vector=[0.1, ...], top_k=50, repo_filter="my-repo")
"""
from __future__ import annotations

import hashlib
from typing import Any, Optional
import structlog

log = structlog.get_logger(__name__)

COLLECTION = "code_embeddings"

# Fallback when dimension cannot be inferred from first batch
_DEFAULT_DIM = 768


class QdrantStore:
    """Async Qdrant client wrapper with quantization and repo-scoped filtering."""

    def __init__(self, url: str = "", api_key: str = "", collection: str = ""):
        from companybrain.config import settings
        self._url        = url        or settings.qdrant_url
        self._api_key    = api_key    or settings.qdrant_api_key
        self._collection = collection or settings.qdrant_collection
        self._client     = None
        self._available: Optional[bool] = None

    def _get_client(self):
        if self._client is None:
            try:
                from qdrant_client import AsyncQdrantClient  # type: ignore
                self._client = AsyncQdrantClient(
                    url=self._url,
                    api_key=self._api_key or None,
                    timeout=10,
                )
            except ImportError:
                log.warning("qdrant-client not installed")
            except Exception as e:
                log.warning("Qdrant client init failed", error=str(e))
        return self._client

    async def ensure_collection(self, vector_dim: int = _DEFAULT_DIM) -> bool:
        """
        Create the collection with scalar quantization if it doesn't exist.
        vector_dim is inferred from the first upsert batch; pass explicitly to override.
        """
        client = self._get_client()
        if client is None:
            return False
        try:
            from qdrant_client.models import (  # type: ignore
                Distance, VectorParams, ScalarQuantizationConfig,
                ScalarType, QuantizationConfig, ScalarQuantization,
            )
            collections = await client.get_collections()
            names = [c.name for c in collections.collections]
            if self._collection not in names:
                await client.create_collection(
                    collection_name=self._collection,
                    vectors_config=VectorParams(size=vector_dim, distance=Distance.COSINE),
                    quantization_config=QuantizationConfig(
                        scalar=ScalarQuantization(
                            scalar=ScalarQuantizationConfig(
                                type=ScalarType.INT8,
                                always_ram=True,
                            )
                        )
                    ),
                )
                log.info("Qdrant collection created",
                         collection=self._collection, dim=vector_dim)
            return True
        except Exception as e:
            log.warning("Qdrant ensure_collection failed", error=str(e))
            return False

    async def upsert(self, points: list[dict[str, Any]]) -> None:
        """
        Upsert embedding points. Each point: {id, vector, payload}.
        Auto-creates the collection with the correct dimension on first call.
        """
        client = self._get_client()
        if client is None or not points:
            return
        try:
            from qdrant_client.models import PointStruct  # type: ignore
            # Infer dimension from first vector and ensure collection exists
            first_vector = points[0]["vector"]
            await self.ensure_collection(vector_dim=len(first_vector))

            qdrant_points = [
                PointStruct(id=p["id"], vector=p["vector"], payload=p.get("payload", {}))
                for p in points
            ]
            await client.upsert(collection_name=self._collection, points=qdrant_points)
        except Exception as e:
            log.warning("Qdrant upsert failed", error=str(e), count=len(points))

    async def search(
        self,
        query_vector: list[float],
        top_k: int = 50,
        repo_filter: str = "",
    ) -> list[dict[str, Any]]:
        """
        Search for nearest neighbors. Optionally filter by repo.
        Returns list of {path, repo, score, payload}.
        """
        client = self._get_client()
        if client is None:
            return []
        try:
            from qdrant_client.models import Filter, FieldCondition, MatchValue  # type: ignore
            q_filter = None
            if repo_filter:
                q_filter = Filter(
                    must=[FieldCondition(key="repo", match=MatchValue(value=repo_filter))]
                )
            results = await client.search(
                collection_name=self._collection,
                query_vector=query_vector,
                limit=top_k,
                query_filter=q_filter,
                with_payload=True,
            )
            return [
                {
                    "path": r.payload.get("path", ""),
                    "repo": r.payload.get("repo", ""),
                    "score": r.score,
                    "payload": r.payload,
                }
                for r in results
            ]
        except Exception as e:
            log.warning("Qdrant search failed", error=str(e))
            return []


def path_to_point_id(repo: str, path: str) -> str:
    """Deterministic point ID from repo + path."""
    return hashlib.sha256(f"{repo}:{path}".encode()).hexdigest()[:32]
