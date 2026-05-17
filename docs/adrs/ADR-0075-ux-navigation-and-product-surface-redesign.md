# ADR-0075 — UX Navigation & Product Surface Redesign

**Status:** Proposed  
**Date:** 2026-05-17  
**Author:** Chinmay Jadhav  
**Depends on:** ADR-0073 (live data wiring), ADR-0074 (source registry)  
**Design handoff:** `docs/adrs/DESIGN-HANDOFF-0075-UI-REDESIGN.md`

---

## Context

The current `new-frontend` has correct data wiring (ADR-0073) but the product surface has six critical UX problems that make every demo session painful and prevent self-service onboarding:

| # | Problem | Impact |
|---|---|---|
| 1 | Navigation has no icons — all 6 items look identical weight | Can't glance-navigate; no visual anchor |
| 2 | Sources empty state is a dead end — no CTA, no button | First demo always starts with a dead wall |
| 3 | No "Add source" anywhere in the UI | Users must use CLI; not demo-friendly |
| 4 | Ask view has no scope context before first query | Users don't know what the brain knows |
| 5 | Sidebar source icons show UUID fragments ("A3", "F2") | Not meaningful — confuses every user |
| 6 | No Settings view — flags, workspace config, user prefs buried | ADR-0073 flags require secret Ctrl+Shift+L |

Beyond fixes, the product has grown in scope since ADR-0071 was written. We now have: time-travel queries, blast-radius graph, citations, MCP agents, source registry, owners rail, drift detection (ADR-0082), and document ingestion (ADR-0070). The information architecture was designed for 3 views; we now need to surface 8 features coherently.

### The pivot in product terms

**Then (ADR-0071):** "A UI for querying a single git repo that we've indexed with the CLI."  
**Now:** "Company Brain — an AI platform that knows everything about your engineering org. Connect any knowledge source (repos, APIs, docs, schemas, tickets). Ask questions about code, risk, ownership, drift, and change impact from any surface (web UI, Cursor, Devin, Claude, VS Code)."

This changes the IA: the primary nav is no longer 6 utility tabs — it's a product with a clear purpose hierarchy.

---

## Decision

### 1. Navigation restructure

**Current** (6 flat items, no icons, no hierarchy):
```
Ask | History | Saved | Agents · MCP | Audit Log | Sources
```

**New** (icons + two tiers: primary workflow vs. workspace management):

```
PRIMARY — what you do every day
  🔍 Ask          /ask
  📋 History      /history
  🔖 Saved        /saved

WORKSPACE — how your brain is configured  
  📡 Sources      /sources    ← includes "Add source"
  🤖 Agents       /agents
  📜 Audit        /audit
  ⚙️  Settings     /settings   ← NEW view (replaces FlagOverlay)
```

Visual treatment:
- Primary group: full-weight items, slightly larger font
- Workspace group: 90% opacity, group label "WORKSPACE" in uppercase 10px muted text
- Active item: left border accent + bg highlight
- Icons: 16px inline SVG, same stroke style as existing icons in the codebase
- Hover: bg-surface + text-primary transition

### 2. Settings view (new route `/settings`)

Replaces the hidden `FlagOverlay` (Ctrl+Shift+L). Every option the FlagOverlay controls is now surfaced here with labels and descriptions.

Sections:
- **Live data** — toggle LIVE_QUERY, LIVE_STREAM, LIVE_HEALTH, LIVE_BLAST, LIVE_CITATIONS individually with descriptions. "Enable all" / "Reset to mock" batch buttons.
- **Workspace** — display workspace ID, name, slug (read-only in v1). Copy buttons.
- **User** — display_name, email (read from /me, read-only in v1)
- **Danger zone** — "Clear all indexed data" (calls DELETE /workspaces/{id}/data with confirmation)

FlagOverlay keyboard shortcut (Ctrl+Shift+L) is kept as a power-user shortcut that now also opens /settings.

### 3. Ask view — scope context chip

Add a "Brain scope" info strip above the query input, replacing the vague empty state. Shows:
- Selected repo + branch (RepoPicker, already exists)
- Entity count from `/repos` response (`entity_count` field)
- Last indexed timestamp (`last_synced_at`)
- A "Not right? →" link that opens RepoPicker or navigates to /sources

```
[ git icon ] network-iq-backend-java@main  ·  1,432 entities  ·  indexed 2h ago  [Change]
```

When `repos.length === 0`: show a warm onboarding banner instead of the bare query input:
```
"Your brain has no sources yet. 
 [+ Add your first repo]  ← navigates to /sources with modal open
```

### 4. Sources view redesign

**Header:**
```
Sources                              [+ Add source]
```
"Add source" button always visible (primary action, right side).

**Source cards** — fix icon (derive from `kind`, not UUID):
```typescript
const KIND_ICONS: Record<string, string> = {
  git_local:     'GIT',
  git_remote:    'GIT',
  openapi:       'API',
  confluence:    'CF',
  db_migrations: 'SQL',
  github_prs:    'PR',
  slack_channel: 'SL',
  default:       '··',
};
```

Source card additions:
- Entity count badge (`entity_count` from source row)
- Progress bar during indexing (pulls from `/sources/{id}/progress` SSE)
- "View entities" link → navigates to Ask with `repo_path` filter pre-filled

**Empty state** — first-run onboarding card (not just text):
```
┌─────────────────────────────────────────────────────────────┐
│  Your Company Brain has no sources yet.                     │
│                                                             │
│  Connect a source to start asking questions about your      │
│  codebase, APIs, docs, and database schemas.               │
│                                                             │
│  [+ Add Git Repo]  [+ Add OpenAPI Spec]  [+ Confluence]    │
└─────────────────────────────────────────────────────────────┘
```

### 5. Add source modal

A 3-step modal triggered by any "Add source" CTA:

**Step 1 — Source type picker**
6 type cards in a 2×3 grid:
```
[📁 Git Repo    ] [🔌 OpenAPI Spec]
[📝 Confluence  ] [🗄️  DB Migrations]
[🐙 GitHub PRs  ] [+ More coming   ]
```
Each card shows: icon, name, 1-line description, availability badge (Ready / Soon).

**Step 2 — Config form** (varies by type)

Git Repo form:
```
Display name:  [___________________]
Repo path:     [/path/to/repo      ] [Browse]
Branch:        [main               ]
☑ Auto-index after adding
```

OpenAPI form:
```
Display name:  [___________________]
Spec URL or path: [https://... or /path/to/openapi.yaml]
☑ Auto-index after adding
```

Confluence form:
```
Display name:  [___________________]
Base URL:      [https://acme.atlassian.net]
Space key:     [ENG                ]
API token env: [CONF_TOKEN         ]  (reads from environment)
☑ Auto-index after adding
```

**Step 3 — Index progress** (if auto_index=True)
Spinner + live log tail from SSE stream. "Done — 1,432 entities indexed." with "View source" and "Ask a question" CTAs.

### 6. Minor fixes (no separate ADR needed)

- `useWorkspaceBootstrap`: fix double `getMe()` call — chain `.then(setMe).then(me => getRepos(me.workspace_id)).then(setRepos)`
- Sidebar source icons: use `KIND_ICONS[s.kind]` instead of `s.id.slice(0,2)`
- TopBar: add `{entity_count} entities` next to repo name when repos are loaded

---

## Information Architecture — full map

```
/ask         Primary query surface. Scope chip. Suggested questions. Streaming answer.
             → BlastRadius panel (right rail, post-answer)
             → Citations panel (right rail, post-answer)
             → Owners rail (right rail, post-answer)
             → Time Travel (right rail, toggle)
             → Compare (right rail, toggle)
             → MCP tools panel (right rail)

/history     All conversations for this workspace. Filter: all / saved / by repo.
             → Click → loads full answer in /ask with context restored

/saved       Bookmarked answers. Sortable. Exportable.

/sources     Source registry. Add / sync / remove sources.
             → "Add source" modal (3-step)
             → Per-source entity count + last synced + progress during index

/agents      MCP agent roster. Live/Idle status. Query counts. MCP endpoint URL to copy.
             → "Connect agent" docs inline

/audit       Full query + index + sync audit log. Filterable by user/source/date.

/settings    Feature flags (Live/Mock per flag). Workspace metadata. Danger zone.
```

---

## Options Considered

### Option A: Minimal fixes only
Fix the 6 bugs listed in Context, keep the same IA, don't add Settings or onboarding.

**Rejected because:** the IA problem compounds — every new feature we add (drift alerts, document ingestion, calibration packs) needs a home. Better to fix the IA now while the surface is still small.

### Option B: Full redesign with a new CSS design system
Throw out the current CSS variables and rebuild with Tailwind or a component library.

**Rejected because:** the current design system (CSS variables, `var(--accent-primary)` etc.) is well-designed and consistent. The problem is feature gaps, not visual language. Rewriting CSS adds risk for no UX gain.

### Option C: Nav icons + Settings + Add Source (chosen)
Keep the visual language, fix the IA, add the missing surfaces. Minimal risk, high impact.

---

## Consequences

### Easier
- New features have a designated home (Settings for config, Sources for all ingestion)
- First-time users can self-serve without the CLI
- Demos start with "Add your repo" rather than explaining how to run the CLI

### Harder
- Settings view is a new maintenance surface
- Step-2 config forms need validation for each source type (path exists, token works)

### Needs follow-up
- Path browser ("Browse" button in Git Repo form) — requires a backend `GET /fs/browse?path=...` endpoint for local path discovery. Defer to v2; v1 uses text input.
- Token validation — when a user enters a Confluence token, validate it before saving. Needs a `POST /sources/validate` endpoint.

---

## Action Items

- [ ] Add `/settings` route to App.tsx + nav
- [ ] Write `Settings.tsx` view
- [ ] Move FlagOverlay content into Settings (keep keyboard shortcut)
- [ ] Add nav icons to all 7 items in Sidebar.tsx
- [ ] Split nav into PRIMARY + WORKSPACE sections with labels
- [ ] Fix sidebar source icon: `KIND_ICONS[s.kind]`
- [ ] Add scope chip above Ask query bar
- [ ] Add "Add source" button to Sources header
- [ ] Write AddSourceModal.tsx (3-step: type picker → config form → progress)
- [ ] Write SourceProgress.tsx (SSE-connected progress bar component)
- [ ] Replace Sources empty state with onboarding card
- [ ] Add Ask view onboarding banner when repos.length === 0
- [ ] Fix double getMe() in useWorkspaceBootstrap
- [ ] Add entity_count to TopBar repo chip
