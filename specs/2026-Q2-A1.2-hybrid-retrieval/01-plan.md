# A1.2 Implementation Plan

## Architecture

```
query.py                retrieval/
     │                      │
     ▼                      │
_hybrid_retrieve()  ──────► RetrievalPipeline.retrieve()
                                   │
                              HybridSearcher.search()
                              (top_candidates=50)
                                   │
                              Reranker.rerank()   ← if enabled + available
                              (top_candidates → top_k)
                                   │
                              RetrievalResult
                              {hits, bm25_count, dense_count,
                               reranked, reranker_model, top_score}
```

## Key Design Decisions

### 1. Pipeline wraps searcher (composition over inheritance)
`RetrievalPipeline.__init__(searcher, reranker=None)` — both injected, factory wires them.
This makes unit testing trivial (mock both) and keeps HybridSearcher untouched.

### 2. Reranker input: SearchHit → (urn, text, score)
The Reranker expects `list[tuple[str, str, float]]` as `(relative_path, content_snippet, original_score)`.
We map `SearchHit.urn` as the path, `payload.get("t1_summary", "")` as the snippet, and
`SearchHit.score` as the original_score.

### 3. Graceful degradation
- If Qdrant is down: `HybridSearcher` already handles this (BM25-only fallback)
- If sentence-transformers not installed: `Reranker._load()` returns False → passthrough
- If reranker disabled via settings: skip reranker, `reranked=False`
- `make_retrieval_pipeline()` wraps the whole construction in try/except

### 4. Config additions
Two new tunables in `Settings`:
- `retrieval_rerank_enabled: bool = True` — master toggle
- `retrieval_rerank_top_candidates: int = 50` — how many RRF hits to feed the reranker

The existing `bm25_top_k`, `dense_top_k`, `rerank_top_k`, `reranker_model` settings remain unchanged.

### 5. query.py wiring
`_hybrid_retrieve()` already returns a formatted string. We create an enhanced version
`_hybrid_retrieve_v2()` that uses `RetrievalPipeline` and passes `top_score` into the
telemetry dict. The function is called inside the existing fallback path, so no primary
flow changes. Append-only.

## Mapping to Reranker API

`Reranker.rerank()` signature:
```python
def rerank(
    self,
    query: str,
    candidates: list[tuple[str, str, float]],  # (relative_path, snippet, original_score)
    top_k: int = 10,
) -> list[RankedResult]:
```

`RankedResult` has `.relative_path`, `.rerank_score`, `.original_score`.

For entity-based hits from HybridSearcher, we use:
- `relative_path` = `SearchHit.urn`
- `snippet` = `SearchHit.payload.get("t1_summary", "")[:500]`
- `original_score` = `SearchHit.score`

After reranking, we reconstruct `SearchHit` objects with `score=rerank_score`.
