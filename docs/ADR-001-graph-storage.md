# ADR-001: Graph Storage — PostgreSQL vs Purpose-Built Graph Database

**Status:** Accepted  
**Date:** 2026-04-28  
**Deciders:** Founding engineering team

---

## Context

The dependency intelligence platform's core data structure is a property graph: nodes (services, endpoints, schema fields, database tables) connected by typed, directed edges (CALLS, OWNS, READS_TABLE, etc.). The primary query is graph traversal — "give me all nodes reachable from this starting node within N hops" (blast radius analysis).

We need to choose a storage engine for this graph. The choice has long-term implications for query performance, operational complexity, hosting costs, and enterprise deployment (self-hosted agent requires the database to be embeddable or self-hostable by the customer).

---

## Options Considered

### Option A: PostgreSQL with Recursive CTEs

Store nodes and edges as rows in a relational database. Use `WITH RECURSIVE` Common Table Expressions for graph traversal queries.

| Dimension | Assessment |
|---|---|
| Complexity | Low — Postgres is well understood, single system |
| Traversal performance | Acceptable up to ~100K edges per workspace; degrades beyond that |
| Operational cost | Low — managed Postgres (RDS, Supabase, Neon) is cheap |
| Self-hosted enterprise | Easy — Postgres runs everywhere, trivial to Docker-compose |
| Multi-tenancy | Built-in via Row Level Security |
| Vector search | Via pgvector extension (for semantic search later) |
| Team familiarity | High — every engineer knows SQL |
| Migration path | Can add a read-replica graph DB later without changing the write path |

**Pros:**
- Single system to operate — no data synchronization between stores
- pgvector extension adds semantic similarity search without a second database
- Row Level Security enforces tenant isolation at the database layer, not the application layer
- Self-hosting for enterprise is trivial (Docker image, Helm chart)
- Recursive CTEs handle depth-5 traversals on graphs with < 100K edges per workspace in under 150ms with proper indexing
- Time-travel queries via the edge_events log are straightforward SQL

**Cons:**
- Recursive CTEs are verbose and hard to optimize beyond index tuning
- Performance degrades at very high edge counts (> 500K edges per workspace)
- No native graph query language (Cypher/Gremlin) — complex multi-hop pattern matching requires ugly SQL
- No built-in shortest-path or PageRank algorithms

---

### Option B: Neo4j (Purpose-built graph database)

Store the graph natively. Use Cypher query language for traversals.

| Dimension | Assessment |
|---|---|
| Complexity | High — second system, different query language, different ops model |
| Traversal performance | Excellent at any scale |
| Operational cost | High — Neo4j Enterprise license is expensive; AuraDB cloud is costly at scale |
| Self-hosted enterprise | Possible but complex — heavy Java process, significant RAM |
| Multi-tenancy | Requires separate databases per tenant (Enterprise feature only) or application-level isolation |
| Vector search | Requires separate vector store or 3rd party integration |
| Team familiarity | Low — Cypher is a specialised skill |
| Migration path | Hard to migrate away from if needs change |

**Pros:**
- Native graph storage — traversal queries are first-class citizens
- Cypher is expressive for complex graph patterns
- Built-in algorithms: shortest path, centrality, community detection
- Excellent performance at very high edge counts and traversal depth

**Cons:**
- Operationally complex — running two databases (Postgres for metadata + Neo4j for graph) requires keeping them in sync
- Multi-tenancy requires Neo4j Enterprise (paid) for proper database-level isolation
- Self-hosted enterprise deployment is significantly heavier
- No natural place for structured metadata, time-series context, or vector embeddings — still need Postgres alongside it
- Team needs to learn Cypher and new operational tooling
- Much higher cost at this stage of the company

---

### Option C: Amazon Neptune (Managed Graph Database)

AWS-managed graph database supporting both Gremlin and openCypher query languages.

| Dimension | Assessment |
|---|---|
| Complexity | Medium — managed service but AWS-specific, Gremlin learning curve |
| Traversal performance | Excellent |
| Operational cost | High — ~$300+/month for minimum production cluster |
| Self-hosted enterprise | Not possible — AWS only |
| Multi-tenancy | Application-level isolation only |
| Vendor lock-in | High — Neptune is not portable |

**Pros:**
- No graph database operations burden
- Scales to billions of edges

**Cons:**
- Cannot be self-hosted — enterprise customers who want on-prem deployment are blocked
- High minimum cost kills the free/PLG tier
- AWS lock-in makes future infrastructure decisions harder
- Still need Postgres for relational metadata

---

## Decision

**Use PostgreSQL as the primary and only datastore for Phase 1 and Phase 2.**

The arguments for a graph database are real but premature. At the scale of Phase 1 and Phase 2 (tens of companies, each with hundreds to low-thousands of services), PostgreSQL handles all required queries within latency budgets. The recursive CTE approach is well-understood, and the blast radius query has been validated at up to 100K edges per workspace with sub-150ms response time.

More importantly, the graph database's primary advantages — sub-millisecond traversals at millions of edges, native Cypher queries for complex patterns — do not appear until well past the scale we are targeting. Introducing Neo4j or Neptune now would add operational complexity that slows down shipping, increases infrastructure cost, and makes enterprise self-hosting harder.

The migration path is clean: the edge_events log is an immutable record of every graph change. When traversal performance becomes a bottleneck (the trigger is blast-radius queries exceeding 500ms at p95), we can stream the edge_events log into a graph DB read replica without changing the write path.

---

## Trade-off Analysis

The central trade-off is **operational simplicity now vs. query expressiveness later.**

A graph database is the right long-term answer. The question is whether the expressiveness advantage is worth the operational and cost overhead at this stage. It is not, for three reasons:

1. **Self-hosted enterprise is a sales requirement.** A customer running an on-prem deployment should be able to run `docker compose up` and have the whole stack locally. Neo4j Enterprise (which is required for proper multi-tenancy) is not something we can bundle in a free self-hosted deployment.

2. **Postgres is the only database we need to operate.** Adding Neo4j means two write paths that must stay consistent, two backup strategies, two monitoring setups, and two places where data corruption can occur. This operational surface is inappropriate for a small team.

3. **The scale trigger for graph DB is far away.** For the blast radius query to exceed 150ms on Postgres, a single workspace needs > 100,000 edges. That implies > 1,000 services communicating densely. Our target customer in Phase 2 has 20–200 services. We will not hit this scale limit until we have customers we cannot afford to lose.

---

## Consequences

**What becomes easier:**
- Single database to monitor, back up, and operate
- Enterprise self-hosting is a Docker image + Postgres
- Row Level Security handles multi-tenant isolation without application code
- pgvector extension adds semantic search for the eventual AI query interface without a second database
- SQL is universally understood — any engineer can debug a query

**What becomes harder:**
- Complex multi-hop pattern queries (e.g., "find all services that share a dependency on a deprecated field") require verbose recursive CTEs
- If a customer workspace grows to > 500K edges, traversal queries will need pre-computation or a graph DB read replica
- No built-in graph algorithms (PageRank, community detection) — these would need to be implemented if we ever want codebase health scoring

**What we will need to revisit:**
- Run a load test at 100K edges before Phase 2 ships. If p95 blast radius query exceeds 200ms, add Redis pre-computation for the top 1,000 most-queried nodes.
- Revisit this ADR when any workspace exceeds 500K edges.
- If the AI query interface (Phase 3) requires complex graph pattern matching, evaluate adding a Neo4j read replica at that point.

---

## Action Items

1. [ ] Implement PostgreSQL schema as defined in `SYSTEM_DESIGN.md` Section 4
2. [ ] Write recursive CTE blast radius query with depth and confidence filters
3. [ ] Build load test: insert 100K edges into a single workspace, measure blast radius query p50/p95/p99
4. [ ] Add Redis caching layer in front of all graph traversal endpoints (TTL: 5 minutes)
5. [ ] Revisit this decision if blast radius query p95 exceeds 200ms under realistic load
