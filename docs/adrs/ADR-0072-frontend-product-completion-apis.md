# ADR-0072 — Frontend Product Completion: Backend APIs for Every Broken Surface

**Status:** Proposed  
**Date:** 2026-05-13  
**Builds on:** ADR-0071 (frontend rebuild), ADR-0064 (audit), ADR-0070 (connectors), ADR-0052 P5 (MCP server)  
**Sequenced with:** Must land before ADR-0071 can be called a product.

---

## Context

ADR-0071 delivered a TypeScript frontend with a working Ask view backed by `mock_fallback.ts`.
A full audit of the running app reveals **six completely non-functional surfaces**:

| Surface | Why broken | Backend gap |
|---|---|---|
| History tab | No query persistence | No `queries` table or read endpoint |
| Saved tab | No bookmark concept | No `saved_queries` table or endpoint |
| Agents · MCP tab | Hardcoded list | No live agent telemetry or `list_agents` endpoint |
| Audit log tab | Dead link | ADR-0064 proposed; nothing implemented |
| Sources panel | Hardcoded rows | No workspace-scoped source registry |
| Ask conversation | Can't go back / re-open | Sessions are in-memory JSON, never persisted |

Additionally:
- Suggested questions are always the same four strings — not derived from the actual codebase
- "4 agents live" in the TopBar is a hardcoded string
- Sources show fake green dots regardless of real sync status

This ADR specifies the **minimum backend changes** that make every currently-broken frontend surface real. It is not a greenfield design — it adds the smallest possible APIs on top of the infrastructure that already exists.

---

## Decision

Six additions, each independently shippable, prioritised by demo impact:

---

### A1 — Conversation History (P0 for demo)

**What the user sees:** History tab lists past questions. Clicking one reopens the full answer.

**Current gap:** `/query` returns an answer and discards it. No storage.

**Schema:**
```sql
-- V16__conversations.sql
CREATE TABLE conversations (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  question     TEXT NOT NULL,
  answer_md    TEXT,
  summary_json JSONB,         -- full QueryResponse cached
  asked_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  saved        BOOLEAN NOT NULL DEFAULT false,
  title        TEXT            -- user-editable, defaults to first 60 chars of question
);
CREATE INDEX ON conversations(workspace_id, asked_at DESC);
```

**New endpoints (Python AI service):**
```
POST /query               → unchanged, but now persists to conversations
GET  /conversations        → list last 50 for workspace, returns [{id, question, title, asked_at, saved}]
GET  /conversations/{id}   → returns full QueryResponse JSON
PATCH /conversations/{id}  → {saved?: bool, title?: str}  (save/rename/unsave)
DELETE /conversations/{id} → soft-delete
```

**Frontend tabs unlocked:** History (full list), Saved (WHERE saved=true filter)

---

### A2 — Saved Queries (P0 for demo, free once A1 lands)

**What the user sees:** Saved tab shows queries the user explicitly bookmarked. Thumbs-up in the answer footer marks it saved.

**No new schema needed** — `conversations.saved = true` covers it.

**New endpoints:**
```
GET  /conversations?saved=true   → Saved tab list
PATCH /conversations/{id}        → {saved: true/false}  (thumbs up/down in the answer footer)
```

**Frontend surfaces unlocked:** Saved tab, thumbs-up/down buttons in answer footer.

---

### A3 — MCP Agent Telemetry (P0 for demo — investors look at "Cursor live")

**What the user sees:** Agents · MCP tab shows which agent clients are currently connected, their QPM, and seat count. The "4 agents live" TopBar chip shows a real number.

**Current gap:** The MCP server (`mcp/server.py`) exists but has no connection registry. Connections are not tracked.

**Schema:**
```sql
-- V17__mcp_agent_sessions.sql
CREATE TABLE mcp_agent_sessions (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id UUID NOT NULL REFERENCES workspaces(id),
  agent_name   TEXT NOT NULL,       -- "Cursor", "Devin", "Cody", etc.
  client_id    TEXT NOT NULL,       -- client-supplied identifier
  connected_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_ping_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  disconnected_at TIMESTAMPTZ,      -- null = still connected
  query_count  INT NOT NULL DEFAULT 0
);
CREATE INDEX ON mcp_agent_sessions(workspace_id, disconnected_at NULLS FIRST);
```

**MCP server changes** (`mcp/server.py`):
- On new SSE connection: INSERT into `mcp_agent_sessions`
- On each tool call: UPDATE `last_ping_at`, increment `query_count`
- On disconnect: UPDATE `disconnected_at`

**New endpoint:**
```
GET /mcp/agents   → [{agent_name, client_id, connected_at, qpm (rolling 1-min), query_count, status: "live"|"idle"|"gone"}]
```

QPM = `query_count` difference over last 60 seconds (computed in Python, no extra table).

**Frontend surfaces unlocked:** Agents · MCP tab, TopBar "N agents live" chip.

---

### A4 — Source Registry (P1 — without it the sources panel is fiction)

**What the user sees:** Sources panel shows the repos and docs that are actually indexed for this workspace, with real sync timestamps and health dots.

**Current gap:** Sources are hardcoded in `mock_fallback.ts`. No workspace-scoped source registry exists. ADR-0070 proposes connectors but is entirely unimplemented.

**Decision: don't implement ADR-0070 connectors yet.** Just expose the sources that the brain already knows about from the pipeline (repos it has indexed).

**Schema (append to existing `workspaces` or pipeline tables):**
```sql
-- V18__workspace_sources.sql
CREATE TABLE workspace_sources (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id  UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  kind          TEXT NOT NULL,   -- 'github' | 'bitbucket' | 'local' | 'adr' | 'notion' | 'jira'
  display_name  TEXT NOT NULL,
  url           TEXT,            -- repo URL or Notion workspace URL
  last_synced_at TIMESTAMPTZ,
  sync_status   TEXT NOT NULL DEFAULT 'pending',  -- 'ok' | 'syncing' | 'error' | 'pending'
  error_message TEXT,
  meta          JSONB           -- branch, commit SHA, page count, etc.
);
CREATE INDEX ON workspace_sources(workspace_id);
```

**How sources get created:**
- When a pipeline run completes (`/v1/internal/pipeline-result`), Java upserts a `workspace_sources` row for each repo in the request.
- ADR connectors (Notion, Jira) will write their own rows when they land.

**New endpoint:**
```
GET  /api/v1/workspaces/{id}/sources   → list sources with status
POST /api/v1/workspaces/{id}/sources   → manually add a source (optional for now)
POST /api/v1/workspaces/{id}/sources/{sid}/sync  → trigger a re-index
```

**Frontend surfaces unlocked:** Sources panel in sidebar (real dots, real names, real sync time).

---

### A5 — Audit Log Read API (P2 — needed for Audit log tab)

**What the user sees:** Audit log tab shows a paginated list of who queried what, when. Each row: actor, question preview, timestamp.

**Current gap:** ADR-0064's full hash-chained audit is unimplemented. That's a 3-week project. We need something shippable in 1 day.

**Decision: lightweight query-level audit only.** Re-use the `conversations` table from A1 — it already captures every query with `workspace_id` and `asked_at`. The audit log tab is just a filtered, read-only view of that table with the actor field added.

**Schema amendment to A1:**
```sql
ALTER TABLE conversations ADD COLUMN actor_id TEXT;   -- user/agent identifier
ALTER TABLE conversations ADD COLUMN actor_kind TEXT;  -- 'user' | 'cursor' | 'devin' | 'mcp'
```

**Endpoint (already exists after A1):**
```
GET /conversations?sort=asked_at&actor=&since=&until=   → reuse for audit tab
```

**Frontend surfaces unlocked:** Audit log tab (paginated query history with actor column).

**Note:** The full tamper-proof hash-chained audit (ADR-0064 M3) is a separate project. This is the 1-day version.

---

### A6 — Suggested Questions from the Brain (P1 — kills hardcoded chips)

**What the user sees:** The four "Try" chips under the query bar reflect actual hotspot questions for the indexed codebase — not always "What breaks if I drop lobName."

**Current gap:** `suggested` in `mock_fallback.ts` is hardcoded.

**Solution:** The AI `/query` endpoint already returns `follow_up_questions` in every `QueryResponse`. Additionally, expose a lightweight endpoint for the initial empty-state chips:

```
GET /suggestions?repo={repo_id}   → [{question, context}]
```

Implementation: on first load, call this endpoint; it runs a fast heuristic over the brain store (top-N entities by blast radius + most recently modified). No LLM call. Falls back to the current mock if the brain has no data.

**Frontend surfaces unlocked:** Initial suggested chips, post-answer follow-up chips.

---

## Files touched

### Python AI service (`company-brain-ai/`)
```
src/companybrain/api/routes/conversations.py    NEW — A1, A2, A5
src/companybrain/api/routes/mcp_agents.py       NEW — A3
src/companybrain/api/routes/suggestions.py      NEW — A6
src/companybrain/api/routes/query.py            EDIT — persist to conversations on each /query call
src/companybrain/mcp/server.py                  EDIT — register/unregister agent sessions
src/companybrain/api/main.py                    EDIT — include new routers
```

### Java backend (`company-brain-backend/`)
```
src/.../controller/WorkspaceController.java     NEW — A4 sources CRUD
src/.../routes/pipeline_result handler          EDIT — upsert workspace_sources row on pipeline complete
```

### DB migrations
```
db/migrations/V16__conversations.sql            NEW — A1
db/migrations/V17__mcp_agent_sessions.sql       NEW — A3
db/migrations/V18__workspace_sources.sql        NEW — A4
```

### Frontend (`new-frontend/`)
```
src/data/brain_client.ts          EDIT — add getConversations, getConversation, patchConversation, getSuggestions, getSources, getAgents
src/views/History.tsx             NEW — History tab
src/views/Saved.tsx               NEW — Saved tab (thin wrapper on History with saved=true)
src/views/AgentsMCP.tsx           NEW — Agents · MCP tab
src/views/AuditLog.tsx            NEW — Audit log tab
src/views/Sources.tsx             NEW — future Sources management (stub for now)
src/App.tsx                       EDIT — wire all tabs to real routes
src/components/Sidebar.tsx        EDIT — make nav links functional
```

---

## What we explicitly do NOT build in this ADR

- Full ADR-0064 hash-chained audit (3 weeks; not needed for demo)
- ADR-0070 connector OAuth flows (Notion, Jira auth — 4 weeks; show as "Coming soon")
- Multi-user access control / team management
- Source deletion / de-indexing
- Agent rate limiting / seat enforcement

---

## Acceptance criteria (demo-blocking)

```
✓ Clicking "History" shows the last N questions asked in this session
✓ Clicking a history item reopens the full answer
✓ Thumbs-up marks a query saved; "Saved" tab shows it
✓ "Agents · MCP" tab shows real connected agents or a clear "none connected yet" state
✓ The TopBar "N agents live" chip reflects the real count (0 when no MCP client is connected)
✓ Sources panel shows repos the pipeline has actually indexed (not the hardcoded GitHub/Bitbucket rows)
✓ "Audit log" tab shows a list of queries with timestamps
✓ Suggested chips on first load come from /suggestions, not mock_fallback.ts
✓ All four sidebar tabs are navigable without a 404 or blank page
```

---

## Effort estimate

| Item | Backend | Frontend | Total |
|---|---|---|---|
| A1 — Conversations (History + Saved) | 1.5d | 0.5d | 2d |
| A2 — Saved (free from A1) | 0d | 0.5d | 0.5d |
| A3 — MCP Agent Telemetry | 1d | 0.5d | 1.5d |
| A4 — Source Registry | 1d | 0.5d | 1.5d |
| A5 — Audit log tab | 0d (reuses A1) | 0.5d | 0.5d |
| A6 — Real suggestions | 0.5d | 0.5d | 1d |
| **Total** | **4d** | **3d** | **7d** |

Parallelisable to **4 days** with two sessions (backend + frontend).
