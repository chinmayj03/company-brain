# ADR-0071 — Frontend Rebuild + End-to-End Brain Integration

**Status:** Proposed
**Date:** 2026-05-11
**Builds on:** existing prototype at `/Users/chinmayjadhav/Documents/Company brain/` + brain MCP server (ADR-0052 P5)
**Sequenced with:** parallel-shippable with ADR-0064-0069. **Critical-path P0 for the seed demo per DEMO-PRIORITY-ORDER.md.**

---

## Context

The prototype exists at `/Users/chinmayjadhav/Documents/Company brain/` (5,252 LOC across 7 JSX files + 1,054 LOC CSS + 149 LOC mock data). It's already designed and substantial — three views, time-travel, blast radius graph, citations, owners, agents panel, sidebar nav, the lobName/customer_id rename as canonical demo.

**Problem**: it's a static design prototype. Loaded via `<script src="data.js">` with hardcoded mock data; React via CDN + Babel-in-browser; no backend wiring; no real queries; no real extractions.

**This ADR's scope**: don't rebuild the UI — that's already done by whoever designed it (excellent work). **Wire it to the brain.** Specifically:

1. Replace `BRAIN_DATA` mock with live API calls to the brain
2. Add a real frontend toolchain (Vite + TypeScript) so it ships, not just demos
3. Connect MCP server (ADR-0052 P5) so the Cursor/Devin/Cody panel is REAL
4. Wire the time-travel slider to the brain's temporal data (ADR-0059)
5. Wire the blast-radius graph to the brain's edge graph (ADR-0058's DatabaseColumn + edges)
6. Wire the codebase view's inline annotations to the brain's BusinessContext (ADR-0060 v2)
7. Wire the push-flow risk badge + recommended-reviewer logic to the brain's TemporalOwnership + RiskAlerts (ADR-0059)

By the end of this ADR, the **lobName/customer_id rename example in the prototype is REAL** — pointing at an actual extracted brain (network-iq-backend-java's lob column) and queries return real cited answers.

---

## Decision

Three coordinated phases, each independently demoable:

### Phase A — Toolchain + data wiring (3 days, P0 for demo)

Migrate from CDN-React-prototype to a buildable Vite app that ships:

```
prototype-as-is/                            (preserved for design reference)
└── /Users/chinmayjadhav/Documents/Company brain/...

new-frontend/                               (NEW, this PR's home)
├── package.json                            Vite + React 18 + TS
├── vite.config.ts
├── tsconfig.json
├── index.html
├── src/
│   ├── App.tsx
│   ├── views/
│   │   ├── Ask.tsx                         ← prototype's main view
│   │   ├── Codebase.tsx                    ← from codebase.jsx
│   │   ├── Trace.tsx                       ← from trace.jsx
│   │   ├── PushFlow.tsx                    ← from push-flow.jsx
│   │   └── DesignCanvas.tsx                ← from design-canvas.jsx (debug-only)
│   ├── components/                         ← from components.jsx
│   ├── data/
│   │   ├── brain_client.ts                 ← NEW: replaces window.BRAIN_DATA
│   │   ├── mcp_client.ts                   ← NEW: talks to brain MCP server
│   │   └── mock_fallback.ts                ← preserved mock for offline dev
│   └── styles/
│       ├── app.css                         ← from app.css (kept)
│       └── prism.css                       ← from prism.css (kept)
└── public/
    └── icons/                              ← extracted from data.js inline SVGs
```

**Conversion strategy**: each prototype JSX file becomes a TypeScript view component with the same JSX preserved. Replace `window.BRAIN_DATA` with React Query hooks pointing at the brain client. **No visual changes** — the design is right; we're just making it run on real data.

The prototype's CSS variables (`--warm-page`, `--bg-surface`, `--danger-soft`, etc.) are imported as-is. No design-system rewrite.

### Phase B — Live brain wiring (5 days, P0 for demo)

Per-view backend integration:

#### View 1 — Ask (the marquee Q&A view)

Today (mock):
```js
suggested = ['What breaks if I drop the lobName column?', ...]
states = [/* canned time-travel answers */]
```

Wired:
```ts
const { data: suggested } = useQuery(['suggested', repo],
  () => brain.getSuggestedQuestions(repo));
const { data: answer, isFetching } = useQuery(['ask', question, asOfDate],
  () => brain.query({ question, repo, as_of_date: asOfDate }));
```

The brain's `/query` returns the structured QueryResponse (ADR-0043 schema):
- `summary_md` — populates the prose answer area
- `cited_entity_urns` — populates the citations panel
- `affected_entities` + per-entity owner data — populates the blast-radius graph
- `confidence` — populates the answer-quality pill
- `follow_up_questions` — populates the suggested-followups list

The time-travel slider hits `brain.query(..., as_of_date=YYYY-MM-DD)` which uses ADR-0059's TemporalOwnership to filter the graph to that date.

#### View 2 — Codebase (annotated reader — the persona's main vehicle)

Today: hardcoded `TREE` + hardcoded `ann` line counts per file.

Wired:
```ts
const { data: tree } = useQuery(['repo-tree', repo], () => brain.getRepoTree(repo));
const { data: file } = useQuery(['file', activeFilePath],
  () => brain.getFile(activeFilePath));
const { data: annotations } = useQuery(['annotations', activeFilePath],
  () => brain.getAnnotations(activeFilePath));
```

Each annotation comes from the brain's BusinessContext (ADR-0060 v2):
- `purpose` → the inline summary
- `is_idempotent`, `transaction_mode`, `security_class` → small badge icons
- `anti_patterns` → red flag indicator
- `engineering_notes` → expandable detail
- `last_modified_commit` → "who/when" attribution
- `change_risk` → background tint

This is the **inline-memory experience** the prototype's comment describes. **The codebase view has the brain's memory layered on top of the actual code, line by line.**

#### View 3 — Trace (one feature traced across every layer)

Today: hardcoded FEATURE constant with 5 layers.

Wired:
```ts
const { data: trace } = useQuery(['trace', endpoint],
  () => brain.traceCallChain({ endpoint, http_method }));
```

The brain's call-chain extraction (ADR-0058 schema awareness + ADR-0055 cross-file pass) returns the layers in order: Frontend → API → Service → Repository → SQL. Per-layer `plain` + `tech` text comes from BusinessContext v2's `purpose` (plain) and `engineering_notes` (tech).

#### View 4 — Push-Flow (PR/branch + risk + reviewer suggestions)

Today: hardcoded BRANCHES with mock files/additions/risk.

Wired:
```ts
const { data: branches } = useQuery(['branches', repo],
  () => brain.getOpenBranches(repo));
const { data: blastRadius } = useQuery(['blast-radius', branchSha],
  () => brain.getBranchBlastRadius(branchSha));
const { data: reviewers } = useQuery(['reviewers', branchSha],
  () => brain.getRecommendedReviewers(branchSha));
```

Branches come from the GitHub MCP (already a connector option). Blast radius uses ADR-0058's edges. Recommended reviewers come from ADR-0059's TemporalOwnership rolled up to the changed files. Risk badge from ADR-0059's RiskAlerts.

#### Sidebar — Agents · MCP

Today: hardcoded list (Cursor, Devin, Cody, Internal copilot).

Wired:
```ts
const { data: agents } = useQuery(['mcp-agents'],
  () => brain.mcp.listConnectedAgents());
```

Each agent shown is a real client connected to our MCP server (ADR-0052 P5). Live counts (`47 qpm · 23 seats`) come from MCP server telemetry.

#### Sidebar — Audit log

Today: just a nav item.

Wired: opens a paginated view of the audit_events table from ADR-0064 (privacy & audit). Each row: actor, action, resource_urn, timestamp. Tamper-verification button calls `audit_verify_chain` MCP tool.

#### Sidebar — History / Saved

Wired to ADR-0066's QueryTrajectory store. History = recent queries; Saved = trajectories with `feedback="thumbs_up"` or `"saved"`.

### Phase C — Real-time + polish (2 days, P1 for demo)

- **SSE streaming** on `/query` (already in ADR-0049): the prose answer streams token-by-token instead of waiting for full response. Drops perceived latency from 5s to 600ms.
- **Live extraction progress**: when the user hits "Index this repo", show real-time progress (chunk N of M extracted, current file, estimated cost) via SSE on `/pipeline/jobs/{id}/stream` (ADR-0051 P4).
- **Cost counter**: "$0.012 spent on this query" pill in the UI corner — proves to investors we know our unit economics.
- **Error states**: friendly messages when brain returns empty / low-confidence / requires augmentation.
- **Dark mode toggle**: the prototype already has `data-theme="dark"` CSS variables wired; add the toggle button.

---

## Files we own (parallel-safe with all other ADRs)

```
new-frontend/                               # NEW DIRECTORY (entire scope is this)
new-frontend/package.json
new-frontend/vite.config.ts
new-frontend/tsconfig.json
new-frontend/index.html
new-frontend/src/                            (full TypeScript port from prototype)
new-frontend/.env.example
new-frontend/Dockerfile                      # for production deployment
new-frontend/README.md
docs/FRONTEND-INTEGRATION.md                  # how the brain API contract maps to UI
```

NEW backend endpoints needed (append-only changes):

```
company-brain-ai/src/companybrain/api/routes/repo.py          # add getRepoTree, getFile, getAnnotations
company-brain-ai/src/companybrain/api/routes/trace.py         # NEW — traceCallChain endpoint
company-brain-ai/src/companybrain/api/routes/push_flow.py     # NEW — getOpenBranches, getBranchBlastRadius, getRecommendedReviewers
company-brain-ai/src/companybrain/api/routes/audit_admin.py   # NEW — paginated audit reads
company-brain-ai/src/companybrain/mcp/tools/list_agents.py    # NEW — telemetry on connected MCP clients
```

We do NOT touch:
- The existing prototype folder (it's the design reference; preserve it)
- The existing `company-brain-frontend/` (legacy; deprecate after this lands)
- Anything owned by ADR-0064-0069

---

## API contract (the brain side must support)

Per-view-mapped endpoints. All under `/api/v2/` (versioned so the prototype API can coexist):

```
GET    /api/v2/repos/{repo_id}/tree                          → file tree + per-file annotation count
GET    /api/v2/repos/{repo_id}/files/{path}                   → file content + per-line annotations
POST   /api/v2/query                                          → existing /query, augmented
GET    /api/v2/query/suggested?repo={repo}                    → suggested questions per repo
GET    /api/v2/repos/{repo}/trace?endpoint={ep}&method={m}    → call chain across layers
GET    /api/v2/repos/{repo}/branches                          → open PR branches
GET    /api/v2/branches/{sha}/blast-radius                    → affected entities + risk
GET    /api/v2/branches/{sha}/reviewers                       → recommended reviewers from temporal data
GET    /api/v2/audit?since=&until=&actor=                     → paginated audit events
GET    /api/v2/history                                        → past query trajectories
GET    /api/v2/mcp/agents                                     → live MCP-connected client list
GET    /api/v2/pipeline/jobs/{id}/stream                      → SSE for live extraction (already exists)
GET    /api/v2/query/stream?question=&repo=&as_of_date=       → SSE for streaming answers
POST   /api/v2/feedback                                       → thumbs up/down on answer (writes to ExperientialMemory per ADR-0066)
```

Each endpoint has a 1-page contract spec in `docs/FRONTEND-INTEGRATION.md`.

---

## Acceptance test (the lob query end-to-end)

```typescript
// new-frontend/tests/e2e/lob-query.spec.ts (Playwright)

test('lob rename query returns real brain data with citations', async ({ page }) => {
  await page.goto('http://localhost:5173/ask');
  await page.fill('input[placeholder="Ask..."]', 'What breaks if I drop the lobName column?');
  await page.click('button:has-text("Ask")');

  // Streams in
  await expect(page.locator('.summary-md')).toContainText('lob', { timeout: 10000 });

  // Citations panel populated
  const citations = page.locator('.citation-row');
  await expect(citations).toHaveCount.greaterThan(2);
  await expect(citations.first()).toContainText('CompetitivenessPlanRepository');

  // Blast-radius graph rendered
  await expect(page.locator('svg.blast-radius circle')).toHaveCount.greaterThan(5);

  // Time-travel slider works
  await page.locator('.time-slider').dragTo(page.locator('.time-anchor-3mo'));
  await expect(page.locator('.summary-md')).toContainText('34 places'); // historical state
});

test('Cursor MCP integration is live (strategic flag demo)', async ({ page }) => {
  await page.goto('http://localhost:5173/sidebar/agents');
  const cursor = page.locator('[data-agent="Cursor"]');
  await expect(cursor).toBeVisible();
  await expect(cursor.locator('.agent-state')).toContainText('live');
  await expect(cursor.locator('.agent-qpm')).toMatch(/\d+ qpm/); // actual telemetry, not "0"
});
```

Plus visual-regression snapshots of all 4 main views.

---

## Effort estimate

10 days, parallelisable to 5-6 days with 2 sessions:

| Session | Workstream | Days |
|---|---|---|
| Frontend session | Phase A toolchain port + Phase B view wiring + Phase C polish | 6 |
| Backend session | New API endpoints + MCP telemetry + SSE streaming polish | 4 |

---

## Sequencing for the demo

Per DEMO-PRIORITY-ORDER.md:

- **Week 2** (in parallel with W2-A schema, W2-B RRF, W2-C ExplorationAgent): Phase A + Phase B core (Ask + Codebase views wired). MUST land by end of week 2 — without UI, no demo.
- **Week 3**: Phase B remaining views (Trace, PushFlow, Sidebar) + Phase C polish. Full demo flow rehearsable.

If running over budget: cut Phase C (polish) entirely. Cut PushFlow view if needed (it's the LEAST critical to the 90-second demo script). The Ask view + Codebase view + agents-panel-with-Cursor-live are the MUST-HAVE.

---

## Action items

1. [ ] Create `new-frontend/` Vite + React 18 + TS scaffold.
2. [ ] Copy + convert 7 JSX files → 7 TS view components (preserve all JSX literally).
3. [ ] Copy `app.css` + `prism.css` unchanged.
4. [ ] Extract inline SVG icons from `data.js` → `public/icons/`.
5. [ ] `src/data/brain_client.ts` — typed wrapper around `/api/v2/*`.
6. [ ] `src/data/mcp_client.ts` — wraps brain MCP server calls (for the Agents panel).
7. [ ] `src/data/mock_fallback.ts` — preserves prototype mock as offline-dev fallback.
8. [ ] Wire each view per Phase B mappings above.
9. [ ] Add 7 new backend endpoints under `/api/v2/`.
10. [ ] SSE streaming for `/query/stream` (use the existing pipeline `/jobs/{id}/stream` pattern).
11. [ ] Telemetry: per-render `query_latency_ms`, `mcp_agents_live_count`, `time_slider_used` (drives Series-A metric story).
12. [ ] Playwright e2e tests: lob-query end-to-end + Cursor MCP integration smoke test.
13. [ ] `docs/FRONTEND-INTEGRATION.md` documenting the API contract.
14. [ ] Dockerfile + production build for hosting (Cloudflare Pages or Vercel).
15. [ ] Decision: do we keep `company-brain-frontend/` as legacy and deprecate, OR delete and start clean? Recommend deprecate-then-delete after new-frontend is stable.
