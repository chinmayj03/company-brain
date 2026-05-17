# A1.2 — Hybrid Retrieval V2: BM25 + Dense + RRF + BGE Cross-Encoder Pipeline

**Status**: In Progress
**Quarter**: 2026-Q2
**Budget**: $20 / 2 engineer-weeks
**ADR**: ADR-0015 (continuation)

## Problem

The `HybridSearcher` in `retrieval/hybrid_search.py` implements BM25+dense+RRF fusion (ADR-0015)
and a `Reranker` class using BAAI/bge-reranker-v2-m3 exists in `retrieval/reranker.py`, but the
reranker is **never called from the entity-based HybridSearcher** — only from the legacy
`FileHybridSearcher`.

The cross-encoder reranker adds ~15-20% precision improvement by jointly scoring (query, document)
pairs rather than relying on independent BM25 and dense scores fused via RRF.

## Solution

Wire the existing `Reranker` into the entity-based retrieval path via a new `RetrievalPipeline`
orchestrator class. The pipeline runs:

1. BM25 + dense + RRF candidates (via existing `HybridSearcher.search()`, top 50)
2. BGE cross-encoder reranking over those candidates, trimmed to final top_k
3. Returns a typed `RetrievalResult` with metadata for downstream confidence aggregation

## Scope

### New Files
- `company-brain-ai/src/companybrain/retrieval/pipeline.py` — `RetrievalPipeline` + `RetrievalResult`
- `company-brain-ai/src/companybrain/retrieval/factory.py` — `make_retrieval_pipeline(settings)`
- `company-brain-ai/tests/unit/test_retrieval_pipeline.py`
- `company-brain-ai/tests/unit/test_retrieval_factory.py`

### Append-Only Modifications
- `config.py` — add `retrieval_rerank_enabled` and `retrieval_rerank_top_candidates`
- `retrieval/__init__.py` — export `RetrievalPipeline`, `RetrievalResult`
- `api/routes/query.py` — thread `retrieval_score` from `RetrievalResult.top_score` into confidence aggregator

## Non-Goals
- Changing the `Reranker` implementation
- Changing `HybridSearcher` internals
- GPU inference or batched model serving

## Acceptance Criteria

1. `RetrievalPipeline.retrieve()` returns `RetrievalResult` with `reranked=True` when reranker available
2. With reranker mocked to passthrough, results are identical to direct `HybridSearcher` output
3. `make_retrieval_pipeline()` returns a working pipeline even when Qdrant is unavailable
4. All new unit tests pass; no regressions in existing retrieval tests
