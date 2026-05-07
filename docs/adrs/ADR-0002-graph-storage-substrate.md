# ADR-0002: Graph Storage Substrate

**Status:** Accepted  
**Date:** 2026-05-03  
**Deciders:** Chinmay  
**Supersedes:** ADR-001 (graph-storage.md) — extends, does not replace  
**Depends on:** ADR-0001 (URN scheme)  
**Unblocks:** Phase 0 `packages/graph`, Neo4j bootstrap

---

## Context

The existing company-brain system uses PostgreSQL 16 with a relational graph model (nodes + edges tables, bidirectional CTE for blast-radius queries). This was the right decision for v1: Postgres is operationally familiar, supports RLS for multi-tenancy, and is sufficient for single-hop graph queries.

The v0.1→MVP build prompt specifies a **typed knowledge graph** that must support:

1. Multi-hop traversals: "find all screens that use a component that imports a function that reads a PII column"
2. Complex path-pattern queries: subgraph extraction for agent context bundles
3. Bidirectional edge traversal as a first-class primitive (not a manually crafted CTE)
4. Full-text + vector indexes on the same node set (Neo4j 5.13+ has both)
5. Schema-enforced node labels for type safety at query time

At scale (>100k nodes, >500k edges), PostgreSQL CTE-based graph traversals become the primary query bottleneck. The ADR-006 blast-radius queries already show this pressure on medium-sized workspaces.

The new TypeScript/Bun extraction layer is being built alongside the existing Java/Python services. It needs its own store that is purpose-built for graph operations.

---

## Decision

**Use Neo4j 5.x Community Edition** for the typed knowledge graph layer of company-brain, running alongside the existing PostgreSQL instance.

### Deployment model

```
docker-compose.yml (updated):
  company-brain-neo4j:   # new — typed graph store for Phase 0+
    image: neo4j:5.20-community
    ports: ["7474:7474", "7687:7687"]

  company-brain-postgres: # existing — semantic graph, multi-tenancy, LLM pipeline
    image: postgres:16
    (unchanged)
```

Both stores run simultaneously. They are **not** a migration target for each other — they serve different layers:

| Layer | Store | Why |
|-------|-------|-----|
| Structural + typed graph (Phase 0+) | Neo4j | Cypher traversals, APOC, vector index |
| Semantic/LLM graph, multi-tenancy | PostgreSQL | RLS, existing pipeline, encrypted node_context |
| MCP tool surface | Both (via query fan-out) | Tools query Neo4j for structural, Postgres for semantic |

### Why not PostgreSQL + Apache AGE

AGE brings Cypher to Postgres. Considered and rejected:

- AGE is in alpha for PG16; production-readiness is uncertain
- APOC (Neo4j's graph algorithm library) has no AGE equivalent  
- Neo4j's native vector index (since 5.13) outperforms pgvector for hybrid graph+vector queries
- Operational cost of running two heavy Postgres extensions (pgvector + AGE) is worse than running a dedicated Neo4j

### Why Community Edition (not AuraDB)

- Self-hosted for the MVP; no cloud dependency
- Community Edition supports all query patterns needed (Cypher, indexes, APOC)
- Enterprise features (sharding, HA) are not needed until post-product-market-fit
- AuraDB free tier has limits that would be hit in a realistic workspace

### Node labels and schema enforcement

Neo4j labels map directly to the `NodeType` enum from the YAML schema. Every node has exactly one primary label (`Repository`, `File`, `Function`, etc.) plus a secondary label `CBNode` for cross-type queries.

Example:
```cypher
CREATE (:CBNode:Repository { id: "urn:cb:repo:acme/web", name: "web", ... })
CREATE (:CBNode:Function { id: "urn:cb:symbol:acme/web:...", ... })
```

The `id` property has a global uniqueness constraint. Additional composite indexes exist on `(label, name)` and `(label, last_modified_commit)`.

### Multi-tenancy in Neo4j

Neo4j Community does not have database-per-tenant. We use a **scope-in-URN** strategy instead: all queries filter by the `scope` segment of the URN (which contains `org/repo`). The graph layer never exposes cross-scope data without an explicit federation query.

For the SaaS product (future), Aura Enterprise or Neo4j Enterprise with database-per-tenant is the upgrade path. The URN scheme (ADR-0001) is compatible with either — scope is always carried in the ID itself.

---

## Consequences

- `packages/graph` exports a typed Neo4j driver wrapper with CRUD helpers
- `infra/neo4j-init/` contains the Cypher constraints and indexes applied at boot
- All Phase 0+ extractors write to Neo4j via `packages/graph`
- The existing Python/Java services continue writing to PostgreSQL unchanged
- The MCP server (company-brain-mcp) reads from both stores for tool responses

### Upgrade path

When the graph grows beyond Community Edition limits or the team wants HA:
1. Switch Neo4j Community → Neo4j Enterprise (drop-in replacement; same Cypher)
2. Or migrate to Memgraph (Cypher-compatible, better streaming workload performance)

The `packages/graph` abstraction layer ensures extractor code doesn't need to change — only the driver config.
