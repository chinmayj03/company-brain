# ADR-0009 — Shared Memory Layer: Tiered Context Snapshots

**Status:** Accepted  
**Date:** 2026-05-03  
**Deciders:** Company Brain core team  
**Depends on:** ADR-0001 (URN identity scheme), ADR-0005 (confidence scoring rubric), ADR-0008 (integration bridge)  
**Supersedes:** —  
**Related:** ADR-0007 (drift detection v1)

---

## Context

### The context window rot problem

Every time an agent (MCP tool call, IDE assistant, CI pipeline check) needs to answer a question about the codebase, it either:

1. **Fetches from the graph directly** — correct but expensive: a cold query against Neo4j or Postgres for a moderately large workspace can return hundreds of nodes, burn thousands of tokens loading context, and still miss the most relevant facts because graph traversal depth is hard to bound dynamically.

2. **Re-runs the LLM pipeline** — accurate but very expensive: Pass 1–4 on a large codebase takes minutes and costs significant LLM API budget. Running this on every agent invocation is not viable.

3. **Relies on previously extracted context in Postgres** — fast but stale: `node_context` rows written by Pass 3 may describe code that was modified three commits ago. There is no mechanism to detect or flag that staleness. An agent reading old context will answer confidently about a codebase state that no longer exists.

The deeper problem is that **there is no shared, compressed, versioned snapshot of what is known about a workspace**. Every agent starts from scratch. Facts discovered by one agent run are not reused by the next. The same symbol gets summarised repeatedly across sessions.

### The token budget problem

A typical MCP tool call has a context budget of 8K–32K tokens. A medium Company Brain workspace (10 repos, 50K LOC) has:
- ~8,000 nodes in Neo4j (structural)
- ~2,000 node_context rows in Postgres (LLM-synthesised)
- Thousands of edges

Loading even 10% of this on every invocation would blow the context budget before the agent could answer anything. Agents need a **pre-compressed, pre-ranked snapshot** that fits in 4K tokens and covers the 80% case.

### The deduplication problem

The same fact about the same symbol can exist in multiple places: in Neo4j as a structural node (confidence 1.0 from AST), in Postgres as an LLM synthesis row (confidence 0.70), and potentially in multiple `node_context` rows written by different pipeline runs. Without a deduplication rule, agents receive conflicting or redundant information.

---

## Decision

### 1 — Tiered shared memory architecture

A **tiered shared memory layer** is maintained per workspace scope. Three tiers define what is always available, what is loaded on demand, and what is archived:

```
┌─────────────────────────────────────────────────────────────┐
│  HOT tier  — last 30 days of active files + open drift       │
│  Always injected into every agent context window             │
│  Target size: ≤ 4K tokens (≤ ~3,200 words)                  │
├─────────────────────────────────────────────────────────────┤
│  WARM tier — frequently queried symbols + LLM summaries      │
│  Loaded on demand when a tool call matches a symbol          │
│  Target size: ≤ 16K tokens per query                         │
├─────────────────────────────────────────────────────────────┤
│  COLD tier — archived context for deprecated/stable code     │
│  Never auto-loaded; retrieved only by explicit request       │
│  No size limit; stored compressed                            │
└─────────────────────────────────────────────────────────────┘
```

**HOT tier** contains:
- Top N (default: 30) most recently modified files (by git commit timestamp)
- Their direct symbol inventory (names + signatures only, no body text)
- Open drift signals with severity `breaking` or `warning`
- Active data assumptions (from ADR-0007 drift signals related to DB contracts)
- API contract summaries for endpoints modified in the last 30 days
- Project context: repo name, primary language, top-level directory structure

**WARM tier** contains:
- Symbols that have been queried by any agent in the last 90 days (tracked by a `query_hit` counter on the node_context row)
- Their full LLM-synthesised context blobs from Pass 3
- Their caller/callee lists (one hop from Neo4j, names only)
- Cross-references to related Linear tickets or PRDs (if available via bridge extractor)

**COLD tier** contains:
- Symbols with `valid_to_commit IS NOT NULL` (invalidated — the symbol was deleted or renamed)
- Symbols last queried > 90 days ago
- Files with no commits in the last 180 days and no open drift signals
- Historical business context for deprecated features

Cold tier entries are compressed with zstd and stored in Postgres in a `cold_context_archive` table. They are never loaded automatically; an agent must call `get_archived_context(urn)` explicitly.

### 2 — Snapshot format: cb-memory.yaml

Each workspace maintains a `cb-memory.yaml` snapshot file, stored in a workspace-specific Postgres row (not in the repo itself — the repo should not be polluted with generated files). The snapshot is regenerated on each pipeline run and on each git commit hook invocation.

The snapshot is a YAML document with this structure:

```yaml
version: "1.0"
schema: "urn:cb:adr:company-brain:ADR-0009"
scope: "acme/api"
generated_at_commit: "abc123def456"
generated_at: "2026-05-03T14:22:00Z"
next_refresh_due: "2026-05-03T18:22:00Z"  # 4 hours, or next commit

hot_tier:
  active_files:
    - path: "src/billing/service.py"
      last_modified_commit: "abc123def456"
      language: "python"
      symbol_count: 12
      symbols:
        - urn: "urn:cb:llm:acme/api:src/billing/service.py:BillingService"
          name: "BillingService"
          kind: "Class"
          confidence: 0.70
          staleness_score: 0.12
        - urn: "urn:cb:llm:acme/api:src/billing/service.py:BillingService.charge"
          name: "BillingService.charge"
          kind: "Method"
          signature: "async def charge(amount: Decimal, user_id: UUID) -> ChargeResult"
          confidence: 0.70
          staleness_score: 0.12
  drift_signals:
    - urn: "urn:cb:drift:acme/api:signals/a1b2c3"
      severity: "breaking"
      description: "ChargeResult.transaction_id field missing from OpenAPI contract"
      implementation_urn: "urn:cb:symbol:acme/api:src/billing/handler.ts:chargeHandler"
      contract_urn: "urn:cb:contract:openapi:acme/api:operations/charge"
  api_contracts:
    - endpoint: "POST /billing/charge"
      contract_urn: "urn:cb:contract:openapi:acme/api:operations/charge@v2"
      last_verified_commit: "abc123def456"
      has_breaking_drift: true
  project_context:
    name: "acme/api"
    primary_language: "python"
    framework: "fastapi"
    top_directories: ["src/billing", "src/auth", "src/notifications"]
    owner_teams: ["billing-eng", "platform"]

warm_tier:
  frequently_queried_symbols:
    - urn: "urn:cb:llm:acme/api:src/billing/service.py:BillingService.charge"
      query_hit_count: 47
      last_queried: "2026-05-02T09:11:00Z"
      llm_context_summary: >
        Handles credit card charge flow via Stripe. Calls PaymentGateway.submit()
        and writes to transactions table. High change_risk: 3 downstream callers.
      callers:
        - "urn:cb:symbol:acme/api:src/billing/handler.ts:chargeHandler"
        - "urn:cb:llm:acme/api:src/billing/retry.py:RetryWorker.retry_charge"
      callees:
        - "urn:cb:llm:acme/api:src/payments/gateway.py:PaymentGateway.submit"
      confidence: 0.70
      staleness_score: 0.12

data_assumptions:
  - urn: "urn:cb:drift:acme/api:signals/d4e5f6"
    description: "transactions.status column assumed to be non-nullable; observed nullable in query"
    severity: "warning"
    detected_at_commit: "abc123def456"

cold_tier_index:
  # URNs only — no payloads. Use get_archived_context(urn) to retrieve.
  - urn: "urn:cb:llm:acme/api:src/billing/legacy.py:LegacyBillingService"
    archived_at_commit: "fed321cba"
    reason: "valid_to_commit set — symbol deleted"
  - urn: "urn:cb:llm:acme/api:src/notifications/sms.py:SmsNotifier"
    archived_at_commit: "aaa111bbb"
    reason: "no_queries_90d"
```

The snapshot is stored in Postgres as a `TEXT` column (YAML string) in a `workspace_snapshots` table, keyed by `(workspace_id, snapshot_type)`. It is NOT stored in git or in the filesystem of the repository being analysed.

### 3 — Staleness scoring

Every node in HOT and WARM tiers carries a `staleness_score` computed at snapshot generation time:

```
staleness_score = min(1.0, (commits_since_extraction / 10) * confidence_decay)
```

Where:
- `commits_since_extraction` = number of commits to the file since `extracted_from_commit` on the node
- `confidence_decay` = `1 - node.confidence` (higher-confidence nodes decay slower)

| staleness_score | Meaning | Agent behaviour |
|---|---|---|
| 0.0 – 0.3 | Fresh — extracted at or near HEAD | Trust fully |
| 0.3 – 0.7 | Stale — worth noting, may have changed | Surface with caveat |
| 0.7 – 1.0 | Very stale — likely wrong | Flag for refresh; do not use for code-generating tasks |

Nodes with `staleness_score > 0.7` are flagged with `stale: true` in the snapshot. The MCP `get_minimal_context` tool filters these out of the HOT tier payload by default (they are still present in the YAML for inspection, but not injected into the agent context window).

This rubric extends ADR-0005's staleness discount formula. The formula there is `0.95^(commits_since_extraction / 10)` applied to the numeric confidence value. Here, the `staleness_score` is a separate scalar (0–1) for easier thresholding and display; internally it is derived from the same `confidence_decay` logic.

### 4 — Refresh triggers

The snapshot is invalidated and re-queued for regeneration on:

1. **Git commit hook** — runs on every `git push` to a tracked branch. The hook compares current file SHA-256 checksums against `source_checksum` on affected nodes. Only files with checksum mismatches are marked stale. The hook emits a `refresh_job` record to the Postgres `pipeline_jobs` table for each affected entity.

2. **Time-based expiry** — `next_refresh_due` in the snapshot header. If the snapshot is older than 4 hours and no commit hook has run, the snapshot is regenerated from current graph state (no LLM re-extraction — only staleness scores are recomputed).

3. **Drift signal change** — when the drift detector (ADR-0007) emits or resolves a `DriftSignal` node, the affected scope's snapshot is immediately regenerated.

4. **Manual invalidation** — MCP tool `invalidate_context(scope)` allows an agent to force a full snapshot regeneration. This triggers a full pipeline re-run (all 4 passes) for the scope.

**Partial refresh (targeted re-extraction):** when a commit hook identifies stale entities, only Pass 3 (business context re-synthesis) is re-run for affected entities, not the full Pass 1–4. Pass 1 (entity extraction) and Pass 2 (relationship mapping) are only re-run if the file's structural signature changes (new functions added, functions renamed). This reduces LLM API cost by 60–80% for typical incremental commits that only change function bodies, not signatures.

```
Commit hook fires
  │
  ├── Compare file checksums
  │     ├── Checksum unchanged → skip file (no re-extraction)
  │     └── Checksum changed
  │           ├── Structural signature unchanged → queue Pass 3 only
  │           └── Structural signature changed   → queue Pass 1 + 2 + 3
  │
  └── Regenerate snapshot for affected scope (staleness scores only, no LLM)
        └── Full snapshot YAML is rebuilt from current graph + new staleness scores
```

The structural signature is computed as: `hash(sorted(function_names + class_names + method_names))` for the file. This is cheap (no parsing required if the AST extractor has already written a `content_hash`).

### 5 — MCP get_minimal_context tool

The MCP server exposes a `get_minimal_context(scope, max_tokens=4000)` tool that:

1. Reads the HOT tier from the current snapshot for `scope`.
2. Filters out nodes with `staleness_score > 0.7`.
3. Serialises the result as a compact YAML blob (no comments, minimal whitespace).
4. Truncates to `max_tokens` by dropping the lowest-confidence symbols first if the serialised output exceeds the token budget.
5. Returns the YAML string as the tool result.

The tool is designed to be called at the start of every agent session as the first context-loading step. The 4K token target leaves headroom in an 8K context window for the agent's question and its response.

```yaml
# Example get_minimal_context output (compressed):
scope: acme/api
commit: abc123def456
hot_files: [src/billing/service.py, src/billing/handler.ts, src/auth/middleware.py]
drift_signals:
  - severity: breaking
    desc: "ChargeResult.transaction_id missing from contract"
    impl: urn:cb:symbol:acme/api:src/billing/handler.ts:chargeHandler
symbols:
  - name: BillingService.charge
    kind: Method
    sig: "async def charge(amount, user_id) -> ChargeResult"
    confidence: 0.70
    stale: false
  - name: chargeHandler
    kind: Function
    sig: "async function chargeHandler(req, res): Promise<void>"
    confidence: 1.0
    stale: false
api_contracts:
  - "POST /billing/charge → ChargeResult (has_drift: true)"
```

### 6 — Context deduplication rule

When the same fact exists in multiple tiers or from multiple extraction sources, only the **highest-confidence version** is surfaced. The deduplication key is the URN. The resolution rule:

```
For each unique URN in the snapshot:
  candidates = all versions of this URN across sources
  winner = max(candidates, key=lambda c: (c.confidence * (1 - c.staleness_score)))
  snapshot[urn] = winner
  # Losers are dropped from the snapshot payload, not from the stores
```

The product `confidence * (1 - staleness_score)` is the **effective confidence** — a node with high original confidence but high staleness can be outranked by a lower-confidence but fresh node. This is the correct behaviour: a fresh LLM inference (0.70 confidence, 0.05 staleness) is more useful than a stale AST fact (1.0 confidence, 0.85 staleness).

Deduplication applies within the snapshot only. Both versions remain in their respective stores (Neo4j and Postgres). The canonical version for query purposes is always the Neo4j node (per ADR-0008, Neo4j is the unified query surface).

---

## Consequences

**Good:**
- Every agent session starts with a pre-compressed, pre-ranked context snapshot. Token burn on context loading drops from "unbounded" to a fixed 4K ceiling.
- Stale context is detected and flagged before it reaches an agent — agents no longer answer confidently about code that changed three commits ago.
- Targeted re-extraction (Pass 3 only for body changes) reduces LLM pipeline cost substantially. A team pushing 20 commits/day with typical body-only changes runs ~80% fewer full pipeline passes.
- The snapshot YAML is human-readable — engineers can inspect `cb-memory.yaml` content to debug why an agent gave a certain answer.
- Context deduplication means agents always receive the highest-confidence version of a fact, not whichever version happened to be queried last.

**Bad:**
- The snapshot is a derived artifact that must be kept in sync with the live graph. There is a window between a code change and the next snapshot regeneration where the snapshot is stale. The `staleness_score` field is the mitigation, but it does not eliminate the window.
- The 4K token HOT tier budget is a constraint. For very large workspaces (>50 active files, many drift signals), the HOT tier must truncate. The truncation logic (drop lowest-confidence first) is a heuristic — it may drop a low-confidence but highly relevant symbol. Agents should be aware that the HOT tier is a best-effort compression, not a complete inventory.
- The `query_hit_count` warm tier promotion mechanism requires write access to `node_context` rows on every MCP tool call. Under high concurrency (many agents querying simultaneously), this creates contention on the Postgres `node_context` table. A Redis counter cache with periodic flush is the mitigation at scale.
- Partial refresh (Pass 3 only) requires the structural signature check to be accurate. If the heuristic (`hash(sorted(names))`) misses a structural change (e.g. a function is renamed but the file has the same number of functions), Pass 1+2 will not be re-queued and the graph will have stale relationship data until the next full run.

**Neutral:**
- The snapshot YAML schema is versioned (`version: "1.0"`). Breaking schema changes increment the major version and trigger a full snapshot regeneration for all workspaces.
- The cold tier archive is append-only at MVP. There is no automatic pruning of cold tier entries. At scale, entries older than 1 year with zero query hits can be hard-deleted.
- The MCP `get_minimal_context` tool is purely a read path — it does not trigger any pipeline runs or graph writes. It is safe to call multiple times per session.

---

## Implementation Notes

### Postgres schema additions

```sql
CREATE TABLE workspace_snapshots (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id    TEXT NOT NULL,
    snapshot_type   TEXT NOT NULL DEFAULT 'full',  -- 'full' | 'hot_only'
    content         TEXT NOT NULL,                  -- YAML string
    commit_sha      TEXT NOT NULL,
    generated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    next_refresh_due TIMESTAMPTZ NOT NULL,
    UNIQUE (workspace_id, snapshot_type)
);

CREATE TABLE cold_context_archive (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id    TEXT NOT NULL,
    node_urn        TEXT NOT NULL,
    archived_at_commit TEXT NOT NULL,
    reason          TEXT NOT NULL,  -- 'valid_to_commit' | 'no_queries_90d' | 'deprecated'
    payload         BYTEA NOT NULL,  -- zstd-compressed JSON of the context blob
    archived_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (workspace_id, node_urn)
);

-- Track query hits for WARM tier promotion
ALTER TABLE node_context ADD COLUMN IF NOT EXISTS query_hit_count INT DEFAULT 0;
ALTER TABLE node_context ADD COLUMN IF NOT EXISTS last_queried_at TIMESTAMPTZ;
```

### Snapshot generator (Python, companybrain/memory/snapshot.py)

The snapshot generator is a standalone async function called:
- By the pipeline runner after each successful Pass 3 completion
- By the git commit hook handler in the FastAPI service
- By the drift detector after emitting new DriftSignal nodes (via ADR-0007)

It reads from both Neo4j (HOT/WARM structural data) and Postgres (LLM context blobs, annotation counts, query hit counts), applies the staleness scoring formula, runs deduplication, and serialises to YAML.

Key design constraints:
- Must complete in < 5 seconds for workspaces up to 100K nodes
- Must not require a full graph scan — uses indexed lookups by `last_modified_commit` and `query_hit_count`
- Must be idempotent — regenerating with the same graph state produces byte-identical output

### git commit hook integration

The hook is a lightweight POST to the FastAPI service's `/hooks/git-push` endpoint (called by a server-side post-receive hook, not a client-side hook, so it cannot be bypassed by individual developers). The hook payload includes the pushed commit SHA and the list of changed file paths. The FastAPI handler:

1. Computes file checksums for all changed paths
2. Queries Neo4j for nodes whose `source_checksum` differs from the current checksum
3. Enqueues refresh jobs in `pipeline_jobs` (Pass 3 only for body changes; Pass 1+2+3 for structural changes)
4. Calls the snapshot generator to recompute staleness scores immediately (no LLM wait)
5. Returns 200 synchronously; LLM re-extraction runs asynchronously

The hook endpoint is idempotent on the same commit SHA — re-delivery of the same webhook does not double-enqueue jobs.

### MCP tool registration

```python
# mcp/tools/memory.py

@tool("get_minimal_context")
async def get_minimal_context(scope: str, max_tokens: int = 4000) -> str:
    """
    Returns the HOT tier context snapshot for the given scope as a compact YAML blob.
    Filters out nodes with staleness_score > 0.7.
    Safe to call at the start of every agent session.
    """
    snapshot = await load_snapshot(scope)
    hot = filter_hot_tier(snapshot, max_staleness=0.7)
    return serialise_compact_yaml(hot, max_tokens=max_tokens)

@tool("get_symbol_context")
async def get_symbol_context(urn: str) -> str:
    """
    Returns the WARM tier context for a specific symbol URN.
    Promotes the symbol's query_hit_count in the background.
    """
    ...

@tool("get_archived_context")
async def get_archived_context(urn: str) -> str:
    """
    Retrieves a COLD tier archived context blob by URN.
    Returns a warning if the context is from a deleted/renamed symbol.
    """
    ...

@tool("invalidate_context")
async def invalidate_context(scope: str) -> str:
    """
    Forces full snapshot regeneration and queues a full pipeline re-run for the scope.
    Use when you suspect the current context is significantly out of date.
    """
    ...
```
