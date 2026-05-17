# B1.3 — ADR-0093 Cross-Source Entity Resolution Phase 1

**Budget:** $40 / 2 engineer-weeks  
**Sprint:** 2026-Q2  
**ADR:** ADR-0093

---

## Summary

Implements the resolution layer that recognizes when two connectors produce
artifacts about the same real-world entity and merges them under a canonical
`domain://` URN.

## Deliverables

- `companybrain.resolution` package with 4-tier algorithm
- `ResolutionStore` (JSON persistence, same pattern as `JsonFileBrainStore`)
- REST endpoints: `GET /resolution/suggestions`, `POST /resolution/confirm/{id}`,
  `POST /resolution/reject/{id}`, `GET /resolution/entity/{domain_urn}`
- Config tunables in `Settings`
- Unit tests (models, name matcher, resolver with mocked embedder)
- Acceptance test (code + Notion artifact → same domain entity)

## Acceptance Criteria

- [x] `CrossSourceEntityResolver` resolves two artifacts with identical normalized
      title (`"PayerModule"` + `"Payer Module"`) to same `domain_urn`
- [x] Explicit link (artifact carries URN reference) resolves at
      `tier=EXPLICIT_LINK`, `confidence=0.95`
- [x] Semantic embed path skips gracefully when sentence-transformers unavailable
- [x] `ResolutionStore` persists and retrieves decisions correctly
- [x] All unit tests pass
- [x] `test_resolution_e2e.py` demonstrates code + Notion artifact resolving
      to same domain entity

## Out of Scope (Phase 2)

- ANN-backed candidate retrieval (Qdrant)
- Postgres-backed ResolutionStore
- Graph propagation of `owl:sameAs` style edges
- Fuzzy string matching tier
