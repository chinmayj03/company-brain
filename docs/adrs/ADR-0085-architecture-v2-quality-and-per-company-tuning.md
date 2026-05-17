# ADR-0085 — Architecture V2: Quality Overhaul + Per-Company Domain Tuning

**Status:** Proposed
**Date:** 2026-05-17
**Builds on:** ADR-0005 (confidence rubric — never implemented), ADR-0007 (drift), ADR-0050 (blast radius), ADR-0057 (universal extraction), ADR-0065 (RRF — designed only), ADR-0066/0067 (experiential + background), ADR-0079/0083 (templates + catalog)
**Strategic goal:** address the four diagnosed quality root causes (SQL coverage, blast radius shallowness, confidence collapse, query latency) AND establish per-company domain tuning as a first-class architecture pillar. Adopt SOTA from Glean, Sourcegraph, Cursor, Joern, sqlglot, LazyGraphRAG, BGE rerankers, Anthropic prompt caching, Tian et al. verbalized confidence, Chain-of-Verification.

Full background: `ARCHITECTURE-V2-QUALITY-AND-DOMAIN-TUNING.md`.

---

## Context

E2E session findings + targeted code diagnosis identified four root causes that no incremental patch will fix:

1. **SQL extractor** (`schema_sql.py:116-127`) only handles CREATE_TABLE / ALTER_TABLE / CREATE_INDEX — 80%+ of real SQL is silently dropped, including all JPA `@Query`, JDBC strings, jOOQ DSL, MyBatis, DML in `.sql` files.
2. **Blast radius** (`BlastRadiusService.java:46-160`) is forward-only by default, hard-capped at depth 5, control-flow-only (no data flow), no confidence decay.
3. **Confidence** (`query.py:544-574`) is `bool(context)` masquerading as a 3-level label. Never reaches "high". No calibration. No multi-signal aggregation. ADR-0005 was specified but never implemented.
4. **Query pipeline** (`query.py:88-310`) is fully sequential: SmartZone → Hybrid → LLM → Exploration → Notes. No prefix cache, no semantic cache, no streaming, no parallel retrieval. P50 17s, P90 43s.

Plus the user's product requirement: per-company domain tuning must be first-class — every brain becomes workspace-adaptive over time (Glean's "knowledge graph per customer", Palantir Foundry's ontology layer pattern, the actual moat).

Patches don't fix these. They need architecture V2.

---

## Decision

Adopt a five-pillar V2 architecture. Each pillar replaces a current weak layer with a SOTA-informed stack and is per-workspace-tunable through Pillar 5.

---

### Pillar 1 — Deep Extraction

Replace heuristic regex + custom extractors with a layered AST-first stack:

| Layer | Tool / Method | Replaces |
|---|---|---|
| Syntactic chunking | Tree-sitter (40+ languages) | Custom regex chunking |
| Type-resolved cross-refs | SCIP indexers (scip-java, scip-python, scip-typescript) + per-language LSP | Heuristic same-file resolution |
| Build-less name resolution | Stack Graphs | (gap — currently missing) |
| SQL DDL+DML | sqlglot (20+ dialects, column-lineage) + OpenLineage-SQL parser for embedded extraction | `schema_sql.py` DDL-only |
| Call + data flow | Joern Code Property Graph for top-N critical paths; Doop for Java pointer analysis on demand | `BlastRadiusService` forward-only |
| Dynamic SQL | Abstract string analysis (Christensen et al. SAS 2003) for placeholder tracking | (gap) |
| Extraction LLM stack | Sonnet extract + Haiku verify (multi-model review per addyosmani pattern) | Single-model extract |

**Schema additions**:
- Every extracted entity carries `provenance.tier` ∈ {syntactic, type-resolved, points-to-resolved, dynamic-trace} so retrieval can weight by extraction confidence.
- Every chunk anchored to a stable symbol URN (tree-sitter symbol path).

**Scope of P1**: tree-sitter chunking + sqlglot SQL deep extractor + multi-model review. SCIP/Stack Graphs/Joern to P2.

---

### Pillar 2 — Hybrid Retrieval

Replace single-mode retrieval with a tiered hybrid pipeline:

```
Query
  ├─ BM25 lexical (rank A)         ┐
  ├─ Dense vectors (rank B)        │  parallel via asyncio.gather
  └─ Graph traversal (rank C)      ┘  (LazyGraphRAG-style; no upfront communities)
                                  
RRF fusion k=60 → top-50         (Cormack 2009)
                                  
BGE-reranker-v2-M3 → top-K       (~8-11% nDCG lift)
                                  
Intent classifier: if multi-hop, HippoRAG layer (Personalized PageRank)
```

**Indexes maintained**:
- BM25 over symbol names + paths + comments (critical for identifier queries — code-specific)
- Dense embeddings (e5-large-v2 or bge-large-en-v1.5) per chunk
- Graph adjacency (entities + weighted edges; no precomputed community summaries)

**Why three signals**: BM25 wins exact-identifier queries (every code search hits this); dense wins semantic ("how do we usually handle X"); graph wins multi-hop ("if I change A, what else").

**Scope of P1**: BM25 + dense + RRF + BGE reranker; graph layer P2.

---

### Pillar 3 — Calibrated Confidence

Replace `bool(context) → label` with a calibrated probability:

1. **LLM emits verbalized confidence** in structured output (Tian et al. arxiv 2305.14975) — RLHF models verbalize better than logits indicate.
2. **Multi-signal aggregator** combines: verbalized score, citation count, mean/min citation confidence (extraction-tier weighted), retrieval RRF agreement, blast-radius coverage.
3. **Per-workspace temperature scaling + logistic calibrator** (single-knob per Guo et al. 1706.04599 + LR per workspace), fit on labeled feedback.
4. **CoVe pass** on high-stakes queries (configurable; default on for PM/VP/CFO personas per ADR-0079).
5. **Reliability diagram per workspace**, recalibrated weekly via ADR-0067 background process when ECE > 0.10.

Schema: every answer carries `confidence: {label, calibrated_score, signals: {...}, signals_used: [...]}`.

**Scope of P1**: verbalized confidence + multi-signal aggregator. CoVe + per-workspace calibrator to P2.

---

### Pillar 4 — Fast Pipeline

Five compounding latency wins:

| Optimization | Source | Expected effect |
|---|---|---|
| Anthropic prompt caching on (system + workspace context + entity-graph card pack) | Anthropic prompt caching docs | 85% TTFT cut, 90% cost cut on cache hits |
| GPTCache semantic cache | arxiv 2411.05276 | 60%+ hit rate on near-duplicate queries |
| Streaming responses (FastAPI StreamingResponse) | standard | TTFT < 1s |
| Parallel retrieval (BM25/dense/graph via asyncio.gather) | standard | Eliminates sequential bottleneck |
| Lazy exploration (only run when initial confidence < 0.5) | standard | Cuts speculative agent runs |

Plus trivia: singleton Neo4j driver (already partial), preloaded brain store, warm-up on boot.

**Target**: P50 < 3s, P90 < 8s (today: 17s / 43s).

---

### Pillar 5 — Per-Company Domain Tuning (new)

Every brain becomes workspace-adaptive across four layers:

| Layer | Component | Update cadence | Effect |
|---|---|---|---|
| Coarse | Vertical pack (Healthcare-RCM / SaaS / Fintech) + workspace YAML | Onboarding | Bootstraps brain to vertical |
| Medium | Auto-discovered glossary; few-shot bank from successful Q&A; persona binding overrides | Weekly background | Brain speaks workspace's vocabulary; matches house style |
| Fine | Per-workspace confidence calibrator; per-workspace LTR reranker tuning; persona template variants | Weekly background | Calibrated probabilities; reranker fits workspace conventions |
| Continuous | Thumbs/edit/follow-up/click-through capture; ADR-0066 experiential memory | Real-time | Feedback loop powers all above |

**Workspace Tuning Store** (per workspace, versioned):
- glossary.yaml (auto-discovered terms + aliases + definitions)
- few_shot_bank.jsonl (top-N successful Q&A by persona)
- calibrator.json (logistic regression coefficients + temperature)
- reranker_weights.json (LTR over BGE scores + workspace features)
- persona_bindings.yaml (per-workspace overrides on ADR-0079 bindings)
- reliability_diagram.json (per persona × shape × week)

Loaded at query time; injected into prompts (cached via Anthropic prompt caching) and reranker calls.

**Background process** (ADR-0067) refits weekly: glossary deltas, few-shot rotations, calibrator coefficients, reranker weights. Versioned per ADR-0083 snapshot model so rollback is safe.

---

## Consequences

**Positive**:
- Quality metrics jump measurably (SQL coverage > 75%, blast radius recall > 65%, P50 latency < 3s)
- Confidence becomes meaningful (per-workspace ECE < 0.15 → user trust restored)
- Per-company tuning becomes the moat ("brain knows your company better every week" — the Glean / Palantir pitch line)
- SOTA stack means we're not reinventing wheels (tree-sitter, sqlglot, BGE, etc. are battle-tested)
- Tunable artifacts are catalog entries (ADR-0083) so curation + rollback already covered
- Spec-driven dev process aligns engineering with frontend feature delivery cleanly

**Negative / risks**:
- Big surface change. Wave 1 alone is 9 engineer-weeks; mis-sequence and we burn the seed window
- Dependencies on external libraries (tree-sitter, sqlglot, BGE) — versions change, indexer accuracy varies per language
- Per-workspace tuning requires feedback signal — cold-start customers have lower-quality tuning for first month
- Calibrator quality depends on label volume; need ≥ 50 labeled examples per workspace before calibrator is useful (fallback to global until then)
- Joern + Doop are slow; only run on critical paths and as background, never inline
- Multi-model review doubles LLM cost per chunk on extraction (mitigated by aggressive caching + small verifier model)

**Cost estimate (Wave 1)**:

| Item | Engineer-weeks | Sonnet $ |
|---|---|---|
| SQL deep extractor (sqlglot + embedded scan + multi-model review) | 2 | $25 |
| Hybrid retrieval (BM25 + dense + RRF + BGE) | 2 | $20 |
| Prompt caching + semantic cache | 1 | $10 |
| Verbalized confidence + multi-signal aggregator | 1 | $15 |
| Streaming + parallel retrieval | 0.5 | $10 |
| Glossary auto-discovery | 1.5 | $20 |
| Few-shot bank | 1 | $15 |
| **Total Wave 1** | **9** | **$115** |

Two engineers, parallel-safe in places, ~4-5 calendar weeks.

---

## Phasing

**Wave 1 (seed window, 4-5 calendar weeks)**: SQL deep extractor + hybrid retrieval + prompt caching + semantic cache + verbalized confidence + streaming + parallel retrieval + glossary auto-discovery + few-shot bank.

**Wave 2 (seed→Series A)**: SCIP indexers + Stack Graphs + Joern critical paths + LazyGraphRAG + CoVe + per-workspace calibrator + reranker LTR.

**Wave 3 (Series A and after)**: Speculative decoding + HippoRAG multi-hop + AGSER self-reflection + per-workspace embedding fine-tune (rare) + persona template auto-variants.

---

## Open questions

1. **Tree-sitter chunking vs LSP chunking — which first?** Recommendation: tree-sitter for Wave 1 (works for all languages, no build dependency); SCIP/LSP for Wave 2 where we want type-resolved edges.
2. **Joern licensing**: open-source community edition vs paid Qwiet — confirm community edition is sufficient for our use cases before depending.
3. **Reranker hosting**: BGE-reranker-v2-M3 can run locally (small GPU) or via Cohere API — pick based on workspace cost tier (self-hosted for enterprise; API for trials).
4. **Glossary discovery cost** scales with corpus size — cost-cap per workspace; degrade gracefully on huge workspaces (sample-based discovery).
5. **Cold-start tuning fallback**: until per-workspace calibrator + reranker have data, use global defaults. Need explicit "tuning maturity: cold/warming/warm" indicator surfaced in admin UI.
6. **Multi-model extraction governance**: Sonnet-extract + Haiku-verify means two model versions in play. Standardize on Anthropic models (already a dependency) to keep version skew manageable.
7. **Federated tuning across customers?** Definitely off by default for trust reasons; possibly an opt-in feature for industry-vertical benchmarks (Healthcare anonymous aggregate) — defer to Series A.

---

## What this unlocks

- Demo: SQL queries now return real lineage + JPA/JDBC coverage; blast radius shows 30-80 sites instead of 4-7; confidence numbers actually move based on evidence; queries stream in under 3s
- Pitch: "Brain that gets smarter about your company every week" becomes provable in 4-week demos (show glossary growing, few-shot bank shifting, calibrator ECE dropping)
- Series-A flywheel: per-workspace tuning is the moat. Easy to demo; impossible for competitors to replicate without time-in-customer
- Enterprise readiness: calibrated confidence + provenance tiers + reliability diagrams unlock the regulated-industry sales conversation

---

## Related docs

- Full background + diagnosis: `docs/ARCHITECTURE-V2-QUALITY-AND-DOMAIN-TUNING.md`
- Dev process: `docs/SPEC-DRIVEN-DEV-FRAMEWORK.md`
- Wave 1 implementation prompt: `docs/adrs/SONNET-IMPLEMENTATION-PROMPT-V2-WAVE-1.md`
