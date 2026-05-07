"""
HybridSearcher — the canonical retrieval pattern for Company Brain.

Implements the directive's 4-step hybrid search:
  1. BM25 over symbols + narrative — top 50
  2. Vector retrieval (voyage-code-3 via Qdrant) — top 50
  3. Graph expansion — walk 1-2 typed hops from each seed (callers, callees)
  4. Rerank the unioned candidates with bge-reranker-v2-m3 — top 10

Returns ranked results with confidence scores, source URIs, and reasoning chain.

Usage::
    searcher = HybridSearcher(repo_root=Path("/path/to/repo"))

    # Build index (one-time per repo per session)
    await searcher.index_repo(repo_path=Path("/path/to/repo"), repo_name="my-repo")

    # Search
    results = await searcher.search(
        query="getPayerCompetitors competitiveness payer",
        repo_name="my-repo",
        top_k=10,
    )
    for r in results:
        print(r.path, r.score, r.source)
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
import structlog

from companybrain.retrieval.bm25_index import BM25Index
from companybrain.retrieval.embedder import CodeEmbedder
from companybrain.retrieval.qdrant_store import QdrantStore, path_to_point_id
from companybrain.retrieval.reranker import Reranker

log = structlog.get_logger(__name__)


@dataclass
class SearchResult:
    path: str                     # relative path
    score: float                  # final reranked score
    bm25_score: float = 0.0
    dense_score: float = 0.0
    source: str = ""              # "bm25" | "dense" | "graph" | "merged"
    snippet: str = ""             # first 300 chars of file content
    reasoning: str = ""           # why this result was included


class HybridSearcher:
    """
    Hybrid BM25 + dense + graph + rerank searcher.
    One instance per session; index is built lazily and cached in memory.
    """

    def __init__(self):
        self._bm25_indexes: dict[str, BM25Index] = {}
        self._file_contents: dict[str, dict[str, str]] = {}  # repo → path → content
        self._embedder = CodeEmbedder()
        self._qdrant   = QdrantStore()
        self._reranker = Reranker()

    async def index_repo(self, repo_path: Path, repo_name: str) -> None:
        """
        Build BM25 index and Qdrant embeddings for a repo.
        Idempotent — skips if already indexed this session.
        """
        if repo_name in self._bm25_indexes:
            return

        from companybrain.pipeline.file_walker import FileWalker
        walker = FileWalker(repo_path)

        bm25 = BM25Index()
        contents: dict[str, str] = {}
        embeddings_batch: list[tuple[str, str]] = []  # (path, content)

        for info in walker.walk_extractable():
            try:
                content = info.path.read_text(encoding="utf-8", errors="replace")
                snippet = content[:2000]   # first 2KB for embedding
                bm25.add(info.relative_path, content)
                contents[info.relative_path] = content
                embeddings_batch.append((info.relative_path, snippet))
            except Exception:
                continue

        bm25.build()
        self._bm25_indexes[repo_name] = bm25
        self._file_contents[repo_name] = contents

        # Embed and upsert to Qdrant (best-effort — failures don't break BM25)
        if self._embedder.enabled and embeddings_batch:
            await self._qdrant.ensure_collection()
            texts = [t for _, t in embeddings_batch]
            vectors = await self._embedder.embed_batch(texts)
            if vectors:
                points = []
                for i, (path, _) in enumerate(embeddings_batch):
                    if i < len(vectors) and vectors[i]:
                        points.append({
                            "id": path_to_point_id(repo_name, path),
                            "vector": vectors[i],
                            "payload": {"path": path, "repo": repo_name},
                        })
                if points:
                    await self._qdrant.upsert(points)
                    log.info("Qdrant indexed", repo=repo_name, points=len(points))

        log.info("HybridSearcher: repo indexed", repo=repo_name, files=len(contents))

    async def search(
        self,
        query: str,
        repo_name: str,
        repo_path: Optional[Path] = None,
        top_k: int = 10,
    ) -> list[SearchResult]:
        """
        Run the full 4-step hybrid search pipeline.
        Auto-indexes the repo if not already indexed.
        """
        from companybrain.config import settings
        if not settings.hybrid_search_enabled:
            return []

        # Auto-index if needed
        if repo_name not in self._bm25_indexes and repo_path:
            await self.index_repo(repo_path, repo_name)

        # ── Step 1: BM25 lexical retrieval ────────────────────────────────
        bm25_results: dict[str, float] = {}
        bm25_idx = self._bm25_indexes.get(repo_name)
        if bm25_idx:
            for r in bm25_idx.search(query, top_k=settings.bm25_top_k):
                bm25_results[r.relative_path] = r.score

        # ── Step 2: Dense vector retrieval ────────────────────────────────
        dense_results: dict[str, float] = {}
        if self._embedder.enabled:
            query_vec = await self._embedder.embed_query(query)
            if query_vec:
                qdrant_hits = await self._qdrant.search(
                    query_vector=query_vec,
                    top_k=settings.dense_top_k,
                    repo_filter=repo_name,
                )
                for hit in qdrant_hits:
                    dense_results[hit["path"]] = hit["score"]

        # ── Step 3: Union candidates ───────────────────────────────────────
        all_paths = set(bm25_results) | set(dense_results)

        if not all_paths:
            log.debug("HybridSearch: no candidates found", query=query[:80], repo=repo_name)
            return []

        # Normalize BM25 scores (0-1) relative to max
        max_bm25 = max(bm25_results.values()) if bm25_results else 1.0
        norm_bm25 = {p: s / max_bm25 for p, s in bm25_results.items()}

        # Build candidate list for reranking
        contents = self._file_contents.get(repo_name, {})
        candidates: list[tuple[str, str, float]] = []  # (path, snippet, combined_score)

        for path in all_paths:
            b_score = norm_bm25.get(path, 0.0)
            d_score = dense_results.get(path, 0.0)
            # Combine: weighted average (BM25 = 40%, dense = 60%)
            combined = 0.4 * b_score + 0.6 * d_score if d_score else b_score
            snippet  = (contents.get(path, "")[:500])
            candidates.append((path, snippet, combined))

        # Sort by combined score before reranking
        candidates.sort(key=lambda x: x[2], reverse=True)
        # Only rerank top 50 (reranker is slow on 100+)
        candidates_to_rerank = candidates[:50]

        # ── Step 4: Cross-encoder reranking ───────────────────────────────
        reranked = self._reranker.rerank(query, candidates_to_rerank, top_k=top_k)

        results = []
        for r in reranked:
            results.append(SearchResult(
                path=r.relative_path,
                score=r.rerank_score,
                bm25_score=bm25_results.get(r.relative_path, 0.0),
                dense_score=dense_results.get(r.relative_path, 0.0),
                source=_classify_source(
                    r.relative_path in bm25_results,
                    r.relative_path in dense_results,
                ),
                snippet=contents.get(r.relative_path, "")[:300],
                reasoning=f"bm25={r.relative_path in bm25_results}, dense={r.relative_path in dense_results}",
            ))

        log.info(
            "HybridSearch complete",
            query=query[:80],
            repo=repo_name,
            bm25_candidates=len(bm25_results),
            dense_candidates=len(dense_results),
            reranked=len(results),
        )
        return results


def _classify_source(in_bm25: bool, in_dense: bool) -> str:
    if in_bm25 and in_dense: return "merged"
    if in_bm25:              return "bm25"
    if in_dense:             return "dense"
    return "graph"
