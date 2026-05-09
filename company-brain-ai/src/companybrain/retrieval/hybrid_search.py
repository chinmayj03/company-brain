"""
Hybrid search implementations for the Company Brain retrieval stack.

Two searchers are provided:
  HybridSearcher      — ADR-0015: entity-based BM25S + dense + RRF, operates
                        over BrainEntity collections in Qdrant + .brain/.bm25/.
  FileHybridSearcher  — Legacy file-based searcher (kept for code_tracer compat).
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import structlog

from companybrain.retrieval.bm25_index import Bm25Index, BM25Index, BM25Result
from companybrain.retrieval.embedder import Embedder, make_embedder, CodeEmbedder
from companybrain.retrieval.qdrant_client import (
    collection_name, ensure_collection, make_client,
)

log = structlog.get_logger(__name__)

RRF_K = 60


# ── ADR-0015: Entity-based HybridSearcher ─────────────────────────────────────

@dataclass
class SearchHit:
    urn: str
    score: float
    payload: dict = field(default_factory=dict)
    bm25_rank: int | None = None
    dense_rank: int | None = None


class HybridSearcher:
    """BM25S + dense + RRF fusion over BrainEntity collections.

    One instance per (brain_root, workspace_slug) pair.
    Reads BM25 corpora from .brain/.bm25/ and dense vectors from Qdrant.
    """

    def __init__(self, brain_root: Path, workspace_slug: str,
                 embedder: Embedder | None = None):
        self.brain_root = Path(brain_root)
        self.workspace_slug = workspace_slug
        self.embedder = embedder or make_embedder()
        self.qdrant = make_client()
        self._bm25_cache: dict[str, Bm25Index] = {}

    def _bm25(self, entity_type: str) -> Bm25Index:
        if entity_type not in self._bm25_cache:
            self._bm25_cache[entity_type] = Bm25Index(
                self.brain_root, self.workspace_slug, entity_type)
        return self._bm25_cache[entity_type]

    def search(self, query: str, *, top_k: int = 20,
               entity_types: list[str] | None = None,
               filters: dict | None = None) -> list[SearchHit]:
        types = entity_types or [
            "component", "screen", "api_contract",
            "data_model", "assumption", "business_context",
        ]
        # Ask Qdrant once which collections actually exist so we don't fire
        # 404s for entity types we never wrote (cuts log noise + saves
        # round-trips on every search). Cached on the instance.
        existing = self._existing_collections()
        all_hits: list[SearchHit] = []
        for et in types:
            coll = collection_name(self.workspace_slug, et)
            if existing is not None and coll not in existing:
                # No Qdrant collection for this type — fall back to BM25-only
                # for this type so we still surface text matches.
                all_hits.extend(self._search_one_type(
                    query, et, top_k=top_k * 2, qdrant_known_missing=True,
                ))
                continue
            all_hits.extend(self._search_one_type(query, et, top_k=top_k * 2))
        all_hits.sort(key=lambda h: h.score, reverse=True)
        return all_hits[:top_k]

    def _existing_collections(self) -> set[str] | None:
        """Return the set of Qdrant collection names that exist for this
        workspace, or None if Qdrant is unreachable (in which case we let the
        search fall through and let each per-type call handle its own error).
        Cached on the instance after first call.
        """
        if hasattr(self, "_existing_cache"):
            return self._existing_cache
        try:
            cols = self.qdrant.get_collections()
            names = {c.name for c in getattr(cols, "collections", [])}
            self._existing_cache: set[str] | None = names
            return names
        except Exception as exc:
            log.warning("Qdrant get_collections failed (will fall back to BM25)",
                        error=str(exc))
            self._existing_cache = None
            return None

    def _search_one_type(self, query: str, entity_type: str,
                         *, top_k: int,
                         qdrant_known_missing: bool = False) -> list[SearchHit]:
        bm25_results = self._bm25(entity_type).search(query, top_k=top_k * 2)
        bm25_rank = {urn: i + 1 for i, (urn, _) in enumerate(bm25_results)}

        coll = collection_name(self.workspace_slug, entity_type)
        if qdrant_known_missing:
            # Cached check told us the collection isn't there — skip Qdrant
            # entirely so we don't burn a round-trip just to log a 404.
            dense_hits = []
        else:
            dense_query = self.embedder.embed(query)
            try:
                dense_hits = _qdrant_search(
                    self.qdrant, coll, dense_query, top_k * 2
                )
            except Exception as exc:
                # Demoted to debug — the collection-existence pre-check above
                # already filters the common 404 case. Genuine errors still
                # surface but won't drown the operator in noise.
                log.debug("Qdrant dense search failed", coll=coll, error=str(exc))
                dense_hits = []
        # Dense hits use UUID point IDs; recover URN from payload for RRF fusion.
        dense_rank = {
            (h.payload or {}).get("urn", str(h.id)): i + 1
            for i, h in enumerate(dense_hits)
        }
        dense_payload = {
            (h.payload or {}).get("urn", str(h.id)): (h.payload or {})
            for h in dense_hits
        }

        all_urns = set(bm25_rank) | set(dense_rank)
        out: list[SearchHit] = []
        for urn in all_urns:
            score = 0.0
            if urn in bm25_rank:
                score += 1.0 / (RRF_K + bm25_rank[urn])
            if urn in dense_rank:
                score += 1.0 / (RRF_K + dense_rank[urn])
            out.append(SearchHit(
                urn=urn, score=score,
                bm25_rank=bm25_rank.get(urn),
                dense_rank=dense_rank.get(urn),
                payload=dense_payload.get(urn, {}),
            ))
        out.sort(key=lambda h: h.score, reverse=True)
        return out[:top_k]


# ── Legacy: FileHybridSearcher — file-based BM25 + dense ─────────────────────

@dataclass
class SearchResult:
    path: str
    score: float
    bm25_score: float = 0.0
    dense_score: float = 0.0
    source: str = ""
    snippet: str = ""
    reasoning: str = ""


class FileHybridSearcher:
    """Legacy file-based hybrid searcher for code_tracer fallback.

    Kept for backward compatibility. New code should use HybridSearcher.
    """

    def __init__(self) -> None:
        self._bm25_indexes: dict[str, BM25Index] = {}
        self._file_contents: dict[str, dict[str, str]] = {}
        self._embedder = CodeEmbedder()
        try:
            from companybrain.retrieval.reranker import Reranker  # type: ignore
            self._reranker = Reranker()
        except Exception:
            self._reranker = None

    async def index_repo(self, repo_path: Path, repo_name: str) -> None:
        if repo_name in self._bm25_indexes:
            return
        from companybrain.pipeline.file_walker import FileWalker
        walker = FileWalker(repo_path)
        bm25 = BM25Index()
        contents: dict[str, str] = {}
        embeddings_batch: list[tuple[str, str]] = []
        for info in walker.walk_extractable():
            try:
                content = info.path.read_text(encoding="utf-8", errors="replace")
                bm25.add(info.relative_path, content)
                contents[info.relative_path] = content
                embeddings_batch.append((info.relative_path, content[:2000]))
            except Exception:
                continue
        bm25.build()
        self._bm25_indexes[repo_name] = bm25
        self._file_contents[repo_name] = contents
        if self._embedder.enabled and embeddings_batch:
            from companybrain.retrieval.qdrant_store import QdrantStore, path_to_point_id
            qs = QdrantStore()
            await qs.ensure_collection()
            texts = [t for _, t in embeddings_batch]
            vectors = await self._embedder.embed_batch(texts)
            if vectors:
                points = [
                    {"id": path_to_point_id(repo_name, path), "vector": vectors[i],
                     "payload": {"path": path, "repo": repo_name}}
                    for i, (path, _) in enumerate(embeddings_batch)
                    if i < len(vectors) and vectors[i]
                ]
                if points:
                    await qs.upsert(points)

    async def search(self, query: str, repo_name: str,
                     repo_path: Optional[Path] = None,
                     top_k: int = 10) -> list[SearchResult]:
        from companybrain.config import settings
        if not settings.hybrid_search_enabled:
            return []
        if repo_name not in self._bm25_indexes and repo_path:
            await self.index_repo(repo_path, repo_name)
        bm25_results: dict[str, float] = {}
        bm25_idx = self._bm25_indexes.get(repo_name)
        if bm25_idx:
            for r in bm25_idx.search(query, top_k=50):
                bm25_results[r.relative_path] = r.score
        dense_results: dict[str, float] = {}
        if self._embedder.enabled:
            query_vec = await self._embedder.embed_query(query)
            if query_vec:
                from companybrain.retrieval.qdrant_store import QdrantStore
                qs = QdrantStore()
                hits = await qs.search(query_vector=query_vec, top_k=50, repo_filter=repo_name)
                for hit in hits:
                    dense_results[hit["path"]] = hit["score"]
        all_paths = set(bm25_results) | set(dense_results)
        if not all_paths:
            return []
        max_bm25 = max(bm25_results.values()) if bm25_results else 1.0
        norm_bm25 = {p: s / max_bm25 for p, s in bm25_results.items()}
        contents = self._file_contents.get(repo_name, {})
        candidates = [
            (p, contents.get(p, "")[:500],
             0.4 * norm_bm25.get(p, 0.0) + 0.6 * dense_results.get(p, 0.0)
             if dense_results.get(p) else norm_bm25.get(p, 0.0))
            for p in all_paths
        ]
        candidates.sort(key=lambda x: x[2], reverse=True)
        if self._reranker is not None:
            reranked = self._reranker.rerank(query, candidates[:50], top_k=top_k)
            return [
                SearchResult(
                    path=r.relative_path,
                    score=r.rerank_score,
                    bm25_score=bm25_results.get(r.relative_path, 0.0),
                    dense_score=dense_results.get(r.relative_path, 0.0),
                    source=_classify_source(
                        r.relative_path in bm25_results,
                        r.relative_path in dense_results,
                    ),
                    snippet=contents.get(r.relative_path, "")[:300],
                )
                for r in reranked
            ]
        return [
            SearchResult(
                path=p,
                score=score,
                bm25_score=bm25_results.get(p, 0.0),
                dense_score=dense_results.get(p, 0.0),
                source=_classify_source(p in bm25_results, p in dense_results),
                snippet=contents.get(p, "")[:300],
            )
            for p, _, score in candidates[:top_k]
        ]


def _qdrant_search(client, collection: str, vector: list[float], limit: int):
    """Search Qdrant, handling API changes across client versions."""
    try:
        # qdrant-client >= 1.10: query_points replaces search
        from qdrant_client.models import NamedVector  # type: ignore
        results = client.query_points(
            collection_name=collection,
            query=vector,
            using="dense",
            limit=limit,
            with_payload=True,
        ).points
    except AttributeError:
        # qdrant-client < 1.10 fallback
        results = client.search(
            collection_name=collection,
            query_vector=("dense", vector),
            limit=limit,
            with_payload=True,
        )
    return results


def _classify_source(in_bm25: bool, in_dense: bool) -> str:
    if in_bm25 and in_dense:
        return "merged"
    if in_bm25:
        return "bm25"
    if in_dense:
        return "dense"
    return "graph"
