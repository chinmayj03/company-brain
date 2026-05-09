"""
Qdrant store implementations for the Company Brain retrieval stack.

Two classes are provided:
  QdrantBrainStore  — ADR-0015: BrainStore consumer that mirrors BrainEntity
                      writes into BM25 + Qdrant collections.
  QdrantStore       — Legacy async Qdrant client wrapper (backward compat).
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Optional

import structlog

log = structlog.get_logger(__name__)


# ── ADR-0015: QdrantBrainStore ────────────────────────────────────────────────

class QdrantBrainStore:
    """Mirror BrainEntity into BM25 + Qdrant. Read path queries via HybridSearcher."""

    def __init__(self, brain_root: Path, workspace_slug: str,
                 embedder=None):
        from companybrain.retrieval.bm25_index import Bm25Index
        from companybrain.retrieval.embedder import make_embedder
        from companybrain.retrieval.qdrant_client import make_client

        self.brain_root = Path(brain_root)
        self.workspace_slug = workspace_slug
        self.embedder = embedder or make_embedder()
        self.qdrant = make_client()
        self._bm25_cache: dict[str, Bm25Index] = {}
        self._buffer: list[tuple[Any, str]] = []   # (entity, indexable_text)

    async def write(self, entity, *, run_id: str, workspace_id: str) -> None:
        text = _build_indexable_text(entity)
        self._buffer.append((entity, text))

    async def read(self, entity_id: str):
        return None  # use HybridSearcher for reads

    async def is_fresh(self, entity_id: str, version_hash: str) -> bool:
        return False

    async def list_ids(self):
        if False:
            yield  # pragma: no cover

    async def commit_run(self, run_id: str) -> None:
        if not self._buffer:
            return

        from companybrain.retrieval.bm25_index import Bm25Index
        from companybrain.retrieval.qdrant_client import (
            collection_name, ensure_collection, upsert_point,
        )
        from companybrain.retrieval.tokenize import tokenize_code

        by_type: dict[str, list[tuple[Any, str]]] = {}
        for be, text in self._buffer:
            by_type.setdefault(be.entity_type, []).append((be, text))

        for entity_type, group in by_type.items():
            # BM25 upsert
            idx = self._bm25_cache.setdefault(
                entity_type,
                Bm25Index(self.brain_root, self.workspace_slug, entity_type),
            )
            for be, text in group:
                idx.upsert(be.id, text)
            idx.flush()

            # Qdrant: ensure collection then upsert points
            ensure_collection(self.qdrant, self.workspace_slug,
                              entity_type, self.embedder.dim)
            coll = collection_name(self.workspace_slug, entity_type)
            texts = [t for _, t in group]
            embeddings = self.embedder.embed_batch(texts)
            for (be, text), emb in zip(group, embeddings):
                tokens = tokenize_code(text)
                tf: dict[int, float] = {}
                for tok in tokens:
                    h = hash(tok) % (2 ** 31)
                    tf[h] = tf.get(h, 0.0) + 1.0
                indices = list(tf.keys())
                values  = list(tf.values())
                payload = {
                    "urn": be.id,
                    "repo": be.repo,
                    "entity_type": be.entity_type,
                    "qualified_name": be.qualified_name,
                    "t1_summary": be.t1_summary,
                    "file": be.file,
                }
                upsert_point(self.qdrant, collection=coll, point_id=be.id,
                             dense=emb, sparse_indices=indices, sparse_values=values,
                             payload=payload)

        log.info("Qdrant store commit complete",
                 entities=len(self._buffer),
                 types=list(by_type.keys()))
        self._buffer.clear()


def _build_indexable_text(e: Any) -> str:
    """Concatenate all human-relevant fields for retrieval."""
    meta = getattr(e, "metadata", {}) or {}
    parts = [
        getattr(e, "qualified_name", ""),
        getattr(e, "t1_summary", ""),
        getattr(e, "t0_token", ""),
        getattr(e, "t1_token", ""),
        meta.get("signature", ""),
        meta.get("code_snippet", "") or "",
        # Tier 3.A: include SQL/JPQL so DatabaseQuery and InterfaceMethod entities
        # are retrievable by SQL keywords (SELECT, WHERE, etc.) and table names.
        meta.get("query_text", "") or "",
    ]
    return " \n".join(p for p in parts if p)


# ── Legacy: QdrantStore ───────────────────────────────────────────────────────

COLLECTION = "code_embeddings"
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
        client = self._get_client()
        if client is None or not points:
            return
        try:
            from qdrant_client.models import PointStruct  # type: ignore
            first_vector = points[0]["vector"]
            await self.ensure_collection(vector_dim=len(first_vector))
            qdrant_points = [
                PointStruct(id=p["id"], vector=p["vector"], payload=p.get("payload", {}))
                for p in points
            ]
            await client.upsert(collection_name=self._collection, points=qdrant_points)
        except Exception as e:
            log.warning("Qdrant upsert failed", error=str(e), count=len(points))

    async def search(self, query_vector: list[float], top_k: int = 50,
                     repo_filter: str = "") -> list[dict[str, Any]]:
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
                {"path": r.payload.get("path", ""), "repo": r.payload.get("repo", ""),
                 "score": r.score, "payload": r.payload}
                for r in results
            ]
        except Exception as e:
            log.warning("Qdrant search failed", error=str(e))
            return []


def path_to_point_id(repo: str, path: str) -> str:
    return hashlib.sha256(f"{repo}:{path}".encode()).hexdigest()[:32]
