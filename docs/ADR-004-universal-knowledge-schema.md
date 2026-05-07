# ADR-004: Universal Knowledge Schema — Domain-Agnostic Graph Primitive

**Status:** Proposed  
**Date:** 2026-04-28  
**Deciders:** Engineering lead, Product  

---

## Context

The current schema uses `node_type` as a code-specific enum (`Service`, `ApiEndpoint`, `DatabaseTable`, `FrontendComponent`, etc.). This tightly couples the graph model to the engineering domain.

The product vision is a **universal company brain** — a single structured knowledge layer that holds not just code dependencies but every repeatable, teachable thing a company does: how support escalations work, how pricing exceptions are approved, how incidents are handled, how invoices flow through finance.

The core question: **do we generalise the schema now, or keep it code-specific and migrate later?**

Generalising now costs ~1 week of schema migration and abstraction work. Migrating later, after thousands of nodes have been ingested across teams, would be a major engineering effort with downtime risk. The decision is clear: **generalise the primitive now**, before scale.

---

## Decision

Introduce a **two-level taxonomy** for all knowledge nodes:

```
domain  (engineering, support, finance, ops, hr, legal, ...)
  └── entity_type (Service, Ticket, Policy, Playbook, Contract, ...)
```

Replace the current code-specific schema with **universal graph primitives** that can hold any company knowledge while preserving the existing graph traversal, blast radius, and annotation capabilities.

---

## Universal Schema (PostgreSQL)

### 1. Domain Registry

```sql
CREATE TABLE domains (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id UUID NOT NULL REFERENCES workspaces(id),
  name        VARCHAR(64) NOT NULL,         -- 'engineering', 'support', 'finance'
  description TEXT,
  config      JSONB DEFAULT '{}',           -- extraction config per domain
  created_at  TIMESTAMPTZ DEFAULT now(),
  UNIQUE (workspace_id, name)
);
```

### 2. Knowledge Nodes (replaces `nodes`)

```sql
CREATE TABLE knowledge_nodes (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id  UUID NOT NULL REFERENCES workspaces(id),
  domain        VARCHAR(64) NOT NULL,         -- 'engineering', 'support', ...
  entity_type   VARCHAR(64) NOT NULL,         -- 'Service', 'Policy', 'Playbook', ...
  external_id   VARCHAR(512),                 -- stable ID from source system
  name          TEXT NOT NULL,
  summary       TEXT,                         -- 1-sentence LLM-synthesised summary
  metadata      JSONB DEFAULT '{}',           -- domain-specific structured data
  embedding     VECTOR(1536),                 -- for semantic search (pgvector)
  confidence    FLOAT DEFAULT 1.0,            -- 0.0–1.0, cross-source agreement
  staleness_risk FLOAT DEFAULT 0.0,           -- 0.0–1.0, rises over time
  source_count  INT DEFAULT 1,                -- how many sources confirmed this node
  is_pruned     BOOLEAN DEFAULT FALSE,
  last_seen     TIMESTAMPTZ DEFAULT now(),
  created_at    TIMESTAMPTZ DEFAULT now(),
  updated_at    TIMESTAMPTZ DEFAULT now(),
  UNIQUE (workspace_id, domain, entity_type, external_id)
);

-- Indexes
CREATE INDEX ON knowledge_nodes USING GIN (metadata jsonb_path_ops);
CREATE INDEX ON knowledge_nodes USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);
CREATE INDEX ON knowledge_nodes (workspace_id, domain, staleness_risk DESC);
CREATE INDEX ON knowledge_nodes USING GIN (to_tsvector('english', name || ' ' || coalesce(summary, '')));
```

### 3. Knowledge Edges (replaces `edges`)

```sql
CREATE TABLE knowledge_edges (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id  UUID NOT NULL REFERENCES workspaces(id),
  source_id     UUID NOT NULL REFERENCES knowledge_nodes(id),
  target_id     UUID NOT NULL REFERENCES knowledge_nodes(id),
  edge_type     VARCHAR(64) NOT NULL,         -- see Edge Type Taxonomy below
  weight        FLOAT DEFAULT 1.0,
  metadata      JSONB DEFAULT '{}',
  confidence    FLOAT DEFAULT 1.0,
  observed_source VARCHAR(128),               -- 'git', 'slack', 'zendesk', ...
  is_pruned     BOOLEAN DEFAULT FALSE,
  last_seen     TIMESTAMPTZ DEFAULT now(),
  created_at    TIMESTAMPTZ DEFAULT now(),
  UNIQUE (workspace_id, source_id, target_id, edge_type)
);
```

### 4. Knowledge Context (replaces `node_context`)

```sql
CREATE TABLE knowledge_context (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id    UUID NOT NULL REFERENCES workspaces(id),
  node_id         UUID NOT NULL REFERENCES knowledge_nodes(id),
  context_type    VARCHAR(64) NOT NULL,       -- see Context Type below
  content         TEXT NOT NULL,              -- plain text, human-readable
  source_ref      TEXT,                       -- URL, commit hash, ticket ID, channel
  source_system   VARCHAR(64),                -- 'git', 'slack', 'zendesk', 'confluence'
  annotation_type VARCHAR(64),                -- for human annotations
  applies_to      TEXT[],                     -- specific fields/steps this context covers
  confidence      FLOAT DEFAULT 1.0,
  embedding       VECTOR(1536),               -- for context-level semantic search
  extracted_at    TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX ON knowledge_context (node_id, context_type);
CREATE INDEX ON knowledge_context USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);
```

### 5. Sources Registry

```sql
CREATE TABLE knowledge_sources (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id  UUID NOT NULL REFERENCES workspaces(id),
  system        VARCHAR(64) NOT NULL,         -- 'git', 'slack', 'zendesk', 'confluence'
  domain        VARCHAR(64) NOT NULL,
  config        JSONB NOT NULL,               -- connection details per system
  last_ingested TIMESTAMPTZ,
  status        VARCHAR(32) DEFAULT 'active', -- 'active', 'paused', 'error'
  created_at    TIMESTAMPTZ DEFAULT now()
);
```

---

## Edge Type Taxonomy

A single taxonomy spanning all domains. Edge types are namespaced by convention `VERB` (universal) or `DOMAIN_VERB` (domain-specific).

```
# Universal (cross-domain)
OWNS          Service --OWNS--> DatabaseTable
              Team --OWNS--> Policy
REFERENCES    ApiEndpoint --REFERENCES--> Policy
              Playbook --REFERENCES--> Escalation
DEPENDS_ON    Service --DEPENDS_ON--> Service
TRIGGERS      Event --TRIGGERS--> Playbook
SUPERSEDES    Policy_v2 --SUPERSEDES--> Policy_v1

# Engineering
CALLS         Service --CALLS--> ApiEndpoint
READS_FROM    Service --READS_FROM--> DatabaseTable
WRITES_TO     Service --WRITES_TO--> DatabaseTable
RENDERS       FrontendComponent --RENDERS--> ApiEndpoint
DEFINES       ApiEndpoint --DEFINES--> SchemaField

# Support
HANDLES       Team --HANDLES--> IssueCategory
ESCALATES_TO  SupportTier --ESCALATES_TO--> SupportTier
RESOLVES_VIA  IssueCategory --RESOLVES_VIA--> Playbook
GOVERNED_BY   Process --GOVERNED_BY--> Policy

# Finance / Ops
APPROVED_BY   PricingException --APPROVED_BY--> Role
ROUTES_TO     Invoice --ROUTES_TO--> ApprovalQueue
EXECUTED_BY   Process --EXECUTED_BY--> Team
```

---

## Entity Type Examples Per Domain

| Domain | Entity Types |
|--------|-------------|
| engineering | Service, ApiEndpoint, DatabaseTable, DatabaseColumn, FrontendComponent, CodeFunction, SchemaField, ExternalService |
| support | IssueCategory, Playbook, EscalationPath, SLAPolicy, KnowledgeArticle, CustomerSegment |
| finance | ApprovalPolicy, ExpenseCategory, InvoiceRoute, BudgetRule, ComplianceRequirement |
| ops | Runbook, AlertRule, OnCallRotation, IncidentSeverity, PostmortemTemplate |
| hr | HiringProcess, OnboardingStep, PolicyDocument, BenefitsPlan |
| legal | Contract, ComplianceRule, DataRetentionPolicy, VendorAgreement |

---

## Context Types

```
llm_synthesis       — LLM-generated summary of node purpose and behaviour
source_extract      — verbatim or paraphrased chunk from a source document
human_annotation    — engineer/analyst-authored annotation (commit-anchored for code)
cross_ref_note      — automated note when same fact appears in multiple sources
staleness_alert     — auto-generated when confidence drops below threshold
gap_flag            — gap detector identified missing context for this node
```

---

## Migration from Current Schema

The current `nodes` / `edges` / `node_context` tables map directly:

```sql
-- Migrate existing nodes
INSERT INTO knowledge_nodes (id, workspace_id, domain, entity_type, external_id, name, metadata, last_seen, created_at)
SELECT id, workspace_id, 'engineering', node_type, external_id, name, metadata, last_seen, created_at
FROM nodes;

-- Migrate existing edges  
INSERT INTO knowledge_edges (id, workspace_id, source_id, target_id, edge_type, weight, metadata, confidence, observed_source, last_seen, created_at)
SELECT id, workspace_id, source_id, target_id, edge_type, weight, metadata, confidence, source, last_seen, created_at
FROM edges;

-- Migrate existing context
INSERT INTO knowledge_context (id, workspace_id, node_id, context_type, content, annotation_type, applies_to, confidence)
SELECT id, workspace_id, node_id, 'llm_synthesis', encode(body, 'escape'), annotation_type, applies_to_fields, confidence
FROM node_context;
```

Backwards-compatible: existing API endpoints continue to work, they simply filter by `domain = 'engineering'`.

---

## Options Considered

### Option A: Keep code-specific schema, migrate later (Rejected)
**Pros:** No migration work now, schema stays simple  
**Cons:** Migration at scale is painful; every new domain adds schema complexity via separate tables; loses the unified blast-radius query across domains

### Option B: Universal schema now (Chosen)
**Pros:** One query engine for all domains; cross-domain blast radius ("what business processes break if I delete this DB table?"); single ingestion pipeline interface; compound confidence scores across sources  
**Cons:** More abstract upfront; entity_type is now a free-form string (mitigated by domain registry + validation)

### Option C: Separate graph per domain
**Pros:** Clean isolation  
**Cons:** Can't query across domains; no cross-domain edges; double the infrastructure

---

## Consequences

**What becomes easier:**
- Adding a new domain (support, finance) requires zero schema changes — just new entity_type strings and extractors
- Cross-domain blast radius: "what breaks in support if this API endpoint changes?"
- Unified semantic search across all company knowledge
- Single annotation UX works for all domains

**What becomes harder:**
- Entity type validation must happen at the application layer, not the DB constraint level
- Domain registry must be kept current when new entity types are introduced
- Embedding index will grow large; will need partitioning by domain at ~10M nodes

**Revisit triggers:**
- If cross-domain graph traversal queries exceed 500ms p95 at >1M nodes → evaluate graph database (Neptune/Neo4j)
- If embedding index exceeds 50GB → evaluate dedicated vector store (Pinecone/Qdrant) alongside PostgreSQL

---

## Action Items

- [ ] Write Flyway migration V2 (rename tables, add domain/embedding columns)
- [ ] Update Java models: Node → KnowledgeNode, Edge → KnowledgeEdge
- [ ] Update GraphService to filter by domain on all engineering queries
- [ ] Add domain registry CRUD endpoints
- [ ] Install pgvector extension and run `CREATE EXTENSION vector`
- [ ] Update AI pipeline to write to knowledge_nodes instead of nodes
