"""
RetrievalPipeline — ADR-0015 A1.2: BM25 + dense + RRF + BGE cross-encoder reranker.

This module wires the existing HybridSearcher (BM25+dense+RRF) together with the
Reranker (BAAI/bge-reranker-v2-m3 cross-encoder) into a single orchestrated pipeline.

The cross-encoder reranker adds ~15-20% precision improvement by jointly scoring
(query, document) pairs rather than relying only on the RRF-fused scores.

Usage::
    from companybrain.retrieval.pipeline import RetrievalPipeline, RetrievalResult
    from companybrain.retrieval.factory import make_retrieval_pipeline
    from companybrain.config import settings

    pipeline = make_retrieval_pipeline(settings)
    result = await pipeline.retrieve(query, workspace_id="ws-slug", top_k=10)
    # result.hits — list[SearchHit] sorted by final score
    # result.reranked — True when BGE cross-encoder was applied
    # result.top_score — score of the top hit (for confidence aggregation)
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Optional

import structlog

from companybrain.retrieval.hybrid_search import HybridSearcher, SearchHit

log = structlog.get_logger(__name__)


@dataclass
class RetrievalResult:
    """Typed result returned by RetrievalPipeline.retrieve().

    Carries both the ranked hits and pipeline-level metadata so callers
    (e.g. the confidence aggregator in query.py) can make informed decisions
    without re-inspecting internals.
    """

    hits: list[SearchHit]
    bm25_count: int
    dense_count: int
    reranked: bool
    reranker_model: Optional[str]
    top_score: float

    @classmethod
    def empty(cls) -> "RetrievalResult":
        """Convenience constructor for the zero-results case."""
        return cls(
            hits=[],
            bm25_count=0,
            dense_count=0,
            reranked=False,
            reranker_model=None,
            top_score=0.0,
        )


class RetrievalPipeline:
    """Orchestrates BM25 + dense + RRF candidates → optional BGE cross-encoder rerank.

    Pipeline stages:
      1. ``HybridSearcher.search()`` — BM25+dense+RRF fusion, returns top
         ``top_candidates`` hits.
      2. Optional reranking — if ``reranker`` is provided AND
         ``settings.retrieval_rerank_enabled`` is True, the cross-encoder
         scores all candidates and re-orders them.
      3. Trim to ``top_k`` and return a ``RetrievalResult`` with metadata.

    Args:
        searcher: Pre-built HybridSearcher instance (one per workspace).
        reranker: Optional Reranker instance. When None, the pipeline runs
            BM25+dense+RRF only (same behaviour as calling HybridSearcher
            directly). The reranker lazily loads the model on first use, so
            construction is always fast.
        rerank_enabled: Master toggle — when False the reranker is skipped
            even if provided. Mirrors ``settings.retrieval_rerank_enabled``.
        top_candidates: How many RRF candidates to feed the reranker.
            Mirrors ``settings.retrieval_rerank_top_candidates``.
    """

    def __init__(
        self,
        searcher: HybridSearcher,
        reranker=None,  # type: Optional[Reranker]  # avoid hard import cycle
        *,
        rerank_enabled: bool = True,
        top_candidates: int = 50,
    ) -> None:
        self._searcher = searcher
        self._reranker = reranker
        self._rerank_enabled = rerank_enabled
        self._top_candidates = top_candidates

    # ── Public API ────────────────────────────────────────────────────────────

    async def retrieve(
        self,
        query: str,
        workspace_id: str,
        *,
        top_k: int = 10,
        entity_types: list[str] | None = None,
        filters: dict | None = None,
        index: str = "default",
    ) -> RetrievalResult:
        """Run the full BM25+dense+RRF+rerank pipeline.

        Args:
            query:        The natural language query string.
            workspace_id: Workspace slug (used for logging; the searcher was
                          already constructed with the slug baked in).
            top_k:        Final number of results to return after reranking.
            entity_types: Forwarded to HybridSearcher (None = all types).
            filters:      Forwarded to HybridSearcher (None = no filters).
            index:        Qdrant granularity index. One of "default", "code",
                          "business", "t2_card".

        Returns:
            RetrievalResult with hits ranked by final score.
        """
        # Stage 1: BM25 + dense + RRF (synchronous; run in executor to avoid
        # blocking the event loop if the caller is async).
        try:
            candidates = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self._searcher.search(
                    query,
                    top_k=self._top_candidates,
                    entity_types=entity_types,
                    filters=filters,
                    index=index,
                ),
            )
        except Exception as exc:
            log.warning("[pipeline] HybridSearcher failed", error=str(exc))
            return RetrievalResult.empty()

        if not candidates:
            return RetrievalResult.empty()

        # Count how many hits came from each signal (for telemetry).
        bm25_count = sum(1 for h in candidates if h.bm25_rank is not None)
        dense_count = sum(1 for h in candidates if h.dense_rank is not None)

        # Stage 2: Optional cross-encoder reranking.
        if self._reranker is not None and self._rerank_enabled:
            hits, reranked = self._rerank(query, candidates, top_k=top_k)
        else:
            hits = candidates[:top_k]
            reranked = False

        top_score = hits[0].score if hits else 0.0
        reranker_model: Optional[str] = None
        if reranked and self._reranker is not None:
            reranker_model = getattr(self._reranker, "_model_name", None)

        log.debug(
            "[pipeline] retrieve done",
            query_len=len(query),
            candidates=len(candidates),
            hits=len(hits),
            reranked=reranked,
            top_score=round(top_score, 4),
            index=index,
        )

        return RetrievalResult(
            hits=hits,
            bm25_count=bm25_count,
            dense_count=dense_count,
            reranked=reranked,
            reranker_model=reranker_model,
            top_score=top_score,
        )

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _rerank(
        self,
        query: str,
        candidates: list[SearchHit],
        *,
        top_k: int,
    ) -> tuple[list[SearchHit], bool]:
        """Run cross-encoder reranking; return (hits, was_reranked).

        If reranking raises or the model is unavailable, falls back to the
        original RRF-ranked order so the pipeline never returns fewer results
        than the caller requested.
        """
        # Map SearchHit → Reranker input format:
        # (relative_path, content_snippet, original_score)
        reranker_input = [
            (
                hit.urn,
                (hit.payload or {}).get("t1_summary", "")[:500],
                hit.score,
            )
            for hit in candidates
        ]

        try:
            ranked = self._reranker.rerank(query, reranker_input, top_k=top_k)
        except Exception as exc:
            log.warning("[pipeline] reranker.rerank() failed — using RRF order",
                        error=str(exc))
            return candidates[:top_k], False

        if not ranked:
            return candidates[:top_k], False

        # Build a lookup map from urn → original SearchHit so we can restore
        # full payload/rank metadata while using the new rerank scores.
        hit_by_urn: dict[str, SearchHit] = {h.urn: h for h in candidates}
        reranked_hits: list[SearchHit] = []
        for r in ranked:
            original = hit_by_urn.get(r.relative_path)
            if original is None:
                # Shouldn't happen, but guard defensively
                continue
            reranked_hits.append(
                SearchHit(
                    urn=original.urn,
                    score=r.rerank_score,
                    payload=original.payload,
                    bm25_rank=original.bm25_rank,
                    dense_rank=original.dense_rank,
                )
            )

        # If somehow no hits survived the merge, fall back to RRF order.
        if not reranked_hits:
            return candidates[:top_k], False

        # Determine whether the model actually fired (passthrough returns
        # scores equal to original_score — treat as "not reranked").
        available = getattr(self._reranker, "_available", None)
        actually_reranked = available is True

        return reranked_hits, actually_reranked
