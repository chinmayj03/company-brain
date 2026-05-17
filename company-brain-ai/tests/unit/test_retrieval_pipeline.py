"""
Unit tests for ADR-0015 A1.2 — RetrievalPipeline.

All external dependencies (HybridSearcher, Reranker) are mocked so these
tests run without Qdrant, sentence-transformers, or any network access.
"""
from __future__ import annotations

import asyncio
import pytest
from dataclasses import dataclass, field
from typing import Optional
from unittest.mock import MagicMock, patch

from companybrain.retrieval.hybrid_search import SearchHit
from companybrain.retrieval.pipeline import RetrievalPipeline, RetrievalResult


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_hit(urn: str, score: float, *, bm25_rank=None, dense_rank=None, payload=None):
    return SearchHit(
        urn=urn,
        score=score,
        bm25_rank=bm25_rank,
        dense_rank=dense_rank,
        payload=payload or {"t1_summary": f"Summary of {urn}", "entity_type": "component"},
    )


def _make_searcher(*hits: SearchHit):
    """Return a mock HybridSearcher that yields the given hits."""
    mock = MagicMock()
    mock.search.return_value = list(hits)
    return mock


def _make_reranker(available: bool = True, raises: bool = False):
    """Return a mock Reranker.

    When available=True, rerank() re-orders candidates by length of urn (a
    deterministic but non-RRF ordering so we can verify reranking happened).
    When available=False, simulates sentence-transformers not installed.
    When raises=True, simulates a runtime reranking failure.
    """
    from companybrain.retrieval.reranker import RankedResult

    mock = MagicMock()
    mock._available = available
    mock._model_name = "BAAI/bge-reranker-v2-m3"

    if raises:
        mock.rerank.side_effect = RuntimeError("model exploded")
    elif available:
        def _rerank(query, candidates, top_k=10):
            # Sort by urn length descending (a proxy for "cross-encoder score")
            ranked = sorted(candidates, key=lambda c: len(c[0]), reverse=True)[:top_k]
            return [
                RankedResult(
                    relative_path=path,
                    rerank_score=float(10 - i),  # descending integers
                    original_score=orig,
                )
                for i, (path, _, orig) in enumerate(ranked)
            ]
        mock.rerank.side_effect = _rerank
    else:
        # Passthrough — returns sorted by original_score
        def _passthrough(query, candidates, top_k=10):
            ranked = sorted(candidates, key=lambda c: c[2], reverse=True)[:top_k]
            return [
                RankedResult(
                    relative_path=path,
                    rerank_score=orig,
                    original_score=orig,
                )
                for (path, _, orig) in ranked
            ]
        mock.rerank.side_effect = _passthrough

    return mock


# ── Tests: basic pipeline operation ──────────────────────────────────────────

def test_retrieve_returns_retrieval_result_type():
    hits = [_make_hit("urn:cb:component:Foo", 0.8)]
    searcher = _make_searcher(*hits)
    pipeline = RetrievalPipeline(searcher=searcher, reranker=None)
    result = asyncio.get_event_loop().run_until_complete(
        pipeline.retrieve("what is Foo?", workspace_id="ws")
    )
    assert isinstance(result, RetrievalResult)


def test_retrieve_no_reranker_returns_rrf_order():
    hits = [
        _make_hit("urn:cb:component:Alpha", 0.9, bm25_rank=1, dense_rank=1),
        _make_hit("urn:cb:component:Beta",  0.6, bm25_rank=2, dense_rank=2),
    ]
    searcher = _make_searcher(*hits)
    pipeline = RetrievalPipeline(searcher=searcher, reranker=None, top_candidates=50)
    result = asyncio.get_event_loop().run_until_complete(
        pipeline.retrieve("Alpha component", workspace_id="ws", top_k=2)
    )
    assert result.reranked is False
    assert result.reranker_model is None
    assert [h.urn for h in result.hits] == ["urn:cb:component:Alpha", "urn:cb:component:Beta"]


def test_retrieve_with_reranker_reranked_flag_true():
    hits = [
        _make_hit("urn:cb:component:A", 0.9),
        _make_hit("urn:cb:component:BB", 0.7),
        _make_hit("urn:cb:component:CCC", 0.5),
    ]
    searcher = _make_searcher(*hits)
    reranker = _make_reranker(available=True)
    pipeline = RetrievalPipeline(searcher=searcher, reranker=reranker)
    result = asyncio.get_event_loop().run_until_complete(
        pipeline.retrieve("test query", workspace_id="ws", top_k=3)
    )
    assert result.reranked is True
    assert result.reranker_model == "BAAI/bge-reranker-v2-m3"
    # Our mock reranks by urn length descending: CCC > BB > A
    assert result.hits[0].urn == "urn:cb:component:CCC"
    assert result.hits[1].urn == "urn:cb:component:BB"
    assert result.hits[2].urn == "urn:cb:component:A"


def test_retrieve_with_reranker_scores_updated():
    """Reranked hits should carry the cross-encoder score, not the RRF score."""
    hits = [_make_hit("urn:cb:component:Foo", 0.8)]
    searcher = _make_searcher(*hits)
    reranker = _make_reranker(available=True)
    pipeline = RetrievalPipeline(searcher=searcher, reranker=reranker)
    result = asyncio.get_event_loop().run_until_complete(
        pipeline.retrieve("query", workspace_id="ws")
    )
    # The mock assigns rerank_score=10.0 to the first result
    assert result.hits[0].score == 10.0
    assert result.top_score == 10.0


def test_retrieve_passthrough_reranker_same_as_no_reranker():
    """When sentence-transformers is unavailable, pipeline falls back gracefully.

    The hits should be the same ones returned by HybridSearcher (sorted by
    original_score), and reranked should be False because _available is False.
    """
    hits = [
        _make_hit("urn:cb:component:X", 0.9),
        _make_hit("urn:cb:component:Y", 0.7),
    ]
    searcher_no = _make_searcher(*hits)
    searcher_pt = _make_searcher(*hits)

    pipeline_no = RetrievalPipeline(searcher=searcher_no, reranker=None)
    pipeline_pt = RetrievalPipeline(
        searcher=searcher_pt,
        reranker=_make_reranker(available=False),
    )

    result_no = asyncio.get_event_loop().run_until_complete(
        pipeline_no.retrieve("q", workspace_id="ws", top_k=2)
    )
    result_pt = asyncio.get_event_loop().run_until_complete(
        pipeline_pt.retrieve("q", workspace_id="ws", top_k=2)
    )

    assert [h.urn for h in result_no.hits] == [h.urn for h in result_pt.hits]
    assert result_pt.reranked is False


def test_retrieve_empty_candidates():
    searcher = _make_searcher()  # no hits
    pipeline = RetrievalPipeline(searcher=searcher, reranker=_make_reranker())
    result = asyncio.get_event_loop().run_until_complete(
        pipeline.retrieve("nothing here", workspace_id="ws")
    )
    assert result.hits == []
    assert result.top_score == 0.0
    assert result.reranked is False


def test_retrieve_searcher_failure_returns_empty():
    searcher = MagicMock()
    searcher.search.side_effect = RuntimeError("Qdrant is down")
    pipeline = RetrievalPipeline(searcher=searcher, reranker=None)
    result = asyncio.get_event_loop().run_until_complete(
        pipeline.retrieve("q", workspace_id="ws")
    )
    assert isinstance(result, RetrievalResult)
    assert result.hits == []
    assert result.top_score == 0.0


def test_retrieve_reranker_failure_falls_back_to_rrf():
    hits = [
        _make_hit("urn:cb:component:A", 0.9),
        _make_hit("urn:cb:component:B", 0.7),
    ]
    searcher = _make_searcher(*hits)
    reranker = _make_reranker(raises=True)
    pipeline = RetrievalPipeline(searcher=searcher, reranker=reranker)
    result = asyncio.get_event_loop().run_until_complete(
        pipeline.retrieve("q", workspace_id="ws", top_k=2)
    )
    # Must fall back to RRF order
    assert result.reranked is False
    assert result.hits[0].urn == "urn:cb:component:A"
    assert result.hits[1].urn == "urn:cb:component:B"


def test_retrieve_rerank_disabled_skips_reranker():
    hits = [_make_hit("urn:cb:component:A", 0.9)]
    searcher = _make_searcher(*hits)
    reranker = _make_reranker(available=True)
    pipeline = RetrievalPipeline(
        searcher=searcher,
        reranker=reranker,
        rerank_enabled=False,
    )
    result = asyncio.get_event_loop().run_until_complete(
        pipeline.retrieve("q", workspace_id="ws")
    )
    assert result.reranked is False
    reranker.rerank.assert_not_called()


def test_retrieve_top_k_respected():
    hits = [_make_hit(f"urn:cb:component:{i}", float(10 - i)) for i in range(10)]
    searcher = _make_searcher(*hits)
    pipeline = RetrievalPipeline(searcher=searcher, reranker=None)
    result = asyncio.get_event_loop().run_until_complete(
        pipeline.retrieve("q", workspace_id="ws", top_k=3)
    )
    assert len(result.hits) == 3


def test_retrieve_bm25_dense_counts():
    hits = [
        _make_hit("urn:cb:a", 0.9, bm25_rank=1, dense_rank=None),
        _make_hit("urn:cb:b", 0.8, bm25_rank=2, dense_rank=1),
        _make_hit("urn:cb:c", 0.7, bm25_rank=None, dense_rank=2),
    ]
    searcher = _make_searcher(*hits)
    pipeline = RetrievalPipeline(searcher=searcher, reranker=None)
    result = asyncio.get_event_loop().run_until_complete(
        pipeline.retrieve("q", workspace_id="ws", top_k=3)
    )
    assert result.bm25_count == 2   # urn:cb:a and urn:cb:b
    assert result.dense_count == 2  # urn:cb:b and urn:cb:c


def test_retrieval_result_empty_constructor():
    r = RetrievalResult.empty()
    assert r.hits == []
    assert r.top_score == 0.0
    assert r.reranked is False
    assert r.reranker_model is None


def test_retrieve_top_candidates_passed_to_searcher():
    """Pipeline should request top_candidates from the searcher, not top_k."""
    hits = [_make_hit(f"urn:cb:{i}", float(i)) for i in range(30)]
    searcher = _make_searcher(*hits)
    pipeline = RetrievalPipeline(searcher=searcher, reranker=None, top_candidates=25)
    asyncio.get_event_loop().run_until_complete(
        pipeline.retrieve("q", workspace_id="ws", top_k=5)
    )
    _, kwargs = searcher.search.call_args
    # top_k passed to searcher should be top_candidates (25), not final top_k (5)
    assert kwargs.get("top_k") == 25


def test_retrieve_forwards_index_to_searcher():
    searcher = _make_searcher()
    pipeline = RetrievalPipeline(searcher=searcher, reranker=None)
    asyncio.get_event_loop().run_until_complete(
        pipeline.retrieve("q", workspace_id="ws", index="business")
    )
    _, kwargs = searcher.search.call_args
    assert kwargs.get("index") == "business"
