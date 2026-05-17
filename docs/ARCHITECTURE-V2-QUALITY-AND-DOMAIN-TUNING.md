# Architecture V2 — Quality Overhaul + Per-Company Domain Tuning

**Status**: Proposal (formalized as ADR-0085)
**Date**: 2026-05-17
**Driver**: extraction quality is poor (SQL incomplete, blast radius shallow, confidences uniformly low, queries 17-43s). The fix is not patches — it's an architectural V2 that (a) borrows SOTA from Glean/Sourcegraph/Cursor + research, (b) makes per-company domain tuning a first-class pillar, and (c) ships via spec-driven development against the new frontend.

This doc is the master strategy. The formal ADR is `ADR-0085-architecture-v2.md`. The dev process is `SPEC-DRIVEN-DEV-FRAMEWORK.md`. The first implementation wave has its own prompt.

---

## TL;DR

Four root causes diagnosed in the codebase (file:line evidence):

1. **SQL extractor only handles DDL** (`schema_sql.py:116-127` — only CREATE/ALTER/INDEX). Misses every SELECT/INSERT/UPDATE/DELETE, every JPA `@Query`, every JDBC string literal, every stored proc.
2. **Blast radius is forward-only, hard-capped, control-flow-only** (`BlastRadiusService.java:46-160` — depth 5, no decay, no data flow). Bidirectional traversal is off by default.
3. **Confidence is a ternary boolean** (`query.py:544-574` — `level = "medium" if context else "low"`). Never reaches "high". No calibration. No multi-signal aggregation.
4. **Query pipeline is fully sequential** (`query.py:88-310` — SmartZone → Hybrid → LLM → Exploration → Notes, all sequential, no caching, no streaming).

The fix is a four-pillar V2 informed by SOTA (Glean, Sourcegraph SCIP, Tree-sitter, sqlglot, Joern, LazyGraphRAG, ColBERT, BGE rerankers, Anthropic prompt caching, verbalized confidence, Chain-of-Verification) and a fifth pillar that's new: **per-company domain tuning** — every brain becomes a workspace-specific brain over time.

Engineering envelope: ~10-14 weeks for V2 wave 1 (2 engineers). Quality target: P50 query latency < 3s; blast radius recall × 3; SQL coverage × 5; confidence calibration ECE < 0.10; per-workspace fit measurably improving week-over-week.

---

## Part 1 — Diagnosis (grounded, with file:line evidence)

### Issue 1: SQL extraction misses 80%+ of real SQL

**File**: `company-brain-ai/src/companybrain/extractors/schema_sql.py:116-127`

```python
def _statement_kind(stmt: str) -> Optional[str]:
    s = stmt.upper().lstrip()
    if s.startswith("CREATE TABLE"): return "CREATE_TABLE"
    if s.startswith("ALTER TABLE"): return "ALTER_TABLE"
    if s.startswith("CREATE INDEX"): return "CREATE_INDEX"
    return None   # ← every other SQL statement: SILENTLY DROPPED
```

Lines 98-103 only route these three kinds. **Every other SQL** (SELECT, INSERT, UPDATE, DELETE, MERGE, CREATE VIEW, CREATE FUNCTION, CREATE TRIGGER, CREATE PROCEDURE) returns None and is discarded. The extractor also never scans `.java`/`.py`/`.kt`/`.go` source for **embedded SQL strings** — JPA `@Query`, `entityManager.createQuery`, jOOQ DSL, raw JDBC `PreparedStatement(...)`, MyBatis annotations, Hibernate HQL.

For a Java-heavy demo repo like network-iq-backend-java, this means we miss ~80-90% of actual SQL behavior. The brain "knows" the schema but doesn't know how the schema is used.

### Issue 2: Blast radius is shallow, single-direction, control-flow-only

**File**: `company-brain-backend/src/main/java/com/companybrain/service/BlastRadiusService.java:46-160`

- `max-depth: 5` (hard cap)
- `bidirectional: true` BUT only when the feature flag is explicitly toggled (default = forward-only)
- Recursive CTE in `runBidirectionalTraversal()` cuts off at `depth < ?` — items at distance 6 are invisible
- JOIN condition is strictly `source_id → target_id`; no edge weight, no confidence decay over hops
- **No data-flow tracking** — only control-flow (callsite edges). Parameter aliasing, return-value propagation, taint paths are not modeled
- **No semantic edges** — two functions that operate on the same domain entity (Payer) aren't connected unless one calls the other

Effect: a developer asks "if I change PayerService.update, what breaks?" — brain returns 4-7 direct callers. Real answer is 30-80 sites once you include data flow + semantic neighborhood.

### Issue 3: Confidence is a binary masquerading as a score

**File**: `company-brain-ai/src/companybrain/api/routes/query.py:544-574, 928-932`

```python
# query.py:567
level = "medium" if context else "low"
```

- Confidence is essentially `bool(context_was_retrieved)` mapped to a 3-level label
- Exploration Agent can lift "low" → "medium" but never to "high" (line 930 hardcodes `level="medium"`)
- No calibration mechanism (ADR-0005 was specified but not implemented)
- LLM is never asked to emit its own confidence (no verbalized confidence)
- No multi-signal aggregation — citation count, edge confidence mean, retrieval-rank consistency are not combined

Effect: every demo answer says "medium confidence." Customers learn to ignore confidence entirely. Trust collapses.

### Issue 4: Query pipeline is sequential by construction

**File**: `company-brain-ai/src/companybrain/api/routes/query.py:88-310`

Stages (all sequential, all blocking):

1. SmartZone assembly (creates new assembler + new Neo4j session per query)
2. Hybrid retrieval (only if SmartZone returns empty; fallback path)
3. LLM call (synchronous `provider.chat()`, full token generation before any response)
4. Exploration Agent (conditional; runs N tool calls AFTER LLM returns)
5. Notes lookup (sequential)
6. Markdown rendering

No embedding cache, no semantic cache, no prefix caching, no parallel retrieval, no streaming response, no speculative tool calls. Each query pays the full setup cost.

Observed P50 latency: 17-43s. SOTA targets (Cursor, Cody, Glean): chat responses begin streaming under 1s, complete under 5s.

---

## Part 2 — SOTA we're going to steal

From two research passes (code-extraction SOTA + retrieval/RAG SOTA) and two GitHub-skill reviews (addyosmani/agent-skills + andrej-karpathy-skills):

### Extraction stack

| Layer | SOTA pick | Why |
|---|---|---|
| Syntactic | **Tree-sitter** (incremental GLR, 40+ grammars, sub-ms reparse) | Used by Sourcegraph, Cursor, Continue, Greptile, Aider. Industry consensus. |
| Resolved cross-refs | **SCIP** (Sourcegraph's LSIF successor) + per-language LSP (jdtls/pyright/gopls/rust-analyzer) | Real types, generics, inheritance. Replaces our weak heuristic resolution. |
| Name resolution without build | **Stack Graphs** (GitHub) | Powers GitHub precise nav. Works when builds are unavailable (the common case). |
| SQL parsing | **sqlglot** (20+ dialects, column-level lineage) + **OpenLineage-SQL parser** (Rust, prod-grade) for embedded SQL extraction | Replaces our DDL-only regex. Gets full SQL coverage. |
| Call graph + data flow | **Joern** (Code Property Graph: AST + CFG + PDG unified) for critical paths; **Doop** (Datalog pointer analysis) for Java | Yamaguchi et al. CPG 2014 is the academic standard; production at GitHub, Qwiet. |
| Dynamic SQL | **Java String Analyzer / abstract string analysis** (Christensen et al. SAS 2003) for prepared-statement placeholder tracking | The only rigorous approach for non-literal SQL. |

### Retrieval stack

| Layer | SOTA pick | Why |
|---|---|---|
| First-stage | **BM25 + dense (e5/bge) fused via RRF** (Cormack 2009; k=60) | Wins on BEIR; BM25 specifically wins on rare-token / identifier queries (critical for code: function/class names). |
| Late interaction | **ColBERTv2** for high-recall pre-rerank when needed | 10× smaller than v1; competitive with cross-encoders at fraction of latency. |
| Reranker | **BGE-reranker-v2-M3** (BAAI, top of BEIR/C-MTEB/MIRACL) on top-50 | ~8-11% nDCG lift on top of first-stage. |
| Graph-augmented | **LazyGraphRAG** (Microsoft, Nov 2024) — defer summarization to query time | Indexing cost = vector-RAG, multi-hop wins of GraphRAG. Best default for our workload. |
| Multi-hop | **HippoRAG** (NeurIPS 2024) — KG + Personalized PageRank | 10-30× cheaper than IRCoT-style iterative RAG, comparable accuracy. |

### Quality / confidence stack

| Layer | SOTA pick | Why |
|---|---|---|
| LLM verbalized | **"Just Ask for Calibration"** (Tian et al., EMNLP 2023, arxiv 2305.14975) | RLHF models verbalize confidence better than logits indicate. Cuts ECE ~50%. |
| Numerical calibration | **Temperature scaling** (Guo et al., arxiv 1706.04599) + per-workspace calibrator | Single most cost-effective calibration knob. |
| Self-verification | **Chain-of-Verification (CoVe)** (Dhuliawala et al., arxiv 2309.11495) | Strong hallucination cut, modest latency. |
| Self-consistency | Sample N, majority-vote (Wang et al., arxiv 2203.11171) for hard queries only | AUROC ~0.74-0.76 for hallucination detection. Expensive; route hard queries only. |
| Hallucination detection | **AGSER** (Attention-Guided Self-Reflection, 2024) | +10-17pp AUC over self-consistency at 3 LLM passes. |

### Latency stack

| Layer | SOTA pick | Effect |
|---|---|---|
| Prefix caching | **Anthropic prompt caching** (system prompt + entity-graph context) | ~85% TTFT reduction, 90% cost reduction on cache hits. Single biggest lever. |
| Semantic cache | **GPTCache** (arxiv 2411.05276) for exact / near-duplicate queries | 61-69% hit rate in production studies; ~31% of queries are semantic duplicates. |
| Speculative decoding | EAGLE-2/Medusa or vLLM speculative | 1.6-3.6× speedup in published deployments. |
| Streaming | Token-by-token prose; structured fields deferred to a final pass | TTFT < 1s achievable. |
| Parallel | All retrieval signals (BM25, dense, graph) fire in parallel; tool calls in parallel where commutative | Eliminates sequential bottleneck. |

### Process / structure (from agent-skills repos)

| Pattern | Source | Use |
|---|---|---|
| **SKILL.md format** (frontmatter + steps + checkpoints + exit criteria) | addyosmani/agent-skills | Wrap every extractor and reasoner in this shell. |
| **Anti-rationalization tables** (pre-written rebuttals to "I'll add tests later") | addyosmani | Embed in extractor prompts to suppress shortcut hallucination. |
| **Evidence-or-it-didn't-happen exit criteria** | addyosmani | Answers without citations are rejected. Same standard as "no merge without test." |
| **Multi-model review** (extract with one, verify with another) | addyosmani | Use Sonnet for extraction; Haiku for verification. Cheap hallucination cut. |
| **Spec-driven gates** (Specify → Plan → Tasks → Implement) | addyosmani | Apply to BOTH (a) brain extraction passes and (b) frontend feature dev. |
| **Surface assumptions explicitly** (preamble: scope, format, fields, volume) | karpathy-skills | Every extractor pass names what it assumed (entry point, framework, language dialect). |
| **Present multiple interpretations** (don't collapse early) | karpathy-skills | When extracting ambiguous entities, output ranked interpretations with confidence. |
| **Goal-driven success criteria** (until N call sites resolve, not "analyze the repo") | karpathy-skills | Frames extraction completion as measurable, not "looks good." |
| **Surgical-change discipline** | karpathy-skills | Map to incremental indexing — only re-extract what changed. |
| **Paired wrong/right examples** | karpathy-skills | All prompts ship contrastive examples (triggers contrastive reasoning more reliably than rules). |

---

## Part 3 — The four-pillar V2 architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    PILLAR 5 — PER-COMPANY DOMAIN TUNING                 │
│   Workspace-adaptive calibrator + glossary + few-shot bank + reranker   │
│   + persona templates + reliability tracker; learns continuously        │
└───────────────────────────┬─────────────────────────────────────────────┘
                            │ tunes every other pillar per workspace
        ┌───────────────────┼───────────────────┬────────────────────┐
        ▼                   ▼                   ▼                    ▼
┌──────────────┐  ┌──────────────────┐  ┌────────────────┐  ┌────────────────┐
│  PILLAR 1    │  │   PILLAR 2       │  │   PILLAR 3     │  │   PILLAR 4     │
│  DEEP        │  │   HYBRID         │  │   CALIBRATED   │  │   FAST         │
│  EXTRACTION  │  │   RETRIEVAL      │  │   CONFIDENCE   │  │   PIPELINE     │
│              │  │                  │  │                │  │                │
│ Tree-sitter  │  │  BM25 + dense    │  │ Verbalized +   │  │ Anthropic      │
│ + LSP/SCIP   │  │  + RRF fusion    │  │ temperature +  │  │ prompt caching │
│ + Stack Grph │  │  + BGE reranker  │  │ multi-signal   │  │ + GPTCache     │
│ + sqlglot    │  │  + LazyGraphRAG  │  │ aggregation    │  │ + streaming    │
│ + Joern      │  │  graph layer     │  │ + CoVe         │  │ + parallel     │
└──────────────┘  └──────────────────┘  └────────────────┘  └────────────────┘
```

### Pillar 1 — Deep Extraction

**Goal**: replace the current shallow regex+heuristic extractor with a layered AST-first stack that catches SQL, resolves cross-file references, and builds true call+data-flow graphs.

**Components**:

- **Tree-sitter chunking** by symbol boundary (function, class, module), not fixed-size windows. Every chunk anchored to a stable symbol URN.
- **SCIP indexing** per language using the official Sourcegraph indexers (scip-java, scip-python, scip-typescript, scip-go, scip-ruby). One mature indexer per language replaces N custom extractors.
- **Stack Graphs** for cross-file name resolution where builds aren't reproducible (the common case for messy customer repos).
- **SQL deep extractor**: scan all source files for embedded SQL via tree-sitter (find `@Query`, `createQuery`, `PreparedStatement`, jOOQ DSL fluent chains, MyBatis XML); parse extracted strings with sqlglot for AST + column-level lineage; route DDL through current DDL path; flag dynamic SQL with reduced confidence; track placeholder paths via abstract string analysis.
- **Joern Code Property Graph** for the top-100 critical paths per repo (the slow but accurate stack). Forwarded into the brain as the high-confidence backbone.
- **Two-agent extraction with model split**: Sonnet does extraction; Haiku does verification (multi-model review per agent-skills pattern). 80% cost reduction on verification with strong hallucination cut.

**What gets extracted that we miss today**: every SELECT/INSERT/UPDATE in JPA / JDBC / jOOQ; column-level lineage from query A to view B to dashboard C; resolved type information across files; cross-file call edges via Stack Graphs; data-flow taints via Joern.

### Pillar 2 — Hybrid Retrieval

**Goal**: replace the current single-mode retrieval with a tiered hybrid pipeline that wins on identifier queries (BM25), semantic queries (dense), and multi-hop reasoning (graph).

**Pipeline**:

```
Query
  ├─ BM25 lexical retrieval (rank list A)         ┐
  ├─ Dense vector retrieval (rank list B)         │ all three fire
  └─ Graph traversal: LazyGraphRAG (rank list C)  ┘ in parallel
                                                  
RRF fusion (k=60) over A, B, C → top-50          (Cormack 2009)
                                                  
BGE-reranker-v2-M3 cross-encoder → top-K         (~8-11% nDCG lift)
                                                  
Optional: HippoRAG personalized PageRank for     
multi-hop queries flagged by intent classifier   
```

**Index requirements**:
- BM25 over symbol names, file paths, comment text, identifier-bag-of-words per entity
- Dense embeddings (e5-large-v2 or bge-large) per chunk + per entity
- Graph adjacency for LazyGraphRAG (entity nodes + edges with weights, no community summaries upfront)

### Pillar 3 — Calibrated Confidence

**Goal**: confidences that mean something. Move from `bool(context)` to a calibrated multi-signal score with reliability tracking.

**Mechanism**:

1. **LLM emits verbalized confidence** in structured output: `{"confidence_label": "high|medium|low", "confidence_score": 0.0-1.0, "rationale": "..."}` (Tian et al. — RLHF models do this better than logits indicate)
2. **Aggregator combines signals**:
   - Verbalized score (from above)
   - Number of citations
   - Mean citation confidence (from extraction)
   - Min citation confidence (the weakest link matters)
   - Retrieval rank consistency across BM25/dense/graph (RRF agreement)
   - Blast-radius coverage (do citations span the affected entity neighborhood?)
3. **Per-workspace temperature scaling + logistic regression calibrator** (single-knob fit on labeled feedback): turns raw aggregate into a calibrated probability matched to "user marks answer correct" labels
4. **CoVe (Chain-of-Verification) pass** on high-stakes queries (configurable; default on for PM/VP/CFO answers): draft → generate verification subquestions → answer independently → revise. Strong hallucination cut.
5. **Reliability diagram per workspace** — track ECE weekly per ADR-0067 background process. Recalibrate when ECE drifts above 0.10.

### Pillar 4 — Fast Pipeline

**Goal**: P50 query latency < 3s, P90 < 8s. Five compounding wins:

1. **Anthropic prompt caching** on (system prompt + workspace context + entity graph card pack). Single biggest lever. 85% TTFT cut on cache hits.
2. **GPTCache semantic cache** in front of the pipeline for near-duplicate queries. 60%+ hit rate observed in production.
3. **Streaming** the prose answer token-by-token (FastAPI StreamingResponse); structured fields (citations, confidence) appended at end.
4. **Parallel retrieval**: BM25, dense, graph fire concurrently via asyncio.gather. Already designed; just not wired.
5. **Lazy exploration**: only run Exploration Agent when initial confidence < 0.5. Today it runs speculatively.

Plus the trivial wins: singleton Neo4j driver (already partial); preloaded brain store; warm-up on boot.

---

## Part 4 — Pillar 5: Per-Company Domain Tuning (the new pillar)

**Premise**: a brain that gives the same answer for every company is a generic search engine. The moat is **the brain that knows YOUR company better every week.** This pillar is what makes that real.

### What "per-company tuning" means concretely

Four layers, in increasing depth:

| Layer | Component | Update cadence |
|---|---|---|
| **Coarse** (config-driven) | Vertical pack selection (Healthcare-RCM / SaaS / Fintech) + workspace YAML | Set at onboarding; rare changes |
| **Medium** (data-driven, no training) | Auto-discovered glossary (terms, synonyms, acronyms); few-shot bank (50-200 successful Q&A from this workspace); persona binding refinements | Weekly background process (ADR-0067) |
| **Fine** (learned, lightweight) | Per-workspace confidence calibrator; per-workspace learning-to-rank reranker tuning; per-workspace prompt template variants | Weekly background process; promoted via ADR-0083 |
| **Continuous** (feedback loop) | Thumbs up/down per answer; edit signals; follow-up frequency; click-through on citations; ADR-0066 experiential memory | Real-time capture; aggregation weekly |

### The architecture

```
                  ┌──────────────────────────────────┐
                  │  WORKSPACE TUNING STORE          │
                  │  (per workspace, versioned)       │
                  │                                  │
                  │  - Glossary YAML                 │
                  │  - Few-shot bank (Q&A pairs)     │
                  │  - Calibrator coefficients       │
                  │  - Reranker tuning weights       │
                  │  - Persona binding overrides     │
                  │  - Reliability diagram           │
                  │  - Vertical pack version pinned  │
                  └────────────┬─────────────────────┘
                               │
                  ┌────────────┴─────────────┐
                  │                          │
        ┌─────────▼─────────┐    ┌──────────▼──────────┐
        │ INFERENCE TIME    │    │ BACKGROUND PROCESS  │
        │ (per query)       │    │ (weekly, ADR-0067)  │
        │                   │    │                     │
        │ Loads tuning      │    │ Aggregates signals  │
        │ artifacts;        │    │ from ADR-0066;      │
        │ injects glossary, │    │ refits calibrator,  │
        │ few-shots,        │    │ updates glossary,   │
        │ calibrator into   │    │ promotes few-shots, │
        │ each prompt and   │    │ updates reranker    │
        │ each ranker call  │    │ weights;            │
        │                   │    │ versions everything │
        └───────────────────┘    └─────────────────────┘
```

### Component details

**Auto-discovered glossary**

Terms appearing in the workspace's corpus that aren't dictionary words or common code symbols are candidate glossary entries. For each:
- Auto-generate a definition from contextual mentions (Haiku-class LLM, batched)
- Track aliases via embedding clustering (e.g., "PA" / "PriorAuth" / "prior_auth" → same entry)
- Per-source frequency (mentioned in code 12×, PRDs 3×, Slack 47×, calls 8×)
- Promotion threshold (configurable; default: appears in 2+ source types, mentioned ≥ 20×) → adds to active glossary

Active glossary is injected into every retrieval and prompt as workspace context. Reduces "what does X mean here" failures dramatically.

**Few-shot bank**

Successful Q&A pairs from this workspace (high thumbs-up, no follow-up, citations clicked) are cached as few-shot exemplars. At query time, the most-similar 2-5 exemplars are injected into the answerer's prompt.

Effect: the brain answers "in this company's voice and pattern" within a few weeks. The pattern doesn't just guide style — it guides which entities to cite, what level of detail, what format.

**Per-workspace calibrator**

Logistic regression over confidence features (verbalized LLM confidence, citation count, mean/min citation confidence, retrieval RRF agreement, blast-radius coverage) calibrated against labeled "answer was correct" feedback. ~50-200 labeled examples bootstrap a usable calibrator; quality improves with more labels.

Output: a calibrated probability replacing the current ternary label. ECE tracked per workspace.

**Per-workspace reranker tuning**

The BGE reranker is generic. After 200+ labeled query-result pairs from a workspace, fit a learning-to-rank model (lightgbm or small linear) over the reranker's scores + workspace features (entity match, term-in-glossary, recency, source authority per ADR-0078).

Effect: the reranker learns "in THIS workspace, citations from the architecture-decisions/ folder rank higher than from chat archives" or similar patterns.

**Persona template variants**

ADR-0079 templates start as defaults. Per workspace, the refinement loop (ADR-0079 M5 + ADR-0066) tracks which template variants score highest for which personas and gradually promotes per-workspace overrides.

**Reliability tracking**

Per workspace, per persona, per template: track answer accuracy via feedback. Surface in admin UI. When a template's reliability drops below threshold, flag for refinement or rollback.

### How tuning makes its way into every pillar

| Pillar | How per-workspace tuning enters |
|---|---|
| 1 Extraction | Glossary informs entity inference (e.g., "PriorAuth" is known); vertical pack provides patterns (Healthcare-RCM extractor variants per ADR-0083 M4) |
| 2 Retrieval | Reranker tuning weights; glossary terms boosted in BM25; few-shot exemplars in query reformulation |
| 3 Confidence | Calibrator coefficients per workspace; reliability diagram per persona |
| 4 Pipeline | Few-shot bank loaded into prompt-cache prefix → cached after first hit; glossary in workspace context block (cached) |
| 5 Self (this pillar) | Background process (ADR-0067) re-fits calibrator + reranker + glossary weekly |

---

## Part 5 — Implementation phasing

### Wave 1 (4-6 weeks, 2 engineers in parallel)

Highest leverage, ship-able now. Single coordinated session prompt (see `SONNET-IMPLEMENTATION-PROMPT-V2-WAVE-1.md`).

| Item | Pillar | Effort | Why first |
|---|---|---|---|
| SQL deep extractor (sqlglot + tree-sitter embedded scan) | 1 | 2 weeks | Biggest single quality win; demoable |
| Hybrid retrieval (BM25 + dense + RRF + BGE reranker) | 2 | 2 weeks | Quality + identifier-query wins |
| Anthropic prompt caching on system + workspace context | 4 | 0.5 week | Single biggest latency win; cheap |
| GPTCache semantic cache in front | 4 | 0.5 week | 60%+ hit rate on repeated queries |
| Verbalized confidence + multi-signal aggregator | 3 | 1 week | Confidence becomes meaningful immediately |
| Streaming responses end-to-end | 4 | 0.5 week | TTFT < 1s |
| Glossary auto-discovery (background) | 5 | 1.5 weeks | Per-workspace tuning bootstrap |
| Few-shot bank capture + injection | 5 | 1 week | Per-workspace tuning compounds weekly |

Total: ~9 engineer-weeks. Two engineers, parallel-safe in places, ~4-5 calendar weeks.

### Wave 2 (post-seed, 6-8 weeks)

| Item | Pillar |
|---|---|
| SCIP indexing per language (replace heuristic resolution) | 1 |
| Stack Graphs for build-less name resolution | 1 |
| Joern CPG for critical paths | 1 |
| Confidence-annealing blast radius + data flow | 1 |
| LazyGraphRAG layer | 2 |
| CoVe pass for high-stakes queries | 3 |
| Per-workspace calibrator + temperature scaling | 5 |
| Reranker tuning per workspace | 5 |

### Wave 3 (Series-A and after)

| Item | Pillar |
|---|---|
| Speculative decoding | 4 |
| HippoRAG for multi-hop | 2 |
| AGSER attention-guided self-reflection | 3 |
| Per-workspace embedding fine-tune (rare) | 5 |
| Persona template variants per workspace (auto) | 5 |

---

## Part 6 — Spec-driven development (for V2 and the frontend)

Per the addyosmani spec-driven-development skill, every V2 component AND every frontend feature ships via a four-gate flow:

```
SPECIFY  →  PLAN  →  TASKS  →  IMPLEMENT
   │          │         │           │
   │          │         │           └─ acceptance gate: tests pass, evidence captured
   │          │         └────────────── breakdown gate: every task < 0.5 day, has acceptance criteria
   │          └──────────────────────── design gate: architecture review, dependency check
   └─────────────────────────────────── intent gate: written user need, success criteria
```

Details in `SPEC-DRIVEN-DEV-FRAMEWORK.md`. The frontend features (drift dashboard, query console, brain browser, persona switcher) all flow through this gate sequence going forward.

---

## Part 7 — Quality targets (measurable, not vibes)

After Wave 1:

| Metric | Today | Wave-1 target |
|---|---|---|
| SQL coverage (% of SQL in corpus extracted with full AST) | ~15-20% | > 75% |
| Blast-radius recall (vs golden set on network-iq) | < 30% | > 65% |
| Confidence ECE (per workspace, weekly) | not measured | < 0.15 |
| Citations per answer (mean) | < 1 | > 3 |
| Query P50 latency | 17s | < 3s |
| Query P90 latency | 43s | < 8s |
| Cache hit rate (semantic + prefix) | 0% | > 40% combined |
| Per-workspace glossary terms (week 4) | 0 | > 50 |
| Few-shot bank size (week 4 of demo) | 0 | > 30 |

Tracked via a `tests/quality/` golden set with per-metric regression tests. Drop in any metric > 10% fails CI.

---

## TL;DR

1. **The four root causes are diagnosed with file:line evidence** — SQL DDL-only, blast radius shallow + forward-only, confidence is a boolean, pipeline is fully sequential.
2. **SOTA gives us a clear stack to steal** — Tree-sitter + SCIP + sqlglot + Joern (extraction); BM25 + dense + RRF + BGE reranker + LazyGraphRAG (retrieval); verbalized confidence + temperature scaling + CoVe (quality); Anthropic prompt caching + GPTCache + streaming (latency).
3. **Per-company domain tuning is the fifth pillar** — workspace-adaptive glossary, few-shot bank, calibrator, reranker tuning, persona variants. Bootstraps fast (week 1) and compounds weekly.
4. **Spec-driven dev applies to both** the V2 engineering AND every frontend feature going forward.
5. **Wave 1 (~9 engineer-weeks) is the bet**: SQL deep extractor, hybrid retrieval, prompt caching, semantic cache, verbalized confidence, streaming, glossary discovery, few-shot bank. Targets a 5× quality + 5× speed gain. Single implementation prompt to run.
6. **Quality targets are measurable**: SQL coverage > 75%, blast radius recall > 65%, P50 < 3s, ECE < 0.15. Not vibes.

Formal ADR: `ADR-0085-architecture-v2.md`. Spec-driven dev process: `SPEC-DRIVEN-DEV-FRAMEWORK.md`. Wave 1 prompt: `SONNET-IMPLEMENTATION-PROMPT-V2-WAVE-1.md`.
