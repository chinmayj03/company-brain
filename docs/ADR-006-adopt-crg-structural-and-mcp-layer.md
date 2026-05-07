# ADR-006: Adopt code-review-graph as Structural Layer + MCP Tool Surface

**Status:** Proposed
**Date:** 2026-04-28
**Deciders:** Chinmay
**Supersedes:** Proposed ADR-007 (Merkle + tree-sitter from scratch) — see below
**Depends on:** ADR-001 (Postgres graph storage), ADR-003 (multi-tenancy via RLS), ADR-005 (artifact-centric pipeline)
**Unblocks:** ADR-008 (tiered memory + context assembly), Skills File API, multi-domain collectors
**Companion analyses:**
- `ANALYSIS-code-review-graph.md` (what's in CRG, what's worth lifting)
- `TRADEOFFS-crg-vs-companybrain.md` (full trade-off matrix across MCP, skills, workflows, multi-tenancy, cost)

---

## Context

### What we learned

We surveyed `tirth8205/code-review-graph` (CRG) — an MIT-licensed, ~27K-line Python project that ships as a pip-installable MCP server for code-review tooling. Its engineering decisions, taken together, look strikingly like the system the proposed ADR-007 (merkle/tree-sitter) wanted to invent — except CRG already shipped, benchmarked it (8.2x average token reduction across 6 real repos, sub-2s incremental updates on 2,900 files), and is in active use.

CRG and company-brain are not competing products. They made nearly opposite architectural bets:

- CRG bet **local-first, structural, free, MCP-native, agent-shaped.**
- Company-brain bet **cloud-first, semantic, LLM-rich, REST-native, team-shaped.**

The asymmetry is the opportunity. Stacking the systems gets us:

- A correct, deterministic, multi-language structural layer (CRG's tree-sitter parsing + hash-diff incremental + multi-factor risk scoring + flow detection + hubs/bridges) that we don't have today.
- An LLM-facing tool surface with hints and skills that turn AI Ask from a one-shot prompt into a multi-turn agent workflow.
- A cost discipline where the cheap structural path runs first and the expensive LLM path only runs when needed.

We keep our differentiated layers (multi-tenant Postgres, encrypted business-context annotations, the four-pass LLM semantic extractor, the artifact-centric pipeline from ADR-005).

### Why this is now the binding constraint

Three things converged:

1. The proposed ADR-007 was going to spend ~3 weeks reinventing tree-sitter + merkle indexing from scratch. CRG ships both, debugged.
2. The shift from code context to business context (the stated product direction) needs the cheap structural layer beneath the expensive LLM layer to be sustainable. Without CRG-style structural pre-computation, the LLM is doing structural work it shouldn't be doing — at every level of the system.
3. AI assistants (Claude Code, Cursor, Windsurf) are converging on MCP as the integration protocol. We currently have no MCP surface. CRG has a 28-tool surface that is exactly the API shape AI assistants drive against. Wrapping our backend in MCP — using CRG's patterns — is the lowest-friction path to AI-assistant compatibility.

### Why supersede ADR-007

ADR-007 designed the right system. CRG already built it. Re-deriving it from scratch when there's an MIT-licensed reference implementation is wasted engineering. ADR-007's intent — **"replace regex with structural parsing; use content-addressed invalidation to make incremental indexing tractable"** — survives. Only the implementation strategy changes from "design and build" to "lift, port, and integrate."

ADR-007 is closed as superseded; the architectural intent it documented is preserved in this ADR's Decision section.

---

## Decision

Adopt CRG as a **structural substrate** beneath company-brain's existing semantic layer, and adopt CRG's **MCP/skills/hints patterns** as the AI-assistant-facing surface above it. Concretely:

1. **Port CRG's parser, incremental engine, risk scoring, and graph analysis** into company-brain's Python AI service. Translate from SQLite to our Postgres schema; keep the algorithms.
2. **Wrap the existing Spring Boot backend with an MCP server** that exposes the same operations as REST today, plus new structural-tool operations enabled by the ported CRG layer.
3. **Adopt CRG's hint and skill patterns** — every MCP response carries `_hints`; every workflow we care about has a `SKILL.md` agent playbook.
4. **Layer cost by tier** — every AI Ask question tries the structural answer first, retrieval second, LLM synthesis third, full LLM extraction last. This is enforced by the order in which the MCP tools are called, biased by hints.

We do **not** adopt CRG's SQLite store, multi-repo registry, MCP stdio plumbing for our SaaS deployment, refactor mutation tools, or pip-install distribution. Those are correct for CRG's local-first model and incorrect for ours.

---

## Options Considered

### Option A: Build proposed ADR-007 from scratch (the original plan)

Design and implement merkle hashing + tree-sitter symbol index ourselves, against our Postgres schema, without reference to CRG.

| Dimension | Assessment |
|---|---|
| Time to ship | ~3 weeks engineering, plus debugging cycles |
| Risk | Untested code paths; edge cases we'd find live |
| Language coverage | Whatever we wire up — likely Java + TS/JS + Python at first |
| Algorithmic novelty | None — we'd be replicating well-known patterns |
| MCP / hints / skills | Out of scope; would be a separate ADR later |

**Verdict:** Reasonable in isolation. Wasteful relative to Option C.

### Option B: Replace company-brain with CRG, add semantic layer on top

Bet on CRG as the foundation and add business-context extraction as a service on the side.

| Dimension | Assessment |
|---|---|
| Multi-tenancy | None in CRG — would have to retrofit RLS into SQLite or fork to Postgres |
| Encryption / audit | None in CRG — would have to add |
| Existing CB code | Mostly thrown away |
| Time to ship | ~6+ weeks (rebuild what we have on a new foundation) |
| Architectural fit | CRG is local-first; we are cloud-first. Foundation mismatch |

**Verdict:** Wrong direction. Throws away differentiated CB capabilities (multi-tenancy, encryption, semantic extraction) that CRG doesn't have and that take real effort to build.

### Option C: Layered integration (recommended)

Keep CB's existing schema, multi-tenancy, semantic layer intact. Port CRG's structural layer underneath. Add MCP/skills/hints on top. Stack the systems; don't blend them.

| Dimension | Assessment |
|---|---|
| Time to ship | ~4 weeks for full sequence, with usable wins after week 2 |
| Risk | Lower — CRG code is battle-tested; we port algorithms not architecture |
| Language coverage | 23 languages from week 1 |
| MCP compatibility | Native after week 3 |
| Cost reduction | 1–2 orders of magnitude on per-question cost (cheap path first) |
| Architectural fit | Stacks cleanly; no foundation conflict |

**Verdict:** Recommended. Captures the value of CRG without giving up CB's strengths.

---

## Architecture

### The layered model

```
┌────────────────────────────────────────────────────────────────────┐
│  L5  Skills + Hints                                                │
│  /skills/{review-pr, audit-policy, onboard-engineer, ...}          │
│  hints.py — every MCP response carries _hints                      │
└─────────────────────────────────┬──────────────────────────────────┘
┌─────────────────────────────────▼──────────────────────────────────┐
│  L4  MCP Tool Surface (NEW)                                        │
│  20–30 tools wrapping REST: get_minimal_context, query_graph,     │
│  get_impact_radius, get_review_context, audit_business_rule, ...   │
└─────────────────────────────────┬──────────────────────────────────┘
┌─────────────────────────────────▼──────────────────────────────────┐
│  L3  Retrieval + Synthesis (existing + ADR-008)                    │
│  Graph traversal, FTS, embeddings, tiered memory, LLM synthesis    │
└─────────────────────────────────┬──────────────────────────────────┘
┌─────────────────────────────────▼──────────────────────────────────┐
│  L2  Semantic Graph (existing — KEEP)                              │
│  nodes / edges / node_context — 4-pass LLM extraction over Artifacts│
│  Multi-tenancy via RLS, encrypted node_context.body                │
└─────────────────────────────────┬──────────────────────────────────┘
┌─────────────────────────────────▼──────────────────────────────────┐
│  L1  Structural Graph (NEW — ported from CRG)                      │
│  tree-sitter parse, qualified-name identity, CALLS/IMPORTS/...     │
│  Multi-factor risk score, flows, communities, hubs, bridges        │
│  SHA-256 dirty-set, bidirectional blast-radius CTE                 │
└─────────────────────────────────┬──────────────────────────────────┘
┌─────────────────────────────────▼──────────────────────────────────┐
│  L0  Artifact Pipeline (per ADR-005)                               │
│  Files / tickets / PRs / annotations / Slack — all hashable        │
│  Dirty-set drives both L1 (re-parse) and L2 (LLM re-extract)       │
└────────────────────────────────────────────────────────────────────┘
```

**Key invariant:** L1 (structural) and L2 (semantic) share node identity via the qualified-name scheme (`path/file.py::Class.method`). The same logical entity has a structural row from L1 and zero or more semantic rows from L2. They join on `external_id`.

### What we lift from CRG (ported, not vendored)

We will write our own modules that adapt CRG's algorithms to our schema. We do not import CRG as a library. License is MIT — we attribute and clean-room the relevant logic where it fits.

| CRG file | What we port | Where it lands in CB |
|---|---|---|
| `parser.py` | Tree-sitter walker, language type maps, qualified-name scheme | `company-brain-ai/src/companybrain/structural/parser.py` |
| `incremental.py:get_changed_files` | Git-diff change detection | `companybrain/structural/changes.py` |
| `incremental.py:find_dependents` | Reverse-import expansion (BFS, hop-capped) | `companybrain/structural/dependents.py` |
| `incremental.py:incremental_update` | Hash-diff loop with parallel parse | `companybrain/structural/indexer.py` |
| `graph.py:get_impact_radius_sql` | Bidirectional recursive CTE blast radius | `BlastRadiusService.java` (ported to Postgres dialect) |
| `flows.py:detect_entry_points` | Framework-decorator regex set + entry-point heuristics | `companybrain/structural/flows.py` |
| `flows.py:trace_flows` + `compute_criticality` | BFS-traced execution flows with criticality | `companybrain/structural/flows.py` |
| `changes.py:compute_risk_score` | Multi-factor risk: security keywords + tests + callers + flows + cross-community | `companybrain/structural/risk.py` |
| `analysis.py:find_hub_nodes` | Top-N by degree | `companybrain/structural/topology.py` |
| `analysis.py:find_bridge_nodes` | Top-N by betweenness centrality (NetworkX, sampled at >5K) | `companybrain/structural/topology.py` |
| `communities.py:detect_communities` | Leiden + file-based fallback | `companybrain/structural/communities.py` |
| `hints.py` | `_INTENT_TOOLS` + `_WORKFLOW` static maps; `_hints` injection | `companybrain/mcp/hints.py` |
| `tools/context.py:get_minimal_context` | ~100-token opener with task-keyword tool suggestions | `companybrain/mcp/tools/context.py` |
| `skills/*.md` | Agent playbook structure + token-efficiency rules | `/skills/{review-pr, audit-policy, ...}.md` |

### What we explicitly do not adopt

| CRG component | Why skipped |
|---|---|
| SQLite store | Postgres + RLS is correct for multi-tenant SaaS |
| Multi-repo registry (`~/.code-review-graph/registry.json`) | Workspace model already covers this |
| MCP stdio transport (CRG's `main.py`) | We expose MCP over HTTP/SSE for SaaS; stdio is on-prem only |
| Refactor mutation tools (`refactor.py`, `apply_refactor`) | CB is server-side; mutation surface is out of scope for v1 |
| pip-install + auto-detect-platform | Different distribution model |
| Watch daemon (`daemon.py`) for SaaS | Scheduled collectors per ADR-005; daemon is reused only for on-prem agent tier |
| Embeddings pipeline | We have our own pgvector plan in ADR-008 |
| Wiki generation | Lower priority; revisit once L1 + L4 ship |

### Schema additions

These are additive. No breaking changes to existing tables.

```sql
-- Structural layer columns on existing nodes table
ALTER TABLE nodes ADD COLUMN qualified_name  TEXT;        -- path::Class.method form
ALTER TABLE nodes ADD COLUMN file_hash       TEXT;        -- sha256 of source bytes
ALTER TABLE nodes ADD COLUMN line_start      INTEGER;
ALTER TABLE nodes ADD COLUMN line_end        INTEGER;
ALTER TABLE nodes ADD COLUMN risk_score      NUMERIC(3,2); -- computed by structural/risk.py
ALTER TABLE nodes ADD COLUMN risk_factors    JSONB;        -- {tests:0.30, callers:0.08, ...}

CREATE INDEX idx_nodes_qualified ON nodes(workspace_id, qualified_name)
  WHERE qualified_name IS NOT NULL;

-- Flows (ported from CRG)
CREATE TABLE flows (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id    UUID NOT NULL,
    name            TEXT NOT NULL,
    entry_node_id   UUID NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
    depth           INTEGER NOT NULL,
    node_count      INTEGER NOT NULL,
    file_count      INTEGER NOT NULL,
    criticality     NUMERIC(4,3) NOT NULL DEFAULT 0,
    path_json       JSONB NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE flow_memberships (
    flow_id         UUID NOT NULL REFERENCES flows(id) ON DELETE CASCADE,
    node_id         UUID NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
    position        INTEGER NOT NULL,
    PRIMARY KEY (flow_id, node_id)
);

-- Communities (ported from CRG)
CREATE TABLE communities (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id       UUID NOT NULL,
    name               TEXT NOT NULL,
    cohesion           NUMERIC(4,3),
    size               INTEGER,
    dominant_language  TEXT,
    description        TEXT
);
CREATE TABLE node_communities (
    node_id        UUID NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
    community_id   UUID NOT NULL REFERENCES communities(id) ON DELETE CASCADE,
    PRIMARY KEY (node_id)
);

-- Topology metrics (computed nightly)
CREATE TABLE graph_metrics (
    workspace_id   UUID NOT NULL,
    node_id        UUID NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
    metric_kind    TEXT NOT NULL,   -- 'hub_degree' | 'bridge_betweenness'
    score          NUMERIC,
    rank           INTEGER,
    computed_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (workspace_id, node_id, metric_kind)
);
```

All new tables get RLS policies consistent with ADR-003.

### MCP server design

A new Python service `company-brain-mcp` runs alongside `company-brain-ai`. It exposes MCP over HTTP+SSE (cloud) and stdio (on-prem). It does not host its own data — every tool delegates to the existing Spring Boot backend via internal REST. The MCP server is essentially a translation layer plus the hints engine.

Initial tool set (~25):

```
Structural (delegated to L1):
  - build_or_update_graph        (incremental indexing)
  - get_minimal_context           (~100 token opener)
  - get_impact_radius             (bidirectional blast radius)
  - query_graph                   (callers_of, callees_of, imports_of, ...)
  - find_large_functions
  - list_flows / get_flow / get_affected_flows
  - list_communities / get_community / get_architecture_overview
  - find_hubs / find_bridges
  - semantic_search_nodes

Semantic (delegated to L2/L3):
  - get_business_context          (existing node_context retrieval)
  - get_review_context            (assembled context per ADR-008)
  - audit_business_rule           (find code that implements/conflicts with a policy)
  - suggest_drifts                (preview-style: find stale annotations)
  - acknowledge_drift             (apply: mark a drift reviewed)

Workflow:
  - detect_changes                (risk-scored change analysis)
  - get_review_context            (one-call review bundle)

Multi-domain:
  - list_artifacts                (per ADR-005)
  - get_artifact_provenance       (which nodes derived from this artifact)
```

Every response is wrapped:

```json
{
  "result": { ...tool-specific... },
  "_hints": [
    {"tool": "get_flow", "suggestion": "Drill into a specific flow"},
    {"tool": "get_affected_flows", "suggestion": "See which flows are affected by recent changes"}
  ]
}
```

### Cost-tiered retrieval flow

This is enforced by skill conventions, not hard constraints. AI assistants are instructed (via skills + hints) to walk the cheap path first.

```
AI Ask question
    │
    ▼
[1] Try structural answer (L1 traversal)            cost ≈ $0
    │  Examples: "who calls X?", "what's the blast radius of Y?"
    │  If sufficient → return
    ▼
[2] Try retrieval against pre-extracted context     cost ≈ $0.001
    │  Examples: "what does X do?" (T1/T2 from ADR-008)
    │  If sufficient → return
    ▼
[3] LLM synthesis with retrieved context            cost ≈ $0.01–$0.05
    │  Examples: "how does refund policy apply to X?"
    │  If sufficient → return
    ▼
[4] Full LLM extraction (only for unindexed)        cost ≈ $0.30+
    │  Examples: a brand-new endpoint that's never been processed
```

Today CB defaults to step 4 every time. After this ADR, the dominant path is step 1 or step 2.

---

## Implementation Phases (4 weeks)

Each phase has a demo-able outcome. The work is sequenced so that intermediate weeks already deliver user value.

### Week 1 — Structural parser + risk score

- [ ] Add Python module `company-brain-ai/src/companybrain/structural/parser.py`. Port CRG's tree-sitter walker. Verify equivalent output to current regex on our Java Spring Boot test workspace.
- [ ] Schema migration `V3__structural_columns.sql`: add `qualified_name`, `file_hash`, `line_start`, `line_end`, `risk_score`, `risk_factors` to `nodes`.
- [ ] Port `compute_risk_score` to `companybrain/structural/risk.py`. Run on existing nodes; backfill the new columns.
- [ ] Frontend: `BlastRadiusPanel.jsx` displays `risk_score` per affected node, sorted descending.
- **Demo:** "On our test workspace, the blast radius now shows risk-sorted nodes with explainable risk factors. The system identifies untested security-sensitive functions automatically."

### Week 2 — Hash-diff incremental indexing + bidirectional blast radius

- [ ] Port `find_dependents` and `incremental_update` to `companybrain/structural/indexer.py`. Postgres adaptation of the dirty-set walk.
- [ ] Wire the dirty-set engine to ADR-005's `artifact_change_events`. Source-file artifacts now drive structural re-indexing automatically.
- [ ] Modify `BlastRadiusService.java` recursive CTE: add reverse branch (`UNION` over `e.target_id = br.id`). Add `direction` parameter (`forward`|`reverse`|`both`, default `both`). Feature-flag for rollback.
- [ ] Backfill: one-time full structural index for existing workspaces.
- **Demo:** "Editing a file and re-running the pipeline now takes under 5 seconds for unchanged code; only changed files and their callers go through extraction. Blast radius shows both upstream and downstream impact."

### Week 3 — MCP server + hints + minimal context

- [ ] Stand up `company-brain-mcp` Python service. HTTP+SSE transport. Reuses `company-brain-backend`'s REST endpoints internally.
- [ ] Port `hints.py` to `companybrain/mcp/hints.py`. Adapt `_INTENT_TOOLS` and `_WORKFLOW` to our tool surface.
- [ ] Implement first 10 MCP tools, each emitting `_hints`. Priority order: `get_minimal_context`, `get_impact_radius`, `query_graph`, `get_review_context`, `semantic_search_nodes`, `get_business_context`, `list_flows`, `get_flow`, `find_hubs`, `find_bridges`.
- [ ] Document the MCP endpoint and authentication for self-hosted users.
- **Demo:** "Connect Claude Code or Cursor to company-brain via MCP. Ask 'review my current branch' — the agent calls `get_minimal_context`, sees the suggested next tools, walks through the workflow, returns a structured review."

### Week 4 — Skills + flows + topology metrics

- [ ] Write 5–6 skills in `/skills/`: `review-pr`, `audit-business-rule`, `onboard-engineer`, `debug-incident`, `explore-codebase`, `impact-analysis`. Each ≤80 lines, with token-efficiency rules.
- [ ] Port `detect_entry_points`, `trace_flows`, `compute_criticality`. Schema: `flows` + `flow_memberships`. Add `list_flows`, `get_flow`, `get_affected_flows` MCP tools.
- [ ] Nightly job: compute hubs (top 50 by degree) and bridges (top 25 by betweenness centrality, sampled at >5K nodes). Write to `graph_metrics`.
- [ ] Frontend: new "Architecture" tab showing top hubs, top bridges, named flows with criticality scores.
- **Demo:** "AI assistant given a `/skill:review-pr` runs the full review workflow in 5 tool calls. Architecture overview surfaces the 5 functions that are critical chokepoints. Named flows let users ask 'what's the user signup flow?' and get the actual call sequence."

### Out of scope for the 4-week sequence (deferred)

- Communities (Leiden) — useful but not on critical path; week 5+
- Refactor mutation tools — domain-translated to "drift detection" pattern, but requires dedicated design
- Wiki generation — once L4 stabilizes
- On-prem agent tier with watch daemon — separate product surface, separate ADR
- Multi-domain collectors (Slack, Zendesk) — gated on ADR-005's collector framework, not on this ADR

---

## Trade-offs Accepted

We're making these commitments knowingly:

- **MCP becomes a public surface.** Versioning, documentation, deprecation. Treated like a REST API.
- **Two parsers in the system.** Tree-sitter at L1, LLM at L2. They must agree on entity identity (qualified-name scheme is the contract). Drift between them is a real risk and needs monitoring.
- **Two cost shapes on different schedules.** L1 updates cheaply on every commit. L2 updates expensively when artifacts change. The dirty-set engine has to decide which is needed for which artifact. Operational complexity is real.
- **Hints and skills require maintenance.** When a new tool is added, the hint workflow map must be updated. When the model changes capabilities, skills may need re-tuning. Not free.
- **Inheriting CRG's failure modes.** Tree-sitter is great but not perfect. Dynamic route registration, runtime metaprogramming, code-generated APIs all stress the structural parser. We inherit those gaps.
- **License attribution.** CRG is MIT. We attribute, we don't relicense. Our modules carry an attribution header and the algorithms remain credited even after porting.

---

## Consequences

### What becomes easier

- AI-assistant integration. Claude Code, Cursor, Windsurf, Codex all speak MCP. After week 3 we plug into all of them for free.
- Per-question cost. The cheap-path-first pattern reduces dominant-path token spend by 1–2 orders of magnitude.
- Structural correctness. 23 languages on day one of week 1. Renamed decorators, dynamic routes, complex inheritance all handled by the parser.
- Live updates. Sub-5-second incremental indexing replaces full pipeline reruns.
- Risk-aware blast radius. Per-node risk scores with explainable factors (security/tests/callers/flows) ship in week 1.
- Architectural reasoning. Hubs, bridges, flows, communities all become queryable.
- Faster iteration on AI Ask. Skills are markdown files; a product manager can edit them.

### What becomes harder

- Two pipelines (L1 structural, L2 semantic) on different schedules. Operations.
- Schema growth — six new tables, six new columns. Migration discipline matters.
- Test surface — every ported algorithm needs equivalence tests against CRG's behavior on a fixture set.
- Versioning the MCP surface alongside REST. Two contracts to honor.

### What we will need to revisit

- **ADR-008 (tiered memory):** the T0 memory token format should now include `risk_score` and flow membership at zero cost.
- **ADR-005 (artifact-centric):** the `kind=source_file` artifact path must trigger L1 re-parse before triggering L2 LLM extraction. The dirty-set engine learns which kinds drive which extractors.
- **`PIPELINE-api-context-builder.md`:** the regex blocks (`_TS_API_CALL_RE`, `_PY_ROUTE_RE`, `_TS_IMPORT_RE`, `_PY_IMPORT_RE`, `_JAVA_MAPPING_RE`) get deleted. CodeTracer queries the structural index instead.
- **`RETRIEVAL-ARCHITECTURE.md`:** the L0–L3 knowledge pyramid stays, but every retrieval profile gets a "structural" preflight that runs before the LLM is touched.
- **The four-pass LLM extractor:** Pass 1 (entity extraction) becomes thinner — entities come from L1 with `confidence: 1.0`; LLM only adds purpose, dataReads, riskFlags, etc. Token cost on Pass 1 drops 30–50%.

---

## Action Items

### Schema and infrastructure

1. [ ] Flyway migration `V3__structural_columns.sql` — add columns to `nodes`, create `flows`/`flow_memberships`/`communities`/`node_communities`/`graph_metrics` tables with RLS.
2. [ ] Add `tree-sitter-languages` to `company-brain-ai/pyproject.toml`. Verify grammars for Java, TS, JS, Python, Go cover our current customer base.
3. [ ] Create `company-brain-mcp` directory with FastAPI/Starlette skeleton, HTTP+SSE transport, JWT-protected.

### Structural layer port (Python)

4. [ ] `companybrain/structural/parser.py` — tree-sitter walker, qualified-name scheme.
5. [ ] `companybrain/structural/changes.py` — git-diff change detection.
6. [ ] `companybrain/structural/dependents.py` — reverse-import expansion with hop cap.
7. [ ] `companybrain/structural/indexer.py` — hash-diff incremental loop, ProcessPoolExecutor, wired to ADR-005 `artifact_change_events`.
8. [ ] `companybrain/structural/risk.py` — multi-factor risk score; backfill job for existing nodes.
9. [ ] `companybrain/structural/flows.py` — entry-point detection (lift CRG's regex set), BFS tracing, criticality scoring.
10. [ ] `companybrain/structural/topology.py` — hub degree + bridge betweenness; nightly job.
11. [ ] `companybrain/structural/communities.py` — Leiden + file-based fallback. (Lower priority — week 5+.)

### Java-side changes

12. [ ] `BlastRadiusService.java` — extend recursive CTE with reverse branch; add `direction` parameter; feature flag.
13. [ ] `BlastRadiusNode.java` (DTO) — add `riskScore`, `riskFactors`, `flowMembership`.
14. [ ] `BlastRadiusPanel.jsx` — render risk-sorted nodes with explainable factor breakdown.

### MCP layer

15. [ ] `companybrain/mcp/server.py` — MCP server entry point (HTTP+SSE for SaaS, stdio for on-prem agent).
16. [ ] `companybrain/mcp/hints.py` — port `_INTENT_TOOLS`, `_WORKFLOW`, `_hints` injection middleware.
17. [ ] `companybrain/mcp/tools/context.py` — `get_minimal_context`.
18. [ ] `companybrain/mcp/tools/structural.py` — `get_impact_radius`, `query_graph`, `find_large_functions`, `find_hubs`, `find_bridges`.
19. [ ] `companybrain/mcp/tools/semantic.py` — `get_business_context`, `audit_business_rule`, `suggest_drifts`, `acknowledge_drift`.
20. [ ] `companybrain/mcp/tools/workflow.py` — `detect_changes`, `get_review_context`.
21. [ ] `companybrain/mcp/tools/flows.py` — `list_flows`, `get_flow`, `get_affected_flows`.

### Skills

22. [ ] `/skills/review-pr/SKILL.md` — adapted from CRG's; references our MCP tool names.
23. [ ] `/skills/audit-business-rule/SKILL.md` — new, CB-specific.
24. [ ] `/skills/onboard-engineer/SKILL.md` — new, CB-specific.
25. [ ] `/skills/debug-incident/SKILL.md` — new, CB-specific.
26. [ ] `/skills/explore-codebase/SKILL.md` — adapted from CRG.
27. [ ] `/skills/impact-analysis/SKILL.md` — new, CB-specific.

### Cleanup and migration

28. [ ] Delete regex blocks in `CodeTracer` — `_TS_API_CALL_RE`, `_PY_ROUTE_RE`, `_TS_IMPORT_RE`, `_PY_IMPORT_RE`, `_JAVA_MAPPING_RE`. Replace with structural-index queries.
29. [ ] Update Pass 1 of the LLM extractor — accept structural entities as input rather than re-discovering them. Measure token reduction.
30. [ ] One-time backfill: run full structural index against every existing workspace, populate `risk_score`, flows, hubs, bridges.

### Validation

31. [ ] Equivalence tests: parser output vs. current regex output on a Java + TS + Python fixture workspace. Targets: parity or strictly better entity recall.
32. [ ] Benchmark: tokens consumed per AI Ask question before and after. Target: 5x reduction at the 50th percentile.
33. [ ] Benchmark: incremental update latency for a 500-file workspace. Target: <5 seconds.
34. [ ] License audit: confirm CRG attribution headers in every ported module; legal sign-off on the port-not-vendor approach.

### Documentation

35. [ ] Update `ARCHITECTURE-company-brain-v2.md` with the L0–L5 layered diagram.
36. [ ] Mark proposed ADR-007 superseded; link here.
37. [ ] Write `docs/MCP-INTEGRATION.md` — how to connect Claude Code / Cursor to a CB workspace.
38. [ ] Write `docs/SKILLS.md` — skill format, token-efficiency rules, how to add a skill.

---

## Why this is the right ADR to commit to

It absorbs three pending threads cleanly. Proposed-ADR-007 (merkle/tree-sitter) is replaced by a port plan. The MCP/skills gap that was unowned now has a home. The cost-tiering discipline that ADR-008 implied but didn't enforce becomes structural through the cheap-path-first pattern.

It produces user-visible wins every week of the rollout. Risk-scored blast radius (week 1), live incremental indexing (week 2), MCP compatibility (week 3), agent skills (week 4) — each demonstrable to stakeholders independently.

It preserves what makes company-brain differentiated (multi-tenancy, encryption, semantic extraction, business-context annotations) and adopts what makes CRG fast (structural primitives, hint-driven agent UX, cheap-path-first cost shape). The two systems' strengths compose; their weaknesses don't.

If only one architecture ADR ships next, this is the one.
