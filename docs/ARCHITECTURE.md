# How the Brain Works (current state, May 2026)

This is the **as-built** architecture of company-brain — what's actually
shipped today. The ADR-0048/0049/0050/0051/0052 sequence describes
where we're going, NOT where we are. If you find something here that
contradicts an ADR, the ADR is the future and this doc is the present.

---

## TL;DR

Company-brain is a **codebase context extraction + query system**. You
point it at a target repo + an HTTP endpoint, and it produces a
queryable knowledge graph of every entity (class, method, table, column,
endpoint) reachable from that endpoint, plus 21 fields of business
context per entity (purpose, change risk, invariants, side effects, …).

Then you ask it natural-language questions like *"what would break if I
rename the `lob` column?"* and it gives you a cited answer in seconds
with the actual SQL chains, affected entities, and risk assessment.

---

## Services (six of them)

```
┌─────────────────────────────────────────────────────────────────────────┐
│  USER                                                                    │
│   ├─ make -f Makefile.demo run-cli  (terminal)                           │
│   ├─ http://localhost:5173          (frontend UI)                        │
│   └─ curl ...                       (direct API)                         │
└──────────┬──────────────────────────────────────────────┬───────────────┘
           │                                               │
           ▼                                               ▼
┌──────────────────────┐                     ┌────────────────────────────┐
│ company-brain-       │                     │ company-brain-frontend     │
│ backend              │                     │ (Vite + React, :5173)      │
│ (Spring Boot, :8080) │                     │ — API Explorer             │
│ — public job API     │                     │ — query UI                 │
│ — orchestrates Python│◀────────────────────┤ — talks to Python :8000    │
│ — persists to PG     │  (job-create only)  │   for live queries         │
└──────────┬───────────┘                     └────────────────────────────┘
           │ HTTP
           ▼
┌──────────────────────────────────────────────────────────────────────────┐
│ company-brain-ai (Python FastAPI, :8000) — THE ACTUAL PIPELINE           │
│  ┌───────────────────────────────────────────────────────────────────┐   │
│  │ POST /pipeline/run     — runs the full extraction pipeline        │   │
│  │ POST /query            — answers natural-language questions       │   │
│  │ POST /query/stream     — same, SSE                                │   │
│  │ POST /resynthesise     — feedback loop on bad answers             │   │
│  │ GET  /pipeline/jobs/X  — job status                               │   │
│  │ GET  /repo/branches    — list branches in a target repo           │   │
│  └───────────────────────────────────────────────────────────────────┘   │
└──────────┬───────────────────────────────────────────────────────────────┘
           │ HTTP (structural pre-pass only)
           ▼
┌──────────────────────────────────────────────────────────────────────────┐
│ cb-api (Bun TypeScript, :8090)                                           │
│ — language-agnostic structural extractor (tree-sitter)                   │
│ — POST /v1/extract  → returns {nodes, edges, file_hashes}                │
│ — populates Neo4j directly with structural facts before LLM extraction   │
│ — language extractors: core-ts, framework-jpa, framework-prisma,         │
│   framework-openapi, framework-sql, framework-next, framework-sqlalchemy │
└──────────────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────────────┐
│ apps/extractor-worker (Bun TypeScript)                                   │
│ — background worker draining the chunk extraction queue                  │
│ — currently optional; the Python orchestrator drains the queue itself    │
└──────────────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────────────┐
│ apps/cli (Bun TypeScript)                                                │
│ — `cb extract <repo> <endpoint>` standalone CLI                          │
│ — same backend as the Java route                                         │
└──────────────────────────────────────────────────────────────────────────┘
```

---

## Data stores (six of them)

| Store | Port | Holds | Read by | Written by |
|---|---|---|---|---|
| **Postgres** | 5432 | `nodes`, `edges`, `edge_events`, `node_context`, `extraction_queue`, `pipeline_jobs`, `flyway_schema_history` | Java, Python, cb-api | Python, Java |
| **Neo4j** | 7687/7474 | structural graph (Class/Method/Table nodes, CALLS/EXTENDS edges) | Python query path | cb-api (structural), Python (LLM-extracted) |
| **Qdrant** | 6333 | entity embeddings (`brain__<workspace>__component`, `…__api_contract`) | Python query path (semantic search) | Python |
| **Redis** | 6379 | rate-limit state, session cache, scheduled task queue | Python | Python |
| **LocalStack S3** | 4566 | artifacts (raw extracted source, git diffs, screenshots) | Python | Python |
| **JSON brain** | filesystem | `<target_repo>/.brain/{index.json,manifest.json,component/*.json,api_contract/*.json,.l2-cache/main.json}` | Python query path (primary read) | Python |
| **Langfuse** | 3001 | LLM call telemetry (cost, latency, tokens) | observability dashboard | Python providers |

---

## External dependencies

- **Anthropic API** — Haiku-4.5 for extraction (`fast`/`balanced`/`synthesis`/`reasoning` roles), Sonnet-4.6 for `/query`. Configured in `.env` as `ANTHROPIC_MODEL_*`.
- **GitHub** — commit history fetched by `GitCollector` for the "why" context (Stage 0b).
- **Ollama** (optional, port 11434) — local-only model fallback; `LLM_PROVIDER=ollama` to route everything through it.

---

## The extraction pipeline (current 7-stage linear flow)

```
                                 ┌───────────────────────────┐
   POST /pipeline/run            │  STAGE 0a — Code Tracing  │
   { endpoint, method, repos }   │                            │
        │                        │  CodeTracer.trace()        │
        │                        │   ├─ LLMHandlerFinder      │  1 LLM call
        │                        │   │  → entry handler file  │
        │                        │   └─ KnowledgeNavigatorAgent│ ~25 LLM calls
        │                        │      → CodeUnit[]          │  (ReAct loop)
        │                        └────────────┬───────────────┘
        ▼                                     │
┌──────────────┐                              ▼
│ orchestrator │              ┌───────────────────────────────┐
│ .run_pipeline│              │  STAGE 0b — Git History       │
│              │─────────────▶│  GitCollector.collect()       │
│ checkpoints  │              │  → 28 clusters, 262 commits   │
│ at every     │              │  (parallel with 0a)           │
│ stage        │              └────────────┬──────────────────┘
└──────┬───────┘                           ▼
       │                ┌───────────────────────────────────────┐
       │                │  STAGE 0.5 — Structural pre-pass      │
       │                │  POST cb-api/v1/extract               │
       │                │  → tree-sitter parses every changed   │
       │                │    file; emits structural nodes/edges │
       │                │    directly into Neo4j                │
       │                │  → returns file fingerprints          │
       │                │  → Stage 1 skips files whose hash     │
       │                │    matches the L2 cache (warm rerun)  │
       │                └────────────┬──────────────────────────┘
       │                             ▼
       │       ┌────────────────────────────────────────────────┐
       │       │  STAGE 1 — Entity extraction                   │
       │       │                                                 │
       │       │  IF BRAIN_USE_CHUNK_QUEUE (default true):       │
       │       │    CodeChunker.chunk_repo()                     │
       │       │      → MethodChunk[] (per-method)               │
       │       │    ChunkRelevanceFilter                         │
       │       │      → drop trivial accessors (tier 1)          │
       │       │    ChunkBatcher                                 │
       │       │      → group small siblings (≤8 per batch)      │
       │       │    enqueue → extraction_queue (Postgres)        │
       │       │    drain_queue (max_workers=2-4)                │
       │       │      → ContextAgent.extract_batch (LLM)         │
       │       │      → entities + edges per method              │
       │       │                                                 │
       │       │  ELSE (BRAIN_LEGACY_EXTRACT=true):              │
       │       │    EntityExtractor (one LLM call per file)      │
       │       │                                                 │
       │       │  Reachability filter: drop entities not on the  │
       │       │    entry endpoint's call graph (BFS over        │
       │       │    structural edges)                            │
       │       └────────────┬───────────────────────────────────┘
       │                    ▼
       │       ┌────────────────────────────────────────────────┐
       │       │  STAGE 1.5 — Intent synthesis (often skipped)  │
       │       │  Auto-skipped when chunked path filled context │
       │       └────────────┬───────────────────────────────────┘
       │                    ▼
       │       ┌────────────────────────────────────────────────┐
       │       │  STAGE 2 — Relationship extraction              │
       │       │  RelationshipExtractor                          │
       │       │    → 50-edge taxonomy (CALLS, READS_COLUMN,…)   │
       │       │  + Structural edge pre-extraction (no LLM):    │
       │       │      CONTAINS, EXTENDS, IMPLEMENTS, INSTANTIATES│
       │       │  + JPA SQL extractor (regex over @Query)        │
       │       │  Dedup: highest-confidence edge per triple      │
       │       └────────────┬───────────────────────────────────┘
       │                    ▼
       │       ┌────────────────────────────────────────────────┐
       │       │  STAGE 3 — Context synthesis                    │
       │       │  ContextSynthesizer (batched, N entities/call)  │
       │       │    → 21-field BusinessContext per entity:       │
       │       │       purpose, change_risk, data_sensitivity,   │
       │       │       invariants[], side_effects[],             │
       │       │       failure_modes[], owners, …                │
       │       └────────────┬───────────────────────────────────┘
       │                    ▼
       │       ┌────────────────────────────────────────────────┐
       │       │  STAGE 4 — Gap detection (often skipped)        │
       │       │  Auto-skipped when chunked path filled context │
       │       └────────────┬───────────────────────────────────┘
       │                    ▼
       │       ┌────────────────────────────────────────────────┐
       │       │  Storage — write to all sinks in parallel       │
       │       │  ├─ Postgres   (nodes, edges, node_context)     │
       │       │  ├─ Neo4j      (graph for relation traversal)   │
       │       │  ├─ Qdrant     (embeddings for semantic search) │
       │       │  └─ JSON brain (per-entity files for /query)    │
       │       └────────────┬───────────────────────────────────┘
       │                    ▼
       └────────────▶  Job complete; result POSTed to Java backend
                       which surfaces it via /v1/pipeline/jobs/{id}
```

**Cost per run today:** $0.30–0.50 (mostly the navigator's 25-turn ReAct
loop + per-method chunk extractor; ADR-0048/0049/0050 cut this to $0.03).

**Wall time today:** ~5 minutes for a 60-method endpoint.

---

## The query pipeline

```
   POST /query                               POST /query/stream
   { question, workspace_id, repo_path }     (same payload, SSE response)
        │
        ▼
┌──────────────────────────────────────────────────────────────────────────┐
│ QueryEngine                                                              │
│                                                                           │
│ 1. SmartZoneAssembler — builds tiered context                            │
│    ├─ T0 (must-include) — query-classifier picks the most relevant       │
│    │     entities by signature/name match (BM25 + structural filter)     │
│    ├─ T1 (summaries)    — t1_summary fields of related entities          │
│    └─ T2 (full context) — code_snippet + query_text + 21-field context   │
│         for the top-K entities; capped at BRAIN_TOKEN_BUDGET (~4000)     │
│                                                                           │
│ 2. HybridSearcher — BM25 + Qdrant vector search to surface candidates    │
│                                                                           │
│ 3. JsonFileBrainStore — reads <repo>/.brain/component/*.json for         │
│    full entity payloads (the JSON brain is the primary read store)       │
│                                                                           │
│ 4. Postgres edge traversal — finds callers/callees/dependencies          │
│                                                                           │
│ 5. Neo4j graph traversal — for "what depends on X" type questions        │
│                                                                           │
│ 6. Build a structured prompt with T0+T1+T2 context blocks and a          │
│    JSON-output schema (call_chain, sql_quotes, affected_entities,        │
│    change_risk, confidence, follow_up_questions)                          │
│                                                                           │
│ 7. Send to Sonnet-4.6 (more capable model for the answer)                │
│                                                                           │
│ 8. Return structured QueryResponse (parsed, not free-text)               │
└──────────────────────────────────────────────────────────────────────────┘
```

**Cost per query:** ~$0.01–0.03 (Sonnet input + output).

---

## User flows

### 1. Extract an endpoint (the canonical demo flow)

```bash
make -f Makefile.demo doctor       # check prereqs (docker, java, node, .env)
make -f Makefile.demo guard        # set cost guards in .env
make -f Makefile.demo up-all       # docker compose up infra (PG, Neo4j, Qdrant, Redis, LocalStack)
make -f Makefile.demo health       # verify infra responding

# In separate terminals:
make backend                        # Spring Boot Java :8080
make ai                             # Python FastAPI :8000
make -f Makefile.demo cb-api        # Bun TypeScript :8090
make frontend                       # Vite :5173

# Find an endpoint:
make -f Makefile.demo discover      # greps @*Mapping annotations in target repo

# Run extraction:
make -f Makefile.demo run-cli ENDPOINT=/competitiveness/summary/competitors/payer METHOD=POST

# Polls Java /v1/pipeline/jobs/{id} every 2s until status=completed
# Job completes; brain populated in PG + Neo4j + Qdrant + .brain/
```

What happens internally:

1. `curl POST localhost:8080/v1/pipeline/start` → Java `PipelineController.start()`
2. Java creates a `pipeline_jobs` row in Postgres, assigns a UUID
3. Java forwards `{endpoint, method, repos}` to Python `localhost:8000/pipeline/run`
4. Python `orchestrator.run_pipeline()` runs all 7 stages
5. Python POSTs progress events to Java `/v1/internal/pipeline-progress` after each stage
6. Python POSTs final result to Java `/v1/internal/pipeline-result`
7. User polls Java `/v1/pipeline/jobs/{id}` → Java returns the cached result

### 2. Ask a question

```bash
make -f Makefile.demo ask Q="what tables and columns does getPayerCompetitors read, and what would break if I rename the lob column?"
```

What happens:

1. `curl POST localhost:8000/query` (skips Java; goes straight to Python)
2. `QueryEngine.answer()`:
   - Build T0/T1/T2 zone via `SmartZoneAssembler`
   - Run hybrid search to surface top-10 candidate entities
   - Read full entity payloads from `<repo>/.brain/component/*.json`
   - Traverse Postgres edges for callers/callees
   - Compose Sonnet prompt with structured JSON schema
3. Sonnet returns structured `QueryResponse`
4. Renderer formats as Markdown for terminal display

### 3. Re-run the same endpoint (warm rerun, today)

Today this re-runs the entire pipeline from scratch. There's an L2
cache (`<repo>/.brain/.l2-cache/main.json`) that stores per-file hashes
so the structural pre-pass (Stage 0.5) can skip unchanged files, but
the chunk extraction queue does NOT yet dedup across runs (ADR-0049 C4
fixes this).

### 4. Compare CLI vs UI runs

```bash
make -f Makefile.demo run-ui ENDPOINT=...   # opens browser; user does same extract via UI
make -f Makefile.demo compare               # diffs Postgres node counts between CLI and UI jobs
```

### 5. Wipe and start fresh

```bash
make -f Makefile.demo wipe                  # truncates PG tables, deletes Neo4j workspace nodes,
                                            # FLUSHDB Redis, deletes Qdrant collections
rm -rf <target_repo>/.brain                 # clears JSON brain
```

### 6. Check spend

```bash
make -f Makefile.demo cost                  # sums cost_usd from edge_events.metadata
```

---

## What works well today

- **End-to-end extraction is functional** — controller → service → repository → SQL chain, all extracted into the brain.
- **Query path returns structured answers** with cited entities, SQL quotes, and affected-entity lists.
- **Chunked extraction** (per-method LLM calls instead of per-file) prevents truncation on big classes.
- **Reachability filter** drops unrelated drift (~48% of raw entities were drift before this).
- **Resume from checkpoints** — long-running pipelines can be resumed if killed mid-run.
- **6 storage layers** all populated correctly (PG, Neo4j, Qdrant, Redis, S3, JSON brain).
- **Multi-provider** — Anthropic / OpenAI / Ollama / Groq / OpenRouter all wired via `LLM_PROVIDER`.

## What's broken / fragile today (the why-the-ADRs-exist list)

- **Cost** — $0.30–0.50 per run; the navigator's 25-turn ReAct loop is the biggest hot-spot (ADR-0048).
- **No prompt caching live** — `cache_creation=0 cache_read=0` on every call; the cache_control hint is dropped (ADR-0049 C1 fixes — the keystone).
- **Bad endpoint = silent garbage** — until the fix shipped today, mistyping the endpoint resulted in 18 useless entries from `StaticDataController` + HTML generators. Now hard-fails with a route list.
- **Big classes truncate** — yesterday's run lost an entire `CompetitivenessRepositoryImpl` extraction to `max_tokens` mid-string. Char-by-char JSON recovery shipped; ADR-0050 makes it bulletproof.
- **Linear stage machine** — every new feature is another `if/else` in `orchestrator.py` (~2000 LOC). ADR-0051 replaces this with an agentic harness.
- **No skills system** — Spring Boot expertise is hard-coded in `_trace_java`; FastAPI in `_trace_python`. Adding a framework = 2-week project. ADR-0051 P3 fixes.
- **No persistent agent memory** — each run re-discovers the same things. ADR-0051 P3 adds `.brain/BRAIN.md`.
- **No IDE integration** — devs ask the brain via `make ask Q=...`, not from inside their editor. ADR-0052 P7 ships VS Code extension.
- **No marketplace / plugins** — orgs can't share custom framework skills. ADR-0052 P6.

## ADR shipping status (verified by grepping the code, May 10 2026)

| ADR | Status | Evidence |
|---|---|---|
| ADR-0048 (two-agent extraction) | ✅ **SHIPPED** | `agents/specialist_agent.py` (161 LOC) + `agents/context_agent.py` (244 LOC), wired into entity_extractor / code_tracer / manifest_filter / worker / orchestrator |
| ADR-0049 (caching + format) | ✅ **MOSTLY SHIPPED** | `llm/anthropic_provider.py` has `cache_control:ephemeral` (L106, L187) + httpx connection pool (L57); `pipeline/queue.py` has `source_job_id` cross-job dedup (L44, L126); `retrieval/qdrant_writer.py` has `version_hash` skip (L48); `util/file_cache.py` + `util/ast_cache.py` exist. Remaining: SSE streaming for /query, full XML output. |
| ADR-0050 (big-repo recovery) | ✅ **SHIPPED** | `pipeline/extraction_recovery.py` (M2 bisection), `pipeline/region_splitter.py` (M3b), `pipeline/batch_planner.py` (M1), `collectors/manifest_filter.py` (M4), `util/xml_partial_parser.py` (replaces char-by-char scanner) |
| ADR-0051 (agentic harness) | ❌ **NOT SHIPPED** | No `harness/` directory in `companybrain/`; orchestrator is still the linear stage machine; sub-agents are via `worker.drain_queue` + `extraction_recovery`, not via a Task tool |
| ADR-0052 (harness extensions) | ⚠ **PARTIALLY SHIPPED** | MCP server lives at `companybrain/mcp/server.py` (784 LOC) exposing 10 tools — but it's not the "brain-as-MCP for IDE integration" shape ADR-0052 proposes; it's a separate backend for the Java + cb-api services. VS Code extension exists at `company-brain-frontend/vscode-extension/` but talks directly to Java :8080 + Python :8000, NOT through the MCP server. The skills/marketplace/scheduled/notebook/image/notes pieces are all unshipped. |

**The user-facing surface of "Claude-Code-equivalent harness" is the
Phase 1–4 work in ADR-0051 plus the surface refactors in ADR-0052 P5
(point the existing VS Code extension at the existing MCP server +
add slash commands).** Everything underneath is already in place.

Implementation prompts for the remaining work are in
`docs/adrs/SONNET-IMPLEMENTATION-PROMPT-ADR-*.md`. Run order in
`docs/adrs/IMPLEMENTATION-ORDER.md`.

---

## File-system layout

```
company-brain/
├── company-brain-backend/        Spring Boot Java service (:8080)
│   └── src/main/java/com/companybrain/
│       ├── CompanyBrainApplication.java
│       └── controller/
│           ├── PipelineController.java     ← /v1/pipeline/start, /jobs/{id}
│           ├── IngestController.java        ← receives results from Python
│           ├── ArchitectureController.java
│           └── GraphController.java
│
├── company-brain-ai/             Python FastAPI service (:8000) — THE PIPELINE
│   ├── pyproject.toml
│   └── src/companybrain/
│       ├── api/                  FastAPI routes
│       │   └── routes/
│       │       ├── pipeline.py   /pipeline/run, /jobs/{id}
│       │       ├── query.py      /query, /query/stream
│       │       ├── feedback.py   /resynthesise
│       │       ├── repo.py       /repo/branches
│       │       └── health.py
│       ├── pipeline/             7-stage orchestrator + workers
│       │   ├── orchestrator.py   ← the central nervous system (~2000 LOC)
│       │   ├── code_chunker.py   tree-sitter splitting
│       │   ├── chunk_extractor.py
│       │   ├── chunk_relevance_filter.py
│       │   ├── chunk_batcher.py
│       │   ├── entity_extractor.py
│       │   ├── relationship_extractor.py
│       │   ├── context_synthesizer.py
│       │   ├── reachability_filter.py
│       │   ├── structural_prepass.py  → cb-api
│       │   ├── _dedup.py
│       │   ├── queue.py          (extraction_queue Postgres ops)
│       │   ├── worker.py         (drain_queue)
│       │   └── merger.py         (merges chunk results into entities)
│       ├── agents/
│       │   ├── navigator_agent.py            (import-graph, fallback)
│       │   ├── knowledge_navigator_agent.py  (LLM ReAct loop, primary)
│       │   ├── context_manager_agent.py
│       │   └── tools/code_tools.py           (find_class, extract_method, etc.)
│       ├── collectors/
│       │   ├── code_tracer.py    Stage 0a entry point
│       │   ├── llm_handler_finder.py
│       │   └── git_collector.py  Stage 0b
│       ├── retrieval/
│       │   ├── hybrid_search.py  BM25 + Qdrant
│       │   ├── qdrant_store.py
│       │   ├── qdrant_writer.py
│       │   └── json_file_brain_store.py  (.brain/ reader)
│       ├── assembly/
│       │   └── smart_zone.py     T0/T1/T2 tiered context
│       ├── providers/            Anthropic / OpenAI / Ollama / Groq / OpenRouter
│       ├── models/entities.py    ExtractedEntity, ExtractedRelationship, …
│       ├── config.py             Pydantic settings (read .env)
│       └── cli.py
│
├── company-brain-frontend/       Vite + React UI (:5173)
│   ├── src/                      API explorer, query UI
│   └── vscode-extension/         (P7 placeholder)
│
├── apps/                         Bun TypeScript services
│   ├── api/                      cb-api :8090 (structural extractor)
│   │   └── src/                  framework-jpa, framework-prisma, etc.
│   ├── extractor-worker/         background queue drainer (optional)
│   └── cli/                      `cb extract` standalone CLI
│
├── packages/                     shared TypeScript packages
│   ├── tools/                    (LLM tool defs)
│   ├── memory/
│   ├── graph/                    Neo4j client
│   ├── drift-detector/
│   ├── schema/
│   ├── repo-map/
│   └── extractors/               (per-framework extractors used by cb-api)
│
├── docker-compose.infra.yml      PG, Neo4j, Qdrant, Redis, LocalStack, Langfuse, Ollama
├── Makefile                      core targets
├── Makefile.demo                 end-to-end demo flow
└── docs/
    ├── ARCHITECTURE.md           ← THIS FILE
    ├── adrs/
    │   ├── ADR-001..ADR-0047    historical
    │   ├── ADR-0048..ADR-0052   proposed (cost cuts + harness migration)
    │   ├── SONNET-IMPLEMENTATION-PROMPT-ADR-*.md   one per session
    │   └── IMPLEMENTATION-ORDER.md   dependency graph
    └── HARNESS.md                (created by ADR-0051 P1)
```

---

## Next time you read this

If anything in this doc feels out of date, the answer is one of:

1. The relevant ADR (0048-0052) shipped — re-derive from the current code state.
2. We added a new stage / agent / store after this was written.
3. The doc was wrong — open a PR fixing it.

Don't trust this doc more than the code. Trust the code more than the
ADRs. Trust the ADRs more than your memory.
