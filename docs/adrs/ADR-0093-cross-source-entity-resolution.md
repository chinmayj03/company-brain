# ADR-0093 — Cross-Source Entity Resolution Phase 1

**Status:** Accepted  
**Date:** 2026-05-17  
**Deciders:** Chinmay Jadhav  
**Sprint:** 2026-Q2 / B1.3

---

## Context

ADR-0091 established the Domain-Entity-First architecture: real-world entities carry
`domain://` URNs and source artifacts are evidence that points to them.
ADR-0092 built the connector framework (`BaseConnector`, `ConnectorRegistry`,
`ConnectorIngestionPipeline`) which produces `SourceArtifact` objects from multiple
sources (Notion pages, code files, Slack messages, etc.).

The missing piece is **entity resolution**: when two connectors produce artifacts about
the same real-world entity (e.g., a Notion page "Payer Module" and a Java class
`PayerModule.java`), how do we recognize them as the same entity and merge them under
a single canonical `domain://` URN?

Without this layer, each connector would create a separate domain entity for every
artifact it encounters, producing a fragmented graph with no cross-source edges.

---

## Decision

We implement a **four-tier resolution algorithm** that compares artifact candidates
in descending confidence order and short-circuits at the first tier that yields a
match above the suggest threshold (0.60).

### Resolution Tiers

| Tier | Confidence | Trigger |
|------|-----------|---------|
| `EXPLICIT_LINK` | 0.95 | Artifact carries an explicit `domain://` URN reference |
| `NAME_MATCH` | 0.82 | Normalized artifact titles are equal |
| `SEMANTIC_EMBED` | ≤ 0.72 | Cosine similarity of sentence embeddings ≥ 0.80 |
| `HUMAN_CONFIRMED` | 1.00 | Operator confirmed via REST API |

### Thresholds

- **≥ 0.80** — auto-resolve: merge artifacts under the same domain entity without human input
- **0.60 – 0.80** — suggest: surface the candidate pair to a human via `GET /resolution/suggestions`
- **< 0.60** — separate: treat as distinct entities

### Name Normalization

`normalize_title()` splits camelCase/PascalCase, replaces non-alphanumeric chars with
spaces, and lowercases.  This makes `"PayerModule"`, `"payer_module"`,
`"payer-module"`, and `"Payer Module"` all identical after normalization, satisfying
the requirement that cross-source name matching works without brittle string equality.

### Semantic Embedding (optional)

The `EmbedMatcher` uses `sentence-transformers` (`all-MiniLM-L6-v2` by default).
The import is **lazy**: if the package is not installed the tier is silently skipped
and the resolver falls through to returning no match.  This keeps the core resolution
path free of heavy ML dependencies for environments that don't need semantic matching.

### Persistence

`ResolutionStore` persists decisions as JSON files under `RESOLUTION_STORE_PATH`
(default `.resolution/`).  The same "JSON is the source of truth" pattern used by
`JsonFileBrainStore` (ADR-0012) is applied here so no new infrastructure is needed
for Phase 1.  A Postgres-backed implementation can replace the store in Phase 2.

### REST API

Four new endpoints are added under `/resolution/`:

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/resolution/suggestions` | Pending matches in the 0.60–0.80 band |
| `POST` | `/resolution/confirm/{id}` | Operator confirms a suggested match |
| `POST` | `/resolution/reject/{id}` | Operator rejects a suggested match |
| `GET` | `/resolution/entity/{domain_urn}` | All artifacts merged under a domain entity |

---

## Alternatives Considered

### 1. Single-tier fuzzy string matching (Levenshtein / Jaro-Winkler)
Rejected because it cannot handle cross-language or semantic similarity.
The four-tier ladder is extensible; fuzzy matching can be added as Tier 1.5 later.

### 2. Always require sentence-transformers
Rejected because it adds a 500 MB+ dependency and GPU/CPU setup complexity.
The lazy-import pattern keeps Phase 1 deployable on minimal Python environments.

### 3. Graph-native entity resolution (SPARQL / OWL sameAs)
Deferred to Phase 2.  The domain graph is not yet large enough to justify the
complexity of a triple-store based owl:sameAs propagation engine.

### 4. Vector-DB-backed candidate retrieval
Deferred.  Phase 1 does linear scan over `existing` candidates.  Phase 2 will
replace this with an ANN lookup against the Qdrant store (ADR-0015).

---

## Consequences

**Positive:**
- Cross-source relationships become first-class: a Notion page and a Java class can
  share a single node in the domain graph.
- The operator-review loop (suggest → confirm/reject) builds a labeled dataset for
  improving the algorithm over time.
- EXPLICIT_LINK tier means connector authors can guarantee resolution by embedding a
  `domain://` URN in their artifact metadata — zero false positives.

**Negative / Trade-offs:**
- Phase 1 does a full linear scan over existing candidates (O(n) per new artifact).
  Acceptable for initial workspace sizes < 10 000 artifacts; replace with ANN in P2.
- JSON-file store is not suitable for concurrent multi-writer scenarios (mitigated by
  `threading.Lock` for single-process use; replace with Postgres in P2).
- Semantic embed tier skips silently when the model is unavailable, which can lower
  recall.  Teams that need high recall must install `sentence-transformers`.

---

## Implementation

### Module layout

```
company-brain-ai/src/companybrain/resolution/
  __init__.py        — public exports
  models.py          — EntityCandidate, ResolutionMatch, ResolutionResult, tiers
  resolver.py        — CrossSourceEntityResolver (4-tier algorithm)
  name_matcher.py    — normalize(), normalize_title(), names_match()
  embed_matcher.py   — EmbedMatcher (lazy sentence-transformers)
  store.py           — ResolutionStore (JSON persistence)

company-brain-ai/src/companybrain/api/routes/resolution.py
  — REST endpoints

tests/unit/test_resolution_models.py
tests/unit/test_name_matcher.py
tests/unit/test_resolver.py
tests/acceptance/test_resolution_e2e.py
```

### Config tunables added to `Settings`

| Key | Default | Description |
|-----|---------|-------------|
| `resolution_store_path` | `.resolution` | Path for JSON persistence |
| `resolution_embed_threshold` | `0.80` | Cosine threshold for SEMANTIC_EMBED |
| `resolution_embed_model` | `all-MiniLM-L6-v2` | HuggingFace model name |
| `resolution_auto_resolve_threshold` | `0.80` | Confidence above which matches auto-resolve |
| `resolution_suggest_threshold` | `0.60` | Confidence floor for surfacing suggestions |

---

## References

- ADR-0091 — Domain-Entity-First Architecture
- ADR-0092 — Multi-Source Connector Framework
- ADR-0012 — BrainStore JSON Source of Truth
- ADR-0015 — Qdrant Hybrid Retriever (Phase 2 ANN backend)
