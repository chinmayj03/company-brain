# company-brain Harness — System Design

> **Goal:** Keep any LLM in its "smart zone" — always operating on the smallest, most relevant slice of context possible, while maintaining full awareness of a 10+ repo platform, cross-repo blast radius, and business intent.
>
> **Version:** 1.0 · **Date:** 2026-05-07

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Core Concepts](#2-core-concepts)
3. [Data Model — Brain Entity Schemas](#3-data-model--brain-entity-schemas)
4. [Extraction Pipeline](#4-extraction-pipeline)
5. [Storage Architecture (JSON + BM25 + Vector Hybrid)](#5-storage-architecture)
6. [Smart-Zone Context Assembly Algorithm](#6-smart-zone-context-assembly)
7. [Blast Radius Engine](#7-blast-radius-engine)
8. [MCP API Layer](#8-mcp-api-layer)
9. [Multi-Repo Federation](#9-multi-repo-federation)
10. [Update Triggers](#10-update-triggers)
11. [Business Context Layer](#11-business-context-layer)
12. [Implementation Roadmap](#12-implementation-roadmap)

---

## 1. System Overview

The harness is a four-layer system. Each layer has a single job and a clean interface to the next.

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        LLM (Claude / any model)                         │
│                         ← SMART ZONE →                                  │
│         Only sees: T1 summaries + T2 relevant detail + business ctx     │
└──────────────────────────────┬──────────────────────────────────────────┘
                               │  MCP / SDK tool calls
┌──────────────────────────────▼──────────────────────────────────────────┐
│                    Layer 4: MCP API + Context Assembler                  │
│  brain_query(task, entities, token_budget)                               │
│  ↳ Smart-zone algorithm: retrieval → blast radius → tier → compress     │
└──────────────────────────────┬──────────────────────────────────────────┘
                               │  reads from / writes to
┌──────────────────────────────▼──────────────────────────────────────────┐
│                       Layer 3: Storage                                   │
│   JSON files (SOT)  +  Qdrant (BM25S sparse + all-MiniLM dense, RRF)   │
│   Dependency graph (adjacency JSON → Memgraph at scale)                 │
└──────────────────────────────┬──────────────────────────────────────────┘
                               │  populated by
┌──────────────────────────────▼──────────────────────────────────────────┐
│                    Layer 2: Extraction Pipeline                          │
│  tree-sitter AST → entity extractor → business context annotator        │
│  Triggers: git hooks · CI jobs · on-demand CLI · session start          │
└──────────────────────────────┬──────────────────────────────────────────┘
                               │  reads from
┌──────────────────────────────▼──────────────────────────────────────────┐
│                    Layer 1: Source Repos (10+)                           │
│  repo-a/  repo-b/  repo-c/ ... repo-n/                                  │
└─────────────────────────────────────────────────────────────────────────┘
```

### Design Principles

**Context efficiency over completeness.** Never dump everything into context. The brain exists so the LLM *doesn't* have to read the whole codebase.

**Three-tier loading.** Summaries (T1) load always. Detail (T2) loads when relevant. Raw source (T3) loads only on explicit request.

**Blast radius first.** Every query expands to its dependency neighborhood before context is assembled.

**Business context is first-class.** Code metadata is cheap to extract. Business context (the *why*) is irreplaceable — it's stored separately and always included in T1.

**JSON is the source of truth.** All brain data lives in git-tracked JSON files. Qdrant is an index over those files, not a database. If Qdrant is wiped, a rebuild script recreates it from JSON.

---

## 2. Core Concepts

### 2.1 Brain Entities

The brain stores six entity types:

| Entity | What it represents | Key relationships |
|---|---|---|
| `component` | A UI component, service class, or module | renders → screen, calls → api, uses → component |
| `screen` | A user-facing page or view | contains → component, triggers → api |
| `api_contract` | An API endpoint definition | served_by → service, consumed_by → component/screen |
| `data_model` | A data structure (DB schema, DTO, interface) | used_by → component/api, stored_in → data_store |
| `assumption` | A data invariant or business rule | applies_to → entity (any type) |
| `business_context` | Domain knowledge, product decisions, user journeys | relates_to → entity (any type) |

### 2.2 The Smart Zone

The smart zone is a token budget (default: 6,000 tokens) allocated across three tiers:

```
Token budget: 6,000
├── T1 Summaries:       ~1,200 tokens  (always loaded — fast overview of relevant entities)
├── T2 Selective detail: ~3,600 tokens  (retrieved via BM25+vector, deduplicated via MMR)
└── T3 Business context: ~1,200 tokens  (always loaded — the "why" behind relevant entities)
```

If the task is simple, T2 may use fewer tokens. Unused budget is not filled — context discipline is the point.

### 2.3 Blast Radius

Blast radius is the set of entities that are transitively connected to a changed or queried entity. It has two directions:

- **Upstream** (what depends on this): components/screens that use an API or component
- **Downstream** (what this depends on): APIs and models a component calls

A change to an entity has potential impact across its entire blast radius. The harness surfaces this automatically.

### 2.4 Identity Across Repos

Every entity has a globally unique ID:

```
{repo}::{entity_type}::{qualified_name}
```

Examples:
- `api-service::api_contract::POST /users/{id}/roles`
- `web-app::component::UserCard`
- `shared-lib::data_model::UserDTO`

Cross-repo edges in the dependency graph use these IDs.

---

## 3. Data Model — Brain Entity Schemas

All entities share a `BrainEntityBase`. Specific entities extend it.

### 3.1 BrainEntityBase

```json
{
  "id": "web-app::component::UserCard",
  "type": "component",
  "repo": "web-app",
  "file": "src/components/UserCard.tsx",
  "qualified_name": "UserCard",
  "t1_summary": "Displays user avatar, name, and role. Used on Dashboard and Settings screens.",
  "last_updated": "2026-05-07T00:00:00Z",
  "last_updated_by": "harness/extractor v1.2",
  "version_hash": "abc123",
  "tags": ["user", "dashboard", "shared"]
}
```

### 3.2 Component Entity

```json
{
  "id": "web-app::component::UserCard",
  "type": "component",
  "repo": "web-app",
  "file": "src/components/UserCard.tsx",
  "qualified_name": "UserCard",
  "t1_summary": "Displays user avatar, name, and role badge. Used on Dashboard, Settings, Admin screens.",
  "props": [
    { "name": "userId", "type": "string", "required": true, "description": "Valid UUID of the user" },
    { "name": "showRole", "type": "boolean", "required": false, "default": true }
  ],
  "state": [
    { "name": "isLoading", "type": "boolean", "initial": false },
    { "name": "userData", "type": "UserDTO | null", "initial": null }
  ],
  "child_components": ["web-app::component::Avatar", "web-app::component::RoleBadge"],
  "api_calls": ["api-service::api_contract::GET /users/{id}"],
  "data_models_used": ["shared-lib::data_model::UserDTO"],
  "rendered_on_screens": ["web-app::screen::Dashboard", "web-app::screen::Settings"],
  "assumptions": ["web-app::assumption::user-id-always-uuid"],
  "business_context_refs": ["web-app::business_context::user-identity-design"],
  "last_updated": "2026-05-07T00:00:00Z",
  "version_hash": "abc123"
}
```

### 3.3 Screen Entity

```json
{
  "id": "web-app::screen::Dashboard",
  "type": "screen",
  "repo": "web-app",
  "file": "src/screens/Dashboard.tsx",
  "qualified_name": "Dashboard",
  "t1_summary": "Main landing screen after login. Shows user cards, activity feed, and quick actions.",
  "route": "/dashboard",
  "auth_required": true,
  "root_components": ["web-app::component::UserCard", "web-app::component::ActivityFeed"],
  "api_calls_triggered": ["api-service::api_contract::GET /users/{id}", "api-service::api_contract::GET /activity"],
  "user_journey_step": "Post-login home",
  "business_context_refs": ["web-app::business_context::dashboard-north-star"],
  "last_updated": "2026-05-07T00:00:00Z",
  "version_hash": "def456"
}
```

### 3.4 API Contract Entity

```json
{
  "id": "api-service::api_contract::GET /users/{id}",
  "type": "api_contract",
  "repo": "api-service",
  "file": "src/routes/users.ts",
  "qualified_name": "GET /users/{id}",
  "t1_summary": "Returns full user profile including roles and permissions. Requires auth token.",
  "method": "GET",
  "path": "/users/{id}",
  "path_params": [{ "name": "id", "type": "string", "format": "uuid" }],
  "query_params": [],
  "request_body": null,
  "response_schema": {
    "200": { "$ref": "shared-lib::data_model::UserDTO" },
    "404": { "error": "string" },
    "401": { "error": "string" }
  },
  "auth_required": true,
  "rate_limited": true,
  "consumed_by": ["web-app::component::UserCard", "mobile-app::component::ProfileHeader"],
  "served_by": "api-service",
  "data_models": ["shared-lib::data_model::UserDTO"],
  "sla_ms": 200,
  "assumptions": ["api-service::assumption::auth-token-always-valid-before-route"],
  "last_updated": "2026-05-07T00:00:00Z",
  "version_hash": "ghi789"
}
```

### 3.5 Data Model Entity

```json
{
  "id": "shared-lib::data_model::UserDTO",
  "type": "data_model",
  "repo": "shared-lib",
  "file": "src/types/user.ts",
  "qualified_name": "UserDTO",
  "t1_summary": "Core user object shared across web, mobile, and API. Immutable after creation except roles.",
  "fields": [
    { "name": "id", "type": "string", "format": "uuid", "required": true, "immutable": true },
    { "name": "email", "type": "string", "format": "email", "required": true },
    { "name": "roles", "type": "Role[]", "required": true, "description": "Always at least one role" },
    { "name": "createdAt", "type": "string", "format": "ISO 8601", "immutable": true }
  ],
  "used_by_components": ["web-app::component::UserCard", "mobile-app::component::ProfileHeader"],
  "used_by_apis": ["api-service::api_contract::GET /users/{id}"],
  "assumptions": ["shared-lib::assumption::user-always-has-one-role"],
  "last_updated": "2026-05-07T00:00:00Z",
  "version_hash": "jkl012"
}
```

### 3.6 Assumption Entity

```json
{
  "id": "shared-lib::assumption::user-always-has-one-role",
  "type": "assumption",
  "repo": "shared-lib",
  "qualified_name": "user-always-has-one-role",
  "t1_summary": "A User object always has at least one role. No code should handle the empty-roles case.",
  "statement": "roles.length >= 1 is always true at runtime",
  "severity": "critical",
  "applies_to": ["shared-lib::data_model::UserDTO", "api-service::api_contract::GET /users/{id}"],
  "violation_impact": "RoleBadge component will crash rendering empty role list",
  "origin": "Product decision — all users must have a default 'viewer' role on creation",
  "verified_by_test": "api-service/tests/users.test.ts:L44",
  "last_updated": "2026-05-07T00:00:00Z"
}
```

### 3.7 Business Context Entity

```json
{
  "id": "web-app::business_context::user-identity-design",
  "type": "business_context",
  "repo": "web-app",
  "qualified_name": "user-identity-design",
  "t1_summary": "Users are identified by email globally. The userId UUID is internal only and never shown in UI.",
  "domain": "Identity & Auth",
  "content": "...(full business context, product decisions, user journey notes)...",
  "relates_to": ["web-app::component::UserCard", "shared-lib::data_model::UserDTO"],
  "source": "Product spec v2.3 · Slack thread 2025-11-12 · ADR-007",
  "last_updated": "2026-05-07T00:00:00Z"
}
```

### 3.8 Dependency Graph Schema

Stored in `.brain/graph.json` per repo, with a platform-level `platform-graph.json` aggregating all repos:

```json
{
  "nodes": [
    { "id": "web-app::component::UserCard", "type": "component", "repo": "web-app" },
    { "id": "api-service::api_contract::GET /users/{id}", "type": "api_contract", "repo": "api-service" },
    { "id": "shared-lib::data_model::UserDTO", "type": "data_model", "repo": "shared-lib" }
  ],
  "edges": [
    {
      "from": "web-app::component::UserCard",
      "to": "api-service::api_contract::GET /users/{id}",
      "type": "calls",
      "cross_repo": true
    },
    {
      "from": "api-service::api_contract::GET /users/{id}",
      "to": "shared-lib::data_model::UserDTO",
      "type": "returns",
      "cross_repo": true
    }
  ]
}
```

Edge types: `calls`, `renders`, `returns`, `uses`, `depends_on`, `imports`, `stores_in`, `triggers`

---

## 4. Extraction Pipeline

The extraction pipeline walks a repo and produces brain JSON files. It runs in three modes: **full rebuild**, **incremental** (changed files only), and **targeted** (specific entity by path).

### 4.1 Pipeline Stages

```
Source file(s)
      │
      ▼
[Stage 1] Language detection + tree-sitter AST parse
      │
      ▼
[Stage 2] Entity detection
│  - Component? (React/Vue/Angular patterns, class components, functional)
│  - Screen? (route registration, page-level component patterns)
│  - API handler? (Express routes, FastAPI, Django views, gRPC defs)
│  - Data model? (TypeScript interfaces, Pydantic models, DB schemas)
      │
      ▼
[Stage 3] Relationship extraction
│  - Import graph → uses / depends_on edges
│  - API call sites → calls edges
│  - Route registrations → serves / triggers edges
│  - Type usage → returns / stores_in edges
      │
      ▼
[Stage 4] Assumption mining (static + heuristic)
│  - JSDoc / docstring annotations tagged @assumption
│  - Guard clauses: if (!user.roles.length) throw → assumption found
│  - Assertions: assert(id !== null), invariant() calls
│  - Non-null assertions: user.roles! → flag as assumption
      │
      ▼
[Stage 5] T1 summary generation (LLM call, small model)
│  - Input: raw extracted metadata (no source code)
│  - Prompt: "Write a 1-2 sentence summary of this entity for an engineer"
│  - Model: claude-haiku (fast, cheap) or local model
│  - Cached: only regenerated when version_hash changes
      │
      ▼
[Stage 6] Write brain JSON + update graph
│  - .brain/{entity_type}/{qualified_name}.json
│  - .brain/graph.json (incremental edge upsert)
      │
      ▼
[Stage 7] Index into Qdrant (hybrid: BM25S + embedding)
      │
      ▼
Done
```

### 4.2 tree-sitter Integration

tree-sitter provides a universal AST parser for 40+ languages. The harness uses language-specific queries (`.scm` files) to extract entities.

**TypeScript/TSX component query:**
```scheme
; Find React functional components
(function_declaration
  name: (identifier) @component.name
  parameters: (formal_parameters
    (object_pattern) @component.props))

; Find JSX returns (confirms it's a component)
(return_statement
  (parenthesized_expression
    (jsx_element) @component.jsx))
```

**Supported languages out of the box:**
TypeScript, JavaScript, Python, Go, Rust, Java, Ruby, PHP, C#

### 4.3 Assumption Mining Heuristics

```python
ASSUMPTION_PATTERNS = [
    # Explicit annotations
    r"@assumption\s+(.+)",           # JSDoc / docstring
    r"# ASSUMPTION:\s+(.+)",          # Python comment
    r"// ASSUME:\s+(.+)",             # JS comment

    # Non-null assertions (TypeScript)
    r"(\w+)!\.(\w+)",                 # user!.role → assumes user is non-null

    # Guard clauses that throw
    r"if\s*\(!(.+?)\)\s*throw",      # if (!user) throw → assumes user

    # Assertion libraries
    r"assert\((.+?)\)",              # assert(id !== null)
    r"invariant\((.+?)\)",           # invariant(roles.length > 0)
    r"expect\((.+?)\)\.toBeDefined", # test-level assumption

    # Zod / validation schemas used as runtime contracts
    r"\.parse\((.+?)\)",             # Zod parse = runtime contract
]
```

### 4.4 Custom Code Tokenizer for BM25

Standard tokenizers fail on code. The harness uses a code-aware tokenizer:

```python
import re

def tokenize_code(text: str) -> list[str]:
    """Split code text into searchable tokens."""
    tokens = []

    for token in re.split(r'[\s\.\,\;\:\(\)\[\]\{\}\=\>\<\!\&\|\+\-\*\/\\"\']', text):
        if not token or len(token) < 2:
            continue

        # Split camelCase: getUserId → get, user, id
        sub = re.sub(r'([a-z])([A-Z])', r'\1 \2', token)
        # Split on digits: user3D → user, 3, d
        sub = re.sub(r'([a-zA-Z])(\d)', r'\1 \2', sub)
        # Split snake_case (already split by _)
        parts = re.split(r'[_\-]', sub.lower())
        tokens.extend([p for p in parts if len(p) >= 2])

    return tokens
```

---

## 5. Storage Architecture

### 5.1 Three-Store Design

```
┌──────────────────────────────────────────────────────────────────┐
│  Store 1: JSON Files (.brain/ in each repo + platform-brain/)   │
│  Role: Source of truth. Git-tracked. Human-readable.             │
│  Operations: write (extractor), read (rebuild index)             │
└──────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────┐
│  Store 2: Qdrant (hybrid index)                                  │
│  Role: Fast retrieval. BM25S sparse + all-MiniLM dense + RRF.   │
│  Operations: upsert (extractor), hybrid_search (context assembler│
└──────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────┐
│  Store 3: Dependency Graph (JSON adjacency list → Memgraph)      │
│  Role: Blast radius computation. Cross-repo edges.               │
│  Operations: upsert_edge (extractor), traverse (assembler)       │
└──────────────────────────────────────────────────────────────────┘
```

### 5.2 JSON File Layout

```
platform-brain/                          ← top-level, all repos
├── index.json                           ← entity index (id → repo + file)
├── platform-graph.json                  ← cross-repo dependency graph
└── business_context/                    ← platform-wide business context
    └── domain-glossary.json

repo-name/.brain/                        ← per-repo brain (committed)
├── index.json                           ← entity index for this repo
├── graph.json                           ← intra-repo dependency graph
├── components/
│   ├── UserCard.json
│   └── ...
├── screens/
│   ├── Dashboard.json
│   └── ...
├── api_contracts/
│   ├── GET_users_{id}.json
│   └── ...
├── data_models/
│   ├── UserDTO.json
│   └── ...
├── assumptions/
│   └── user-always-has-one-role.json
└── business_context/
    └── user-identity-design.json
```

### 5.3 Qdrant Collection Schema

One collection per entity type. Each document stores the T1 summary for fast retrieval:

```python
from qdrant_client import QdrantClient
from qdrant_client.models import (
    VectorParams, SparseVectorParams, SparseIndexParams, Distance
)

client = QdrantClient(url="http://localhost:6333")

# One collection per entity type
for entity_type in ["component", "screen", "api_contract", "data_model", "assumption", "business_context"]:
    client.recreate_collection(
        collection_name=f"brain_{entity_type}",
        vectors_config={
            "dense": VectorParams(size=384, distance=Distance.COSINE)
        },
        sparse_vectors_config={
            "sparse": SparseVectorParams(index=SparseIndexParams(on_disk=False))
        }
    )
```

**Document payload stored per entity:**
```json
{
  "id": "web-app::component::UserCard",
  "repo": "web-app",
  "type": "component",
  "t1_summary": "Displays user avatar, name, and role badge. Used on Dashboard, Settings, Admin screens.",
  "tags": ["user", "dashboard", "shared"],
  "file": "src/components/UserCard.tsx"
}
```

The full entity JSON (T2) is fetched from the JSON files on demand — not stored in Qdrant.

### 5.4 Hybrid Retrieval with RRF

```python
from bm25s import BM25, tokenize
import numpy as np
from sentence_transformers import SentenceTransformer

encoder = SentenceTransformer("all-MiniLM-L6-v2")

def hybrid_search(
    query: str,
    entity_types: list[str],
    top_k: int = 20,
    rrf_k: int = 60
) -> list[dict]:
    """
    Hybrid BM25S + dense vector search with RRF fusion.
    Returns ranked list of entity IDs with scores.
    """

    # --- BM25S sparse retrieval ---
    query_tokens = tokenize_code(query)
    bm25_results = bm25_index.retrieve(query_tokens, k=top_k * 2)  # over-fetch

    # --- Dense vector retrieval ---
    query_embedding = encoder.encode(query, normalize_embeddings=True)
    vector_results = qdrant_client.search(
        collection_name=f"brain_{'_'.join(entity_types)}",
        query_vector=("dense", query_embedding.tolist()),
        limit=top_k * 2
    )

    # --- RRF Fusion ---
    # Build rank maps
    bm25_ranks = {doc_id: rank + 1 for rank, doc_id in enumerate(bm25_results)}
    vector_ranks = {hit.payload["id"]: rank + 1 for rank, hit in enumerate(vector_results)}

    all_ids = set(bm25_ranks.keys()) | set(vector_ranks.keys())
    rrf_scores = {}
    for doc_id in all_ids:
        score = 0.0
        if doc_id in bm25_ranks:
            score += 1.0 / (rrf_k + bm25_ranks[doc_id])
        if doc_id in vector_ranks:
            score += 1.0 / (rrf_k + vector_ranks[doc_id])
        rrf_scores[doc_id] = score

    ranked = sorted(rrf_scores.items(), key=lambda x: x[1], reverse=True)
    return [{"id": id_, "score": score} for id_, score in ranked[:top_k]]
```

**RRF formula:**
```
score(d) = Σ [ 1 / (k + rank_i(d)) ]  for each retrieval system i
```
Where `k = 60` (empirically optimal).

### 5.5 MMR Deduplication

After retrieval, apply Maximum Marginal Relevance to avoid loading near-duplicate entities:

```python
def mmr_rerank(
    query_embedding: np.ndarray,
    candidate_embeddings: dict[str, np.ndarray],
    initial_scores: dict[str, float],
    lambda_: float = 0.7,
    top_k: int = 10
) -> list[str]:
    """
    MMR: balance relevance vs diversity to avoid redundant context.
    lambda_=1.0 → pure relevance; lambda_=0.0 → pure diversity.
    """
    selected = []
    remaining = list(candidate_embeddings.keys())

    while remaining and len(selected) < top_k:
        if not selected:
            # First pick: highest relevance
            best = max(remaining, key=lambda x: initial_scores[x])
        else:
            best = None
            best_score = -1.0
            for doc_id in remaining:
                relevance = initial_scores[doc_id]
                # Redundancy = max similarity to already-selected docs
                redundancy = max(
                    cosine_sim(candidate_embeddings[doc_id], candidate_embeddings[s])
                    for s in selected
                )
                mmr_score = lambda_ * relevance - (1 - lambda_) * redundancy
                if mmr_score > best_score:
                    best_score = mmr_score
                    best = doc_id

        selected.append(best)
        remaining.remove(best)

    return selected
```

---

## 6. Smart-Zone Context Assembly

This is the harness's core innovation. Given a task description and an optional entity list, it assembles the optimal context payload for the LLM within a token budget.

### 6.1 Assembly Pipeline

```
Input: { task: string, entities?: string[], token_budget: number }
         │
         ▼
[Step 1] Query Understanding
│  - Extract entity mentions from task (NER-lite: PascalCase, /api/paths, snake_case)
│  - Detect task type: READ (query) | WRITE (modify) | AUDIT (review) | DEBUG
│  - Set retrieval parameters based on task type
         │
         ▼
[Step 2] Primary Retrieval (Hybrid BM25S + Vector, RRF)
│  - Search across entity types relevant to task
│  - Over-fetch: top_k = 40 candidates
│  - Filter by relevance threshold: score > 0.05
         │
         ▼
[Step 3] Blast Radius Expansion
│  - For each primary hit: expand 1-hop in dependency graph
│  - For WRITE tasks: also expand 2-hop upstream (what uses this)
│  - For DEBUG tasks: also expand downstream data models
│  - Deduplicate expanded set
         │
         ▼
[Step 4] MMR Reranking (lambda=0.7)
│  - Remove near-duplicates from retrieval results
│  - Retain diversity while preserving relevance
         │
         ▼
[Step 5] Tier Assignment
│  - T1 (summaries only): all entities passing threshold → ~10-30 tokens each
│  - T2 (full detail): top-N entities by score until T2 budget filled
│  - T3 (raw source): only if explicitly flagged by task
│  - Business context: always included for T2 entities
         │
         ▼
[Step 6] Contextual Compression
│  - T2 entities: strip fields not relevant to task type
│   · READ task: keep summary, props, api_calls → drop state, assumptions
│   · WRITE task: keep props, state, assumptions, child_components
│   · DEBUG task: keep everything
│  - Estimate token count; trim lowest-scored entities if over budget
         │
         ▼
Output: { t1: [...], t2: [...], business_context: [...], blast_radius: [...], tokens_used: number }
```

### 6.2 Smart-Zone Payload Format

The assembled payload is structured so the LLM receives it in a predictable format:

```
=== COMPANY BRAIN CONTEXT ===

[ENTITY SUMMARIES - T1]
web-app::component::UserCard
  → Displays user avatar, name, and role badge. Used on Dashboard, Settings, Admin screens.
  → Calls: GET /users/{id} | Uses: UserDTO | On screens: Dashboard, Settings

api-service::api_contract::GET /users/{id}
  → Returns full user profile including roles. Requires auth token. SLA: 200ms.
  → Consumed by: UserCard, ProfileHeader (mobile-app) | Returns: UserDTO

shared-lib::data_model::UserDTO
  → Core user object. id (UUID), email, roles[], createdAt. Immutable except roles.

[DETAILED CONTEXT - T2]
<UserCard full entity JSON>
<GET /users/{id} full entity JSON>

[BUSINESS CONTEXT]
user-identity-design: Users are identified by email globally. The userId UUID is
internal only and never shown in UI. All roles are managed by the IAM service.

[BLAST RADIUS]
Direct upstream of UserCard: Dashboard (screen), Settings (screen), AdminPanel (screen)
Direct downstream of UserCard: GET /users/{id} (api), UserDTO (model), Avatar (component)
Cross-repo impact: mobile-app::ProfileHeader also calls GET /users/{id}

[ASSUMPTIONS IN SCOPE]
• user-always-has-one-role [CRITICAL]: roles.length >= 1 always true.
  Violation = RoleBadge crash. Verified in: api-service/tests/users.test.ts:L44

=== END BRAIN CONTEXT (tokens used: 1,847 / 6,000 budget) ===
```

### 6.3 Task Type → Retrieval Parameters

| Task type | Detection pattern | T2 depth | Blast radius hops | MMR λ |
|---|---|---|---|---|
| `READ` | "what does X do", "explain", "how does" | shallow | 1-hop | 0.6 |
| `WRITE` | "change", "add", "refactor", "implement" | deep | 2-hop upstream | 0.75 |
| `DEBUG` | "error", "bug", "failing", "why is" | deep | 2-hop both dirs | 0.5 |
| `AUDIT` | "review", "check", "is X safe", "what uses" | summaries only | 3-hop upstream | 0.7 |
| `ONBOARD` | "explain the whole", "overview", "how does this system" | summaries only | none | 0.4 |

### 6.4 Token Budget Allocation

```python
DEFAULT_BUDGET = {
    "total": 6000,
    "t1_summaries": 1200,     # 20% — always loaded
    "t2_detail": 3600,         # 60% — relevance-based
    "business_context": 600,   # 10% — always for T2 entities
    "blast_radius": 600,       # 10% — impact map
}

CONSERVATIVE_BUDGET = {   # For large models with many other system messages
    "total": 4000,
    "t1_summaries": 800,
    "t2_detail": 2400,
    "business_context": 400,
    "blast_radius": 400,
}

DEEP_BUDGET = {           # For dedicated brain-query sessions
    "total": 12000,
    "t1_summaries": 1500,
    "t2_detail": 7500,
    "business_context": 2000,
    "blast_radius": 1000,
}
```

---

## 7. Blast Radius Engine

### 7.1 Graph Traversal

```python
def compute_blast_radius(
    entity_id: str,
    graph: dict,            # adjacency list from graph.json
    hops: int = 2,
    direction: str = "both" # "upstream" | "downstream" | "both"
) -> dict:
    """
    Compute blast radius via BFS from entity_id.
    Returns { upstream: [...], downstream: [...], cross_repo: [...] }
    """
    upstream = set()    # what depends on this entity (will break if this changes)
    downstream = set()  # what this entity depends on

    # Build adjacency maps
    fwd_edges = {}   # entity → what it calls/uses
    rev_edges = {}   # entity → what calls/uses it

    for edge in graph["edges"]:
        fwd_edges.setdefault(edge["from"], []).append(edge["to"])
        rev_edges.setdefault(edge["to"], []).append(edge["from"])

    # BFS upstream (who depends on entity_id)
    if direction in ("upstream", "both"):
        queue = [(entity_id, 0)]
        visited = {entity_id}
        while queue:
            node, depth = queue.pop(0)
            if depth >= hops:
                continue
            for parent in rev_edges.get(node, []):
                if parent not in visited:
                    upstream.add(parent)
                    visited.add(parent)
                    queue.append((parent, depth + 1))

    # BFS downstream (what entity_id depends on)
    if direction in ("downstream", "both"):
        queue = [(entity_id, 0)]
        visited = {entity_id}
        while queue:
            node, depth = queue.pop(0)
            if depth >= hops:
                continue
            for child in fwd_edges.get(node, []):
                if child not in visited:
                    downstream.add(child)
                    visited.add(child)
                    queue.append((child, depth + 1))

    # Identify cross-repo edges
    entity_repo = entity_id.split("::")[0]
    cross_repo = [e for e in (upstream | downstream) if e.split("::")[0] != entity_repo]

    return {
        "entity": entity_id,
        "upstream": list(upstream),
        "downstream": list(downstream),
        "cross_repo": cross_repo,
        "total_affected": len(upstream | downstream)
    }
```

### 7.2 Blast Radius Severity Scoring

```python
SEVERITY_WEIGHTS = {
    "component": 1.0,
    "screen": 1.5,        # User-facing — higher impact
    "api_contract": 2.0,  # Breaking API change = multi-repo blast
    "data_model": 2.5,    # Schema change = widest blast radius
    "assumption": 3.0,    # Violated assumption = silent bugs
}

def score_blast_radius(blast: dict, entity_metadata: dict) -> float:
    """Score severity of a change to help the LLM prioritize warnings."""
    score = 0.0
    for affected_id in blast["upstream"] + blast["downstream"]:
        entity_type = affected_id.split("::")[1]
        weight = SEVERITY_WEIGHTS.get(entity_type, 1.0)
        cross_repo_multiplier = 2.0 if affected_id in blast["cross_repo"] else 1.0
        score += weight * cross_repo_multiplier
    return score
```

### 7.3 Change Impact Report

When a file changes (via git hook or CI), the harness generates an impact report:

```json
{
  "changed_entity": "api-service::api_contract::GET /users/{id}",
  "change_type": "schema_change",
  "blast_radius": {
    "upstream_count": 4,
    "downstream_count": 2,
    "cross_repo_count": 2,
    "severity_score": 14.5,
    "upstream": [
      "web-app::component::UserCard",
      "web-app::screen::Dashboard",
      "mobile-app::component::ProfileHeader",
      "web-app::screen::Settings"
    ],
    "downstream": [
      "shared-lib::data_model::UserDTO",
      "api-service::data_model::UserDB"
    ],
    "cross_repo": [
      "mobile-app::component::ProfileHeader",
      "shared-lib::data_model::UserDTO"
    ]
  },
  "risk_assessment": "HIGH — cross-repo API contract change affects 2 repos",
  "required_updates": [
    "Update UserCard to handle new response shape",
    "Update mobile-app ProfileHeader — cross-repo, requires coordination",
    "Update shared-lib UserDTO type definition"
  ]
}
```

---

## 8. MCP API Layer

The MCP server is the primary interface between Claude Code (and any LLM tooling) and the brain.

### 8.1 MCP Tool Inventory

| Tool | Description | Input | Side effects |
|---|---|---|---|
| `brain_query` | **Main entry point.** Assembles smart-zone context for a task | `{ task, entities?, token_budget?, repo? }` | None |
| `brain_get` | Get full entity by ID | `{ entity_id }` | None |
| `brain_search` | Hybrid search for entities by keyword | `{ query, entity_types?, top_k? }` | None |
| `brain_blast_radius` | Compute blast radius for an entity | `{ entity_id, hops?, direction? }` | None |
| `brain_set_component` | Write/update component entity | `{ entity }` | Writes JSON + reindexes |
| `brain_set_screen` | Write/update screen entity | `{ entity }` | Writes JSON + reindexes |
| `brain_set_api_contract` | Write/update API contract entity | `{ entity }` | Writes JSON + reindexes |
| `brain_set_assumption` | Write/update assumption | `{ entity }` | Writes JSON + reindexes |
| `brain_add_business_context` | Write/update business context | `{ entity }` | Writes JSON + reindexes |
| `brain_rebuild` | Full or incremental brain rebuild for a repo | `{ repo, mode: "full"\|"incremental" }` | Writes all JSON + reindexes |

### 8.2 brain_query Tool (Core)

This is the single tool the LLM calls most of the time:

**Input:**
```json
{
  "task": "I need to modify UserCard to show a status indicator — what should I be aware of?",
  "entities": ["UserCard"],
  "token_budget": 6000,
  "repo": "web-app"
}
```

**Output:** The full smart-zone payload (T1 + T2 + business context + blast radius), structured as shown in Section 6.2.

### 8.3 MCP Server Config (`.mcp.json`)

```json
{
  "mcpServers": {
    "company-brain": {
      "command": "python",
      "args": ["-m", "company_brain.mcp_server"],
      "env": {
        "BRAIN_ROOT": "${env:BRAIN_ROOT}",
        "QDRANT_URL": "${env:QDRANT_URL}",
        "QDRANT_API_KEY": "${env:QDRANT_API_KEY}"
      }
    }
  }
}
```

### 8.4 CLAUDE.md Integration

Every project using the brain should include in its CLAUDE.md:

```markdown
## company-brain

Before making any significant code change, call:
  brain_query(task="<your task description>", entities=["<EntityName>"])

This will give you:
- Summaries of related components, APIs, and data models
- Business context for the entities involved
- Blast radius: what else will be affected by your change
- Critical assumptions you must not violate

The brain MCP is always available. Use it liberally — it is much cheaper
than reading source files, and it includes context that isn't in the code.
```

---

## 9. Multi-Repo Federation

### 9.1 Repository Registration

A `platform-brain/repos.json` registers all repos in the platform:

```json
{
  "repos": [
    {
      "id": "web-app",
      "name": "Web Application",
      "description": "React SPA — customer-facing dashboard",
      "path": "/repos/web-app",
      "brain_path": "/repos/web-app/.brain",
      "languages": ["typescript", "tsx"],
      "domain": "frontend",
      "team": "Platform UI"
    },
    {
      "id": "api-service",
      "name": "Core API",
      "description": "Node.js REST API — all business logic endpoints",
      "path": "/repos/api-service",
      "brain_path": "/repos/api-service/.brain",
      "languages": ["typescript"],
      "domain": "backend",
      "team": "Platform API"
    },
    {
      "id": "shared-lib",
      "name": "Shared Types Library",
      "description": "TypeScript types shared across all repos",
      "path": "/repos/shared-lib",
      "brain_path": "/repos/shared-lib/.brain",
      "languages": ["typescript"],
      "domain": "shared",
      "team": "Platform"
    }
  ]
}
```

### 9.2 Cross-Repo Entity Resolution

When the extractor finds an import from another repo (e.g., `import { UserDTO } from '@company/shared-lib'`), it resolves to the canonical entity ID:

```python
IMPORT_MAP = {
    "@company/shared-lib": "shared-lib",
    "@company/api-types": "api-service",
    # ... from package.json workspaces or monorepo config
}

def resolve_import_to_entity_id(import_path: str, symbol: str) -> str | None:
    """Resolve a cross-repo import to a brain entity ID."""
    for pkg_name, repo_id in IMPORT_MAP.items():
        if import_path.startswith(pkg_name):
            # Lookup in platform index
            return platform_index.find(repo=repo_id, qualified_name=symbol)
    return None
```

### 9.3 Platform-Level Brain Query

When `brain_query` is called without a `repo` parameter, it searches across all repos:

```python
def brain_query_platform(task: str, token_budget: int) -> SmartZonePayload:
    """Query the entire platform brain."""

    # Parallel search across all repo Qdrant collections
    results = await asyncio.gather(*[
        hybrid_search(task, entity_types=ALL_TYPES, collection_prefix=repo_id)
        for repo_id in registered_repos
    ])

    # Flatten and re-rank across repos
    all_candidates = [item for sublist in results for item in sublist]
    all_candidates.sort(key=lambda x: x["score"], reverse=True)

    # Build blast radius across platform graph
    blast = compute_blast_radius(
        entity_ids=[c["id"] for c in all_candidates[:10]],
        graph=platform_graph,
        hops=2
    )

    return assemble_smart_zone(all_candidates, blast, token_budget)
```

### 9.4 Platform Dependency Graph Visualization

The platform graph can be rendered for humans as a Mermaid diagram (auto-generated by the harness):

```
brain_query({ task: "show platform dependency overview" })
→ generates Mermaid graph of top-level cross-repo edges
```

---

## 10. Update Triggers

### 10.1 On Commit (Git Hook)

Install in each repo's `.git/hooks/post-commit`:

```bash
#!/bin/bash
# Incremental brain update on commit

CHANGED_FILES=$(git diff-tree --no-commit-id -r --name-only HEAD)

for file in $CHANGED_FILES; do
  if [[ "$file" == *.ts || "$file" == *.tsx || "$file" == *.py || "$file" == *.go ]]; then
    python -m company_brain.extract --file "$file" --mode incremental
  fi
done
```

### 10.2 CI Pipeline (Full Rebuild)

Run nightly or on PRs merging to main:

```yaml
# .github/workflows/brain-rebuild.yml
name: Brain Rebuild
on:
  schedule:
    - cron: '0 2 * * *'   # Nightly at 2am
  push:
    branches: [main]

jobs:
  rebuild:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Rebuild company-brain
        run: |
          pip install company-brain-harness
          brain rebuild --repo ${{ github.event.repository.name }} --mode full
          brain push --target platform-brain-repo
```

### 10.3 Session Start (Claude Code Hook)

In `.claude/settings.json`:

```json
{
  "hooks": {
    "SessionStart": [
      {
        "matcher": ".*",
        "hooks": [
          {
            "type": "command",
            "command": "python -m company_brain.session_init --repo $(pwd) --output /tmp/brain-session-context.json"
          }
        ]
      }
    ]
  }
}
```

The `session_init` script pre-fetches a warm context payload based on recently modified files and injects it as a note into the session.

### 10.4 On-Demand (CLI)

```bash
# Map a specific file
brain map src/components/UserCard.tsx

# Full repo rebuild
brain rebuild --repo .

# Rebuild all registered repos
brain rebuild --all

# Show blast radius for an entity
brain blast-radius "web-app::component::UserCard" --hops 2

# Interactive brain query
brain query "what do I need to know before changing UserCard?"

# Push brain updates to platform repo
brain push
```

---

## 11. Business Context Layer

Business context is the most valuable and hardest-to-extract knowledge in the brain. Unlike code metadata (which can be derived from AST analysis), business context requires human input or extraction from semi-structured sources.

### 11.1 Sources

| Source | Extraction method |
|---|---|
| Product specs / PRDs | LLM extraction with structured output |
| ADRs (Architecture Decision Records) | LLM extraction + structured template |
| Confluence / Notion pages | MCP connectors → LLM extraction |
| Slack decision threads | Slack MCP → LLM extraction |
| PR descriptions | Git hook → LLM extraction |
| Jira/Linear tickets | PM tool MCP → LLM extraction |
| Inline code comments tagged `@business` | Static extraction |
| Interview sessions with engineers | Claude Code session → structured capture |

### 11.2 Business Context Extraction Prompt

When extracting business context from a source document:

```
You are extracting business context for the company-brain knowledge store.

Source document:
{document_text}

Extract and return a JSON array of business context entities. For each entity:
- qualified_name: short kebab-case identifier
- domain: the business domain (e.g., "Identity & Auth", "Billing", "Dashboard")
- t1_summary: 1-2 sentences — what an engineer needs to know
- content: full context (decisions, reasoning, constraints, user journey notes)
- relates_to: list of entity IDs this context applies to (use best-guess entity IDs)
- source: where this came from

Focus on:
- Non-obvious constraints ("we can't do X because of Y contract")
- Product decisions with downstream code impact
- User journey steps that explain why features are built a certain way
- Domain invariants ("users always have exactly one org")
- Integration contracts with third parties

Return ONLY the JSON array. No explanation.
```

### 11.3 Business Context in the Smart Zone

Business context is injected at 100% rate for any entity that appears in T2. It is never omitted — it's the "why" that makes the context useful.

---

## 12. Implementation Roadmap

### Phase 1 — Foundation (Week 1-2)

Build the core: JSON schema + simple extractor + MCP server. No search yet.

- [ ] Define all entity schemas (Section 3)
- [ ] Build tree-sitter extraction for 1 language (TypeScript)
- [ ] Implement code tokenizer
- [ ] Write minimal MCP server with `brain_get`, `brain_set_*`, `brain_query` (simple)
- [ ] Add `brain_query` to CLAUDE.md of one repo
- [ ] Manually populate brain for 5-10 entities

### Phase 2 — Search (Week 3-4)

Add hybrid retrieval for actual smart-zone performance.

- [ ] Set up Qdrant locally
- [ ] Integrate BM25S index with code tokenizer
- [ ] Integrate all-MiniLM-L6-v2 embeddings
- [ ] Implement RRF fusion
- [ ] Implement MMR deduplication
- [ ] Upgrade `brain_query` to use hybrid search

### Phase 3 — Blast Radius (Week 5-6)

Build the dependency graph and blast radius engine.

- [ ] Implement graph.json schema and adjacency list
- [ ] Extend extractor to emit graph edges (imports, API calls, component usage)
- [ ] Implement BFS blast radius traversal
- [ ] Add `brain_blast_radius` MCP tool
- [ ] Integrate blast radius into `brain_query` payload

### Phase 4 — Multi-Repo (Week 7-8)

Federation across all repos.

- [ ] Set up platform-brain repo
- [ ] Implement cross-repo import resolution
- [ ] Build platform graph aggregator
- [ ] Add platform-level `brain_query`
- [ ] Register all repos in `repos.json`

### Phase 5 — Automation (Week 9-10)

Wiring up the update triggers.

- [ ] Git post-commit hook for incremental updates
- [ ] CI pipeline for nightly full rebuild
- [ ] Claude Code SessionStart hook for warm context
- [ ] On-demand CLI (`brain map`, `brain rebuild`, `brain query`)

### Phase 6 — Business Context (Week 11-12)

The irreplaceable layer.

- [ ] Connect Confluence/Notion/Slack MCPs for source extraction
- [ ] Build extraction prompt + structured output pipeline
- [ ] Seed business context for all critical entities
- [ ] Add business context engineer workflow (interview sessions)

---

## Appendix A: Tech Stack Summary

| Layer | Technology | Why |
|---|---|---|
| AST parsing | tree-sitter | Language-agnostic, production-grade, 40+ langs |
| BM25 | bm25s | 500x faster than rank-bm25, actively maintained |
| Sparse neural | SPLADE++ | Better recall than raw BM25, inverted-index compatible |
| Dense embeddings | all-MiniLM-L6-v2 | 80% of SOTA performance, free, self-hosted |
| Hybrid search + RRF | Qdrant | Native sparse+dense support, local or cloud |
| MMR diversity | Custom (scikit-learn cosine_similarity) | Token budget optimization |
| Dependency graph | JSON adjacency list → Memgraph | Start simple, scale to in-memory graph DB |
| Context assembly | Custom tiered pipeline (T1/T2/T3) | RAPTOR-inspired, task-aware |
| MCP server | Python (stdio) | Direct Claude Code integration |
| Brain storage | JSON files (git-tracked) | Human-readable, versionable SOT |
| Business context extraction | Claude Haiku (LLM) | Cheap, fast structured output |

## Appendix B: Environment Variables

```bash
BRAIN_ROOT=/path/to/platform-brain      # Root of the platform brain
BRAIN_REPO_ID=web-app                   # Current repo identifier
QDRANT_URL=http://localhost:6333        # Qdrant instance
QDRANT_API_KEY=                         # Optional for local
BRAIN_TOKEN_BUDGET=6000                 # Default smart-zone budget
BRAIN_EMBEDDING_MODEL=all-MiniLM-L6-v2 # Sentence transformer model
BRAIN_LLM_MODEL=claude-haiku-4-5-20251001  # For T1 summary generation
```

---

*Sources: BM25S (bm25s GitHub), Qdrant hybrid search docs, Sourcegraph BM25F blog, RRF (OpenSearch / Azure AI Search docs), SPLADE++ (Naver GitHub), tree-sitter.github.io, RAPTOR paper, MMR (Elastic search labs), Memgraph vs Neo4j benchmarks — verified 2026-05-07*
