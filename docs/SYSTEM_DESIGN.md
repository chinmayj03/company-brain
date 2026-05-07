# Dependency Intelligence Platform — System Design

> This document covers the full architecture for the dependency graph at the core of the Company Brain platform. Read alongside `ADR-001-graph-storage.md`, `ADR-002-ingestion-pipeline.md`, and `ADR-003-multi-tenancy.md`.

---

## 1. What Are We Actually Storing?

Before choosing any technology, we need a precise answer to this question. The word "dependency graph" is vague. Here is what it concretely means for this product.

### 1.1 Node Types

Every entity that can be a source or destination of a dependency is a node.

| Node Type | Example | Source of Truth |
|---|---|---|
| `Service` | `payment-service`, `user-api` | IaC, service registry, K8s |
| `ApiEndpoint` | `POST /payments/charge` | OpenAPI spec, Protobuf |
| `SchemaField` | `charge.amount` (field in request body) | OpenAPI, Protobuf, GraphQL |
| `DatabaseTable` | `users`, `transactions` | ORM models, migration files |
| `DatabaseColumn` | `transactions.user_id` | Migration files, ORM |
| `CodeFile` | `src/services/payment.ts` | Git |
| `CodeFunction` | `PaymentService.charge()` | Static analysis |
| `Team` | `payments-team` | CODEOWNERS, org config |
| `ExternalService` | `stripe`, `sendgrid` | Import analysis, API call detection |

### 1.2 Edge Types

Every dependency, ownership, or relationship between nodes is an edge. Edges are **directed** and **typed**.

| Edge Type | From → To | Example | Source of Truth |
|---|---|---|---|
| `CALLS` | Service → ApiEndpoint | `order-service` calls `POST /payments/charge` | Distributed traces |
| `EXPOSES` | Service → ApiEndpoint | `payment-service` exposes `POST /payments/charge` | OpenAPI spec |
| `CONSUMES_FIELD` | Service → SchemaField | `order-service` reads `charge.amount` | Trace + schema join |
| `READS_TABLE` | Service → DatabaseTable | `payment-service` reads `transactions` | ORM analysis |
| `WRITES_COLUMN` | Service → DatabaseColumn | `payment-service` writes `transactions.user_id` | ORM analysis |
| `OWNS` | Team → Service | `payments-team` owns `payment-service` | CODEOWNERS |
| `DEFINED_IN` | SchemaField → ApiEndpoint | `charge.amount` defined in `POST /payments/charge` | OpenAPI spec |
| `IMPORTS` | Service → ExternalService | `payment-service` imports `stripe` | Package manifests |
| `CHANGED_BY` | Node → Commit | `SchemaField.charge.amount` changed by `abc123` | Git |

### 1.3 Edge Metadata

Every edge carries metadata that makes it useful, not just connectable.

```json
{
  "edge_type": "CALLS",
  "source_id": "order-service",
  "target_id": "payment-service::POST /payments/charge",
  "confidence": 0.97,
  "first_seen": "2024-01-15T10:00:00Z",
  "last_seen": "2024-04-28T09:45:00Z",
  "call_frequency_per_day": 14200,
  "source": "opentelemetry",
  "workspace_id": "ws_abc123"
}
```

The `confidence` field matters. An edge from a static analysis scan has lower confidence than one observed thousands of times in production traces. The product surfaces this to engineers ("this dependency is inferred from code, not confirmed at runtime").

---

## 2. The Real Challenges in Storing This Data

This section is the honest answer to the question "what is actually hard here?" before any technology decisions.

### Challenge 1: Heterogeneous signal quality

Different sources give different levels of certainty. An edge from distributed tracing means two services definitely communicate in production right now. An edge from static analysis means the code could call this function — but maybe only on a code path that never executes. The graph must represent certainty, not just connectivity.

**Implication:** Every edge needs a `confidence` score and a `source` tag. Queries must be able to filter by source or confidence threshold.

### Challenge 2: Staleness and decay

A service-to-service call observed 90 days ago may no longer exist. A schema field observed in an old OpenAPI spec may have been deprecated. The graph goes stale constantly. This is the hardest operational problem — there is no event that says "this dependency no longer exists." It just stops being observed.

**Implication:** Every edge needs a `last_seen` timestamp. A background job decays edges not seen in N days (configurable per source type — traces decay faster than IaC definitions). This is an edge pruning system, not a deletion system.

### Challenge 3: Graph traversal at query time

The killer feature — "what is the blast radius of changing this column?" — is a graph traversal query. Starting from a node, follow all outgoing edges to depth N, collecting all reachable nodes. This is a BFS/DFS over the graph. It is fast in purpose-built graph databases and acceptable in relational databases up to moderate depth and breadth. At very large companies (1000+ services), it can become slow.

**Implication:** The storage choice matters, and traversal queries need to be pre-computed or cached aggressively. See ADR-001.

### Challenge 4: Cross-boundary joins

The most interesting queries join data from multiple sources. "Who owns the service that is most affected by this change?" requires: (1) blast radius traversal → (2) join with ownership edges → (3) return team metadata. This cross-source join is what makes the product valuable and what is architecturally complex.

**Implication:** All metadata must live in one queryable store. You cannot answer this question if blast radius data is in one database and ownership is in another.

### Challenge 5: Multi-tenant isolation with per-company encryption

Each company's graph is entirely private and must never leak to another company's query. Additionally, enterprise customers will demand that their metadata be encrypted with their own keys (BYOK — bring your own key).

**Implication:** Every row in every table is tagged with a `workspace_id`. Row-level security enforces isolation at the database layer. Encryption at the column level (using pgcrypto or application-level AES-256) handles the BYOK case.

### Challenge 6: Code never leaves the customer's environment

The metadata agent runs inside the customer's infrastructure. It must extract only structured metadata (not source code), sign it with a customer key, and send it to the ingestion API. The product cannot ask customers to expose their source code to a third party.

**Implication:** The agent's output format must be designed carefully. A function's name and signature can be sent. Its body cannot. This constrains what the graph can represent at the code-function level.

---

## 3. System Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                       CUSTOMER INFRASTRUCTURE                        │
│                                                                      │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐           │
│  │  Git     │  │  OTel    │  │ OpenAPI  │  │ Terraform│           │
│  │Connector │  │Connector │  │Connector │  │Connector │           │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬─────┘           │
│       │              │              │              │                  │
│       └──────────────┴──────────────┴──────────────┘                │
│                              │                                       │
│                    ┌─────────▼─────────┐                            │
│                    │   Metadata Agent   │  (runs on-prem or         │
│                    │   (lightweight)    │   customer cloud)          │
│                    │                   │                             │
│                    │  - Extracts only  │                             │
│                    │    metadata       │                             │
│                    │  - Signs payload  │                             │
│                    │  - No source code │                             │
│                    └─────────┬─────────┘                            │
└──────────────────────────────│──────────────────────────────────────┘
                               │  HTTPS + HMAC signature
                               │  (structured JSON, no source code)
┌──────────────────────────────▼──────────────────────────────────────┐
│                         COMPANY BRAIN PLATFORM                       │
│                                                                      │
│  ┌────────────────────────────────────────────────────────────────┐ │
│  │                      INGESTION LAYER                            │ │
│  │                                                                 │ │
│  │  Ingestion API → Event Queue (SQS/Kafka) → Graph Builder       │ │
│  │                                              ↓                  │ │
│  │                                        Confidence Engine        │ │
│  │                                              ↓                  │ │
│  │                                        Edge Pruner (cron)       │ │
│  └─────────────────────────────────────────────┬───────────────────┘ │
│                                                 │                    │
│  ┌──────────────────────────────────────────────▼───────────────────┐│
│  │                        STORAGE LAYER                             ││
│  │                                                                  ││
│  │  PostgreSQL (primary) ←──────── Redis (query cache, 5min TTL)   ││
│  │  ├─ nodes table                                                  ││
│  │  ├─ edges table (adjacency list)                                 ││
│  │  ├─ edge_events (immutable log)                                  ││
│  │  ├─ metadata (git, PR, ticket context)                           ││
│  │  └─ workspace_keys (per-tenant encryption)                       ││
│  └──────────────────────────────────────────────┬───────────────────┘│
│                                                  │                   │
│  ┌───────────────────────────────────────────────▼──────────────────┐│
│  │                        QUERY LAYER                               ││
│  │                                                                  ││
│  │  REST API                                                        ││
│  │  ├─ GET /symbol/:id/context      → git + PR + ticket context     ││
│  │  ├─ GET /symbol/:id/blast-radius → graph traversal (BFS)        ││
│  │  ├─ GET /service/:id/dependents  → who calls this               ││
│  │  └─ GET /service/:id/graph       → full local neighbourhood      ││
│  └──────────────────────────────────────────────┬───────────────────┘│
│                                                  │                   │
└──────────────────────────────────────────────────│───────────────────┘
                                                   │
                         ┌─────────────────────────┤
                         │                         │
              ┌──────────▼──────┐       ┌──────────▼───────┐
              │  VS Code        │       │  Web Dashboard    │
              │  Extension      │       │  (managers)       │
              │  (hover/select) │       │  (service map)    │
              └─────────────────┘       └──────────────────┘
```

---

## 4. Data Model (PostgreSQL Schema)

```sql
-- Every entity in the graph
CREATE TABLE nodes (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id  UUID NOT NULL REFERENCES workspaces(id),
  node_type     TEXT NOT NULL,  -- 'Service', 'ApiEndpoint', 'SchemaField', etc.
  external_id   TEXT NOT NULL,  -- Stable identifier from source system
  name          TEXT NOT NULL,
  metadata      JSONB,          -- Type-specific fields (url, method, owner, etc.)
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (workspace_id, node_type, external_id)
);

-- Every dependency/relationship in the graph
CREATE TABLE edges (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id  UUID NOT NULL REFERENCES workspaces(id),
  edge_type     TEXT NOT NULL,  -- 'CALLS', 'OWNS', 'READS_TABLE', etc.
  source_id     UUID NOT NULL REFERENCES nodes(id),
  target_id     UUID NOT NULL REFERENCES nodes(id),
  confidence    FLOAT NOT NULL DEFAULT 1.0,  -- 0.0 to 1.0
  source        TEXT NOT NULL,  -- 'opentelemetry', 'openapi', 'git', etc.
  metadata      JSONB,          -- call_frequency, method, etc.
  first_seen    TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_seen     TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (workspace_id, edge_type, source_id, target_id, source)
);

-- Immutable event log for every change to the graph (enables time-travel queries)
CREATE TABLE edge_events (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id  UUID NOT NULL,
  event_type    TEXT NOT NULL,  -- 'upserted', 'pruned', 'confidence_updated'
  edge_id       UUID NOT NULL,
  payload       JSONB NOT NULL,
  occurred_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Rich context metadata attached to nodes (git blame, PR descriptions, tickets)
CREATE TABLE node_context (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id  UUID NOT NULL,
  node_id       UUID NOT NULL REFERENCES nodes(id),
  context_type  TEXT NOT NULL,  -- 'git_commit', 'pull_request', 'ticket', 'adr'
  title         TEXT,
  body          TEXT,           -- PR description, ticket summary (NOT source code)
  author        TEXT,
  source_url    TEXT,
  source_id     TEXT,           -- External ID in source system
  occurred_at   TIMESTAMPTZ,
  metadata      JSONB
);

-- Per-workspace encryption key reference (actual key stored in KMS)
CREATE TABLE workspaces (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name          TEXT NOT NULL,
  slug          TEXT UNIQUE NOT NULL,
  kms_key_id    TEXT,           -- AWS KMS / GCP KMS key reference
  plan          TEXT NOT NULL DEFAULT 'free',
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Critical indexes for traversal performance
CREATE INDEX idx_edges_source    ON edges(workspace_id, source_id, edge_type);
CREATE INDEX idx_edges_target    ON edges(workspace_id, target_id, edge_type);
CREATE INDEX idx_edges_last_seen ON edges(workspace_id, last_seen);
CREATE INDEX idx_nodes_external  ON nodes(workspace_id, node_type, external_id);
CREATE INDEX idx_context_node    ON node_context(workspace_id, node_id, context_type);

-- Row-level security: each query automatically scoped to workspace
ALTER TABLE nodes       ENABLE ROW LEVEL SECURITY;
ALTER TABLE edges       ENABLE ROW LEVEL SECURITY;
ALTER TABLE node_context ENABLE ROW LEVEL SECURITY;

CREATE POLICY workspace_isolation ON nodes
  USING (workspace_id = current_setting('app.workspace_id')::UUID);
CREATE POLICY workspace_isolation ON edges
  USING (workspace_id = current_setting('app.workspace_id')::UUID);
```

---

## 5. Blast Radius Query (The Core Query)

This is the most important query in the system — the one that makes the product worth using.

```sql
-- Given a starting node, find all nodes reachable within N hops
-- (blast radius of changing this node)
WITH RECURSIVE blast_radius AS (
  -- Base case: the starting node
  SELECT
    n.id,
    n.name,
    n.node_type,
    e.edge_type,
    e.confidence,
    0 AS depth,
    ARRAY[n.id] AS path
  FROM nodes n
  WHERE n.id = $1  -- starting node
    AND n.workspace_id = $2

  UNION ALL

  -- Recursive case: follow edges outward
  SELECT
    target.id,
    target.name,
    target.node_type,
    e.edge_type,
    e.confidence,
    br.depth + 1,
    br.path || target.id
  FROM blast_radius br
  JOIN edges e ON e.source_id = br.id
    AND e.workspace_id = $2
    AND e.confidence >= 0.5    -- only confident dependencies
    AND e.last_seen > now() - INTERVAL '30 days'  -- only live edges
  JOIN nodes target ON target.id = e.target_id
  WHERE br.depth < 5           -- max depth, prevents runaway traversal
    AND NOT (target.id = ANY(br.path))  -- prevents cycles
)
SELECT DISTINCT
  br.id,
  br.name,
  br.node_type,
  br.depth,
  n_owner.name AS owning_team,
  br.confidence
FROM blast_radius br
LEFT JOIN edges own_edge ON own_edge.target_id = br.id
  AND own_edge.edge_type = 'OWNS'
LEFT JOIN nodes n_owner ON n_owner.id = own_edge.source_id
ORDER BY br.depth, br.confidence DESC;
```

This query:
1. Starts from a node (e.g., a database column being changed)
2. Follows all dependency edges up to 5 hops
3. Filters out low-confidence and stale edges
4. Joins with ownership data to tell you which team owns each affected service
5. Returns the full blast radius with depth and confidence

**Performance note:** At moderate scale (< 500 services, < 50,000 edges per workspace), this query runs in under 100ms with proper indexing. Above that, pre-compute blast radius for high-traffic nodes and cache in Redis. At very large scale, migrate the traversal to a graph DB (see ADR-001).

---

## 6. The Metadata Agent

The agent runs inside the customer's infrastructure. It is a lightweight daemon (~20MB binary, single Go binary or Docker container). It knows nothing about other companies' data.

### What the agent does NOT send:
- Source code (function bodies, class implementations)
- Database contents
- Secrets, credentials, environment variables
- Personal data

### What the agent DOES send:
- Function/class/endpoint **names and signatures** (identifiers only)
- File paths and directory structure
- Git commit hashes, timestamps, and PR titles/descriptions
- OpenAPI/Protobuf/GraphQL schema definitions (structure, not data)
- Trace metadata: service name, endpoint, status code, latency — not request/response bodies
- CI event: which tests failed, which services were deployed

### Agent event format:
```json
{
  "workspace_id": "ws_abc123",
  "agent_version": "0.4.2",
  "timestamp": "2024-04-28T10:00:00Z",
  "signature": "hmac-sha256:...",
  "events": [
    {
      "type": "edge_observed",
      "edge_type": "CALLS",
      "source": {
        "type": "Service",
        "external_id": "order-service",
        "name": "order-service"
      },
      "target": {
        "type": "ApiEndpoint",
        "external_id": "payment-service::POST /payments/charge",
        "name": "POST /payments/charge",
        "service": "payment-service"
      },
      "observed_source": "opentelemetry",
      "confidence": 0.99,
      "metadata": {
        "call_count_last_hour": 2400,
        "p99_latency_ms": 340
      }
    }
  ]
}
```

### Agent connector plugins:
The agent is extensible. Each integration (Git, OTel, Terraform, etc.) is a plugin that implements a simple interface: `Collect() []Event`. New integrations ship as plugins, not as agent releases.

---

## 7. Ingestion Pipeline

```
Agent POST /ingest
      │
      ▼
 Signature verification (HMAC)
 Rate limiting (per workspace)
      │
      ▼
 SQS / Kafka Queue
 (async, decoupled from agent response)
      │
      ▼
 Graph Builder (worker pool)
 ├─ Upsert nodes (ON CONFLICT DO UPDATE)
 ├─ Upsert edges (update last_seen, update confidence)
 ├─ Write edge_events log
 └─ Invalidate Redis cache for affected nodes
      │
      ▼
 Confidence Engine (async, runs every 15 min)
 ├─ Boost confidence for edges seen across multiple sources
 │   (edge confirmed by both traces AND OpenAPI → confidence = 1.0)
 └─ Decay confidence for edges not seen in recent window
      │
      ▼
 Edge Pruner (cron, runs daily)
 └─ Mark edges as 'pruned' where last_seen < threshold
    (does NOT delete — writes to edge_events log for history)
```

**Why a queue?** The agent batch may contain thousands of events. Processing synchronously would slow the agent's HTTP response and create backpressure. The queue absorbs bursts and gives the graph builder reliable exactly-once processing guarantees.

**Why not delete stale edges?** Deletion destroys history. Instead, edges are marked as pruned but remain queryable for historical analysis ("this dependency existed 3 months ago"). The edge_events log is the immutable audit trail.

---

## 8. Query API

The VS Code extension and web dashboard call this API. Latency target: **< 150ms at p95** for all endpoints.

### Endpoints

| Method | Path | Description | Cache TTL |
|---|---|---|---|
| GET | `/v1/nodes/:id/context` | Git, PR, ticket context for a node | 2 min |
| GET | `/v1/nodes/:id/blast-radius` | Affected nodes if this node changes | 5 min |
| GET | `/v1/nodes/:id/dependents` | What calls/uses this node | 5 min |
| GET | `/v1/nodes/:id/dependencies` | What this node calls/uses | 5 min |
| GET | `/v1/services/:id/graph` | Full local neighbourhood (2 hops) | 5 min |
| GET | `/v1/search` | Fuzzy search across node names | 30 sec |
| POST | `/v1/workspaces/:id/graph/export` | Full graph export (for dashboard) | no cache |

### Cache invalidation

Redis keys are invalidated when:
1. The graph builder upserts a new edge touching a node
2. Explicitly via admin API (for debugging)

Cache keys are scoped: `cache:ws_{workspace_id}:node_{node_id}:blast_radius`

---

## 9. MVP Scope vs. Full Platform

| Capability | MVP (4 weeks) | Phase 2 | Phase 3 |
|---|---|---|---|
| Git context (blame, PR, ticket) | ✅ Public repos only | Private repos via agent | — |
| OpenAPI schema parsing | ✅ Public specs | Private via agent | — |
| Blast radius query | ❌ | ✅ | — |
| Distributed trace ingestion | ❌ | ✅ | — |
| Multi-tenant isolation | ❌ (single workspace) | ✅ | — |
| BYOK encryption | ❌ | ❌ | ✅ |
| Self-hosted agent | ❌ | ❌ | ✅ |
| Historical snapshots | ❌ | ❌ | ✅ |
| VS Code extension | ✅ | ✅ | ✅ |
| Web dashboard | ❌ | ✅ | ✅ |

---

## 10. Open Architecture Questions

These are the questions that need answers before starting Phase 2 build:

1. **Graph traversal at scale:** At what edge count does the recursive CTE approach break down on Postgres? Run a load test at 100K edges and measure query time. This determines whether we need to introduce a graph DB before Phase 2 ships.

2. **Trace sampling:** Production traces are sampled (typically 1-10%). Does a sampled trace graph give accurate blast radius results, or do we need to run a head-based sampler specifically for dependency detection?

3. **Schema versioning:** When an OpenAPI spec changes (field renamed or deleted), how do we reconcile old edges pointing to the old field name with new edges pointing to the new name? This is an entity resolution problem that needs a defined policy.

4. **Agent distribution:** How do customers install and update the agent? A Helm chart for K8s is the right answer for most enterprises, but the install/upgrade path needs to be designed before the agent is written.

5. **Cold start for new workspaces:** When a company first connects, they have no graph. The first agent sync populates it. How long does that take for a company with 200 services and 3 years of git history? Needs a benchmark.
