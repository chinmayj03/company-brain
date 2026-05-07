# ADR-0006 — Framework Extractor Contract

**Status:** Accepted  
**Date:** 2026-05-03  
**Deciders:** Company Brain core team  
**Supersedes:** —  
**Related:** ADR-0003 (extractor plugin contract), ADR-0001 (URN scheme)

---

## Context

Phase 1 established the `CoreTsExtractor` which handles language-level constructs (files, symbols, call edges). But modern TypeScript projects are layered: a Next.js codebase has *routes*, a Prisma project has *tables*, and an OpenAPI-first team has *contracts*. These constructs carry business meaning that pure AST analysis cannot recover.

Three domain-specific extractors are needed:

| Extractor | Input | Key node types |
|---|---|---|
| `FrameworkNextExtractor` | `app/` / `pages/` directory layout | Screen, APIRoute, Layout, Component |
| `FrameworkPrismaExtractor` | `schema.prisma` files | DatabaseSchema, DatabaseTable, Column, Index, Enum |
| `FrameworkOpenApiExtractor` | `openapi.yaml` / `openapi.json` | ContractDocument, ContractEndpoint, ContractRequestSchema, ContractResponseSchema |

All three must conform to the ADR-0003 extractor plugin contract. This ADR defines the *additional* invariants they must respect.

---

## Decision

### 1 — Composition model

Framework extractors run **after** `CoreTsExtractor` in the pipeline. They may read graph nodes written by `CoreTsExtractor` (e.g., to link an `APIRoute` node to the `Function` node that implements it) but must never mutate them.

Order in `apps/extractor-worker/src/registry.ts`:
```
[GitExtractor, CoreTsExtractor, FrameworkNextExtractor, FrameworkPrismaExtractor, FrameworkOpenApiExtractor, DriftDetector]
```

### 2 — URN patterns for framework nodes

All framework nodes follow the standard URN pattern `urn:cb:<source>:<scope>:<artifact>[:<symbol>]`:

| Node type | Source | Artifact pattern |
|---|---|---|
| Screen | `next` | `routes/<normalized-path>` |
| APIRoute | `next` | `routes/<normalized-path>#<METHOD>` |
| Layout | `next` | `layouts/<normalized-path>` |
| Component | `next` | `components/<repoRelativePath>` |
| DatabaseSchema | `prisma` | `schema/<filename-stem>` |
| DatabaseTable | `prisma` | `schema/<filename-stem>/tables/<ModelName>` |
| Column | `prisma` | `schema/<filename-stem>/tables/<ModelName>/columns/<fieldName>` |
| Index | `prisma` | `schema/<filename-stem>/tables/<ModelName>/indexes/<indexName>` |
| Enum | `prisma` | `schema/<filename-stem>/enums/<EnumName>` |
| ContractDocument | `contract` | `contracts/<filename-stem>` |
| ContractEndpoint | `contract` | `contracts/<filename-stem>/operations/<operationId>` |
| ContractRequestSchema | `contract` | `contracts/<filename-stem>/operations/<operationId>/request` |
| ContractResponseSchema | `contract` | `contracts/<filename-stem>/operations/<operationId>/responses/<statusCode>` |

Normalized path: filesystem path with `[param]` preserved, leading `app/` or `pages/` stripped, trailing `page`, `route`, `layout`, `index` stripped.  
Example: `app/billing/[invoiceId]/route.ts` → `billing/[invoiceId]`

### 3 — Confidence levels

Framework extractors emit with the following baseline confidence values:

| Derivation | Value | Rationale |
|---|---|---|
| `framework_parser` | 0.95 | Structure inferred from framework conventions (filesystem layout, Prisma SDL) |
| `config` | 0.95 | Explicit schema files (openapi.yaml, schema.prisma) |
| `static_analysis` | 0.85 | Inferred from code patterns (e.g., detecting "use client" directive) |

### 4 — Cross-extractor edges

When a framework extractor links to a `CoreTs` node (e.g., APIRoute → Function), it emits an edge with `derivation: "static_analysis"` and `confidence: 0.85`, since the link is inferred rather than declared.

Required cross-extractor edges:

| From | Edge type | To | Condition |
|---|---|---|---|
| APIRoute | `implemented_by` | Function (CoreTs) | Route handler file matches a `Function`/`Method` with exported HTTP-method name |
| Screen | `implemented_by` | Function (CoreTs) | Page file exports a default function |
| DatabaseTable | `implemented_by` | Class (CoreTs) | Class name matches table model name (case-insensitive) |

### 5 — Incremental extraction

Framework extractors implement the same dirty-set protocol as `CoreTsExtractor`:

1. On first run: extract all matching files.
2. On subsequent runs: re-extract files whose `repoRelativePath` appears in the dirty set (changed since `.cb-last-sha`).
3. Call `graph.invalidateByPrefix(urnPrefix, currentSha)` before re-extracting any previously indexed file.

### 6 — Failure isolation

Framework extractors **must not** throw at the extractor level. Malformed input (corrupt Prisma schema, invalid OpenAPI YAML) must be caught and logged per ADR-0003. The extractor returns an empty `WriteBatch` for the affected file and continues.

### 7 — Detection heuristics

**FrameworkNextExtractor** detects framework presence by:
- Existence of `next.config.js` / `next.config.ts` / `next.config.mjs` in repo root, OR
- `"next"` in `package.json` dependencies

**FrameworkPrismaExtractor** detects presence by:
- Any `*.prisma` file under repo root, OR
- `"@prisma/client"` in `package.json` dependencies

**FrameworkOpenApiExtractor** detects presence by:
- Any file matching `openapi.{yaml,yml,json}` or `swagger.{yaml,yml,json}` anywhere in the repo

---

## Consequences

**Good:**
- Framework nodes give the agent semantic query targets that pure AST cannot provide (e.g., "which API routes handle billing?")
- Composition model ensures `CoreTs` and framework nodes coexist in the same graph without conflicts
- Confidence rubric from ADR-0005 applies uniformly; consumers can filter by confidence tier

**Bad:**
- Three more extractor packages increase monorepo surface area
- Cross-extractor edge quality depends on naming conventions; projects that don't follow them will have gaps
- Framework detection is heuristic; multi-framework repos may get partial extraction

**Neutral:**
- DriftDetector (ADR-0007) depends on both `FrameworkNextExtractor` and `FrameworkOpenApiExtractor` completing first
