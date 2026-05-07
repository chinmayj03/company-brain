# ADR-003: Multi-Tenancy Isolation Strategy

**Status:** Accepted  
**Date:** 2026-04-28  
**Deciders:** Founding engineering team

---

## Context

The platform stores sensitive metadata about multiple companies' codebases. A data leak — where Company A can query Company B's dependency graph — is an existential risk. We need to choose a multi-tenancy isolation model that is provably correct, operationally simple, and supports future enterprise requirements (BYOK encryption, SOC 2, self-hosting).

The three standard multi-tenancy models are: separate databases per tenant, separate schemas per tenant, and shared schema with row-level security.

---

## Options Considered

### Option A: Separate Database per Tenant

Each company gets its own Postgres database (or RDS instance).

| Dimension | Assessment |
|---|---|
| Isolation strength | Very high — databases are physically separate |
| Blast radius of a bug | Single tenant |
| Operational complexity | Very high — N databases to manage, upgrade, back up |
| Cost | Very high — each database has fixed overhead |
| Cross-tenant analytics | Not possible (needed for platform health metrics) |
| BYOK encryption | Natural — each DB has its own encryption config |
| Self-hosted enterprise | Works — customer runs their own DB |

**Verdict:** Too operationally expensive for a startup. Suitable only for very high-value, regulated enterprise contracts (banks, healthcare). May offer this as an add-on for top-tier enterprise later.

---

### Option B: Separate Schema per Tenant

Each company gets their own Postgres schema within a shared cluster.

| Dimension | Assessment |
|---|---|
| Isolation strength | High — schema separation prevents accidental cross-tenant queries |
| Operational complexity | Medium — schema migration must run for each tenant |
| Cost | Low — shared cluster |
| Cross-tenant analytics | Possible but requires schema-hopping queries |
| BYOK encryption | Requires column-level encryption (same challenge as Option C) |

**Verdict:** Migrations become expensive fast. Running `ALTER TABLE` across 500 tenant schemas is painful. And schema-level isolation is not enforced by Postgres at the query level — a bug in application code can still cross schemas. Not meaningfully better than Option C for the operational cost.

---

### Option C: Shared Schema with Row Level Security (RLS)

All tenants share the same tables. Every row has a `workspace_id` column. Postgres Row Level Security policies enforce that queries automatically filter to the current workspace.

| Dimension | Assessment |
|---|---|
| Isolation strength | High — enforced at database layer, not application layer |
| Operational complexity | Low — single schema, standard migrations |
| Cost | Low — shared cluster |
| Cross-tenant analytics | Natural — platform queries bypass RLS using superuser role |
| BYOK encryption | Column-level encryption via pgcrypto or application-layer AES |
| Self-hosted enterprise | Customer runs the full stack; their data never mingles with others |

**Verdict:** The right choice for Phase 2. RLS is enforced at the database level, which means even if the application has a bug that fails to set `workspace_id`, the query returns no rows rather than leaking data. This is a fundamentally stronger isolation guarantee than application-level filtering.

---

## Decision

**Use shared schema with PostgreSQL Row Level Security for Phase 2. Offer separate database as an enterprise add-on in Phase 3.**

RLS is set on all tables containing customer data: `nodes`, `edges`, `edge_events`, `node_context`. The application sets `app.workspace_id` at the start of every database session. All queries are automatically scoped.

```sql
-- Set at the start of every DB session
SET app.workspace_id = 'ws_abc123';

-- RLS policy (set once, enforced forever)
CREATE POLICY workspace_isolation ON nodes
  USING (workspace_id = current_setting('app.workspace_id')::UUID);

-- Platform analytics queries bypass RLS using superuser
SET ROLE platform_admin;  -- bypasses RLS
SELECT count(*) FROM nodes GROUP BY workspace_id;
```

---

## Encryption Strategy

Sensitive metadata fields (PR descriptions, commit messages, ticket summaries) are encrypted at the application layer before being stored in Postgres. This ensures that even a database dump does not expose cleartext customer data.

### For standard customers (platform-managed keys):
- AES-256-GCM encryption at the application layer
- Key stored in AWS KMS, one key per workspace
- Application fetches key on startup, caches in memory with 15-minute TTL
- All encrypted fields stored as `BYTEA` in Postgres

### For enterprise BYOK customers:
- Customer provisions a key in their own AWS KMS or GCP KMS
- They grant the platform's IAM role `kms:Decrypt` and `kms:GenerateDataKey` permissions
- The `workspace_keys` table stores only the KMS key ARN, not the key material
- If the customer rotates or revokes the key, the platform immediately loses access to that workspace's data (this is the guarantee they're buying)

### What is NOT encrypted (query-necessary fields):
- `node_type`, `edge_type` — needed for graph traversal queries
- `workspace_id` — needed for RLS
- `source_id`, `target_id` — needed for adjacency queries
- `first_seen`, `last_seen` — needed for staleness queries

### What IS encrypted:
- `nodes.metadata` (JSONB containing names, paths, descriptions)
- `node_context.title`, `node_context.body` (PR descriptions, commit messages, ticket text)
- `edges.metadata` (call frequency, latency percentiles)

---

## Security Controls Summary

| Control | Mechanism |
|---|---|
| Query isolation | PostgreSQL Row Level Security on all customer tables |
| Encryption at rest | AWS RDS encryption (AES-256) for entire volume |
| Field-level encryption | Application-layer AES-256-GCM for sensitive text fields |
| Key management | AWS KMS per workspace; BYOK for enterprise |
| Network isolation | Postgres not publicly accessible; only application tier connects |
| Agent authentication | HMAC-SHA256 signature on every agent payload |
| API authentication | JWT with workspace_id claim; verified on every request |
| Audit logging | All query and ingestion events logged to CloudWatch Logs |

---

## Consequences

**What becomes easier:**
- Schema migrations run once, not N times
- Platform health metrics span all workspaces in a single query
- Adding a new customer is a row insert in the `workspaces` table, not a new database provisioning
- RLS provides a true database-level guarantee against cross-tenant data leakage

**What becomes harder:**
- Application code must always set `app.workspace_id` before any query — missing this causes queries to return zero rows (silent failure, not error). Requires a global middleware that enforces this.
- Column-level encryption adds latency to reads and writes of encrypted fields (benchmark: ~2ms per field with AES-256-GCM)
- BYOK key rotation requires re-encrypting all data for that workspace — expensive operation, needs a background job

**What we will need to revisit:**
- If a customer requires a dedicated database (SOC 2 Type II, HIPAA, or contractual requirement), offer it as a paid add-on. The application code is already workspace-scoped, so moving a workspace to its own database is an operational task, not a code change.
- Add pgaudit extension when pursuing SOC 2 certification to log all database queries.

---

## Action Items

1. [ ] Enable RLS on all customer-data tables with `workspace_id` policies
2. [ ] Implement global database middleware that sets `app.workspace_id` from JWT claim on every request
3. [ ] Write a test that verifies RLS: insert data for workspace A, query as workspace B, assert zero rows returned
4. [ ] Implement application-layer AES-256-GCM encryption for `node_context.body` and `nodes.metadata`
5. [ ] Add KMS key provisioning to workspace creation flow
6. [ ] Write runbook for BYOK key rotation
