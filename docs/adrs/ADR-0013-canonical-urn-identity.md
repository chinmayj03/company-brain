# ADR-0013: Canonical URN identity for entities across all stores

**Status:** Proposed
**Date:** 2026-05-07
**Effort:** 3 days
**Depends on:** ADR-0012 (BrainStore exists; the URN flows through it)
**Unblocks:** ADR-0015, ADR-0017, ADR-0018, ADR-0019
**Builds on:** ADR-0001 (URN identity scheme — drafted but not yet enforced)

---

## Context

Today the same entity has two identities:

- **Postgres** (`nodes.external_id`): `backend/src/payment.ts::chargePayment`
- **Neo4j** (`urn:cb:llm:{workspace_id}:{file_path}:{entity_name}`): `urn:cb:llm:dev:backend/src/payment.ts:chargePayment`
- **Qdrant** (would-be): no scheme defined yet.
- **JSON SOT** (introduced in ADR-0012): `<repo>::<entity_type>::<qualified_name>`

Cross-store joins are currently impossible without per-call translation. ADR-0001 drafts a URN scheme but does not enforce it. Stage 1's smart-zone assembly (ADR-0018) needs to merge entity attributes from all three stores, which requires one canonical ID.

## Decision

Adopt one canonical URN per entity across **all** stores:

```
urn:cb:{tenant}:{domain}:{repo}:{entity_type}:{qualified_name}
```

Examples:

```
urn:cb:acme:code:web-app:component:UserCard
urn:cb:acme:code:api-service:api_contract:GET_/users/{id}
urn:cb:acme:code:shared-lib:data_model:UserDTO
urn:cb:acme:code:shared-lib:assumption:user-always-has-one-role
```

Where:
- `tenant` = the workspace slug (default `dev` for Stage 1; replaces today's `workspace_id` UUID at the URN layer — UUID is retained as the RLS key in Postgres).
- `domain` = `code` for now; reserved for later (`support`, `product`, `runtime`, `design`, `infra`).
- `repo` = the repo identifier from `repos.json` (introduced in Stage 2; defaults to the single-repo workspace in Stage 1).
- `entity_type` ∈ {component, screen, api_contract, data_model, assumption, business_context, function_node}.
- `qualified_name` = the structural FQN (e.g. `UserCard`, `GET_/users/{id}` with path encoded).

Postgres `external_id`, Neo4j node `id` property, Qdrant point ID, and `.brain/` index keys all use the same URN string.

## Implementation

### Module: `store/identity.py`

```python
"""
URN identity for brain entities.

Format: urn:cb:{tenant}:{domain}:{repo}:{entity_type}:{qualified_name}

Round-trip-safe encoding for qualified_names that contain special characters
(e.g. HTTP paths like '/users/{id}') uses percent-encoding for ':' '/' '%'
and a recognisable plus-prefix for HTTP method+path entities.
"""
from __future__ import annotations
from dataclasses import dataclass
from urllib.parse import quote, unquote

URN_SCHEME = "urn:cb"
URN_SEPARATOR = ":"
DEFAULT_TENANT = "dev"
DEFAULT_DOMAIN = "code"
ALLOWED_ENTITY_TYPES = frozenset({
    "component", "screen", "api_contract", "data_model",
    "assumption", "business_context", "function_node",
})


@dataclass(frozen=True)
class URNParts:
    tenant: str
    domain: str
    repo: str
    entity_type: str
    qualified_name: str

    def to_urn(self) -> str:
        return URN_SEPARATOR.join([
            URN_SCHEME, self.tenant, self.domain, self.repo,
            self.entity_type, _encode(self.qualified_name),
        ])


def to_urn(*, tenant: str, domain: str, repo: str,
           entity_type: str, qualified_name: str) -> str:
    if entity_type not in ALLOWED_ENTITY_TYPES:
        raise ValueError(f"Unknown entity_type: {entity_type}")
    return URNParts(tenant, domain, repo, entity_type, qualified_name).to_urn()


def parse_urn(urn: str) -> URNParts:
    if not urn.startswith(f"{URN_SCHEME}{URN_SEPARATOR}"):
        raise ValueError(f"Not a CB URN: {urn}")
    parts = urn.split(URN_SEPARATOR, 6)   # at most 7 segments — qname may contain ':' encoded
    if len(parts) < 7:
        raise ValueError(f"Malformed URN (too few segments): {urn}")
    _, _, tenant, domain, repo, entity_type, qname_encoded = parts[:7]
    return URNParts(
        tenant=tenant, domain=domain, repo=repo,
        entity_type=entity_type, qualified_name=_decode(qname_encoded),
    )


# ── Migration helpers (legacy → URN) ─────────────────────────────────────────

def from_legacy_postgres(*, workspace_slug: str, node_type: str,
                          legacy_external_id: str, repo: str = "monorepo") -> str:
    """
    Translate the existing Postgres external_id 'backend/src/payment.ts::chargePayment'
    into a URN. node_type is mapped via NODE_TYPE_TAXONOMY.
    """
    entity_type = NODE_TYPE_TAXONOMY.get(node_type, "component")
    qname = legacy_external_id.split("::")[-1] if "::" in legacy_external_id else legacy_external_id
    return to_urn(tenant=workspace_slug, domain=DEFAULT_DOMAIN, repo=repo,
                  entity_type=entity_type, qualified_name=qname)


def from_legacy_neo4j(legacy_urn: str, *, repo: str = "monorepo") -> str:
    """
    'urn:cb:llm:{ws}:{file_path}:{entity_name}' → canonical URN.
    """
    parts = legacy_urn.split(URN_SEPARATOR)
    if len(parts) < 6 or parts[0:3] != ["urn", "cb", "llm"]:
        raise ValueError(f"Not a legacy Neo4j URN: {legacy_urn}")
    _, _, _, workspace, *rest = parts
    file_path, qname = (rest[0], rest[-1]) if len(rest) >= 2 else ("", rest[0])
    # Default entity_type = component; refined later in the migration script.
    return to_urn(tenant=workspace, domain=DEFAULT_DOMAIN, repo=repo,
                  entity_type="component", qualified_name=qname)


# ── node_type → entity_type taxonomy ─────────────────────────────────────────

NODE_TYPE_TAXONOMY: dict[str, str] = {
    # Existing free-form node_type values → 6-type harness taxonomy
    "ApiEndpoint": "api_contract",
    "Function": "component",       # default; promoted to function_node when ADR-0021 lands
    "CodeFunction": "component",
    "Class": "component",
    "Service": "component",
    "FrontendComponent": "component",
    "SchemaField": "data_model",
    "DatabaseTable": "data_model",
    "DatabaseColumn": "data_model",
    "DatabaseQuery": "data_model",
    "ExternalService": "component",
    "ConfigKey": "component",
    "SharedType": "data_model",
}


# ── Encoding ─────────────────────────────────────────────────────────────────

def _encode(qname: str) -> str:
    # Encode ':' so it doesn't break URN segmentation. Encode '/' for path-like names.
    return quote(qname, safe="._-{}")


def _decode(encoded: str) -> str:
    return unquote(encoded)
```

### Flyway migration: `V2__urn_external_ids.sql`

```sql
-- ============================================================
-- V2: Migrate Postgres external_id to canonical URN format
-- See ADR-0013.
-- ============================================================

-- 1. Add a new column for the URN; keep old external_id during the transition
ALTER TABLE nodes ADD COLUMN urn TEXT;

-- 2. Backfill — every existing node gets a URN derived from its current
-- external_id. Workspace slug comes from workspaces.slug.
UPDATE nodes n
SET urn = 'urn:cb:' || w.slug || ':code:monorepo:' ||
          CASE n.node_type
            WHEN 'ApiEndpoint'      THEN 'api_contract'
            WHEN 'FrontendComponent' THEN 'component'
            WHEN 'SchemaField'      THEN 'data_model'
            WHEN 'DatabaseTable'    THEN 'data_model'
            WHEN 'DatabaseColumn'   THEN 'data_model'
            WHEN 'DatabaseQuery'    THEN 'data_model'
            WHEN 'SharedType'       THEN 'data_model'
            ELSE 'component'
          END || ':' ||
          replace(replace(replace(
            CASE
              WHEN position('::' IN n.external_id) > 0
                THEN split_part(n.external_id, '::', array_length(string_to_array(n.external_id, '::'), 1))
              ELSE n.external_id
            END,
          '/', '%2F'), ' ', '%20'), '%', '%25')
FROM workspaces w
WHERE n.workspace_id = w.id;

-- 3. Constraint: every URN must follow the canonical format.
ALTER TABLE nodes ADD CONSTRAINT chk_urn_format
  CHECK (urn ~ '^urn:cb:[a-z0-9_-]+:[a-z]+:[a-zA-Z0-9_-]+:[a-z_]+:.+$');

-- 4. Make URN unique per workspace
CREATE UNIQUE INDEX uq_nodes_urn ON nodes (workspace_id, urn);

-- 5. NOT NULL after backfill
ALTER TABLE nodes ALTER COLUMN urn SET NOT NULL;

-- 6. Old external_id column stays for one release for rollback safety;
--    new code reads from urn. Remove external_id in V5 after a stable release.

-- ============================================================
-- Edge identity also needs URN-keyed source/target. We keep edges.source_id
-- and edges.target_id as UUIDs (FKs to nodes.id) — the URN is on the node
-- side, not the edge side. No change needed to edges.
-- ============================================================
```

### Flyway migration: `V3__entity_type_column.sql`

```sql
-- Add a constrained entity_type column derived from node_type.
ALTER TABLE nodes ADD COLUMN entity_type TEXT;

UPDATE nodes SET entity_type = CASE node_type
  WHEN 'ApiEndpoint'      THEN 'api_contract'
  WHEN 'FrontendComponent' THEN 'component'
  WHEN 'SchemaField'      THEN 'data_model'
  WHEN 'DatabaseTable'    THEN 'data_model'
  WHEN 'DatabaseColumn'   THEN 'data_model'
  WHEN 'DatabaseQuery'    THEN 'data_model'
  WHEN 'SharedType'       THEN 'data_model'
  ELSE 'component'
END;

ALTER TABLE nodes
  ALTER COLUMN entity_type SET NOT NULL,
  ADD CONSTRAINT chk_entity_type
    CHECK (entity_type IN ('component','screen','api_contract','data_model',
                           'assumption','business_context','function_node'));

CREATE INDEX idx_nodes_entity_type ON nodes (workspace_id, entity_type);
```

### Neo4j migration script

`apps/api/src/scripts/migrate-urn.ts` (Bun-runnable):

```typescript
/**
 * One-shot Neo4j migration to canonical URN per ADR-0013.
 * Reads existing nodes whose `id` matches the legacy 'urn:cb:llm:...' prefix
 * and rewrites them to 'urn:cb:{tenant}:code:{repo}:{entity_type}:{qname}'.
 *
 * Run with: bun run apps/api/src/scripts/migrate-urn.ts <tenant> <repo>
 *
 * Idempotent: re-running on already-migrated data is a no-op.
 */
import { GraphClient } from "@company-brain/graph";

const [, , tenantArg, repoArg] = process.argv;
const tenant = tenantArg ?? "dev";
const repo = repoArg ?? "monorepo";

const graph = new GraphClient({
  uri: process.env["NEO4J_URI"] ?? "bolt://localhost:7687",
  user: process.env["NEO4J_USER"] ?? "neo4j",
  password: process.env["NEO4J_PASSWORD"] ?? "password",
});

await graph.runWrite(
  `MATCH (n) WHERE n.id STARTS WITH 'urn:cb:llm:' AND NOT n.id STARTS WITH $newPrefix
   WITH n, split(n.id, ':') AS parts
   WITH n, parts[size(parts)-1] AS qname
   SET n.id = $newPrefix + ':' + coalesce(n.entity_type, 'component') + ':' + qname,
       n.legacy_id = n.id
   RETURN count(n) AS migrated`,
  { newPrefix: `urn:cb:${tenant}:code:${repo}` }
);

console.log(`URN migration complete for tenant=${tenant} repo=${repo}`);
process.exit(0);
```

### Edits to existing code

- `companybrain/store/base.py` — `BrainEntity.id` is now a URN. Update docstring; behaviour unchanged.
- `companybrain/store/json_store.py` — `_qname_to_filename()` already sanitises; URN parsing happens at write time:
  ```python
  from companybrain.store.identity import parse_urn
  parts = parse_urn(entity.id)
  entity_file = self.root / parts.entity_type / f"{_qname_to_filename(parts.qualified_name)}.json"
  ```
- `companybrain/graph/neo4j_writer.py` — replace internal URN construction with `to_urn(...)`. Strip the `urn:cb:llm:` shortcut.
- `companybrain/graph/java_client.py` — `flush()` payload sets `urn` field on each node entry; Java backend writes it to `nodes.urn`.
- `company-brain-backend/src/main/java/com/companybrain/dto/NodeDto.java` — add `urn` field; nullable during transition.
- `company-brain-backend/src/main/java/com/companybrain/service/PipelineService.java` (referenced; if absent, write the upsert here) — write URN to `nodes.urn`, keep `external_id` for one release.

### Helper for the orchestrator

In `store/identity.py`, expose:

```python
def workspace_slug_for(workspace_id: str) -> str:
    """Resolve workspace UUID → slug. In Stage 1 the slug is hardcoded
    'dev'; ADR-0016 reads it from a workspace registry."""
    return os.getenv("BRAIN_WORKSPACE_SLUG", "dev")
```

And in `pipeline/orchestrator.py`, when constructing `BrainEntity`s:

```python
from companybrain.store.identity import to_urn, workspace_slug_for, NODE_TYPE_TAXONOMY

urn = to_urn(
    tenant=workspace_slug_for(request.workspace_id),
    domain="code",
    repo=request.repos[0].name or "monorepo",
    entity_type=NODE_TYPE_TAXONOMY.get(ee.entity_type, "component"),
    qualified_name=ee.name,
)
```

## Test plan

### Unit tests

`tests/unit/store/test_identity.py`:

```python
import pytest
from companybrain.store.identity import (
    to_urn, parse_urn, from_legacy_postgres, from_legacy_neo4j,
    URNParts, ALLOWED_ENTITY_TYPES,
)


def test_round_trip():
    urn = to_urn(tenant="acme", domain="code", repo="web",
                 entity_type="component", qualified_name="UserCard")
    parts = parse_urn(urn)
    assert parts == URNParts("acme", "code", "web", "component", "UserCard")


def test_path_qname_round_trip():
    urn = to_urn(tenant="acme", domain="code", repo="api",
                 entity_type="api_contract",
                 qualified_name="GET /users/{id}")
    parts = parse_urn(urn)
    assert parts.qualified_name == "GET /users/{id}"


def test_rejects_unknown_entity_type():
    with pytest.raises(ValueError):
        to_urn(tenant="a", domain="code", repo="r",
               entity_type="weird", qualified_name="X")


def test_legacy_postgres_translation():
    urn = from_legacy_postgres(workspace_slug="dev", node_type="ApiEndpoint",
                                legacy_external_id="backend/src/p.ts::charge",
                                repo="monorepo")
    assert urn == "urn:cb:dev:code:monorepo:api_contract:charge"


def test_legacy_neo4j_translation():
    legacy = "urn:cb:llm:dev:src/Foo.ts:Foo"
    urn = from_legacy_neo4j(legacy, repo="monorepo")
    assert urn.startswith("urn:cb:dev:code:monorepo:component:")
```

### Integration test

`tests/integration/test_urn_alignment.py`:

```python
"""
After running the orchestrator on the pilot repo, every entity's URN must
appear identically in:
  - .brain/index.json
  - postgres nodes.urn
  - neo4j (n.id)
"""
import json, asyncpg, neo4j

# 1. Read .brain/index.json
brain_urns = set(json.load(open("pilot/.brain/index.json")).keys())

# 2. Read Postgres nodes.urn
pg_urns = set(...)

# 3. Read Neo4j (n.id)
n4j_urns = set(...)

assert brain_urns == pg_urns == n4j_urns
```

## Acceptance criteria

- [ ] `companybrain/store/identity.py` exists with `to_urn`, `parse_urn`, `URNParts`, `from_legacy_postgres`, `from_legacy_neo4j`, `NODE_TYPE_TAXONOMY`.
- [ ] All identity unit tests pass.
- [ ] Flyway V2 migration runs cleanly on a copy of the dev DB and backfills `nodes.urn`.
- [ ] Flyway V3 migration runs and adds `entity_type` constraint.
- [ ] Neo4j migration script (`bun run apps/api/src/scripts/migrate-urn.ts`) runs idempotently.
- [ ] Pipeline run produces a URN that is identical across `.brain/`, Postgres, and Neo4j (integration test).
- [ ] Constraint `chk_urn_format` rejects malformed inserts.
- [ ] `existing_external_id` queries (used by VS Code extension etc.) still work — Postgres keeps the old column for one release.

## Verification commands

```bash
# 1. Run migrations
make db-migrate
bun run apps/api/src/scripts/migrate-urn.ts dev monorepo

# 2. Verify Postgres has urn column with constraint
psql -c "\d nodes" | grep urn
psql -c "SELECT urn FROM nodes LIMIT 5;"

# 3. Verify Neo4j has new URN
cypher-shell -u neo4j -p password "MATCH (n) RETURN n.id LIMIT 5;"

# 4. Run pipeline + verify alignment
make ai-test-pipeline REPO=./pilot
python -m pytest tests/integration/test_urn_alignment.py -v
```

## Rollback

V2 migration is reversible: `ALTER TABLE nodes DROP COLUMN urn;` and `DROP INDEX uq_nodes_urn;`.

V3: `ALTER TABLE nodes DROP COLUMN entity_type;`.

Neo4j: nodes' `legacy_id` property is preserved; revert script:
```cypher
MATCH (n) WHERE n.legacy_id IS NOT NULL SET n.id = n.legacy_id REMOVE n.legacy_id;
```

The Python identity module is additive — its absence does not break the older code path.

## Out of scope

- **Multi-repo `repo` segment.** Today the segment is hardcoded to `monorepo` or `request.repos[0].name`. Stage 2 (ADR-002x) wires `repos.json` so each repo declares its slug.
- **Tenant federation.** Stage 3 concern — multi-tenant URNs need encryption-at-rest tenancy, KMS BYOK, etc.
- **Domain extension.** Today only `code`. Adding `support`, `runtime`, etc. is a Stage 3 concern.
- **Removing `nodes.external_id`.** Defer to V5 in a future ADR; keep both columns one release for safety.
