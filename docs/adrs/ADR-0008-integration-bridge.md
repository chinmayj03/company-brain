# ADR-0008 — Integration Bridge: Python LLM Entities in Neo4j

**Status:** Accepted  
**Date:** 2026-05-03  
**Deciders:** Company Brain core team  
**Depends on:** ADR-0001 (URN identity scheme), ADR-0002 (graph storage substrate), ADR-0005 (confidence scoring rubric)  
**Supersedes:** —  
**Related:** ADR-0003 (extractor plugin contract), ADR-0007 (drift detection v1)

---

## Context

Company Brain has three independent graph-writing systems that have grown without a shared integration contract:

1. **TypeScript/Bun extractors** — six extractors (git, core-ts, framework-next, framework-prisma, framework-openapi, docs-md) write structural facts to Neo4j. These nodes carry URNs per ADR-0001, confidence scores per ADR-0005, and are governed by the extractor plugin contract from ADR-0003. Neo4j is the authoritative structural graph.

2. **Python FastAPI AI service** — a 4-pass LLM pipeline (Pass 1: entity extraction, Pass 2: relationship mapping, Pass 3: business context synthesis, Pass 4: validation) runs in `company-brain-ai`. All output lands exclusively in PostgreSQL via `graph/builder.py`. Entities are identified by a raw `external_id` field (a UUID4), not a URN. They are scoped by `workspace_id` but have no shared identity key with Neo4j.

3. **Java Spring Boot backend** — the auth gateway and blast-radius engine. Reads from Postgres for multi-tenancy context, human annotations, and CTE-based blast-radius traversals. Exposes REST on `:8080` to the React frontend and the Python MCP server.

The result is **two entirely separate graph populations** that describe the same codebase but cannot reference each other:

- A Python-extracted `Class` node in Postgres for `BillingService` and a TypeScript-extracted `Class` node in Neo4j for the same `BillingService` have no link — no shared ID, no edge, no cross-reference.
- When the MCP server calls Java REST to get context for a symbol, it gets LLM-synthesised summaries from Postgres but cannot ask "what does Neo4j know about the callers of this function?" because Neo4j has no entry point for the MCP server.
- The React frontend can query Java for annotations and blast-radius, but cannot ask structural questions (call graphs, contract drift, import chains) against Neo4j at all.
- Python-extracted entities in Postgres have no `source_checksum` (file hash at extraction time). When a file changes, there is no mechanism to detect that a previously extracted entity is now stale. Context rots silently.

This is not a data quality problem — both stores are correct for what they individually capture. It is an **integration gap**: the two halves of the graph cannot be joined, queried together, or kept consistent.

---

## Decision

### 1 — Dual-write from Python graph/builder.py

`GraphBuilder` in `company-brain-ai/src/companybrain/graph/builder.py` is extended to write to **both** PostgreSQL and Neo4j on every pipeline run. PostgreSQL writes are unchanged (backward-compatible). Neo4j writes are additive.

The dual-write is **not optional** and **not configurable via feature flag** — every entity and relationship the pipeline extracts is written to both stores on every successful pipeline pass. The two stores are kept in sync by construction, not by a reconciliation job.

The Neo4j write uses the same `packages/graph` driver used by the TypeScript extractors, called via a lightweight Python wrapper over the Bolt protocol (neo4j-driver-python 5.x). The Python service already has network access to Neo4j (same docker-compose network).

### 2 — URN identity for Python-extracted entities

Python-extracted entities receive a `urn:cb:llm:<scope>:<artifact>[:<symbol>]` URN, computed deterministically from the entity's source coordinates before any write.

```
urn:cb:llm:<scope>:<file_path>:<entity_name>
```

| Segment | Value |
|---------|-------|
| `urn:cb` | Fixed prefix |
| `llm` | Source system — identifies this as an LLM-extracted entity |
| `<scope>` | `workspace_id` normalised to `org/repo` format (e.g. `acme/api`) |
| `<file_path>` | Relative file path from repo root (e.g. `src/billing/service.py`) |
| `<entity_name>` | Qualified name within the file (e.g. `BillingService` or `BillingService.charge`) |

This URN is computed in a new `companybrain/graph/urn.py` module and stored in both Postgres (as `external_id`, replacing the UUID4) and Neo4j (as the `id` property).

**Why `llm` as source, not `repo`?** The `repo` source is reserved for deterministic AST-derived facts (confidence 1.0). LLM-extracted entities are `llm_with_evidence` (confidence 0.70) or `llm_inference_only` (confidence 0.50). Using a distinct source segment prevents URN collisions and makes the provenance immediately legible in logs and queries.

**Cross-system identity resolution:** when the same code artifact exists in both stores (e.g. `urn:cb:llm:acme/api:src/billing/service.py:BillingService` in Postgres/Neo4j and `urn:cb:symbol:acme/api:src/billing/service.py:BillingService` in Neo4j), a `same_as` edge links them:

```cypher
MATCH (llm:CBNode { id: "urn:cb:llm:acme/api:src/billing/service.py:BillingService" })
MATCH (ast:CBNode { id: "urn:cb:symbol:acme/api:src/billing/service.py:BillingService" })
MERGE (llm)-[:same_as { confidence: 0.90, derivation: "name_path_match" }]->(ast)
```

This `same_as` edge is emitted by the Python builder after resolving Neo4j nodes by file path + name. Confidence is 0.90 (high but not 1.0, because name matching across extraction systems can have edge cases).

### 3 — Node type mapping: Python entity types → CB schema node types

Python `entity_type` values from `ExtractedEntity` map to CB schema node labels as follows:

| Python `entity_type` | Neo4j label | Notes |
|---|---|---|
| `Class` | `Class` | Direct mapping |
| `Function` | `Function` | Top-level functions only |
| `Method` | `Method` | When parent class is resolvable |
| `Module` | `Module` | Python module → CB Module node |
| `Interface` | `Interface` | Python Protocol / abstract base |
| `Constant` | `Constant` | Module-level constants |
| `DataModel` | `DatabaseTable` | Pydantic / SQLAlchemy model → DB table |
| `APIEndpoint` | `HTTPEndpoint` | FastAPI route handlers |
| `unknown` | `Function` | Fallback; confidence capped at 0.50 |

When a Python entity type has no direct CB equivalent, the entity is written with the closest structural label and a `llm_entity_type` property preserving the original Python value for debugging.

### 4 — Edge type mapping: Python relationship types → CB schema edge types

| Python `edge_type` | Neo4j edge type | Notes |
|---|---|---|
| `calls` | `calls` | Direct mapping |
| `imports` | `imports` | Direct mapping |
| `extends` | `extends` | Direct mapping (class inheritance) |
| `implements` | `implements` | Interface implementation |
| `uses` | `references` | Generic usage relationship |
| `defines` | `defines` | Module → symbol |
| `returns` | `returns_type` | Function return type edge |
| `raises` | `raises` | Exception propagation edge |
| `depends_on` | `depends_on` | Package/module dependency |
| `accesses` | `reads_from` | DB / store access (mapped to reads_from) |
| `writes_to` | `writes_to` | DB / store write (direct mapping) |

Unmapped relationship types are written to Neo4j as `llm_relationship` with the original type in an `original_type` property. They are queryable but not surfaced by standard tools.

### 5 — Staleness detection via source_checksum

Every entity written by the Python pipeline includes a `source_checksum` property: the SHA-256 hash of the source file at the time of extraction.

```python
# companybrain/graph/checksum.py
import hashlib

def file_checksum(path: str) -> str:
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()
```

This checksum is stored:
- In Postgres: in the `metadata` JSONB column as `source_checksum`
- In Neo4j: as a first-class `source_checksum` property on the node

When the pipeline runs incrementally (triggered by a git commit hook or a webhook), it compares the current file checksum against `source_checksum` on existing nodes. If they differ, the node is flagged as stale (`stale: true` property) and re-queued for re-extraction. If the file has been deleted, the node receives `valid_to_commit` = current SHA (same invalidation pattern used by TypeScript extractors per ADR-0003).

### 6 — Unified query surface: Neo4j via TypeScript tRPC API

Neo4j is the unified query surface for structural and cross-system graph queries. The access pattern per service is:

```
React frontend
  ├── Java :8080          → auth, human annotations, blast-radius CTE, pipeline job state
  └── TypeScript tRPC     → structural graph, call chains, contracts, drift signals

Python MCP server
  ├── Java REST :8080     → semantic context (LLM summaries, NarrativeNotes), auth checks
  └── TypeScript tRPC     → structural queries (find_symbol, find_callers, get_drift_signals)

Python AI service
  ├── Postgres (direct)   → write LLM entities, contexts (via GraphBuilder)
  └── Neo4j (direct)      → write LLM entities (dual-write via GraphBuilder)
```

The TypeScript tRPC API (`apps/api`) is the gatekeeper for all Neo4j reads. It enforces scope-in-URN isolation (no cross-workspace data leaks), applies confidence floors by tool category (per ADR-0005), and provides the query contracts the MCP server and frontend depend on.

Java REST remains the **auth gateway and blast-radius engine**. It is not replaced — the Postgres CTE blast-radius traversal is fast and correct for that use case. Java does not write to Neo4j and does not need to know about it.

### 7 — Store responsibilities (final state)

| Concern | Store | Rationale |
|---|---|---|
| LLM-extracted entities and relationships | Postgres + Neo4j (dual-write) | Postgres for RLS/multi-tenancy; Neo4j for cross-system joins |
| Business context blobs (Pass 3 synthesis) | Postgres only | Large text; multi-tenant; not graph-traversable |
| Human annotations and NarrativeNotes | Postgres only | Requires RLS; user-authored; no traversal need |
| Auth, workspace membership, RLS | Postgres only | Row-level security is a Postgres-native feature |
| Pipeline job state (run history, pass status) | Postgres only | Operational log; not part of the knowledge graph |
| Blast-radius queries | Postgres only | Existing CTE is fast; no reason to migrate |
| Structural graph (AST-extracted nodes/edges) | Neo4j only | TypeScript extractors; deterministic; 1.0 confidence |
| Contract nodes and drift signals | Neo4j only | ADR-0007; queryable by agents and frontend |
| Cross-repo federation queries | Neo4j only | Scope-in-URN traversal; Cypher path patterns |
| Fast symbol lookups, call chains, import graphs | Neo4j only | Purpose-built traversal; outperforms Postgres at depth |

### 8 — Migration: backfill existing Postgres entities to Neo4j

A one-time backfill job (`scripts/backfill-neo4j.py`) reads all existing rows from the Postgres `nodes` and `edges` tables and writes them to Neo4j using the same URN scheme and type mappings defined above.

Backfill strategy:

1. Read Postgres `nodes` in pages of 500, ordered by `created_at ASC`.
2. For each row: compute a URN from `(workspace_id, node_type, external_id, metadata.file, name)`. If `external_id` is already a valid `urn:cb:` string, reuse it; if it is a UUID4, compute the URN and update the Postgres row to store the new URN as `external_id` (migration is in-place).
3. Upsert into Neo4j via `packages/graph` `GraphClient`.
4. After all nodes: replay edges, resolving source/target by the new URNs.
5. Run `same_as` edge resolution pass: for each LLM node, attempt to match against an AST node by `(scope, file_path, name)`. Emit `same_as` edges where matches are found.
6. Log backfill metrics (nodes written, edges written, same_as edges emitted, failures) to stdout.

The backfill is idempotent (Neo4j upsert on `id` uniqueness constraint). It can be re-run safely.

---

## Consequences

**Good:**
- The MCP server gains access to structural Neo4j queries (call chains, contracts, drift signals) without routing through Java or duplicating logic.
- LLM-extracted entities are now traversable from structural nodes via `same_as` edges, enabling mixed-confidence queries ("find callers of this function, annotated with LLM business context").
- Neo4j becomes the single query surface for cross-system graph questions; agent tools no longer need to fan-out to two stores and manually join results.
- `source_checksum` enables targeted re-extraction: only files that changed need to be re-processed, not the full corpus.
- The URN scheme for Python entities is consistent with ADR-0001 — logs, debug output, and agent responses all use the same identity format regardless of which system produced the node.

**Bad:**
- Every Python pipeline run now has two write paths. If the Neo4j write fails after the Postgres write succeeds, the two stores are temporarily inconsistent. The builder must implement compensating logic: wrap both writes in a try/except, log Neo4j failures as non-fatal, and schedule a repair job.
- The Python service takes on a dependency on the Neo4j Bolt endpoint. If Neo4j is down, the Python pipeline can still succeed (Postgres write) but the Neo4j write is queued for retry — this requires a retry queue (a simple Postgres-backed job table is sufficient for MVP).
- URN migration for existing Postgres rows requires a careful backfill that updates `external_id` in place. Until the backfill completes, some Postgres nodes and their Neo4j counterparts have different ID formats. The backfill must complete before enabling `same_as` edge resolution.
- The `same_as` edge confidence of 0.90 means some false matches are possible for common symbol names (`__init__`, `main`, `handle`). The resolution pass should require both file path AND name to match, not name alone.

**Neutral:**
- Java REST is unchanged. Existing consumers of Java API endpoints are not affected.
- Postgres schema does not change. The only in-place migration is updating `external_id` from UUID4 to URN format during the backfill.
- TypeScript extractor code is unchanged — they already write to Neo4j with the correct schema.

---

## Implementation Notes

### companybrain/graph/urn.py (new)

```python
import re

def build_llm_urn(scope: str, file_path: str, entity_name: str) -> str:
    """
    Build a urn:cb:llm URN for a Python-extracted entity.
    scope: org/repo, e.g. "acme/api"
    file_path: relative path from repo root, e.g. "src/billing/service.py"
    entity_name: qualified name, e.g. "BillingService" or "BillingService.charge"
    """
    # Normalise: strip leading slash, forward-slash only
    file_path = file_path.lstrip("/").replace("\\", "/")
    # URN safe: no spaces
    entity_name = entity_name.replace(" ", "_")
    return f"urn:cb:llm:{scope}:{file_path}:{entity_name}"

def is_urn(value: str) -> bool:
    return value.startswith("urn:cb:")
```

### GraphBuilder changes (summary)

- `upsert_entities()`: after Postgres write, call `self._neo4j_upsert_entities(entities)`. Non-fatal on Neo4j failure.
- `upsert_relationships()`: after Postgres write, call `self._neo4j_upsert_relationships(relationships)`.
- `upsert_contexts()`: Postgres only (context blobs are not written to Neo4j).
- New `_resolve_same_as()` method: run after all entities are written; match LLM nodes to AST nodes by `(scope, file_path, name)`; emit `same_as` edges.

### Neo4j write pattern

Python nodes are written with two labels: `CBNode` and the mapped CB schema label (e.g. `Class`). They also carry `source: "llm"` as a property to distinguish them from AST-extracted nodes at query time.

```cypher
MERGE (n:CBNode:Class { id: $urn })
ON CREATE SET
  n.name = $name,
  n.source = "llm",
  n.confidence = $confidence,
  n.derivation = $derivation,
  n.source_checksum = $checksum,
  n.extracted_from_commit = $commit_sha,
  n.extraction_timestamp = $ts
ON MATCH SET
  n.source_checksum = $checksum,
  n.confidence = $confidence,
  n.extraction_timestamp = $ts
```

### Retry queue for Neo4j write failures

A Postgres table `neo4j_write_queue` holds serialised write payloads for failed Neo4j writes. A background worker (asyncio task in the FastAPI service) drains the queue with exponential backoff. This is MVP-sufficient; a proper message queue (Redis Streams, Kafka) is the upgrade path at scale.

```sql
CREATE TABLE neo4j_write_queue (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id TEXT NOT NULL,
    payload     JSONB NOT NULL,
    attempts    INT DEFAULT 0,
    last_error  TEXT,
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    next_retry  TIMESTAMPTZ DEFAULT NOW()
);
```
