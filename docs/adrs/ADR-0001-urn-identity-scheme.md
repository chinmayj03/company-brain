# ADR-0001: URN Identity Scheme

**Status:** Accepted  
**Date:** 2026-05-03  
**Deciders:** Chinmay  
**Supersedes:** —  
**Unblocks:** ADR-0002 (graph storage), ADR-0003 (extractor plugin contract), all Phase 0 extractors

---

## Context

Every node and edge in the Company Brain knowledge graph needs a stable, globally unique identity that:

1. Stays the same across re-extractions (same file + same symbol → same ID)
2. Is collision-resistant across multiple repositories and source systems
3. Encodes enough structure to be human-readable in logs and debug output
4. Supports point-in-time queries without embedding a commit SHA in the primary key
5. Is safe to pass as a URL parameter or a Cypher property value

The simplest choice — a UUID surrogate key — fails requirement 1 (re-extraction generates a new UUID) and requirement 3 (no human meaning). A content hash fails requirement 2 when two different repos have a file with identical content.

---

## Decision

**Use the `urn:cb:...` scheme** described below. IDs are **content/path-derived**, **stable across re-extractions**, and **version-free** (temporal validity is carried in edge properties, not in the primary key).

### Format

```
urn:cb:<source>:<scope>:<artifact>[:<symbol>][@<version>]
```

| Segment | Meaning | Required |
|---------|---------|----------|
| `urn:cb` | Fixed prefix. `cb` = company-brain namespace | Always |
| `<source>` | Source system: `repo`, `linear`, `notion`, `openapi`, `figma`, `slack` | Always |
| `<scope>` | Org/repo scope: `acme/web`, `acme/api` | Always |
| `<artifact>` | File path, schema name, ticket ID, etc. | Always |
| `<symbol>` | Qualified symbol within the artifact (for code) | Code only |
| `@<version>` | Semantic version or schema version (for contracts) | Optional |

### Examples

```
urn:cb:repo:acme/web                                         # Repository node
urn:cb:repo:acme/web:main                                    # Branch
urn:cb:repo:acme/web:abc123def                               # Commit (SHA)
urn:cb:file:acme/web:src/billing/handler.ts                  # File
urn:cb:symbol:acme/web:src/billing/handler.ts:createSubscription          # Function
urn:cb:symbol:acme/web:src/billing/handler.ts:BillingService.charge       # Method
urn:cb:symbol:acme/web:src/billing/handler.ts:MAX_RETRY_ATTEMPTS          # Constant
urn:cb:contract:openapi:acme/api:operations/createSubscription@v2         # OpenAPI operation
urn:cb:contract:graphql:acme/api:Query.listOrders                         # GraphQL field
urn:cb:schema:prisma:acme/api:User                                        # Prisma model
urn:cb:schema:prisma:acme/api:User.email                                  # Prisma field
urn:cb:linear:acme:LIN-4821                                               # Linear ticket
urn:cb:notion:acme:page/abc123                                            # Notion page
urn:cb:prd:acme:pricing-v3                                                # PRD
urn:cb:adr:acme:ADR-0001                                                  # ADR (self-referential)
```

### Character rules

- Allowed: `[A-Za-z0-9/_.:@-]`
- Forward slashes are scope separators within segments
- Colons separate segments
- No spaces. Percent-encode special characters if they appear in source data.
- Case-sensitive (file paths are case-sensitive on Linux; preserve case exactly)

### Stability guarantee

The primary URN **never includes** a commit SHA, timestamp, or extraction run ID. Temporal validity is stored in **properties** on the node/edge (`valid_from_commit`, `valid_to_commit`). This means:

- Re-extracting the same file at a different commit → same node URN, updated `valid_to_commit` if changed
- The node at HEAD is always the one with `valid_to_commit = null`

### Length

Max 512 characters (Neo4j string property limit is 8k; this is far below). In practice most URNs are 40–100 characters.

---

## Options Considered

### Option A: UUID surrogate key
- ✅ Simple to generate
- ❌ Not stable across re-extractions
- ❌ Not human-readable
- ❌ Cannot be computed deterministically from source data

### Option B: SHA-256 content hash
- ✅ Deterministic
- ❌ Changes when code is reformatted (same logic, new hash)
- ❌ Collides if two repos have identical file content
- ❌ Not human-readable

### Option C: `urn:cb:...` path-derived (chosen)
- ✅ Stable across re-extractions
- ✅ Human-readable in logs
- ✅ Encodes source system, scope, and artifact in a parseable format
- ✅ Version-free primary key; temporal validity is edge metadata
- ⚠️ Slightly longer than a UUID but well within Neo4j limits

---

## Consequences

- Every extractor **must** compute the URN before writing a node. There is no auto-generated ID.
- The `packages/schema` package exports a `buildUrn()` utility and a `parseUrn()` validator.
- Neo4j has a uniqueness constraint on `id` for every node label.
- When a symbol is renamed, the old URN gets `valid_to_commit` set, and a new node is created with the new URN. They are linked by a `renamed_to` edge.
- The `source_uri` property on a node/edge is a human-navigable URL (GitHub permalink, Linear URL, etc.) — **distinct from** the URN identity.

---

## Implementation note

`buildUrn()` is the single place where the scheme is enforced:

```ts
// packages/schema/src/urn.ts
export function buildUrn(parts: UrnParts): string { ... }
export function parseUrn(urn: string): UrnParts | null { ... }
export function assertValidUrn(urn: string): asserts urn is string { ... }
```

All extractors import from `@company-brain/schema` — they never construct URN strings manually.
