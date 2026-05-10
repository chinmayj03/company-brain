# Migration Plan: Single Mono-Repo → Multi-Repo → Company-wide Semantic Brain

> **Purpose.** Concrete architectural shifts and migration steps to take the harness + extraction pipeline from "barely works on one mono-repo" to "answers cross-repo blast radius across 10+ services" to "company-wide semantic memory that includes runtime, infra, design, and tribal knowledge."
>
> **Companion to:** `current-state-and-breakages.md`
>
> **Reads from:** `claude-code-architecture.md`, `harness-system-design.md` (v1), `ADR-001-enhanced-extraction-pipeline.md`, `company-brain-v2-system-design.md`
>
> **Date:** 2026-05-07

---

## TL;DR — the three stages

| Stage | Scope | Key shift | Storage SOT | Identity scheme | Triggers |
|---|---|---|---|---|---|
| **S1 — Mono-repo MVP** | One repo, one branch | Stop doing endpoint-by-endpoint LLM tracing. Do whole-repo structural extraction first, then enrich with LLM where structure is ambiguous. | `.brain/` JSON files (git-tracked) **+** Postgres mirror | `repo::entity_type::qualified_name` | One-shot CLI + git post-commit hook |
| **S2 — Multi-repo federation** | 5–15 repos in one platform | Add a `platform-brain` aggregator and cross-repo edge resolver. Promote graph from adjacency-list JSON to a proper graph DB once node count > 10K. | `.brain/` per-repo + `platform-brain/` aggregate + Memgraph (or Postgres recursive CTEs as bridge) | Same canonical IDs; `IMPORT_MAP` resolves package names to repo IDs | Per-repo CI rebuild + nightly platform aggregation |
| **S3 — Company-wide semantic** | All repos + Slack + Confluence + Jira + OTel + IaC + Figma | Decouple "code brain" from "knowledge brain" via a universal entity schema. Add streaming extraction (LSP), inference engine, runtime telemetry edges, proactive alerts. | Memgraph (graph SOT) + JSON snapshot exports + Qdrant (semantic) + Object store (raw artifacts) | URN scheme `urn:cb:{tenant}:{domain}:{type}:{qname}` (extension of S2 IDs, see ADR-0001) | LSP `didSave` hooks, OTel collector, MCP connectors, daily aggregations |

The migration is **additive**. Every later stage keeps the earlier stage's data formats working. Nothing is rewritten — layers are added.

---

## 1. Why the current pipeline cannot scale as-is

The current system (see `CURRENT-STATE-and-breakages.md` for full detail) was built around a single, tactical question: *"Given an endpoint path, build me a graph of everything that touches it."* It has more pieces in place than a first read suggests — L1/L2/Main-Memory context hierarchy, T0/T1 memory tokens, dual graph store (Postgres for semantic, Neo4j for structural), Qdrant deployed, Bun-based extractor-worker registry. The structural problems for the long game are:

1. **Endpoint-scoped triggering is anti-incremental.** Each pipeline run is a fresh LLM trace from one route handler down to the DB. There is no notion of "the repo's brain" as a stable artifact — only ephemeral per-endpoint snapshots that share the L2 context within one run but throw it away at the end. Re-running across 200 endpoints is 200× the cost and produces 200 partially overlapping graphs.
2. **LLM-first ordering — except where it isn't.** The Python orchestrator calls Haiku/Sonnet for entity extraction, relationship extraction, intent synthesis, context synthesis, and gap detection on raw source. The Bun extractor-worker (`apps/extractor-worker/`) does run structural extractors first, but it runs *after* the LLM pipeline completes (post-Stage-5 trigger via `cb-api`). ADR-0010's frugality hierarchy says tree-sitter first, then regex, then heuristics, then embeddings, then small LLM, then large LLM. Today the order is inverted: LLM first, structural pass second.
3. **Two graph stores, two ID schemes, no join layer.** Postgres `external_id` uses `repo/file::method`; Neo4j uses URN `urn:cb:llm:{workspace_id}:{file_path}:{entity_name}`. The dual-write happens but querying *across* both stores requires an adapter layer that doesn't yet exist. Qdrant is up but no Python consumer reads from it (`retrieval/hybrid_search.py` is missing).
4. **L2 context evaporates at the end of a run.** The L2SharedContext is great for the run that generated it, but it's not persisted to disk or replayed on the next run for the same repo. Each new run rebuilds domain glossary / service registry / pattern library from scratch.

The migration plan below fixes these in order, and explicitly leverages the L1/L2/Main-Memory tiering and the Postgres+Neo4j+Qdrant trio that are already in place rather than rebuilding them.

---

## 2. Stage 1 — Mono-repo MVP (weeks 1–4)

**Goal.** Turn one whole repository into a stable, queryable brain. Nothing fancy. Get the loop right.

### 2.1 Architectural shifts

#### 2.1.1 Switch the trigger from endpoint to repo

The pipeline today starts from `(endpoint_path, http_method)`. That has to become `(repo_path, commit_sha)`. The endpoint flow is retained as a *projection* over the repo brain, not the entry point.

```
Old:  endpoint_path  →  CodeTracer  →  NavigatorAgent (LLM)  →  Stage 1..5
New:  repo_path      →  Whole-repo structural pass (tree-sitter)
                     →  Per-entity LLM enrichment (only on changed hashes)
                     →  Aggregate into .brain/  →  Mirror to Postgres
```

Endpoint-level queries (`brain_query("what touches POST /payments/charge")`) become **graph traversals over the repo brain** — they no longer drive extraction.

#### 2.1.2 Introduce `.brain/` as the per-repo source of truth

Every brain entity becomes one git-tracked JSON file under `.brain/{type}/{qualified_name}.json`. Postgres becomes a *projection* of those files (kept for fast queries, RLS, history), not the SOT.

Why git-tracked JSON:
- **Reviewable.** A schema change shows up as a diff in PR review.
- **Bootstrappable.** A new clone of the repo gets the brain for free.
- **Recoverable.** Postgres / Qdrant can be wiped and rebuilt from JSON in minutes.
- **Branch-aware.** The brain on `feature/x` differs from `main` exactly as the code does.

This requires a lightweight `BrainStore` interface that writes JSON and emits change events; the existing `JavaGraphClient` becomes one consumer of those events instead of the only writer.

#### 2.1.3 Move structural extraction onto the main path

The hybrid extraction described in ADR-001 has to become Stage 0.5 of the orchestrator, **before** any LLM call. The Bun extractor-worker registry (`apps/extractor-worker/src/registry.ts`) already implements the structural side — Pass 1 (Git, CoreTs), Pass 2 (Next/Prisma/OpenAPI), Pass 2b (SQL/JPA/SQLAlchemy), Pass 3 (DriftDetector). Today this registry runs *after* the Python LLM pipeline; it has to move to the front of the train.

Two implementation options:

- **Option A — Have the Python orchestrator call cb-api first.** Reorder `_trigger_structural_extraction()` to run as Stage 0.5 (before Stage 1 entity extraction). The extractor-worker writes structural nodes/edges into Neo4j. The Python orchestrator then queries Neo4j for the structural facts of each code unit and uses them as the deterministic baseline. LLM extraction enriches *only* the entities whose structural fingerprint changed.
- **Option B — Inline `companybrain/structural/parser.py` into the Python orchestrator.** Use the existing tree-sitter Python port directly. Loses the framework-aware Bun extractors but doesn't depend on cb-api running.

Recommend **A** — keep the framework extractors. Document the Bun side as a hard dep of the pipeline (which it kind of already is, just trigged at the wrong time).

LLM passes (Stage 1, 1.5, 3, 4) then run **only on entities whose structural fingerprint changed since the last `.brain/` snapshot**, and only to enrich them with `t1_summary`, `purpose`, `assumptions`, `business_context`. This is the cost reduction ADR-0010 demands.

#### 2.1.4 Add the canonical entity ID scheme

Every entity gets an ID of the form `{repo}::{entity_type}::{qualified_name}`. This is non-negotiable — every later stage depends on it. It needs to be enforced at write time (one place: the BrainStore writer), validated at read time, and indexed in Postgres (`UNIQUE(workspace_id, external_id)` already exists, just align the format).

The harness doc and ADR-001 disagree slightly with the existing Postgres `external_id` format (`backend/src/payment.ts::chargePayment`). Pick one and migrate. Recommendation: harness format (`{repo}::{type}::{qualified_name}`), because it survives multi-repo and is what S2 needs.

#### 2.1.5 Bring in the six v1 entity types as first-class

Postgres today has free-form `node_type TEXT`. The harness expects `component | screen | api_contract | data_model | assumption | business_context`. Either map the existing types onto these six (cheap, recommended), or extend Postgres with a small lookup table. The mapping is straightforward:

| Existing | Maps to |
|---|---|
| `ApiEndpoint`, `Function` (HTTP handler) | `api_contract` |
| `Function`, `Class` (services) | `component` (backend) |
| `FrontendComponent` | `component` (frontend) |
| `SchemaField`, `DatabaseTable`, `DatabaseColumn` | `data_model` |
| `node_context.context_type='invariant'` | `assumption` (promote to first-class) |
| `node_context.context_type='llm_synthesis'`, `'business_context'` | `business_context` (promote to first-class) |

Screens are new — they can be skeletoned now (1 entity per Vite/Next route file) and filled in later.

### 2.2 Code-level changes (Stage 1)

| What | Where | Effort |
|---|---|---|
| New `BrainStore` interface (write JSON + emit event; backed by `.brain/` files, fans out to existing `JavaGraphClient` + `Neo4jWriter` + (eventually) Qdrant indexer) | `company-brain-ai/src/companybrain/store/` (new module) | 3 days |
| Whole-repo structural pre-pass — call cb-api `/extract` *before* Stage 1 instead of after Stage 5 | `pipeline/orchestrator.py` (reorder) | 1 day |
| Wire L2SharedContext persistence — serialise to `.brain/.l2-cache.json` at end of run, reload at start of next run for the same repo | `pipeline/shared_context_accumulator.py` + `BrainStore` | 2 days |
| Hash-based freshness map lifted from `JavaGraphClient.check_freshness` into `BrainStore` | `store/freshness.py` | 2 days |
| Make freshness check honor the structural fingerprint from Neo4j (not just file SHA) so a refactor that doesn't change function bodies skips LLM | `pipeline/orchestrator.py` Stage 0c | 2 days |
| Move LLM enrichment to "only on dirty entities" | `pipeline/orchestrator.py` (refactor) | 4 days |
| Postgres ↔ Neo4j ID adapter — translate `urn:cb:llm:...` ⇄ `repo/file::method` so cross-store joins work | `companybrain/store/identity.py` | 3 days |
| Wire Qdrant — implement the missing `companybrain/retrieval/hybrid_search.py` (BM25S + dense via voyage-code-3 + RRF) | new module | 5 days |
| Postgres mirror writer — Java `PipelineService` becomes a consumer of brain events | `company-brain-backend` | 3 days |
| ID normalisation migration (Flyway V2) | `db/migration/V2__normalise_external_ids.sql` | 1 day |
| Node-type mapping migration (Flyway V3) | `db/migration/V3__entity_type_taxonomy.sql` | 1 day |
| Promote `assumption` and `business_context` to first-class graph nodes (currently in `node_context` rows) | Java model + DTO + Postgres migration + Neo4j label | 2 days |
| Python `brain` CLI (`brain index`, `brain map`, `brain query`, `brain blast-radius`) — wraps orchestrator + cb-api + Java backend | `company-brain-ai/src/companybrain/cli.py` (absent today) | 3 days |

### 2.3 Non-code changes (Stage 1)

- Decide and document the canonical ID format in `docs/adrs/ADR-0001-urn-identity-scheme.md` (already exists — align it with the harness `repo::type::qname` format if it differs).
- Add `.brain/` to the **target** repo's `.gitignore` policy: brain files **stay in git** (committed), but generated indexes (`.brain/.cache/`) are ignored.
- Pick one mono-repo as the pilot. The pilot must satisfy:
  - Builds locally with one command.
  - Has at least one Spring Boot service or one TS/Express service so the existing CodeTracer regex hits.
  - <500 source files (so a full LLM pass on dirty entities costs <$1).
- Set up `.env` profile `dev-pilot` with sensible defaults: `LLM_PROVIDER=anthropic`, `OLLAMA_NUM_CTX=8192`, `BRAIN_TOKEN_BUDGET=4000` for early testing.

### 2.4 What gets unlocked at end of Stage 1

- `brain query "what does PaymentService do"` → traversal over `.brain/` returns T1 + T2 + business context, no LLM call.
- `brain blast-radius PaymentService` → BFS over the per-repo dependency graph, no LLM call.
- Full extraction in <10 min on the pilot, incremental <30 s per changed file.
- The pilot repo's `.brain/` directory is checkable into git; reviewers see "what changed in the brain" in PRs.
- Postgres remains queryable through the existing Java backend — backward-compatible for the React UI and the VS Code extension.

### 2.5 Acceptance criteria for Stage 1

- [ ] `make brain-index ./repo` produces a complete `.brain/` directory in <10 min for the pilot.
- [ ] Re-running `make brain-index` on an unchanged repo skips 100% of LLM calls.
- [ ] Editing one file and re-running produces a diff in `.brain/` containing only that entity (and any explicit edges that changed).
- [ ] Postgres queries return identical results to the JSON SOT (one bidirectional integrity test).
- [ ] The existing endpoint-driven flow (`/pipeline/start`) keeps working as a thin wrapper over `brain query`.

---

## 3. Stage 2 — Multi-repo federation (weeks 5–10)

**Goal.** Make blast radius answerable across repo boundaries. This is where the product thesis lives — *"the change cascades across services no one repo sees."*

### 3.1 Architectural shifts

#### 3.1.1 Add the `platform-brain/` aggregate

A new top-level repo (or directory) that holds:
- `repos.json` — registry of every repo in the platform (id, path, brain_path, languages, domain, team).
- `platform-graph.json` — aggregated cross-repo edges only (not a copy of every per-repo edge — just the edges whose `from.repo != to.repo`).
- `platform-index.json` — entity ID → repo + file lookup, used by the import resolver.
- `business_context/` — domain glossary and platform-wide business context.

Every per-repo `.brain/` keeps its intra-repo brain. The platform brain stitches them together.

#### 3.1.2 Cross-repo edge resolution

The single hardest engineering problem of Stage 2. When `web-app/UserCard.tsx` does `import { UserDTO } from '@company/shared-lib'`, the extractor must know that `@company/shared-lib` resolves to the brain entity `shared-lib::data_model::UserDTO`.

Resolution strategy:
1. Each repo declares its `package_aliases` in its `.brain/repo.json` (e.g. `@company/shared-lib → shared-lib`). Source: `package.json` workspaces, Maven coordinates, Python distribution names.
2. The extractor, on encountering an import from a non-relative path, checks the `IMPORT_MAP` in `platform-brain/repos.json`.
3. If matched, it emits a `cross_repo_edge` candidate with the resolved canonical ID.
4. At platform aggregation time, candidates are validated against the target repo's index — broken cross-repo edges become an alert (`UNRESOLVED_IMPORT`).

This must work for at least: npm/yarn workspaces, Maven multi-module, Gradle multi-module, Python namespace packages, Go module replace directives, Cargo workspaces.

#### 3.1.3 Graph storage — already on Neo4j

Multi-hop blast radius is already a Neo4j Cypher query. The "promote to a real graph DB at threshold" decision is moot — the platform shipped on Neo4j 5.18 community via `cb-neo4j` in the docker-compose. The decision tree becomes:

| Node count | Action | Rationale |
|---|---|---|
| <1M total | Stay on Neo4j community | Free, working, sufficient |
| 1M–10M | Tune Neo4j heap / pagecache; consider AuraDB | Memory pressure starts to bite at this size |
| >10M | Memgraph (in-memory) or Neo4j cluster | Sub-millisecond traversal at platform scale |

The Postgres semantic graph remains the metadata + RLS + audit-trail layer. Neo4j is the structural / traversal layer. The two should not be unified — they serve different access patterns. What does need to be unified is the **ID scheme** so `BrainGraphAdapter` can return the same entity from either store given a single canonical ID.

Critically: the `BrainGraphAdapter` interface is what hides this dual-store reality from the callers. `brain blast_radius(entity_id, hops)` queries Neo4j; `brain get(entity_id)` may hit Postgres for metadata + Neo4j for relationships and stitch the result.

#### 3.1.4 Per-repo CI rebuild + nightly platform aggregation

Each repo gets its own brain CI job that runs on `push` to main:
```yaml
# .github/workflows/brain.yml in every participating repo
on: [push]
jobs:
  brain-index:
    steps:
      - uses: actions/checkout@v4
      - run: brain rebuild --repo . --mode full
      - run: brain push --target platform-brain-repo  # commits to platform-brain
```

A nightly job in `platform-brain` aggregates all per-repo `.brain/` updates into the platform graph, resolves cross-repo edges, and produces alerts for unresolved imports.

#### 3.1.5 Multi-repo brain query routing

The MCP `brain_query` tool gains an optional `repo` filter and a default behaviour of querying across all registered repos in parallel. The Qdrant collections become per-repo (`brain_<repo>_component`, `brain_<repo>_api_contract`, etc.) so each repo's index can be rebuilt independently. Cross-repo searches do parallel `client.search()` calls and re-rank the merged result with RRF.

### 3.2 Code-level changes (Stage 2)

| What | Where | Effort |
|---|---|---|
| `platform-brain` repo skeleton + `repos.json` schema | new repo | 1 day |
| `IMPORT_MAP` builder per language (npm, Maven, Gradle, Python) | `companybrain/store/import_resolver.py` | 5 days |
| Cross-repo edge candidate emission in extractor | extend `import_graph.py` | 3 days |
| Platform aggregator job | `cli.brain_aggregate` | 4 days |
| `BrainGraphAdapter` abstraction (JSON vs Postgres CTE vs Memgraph) | `companybrain/graph/adapter.py` | 3 days |
| Postgres recursive-CTE blast radius implementation | `BlastRadiusService.java` (extend) | 3 days |
| Per-repo Qdrant collection naming + sharded index | `companybrain/retrieval/hybrid_search.py` (currently referenced but missing — needs to be built) | 6 days |
| Cross-repo `brain_query_platform` MCP tool | `companybrain/mcp/tools/query.py` | 2 days |
| `UNRESOLVED_IMPORT` alert wiring | `companybrain/alerts/` (new) | 2 days |

### 3.3 Non-code changes (Stage 2)

- Decide ownership of `platform-brain/`: which team merges PRs to it. Recommend Platform Eng owns it; per-repo teams own their `.brain/`.
- Naming convention for repos in `repos.json` must be unique and stable. Once a repo ID is chosen, it cannot change without a graph migration.
- Confluence/Notion runbook: "How to add a new repo to the platform brain" — covers `repos.json` registration, alias mapping, CI workflow installation.
- SLA on cross-repo brain freshness: target ≤24 h lag from a merge in repo A to a cross-repo edge appearing in `platform-graph.json`.
- Capacity planning for Memgraph (when triggered): start at 8 GB RAM container, scale by node count.

### 3.4 What gets unlocked at end of Stage 2

- `brain blast-radius shared-lib::data_model::UserDTO` answers across all consumer repos in <500 ms.
- `brain query "who consumes the GET /users/{id} API"` returns components from web-app, mobile-app, admin-app correctly.
- A PR that changes a shared-lib type can show, in the PR body, every cross-repo consumer that needs updating.
- The VS Code extension can show "this function is called by N functions in M other repos" without leaving the editor.

### 3.5 Acceptance criteria for Stage 2

- [ ] At least 5 repos registered, each with their own CI brain rebuild green.
- [ ] `platform-graph.json` has >100 cross-repo edges and zero unresolved imports.
- [ ] `brain blast-radius` p95 < 500 ms for a 3-hop query on the platform graph.
- [ ] `brain query` MCP tool returns results from multiple repos in a single response.
- [ ] One real cross-repo PR ships using the brain to identify the consumer list (manual validation).

---

## 4. Stage 3 — Company-wide semantic brain (weeks 11–30)

**Goal.** Stop being "a code dependency tool." Start being "the company's executable memory." This is the v2 vision from `company-brain-v2-system-design.md` — graph-native, continuously reasoning, multi-modal, proactive.

### 4.1 Architectural shifts

#### 4.1.1 URN identity scheme (extension, not replacement)

For company-wide knowledge that isn't code (Slack threads, Zendesk tickets, Confluence pages, OTel spans), the `repo::type::qname` ID is too narrow. Promote to URNs:

```
urn:cb:{tenant}:{domain}:{type}:{qualified_name}

# code (compatible with S2 IDs by mapping {tenant}={org}, {domain}=code, {type}={entity_type})
urn:cb:acme:code:web-app:component:UserCard

# non-code
urn:cb:acme:support:zendesk:ticket:54321
urn:cb:acme:identity:slack:thread:T0123/C456/p789
urn:cb:acme:product:confluence:page:Refunds-Policy-v3
urn:cb:acme:infra:terraform:aws_rds:users-rds-prod
urn:cb:acme:runtime:otel:span:GET_/users/{id}
urn:cb:acme:design:figma:component:UserCard
```

This is already drafted as ADR-0001 in the repo. Make it law.

#### 4.1.2 Graph-native storage — already on Neo4j; Memgraph optional

S3's heavy graph work (inference rules, temporal queries, streaming extraction) runs against Neo4j directly — the existing store. The decisions for S3 are:

- **Temporal versioning.** The URN scheme already encodes scope; add `valid_from` / `valid_to` properties on Neo4j edges and write the inference engine + queries to honor them. Soft-invalidation (no deletes) is already the contract per `neo4j_writer.py` — extend it to first-class temporal fields.
- **Inference rules as Cypher.** Live in `inference_rules/*.cypher` with YAML metadata. The Inference Engine daemon executes them against Neo4j on change events. Same engine pattern as v2 doc but pointed at Neo4j instead of Memgraph.
- **Memgraph migration only if Neo4j community can't keep up.** Empirical trigger: p95 traversal > 200 ms on a 1M-node graph, or write-throughput cap during streaming extraction. If you cross that line, Memgraph's Cypher compatibility makes the migration low-risk.

JSON `.brain/` files remain as the **export format**. `brain export --to-json` regenerates `.brain/` from Postgres + Neo4j — this is how the brain stays git-friendly across both stores.

#### 4.1.3 Streaming extraction via LSP

The current orchestrator runs on commit + on demand. For the v2 promise of "the brain knows what your editor knows, ~5 s after you save," the trigger has to move to LSP `textDocument/didSave`. Two implementation paths:

- **Sidecar.** A background process subscribes to LSP events from the active editor (VS Code extension API exposes them; JetBrains via plugin SDK) and forwards them to a local extractor.
- **CLI watcher.** `brain watch` runs `chokidar`/`watchdog` on the repo and re-extracts on change. Less precise (file mtime, not save event) but works in any editor.

Either way the extractor itself becomes incremental: TypeScript Compiler API in incremental mode, pyright dmypy daemon, kotlinc daemon. The `_TIMEOUT=300s` and `OLLAMA_NUM_CTX=3072` defaults from the current Ollama provider are fine for one-shot local LLM enrichment of one file at a time.

#### 4.1.4 Inference engine

A daemon that watches the graph for changes and applies declarative Cypher rules to derive new edges (transitive assumption propagation, contract drift, coverage gap, pattern divergence, state mutation reachability — see v2 §4). Rules live in a `inference_rules/` directory in the platform-brain repo. New rules are added by writing a `.cypher` file and a YAML metadata file describing what triggers it.

This is the single highest-leverage component of S3. Without it the brain remains a static index. With it the brain *reasons*.

#### 4.1.5 Runtime telemetry edges (OTel)

OpenTelemetry traces become first-class graph edges (`CALLED_AT_RUNTIME`) keyed by 7-day rolling windows. Implementation: a new exporter pluggable into the OTel Collector that converts spans to graph upserts. The mapping from `span.name` → `function_node.id` is the brittle bit; it requires consistent service naming + method-name conventions. Document a contract: every service's OTel `service.name` matches its `repos.json` repo id; every span name is `{class_name}.{method_name}`.

#### 4.1.6 Multi-modal collectors (plug-in architecture)

The `companybrain/collectors/` module already has the right shape (`base.py`, `git_collector.py`). Extend with:

- `slack_collector.py` — uses the Slack MCP to ingest decision threads, channel messages tagged `#decision`.
- `confluence_collector.py` / `notion_collector.py` — page content as `business_context` entities.
- `jira_collector.py` / `linear_collector.py` — ticket linkage to commits → PRs → entities.
- `terraform_collector.py` / `k8s_collector.py` — infra services and `DEPLOYED_ON` edges.
- `figma_collector.py` — design components and `MATCHES_DESIGN` edges.
- `ci_collector.py` — test coverage and `COVERS` edges.
- `db_migration_collector.py` — Alembic/Flyway/Liquibase migrations as DataModel version evolution.

Each collector implements a stable contract (see ADR-0003): produce `RawChunk[] → ExtractedEntity[] → ExtractedRelationship[]`. The orchestrator becomes a thin runner that knows nothing collector-specific.

#### 4.1.7 Proactive intelligence layer

The Inference Engine produces edges; the Proactive layer turns *edges of certain types* into outbound notifications. `CONTRACT_DRIFT`, `ASSUMPTION_VIOLATION`, `HOT_PATH_AT_RISK`, `PATTERN_DIVERGENCE` route to Slack / GitHub PR comments per the v2 spec. The router is a single config file (`alerts.yml`) — adding a new alert type is one PR.

#### 4.1.8 Brain API v2 — GraphQL + SSE on top of MCP

The MCP tools stay backward-compatible (the LLM is one consumer). Two new surfaces:

- **GraphQL API** for programmatic consumers (CI gates, dashboards, the IDE extension's structured queries).
- **SSE event stream** for real-time brain updates — IDE plugins subscribe to receive `ENTITY_UPDATED` / `INFERRED_FACT` / `CONTRACT_DRIFT` events.

Both sit in front of Memgraph and reuse the same Cypher routing.

#### 4.1.9 Semantic cache for LLM-synthesised answers

When `brain_query("what does UserCard do")` is called repeatedly with the same payload version hashes, the LLM response is cached keyed by `hash(query) + hash(entity_versions)`. Memgraph triggers invalidate cache entries on entity update. Target hit rate >70% for common queries; this is the primary cost lever once usage scales.

### 4.2 Code-level changes (Stage 3)

| What | Where | Effort |
|---|---|---|
| URN parser + validator (extend existing `urn:cb:llm:...` scheme to full ADR-0001 URN) | `companybrain/store/identity.py` | 2 days |
| Temporal edge fields (`valid_from`, `valid_to`) on Neo4j; write-path support | extend `graph/neo4j_writer.py` | 3 days |
| (Optional) Memgraph adapter for `BrainGraphAdapter` if Neo4j community caps out | `companybrain/graph/memgraph_adapter.py` | 8 days |
| LSP middleware (VS Code extension + sidecar daemon) | `company-brain-frontend/vscode-extension/` (extend) + `companybrain/lsp/middleware.py` (new) | 10 days |
| Incremental tsc / dmypy / kotlinc adapters | `companybrain/structural/incremental/` | 12 days |
| Inference Engine daemon + rule loader | `companybrain/inference/engine.py` + `inference_rules/*.cypher` | 10 days |
| OTel exporter for graph edges | `companybrain/runtime/otel_exporter.py` | 6 days |
| Telemetry processor (rolling-window aggregation) | `companybrain/runtime/aggregator.py` | 4 days |
| Slack / Confluence / Notion / Jira collectors | `companybrain/collectors/{slack,confluence,notion,jira}_collector.py` | 4 days each |
| Terraform / k8s / Figma / CI / DB-migration collectors | one each, ~3–5 days each | 20 days |
| Alert router + Slack/GitHub integrations | `companybrain/alerts/router.py` | 5 days |
| GraphQL API on top of Memgraph | `companybrain/api/graphql.py` | 7 days |
| SSE event stream | `companybrain/api/events.py` | 4 days |
| Semantic cache (Redis-backed) | `companybrain/cache/semantic_cache.py` | 4 days |
| brain_inferred_facts / brain_runtime_profile / brain_watch MCP tools | `companybrain/mcp/tools/v2.py` | 5 days |

### 4.3 Non-code changes (Stage 3)

- Tenant strategy. Decide single-tenant vs multi-tenant. Single-tenant per-customer Memgraph instance is operationally simpler; multi-tenant requires URN-tenant isolation enforced in every query. Recommend single-tenant for the first paying enterprise, multi-tenant for the SaaS tier.
- Data residency policy. Once Confluence / Slack content lives in the brain, GDPR / SOC2 / HIPAA requirements bite. Establish:
  - Where Memgraph runs (customer VPC for enterprise; Anthropic/customer-of-record region for SaaS).
  - Encryption-at-rest with BYOK option (the Postgres schema already has `kms_key_id` on `workspaces` — extend to Memgraph).
  - Right-to-erasure support: deleting a user from Slack must delete their messages from the brain.
- Ingestion contracts with each non-code source. Slack/Zendesk/Confluence rate limits, OAuth scopes, agent-vs-platform permissions. Each collector ADR documents its contract.
- Onboarding flow for a new customer:
  1. Connect repos (per-repo CI workflow installation).
  2. Connect MCP sources (Slack, Confluence, Jira, OTel collector).
  3. Optional: Figma, Terraform, k8s.
  4. Wait for first full extraction (depends on repo size).
  5. Verify cross-repo blast-radius answers a known PR's blast list.
- Org-mode runbooks for: brain rebuild from scratch (DR), Memgraph upgrade, schema migration, customer offboarding.

### 4.4 What gets unlocked at end of Stage 3

- A new engineer can ask "what is the IAM domain in our system" and the brain answers with: services, screens, APIs, owning team, owning Slack channel, recent ADRs, latest production incidents, Figma source — all linked.
- A PR that changes a DataModel field can fail the CI gate if the brain detects a `CONTRACT_DRIFT` against a cross-repo consumer that wasn't updated in the same PR.
- A Slack `@brain` bot can answer "who decided the refund window is 30 days" with the original decision thread + linked Confluence + the code that enforces it.
- Claude Code, when started in a repo, automatically loads a warm brain context via SessionStart hook + MCP, so the model "knows" the codebase before the first user message.
- Observability: hot-path queries (`brain runtime-profile <fn>`) connect call volume + latency + error rate to source code, replacing 80% of "is this code on a hot path?" investigations.

### 4.5 Acceptance criteria for Stage 3

- [ ] 10+ repos + Slack + Confluence + OTel + Terraform all ingesting into a single Memgraph with sub-second p95 query latency.
- [ ] Inference Engine produces ≥3 useful inferred edge types in production (e.g. transitive assumption propagation, contract drift, coverage gap).
- [ ] At least one alert type (Contract Drift) running in production firing on real PRs.
- [ ] LSP streaming achieving target <5 s lag from save to brain update on a 50K-LOC repo.
- [ ] One enterprise customer onboarded with their own tenant, with full multi-modal ingestion working.
- [ ] Semantic cache hit rate >50% within first 7 days of usage.

---

## 5. Migration mechanics — turning today's code into Stage 1

The single most important rule of this migration: **don't rewrite, don't fork, don't pause.** Every layer is added on top.

### 5.1 Phase 1.A — split the orchestrator into "structural" and "semantic"

Today's `pipeline/orchestrator.py` does both. Split it:

```
orchestrator.py
├── structural_pass()      # tree-sitter, regex, import-graph (deterministic)
└── semantic_pass()        # LLM enrichment, intent synthesis, gap detection
```

`run_pipeline()` becomes:

```python
async def run_pipeline(request):
    structural_result = await structural_pass(request)   # NEW: zero LLM calls
    if structural_result.entities_changed:               # only enrich changed
        semantic_result = await semantic_pass(structural_result, request)
    else:
        semantic_result = structural_result.cached_semantic
    await brain_store.commit(structural_result, semantic_result)
```

This is the single highest-leverage refactor. It cuts cost by 5–10× on incremental runs.

### 5.2 Phase 1.B — write `BrainStore` and replay events into Postgres

```python
class BrainStore:
    def write(self, entity: BrainEntity) -> None: ...
    def read(self, entity_id: str) -> BrainEntity: ...
    def emit(self, event: BrainEvent) -> None: ...

class JsonFileBrainStore(BrainStore):
    """Writes to .brain/ directory. SOT."""

class PostgresBrainStore(BrainStore):
    """Mirror. Subscribes to events, replays into nodes/edges/node_context."""
```

The Java `PipelineService.applyPipelineResult()` becomes a `PostgresBrainStore.handle_event()`. The shape of what it does (upserts to nodes/edges) doesn't change — only the input format does (an event instead of a pipeline result POST).

### 5.3 Phase 1.C — hash-based dirty detection at the file level (already half-built)

Lift `JavaGraphClient.check_freshness()` into `BrainStore.is_fresh(entity_id, content_hash)`. It already does the right thing (SHA-256 of source content), it's just in the wrong layer.

Once lifted, the structural pass becomes:
```python
for file in repo.walk():
    h = sha256(file.content)
    if not brain_store.is_fresh(file.path, h):
        entities = structural_parser.parse(file)
        for e in entities: brain_store.write(e)
```

### 5.4 Phase 1.D — `cli.py` entry point

Today there is no CLI; users trigger the pipeline via the React UI which calls Java which calls Python. For brain rebuild and incremental runs, a CLI is non-negotiable:

```bash
brain index ./my-repo                  # full
brain index ./my-repo --since HEAD~5   # incremental
brain map ./my-repo/src/foo.ts         # one file
brain query "what does PaymentService do" --repo my-repo
brain blast-radius "my-repo::component::PaymentService" --hops 2
brain push --target platform-brain
```

The CLI calls into the same `companybrain.pipeline.orchestrator` and `companybrain.store.brain_store`, just from a different entry point. ~1 day of work using `click` or `typer`.

### 5.5 Phase 1.E — Postgres schema migrations

Two migrations, both reversible:

**V2 — normalise external_id format**
```sql
-- Backfill existing rows to the canonical {repo}::{type}::{qname} format.
UPDATE nodes SET external_id = workspace_id || '::' || node_type || '::' || external_id
  WHERE external_id NOT LIKE '%::%::%';
ALTER TABLE nodes ADD CONSTRAINT chk_external_id_format
  CHECK (external_id ~ '^[a-zA-Z0-9_-]+::[a-zA-Z_]+::.+$');
```

**V3 — entity_type taxonomy**
```sql
-- Replace freeform node_type with a constrained set.
ALTER TABLE nodes ADD COLUMN entity_type TEXT;
UPDATE nodes SET entity_type = CASE
  WHEN node_type IN ('ApiEndpoint','Function','Service') AND name LIKE '%Controller%' THEN 'api_contract'
  WHEN node_type = 'FrontendComponent' THEN 'component'
  WHEN node_type IN ('SchemaField','DatabaseTable','DatabaseColumn') THEN 'data_model'
  WHEN node_type IN ('Function','Class','Service','CodeFunction') THEN 'component'
  ELSE 'component'  -- safe default
END;
```

Both migrations must run with the existing data; tested on a snapshot of the dev database.

### 5.6 Phase 1.F — backward compatibility for the existing UI

The React frontend, the VS Code extension, the existing `/v1/internal/pipeline-result` callback, the SQS ingest path — none of them break. The `PostgresBrainStore` event handler emits the same DB writes the Java `PipelineService` does today. The React UI keeps reading the same Postgres tables.

What changes is *what writes to those tables*. Today it's the Python AI service POSTing pipeline results. After the refactor it's `PostgresBrainStore` consuming brain events. Same data, different write path.

This means: Stage 1 can ship without any frontend changes. Validate the brain via the CLI and via `psql`, then move on.

---

## 6. The decision tree — where do I go next?

```
Have you got Stage 1 working on one mono-repo end-to-end?
├── No  → fix Stage 1. Focus order:
│         1. Split orchestrator into structural_pass / semantic_pass
│         2. Implement BrainStore (JSON SOT)
│         3. Hash-based dirty detection
│         4. CLI entry points
│         5. Postgres migrations + Postgres mirror writer
│
├── Yes → Are you planning to onboard a 2nd repo this quarter?
│         ├── Yes → start Stage 2:
│         │        1. platform-brain skeleton + repos.json
│         │        2. IMPORT_MAP builder for the languages you actually use
│         │        3. Cross-repo edge candidate emission
│         │        4. Per-repo CI rebuild
│         │        5. (only if node count >10K) Postgres recursive CTE blast radius
│         │
│         └── No  → you have time. Use it on Stage 1 hardening:
│                  - LLM cost telemetry
│                  - Better tree-sitter coverage (TSX state hooks, Spring annotation
│                    nuances, FastAPI dependency injection)
│                  - Code pattern library (ADR-001 §A2)
│                  - assumption mining heuristics on real code
│                  Stage 2 will be cheap when you have a clean Stage 1.
│
And for Stage 3, defer until Stage 2 is production-stable. The temptation to
build the inference engine before the basic graph is correct is exactly how
"company brain" projects die — clever reasoning over wrong data.
```

---

## 7. Estimate summary

| Stage | Calendar weeks | One-engineer effort (days) | Notes |
|---|---|---|---|
| Stage 1 (mono-repo MVP) | 4 | 25–30 | Includes orchestrator refactor + BrainStore + CLI + 2 Postgres migrations |
| Stage 2 (multi-repo federation) | 6 | 35–45 | Dominated by IMPORT_MAP builders per language |
| Stage 3 (company-wide semantic) | 16–20 | 130–180 | Memgraph migration, LSP middleware, inference engine, multi-modal collectors |
| **Total to v2 vision** | **~30 weeks** | **~200 days of focused work** | Aligns with v2's own "21 weeks from baseline" estimate |

These are floor estimates assuming one full-time engineer. Real team will be faster on parallelisable work (collectors, language adapters), slower on integration milestones.

---

## 8. Risks worth naming

1. **Cross-repo edge resolution will be the slow part.** Every monorepo and every package manager has a different way of expressing inter-package boundaries. Plan for 2× the time you think it will take.
2. **Memgraph operational maturity.** It is OSS but it is not Postgres. Backups, replication, tuning — there will be a learning curve. The interim "Postgres recursive CTE" path is an insurance policy; do not skip it.
3. **LSP middleware is invasive.** Engineers don't like sidecars in their editors. Make installation one-click; make the middleware crash-isolated from the editor; ship a `brain watch` CLI fallback.
4. **The inference engine can produce noise.** Bad rules generate false alerts; engineers turn off the brain. Every rule must have a documented precision/recall target and a kill-switch in `alerts.yml`.
5. **Multi-modal data without business context becomes another data lake.** Slack ingestion alone is not useful. Pair every collector with a `business_context` extraction step that uses LLM to distil the Slack thread into a structured business_context entity. Otherwise the brain gets bigger but not smarter.
6. **Tenant isolation in S3.** The moment a customer trusts you with their Slack content, a single missed `WHERE tenant = ?` is a disclosure incident. Enforce tenant in the URN scheme, in every query path, and have a third-party audit before the first paid SaaS customer.

---

*Read alongside `current-state-and-breakages.md` for the present-day code-level reality.*

---

## 9. ADR-0042 — Language-Agnostic Extraction Enhancements (Accepted, 2026-05-10)

Stage 1 is now extended by ADR-0042, which removes all per-language branching from
orchestrator paths and introduces LLM-as-pattern-recognizer passes that work for
Java, Python, and TypeScript from a single implementation.

### 9.1 What was added

| Enhancement | Description |
|---|---|
| **E1** | Cross-file call graph: 3-hop default, ripgrep+LLM two-tier symbol resolver |
| **E2** | AnnotationPass — ANNOTATES edges for framework annotations/decorators |
| **E3** | StorageTargetPass — PERSISTS\_TO edges to DatabaseTable entities |
| **E4** | Method-level freshness hashes (sha256 per body, skip unchanged methods) |
| **E5** | SchemaMigrationPass — CONTAINS edges from migration files (any framework) |
| **E6** | ClientCallPass — CALLS\_ENDPOINT edges for outbound HTTP/gRPC/queue calls |
| **E7** | TestCoveragePass — TESTED\_BY edges from test to production entities |
| **E8** | Multi-pass chunked relationship extraction (co-locality grouping, 25-entity batches) |
| **E9** | `edges_reverse` materialized view for blast-radius/upstream queries |
| **E10** | Intent router — classifies question into 7 intents before SmartZoneAssembler |

### 9.2 Supported languages matrix (as of ADR-0042)

| Pass | Java / Spring | Python / FastAPI | TypeScript / Next.js |
|---|---|---|---|
| AnnotationPass | @Transactional, @Cacheable, @Controller | @app.get, @login\_required | @Controller, @UseGuards, export function |
| StorageTargetPass | jOOQ Tables.*, Hibernate @Table | SQLAlchemy \_\_tablename\_\_ | Drizzle pgTable(), Prisma model |
| SchemaMigrationPass | Flyway SQL, Liquibase | Alembic op.create\_table | Prisma migrate, Drizzle schema |
| ClientCallPass | RestTemplate, WebClient, @FeignClient | requests, httpx | fetch, axios, useSWR |
| TestCoveragePass | JUnit 4/5, TestNG | pytest, unittest | Jest, Vitest, Mocha |

### 9.3 Ops notes

- All passes individually disableable via `BRAIN_SKIP_<PASS_NAME>=true` env var.
- Cost guard: job halts if cumulative cost exceeds `BRAIN_JOB_BUDGET_USD` (default $0.50).
- `edges_reverse` materialized view refreshed with `REFRESH MATERIALIZED VIEW CONCURRENTLY`.
- Intent router adds ~$0.001/query; disable with `ENABLE_INTENT_ROUTER=false`.
