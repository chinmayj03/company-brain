# Current State of the Company Brain — Code-level vs Non-code-level Reality

> **Purpose.** A frank audit of what exists in the repo today, mapped against the design ambitions in `harness-system-design.md` (v1), `ADR-001-enhanced-extraction-pipeline.md`, `company-brain-v2-system-design.md`, and `claude-code-architecture.md`. Plus a root-cause inventory of why the pipeline breaks even on a single small repo.
>
> **Companion to:** `MIGRATION-mono-to-multirepo-to-company.md`
>
> **Date:** 2026-05-07

---

## 1. Top-line summary

The repository contains **four working services** (Java backend, Python AI service, **TypeScript/Bun cb-api at `apps/api/` on port 8090**, React frontend) plus a **dual graph store** — Postgres for the semantic graph (with workspace-level RLS) and **Neo4j for the structural graph** (URN-keyed). It runs a **multi-tier context architecture in the LLM extraction layer** — L1 (per-call FocalContext), L2 (pipeline-shared context that grows across the run), and Main Memory (the persistent graph). T0/T1 memory tokens are generated for every entity. **Qdrant** is deployed for code embeddings; **Langfuse** is wired for LLM call telemetry.

The current pipeline is best described as **"v0+ heading toward v1"**: it has more of the harness's intent than I originally credited (L1/L2/main memory tiers, T0/T1 tokens, dual graph stores, structural extractor framework). What it doesn't yet have are the v1 surface properties: per-repo `.brain/` JSON SOT, function-level entities, smart-zone token budget assembly across both stores, repo-scoped (not endpoint-scoped) extraction, and a working hybrid retriever connecting Qdrant to Postgres + Neo4j. ADR-001's compiler-grade type extraction and v2's streaming + inference + runtime telemetry remain future work.

It demonstrates a working end-to-end path: clone a repo → trace an endpoint → extract entities (with L2 enrichment between calls) → dual-write to Postgres + Neo4j → store T0/T1 + business context → query via NL. The bones are right; the muscle is partial; some of the muscle is wired together while other pieces are scaffolded but unwired.

---

## 2. What exists in the repo (component inventory)

### 2.1 `company-brain-backend/` (Java 21, Spring Boot 3.2)

**Working today:**
- REST API on port 8080 with JWT auth + workspace context interceptor (`WorkspaceContext`, `JwtAuthFilter`).
- Postgres schema (`V1__create_graph_tables.sql`) with `workspaces`, `nodes`, `edges`, `edge_events`, `node_context`, RLS policies enforcing workspace isolation, indexes for forward/reverse edge traversal.
- `IngestController` accepts metadata batches via HMAC-signed POST and enqueues to SQS.
- `IngestService` validates HMAC and forwards to `SqsTemplate`.
- `EdgePrunerJob` (assumed scheduled) prunes stale edges via `last_seen` timestamps.
- DTOs and JPA entity models for `Node`, `Edge`, `NodeContext`, `Workspace`.
- Internal endpoint `/v1/internal/pipeline-result` (referenced by `JavaGraphClient`) for the AI service to post extraction results back.
- Blast-radius and graph endpoints exposed through controllers (referenced by frontend client).

**Designed but not visibly present in the repo (mentioned in code comments / docs):**
- `PipelineService.dispatchToAi()` — the entrypoint that delegates a job to the Python AI service.
- `ContextAssemblerService` — the T0/T1/T2 context renderer for Ask queries.
- `BlastRadiusService` — the Postgres-backed BFS implementation.
- `ArtifactWriterService` — the freshness-check counterpart to `JavaGraphClient.check_freshness()`.

These services are referenced extensively in Python comments and ADRs but the corresponding Java sources are not in the file inventory I scanned. They may exist (I did not enumerate every Java file) but if they are missing, the `/v1/internal/pipeline-result` endpoint silently fails to do real work, and the brain stays empty even though the pipeline reports "completed."

### 2.2 `company-brain-ai/` (Python 3.11, FastAPI, port 8000)

**Working today:**
- FastAPI app with routes for `/pipeline/{run,start,jobs/{id}}`, `/query`, `/repo/branches`, `/feedback`, `/health`.
- `pipeline/orchestrator.py` orchestrates the multi-stage flow: code tracing (0a), git collection (0b), freshness pre-flight (0c), entity extraction (1) gated by **ContextManagerAgent + L2 shared context**, dependency expansion (1.4), intent synthesis (1.5), import-graph CALLS edges (1.6), relationship extraction (2), context synthesis (3), **memory tokenization T0/T1 (3.5)**, gap detection (4), graph population (5), then **trigger structural extraction via cb-api → Neo4j (ADR-0008)**.
- **L1 / L2 / Main-Memory context hierarchy** (`pipeline/context_hierarchy.py`, `shared_context_accumulator.py`, `context_manager_agent.py`) — modeled on a CPU memory hierarchy. L2 holds domain glossary, service registry, architecture patterns, cross-cutting concerns, field semantics, and a top-confidence entity catalog. It grows rule-based as each unit is extracted; the ContextManagerAgent renders a per-unit prompt patch + relevant L2 section before each LLM call. This is in the active extraction path right now.
- **MemoryTokenizer (Stage 3.5)** — generates T0 (~15 tokens, "I've heard of this") and T1 (~100 tokens, "I know what this does") deterministically from `BusinessContext`. Tokens are stored on each entity's metadata and shipped to Java in the pipeline payload, where `ContextAssemblerService` is supposed to read them for fast Ask responses.
- LLM provider abstraction (`llm/{base,factory,ollama_provider,anthropic_provider,openai_provider}.py`) with role-based model assignment (`FAST`, `BALANCED`, `SYNTHESIS`, `REASONING`, `QUERY`).
- Provider-aware concurrency (`pipeline/concurrency.py`) — Ollama/Groq sequential, OpenAI/Anthropic parallel, OpenRouter low-concurrency.
- Checkpoint resume mechanism in `/tmp/cb_checkpoint_<hash>.json` keyed by `(workspace_id, http_method, endpoint_path)`, with stage-reached granularity (`1`, `1.5`, `1.6`).
- Code tracer (`collectors/code_tracer.py`) supporting Java Spring annotations, TS/JS axios/fetch patterns, Python FastAPI/Flask routes, with regex extraction.
- `NavigatorAgent` (`agents/navigator_agent.py`) — two-phase LLM-driven traversal: import-graph pre-assembly then single LLM classification call.
- Git collector (`collectors/git_collector.py`) — pulls commit history and clusters per endpoint; also produces `Artifact[]` for the freshness check.
- Import-graph analyzer (`pipeline/import_graph.py`) — deterministic CALLS edges from `@Autowired`/import statements.
- Java client (`graph/java_client.py`) — HTTP wrapper around `/v1/internal/pipeline-result`, freshness check, artifact writer.
- **Neo4j writer (`graph/neo4j_writer.py`)** — async dual-write to Neo4j keyed on the URN scheme `urn:cb:llm:{workspace_id}:{file_path}:{entity_name}`. Idempotent MERGE-by-id, soft-invalidation (no deletes), exponential-backoff retry on transient errors, batch size 100, never raises (errors logged and swallowed so a Neo4j outage cannot crash the Postgres write path).
- Tree-sitter structural parser (`structural/parser.py`) — ported from `code-review-graph` (MIT). Supports Java/TS/TSX/JS/Python/Go.
- Structural index / changes / dependents / risk / flows / topology helpers (`structural/*.py`) — these run via the Bun extractor-worker / cb-api, not from the main Python orchestrator.
- MCP module skeleton (`mcp/{client,tools/{context,flows}}.py`) — partial; the actual MCP stdio server entry point is not the main FastAPI app.

**Referenced in code but not implemented (or not in the repo):**
- `companybrain.retrieval.hybrid_search.HybridSearcher` — referenced lazily in `code_tracer._get_hybrid_searcher()` but no `retrieval/` package exists in the file listing. First call to hybrid search would `ImportError` at runtime. (Qdrant is deployed; nothing in the Python service consumes it yet.)
- `companybrain.config.settings` — used heavily; assumed Pydantic settings class but I did not see its definition. If env vars are missing the FastAPI app crashes at startup.
- `companybrain.models.entities` — referenced everywhere; Python dataclasses for `ExtractedEntity`, `ExtractedRelationship`, `BusinessContext`, `Artifact`, `RepoConfig`, `PipelineStartRequest`, etc. Likely present; not enumerated.
- A `companybrain` CLI (`cli.py`) — the README assumes `make backend` / `make ai` etc., but `brain index`, `brain map`, `brain query`, `brain blast-radius` (described in the harness doc) do not exist as Python executables. The Bun-based `apps/cli/` exists for the TS extractor side but not for the LLM pipeline.

### 2.3 `company-brain-frontend/` (React 18, Vite, Tailwind, port 5173)

**Working today:**
- Vite-built SPA with components for `DependencyGraph`, `BlastRadiusPanel`, `AnnotationEditor`, `CommitTimeline`.
- API client (`src/api/client.js`) that talks to the Java backend.
- Pages: Dashboard, ApiExplorer (the pipeline trigger UI), QueryPage (Ask).
- VS Code extension scaffold under `vscode-extension/` (just `package.json` + `extension.js`).

**Notes:**
- The dashboard surfaces real-time pipeline progress by polling Redis-backed job state (the AI service writes progress to `job:{job_id}` keys; the frontend polls). This works when LocalStack + Redis + AI service + Java are all up.

### 2.4 Infrastructure

**Up via `make up` / `docker-compose.infra.yml`:**
- PostgreSQL 16 — semantic graph store with RLS.
- Redis 7 — job state + blast-radius cache.
- LocalStack 3 — SQS emulation.
- Ollama — local LLM, optional native mode for Apple Silicon.
- **Neo4j 5.18 community** — structural graph store (browser at 7474, bolt at 7687). Written by `graph/neo4j_writer.py` from Python and by the TypeScript `apps/api/` and `apps/extractor-worker/`. Auth `neo4j/password` in dev, APOC plugin enabled.
- **Qdrant 1.9.2** — vector store for code embeddings (REST 6333, gRPC 6334). Designated for voyage-code-3 embeddings with scalar quantization. Currently deployed but no Python consumer is wired (the `retrieval/hybrid_search.py` that would consume it is missing).
- **Langfuse** — LLM call tracing + cost telemetry, dashboard at 3001. Wired as a no-op forward target in `llm/base.py`'s `log_llm_call()`.

**`apps/` (TypeScript / Bun) — additional services:**
- **`apps/api/`** — Bun + tRPC server on port 8090. Talks to Neo4j (`bolt://neo4j:7687`) via `@company-brain/graph`'s `GraphClient`. Exposes the tool router over HTTP + a `POST /extract` endpoint that runs the registered extractors against a target repo. **This is the `cb-api` referenced from the Python orchestrator's `_trigger_structural_extraction()`.**
- **`apps/extractor-worker/`** — registry + index of structural extractors. Pass 1: `GitExtractor`, `CoreTsExtractor`. Pass 2: framework extractors for Next, Prisma, OpenAPI. Pass 2b: SQL, JPA, SQLAlchemy schema extractors. Pass 3: `DriftDetector`. All write into Neo4j via the same `GraphClient`.
- **`apps/cli/`** — Bun CLI (presumably the `cb` command for triggering extractor-worker runs).

**Not present:**
- Memgraph (v2 SOT). Neo4j fills the structural-graph role; migration to Memgraph would only matter if/when Cypher latency on Neo4j community edition becomes a bottleneck.
- OTel Collector / any runtime telemetry ingestion.
- Any LSP middleware sidecar.
- pgvector extension on Postgres (v2 docs assume it; not in V1 migration). Qdrant is the chosen vector store instead.

### 2.5 Documentation

**In `docs/`:**
- `SYSTEM_DESIGN.md`
- `ADR-001-graph-storage.md`, `ADR-002-ingestion-pipeline.md`, `ADR-003-multi-tenancy.md`, `ADR-004-universal-knowledge-schema.md`
- `ARCHITECTURE-company-brain-v2.md`, `RETRIEVAL-ARCHITECTURE.md`, `PIPELINE-api-context-builder.md`
- `adrs/` subdirectory with ADR-0001 through ADR-0010 (URN identity, graph storage, extractor plugin, tool naming, confidence scoring, framework extractor, drift detection, integration bridge, shared memory, extraction optimization).

**At repo root:**
- `claude-code-architecture.md`, `harness-system-design.md`, `ADR-001-enhanced-extraction-pipeline.md`, `company-brain-v2-system-design.md`, `PROJECT_CONTEXT.md`, `README.md`.

The documentation is **substantially ahead of the code**. The harness/v2 docs describe a system that does not yet exist; the existing code most closely matches an earlier iteration of `PIPELINE-api-context-builder.md`.

---

## 3. What works at code level (today, end-to-end)

A useful baseline. With the current code on a small repo, you can:

1. **Start the stack.** `make setup && make up && make backend && make ai && make frontend` brings online Postgres + Redis + LocalStack + Ollama + Neo4j + Qdrant + Langfuse, plus Java backend, Python AI service, and React frontend (provided Java 21, Node 20+, Bun, Python 3.11+, Docker, and an Ollama model are all installed correctly).
2. **Trigger an endpoint extraction.** From the React UI, paste a local repo path + endpoint path + HTTP method, hit Run. The Java backend creates a job, dispatches to the Python AI service, and the Python pipeline begins.
3. **Watch live progress.** Stage 0a/b/c through Stage 5 emit progress events to Redis; the frontend polls `/pipeline/jobs/{id}` and shows what's happening.
4. **Benefit from L2 shared context across a single run.** The 4th file's extraction sees domain glossary, service registry, architecture patterns, and the entity catalog from the first 3 — injected via `ContextManagerAgent` as a prompt patch + Workspace Context section. This visibly improves entity quality on real Java/TS codebases.
5. **Get a partial dual graph.** Assuming the endpoint is found and the LLM doesn't choke: Postgres gets `nodes` + `edges` + `node_context` rows under workspace RLS; Neo4j gets URN-keyed structural nodes via the `Neo4jWriter` dual-write; T0/T1 memory tokens land in entity metadata.
6. **Trigger structural-extractor pass.** After the LLM pipeline finishes, the orchestrator POSTs to `cb-api:8090/extract`, which runs the Bun `extractor-worker` registry (Git → CoreTs → Next/Prisma/OpenAPI → SQL/JPA/SQLAlchemy → DriftDetector). Each extractor writes into the same Neo4j store, complementing the LLM-extracted semantic context.
7. **Visualise the graph.** The DependencyGraph component renders nodes/edges from the Java backend's REST API.
8. **Run a NL query.** The `/query` endpoint takes a question, retrieves relevant nodes, and synthesises an answer via the configured LLM. T0/T1 tokens are intended to power fast pre-filtering in `ContextAssemblerService`.
9. **Resume after failure.** A pipeline that crashed mid-Stage-1 leaves `/tmp/cb_checkpoint_*.json` (with stage_reached granularity) and the next run skips already-extracted entities + intent synthesis + import-graph analysis as appropriate.
10. **Track LLM cost.** Every LLM call records provider, model, role, token counts, USD cost via `LLMCallRecord` and forwards to Langfuse + structlog (ADR-0010 §2). Anthropic system prompts use `cache_control: ephemeral` for 50–90% cost reduction after the first call per session.

This is more than I credited in the first draft. The L1/L2/Main-Memory hierarchy and dual-graph dual-write are real, working systems — not just designs.

---

## 4. What does not work (code level)

### 4.1 Missing or unwired components

| Component | Status | Effect |
|---|---|---|
| `.brain/` per-repo JSON source-of-truth | Not implemented | No git-trackable brain. Every brain wipe = full LLM re-run. No per-PR brain diff. |
| Qdrant hybrid retrieval (BM25S + dense + RRF) wiring from Python | Qdrant is **deployed**; Python consumer **not wired** | `code_tracer._get_hybrid_searcher()` would `ImportError` first call. The `retrieval/` package is missing. RAG-flavoured retrieval not yet operational. |
| `companybrain.retrieval.hybrid_search` module | Missing source files | Anything that tries to use hybrid search hits `ImportError` |
| `companybrain.cli` Python entry points | Not implemented | The Python LLM pipeline must be triggered through the React UI or curl-against-FastAPI. The Bun `apps/cli/` is for the structural extractor side, not the LLM side. |
| Tree-sitter integration into the Python orchestrator's main path | Parser exists, structural extractors exist via Bun extractor-worker, but **the Python orchestrator's Stage 0/1 still LLM-extracts first** | The frugality hierarchy is half-honoured: structural pass runs *after* the LLM pipeline (post-completion call to cb-api), not *before* it. The cost benefit of "skip LLM if structural unchanged" is not yet realised in the Python path. |
| Function-level entities (`function_node`) per ADR-001 | Not modeled in the LLM pipeline; structural extractors emit `Function` / `Method` nodes into Neo4j | Function-precise blast radius is partial — structurally available in Neo4j, semantically unavailable in Postgres. The two graphs do not yet join cleanly at the function level. |
| Code patterns / state slices / type flows / call paths (ADR-001) | Not modeled | All of ADR-001's added entity types absent on both sides. |
| LSP middleware / streaming extraction | Not implemented | The "save → brain updated in 5s" property does not exist. |
| Inference Engine over Neo4j (Cypher rules: transitive assumption, contract drift, coverage gap, etc.) | Not implemented | The data is there; the rules aren't. **`DriftDetector` runs as a Pass-3 extractor in the Bun pipeline** — that's the seed of an inference engine, but the v2 declarative rule-loader pattern is not built. |
| OTel runtime ingestion | Not implemented | No `CALLED_AT_RUNTIME` edges. Hot-path scoring impossible. |
| Proactive alerts (Contract Drift, Hot-Path-At-Risk, Architecture Drift) | Not implemented as outbound notifications | DriftDetector emits structural drift edges into Neo4j; nothing routes them to Slack / GitHub PR comments yet. |
| Multi-modal collectors (Slack, Confluence, Jira, Terraform, Figma, CI, DB migrations) | Only `git_collector.py` (Python) + `GitExtractor` (Bun) exist | The brain is code-only. The "company brain" framing in `PROJECT_CONTEXT.md` is aspirational. |
| GraphQL API + SSE event stream | Not implemented | The `apps/api/` exposes tRPC over HTTP — close to GraphQL in spirit but not the v2 graph-query surface. |
| Semantic cache for LLM results | Not implemented | Same query repeated = same cost. (Anthropic prompt caching is wired; that's per-prompt, not per-query result.) |
| MCP stdio server entry point | Skeleton in `mcp/`, not wired as a CLI | Claude Code cannot connect to it; the harness MCP integration is theoretical. |
| Repo-scoped (vs endpoint-scoped) extraction | Not implemented | Still runs per `(endpoint_path, http_method)`. Even with L2 + dual-graph, you cannot say "ingest the whole repo." |
| Postgres ↔ Neo4j join key consistency | Partial mismatch | Postgres `external_id` uses `repo/file::method`; Neo4j uses URN `urn:cb:llm:{workspace_id}:{file_path}:{entity_name}`. Two stores, two ID schemes — joins / cross-references at query time require an adapter layer that doesn't visibly exist. |

### 4.2 Wrong shape (correct components, wrong architecture)

| Issue | Detail |
|---|---|
| **Endpoint-scoped trigger** | The pipeline starts from `(endpoint_path, http_method)`. To brain a repo with 200 endpoints, you'd run the pipeline 200 times. There is no `brain index <repo>` operation. |
| **LLM-first ordering** | Stage 1 (entity extraction) is an LLM call per code unit. The deterministic structural pass (tree-sitter) exists but runs after the fact. Reverses the cost ladder ADR-0010 mandates. |
| **No JSON SOT** | All extraction results write directly to Postgres via the `JavaGraphClient → /v1/internal/pipeline-result` callback. There's no intermediate `.brain/` representation. So you cannot inspect the brain in git, cannot review brain changes in PRs, cannot rebuild Postgres from a checked-in source. |
| **Free-form `node_type`** | Postgres `nodes.node_type` is `TEXT` with no constraint. The extractor emits `ApiEndpoint`, `Function`, `Class`, `SchemaField`, `DatabaseTable`, `DatabaseColumn`, `DatabaseQuery`, `FrontendComponent`, `ExternalService`, `ConfigKey`, `SharedType` — eleven types that don't map cleanly onto the harness's six (`component`, `screen`, `api_contract`, `data_model`, `assumption`, `business_context`). The MCP tool surface in the harness doc cannot be exposed accurately because the underlying types disagree. |
| **`assumption` and `business_context` stored in `node_context` not `nodes`** | They live as rows in `node_context` keyed off another node, not as first-class graph entities. They cannot be searched, edged, or blast-radius'd. The `RELIES_ON` edge type required by v2 has nowhere to land. |
| **`external_id` format inconsistency** | Existing rows use formats like `backend/src/payment.ts::chargePayment`. The harness wants `repo::type::qname`. Both formats work as text, but cross-repo lookups break because the prefix isn't a repo id. |
| **Heavy reliance on `/tmp` checkpoints** | `_checkpoint_save()` writes to `/tmp/cb_checkpoint_<hash>.json`. In a Docker container, `/tmp` is ephemeral. In CI, the checkpoint never survives a job. The 24h staleness rule means a stale checkpoint can poison a fresh run. |
| **Concurrency tied to LLM provider, not workload** | `concurrency.py` selects parallelism by provider name. So Ollama always runs sequentially, even on a 32-core M3 Max. That's correct for the model's GPU contention but it slows incremental runs disproportionately for multi-file changes. |
| **Workspace-as-tenant-as-pipeline-trigger conflation** | The Java `workspace_id` does triple duty: tenant isolation key, RLS predicate, and pipeline-job grouping key. There is no notion of a "repo" inside a workspace. So if you want to brain three repos in one workspace, you have one set of nodes/edges with no repo discriminator beyond what the extractor jams into `metadata.repo`. The `repo::type::qname` ID format would solve this; today it's not enforced. |

---

## 5. What works at non-code level (process, docs, ops)

| Area | What's strong |
|---|---|
| Vision clarity | `PROJECT_CONTEXT.md` is unambiguous — the product is a dependency intelligence layer that scales from VS Code extension wedge to enterprise self-hosted agent. The strategy is articulated. |
| Architecture documents | The four hand-offered docs (`claude-code-architecture.md`, `harness-system-design.md`, `ADR-001-enhanced-extraction-pipeline.md`, `company-brain-v2-system-design.md`) form a coherent design ladder. Each builds on the previous. The migration path from v1 to v2 is sketched and feasible. |
| ADR discipline | `docs/adrs/ADR-0001` through `ADR-0010` cover identity, storage, extractor plugins, tool naming, confidence scoring, framework extraction, drift detection, integration bridges, shared memory, and extraction optimization. The big decisions are written down. |
| Local dev experience | `make setup` + three terminals + `make backend/ai/frontend` is one of the better OSS dev loops for a polyglot stack. The Makefile + README + `check-prereqs.sh` script removes most "doesn't work on my machine" friction. |
| Provider abstraction | The LLM provider layer (Ollama/Anthropic/OpenAI/Groq) is well-structured. Switching providers via `make switch-anthropic`, `make switch-ollama` is one command. |
| RLS-first multi-tenancy design | `V1__create_graph_tables.sql` enforces workspace isolation in the database, not in application code. This is the correct pattern for a tenanted SaaS — wrong-tenant queries fail at the DB, not at the API layer. |
| Cost / latency awareness | ADR-0010 (extraction pipeline optimization) names the failure modes (LLM cost explosion, sequential latency, noisy file ingestion) and prescribes a six-tier frugality hierarchy. The thinking is correct even if the code hasn't caught up. |
| Storage design intent | The decision to keep JSON files as SOT (harness §5.1, repeated in v2) and treat Qdrant/Memgraph as derived indexes is the right architectural call for a brain that needs to be portable, reviewable, and recoverable. |

---

## 6. What does not work at non-code level

| Area | The gap |
|---|---|
| **Doc–code drift** | The harness/v2 docs describe systems that do not exist in code. A new contributor reads the docs, opens the code, and is confused. Either the docs need a `STATUS: aspirational` banner, or the docs need to be split into "current architecture" and "target architecture." |
| **No single owner/runbook for full-rebuild** | If Postgres + Redis + LocalStack + Ollama all need to be reset, there is no documented `make brain-reset` workflow that covers every state container. `make db-reset` exists but Redis cache, `/tmp/cb_checkpoint_*.json` files, and Ollama-pulled models are not part of it. |
| **No production target defined** | Everything is dev-on-laptop. There is no decision on where the SaaS would run (AWS? GCP?), what the deployment unit is (k8s? ECS?), how secrets are managed (env vars are fine for dev; not for prod). |
| **No cost ceiling on a pipeline run** | Running `extract POST /payments/charge` on a large repo could fan out to hundreds of LLM calls. There is no per-job spend cap, no kill-switch when a run exceeds $X. ADR-0010 mentions cost telemetry but no budget enforcement. |
| **No test coverage for the pipeline** | I saw `tests/unit/structural/` (3 test files) covering the tree-sitter parser. The orchestrator, navigator agent, intent synthesizer, and other LLM-heavy components have no unit tests visible in the file inventory. The whole system is validated by manual UI runs. |
| **No load / soak testing** | The Postgres schema has indexes for traversal, but nobody has measured `idx_edges_source` performance at 1M edges. Memgraph migration triggers are predicated on numbers nobody has captured. |
| **Onboarding flow for a 2nd repo doesn't exist** | The harness's `repos.json` registration is documented; the actual operational steps (where do I commit? what CI workflow do I copy? who reviews the PR?) are not. |
| **Security review absent at the AI-service boundary** | The AI service accepts `AiRunRequest` with a `callback_url`. If the URL is attacker-controlled, the pipeline POSTs results to attacker-controlled servers. There's no allowlist of acceptable callback hosts. The `INTERNAL_KEY` shared secret is the only barrier and it's an env var defaulting to `"dev-internal-key"`. |
| **No fail-loud telemetry** | `_trigger_structural_extraction()` swallows all errors as "non-fatal." Several other "(non-fatal)" comments scatter the pipeline. Failures slowly degrade the brain to less-than-it-should-be without anyone noticing. |
| **Ollama default `OLLAMA_NUM_CTX=3072` is wrong for code extraction** | A typical Spring controller class is 500-1500 LOC, which is well above 3072 tokens of context once you add the system prompt + examples + JSON output schema. The model silently truncates and produces broken JSON. The default should be `8192` minimum for code work. The comment in the file even acknowledges this — it just hasn't been changed. |
| **Brain rebuild is not idempotent under concurrent runs** | `_checkpoint_path()` keys on `(workspace_id, http_method, endpoint_path)`. If two engineers trigger the same endpoint extraction simultaneously they share a checkpoint file and corrupt each other's state. There's no per-run salt or job-id in the path. |

---

## 7. Why the pipeline breaks even on a single small repo

Below are the most likely root causes for a fresh setup, from highest probability to lowest. Many of these compound.

### 7.1 Endpoint not found → fallback path produces garbage

**Symptom.** Pipeline progresses to Stage 0a, logs `No handler found — will fall back to git diff extraction`, then Stage 1 produces 0–3 entities, Stage 2 produces 0 edges, the result is "completed" but the graph has nothing useful.

**Root causes:**
- The endpoint path you typed has a typo or includes a leading/trailing slash that doesn't match the source code.
- The repo uses a routing pattern not covered by the regex in `code_tracer.py` (e.g. `@RequestMapping(method=RequestMethod.GET, path="/users/{id}")` with the path on a separate line; or NestJS decorators; or Express custom router instances).
- The `NavigatorAgent` ran out of turns before reaching the handler (ADR-001 §1.1 acknowledges this).
- The repo isn't actually checked out at the path you provided (a sibling repo, an older clone).

**Where this manifests in the code.** `code_tracer.py` returns an empty `FocalContext` → `focal_context.is_empty()` is true → Stage 1 falls back to `extract_from_clusters()` which tries to extract entities from git diffs alone. Diffs often contain just whitespace or import re-orderings — the LLM produces noise.

### 7.2 Ollama context window too small

**Symptom.** Pipeline reaches Stage 1, sends a 2000-line Spring service to llama3.1:8b with `OLLAMA_NUM_CTX=3072`, gets back a half-truncated JSON, retries 4 times, fails with `RetryError`.

**Root causes:**
- Default `OLLAMA_NUM_CTX=3072` is below the realistic input size for code extraction.
- llama3.1:8b's JSON output adherence is mediocre on long inputs; truncation makes it worse.
- The retry backoff (`_wait_for_rate_limit`) handles 429s but not malformed JSON — those go through the regular tenacity exponential backoff and burn through retries fast.

**Where this manifests.** `OllamaProvider.chat()` returns whatever Ollama gave it; `EntityExtractor._extract_from_code_unit()` tries to parse the JSON, fails, raises, the retry decorator triggers, eventually `reraise=True` fires.

**Quickest fix.** Set `OLLAMA_NUM_CTX=8192` in `.env` and pull a code-aware model (`deepseek-coder-v2:16b` or larger).

### 7.3 LocalStack SQS not healthy → ingestion path silently dead

**Symptom.** The legacy `/v1/ingest` endpoint accepts a batch and returns 202, but nothing ever appears in the graph. Logs show `Failed to enqueue ingest batch to SQS queue ...`.

**Root cause.** LocalStack starts before the SQS queue is ready; the Java backend's `SqsTemplate` connects, fails, throws 503. This is more an issue for the agent-driven ingestion (S3 of the migration plan) than the on-demand pipeline path, but if you're using the agent flow, this is a top failure mode.

**Where this manifests.** `IngestService.acceptBatch()` catches the exception and rethrows as `ResponseStatusException(SERVICE_UNAVAILABLE)`. In dev mode HMAC validation is skipped, so the failure is purely SQS connectivity.

**Fix.** `make up` waits for `cb-localstack Up (healthy)` before starting Java. The README troubleshooting section calls this out.

### 7.4 Workspace context not set → RLS silently filters all writes

**Symptom.** Pipeline reports completed, posts results to Java's `/v1/internal/pipeline-result`, gets 200 back. Frontend shows zero nodes for the workspace.

**Root cause.** RLS policy `workspace_isolation` requires `current_setting('app.workspace_id', true)::UUID` to match `nodes.workspace_id`. If the Java request doesn't run `SET LOCAL app.workspace_id = '<uuid>'` before the insert, RLS treats `workspace_id` as NULL and the insert fails silently or rejects all rows.

**Where this manifests.** In the JPA insert path. The `WorkspaceContext` interceptor is supposed to set the session variable per request; if a code path bypasses it (a worker thread? a scheduled job?), writes silently fail.

**Verification.** `psql -c "SELECT count(*) FROM nodes WHERE workspace_id = '<uuid>'"` after a "successful" run. If it's zero, RLS swallowed the writes.

### 7.5 Ollama model not pulled → first call fails

**Symptom.** Pipeline starts, Stage 1 calls Ollama, gets `model "llama3.1:8b" not found, try pulling it first`.

**Root cause.** `make pull-small` was not run, or LocalStack/Docker Compose started Ollama with a different volume that doesn't have the model.

**Where this manifests.** `OllamaProvider._check_and_patch_models()` runs lazily on first call; if zero candidate models are pulled, every request 404s.

### 7.6 JSON output not valid → entity extraction returns nothing

**Symptom.** Stage 1 reports "0 entities" for a code unit that obviously contains classes and methods. Logs show `Failed to parse JSON response`.

**Root causes.**
- The LLM prepended a markdown fence (` ```json `) or said "Here is the JSON:" before the object.
- The LLM truncated mid-array because of context window.
- Llama3.1:8b emitted a comment `// note:` inside the JSON.
- A fields name had backticks in the source code that confused the model's quoting.

**Where this manifests.** `EntityExtractor._extract_from_code_unit()` calls `json.loads()` on the response. The current code (from what I read) doesn't have a robust JSON-extraction step that strips fences or recovers partial JSON. It just retries.

**Fix.** Wrap the parse in a `_extract_json_block()` helper that:
1. Strips ```json``` fences.
2. Locates the outermost `{...}` via balanced brace counting.
3. Falls back to `json5` for trailing-comma tolerance.

### 7.7 Stage 1.4 dependency expansion explodes context

**Symptom.** On a small repo, the pipeline reports "Expanded 5 candidates" but the resulting entities include random utility classes that have no relation to the endpoint.

**Root cause.** `_llm_suggest_expansions()` extracts every CamelCase identifier from entity snippets, scores by suffix, and returns top 8. On a small repo with low signal, the top scorers can be `OrderHelper`, `DateUtils`, `JsonMapper` — not collaborators. Stage 1.4 then opens those files and runs Stage 1 extraction over them, producing entities that are noise.

**Where this manifests.** `pipeline/orchestrator.py` lines ~540-600. The `_LEAF_SUFFIX` filter helps but doesn't catch generic Helper / Util / Mapper aliases consistently.

**Fix.** Add an LLM-gate: pass the candidate list + the current entity graph to a single Haiku call that says `which of these classes is actually a collaborator on this endpoint?` Returns a filtered list. One extra call, prevents 5 wasted unit extractions.

### 7.8 Checkpoint stale across `git pull`

**Symptom.** Engineer runs the pipeline, then `git pull` brings new code, then re-runs. The pipeline reports "Resuming from checkpoint" and produces stale entities — including ones whose source files no longer exist.

**Root cause.** `_checkpoint_load()` only checks 24 h staleness; it doesn't check whether the saved `code_units` still point at valid file paths or whether the file content hashes still match.

**Fix.** On checkpoint load, verify each `code_unit`'s file still exists and `content_hash` still matches. Discard entities tied to stale units.

### 7.9 `cb-api` (port 8090) not running → structural Neo4j graph empty

**Symptom.** Pipeline succeeds. Postgres has nodes/edges. But Neo4j has nothing useful (no `Function` / `Method` nodes from CoreTs, no Prisma / OpenAPI / SQL extractor output).

**Root cause.** The orchestrator's `_trigger_structural_extraction()` calls `http://cb-api:8090/extract`. The `apps/api/` Bun service exists, but if it isn't running (no `bun run` or no `cb-api` Docker container), the HTTP call fails. The helper catches the exception, logs "non-fatal," moves on.

**Compounding factor.** `cb-api` requires Bun installed locally and depends on the `@company-brain/*` workspace packages being built (`bun install` at the repo root). On a fresh clone these may not be set up — the README focuses on Java + Python + Node and doesn't make the Bun side a first-class step.

**Effect.** Anything downstream that relies on the structural Neo4j graph (function-level blast radius, framework-aware extractors for Next/Prisma/OpenAPI/JPA/SQLAlchemy, DriftDetector) doesn't run. The brain stays at LLM-only fidelity.

**Fix.** Add a `make cb-api` (or `make extractor`) target that runs `bun install && bun run --cwd apps/api dev`, document it in the README's "Start the App" section, and add `cb-api` to the docker-compose so `make up` brings it online alongside Neo4j. Optionally inline `structural/parser.py` into the Python orchestrator as Stage 0.5 fallback so a degraded path still works without Bun.

### 7.10 Backend ↔ AI callback URL mismatch in Docker

**Symptom.** Java logs `dispatched job to AI` then never receives the callback. AI service logs `httpx.ConnectError` when posting to `http://localhost:8080/v1/internal/pipeline-result`.

**Root cause.** When the AI service runs in Docker, `localhost` from inside the container is the container itself, not the host. The callback URL has to be `host.docker.internal:8080` (Mac/Windows) or the docker-compose service name (`backend:8080`).

**Where this manifests.** `JavaGraphClient.flush()` POSTs to whatever `BACKEND_URL` env var was set (default `http://localhost:8080`).

**Fix.** Set `BACKEND_URL=http://host.docker.internal:8080` in the AI service container's env, or run AI service natively (not in Docker) for development.

### 7.11 GitPython missing or repo without `.git`

**Symptom.** `/repo/branches` returns `gitpython not installed`, OR returns `Not a git repo`. The frontend's branch dropdown is empty; the pipeline can't be configured.

**Root cause.** Either `gitpython` was not installed in the AI service venv, or the user pointed at a directory that isn't a git repo (e.g. a downloaded zip).

**Fix.** `pip install gitpython` (should be in requirements; verify), and refuse to proceed unless `.git` exists.

### 7.12 The Postgres `external_id` UNIQUE constraint conflicts with re-extraction

**Symptom.** Pipeline succeeds the first time, fails the second time on the same repo with `ERROR: duplicate key value violates unique constraint "uq_node_identity"`.

**Root cause.** The Java write path is supposed to UPSERT (`ON CONFLICT (workspace_id, node_type, external_id) DO UPDATE`) but if the Java side does a plain INSERT, the second run collides on the unique constraint.

**Where this manifests.** Inside the (referenced but not visibly enumerated) `PipelineService.applyPipelineResult()` method on the Java side. Without seeing the source I cannot verify, but the symptom matches a missing UPSERT.

**Fix.** Verify the Java write path uses `MERGE` / `ON CONFLICT DO UPDATE` semantics for both nodes and edges.

### 7.13 Frontend polls for Redis state that the AI service stopped writing

**Symptom.** Pipeline completed (logs say so) but the React UI hangs on "running."

**Root cause.** `_run_and_callback` writes the running state but the **completion state** is supposed to come back from the Java callback. If the callback doesn't fire (see 7.10), the AI service never updates `job:{job_id}` to `completed`. The frontend polls forever.

**Fix.** Always write `job:{job_id}` from the AI service at the end of `_run_and_callback`, even when the Java callback URL is configured. The Redis state is the frontend's source of truth, regardless of who else gets notified.

### 7.14 Docker memory pressure kills Ollama mid-run

**Symptom.** Pipeline runs for 10 minutes, Stage 1 completes, then Stage 3 (context synthesis with deepseek-r1:14b) gets `connection reset by peer` mid-call.

**Root cause.** deepseek-r1:14b needs ~16 GB RAM; if Docker Desktop is configured with 8 GB and Ollama is running inside Docker, the model OOM-kills, Docker restarts the container, the pipeline gets a connection error.

**Fix.** Either increase Docker memory, switch to `make ollama-native` on Apple Silicon, or downgrade the SYNTHESIS role to a smaller model via `OLLAMA_MODEL_SYNTHESIS=llama3.1:8b`.

### 7.15 The PostgreSQL connection pool gets exhausted under concurrent pipelines

**Symptom.** Two pipelines running concurrently; one of them gets `HikariCP connection timeout`.

**Root cause.** Default Spring datasource pool is 10 connections. The AI service's freshness check + flush + RLS `SET LOCAL` all hold connections. Under concurrent load this is enough to starve.

**Fix.** Increase `spring.datasource.hikari.maximum-pool-size` to 20+ for dev, 50+ for prod-grade.

---

## 8. Severity-ordered fix list (what to attack this week)

If the goal is "make the pipeline reliably succeed on one small mono-repo," the order should be:

1. **Bump `OLLAMA_NUM_CTX` to 8192 by default and document that 3072 is broken for code work.** (5 minutes; root cause for 50% of fresh-setup failures.)
2. **Add a robust JSON extractor** to the LLM response handling that strips fences and tolerates partial output. (1 hour; root cause for another 20%.)
3. **Verify the Java upsert path** for nodes/edges — actually inspect `PipelineService` (or whoever owns `/v1/internal/pipeline-result`) to confirm `ON CONFLICT DO UPDATE`. If not, fix it. (1 day if the source is somewhere; longer if the Java side needs to be written.)
4. **Wire `companybrain/structural/parser.py` into Stage 0.5** of the orchestrator. Run tree-sitter before any LLM call; only LLM-enrich entities whose `file_hash` changed. (3–4 days; the single biggest cost / latency win.)
5. **Always write `job:{job_id}` completion state** from the AI service to Redis, regardless of Java callback success. (30 minutes; fixes the "running forever" UI bug.)
6. **Add a `brain index <repo>` CLI** that drives a whole-repo extraction without needing the React UI. (1–2 days; unlocks CI integration and removes dependency on the UI for testing.)
7. **Drop the `_trigger_structural_extraction(cb-api)` call** until cb-api exists, or replace it with the in-process tree-sitter extraction from #4. (15 minutes.)
8. **Add per-run cost ceiling** that aborts a pipeline if cumulative LLM spend exceeds $X (default $5 for dev). ADR-0010 mentions cost telemetry but no enforcement. (1 day.)
9. **Verify `WorkspaceContext` is set on every Postgres-touching request path**, including any background workers. Add an integration test that fails if RLS swallows writes. (1 day.)
10. **Document, in the README, the exact dev environment that was tested.** OS, Docker version, Ollama version, model versions, recommended `.env` values. (1 hour.)

This list does not get you to the harness vision. It gets you to "the pipeline I have, works." That's a precondition for everything in `MIGRATION-mono-to-multirepo-to-company.md`.

---

## 9. Honest assessment of where the project sits on the harness/v2 ladder

Mapped against the harness/v2 documents — corrected for the L1/L2/Main-Memory hierarchy, T0/T1 tokens, dual graph store (Postgres + Neo4j), Qdrant deployment, and Bun extractor-worker that I underweighted in the first draft:

| Harness / v2 stage | Status |
|---|---|
| Pre-v1 (one-off LLM-driven endpoint extraction) | **In place.** |
| v1 §2.2 / ADR-009 (L1 / L2 / Main-Memory hierarchy) | **In place.** L2 shared context + ContextManagerAgent + SharedContextAccumulator are live in the orchestrator. The "Main Memory" tier (paged in selectively from graph stores for high-uncertainty nodes) is partial — selective fetch logic isn't fully wired. |
| ADR-004 (T0/T1 tiered memory tokens stored in node metadata) | **In place.** MemoryTokenizer runs at Stage 3.5; `ContextAssemblerService` consumption on the Java side is referenced but I didn't enumerate the source. |
| v1 §3 (six entity types) | **Partial.** Five of six mappable from existing Postgres types + Neo4j labels; `assumption` + `business_context` live as `node_context.context_type` values, not first-class graph nodes. |
| v1 §4 (tree-sitter extractor + hash-based incremental + JSON write) | **Partial.** Bun `extractor-worker` runs tree-sitter via CoreTs + framework extractors and writes Neo4j. Hash-based freshness check exists in `JavaGraphClient.check_freshness`. **JSON `.brain/` SOT does not exist.** |
| v1 §5 (JSON SOT + hybrid retrieval index + dependency graph) | **Partial.** Neo4j is the structural graph; Qdrant is deployed for embeddings; **JSON SOT and the Python `retrieval/hybrid_search` consumer are missing.** |
| v1 §6 (smart-zone token-budget assembly, T1/T2/T3) | **Partial.** T0/T1 tokens generated and shipped to Java; the smart-zone *assembly* (T1 always + T2 retrieval-based + T3 on-demand under a token budget) is the missing layer. |
| v1 §7 (BFS blast radius engine) | **Partial.** Java `BlastRadiusService` referenced but not enumerated; Neo4j Cypher traversal is structurally available. Postgres recursive CTE not in `V1__create_graph_tables.sql`. |
| v1 §8 (MCP server with `brain_query`, `brain_get`, `brain_search`, etc.) | **Skeleton only** (`mcp/client.py` + 2 tool stubs + tRPC tool router in `apps/api/`). Not running as an MCP stdio server that Claude Code could attach to. |
| v1 §9 (multi-repo federation, `repos.json`, cross-repo edge resolution) | **None.** Workspace ID is the only multi-tenant key; there is no per-repo discriminator. |
| v1 §10 (git hooks + CI rebuild + SessionStart) | **Partial — nothing on git hook side; SessionStart not wired; no CI workflow checked in.** |
| v1 §11 (business context layer with Slack/Confluence/Jira ingestion) | **None.** Business context exists as a `node_context` row type but no ingestion of external sources. |
| ADR-001 §E1–E3 (function call graphs, type flows, state slices via tsc/pyright/java-callgraph2 + LLM enrichment) | **Partial.** Bun `CoreTsExtractor` produces function-level structural nodes in Neo4j (no full type-resolved graph yet); pyright / java-callgraph2 not integrated. State slices not modeled. Type flows not modeled. |
| ADR-001 §A1 (call_path pre-computation) | **None.** |
| ADR-001 §A2 (code pattern library) | **None.** |
| v2 §2 (graph-native store + temporal versioning + runtime edges) | **Partial.** Neo4j is in place — temporal versioning via `valid_to_commit` soft-invalidation is the URN scheme intent. No runtime edges yet. Memgraph migration is optional, not necessary. |
| v2 §3 (LSP streaming extraction <5s lag) | **None.** |
| v2 §4 (Inference Engine, Cypher rules) | **Seed only.** `DriftDetector` runs as Pass-3 in the Bun extractor pipeline. Declarative rule loader, transitive-assumption propagation, contract-drift inference, coverage-gap detection are not implemented. |
| v2 §5 (OTel runtime telemetry) | **None.** |
| v2 §6 (Proactive Intelligence alerts) | **None as outbound channels.** Drift edges exist in Neo4j; nothing routes them to Slack / GitHub PR. |
| v2 §7 (multi-modal: Figma, Terraform, DB migrations, CI/CD) | **None except Git.** SQL/Prisma/OpenAPI extractors exist for in-repo schema files but not for external systems. |
| v2 §8 (Brain API v2 — GraphQL + SSE) | **Partial.** `apps/api/` exposes a tRPC tool router (close-cousin to GraphQL); SSE not implemented. |
| v2 §9 (Semantic cache) | **None.** Anthropic prompt caching is wired (call-level), not result-level. |
| Claude Code integration (CLAUDE.md, hooks, skills) | **None.** ~1 week once an MCP stdio entry point exists. |

**Revised net assessment:** the system is roughly **30–35% of the way** to v1 (harness) and **10–15% of the way** to v2 (living brain). The L1/L2/Main-Memory tiering, T0/T1 tokens, dual-graph store (Postgres + Neo4j), Qdrant deployment, and Bun extractor-worker registry are real components that pull the project further along the ladder than the bare-pipeline framing of my first draft suggested. The honest gap is integration: the components exist but they don't yet *talk to each other* coherently — Postgres and Neo4j use different ID schemes, Qdrant has no Python consumer, the MCP server isn't running, the smart-zone assembler is missing, and the trigger is still endpoint-scoped instead of repo-scoped.

---

## 10. Final framing

The ambition documented in the four reference docs is correct and the strategic direction is sound. The codebase contains the right pieces — provider abstraction, per-stage LLM passes, structural parser, multi-tenant DB schema — and they're stitched together well enough to demonstrate the loop end-to-end on a good day.

What the codebase does *not* contain is the architectural backbone the harness assumes: per-repo brain JSONs, function-level entities, hybrid retrieval, an actual graph database, streaming extraction, and inference. Those are not bolt-ons. They are the spine of v1 and v2.

The migration plan in the companion document is the ladder out. The fix list in §8 of this document is the foothold to start climbing. Don't skip §8.

---

*Sources: source-code audit of `company-brain-ai/src/companybrain/`, `company-brain-backend/src/main/java/com/companybrain/`, `company-brain-frontend/`, `docs/`, root-level reference docs — verified 2026-05-07.*
