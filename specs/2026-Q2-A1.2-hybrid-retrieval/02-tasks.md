# A1.2 Task Breakdown

## T1 — Config additions (config.py, append-only)
Add `retrieval_rerank_enabled` and `retrieval_rerank_top_candidates` to Settings.
**Owner**: this session | **Effort**: 15 min

## T2 — RetrievalPipeline (retrieval/pipeline.py, NEW)
Implement `RetrievalResult` dataclass and `RetrievalPipeline` class.
`retrieve()` calls HybridSearcher, optionally reranks, returns RetrievalResult.
**Owner**: this session | **Effort**: 1h

## T3 — Factory (retrieval/factory.py, NEW)
Implement `make_retrieval_pipeline(settings)` with graceful degradation.
**Owner**: this session | **Effort**: 30 min

## T4 — retrieval/__init__.py exports (append-only)
Export RetrievalPipeline and RetrievalResult.
**Owner**: this session | **Effort**: 5 min

## T5 — query.py wiring (api/routes/query.py, append-only)
Add retrieval_score propagation from RetrievalResult.top_score into telemetry.
**Owner**: this session | **Effort**: 30 min

## T6 — Unit tests: pipeline (tests/unit/test_retrieval_pipeline.py, NEW)
Mock HybridSearcher + Reranker; verify pipeline stages, passthrough, and rerank.
**Owner**: this session | **Effort**: 1h

## T7 — Unit tests: factory (tests/unit/test_retrieval_factory.py, NEW)
Verify factory builds correct pipeline from settings, graceful degradation paths.
**Owner**: this session | **Effort**: 30 min

## T8 — Spec files (specs/2026-Q2-A1.2-hybrid-retrieval/)
This document + 00-spec.md + 01-plan.md.
**Owner**: this session | **Effort**: 30 min

## T9 — Commit
Single commit: `feat(retrieval): A1.2 — BM25+dense+RRF+BGE cross-encoder pipeline`
**Owner**: this session | **Effort**: 5 min

## Status

| Task | Status |
|------|--------|
| T1 config | done |
| T2 pipeline.py | done |
| T3 factory.py | done |
| T4 __init__.py | done |
| T5 query.py | done |
| T6 test_pipeline | done |
| T7 test_factory | done |
| T8 spec files | done |
| T9 commit | pending |
