# PRD-0001 — Company Brain: Frontend Product Completion

**Status:** Approved for implementation  
**Date:** 2026-05-13  
**Author:** Product  
**Implements:** ADR-0072 (backend APIs), ADR-0071 (frontend rebuild)  
**Demo target:** Investor demo + design partner pilot  

---

## Problem statement

The Company Brain frontend (ADR-0071) ships a working Ask view backed by `mock_fallback.ts`. Every other surface in the app — History, Saved, Agents · MCP, Audit Log, Sources — is either a hardcoded stub or a dead tab. The "4 agents live" TopBar chip is a string literal. Suggested questions are always the same four. Sources show fake green dots.

This means we cannot show an investor a session that feels real, cannot give a design partner access without embarrassment, and cannot retain a user beyond the first five minutes.

This PRD defines the minimum change set that makes every surface real and removes every hardcoded string from the UI.

---

## Users and buyers

| Persona | Description | Primary surfaces |
|---|---|---|
| **Developer** | Asks questions about the codebase during daily work | Ask, History, Saved |
| **Engineering Lead** | Monitors agent activity, tracks query patterns | Agents · MCP, Audit Log |
| **Compliance / Security** | Reviews who queried what and when | Audit Log |
| **Platform Admin** | Manages what data is indexed, triggers re-syncs | Sources |
| **AI Agent (Cursor / Devin / Cody)** | Issues queries via MCP protocol | API surface only |

---

## Goals

1. Every tab in the sidebar navigates to a functional, non-blank page.
2. "N agents live" TopBar chip reflects reality (0 when no MCP client is connected).
3. History and Saved tabs show real past queries from the current session and beyond.
4. The Sources panel shows repos the pipeline has actually indexed — no hardcoded rows.
5. The Audit Log tab shows a read-only paginated query log.
6. Suggested chips on first load come from the brain's actual indexed content, not static strings.

---

## Non-goals (explicit scope cuts)

- Full hash-chained tamper-proof audit log (ADR-0064 M3) — 3 weeks; out of scope
- Connector OAuth flows for Notion, Jira (ADR-0070) — 4 weeks; shown as "Coming soon"
- Multi-user access control, team management, roles
- Source deletion / de-indexing
- Agent rate limiting or seat enforcement
- Real-time collaboration on queries
- Mobile / responsive layout

---

## Feature breakdown

---

### F1 — Conversation History

**User story:**  
*As a developer, I want to see the questions I've asked in the past so I can revisit an answer without asking again.*

**Acceptance criteria:**
- History tab lists the last 50 queries for the workspace, newest first
- Each row shows: question preview (truncated at 80 chars), timestamp (relative: "2 min ago", "3 days ago"), saved indicator
- Clicking a row opens the full answer in the Ask view — same layout as a fresh query
- Answers load from the cached `summary_json`, not by re-running the query
- If history is empty: "No questions yet. Ask your first question →" with a link to Ask
- History persists across page reloads and browser restarts (server-side, not localStorage)

**Backend (ADR-0072 A1):**
- `POST /query` persists each answer to `conversations` table before returning
- `GET /conversations` — list last 50, fields: `id, question, title, asked_at, saved`
- `GET /conversations/{id}` — full `QueryResponse` JSON

**Out of scope for F1:** search/filter within history, delete individual items from the UI

---

### F2 — Saved Queries

**User story:**  
*As a developer, I want to bookmark answers I find useful so I can return to them later.*

**Acceptance criteria:**
- Thumbs-up button in the answer footer marks a query saved (visual toggle, immediate optimistic update)
- Thumbs-down or second thumbs-up press un-saves
- Saved tab shows only saved queries, same row format as History
- Save state is per-query, not per-session
- Saved badge visible in the History list for cross-reference

**Backend (ADR-0072 A2):**
- `PATCH /conversations/{id}` — `{saved: true|false}`
- `GET /conversations?saved=true` — Saved tab list

**Out of scope for F2:** shared saves, team-level curation, collections/folders

---

### F3 — MCP Agent Telemetry

**User story:**  
*As an engineering lead, I want to see which AI agents are connected to our knowledge base so I know the system is being used and nothing looks anomalous.*

**Acceptance criteria:**
- Agents · MCP tab shows a live list of connected agents: name, status badge (live / idle / disconnected), queries today, queries per minute
- Status updates within 60 seconds of an agent disconnecting
- TopBar "N agents live" chip shows a real count; 0 when no MCP client is connected
- If no agents have ever connected: clear empty state "No agents connected yet. Point a Cursor or Devin client at the MCP endpoint."
- The MCP endpoint URL is shown with a one-click copy button
- Connected agents can be filtered by status (all / live / idle)

**Backend (ADR-0072 A3):**
- `mcp/server.py` registers sessions on connect, pings on tool call, closes on disconnect
- `GET /mcp/agents` — list with computed QPM, status derived from `last_ping_at`

**Out of scope for F3:** per-agent query detail drill-down, revoke agent access from the UI, agent seat limits

---

### F4 — Source Registry

**User story:**  
*As a platform admin, I want to see what repos and docs are indexed in the brain so I know what the AI is drawing from when it answers.*

**Acceptance criteria:**
- Sources panel in sidebar lists every source the pipeline has indexed for this workspace
- Each row: source name, kind (GitHub / Bitbucket / ADR / Notion), last synced time, health dot (green = ok, amber = syncing, red = error)
- Clicking "Sync" on a row triggers a re-index of that source
- Error rows show the error message on hover / expand
- If no sources have been indexed: "No sources indexed yet. Run the pipeline to populate."
- Panel is read-only for developers; Sync button visible to admins only (role: workspace_admin)

**Backend (ADR-0072 A4):**
- `workspace_sources` table populated on each pipeline result via Java handler
- `GET /api/v1/workspaces/{id}/sources` — sources list
- `POST /api/v1/workspaces/{id}/sources/{sid}/sync` — trigger re-index

**Out of scope for F4:** manually adding new source URLs from the UI (sources come from pipeline), source deletion, Notion/Jira connector auth (ADR-0070)

---

### F5 — Audit Log

**User story:**  
*As a compliance officer, I want a read-only log of every query made to the brain — who asked it, when, and what — so I can satisfy audit requirements.*

**Acceptance criteria:**
- Audit Log tab shows a paginated table: actor (user or agent name), question preview, timestamp
- Default sort: newest first
- Filters: actor, date range (since / until)
- Each row is expandable to show the full question and a link to the answer (if still in history)
- Export to CSV button (client-side, from loaded data — no server export endpoint required for now)
- If empty: "No queries logged yet."

**Backend (ADR-0072 A5):**
- `conversations` table gets `actor_id` and `actor_kind` columns
- `GET /conversations?actor=&since=&until=&sort=asked_at` reused for audit tab

**Note:** this is the lightweight query-level audit. The full hash-chained tamper-proof audit (ADR-0064) is a separate, future initiative.

**Out of scope for F5:** server-side CSV export, audit log alerting, compliance report generation

---

### F6 — Real Suggested Questions

**User story:**  
*As a developer opening the app for the first time, I want the suggested questions to reflect actual hotspots in my codebase so I know what the brain knows.*

**Acceptance criteria:**
- Four chips shown on the Ask empty state come from the brain's indexed content — entities with the highest blast radius or most recent modification
- Chips update when the source index changes (cache TTL: 1 hour)
- On fallback (brain has no data, endpoint unavailable): existing static chips are shown — no error state displayed
- After an answer, follow-up chips come from `QueryResponse.follow_up_questions` (already returned by the API)
- Chips are tappable and pre-fill the query bar

**Backend (ADR-0072 A6):**
- `GET /suggestions?repo={repo_id}` — heuristic over brain store, no LLM call, max 200ms
- Falls back to static defaults if brain store is empty

**Out of scope for F6:** personalised suggestions per user, suggestions based on team query patterns

---

## UX notes

### Empty states
Every previously-broken tab must have a useful empty state when data is absent — not a blank page, not a spinner that never resolves. Empty states should link to the action that would populate the view (e.g., "Run the pipeline" for Sources, "Ask a question" for History).

### Error states
Network errors should surface inline with a retry action, not a full-page error. If the backend is unreachable, mock/cached data should be shown with a "Live data unavailable" banner — never a blank or broken page in front of a design partner.

### Loading states
Skeleton rows (not spinners) for list views. Latency target: History list loads under 300ms p95.

### Navigation
All sidebar links must route to real pages. No 404s. No React `<a href="#">` dead links.

---

## Success metrics (post-launch)

| Metric | Target | Measurement |
|---|---|---|
| History tab opens without blank page | 100% | Manual QA + synthetic test |
| Saved tab shows correct saved queries | 100% | Acceptance test |
| TopBar agent count accurate | ±0 | Manual: connect Cursor, verify count |
| Sources panel shows indexed repos | ≥1 source when pipeline has run | Manual QA |
| Audit log shows queries with actor | 100% of /query calls | DB row check |
| Suggested chips differ from hardcoded | Yes, when brain has data | Visual QA |
| Zero "blank page" tabs in sidebar | 100% | Playwright e2e sweep |

---

## Phased rollout

### Phase 1 — Demo-critical (P0, ship before any investor demo)
- F1 History tab
- F2 Saved queries + thumbs-up button
- F3 MCP agent telemetry + live TopBar count
- All sidebar tabs navigate (even if F4/F5/F6 show empty states with messaging)

### Phase 2 — Product-complete (P1, ship within 2 weeks of Phase 1)
- F4 Sources panel (real data, sync button)
- F6 Real suggested questions

### Phase 3 — Design-partner-ready (P2, ship before first design partner onboarding)
- F5 Audit log tab
- CSV export
- Sources sync trigger with status feedback

---

## Effort summary

| Feature | Backend | Frontend | Total |
|---|---|---|---|
| F1 History | 1.5d | 0.5d | 2d |
| F2 Saved | 0d (reuses F1) | 0.5d | 0.5d |
| F3 MCP Telemetry | 1d | 0.5d | 1.5d |
| F4 Sources | 1d | 0.5d | 1.5d |
| F5 Audit Log | 0d (reuses F1) | 0.5d | 0.5d |
| F6 Suggestions | 0.5d | 0.5d | 1d |
| **Total** | **4d** | **3d** | **7d** |

Two parallel streams (backend + frontend) → **4 calendar days**.

---

## Open questions

| # | Question | Owner | Due |
|---|---|---|---|
| 1 | Role system for Sources sync button (admin vs. developer) — do we ship role-gating in Phase 1 or show Sync to all? | Product | Before Phase 1 dev starts |
| 2 | QPM computation window: 60s rolling vs. last-N-queries? 60s is simpler and avoids the case where a burst during a demo looks like 0 QPM a minute later | Backend | Before A3 impl |
| 3 | Audit log retention policy: how long do we keep `conversations` rows? | Product / Legal | Before Phase 3 |
| 4 | Suggestion chip count: 4 chips hardcoded or configurable? | Product | Before F6 impl |

---

## Dependencies

- ADR-0071 frontend rebuild — **done**  
- ADR-0072 backend API decisions — **done**  
- DB migration runner (Flyway) — **exists**, used for V1–V15 already  
- MCP server (`mcp/server.py`) — **exists**, needs session tracking added  
- Java pipeline result handler — **exists**, needs upsert call added  
