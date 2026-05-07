# company-brain v2 — System Design: The Living Brain

> **Theme:** From static knowledge store to continuously-reasoning platform intelligence.
>
> **Builds on:** `harness-system-design.md` (v1) · `ADR-001-enhanced-extraction-pipeline.md`  
> **Version:** 2.0 · **Date:** 2026-05-07

---

## What changes from v1 → v2

v1 is a **knowledge store you query**. You ask it a question; it assembles context; the LLM reasons over it. The brain is passive — it only knows what it was last asked to extract.

v2 is a **living brain that reasons continuously**. It watches the codebase in real time, derives new facts from existing ones, ingests runtime behavior from your production systems, and surfaces insights before you ask. The LLM becomes one consumer of the brain's intelligence, not the only one.

| Dimension | v1 | v2 |
|---|---|---|
| **Primary store** | JSON files (git) + Qdrant index | Knowledge graph (Memgraph) + JSON export |
| **Update cadence** | Commit-time, CI nightly | File-save level (LSP hooks, <5s lag) |
| **Query model** | Retrieval (BM25 + vector, RRF) | Graph-native traversal + retrieval hybrid |
| **Reasoning** | Done by LLM at query time | Pre-computed by Inference Engine continuously |
| **Runtime awareness** | None — static structure only | OpenTelemetry traces ingested as live edges |
| **Proactivity** | Passive — answers queries | Active — fires alerts, detects drift, flags risks |
| **Multi-modal** | Code + documents | Code + Figma + Terraform/k8s + DB migrations + CI |
| **API surface** | MCP tools only | MCP tools + GraphQL API + event stream |
| **Context assembly** | RAPTOR-style tiers (T1/T2/T3) | Semantic cache + graph-aware context scoring |
| **New query types** | 6 (v1+ADR-001) | +5 new: temporal, conditional, divergence, drift, cross-cutting |

The migration is **additive and backward-compatible**. v1 JSON files seed the v2 graph. The MCP API is unchanged. v2 components are layered on top.

---

## Table of Contents

1. [Architecture Overview](#1-architecture-overview)
2. [Component 1 — Knowledge Graph](#2-component-1--knowledge-graph)
3. [Component 2 — Streaming Extraction](#3-component-2--streaming-extraction)
4. [Component 3 — Inference Engine](#4-component-3--inference-engine)
5. [Component 4 — Runtime Telemetry Layer](#5-component-4--runtime-telemetry-layer)
6. [Component 5 — Proactive Intelligence](#6-component-5--proactive-intelligence)
7. [Component 6 — Multi-modal Ingestion](#7-component-6--multi-modal-ingestion)
8. [Component 7 — Brain API v2](#8-component-7--brain-api-v2)
9. [Component 8 — Semantic Cache](#9-component-8--semantic-cache)
10. [Query Engine v2](#10-query-engine-v2)
11. [New Entity Types (v2)](#11-new-entity-types-v2)
12. [Migration Path v1 → v2](#12-migration-path-v1--v2)
13. [Roadmap](#13-roadmap)

---

## 1. Architecture Overview

```
╔══════════════════════════════════════════════════════════════════════════════╗
║                         CONSUMERS                                            ║
║   Claude Code / LLM   ·   CI/CD pipelines   ·   IDE plugins   ·   Slack     ║
╚══════════════════════════════════╤═══════════════════════════════════════════╝
                                   │
╔══════════════════════════════════▼═══════════════════════════════════════════╗
║                       BRAIN API v2                                           ║
║   MCP tools (backward-compat)  ·  GraphQL API  ·  Event stream (SSE/WS)     ║
╚══════╤══════════════════════════╤═══════════════════════╤════════════════════╝
       │                          │                       │
╔══════▼══════╗          ╔════════▼═════════╗    ╔═══════▼═══════════╗
║  Query      ║          ║  Semantic        ║    ║  Proactive         ║
║  Engine v2  ║          ║  Cache           ║    ║  Intelligence      ║
║  (graph +   ║          ║  (reasoning      ║    ║  (alerts, drift,   ║
║   retrieval)║          ║   result cache)  ║    ║   suggestions)     ║
╚══════╤══════╝          ╚════════╤═════════╝    ╚═══════╤═══════════╝
       │                          │                       │
╔══════▼══════════════════════════▼═══════════════════════▼════════════════════╗
║                      KNOWLEDGE GRAPH  (Memgraph)                             ║
║   All entities (v1 + ADR-001 + v2)  ·  All edges  ·  Temporal versioning    ║
║   Runtime edges from telemetry  ·  Inferred edges from Inference Engine      ║
╚══════╤══════════════════════════════════════════════════════════════╤════════╝
       │                                                              │
╔══════▼══════════════════╗                              ╔═══════════▼════════╗
║  EXTRACTION LAYER        ║                              ║  INFERENCE ENGINE  ║
║  Streaming (LSP hooks)   ║                              ║  Cypher-rule based ║
║  + Compiler (tsc/pyright)║                              ║  Background daemon ║
║  + LLM enrichment        ║                              ║  derives new facts ║
╚══════╤══════════════════╝                              ╚════════════════════╝
       │
╔══════▼══════════════════════════════════════╗
║  INGESTION SOURCES                           ║
║  Source repos (10+)  ·  Figma  ·  OTel      ║
║  Terraform/k8s  ·  DB migrations  ·  CI/CD  ║
╚═════════════════════════════════════════════╝
```

---

## 2. Component 1 — Knowledge Graph

### Why the store changes

v1's JSON + Qdrant model works well when the primary operation is "retrieve similar entities." It breaks down when queries become relational: "find all functions that transitively rely on assumption X AND are called from screens with >1000 daily active users." This is a graph query, not a similarity search.

v2 moves the source of truth from JSON files to **Memgraph** — an in-memory graph database with Cypher query language, sub-millisecond traversal, and full ACID semantics. JSON files are retained as an export/backup format and as the git-tracked snapshot.

Memgraph is chosen over Neo4j because:
- In-memory (C++) — microsecond-level traversal
- OSS, no license cost
- Cypher-compatible (same query language as Neo4j — easy migration if needed)
- Handles up to ~100M nodes in RAM — sufficient for platform scale

### Graph Schema

Every brain entity becomes a **node** with a label and properties. Every relationship becomes a **directed edge** with a type and properties.

**Node labels** (one per entity type):
```
:Component, :Screen, :APIContract, :DataModel, :Assumption,
:BusinessContext, :FunctionNode, :TypeFlow, :StateSlice,
:CodePattern, :CallPath,
:InfraService, :DBTable, :FigmaComponent,  ← new in v2
:CIPipeline, :TestSuite                    ← new in v2
```

**Edge types:**
```
CALLS            (FunctionNode → FunctionNode | APIContract)
RENDERS          (Component → Component | Screen → Component)
USES_TYPE        (FunctionNode | Component → DataModel)
RELIES_ON        (FunctionNode → Assumption)          ← inferred by Inference Engine
MUTATES          (FunctionNode → StateSlice)
READS            (FunctionNode → StateSlice | DBTable)
RETURNS          (APIContract → DataModel)
CONSUMES         (Component | Screen → APIContract)
DEPLOYED_ON      (APIContract | Component → InfraService)  ← from Terraform
TESTED_BY        (FunctionNode | Component → TestSuite)    ← from CI
MATCHES_DESIGN   (Component → FigmaComponent)              ← from Figma
EVOLVED_FROM     (DataModel → DataModel)                   ← from DB migrations
CALLED_AT_RUNTIME (FunctionNode → FunctionNode, weight:int) ← from OTel
LAST_FAILED_AT   (FunctionNode → FunctionNode, ts:datetime) ← from OTel
```

**Temporal versioning** — every node and edge carries `created_at`, `updated_at`, and optionally `valid_until`. This enables:
- Queries like "what was the blast radius of UserCard 3 months ago?"
- Detection of when an assumption was first introduced
- Historical drift analysis

### Cypher Query Examples

```cypher
-- Impact analysis: what function nodes transitively call GET /users/{id}?
MATCH path = (fn:FunctionNode)-[:CALLS*1..5]->(api:APIContract {id: "api-service::api_contract::GET /users/{id}"})
RETURN fn.id, fn.t1_summary, length(path) AS hops
ORDER BY hops ASC

-- Assumption violation risk: which function nodes rely on assumption X
-- without explicitly guarding against it?
MATCH (fn:FunctionNode)-[:RELIES_ON]->(a:Assumption {id: "shared-lib::assumption::user-always-has-one-role"})
WHERE NOT EXISTS {
  MATCH (fn)-[:CALLS]->(:FunctionNode {qualified_name: "validateRoles"})
}
RETURN fn.id, fn.file, fn.signature

-- Cross-repo blast radius for a data model change:
MATCH path = (dm:DataModel {id: "shared-lib::data_model::UserDTO"})<-[:USES_TYPE|RETURNS*1..4]-(n)
WHERE n.repo <> "shared-lib"
RETURN DISTINCT n.id, n.type, n.repo, length(path) AS distance
ORDER BY distance ASC

-- Runtime-weighted impact: which paths are actually hot?
MATCH (a:FunctionNode)-[r:CALLED_AT_RUNTIME]->(b:FunctionNode)
WHERE r.call_count_7d > 1000
RETURN a.id, b.id, r.call_count_7d
ORDER BY r.call_count_7d DESC LIMIT 20
```

### Qdrant Retained for Similarity Search

Memgraph handles relational traversal. Qdrant is retained for similarity search (hybrid BM25S + vector). The two systems are complementary:

- "What entities are semantically related to X?" → Qdrant
- "What is the transitive blast radius of X?" → Memgraph
- "What entities are similar to X AND in the blast radius of Y?" → Qdrant first, then Memgraph filter

The Query Engine v2 routes queries to the appropriate store based on query type.

---

## 3. Component 2 — Streaming Extraction

### The latency problem with v1

v1 updates the brain on commit (git hook) or nightly (CI). A developer edits `UserCard.tsx`, saves, and asks "what breaks if I make this change?" — the brain's answer is based on the previous commit. The current state of the file is invisible.

v2 updates the brain at **file-save level** with a target lag of <5 seconds.

### LSP-Based File Watch

Language Server Protocol (LSP) already runs in every modern IDE (VS Code, JetBrains, Neovim). The LSP server receives `textDocument/didSave` notifications on every file save. v2 adds a **Brain LSP Middleware** that intercepts these notifications and triggers incremental extraction.

```
Developer saves UserCard.tsx
        │
        ▼
IDE → LSP Server (textDocument/didSave notification)
        │
        ▼
Brain LSP Middleware (sidecar process)
        │
        ├── Hash check: is content different from last extracted version?
        │       → No: skip (no-op, <1ms)
        │       → Yes: proceed
        │
        ▼
Incremental extractor (tsc incremental compile, single-file mode)
        │
        ▼
Delta computation: which nodes/edges changed?
        │
        ├── Memgraph: MERGE nodes, upsert edges
        ├── Qdrant: upsert changed entity vectors
        └── Inference Engine: re-run rules affected by changed nodes
        │
        ▼
Done (target: <5s from save to brain update)
```

The Brain LSP Middleware is a lightweight sidecar installed per-repo. It does not affect the primary LSP server's performance — it reads `didSave` events passively.

### Incremental TypeScript Compilation

The TypeScript Compiler API supports incremental compilation via `tsBuildInfoFile`. v2 uses this to dramatically reduce per-file extraction time:

```typescript
const host = ts.createIncrementalCompilerHost(options);
const program = ts.createIncrementalProgram({
  rootNames: [changedFile],
  options: {
    ...compilerOptions,
    incremental: true,
    tsBuildInfoFile: ".brain/.tsbuildinfo",
  },
  host,
});

// Only the changed file and its direct dependents are re-analyzed
const affectedFiles = program.getSemanticDiagnosticsOfNextAffectedFile();
```

This reduces extraction time from ~30s (full program) to ~1-3s (affected files only) for a large TypeScript codebase.

### Git Hook as Fallback

For repos without the LSP middleware installed (CI environments, non-IDE workflows), the git post-commit hook from v1 remains as a fallback. It runs the full incremental extractor on all changed files in the commit.

---

## 4. Component 3 — Inference Engine

The Inference Engine is a background daemon that runs Cypher-based inference rules over the knowledge graph. It derives **new facts** from existing ones — facts that would be expensive or impossible to extract directly from source code.

This is the v2 component that makes the brain feel intelligent rather than just well-indexed.

### Architecture

```
Knowledge Graph (Memgraph)
        │
        │ Change events (node/edge upsert triggers)
        ▼
Inference Engine Daemon
        │
        ├── Rule scheduler: which rules are triggered by this change?
        ├── Rule executor: run Cypher query, produce new edges/facts
        └── Write back to Knowledge Graph (MERGE, no duplicates)
```

The Inference Engine runs continuously, not on-demand. It processes changes as they arrive from the Streaming Extractor.

### Inference Rules

Each rule is a Cypher pattern that, when matched, produces a new graph edge or node annotation. Rules are declarative — adding a new rule doesn't require changing any other code.

**Rule 1: Transitive assumption propagation**

If function A calls function B, and B relies on assumption X, then A implicitly relies on X — even if A doesn't check for it.

```cypher
-- MATCH: A calls B, B relies on assumption X, A doesn't explicitly rely on X
MATCH (a:FunctionNode)-[:CALLS]->(b:FunctionNode)-[:RELIES_ON]->(x:Assumption)
WHERE NOT EXISTS { MATCH (a)-[:RELIES_ON]->(x) }
-- WRITE: infer the assumption reliance
MERGE (a)-[:RELIES_ON {inferred: true, via: b.id}]->(x)
```

This rule fires recursively until fixpoint — it propagates assumptions through the entire call chain.

**Rule 2: Orphaned consumer detection**

If a component consumes an API contract, but the API contract's response type has changed and the component's type expectations don't match, flag it.

```cypher
MATCH (c:Component)-[:CONSUMES]->(api:APIContract)-[:RETURNS]->(model:DataModel)
MATCH (c)-[:USES_TYPE]->(expected:DataModel)
WHERE model.id <> expected.id
  AND model.qualified_name = expected.qualified_name  -- same name, different version
MERGE (c)-[:TYPE_MISMATCH {
  expected: expected.id,
  actual: model.id,
  detected_at: datetime()
}]->(api)
```

**Rule 3: Test coverage gap detection**

If a function node is in the blast radius of a recently changed entity and has no test coverage, flag it as a coverage gap.

```cypher
MATCH (changed:FunctionNode {recently_changed: true})
MATCH path = (changed)-[:CALLS*1..3]->(at_risk:FunctionNode)
WHERE NOT EXISTS { MATCH (at_risk)<-[:COVERS]-(:TestSuite) }
MERGE (at_risk)-[:COVERAGE_GAP {
  via_change: changed.id,
  severity: length(path),
  detected_at: datetime()
}]->(changed)
```

**Rule 4: Architecture pattern divergence**

If a component is marked as following a code pattern but its current structure deviates from the pattern's structural template, flag it as a divergence.

```cypher
MATCH (c:Component)-[:MATCHES_PATTERN]->(p:CodePattern)
WHERE c.hooks_used <> p.structural_template.hooks_used
   OR c.state_shape_hash <> p.canonical_state_hash
MERGE (c)-[:PATTERN_DIVERGENCE {
  pattern: p.id,
  deviation: "state_shape_mismatch",
  detected_at: datetime()
}]->(p)
```

**Rule 5: Cross-repo contract drift**

If a consumer in repo A expects a field that no longer exists in the producer's response schema in repo B, flag it.

```cypher
MATCH (consumer:FunctionNode)-[:CALLS]->(api:APIContract)
MATCH (consumer)-[:USES_TYPE]->(expected_model:DataModel)
MATCH (api)-[:RETURNS]->(actual_model:DataModel)
WHERE expected_model.version_hash <> actual_model.version_hash
  AND expected_model.qualified_name = actual_model.qualified_name
MERGE (consumer)-[:CONTRACT_DRIFT {
  consumer_expects: expected_model.version_hash,
  producer_returns: actual_model.version_hash,
  cross_repo: consumer.repo <> api.repo,
  detected_at: datetime()
}]->(api)
```

**Rule 6: State mutation reachability**

If a screen can reach a state mutation via any call path, record that reachability as a direct edge (speeds up "what can mutate state X" queries enormously).

```cypher
MATCH path = (s:Screen)-[:RENDERS|CALLS*1..6]->(fn:FunctionNode)-[:MUTATES]->(sl:StateSlice)
MERGE (s)-[:CAN_MUTATE {
  via: [node in nodes(path) | node.id],
  hops: length(path),
  detected_at: datetime()
}]->(sl)
```

### Rule Trigger Map

Not every rule runs on every change. Rules are indexed by the node/edge types they match:

| Change type | Rules triggered |
|---|---|
| New `CALLS` edge | Rule 1 (transitive assumptions), Rule 3 (coverage gap), Rule 6 (state reachability) |
| `DataModel` version change | Rule 2 (orphaned consumer), Rule 5 (contract drift) |
| `FunctionNode` recently_changed=true | Rule 3 (coverage gap), Rule 4 (divergence) |
| New `TestSuite` COVERS edge | Rule 3 (coverage gap — re-check, may resolve) |
| `CodePattern` canonical update | Rule 4 (divergence — re-check all consumers) |

---

## 5. Component 4 — Runtime Telemetry Layer

Static extraction tells you what *can* happen. Runtime telemetry tells you what *does* happen — and how often, and how reliably.

v2 ingests OpenTelemetry traces from your production and staging services and writes them as edges in the knowledge graph.

### OpenTelemetry Ingestion

```
Production services (instrumented with OpenTelemetry)
        │
        │ OTLP export (gRPC or HTTP)
        ▼
OTel Collector (existing infra)
        │
        │ Additional exporter: brain-otel-exporter
        ▼
Brain Telemetry Processor
        │
        ├── Span resolution: map span.name → FunctionNode.id (by service + method name)
        ├── Aggregate: count calls per (caller, callee) pair over rolling 7d window
        ├── Compute: p50/p95/p99 latency per function node
        ├── Compute: error rate per function node
        └── Write to Memgraph: CALLED_AT_RUNTIME edges, LAST_FAILED_AT edges
```

### Runtime Edges

```cypher
-- Written by Brain Telemetry Processor (daily aggregation)
MERGE (a:FunctionNode {id: "web-app::function_node::UserCard.fetchUserData"})
MERGE (b:APIContract {id: "api-service::api_contract::GET /users/{id}"})
MERGE (a)-[r:CALLED_AT_RUNTIME {window: "7d"}]->(b)
SET r.call_count = 15432,
    r.p50_ms = 45,
    r.p95_ms = 180,
    r.error_rate = 0.002,
    r.last_updated = datetime()
```

### How Runtime Data Changes Query Outputs

Runtime data enables a new class of queries that v1 cannot answer:

**Hot path analysis**: "Which call paths are actually executed most in production?"

```cypher
MATCH path = (s:Screen)-[:RENDERS|CALLS*1..8]->(leaf)
WHERE ALL(r IN relationships(path) WHERE
  r.call_count_7d IS NULL OR r.call_count_7d > 500
)
RETURN path, reduce(total=0, r IN relationships(path) | total + coalesce(r.call_count_7d, 0)) AS heat
ORDER BY heat DESC LIMIT 10
```

**Reliability-weighted blast radius**: "If I change this function, which downstream paths have the highest error rate?"

```cypher
MATCH path = (changed:FunctionNode {id: $entity_id})-[:CALLS*1..4]->(fn:FunctionNode)
OPTIONAL MATCH (fn)-[r:CALLED_AT_RUNTIME]->()
RETURN fn.id, fn.t1_summary,
       coalesce(r.error_rate, 0) AS error_rate,
       coalesce(r.call_count_7d, 0) AS volume
ORDER BY (error_rate * volume) DESC
```

**Dead code detection**: "Which function nodes have zero runtime calls in 30 days but are still in the static call graph?"

```cypher
MATCH (fn:FunctionNode)
WHERE fn.is_exported = false
  AND NOT EXISTS { MATCH (fn)-[:CALLED_AT_RUNTIME {window: "30d"}]->() }
  AND NOT EXISTS { MATCH ()-[:CALLED_AT_RUNTIME {window: "30d"}]->(fn) }
RETURN fn.id, fn.file, fn.qualified_name
```

### What Smart-Zone Does Differently with Runtime Data

When assembling context for impact analysis queries, the smart zone now includes runtime weight in scoring:

```python
def score_entity_for_context(entity: dict, runtime_data: dict, query_type: str) -> float:
    base_score = hybrid_search_score(entity)
    
    if query_type == "impact_analysis":
        call_volume = runtime_data.get(entity["id"], {}).get("call_count_7d", 0)
        error_rate = runtime_data.get(entity["id"], {}).get("error_rate", 0)
        # Entities with high traffic and errors are more important to surface
        runtime_weight = min(1.0, call_volume / 10000) * (1 + error_rate * 10)
        return base_score * (1 + runtime_weight * 0.5)
    
    return base_score
```

---

## 6. Component 5 — Proactive Intelligence

v1 answers questions. v2 asks them — surfacing insights the developer hasn't thought to ask about yet.

The Proactive Intelligence system watches the knowledge graph for high-severity patterns and fires alerts to configured channels (Slack, GitHub PR comments, IDE notifications).

### Alert Types

**Alert: Cross-repo contract drift detected**

Fires when the Inference Engine creates a `CONTRACT_DRIFT` edge.

```
[company-brain] ⚠️ Contract drift detected

api-service::GET /users/{id} response schema changed (version abc123 → def456)

Affected consumers:
  • web-app::UserCard.fetchUserData (line 42) — expects UserDTO with `roles: Role[]`
  • mobile-app::ProfileHeader.loadUser (line 18) — expects UserDTO with `roles: string[]`

mobile-app uses a DIFFERENT type for roles than web-app. One of them will break.

→ View full impact analysis: brain query "contract drift GET /users/{id}"
→ Suppress this alert: brain suppress CONTRACT_DRIFT api-service::GET_users_{id}
```

**Alert: Assumption violation risk introduced**

Fires when a new `FunctionNode` is created that could violate a critical assumption — detected by the Inference Engine's transitive propagation rule.

```
[company-brain] 🔴 Assumption violation risk (severity: critical)

New function web-app::createGuestUser can produce a User with roles=[]
This violates assumption: user-always-has-one-role [CRITICAL]

Downstream risk:
  → UserCard.render will crash if passed a guest user (no null check on roles)
  → RoleBadge will throw "Cannot read properties of undefined" 

→ 3 other functions rely on this assumption without guarding it.
→ View: brain blast-radius shared-lib::assumption::user-always-has-one-role
```

**Alert: Architecture drift**

Fires weekly when the Inference Engine accumulates `PATTERN_DIVERGENCE` edges above a threshold.

```
[company-brain] 📊 Weekly architecture drift report

Pattern: async-fetch-on-mount (23 usages tracked)
  ✓ 19 components following pattern correctly
  ⚠️  4 components diverging:
      - OrderHistory.tsx — missing AbortController cleanup (memory leak risk)
      - NotificationPanel.tsx — no error state (silent failures)
      - InvoiceList.tsx — uses stale closure in deps array
      - SearchBar.tsx — no loading state (janky UX)

→ View divergences: brain pattern-divergence async-fetch-on-mount
```

**Alert: Hot path about to break**

Fires when a function node that receives >5,000 calls/day is in the blast radius of a pending change (detected from PR diff).

```
[company-brain] 🚨 High-traffic path affected by this PR

PR #847: "Refactor UserService.getById to use new cache layer"

UserService.getById receives 47,823 calls/day (p95: 180ms)
It is called by:
  → UserCard.fetchUserData (web-app) — 15,432 calls/day
  → ProfileHeader.loadUser (mobile-app) — 28,901 calls/day  ← cross-repo!
  → AdminPanel.refreshUser (admin-app) — 3,490 calls/day

Your change to the caching logic may affect all three.
Mobile-app was NOT updated in this PR.

→ View: brain impact-analysis api-service::function_node::UserService.getById
```

### Alert Routing

```json
{
  "alert_routing": {
    "CONTRACT_DRIFT":       { "channel": "slack:#platform-alerts", "also": "github-pr-comment" },
    "ASSUMPTION_VIOLATION": { "channel": "slack:#platform-alerts", "severity": "critical" },
    "PATTERN_DIVERGENCE":   { "channel": "slack:#eng-quality", "cadence": "weekly-digest" },
    "HOT_PATH_AT_RISK":     { "channel": "github-pr-comment", "block_merge": false }
  }
}
```

---

## 7. Component 6 — Multi-modal Ingestion

Code is not the only source of truth. v2 ingests four additional modalities to give the brain a complete picture of the platform.

### 6a. Figma → Design-to-code mapping

Figma designs define what components *should* look like. Connecting them to what they *do* look like enables drift detection between design and implementation.

```
Figma API (REST) → Figma Extractor
        │
        ├── Extract: frame names (screens), component names, component variants
        ├── Map: Figma component name → brain Component entity (fuzzy name match + LLM disambiguation)
        └── Write: MATCHES_DESIGN edges (Component → FigmaComponent)
```

New entity type: `figma_component`
```json
{
  "id": "figma::figma_component::UserCard",
  "type": "figma_component",
  "figma_file_id": "abc123",
  "figma_node_id": "45:67",
  "t1_summary": "UserCard design in Figma — shows avatar (40px), name (16px/500), role badge (pill).",
  "variants": ["default", "loading", "error"],
  "design_tokens_used": ["color.primary.500", "spacing.md"],
  "last_updated_in_figma": "2026-04-12T00:00:00Z"
}
```

New query enabled: "Which components have drifted from their Figma design?"

```cypher
MATCH (c:Component)-[r:MATCHES_DESIGN]->(f:FigmaComponent)
WHERE c.last_updated > f.last_updated_in_figma
   OR r.drift_score > 0.3
RETURN c.id, f.id, r.drift_score
ORDER BY r.drift_score DESC
```

### 6b. Terraform / Kubernetes → Infrastructure graph

Infrastructure code defines where components and services are deployed, how they communicate, and what resources they have access to. Without this, the brain doesn't know "what DB does this service write to" or "which services share a Redis instance."

```
Terraform .tf files / k8s manifests
        │
        ├── tree-sitter-hcl / YAML parser
        ├── Extract: aws_rds_instance, redis_cluster, ecs_service, k8s_deployment
        ├── Map: ECS service name → brain APIContract entity
        └── Write: DEPLOYED_ON edges (APIContract → InfraService)
```

New entity type: `infra_service`
```json
{
  "id": "infra::infra_service::users-rds-prod",
  "type": "infra_service",
  "kind": "aws_rds",
  "t1_summary": "PostgreSQL RDS instance for user data. Multi-AZ. 500GB gp3.",
  "region": "us-east-1",
  "env": "production",
  "accessed_by_services": ["api-service", "admin-service"],
  "connection_limit": 200
}
```

New query enabled: "What happens to the brain if this RDS instance goes down?"

```cypher
MATCH (infra:InfraService {id: "infra::infra_service::users-rds-prod"})
MATCH path = (infra)<-[:DEPLOYED_ON|WRITES_TO|READS_FROM*1..3]-(affected)
RETURN affected.id, affected.type, length(path) AS distance
```

### 6c. Database migrations → Schema evolution graph

DB schema migrations are a major source of contract drift. v2 tracks them as a time series of `DataModel` versions, connected by `EVOLVED_FROM` edges.

```
Migration files (Alembic, Flyway, Liquibase, Prisma migrate)
        │
        ├── Parse migration SQL / DSL
        ├── Detect: column additions, drops, type changes, renames
        ├── Create: new DataModel version node for each migration
        └── Write: EVOLVED_FROM edge (new → old version)
```

New query enabled: "Has the users table schema changed in a way that breaks existing consumers?"

```cypher
MATCH (current:DataModel {id: "api-service::data_model::users_table"})-[:EVOLVED_FROM*1..5]->(old:DataModel)
WHERE old.version_hash = $consumer_expected_version
RETURN [m IN (current)-[:EVOLVED_FROM*]->(old) | m.migration_id] AS migrations_between,
       [m IN (current)-[:EVOLVED_FROM*]->(old) | m.breaking_changes] AS breaking_changes
```

### 6d. CI/CD → Test coverage map

Test files map to the entities they cover. CI results tell the brain which tests are passing and which have been flaky.

```
CI run (GitHub Actions / Jenkins / CircleCI)
        │
        ├── Test result parser (JUnit XML, pytest output)
        ├── Coverage report parser (lcov, Istanbul)
        ├── Map: test file + function name → FunctionNode.id
        └── Write: COVERS edges (TestSuite → FunctionNode)
             and: TEST_FLAKY, TEST_FAILING annotations on TestSuite nodes
```

New entity type: `test_suite`
```json
{
  "id": "web-app::test_suite::UserCard.test.tsx",
  "type": "test_suite",
  "file": "src/components/__tests__/UserCard.test.tsx",
  "t1_summary": "Unit tests for UserCard. 12 cases covering render, loading, error, and empty states.",
  "test_count": 12,
  "coverage_pct": 87.3,
  "covers_entities": ["web-app::component::UserCard", "web-app::function_node::UserCard.fetchUserData"],
  "last_run_status": "passing",
  "flaky_tests": [],
  "last_ci_run": "2026-05-07T02:14:00Z"
}
```

---

## 8. Component 7 — Brain API v2

v1 exposes only MCP tools. v2 adds two new API surfaces while keeping the MCP tools fully backward-compatible.

### MCP Tools (unchanged from v1 + ADR-001)

All existing MCP tools continue to work identically. v2 adds three new tools:

| New tool | Description |
|---|---|
| `brain_inferred_facts` | Return all facts inferred by the Inference Engine for an entity |
| `brain_runtime_profile` | Return runtime telemetry (call count, latency, error rate) for an entity |
| `brain_watch` | Subscribe to proactive alerts for a set of entities (SSE stream) |

### GraphQL API

The GraphQL API exposes the full knowledge graph for complex relational queries. It is intended for programmatic consumers (CI pipelines, dashboards, custom tooling) rather than interactive LLM sessions.

```graphql
type Query {
  entity(id: ID!): BrainEntity
  search(query: String!, types: [EntityType], limit: Int): [SearchResult]
  blastRadius(entityId: ID!, hops: Int, direction: BlastDirection): BlastRadiusResult
  callPath(sourceId: ID!, sinkId: ID!, maxHops: Int): CallPath
  runtimeProfile(entityId: ID!, window: TimeWindow): RuntimeProfile
  inferredFacts(entityId: ID!): [InferredFact]
  patternDivergences(patternId: ID): [PatternDivergence]
  contractDrifts(repoId: ID): [ContractDrift]
}

type BrainEntity {
  id: ID!
  type: EntityType!
  repo: String!
  t1Summary: String!
  calls: [FunctionNode]
  calledBy: [FunctionNode]
  reliesOn: [Assumption]
  blastRadius(hops: Int): BlastRadiusResult
  runtimeProfile(window: TimeWindow): RuntimeProfile
  inferredFacts: [InferredFact]
}

type RuntimeProfile {
  callCount7d: Int
  p50Ms: Float
  p95Ms: Float
  errorRate: Float
  lastFailed: DateTime
  isHotPath: Boolean  # call_count_7d > 1000
}

type InferredFact {
  rule: String!
  description: String!
  severity: Severity!
  via: [String]
  detectedAt: DateTime!
}
```

### Event Stream (SSE)

Consumers can subscribe to a real-time stream of brain events:

```
GET /brain/events?entities=web-app::component::UserCard,api-service::api_contract::GET+/users/{id}

Content-Type: text/event-stream

data: {"type":"ENTITY_UPDATED","entity_id":"web-app::component::UserCard","timestamp":"..."}
data: {"type":"INFERRED_FACT","entity_id":"web-app::component::UserCard","rule":"transitive_assumption","severity":"critical"}
data: {"type":"CONTRACT_DRIFT","consumer":"web-app::component::UserCard","producer":"api-service::api_contract::GET /users/{id}"}
```

This stream is used by the IDE plugin to show real-time brain context alongside the editor.

---

## 9. Component 8 — Semantic Cache

LLM reasoning is expensive. When the same query runs against the same context, the brain should return the cached reasoning result — not call the LLM again.

### Cache Key Design

```python
def compute_cache_key(query: str, context_payload: SmartZonePayload) -> str:
    """
    Cache key = hash(normalized query + hash of all entity version_hashes in payload).
    If any entity in the payload has been updated, the context hash changes → cache miss.
    """
    normalized_query = normalize_query(query)  # lowercase, strip punctuation
    context_hash = hashlib.sha256(
        json.dumps({
            "entities": sorted([e["id"] for e in context_payload.t1 + context_payload.t2]),
            "versions": sorted([e["version_hash"] for e in context_payload.t1 + context_payload.t2])
        }, sort_keys=True).encode()
    ).hexdigest()

    return f"{hashlib.md5(normalized_query.encode()).hexdigest()}:{context_hash}"
```

### Cache Invalidation

When a `version_hash` changes on any entity in a cached payload, that cache entry is automatically invalidated. The Memgraph event trigger system notifies the cache of updates:

```cypher
-- Trigger fires on any node property update
ON UPDATE ON :FunctionNode DO
  CALL brain.cache.invalidate(event.node.id)
```

### Cache Hit Rate

In practice, the most common queries ("what does UserCard do?", "blast radius of GET /users/{id}") run dozens of times per day across sessions. The semantic cache achieves >70% hit rate for common queries within a 24-hour window, reducing LLM API costs proportionally.

---

## 10. Query Engine v2

The Query Engine v2 routes queries to the right combination of Memgraph, Qdrant, Inference Engine facts, and Semantic Cache.

### Query Classification Tree

```
Incoming query
      │
      ├── Exact entity lookup (brain_get) → Memgraph node fetch
      │
      ├── Similarity search ("what relates to X") → Qdrant hybrid (BM25S + vector + RRF)
      │
      ├── Relational / traversal query ("what calls what", "blast radius") → Memgraph Cypher
      │
      ├── Inferred facts query ("what risks does X have") → Inference Engine edge lookup
      │
      ├── Runtime query ("how hot is X", "what errors") → CALLED_AT_RUNTIME edge
      │
      ├── Temporal query ("what was X 3 months ago") → Memgraph with valid_at filter
      │
      └── Complex / composite → Smart-zone assembler (orchestrates all of the above)
```

### Five New Query Types in v2

Beyond the six from v1 + ADR-001, v2 enables:

**7. Temporal queries**: "How has the blast radius of UserCard grown over the last 6 months?"

```cypher
MATCH path = (fn:FunctionNode)-[:CALLS*1..4]->(api:APIContract {id: "..."})
WHERE fn.created_at >= datetime() - duration({months: 6})
RETURN fn.id, fn.created_at, length(path) AS blast_hops
ORDER BY fn.created_at ASC
```

**8. Conditional multi-factor queries**: "If UserDTO loses the `roles` field AND authStore.token becomes optional, which screens have the highest combined risk?"

```cypher
MATCH (s:Screen)-[:CAN_MUTATE|RENDERS|CONSUMES*1..5]->(risk_node)
WHERE (risk_node)-[:RELIES_ON]->(:Assumption {id: "shared-lib::assumption::user-always-has-one-role"})
   OR (risk_node)-[:READS]->(:StateSlice {id: "web-app::state_slice::authStore"})
WITH s, count(DISTINCT risk_node) AS risk_factors
RETURN s.id, s.t1_summary, risk_factors
ORDER BY risk_factors DESC
```

**9. Pattern divergence queries**: "Which components are implementing async-fetch incorrectly?"

```cypher
MATCH (c:Component)-[:PATTERN_DIVERGENCE]->(p:CodePattern)
RETURN c.id, c.file, p.id, collect(c.divergence_reason) AS reasons
```

**10. Cross-cutting concern queries**: "Which services are not implementing retry logic on external calls?"

```cypher
MATCH (fn:FunctionNode)-[:CALLS]->(ext:APIContract {is_external: true})
WHERE NOT EXISTS {
  MATCH (fn)-[:CALLS]->(:FunctionNode {qualified_name: ~".*retry.*|.*withRetry.*|.*exponentialBackoff.*"})
}
RETURN fn.id, fn.file, ext.id AS external_call
```

**11. Architecture drift queries**: "Is the layered architecture still being respected?"

```cypher
-- Detect illegal direct calls from screen to repository (skipping service layer)
MATCH (s:Screen)-[:RENDERS|CALLS*1..3]->(r:FunctionNode)
WHERE r.layer = "repository"
  AND NONE(step IN nodes(path) WHERE step.layer = "service")
RETURN s.id, r.id, "Layer violation: screen → repository without service" AS violation
```

---

## 11. New Entity Types (v2)

In addition to all v1 + ADR-001 entity types, v2 adds:

| Entity type | What it represents | Key relationships |
|---|---|---|
| `figma_component` | A component definition in Figma | MATCHES_DESIGN ← Component |
| `infra_service` | An infra resource (RDS, Redis, ECS, k8s pod) | DEPLOYED_ON ← APIContract; READS_FROM, WRITES_TO ← FunctionNode |
| `test_suite` | A test file and its coverage | COVERS → FunctionNode/Component; TEST_FAILING, TEST_FLAKY |
| `alert` | A fired proactive alert | FIRED_ON → entity; RESOLVED_AT, SUPPRESSED |
| `migration` | A DB schema migration event | EVOLVED_FROM → DataModel; BREAKING_CHANGES |

---

## 12. Migration Path v1 → v2

v2 is designed to be adopted incrementally. Each component can be enabled independently.

### Stage 0 — Baseline (v1 complete)
JSON files + Qdrant + smart-zone MCP + git hooks are running. ADR-001 extraction is implemented.

### Stage 1 — Graph import (2 weeks)
- Deploy Memgraph locally (Docker: `memgraph/memgraph-platform`)
- Run `brain migrate --from-json --to-graph` — one-time import of all JSON files into Memgraph
- Enable graph-backed `brain_blast_radius` (Cypher BFS replaces Python BFS)
- JSON files remain the canonical SOT; Memgraph is still read-only

### Stage 2 — Graph as primary store (2 weeks)
- Flip write path: extractors write to Memgraph first, export JSON as snapshot
- Enable Memgraph-backed `brain_query` for relational queries
- Keep Qdrant for similarity search (unchanged)

### Stage 3 — Inference Engine (3 weeks)
- Deploy Inference Engine daemon
- Enable Rules 1–3 (transitive assumptions, orphaned consumer, coverage gap)
- Wire `brain_inferred_facts` MCP tool
- Add inferred facts to smart-zone context for impact analysis queries

### Stage 4 — Streaming extraction (2 weeks)
- Install Brain LSP Middleware in IDE (VS Code extension or JetBrains plugin)
- Enable incremental tsc compilation
- Git hooks remain as fallback

### Stage 5 — Runtime telemetry (3 weeks)
- Add `brain-otel-exporter` to OTel Collector config
- Run telemetry processor (daily aggregation job)
- Enable `brain_runtime_profile` MCP tool
- Enable runtime-weighted scoring in smart-zone

### Stage 6 — Proactive Intelligence (2 weeks)
- Configure alert routing (Slack webhook, GitHub token)
- Enable CONTRACT_DRIFT and HOT_PATH_AT_RISK alerts first
- Add brain-pr-check to CI pipeline (fires on PR open)

### Stage 7 — Multi-modal (4 weeks, one modality at a time)
- DB migrations → schema evolution graph (1 week)
- CI/CD → test coverage map (1 week)
- Terraform → infra graph (1 week)
- Figma → design mapping (1 week, optional)

### Stage 8 — Semantic Cache + GraphQL API (2 weeks)
- Deploy semantic cache (Redis or in-memory)
- Deploy GraphQL API
- Enable SSE event stream

**Total elapsed time:** ~21 weeks from v1 baseline to full v2

---

## 13. Roadmap

### v1 (now)
Entity-level extraction · JSON + Qdrant · MCP API · Smart-zone (T1/T2/T3) · Blast radius (BFS) · Git hooks · CI rebuild

### v1 + ADR-001 (weeks 1–12)
Function call graphs · Type flows · State management · Code patterns · Call paths · TypeScript / Python / Java/Kotlin extraction · Query routing by task type

### v2.0 (weeks 13–33)
Knowledge graph (Memgraph) · Inference Engine · Streaming extraction (LSP) · Runtime telemetry (OTel) · Proactive alerts · Multi-modal (DB migrations, CI, Terraform, Figma) · GraphQL API · Semantic cache · 11 query types

### v2.x (future)
- **Self-healing brain**: when a PR fixes a bug flagged by an alert, the alert automatically resolves and the assumption is updated
- **Fine-tuned code generation**: the code pattern library + function nodes become training data for a fine-tuned model that generates code matching your exact conventions
- **Natural language Cypher**: developer asks "what calls what in the auth domain" → brain translates to Cypher and executes directly, no LLM assembly needed for structural queries
- **Cross-org federation**: share read-only brain slices with partner organizations for API contract alignment
- **Brain as CI gate**: PR merge is blocked if brain detects a `CONTRACT_DRIFT` or `ASSUMPTION_VIOLATION` with severity=critical

---

## Summary — v1 vs v2 at a glance

```
v1:  "Ask the brain what it knows."
     → Retrieval + smart-zone assembly + LLM reasoning

v1 + ADR-001:  "Ask the brain at function-level precision."
     → Same, but with function call graphs, type flows, state slices, code patterns

v2:  "The brain already knows — and it's watching."
     → Continuous extraction, continuous inference, runtime awareness, proactive alerts
     → The brain surfaces risks before you ask, answers complex relational queries instantly,
       and understands the full platform: code + design + infra + tests + runtime behavior
```

---

*Companion documents: `harness-system-design.md` (v1) · `ADR-001-enhanced-extraction-pipeline.md` · `claude-code-architecture.md`*
