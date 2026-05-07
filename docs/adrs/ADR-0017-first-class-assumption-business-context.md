# ADR-0017: Promote `assumption` and `business_context` to first-class graph nodes

**Status:** Proposed
**Date:** 2026-05-07
**Effort:** 2 days
**Depends on:** ADR-0013 (URN identity)
**Unblocks:** ADR-0018 (smart-zone surfaces them in T1)

---

## Context

Today both assumptions ("the user always has at least one role") and business contexts ("user identity is always email-globally-unique") live as rows in `node_context` with `context_type IN ('invariant', 'risk_flag', 'business_context', 'llm_synthesis')`. They reference a parent node via `node_id` but cannot themselves be the source or target of an edge. Effects:

- `RELIES_ON` edges (function_node → assumption) have nowhere to land.
- `brain blast-radius <assumption>` cannot return the function_nodes that depend on it.
- `brain query "what business context applies to UserCard"` requires a join through `node_context`, not a graph traversal.
- The harness §3.6 / §3.7 schema treats both as first-class entities.

## Decision

Promote `assumption` and `business_context` to first-class entities in the same way `component` / `api_contract` already are. They become rows in `nodes` with `entity_type IN ('assumption','business_context')`, get URNs, and are written through `BrainStore`. The `node_context` table is retained for git_commit / pull_request / ticket / user_annotation / risk_flag context — those remain attached-to-a-node, not nodes themselves.

Add two edge types:
- `RELIES_ON` — `(function_node | component | api_contract) → assumption`
- `EXPLAINS` — `business_context → (any entity)` (one business_context can explain many entities)

## Implementation

### Flyway migration: `V4__assumption_business_context_nodes.sql`

```sql
-- ============================================================
-- V4: Promote assumption + business_context to first-class nodes
-- See ADR-0017.
-- ============================================================

-- 1. Migrate existing node_context rows of types 'invariant' and 'business_context'
--    into the nodes table.
WITH source AS (
  SELECT
    nc.id            AS old_ctx_id,
    nc.workspace_id,
    n.urn            AS parent_urn,
    n.repo           AS parent_repo,
    nc.context_type,
    nc.title         AS qualified_name,
    encode(nc.body, 'escape') AS body_text,
    nc.confidence,
    nc.metadata,
    nc.created_at
  FROM node_context nc
  JOIN nodes n ON n.id = nc.node_id
  WHERE nc.context_type IN ('invariant', 'business_context', 'llm_synthesis')
)
INSERT INTO nodes (
    id, workspace_id, node_type, entity_type, external_id, urn, name, metadata
)
SELECT
    gen_random_uuid(),
    s.workspace_id,
    CASE WHEN s.context_type = 'invariant' THEN 'Assumption'
         ELSE 'BusinessContext' END        AS node_type,
    CASE WHEN s.context_type = 'invariant' THEN 'assumption'
         ELSE 'business_context' END       AS entity_type,
    -- legacy external_id placeholder; URN is the canonical key
    'legacy_' || s.old_ctx_id::text       AS external_id,
    -- New URN: derive from parent's URN repo + entity_type + slugged title
    'urn:cb:' ||
      split_part(s.parent_urn, ':', 3) || ':' ||      -- tenant
      'code:' ||
      s.parent_repo || ':' ||
      CASE WHEN s.context_type = 'invariant' THEN 'assumption'
           ELSE 'business_context' END || ':' ||
      regexp_replace(coalesce(s.qualified_name, 'untitled-' || s.old_ctx_id::text),
                     '[^A-Za-z0-9._-]+', '_', 'g')   AS urn,
    coalesce(s.qualified_name, 'untitled')           AS name,
    jsonb_build_object(
      'body',          s.body_text,
      'confidence',    s.confidence,
      'origin',        'migrated_from_node_context',
      'old_node_context_id', s.old_ctx_id::text,
      'created_at',    s.created_at
    ) || coalesce(s.metadata, '{}'::jsonb)
FROM source s
ON CONFLICT (workspace_id, node_type, external_id) DO NOTHING;

-- 2. Backfill RELIES_ON edges from node_context.applies_to_fields where present.
-- (Existing data may not have applies_to_fields populated; new extractor writes
--  these directly as edges, not as text fields.)

-- 3. Add CHECK to allow new edge types.
-- The edges.edge_type column is TEXT; no constraint to relax.
-- Just document the new edge types in code:
--   RELIES_ON  — entity → assumption
--   EXPLAINS   — business_context → entity
COMMENT ON COLUMN edges.edge_type IS
  'CALLS | EXPOSES | CONSUMES_FIELD | READS_TABLE | WRITES_COLUMN | OWNS | IMPORTS '
  '| RENDERS_FIELD | CALLS_ENDPOINT | VALIDATES | DEPENDS_ON | RELIES_ON | EXPLAINS';
```

### Java backend changes

Add two enum entries:

`company-brain-backend/src/main/java/com/companybrain/model/EntityType.java` (create if absent):
```java
package com.companybrain.model;

public enum EntityType {
    COMPONENT("component"),
    SCREEN("screen"),
    API_CONTRACT("api_contract"),
    DATA_MODEL("data_model"),
    ASSUMPTION("assumption"),
    BUSINESS_CONTEXT("business_context"),
    FUNCTION_NODE("function_node");

    private final String value;
    EntityType(String value) { this.value = value; }
    public String value() { return value; }
}
```

`company-brain-backend/src/main/java/com/companybrain/model/EdgeType.java` (extend if exists):
```java
public enum EdgeType {
    CALLS, EXPOSES, CONSUMES_FIELD, READS_TABLE, WRITES_COLUMN, OWNS,
    IMPORTS, RENDERS_FIELD, CALLS_ENDPOINT, VALIDATES, DEPENDS_ON,
    RELIES_ON,    // NEW (ADR-0017)
    EXPLAINS;     // NEW (ADR-0017)
}
```

Update DTOs to allow these new types in JSON:
- `AnnotationRequest` / `AnnotationResponse` accept `entity_type=assumption|business_context`.
- `EdgeDto.edge_type` allows `RELIES_ON|EXPLAINS`.

### Python extractor changes

#### `companybrain/pipeline/business_semantics_extractor.py`

The extractor already produces business_context content. Change its output to emit `BrainEntity(entity_type='business_context', ...)` instead of `BusinessContext` rows attached to a parent node. Sketch:

```python
# After synthesising a business_context for a parent entity:
bc = BrainEntity(
    id=to_urn(tenant=slug, domain="code", repo=parent.repo,
              entity_type="business_context",
              qualified_name=f"{parent.qualified_name}__rationale"),
    entity_type="business_context",
    repo=parent.repo,
    file=parent.file,
    qualified_name=f"{parent.qualified_name}__rationale",
    t1_summary=synthesised_t1,
    metadata={"body": full_body, "domain": domain_tag, "source": source_refs},
    relationships=[{
        "target_id": parent.id,
        "edge_type": "EXPLAINS",
        "confidence": 0.9,
        "source": "llm_synthesis",
    }],
)
yield bc
```

#### Assumption mining (new module: `companybrain/pipeline/assumption_miner.py`)

Heuristic patterns from harness §4.3, deterministic (no LLM):

```python
"""
Static assumption miner — extracts invariants from source code patterns.

Heuristics:
  - JSDoc/docstring '@assumption' tags
  - Non-null assertions in TypeScript: `user!.role`
  - Guard-clause throws: `if (!user) throw`
  - Assertion library calls: `assert(...)`, `invariant(...)`
  - Zod / Pydantic .parse() — runtime contract
"""
from __future__ import annotations
import re
from dataclasses import dataclass

from companybrain.collectors.code_tracer import CodeUnit
from companybrain.store.base import BrainEntity
from companybrain.store.identity import to_urn, workspace_slug_for

_PATTERNS = {
    "explicit_jsdoc":    re.compile(r"@assumption\s+(.+)"),
    "explicit_python":   re.compile(r"#\s*ASSUMPTION:\s*(.+)"),
    "explicit_js":       re.compile(r"//\s*ASSUME:\s*(.+)"),
    "non_null_ts":       re.compile(r"(\w+)!\.(\w+)"),
    "guard_throw":       re.compile(r"if\s*\(\s*!(.+?)\s*\)\s*throw"),
    "assert":            re.compile(r"\bassert\s*\((.+?)\)"),
    "invariant":         re.compile(r"\binvariant\s*\((.+?)\)"),
    "zod_parse":         re.compile(r"\.parse\s*\((.+?)\)"),
}


def mine_assumptions(unit: CodeUnit, parent: BrainEntity, *, workspace_id: str) -> list[BrainEntity]:
    """Return a list of BrainEntity(entity_type='assumption') for this code unit."""
    out: list[BrainEntity] = []
    slug = workspace_slug_for(workspace_id)
    seen: set[str] = set()

    for pattern_name, pattern in _PATTERNS.items():
        for m in pattern.finditer(unit.content or ""):
            statement = m.group(1).strip()[:200]
            qname = f"{parent.qualified_name}__{pattern_name}__{hash(statement) & 0xffff:04x}"
            if qname in seen:
                continue
            seen.add(qname)
            urn = to_urn(tenant=slug, domain="code", repo=parent.repo,
                         entity_type="assumption", qualified_name=qname)
            out.append(BrainEntity(
                id=urn, entity_type="assumption", repo=parent.repo, file=unit.file_path,
                qualified_name=qname,
                t1_summary=f"{pattern_name}: {statement}",
                metadata={
                    "statement": statement,
                    "pattern": pattern_name,
                    "severity": _severity_for(pattern_name),
                    "origin": "static_extractor",
                },
                relationships=[{
                    "target_id": parent.id,
                    "edge_type": "RELIES_ON",
                    "confidence": _confidence_for(pattern_name),
                    "source": "static_analysis",
                }],
            ))
    return out


def _severity_for(pattern: str) -> str:
    return {
        "non_null_ts":     "medium",
        "guard_throw":     "high",
        "assert":          "high",
        "invariant":       "critical",
        "zod_parse":       "medium",
    }.get(pattern, "low")


def _confidence_for(pattern: str) -> float:
    return {
        "explicit_jsdoc":  0.95,
        "explicit_python": 0.95,
        "explicit_js":     0.95,
        "non_null_ts":     0.7,
        "guard_throw":     0.85,
        "assert":          0.9,
        "invariant":       0.95,
        "zod_parse":       0.8,
    }.get(pattern, 0.5)
```

#### Wire into orchestrator

After Stage 1 entity extraction, run `mine_assumptions` for each unit and append the resulting BrainEntities to the write batch:

```python
# pipeline/orchestrator.py — after Stage 1, before Stage 2:
from companybrain.pipeline.assumption_miner import mine_assumptions

assumption_entities: list[BrainEntity] = []
for unit, unit_entities in unit_results:   # the result of Stage 1
    for parent in unit_entities:
        # parent is the LLM-extracted entity; wrap to BrainEntity for URN
        parent_be = _to_brain_entity(parent, contexts.get(parent.external_id),
                                     None, [], request.workspace_id)
        assumption_entities.extend(mine_assumptions(unit, parent_be,
                                                     workspace_id=request.workspace_id))
```

The Postgres / Neo4j / Qdrant fanout writes these the same as any other entity.

### React UI (no breaking changes)

The frontend reads `nodes` and `edges`. Once new entity_types exist, they appear in the graph. Recommend adding a colour for `assumption` (red) and `business_context` (blue) in `DependencyGraph.jsx`; this is a one-liner styling change, not in scope here.

## Test plan

`tests/unit/pipeline/test_assumption_miner.py`:

```python
from companybrain.collectors.code_tracer import CodeUnit
from companybrain.store.base import BrainEntity
from companybrain.pipeline.assumption_miner import mine_assumptions


def _parent():
    return BrainEntity(
        id="urn:cb:dev:code:r:component:UserCard",
        entity_type="component", repo="r", file="UserCard.tsx",
        qualified_name="UserCard",
    )


def test_extracts_jsdoc_assumption():
    unit = CodeUnit(file_path="UserCard.tsx", repo_name="r", role="component",
                    class_name="UserCard",
                    content="/** @assumption userId is always a UUID */\nfunction f(){}",
                    language="typescript")
    out = mine_assumptions(unit, _parent(), workspace_id="ws")
    assert any("userId is always a UUID" in a.t1_summary for a in out)


def test_extracts_non_null_assertion():
    unit = CodeUnit(file_path="x.ts", repo_name="r", role="component", class_name="X",
                    content="const r = user!.role;", language="typescript")
    out = mine_assumptions(unit, _parent(), workspace_id="ws")
    assert any(a.metadata["pattern"] == "non_null_ts" for a in out)
    assert all(a.relationships[0]["edge_type"] == "RELIES_ON" for a in out)


def test_no_duplicates_on_same_statement():
    unit = CodeUnit(file_path="x.ts", repo_name="r", role="component", class_name="X",
                    content="assert(id != null);\nassert(id != null);",
                    language="typescript")
    out = mine_assumptions(unit, _parent(), workspace_id="ws")
    statements = [a.metadata["statement"] for a in out]
    assert statements.count("id != null") == 1
```

Migration test:

```bash
psql -c "
  -- 1. Existing data check (run BEFORE migration on a snapshot)
  SELECT count(*) FROM node_context WHERE context_type IN ('invariant','business_context');
"
make db-migrate
psql -c "
  -- 2. After-migration: same count + new entity_types in nodes
  SELECT entity_type, count(*) FROM nodes
   WHERE entity_type IN ('assumption','business_context')
   GROUP BY entity_type;
"
```

## Acceptance criteria

- [ ] Flyway V4 runs cleanly on a snapshot of dev DB.
- [ ] Pre/post counts: every `node_context` row of `context_type IN ('invariant', 'business_context')` has a corresponding `nodes` row with `entity_type IN ('assumption', 'business_context')`.
- [ ] Java `EntityType.ASSUMPTION` and `EntityType.BUSINESS_CONTEXT` exist; `EdgeType.RELIES_ON` and `EXPLAINS` exist.
- [ ] `companybrain/pipeline/assumption_miner.py` exists with the seven heuristics.
- [ ] Orchestrator emits assumption entities after Stage 1 with `RELIES_ON` back-edges.
- [ ] Orchestrator's business_context_extractor emits first-class `business_context` entities with `EXPLAINS` edges.
- [ ] Unit tests for `assumption_miner` pass.
- [ ] After running the pipeline on a TypeScript file with `@assumption` JSDoc, the resulting `.brain/assumption/<qname>.json` exists.
- [ ] `brain blast-radius <assumption_urn>` returns at least the entities that have `RELIES_ON` edges.
- [ ] Existing `node_context` rows are not deleted (kept for safety; remove in a future ADR after stability).

## Verification commands

```bash
make db-migrate
make ai-test-pipeline REPO=./pilot

# Confirm new entity types in Postgres
psql -c "SELECT entity_type, count(*) FROM nodes WHERE entity_type IN ('assumption','business_context') GROUP BY entity_type;"

# Confirm new edge types
psql -c "SELECT edge_type, count(*) FROM edges WHERE edge_type IN ('RELIES_ON','EXPLAINS') GROUP BY edge_type;"

# Confirm Neo4j has Assumption / BusinessContext nodes
cypher-shell -u neo4j -p password "MATCH (n) WHERE n.entity_type IN ['assumption','business_context'] RETURN n.entity_type, count(n);"

# Confirm .brain/ has assumption/ and business_context/ dirs
ls pilot/.brain/assumption/ pilot/.brain/business_context/
```

## Rollback

V4 migration is reversible by deleting the inserted rows:

```sql
DELETE FROM nodes WHERE metadata->>'origin' = 'migrated_from_node_context';
```

The Python extractor changes are revert-clean (`git revert`). The Java enum additions are additive and do not break existing code.

## Out of scope

- **Removing `node_context` rows post-migration.** Kept for safety. A follow-up ADR can drop the `invariant` / `business_context` types from `node_context` once the new system is stable.
- **LLM-extracted assumptions.** This ADR adds the deterministic miner. LLM-extracted assumptions (more sophisticated) are a follow-up.
- **Cross-repo `RELIES_ON` edges.** Stage 2 — assumes cross-repo URNs resolve correctly first.
- **Severity-weighted blast-radius scoring.** Defined in harness §7.2; implementation ADR is a follow-up tied to the smart-zone assembler (ADR-0018).
