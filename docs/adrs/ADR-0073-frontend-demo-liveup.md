# ADR-0073 — Frontend Demo Live-Up: Kill the Mocks, Wire the Brain

**Status:** Proposed  
**Branch:** `feature/adr-0073-frontend-liveup`  
**Base:** `feat/adr-0072-frontend-views` (worktree at `.claude/worktrees/adr0072-frontend`)  
**Author:** Chinmay Jadhav  
**Date:** 2026-05-17  
**Cost budget:** $15 LLM · 4–6 h wall-time  

---

## Problem

The `new-frontend` (ADR-0072) is visually complete but functionally dead for a real demo:

| Area | Current state | Impact |
|---|---|---|
| Repo connect | No UI at all | Can't point the brain at a repo |
| Branch select | No UI, hardcoded `stripe-node@main` | Can't switch branch |
| Repo autodetect | Not implemented | First-run UX is broken |
| Query scope | Hardcoded string in Ask.tsx | Queries always use wrong repo |
| TopBar repo chip | Hardcoded `stripe-node · main · abc1234` | Misleading in demos |
| Sidebar recents | Mock `recents[]` from `mock_fallback.ts` | Shows fake history |
| Sidebar user | Hardcoded "Tom Blomfield / Owner · acme" | Embarrassing in demos |
| Sidebar workspace | Hardcoded "acme · payments" | Wrong in every install |
| Owners rail | Always mock, even when `LIVE_QUERY = true` | Blast radius data live, owners not |
| Bus factor | Hardcoded `2` + hardcoded PTO notice | Dangerously misleading |
| Related docs | All `href="#"` with fake filenames | Clicks go nowhere |
| Time travel | `as_of_date` is a TODO comment | Feature does nothing live |
| `workspace_id` | Falls back to `'default'` everywhere | Breaks every API call |
| Feature flags | All OFF by default, toggle hidden | Demo mode requires secret keyboard shortcut |

Additionally, five backend API endpoints the frontend needs don't exist yet:

- `GET /ai/workspaces/{id}/repos` — list indexed repos
- `GET /ai/workspaces/{id}/repos/{repoId}/branches` — list branches
- `GET /ai/entities/{urn}/owners` — git-blame owner breakdown
- `GET /ai/me` — current user (can be env-config stub)
- `GET /ai/workspaces/{id}` — workspace display metadata

---

## Decision

Land all five missing backend routes and fix all fourteen frontend problems in a single PR. No new mock surfaces are permitted; every panel must either render live data or show an honest empty/error state with a call-to-action.

---

## Scope — what's IN this ADR

### Backend (company-brain-ai)

**R1 — `GET /ai/workspaces/{id}/repos`**  
Returns the list of repos indexed into this workspace with their last-sync timestamp and current branch list.

```json
[
  {
    "id": "uuid",
    "display_name": "acme/payments",
    "repo_path": "/Users/chinmay/projects/payments",
    "default_branch": "main",
    "current_branch": "main",
    "last_synced_at": "2026-05-17T08:00:00Z",
    "entity_count": 1847,
    "sync_status": "ok"
  }
]
```

Source: query Postgres `workspaces` / `repos` table (or brain store manifest). Return the repo_path as `display_name` trimmed to the last two path segments if no label is set.

**R2 — `GET /ai/workspaces/{id}/repos/{repoId}/branches`**  
Run `git branch -a` (or `git for-each-ref`) inside `repo_path` and return branch list. Mark the current checked-out branch.

```json
{
  "current": "main",
  "branches": ["main", "feature/payments-v2", "hotfix/webhook-signing"]
}
```

Timeout: 3 s. If the path doesn't exist, return 404.

**R3 — `GET /ai/entities/{urn}/owners`**  
Accept a symbol URN (e.g. `urn:cb:fn:CustomerService.getById`). Look up the entity's `file_path` + `line_range` in the brain store. Run `git log --follow -L <range>:<file>` to get committers over the last 90 days. Return top-3 by commit count.

```json
{
  "urn": "urn:cb:fn:CustomerService.getById",
  "owners": [
    { "email": "jordan@acme.com", "name": "Jordan M.", "commit_count": 14, "last_commit_at": "2026-05-10T09:00:00Z", "pct": 41 },
    { "email": "priya@acme.com",  "name": "Priya A.",  "commit_count": 9,  "last_commit_at": "2026-05-06T14:00:00Z", "pct": 28 }
  ],
  "bus_factor": 2
}
```

Timeout: 5 s. Fall back to empty list on git error (don't 500).

**R4 — `GET /ai/me`**  
Stub derived from environment/config. No auth required for now.

```json
{
  "id": "local",
  "display_name": "You",
  "email": "jadhavchinmay0007@gmail.com",
  "workspace_id": "00000000-0000-0000-0000-000000000001",
  "workspace_name": "company-brain"
}
```

Read `CB_USER_NAME`, `CB_USER_EMAIL`, `CB_WORKSPACE_NAME` env vars; fall back to `git config user.name / user.email` and the current directory name.

**R5 — `GET /ai/workspaces/{id}`**  
Workspace metadata for the sidebar header.

```json
{
  "id": "00000000-0000-0000-0000-000000000001",
  "name": "company-brain",
  "slug": "dev",
  "repo_count": 1,
  "source_count": 2
}
```

---

### Frontend (new-frontend)

**F1 — Repo + branch picker**  
Replace the hardcoded `stripe-node@main` scope badge in `Ask.tsx` and the hardcoded `repoLabel` in `TopBar.tsx` with a real `<RepoPicker>` component.

- On mount: call `GET /ai/workspaces/{id}/repos` to get repo list
- On repo select: call `GET /ai/workspaces/{id}/repos/{repoId}/branches` to populate branch dropdown
- Persist selected `{ repoId, repoPath, branch }` in Zustand store (`useRepoStore`)
- All `queryBrain()` calls pass `repo_path` from the store
- Both `Ask.tsx` scope badge and `TopBar` chip read from `useRepoStore`

If only one repo exists → auto-select it and skip the picker dropdown (just show the chip).  
If zero repos → show a "Connect a repo" CTA that opens the Sources view.

**F2 — Workspace ID resolution**  
Remove the `window.__WORKSPACE_ID__ ?? 'default'` anti-pattern. Replace with a `useWorkspace()` hook that calls `GET /ai/me` on mount, stores the result in Zustand, and exposes `workspaceId`, `workspaceName`, `userEmail`. All components use this hook. `brain_client.ts` reads from the store on every call.

**F3 — Sidebar: live recents**  
Replace `recents` import from `mock_fallback` in `Sidebar.tsx` with `getConversations(workspaceId)`. Show the 5 most recent. Show a spinner during load; show nothing (not a placeholder) on error. On click, navigate to `/history`.

**F4 — Sidebar: live user + workspace name**  
Replace hardcoded "Tom Blomfield / Owner · acme" with data from `useWorkspace()` (display_name, email, workspace_name). Gracefully fall back to "You" if `/ai/me` fails.

**F5 — Owners rail: live git blame**  
In `Ask.tsx`, when `liveResp` is set, iterate `liveResp.cited_entity_urns` (or `affected_entities[0].id` as a proxy URN) and call `GET /ai/entities/{urn}/owners`. Replace the mock `owners` array. Show a loading skeleton (3 rows) while fetching. Fall back to the mock owners array if the call fails with a visible `(estimated)` label.

**F6 — Bus factor: live**  
Derive `bus_factor` from the owners response (R3 returns it directly). Remove the hardcoded PTO notice. Remove the hardcoded `2`.

**F7 — Related docs rail: live from QueryResponse**  
`QueryResponse` already returns `notes` and `call_chain`. Map `QueryResponse.notes` (type `{ urn, label, kind }`), filter for `kind === 'adr' | 'doc' | 'notion'`, and render those as the Related Docs list. Links should open `urn` in a new tab (brain store can resolve URNs to file paths). If empty, hide the rail entirely rather than showing hardcoded items.

**F8 — Time travel: wire as_of_date**  
The `ask()` function in `Ask.tsx` has this comment: `// time-travel TBD Phase C`. Remove the TODO. When `position < 1.0`, resolve the nearest `TimelineEvent.date` and pass it as `as_of_date` to `queryBrain()` / `queryBrainStream()`. Format as `YYYY-MM-DD`.

**F9 — Feature flags: visible toggle + smarter defaults**  
On mount, call `GET /ai/health`. If the health check succeeds, auto-enable `LIVE_QUERY` and `LIVE_HEALTH` flags (keep `LIVE_STREAM`, `LIVE_BLAST`, `LIVE_CITATIONS` off for stability). Add a visible pill in the `FlagOverlay` (or a small corner chip when overlay is closed) showing `● Live` / `○ Mock` so users can see and toggle the mode without knowing the keyboard shortcut.

**F10 — Remove all remaining `href="#"` links**  
Audit every `<a href="#">` in the codebase. Either wire to real navigation (e.g. `/history`, `/sources`) or remove the element. No dead links in the demo build.

---

## What's explicitly OUT of scope

- Authentication (no login/logout; single-user local)
- Multi-workspace switching
- Notion / Jira source connectors (still "coming soon" in Sources view)
- Time-travel UI redesign (just wire the existing slider)
- Compare view wiring (ADR-0074)
- MCP agent live wiring — already works via `getMcpAgents()` in `AgentsMCP.tsx`

---

## File ownership

```
# Backend — new files
company-brain-ai/src/companybrain/api/routes/repos.py       # NEW (R1, R2)
company-brain-ai/src/companybrain/api/routes/owners.py      # NEW (R3)
company-brain-ai/src/companybrain/api/routes/me.py          # NEW (R4)

# Backend — append-only
company-brain-ai/src/companybrain/api/server.py             # register new routes
company-brain-ai/src/companybrain/api/routes/workspace.py   # ADD GET /workspaces/{id} (R5)

# Frontend — new files
new-frontend/src/components/RepoPicker.tsx                  # NEW (F1)
new-frontend/src/store/repo_store.ts                        # NEW (F1, F2)
new-frontend/src/store/workspace_store.ts                   # NEW (F2)
new-frontend/src/hooks/useWorkspace.ts                      # NEW (F2)
new-frontend/src/hooks/useOwners.ts                         # NEW (F5, F6)

# Frontend — modified
new-frontend/src/views/Ask.tsx                              # F1, F5, F6, F7, F8, F10
new-frontend/src/components/TopBar.tsx                      # F1, F4
new-frontend/src/components/Sidebar.tsx                     # F3, F4
new-frontend/src/components/FlagOverlay.tsx                 # F9
new-frontend/src/data/brain_client.ts                       # R1-R5 client functions
new-frontend/src/data/feature_flags.ts                      # F9 auto-enable logic
new-frontend/src/data/mock_fallback.ts                      # remove recents export (F3)
new-frontend/vite.config.ts                                 # confirm proxy for /ai/me, /ai/workspaces

# Tests — new
company-brain-ai/tests/unit/test_repos_route.py
company-brain-ai/tests/unit/test_owners_route.py
company-brain-ai/tests/unit/test_me_route.py
```

Do NOT modify any other file.

---

## Acceptance criteria

- [ ] Vite dev server starts without errors on the worktree: `cd .claude/worktrees/adr0072-frontend/new-frontend && bun run dev`
- [ ] Navigating to `http://localhost:5173` shows the real workspace name and user name (not "Tom Blomfield" / "acme · payments")
- [ ] Scope badge in Ask shows a real indexed repo; clicking it opens branch dropdown
- [ ] Asking a question sends `repo_path` and `workspace_id` in the request body (verify in network tab)
- [ ] Owners rail shows real git-blame data for the returned entities (not hardcoded JM / PA / SK)
- [ ] Bus factor number matches the owners API response
- [ ] Sidebar "Recent" shows real conversation history from `/ai/conversations`
- [ ] Dragging the time-travel slider and re-asking a question sends a different `as_of_date`
- [ ] Related docs rail is hidden when `QueryResponse.notes` is empty; shows real docs when populated
- [ ] No `href="#"` anchors anywhere in the running app
- [ ] Health check auto-enables Live mode on startup when AI service is running
- [ ] `FlagOverlay` (or corner chip) shows current live/mock state visibly
- [ ] All existing `pytest` tests still pass

---

## Definition of done

Open the demo, point it at `network-iq-backend-java`, ask "What breaks if I rename customer_id?" → every panel populates from the live brain. No hardcoded names, no dead links, no mock owners.
