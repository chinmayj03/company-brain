# Design Handoff — ADR-0075 UI Redesign

**For:** Sonnet / frontend engineer implementing ADR-0075  
**Date:** 2026-05-17  
**Status:** Ready for implementation  
**Design system:** Existing CSS variables in `new-frontend/src/index.css` — no changes to tokens  

---

## Design Principles

1. **Keep the visual language** — same CSS variables, same dark theme, same font stack. We're fixing features, not redesigning.
2. **Every empty state has a CTA** — no page is a dead wall.
3. **Icons are purely functional** — simple 16px inline SVG, 1.6 stroke-width, consistent with existing icons in the codebase.
4. **Group by purpose** — primary workflow nav vs. workspace management nav.

---

## Component Specs

---

### C1 — Sidebar Navigation (update to `Sidebar.tsx`)

**Current:** 6 flat text items, no icons, no hierarchy  
**New:** icons + two groups with a divider

#### Layout

```
┌─────────────────────────┐
│  ▓▓ Company Brain       │  ← brand mark (unchanged)
│     workspace name      │
├─────────────────────────┤
│                         │
│  🔍 Ask                 │  ← PRIMARY group (no label)
│  📋 History             │
│  🔖 Saved               │
│                         │
│  ──── WORKSPACE ──────  │  ← divider + label
│                         │
│  📡 Sources             │  ← WORKSPACE group
│  🤖 Agents              │
│  📜 Audit               │
│  ⚙️  Settings            │
│                         │
├─────────────────────────┤
│  [src1 icon] src name ● │  ← Sources section (unchanged)
│  [src2 icon] src name ● │
├─────────────────────────┤
│  [GH] Recent query...   │  ← Recents (unchanged)
├─────────────────────────┤
│  [AV] Name  · email     │  ← User (unchanged)
└─────────────────────────┘
```

#### Nav item spec

```tsx
// navItems split into two groups:
const PRIMARY_NAV = [
  { id: 'ask',     label: 'Ask',      path: '/ask',      icon: <IconSearch /> },
  { id: 'history', label: 'History',  path: '/history',  icon: <IconClock /> },
  { id: 'saved',   label: 'Saved',    path: '/saved',    icon: <IconBookmark /> },
];

const WORKSPACE_NAV = [
  { id: 'sources', label: 'Sources',  path: '/sources',  icon: <IconDatabase /> },
  { id: 'agents',  label: 'Agents',   path: '/agents',   icon: <IconCpu /> },
  { id: 'audit',   label: 'Audit',    path: '/audit',    icon: <IconScroll /> },
  { id: 'settings',label: 'Settings', path: '/settings', icon: <IconSettings /> },
];
```

#### Icon SVGs (inline, 16×16, stroke 1.6, linecap round)

```tsx
const IconSearch = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6"
    strokeLinecap="round" strokeLinejoin="round" style={{width:16,height:16,flexShrink:0}}>
    <circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>
  </svg>
);

const IconClock = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6"
    strokeLinecap="round" strokeLinejoin="round" style={{width:16,height:16,flexShrink:0}}>
    <circle cx="12" cy="12" r="9"/><polyline points="12 7 12 12 15 14"/>
  </svg>
);

const IconBookmark = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6"
    strokeLinecap="round" strokeLinejoin="round" style={{width:16,height:16,flexShrink:0}}>
    <path d="M19 21l-7-5-7 5V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z"/>
  </svg>
);

const IconDatabase = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6"
    strokeLinecap="round" strokeLinejoin="round" style={{width:16,height:16,flexShrink:0}}>
    <ellipse cx="12" cy="5" rx="9" ry="3"/>
    <path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3"/>
    <path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5"/>
  </svg>
);

const IconCpu = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6"
    strokeLinecap="round" strokeLinejoin="round" style={{width:16,height:16,flexShrink:0}}>
    <rect x="4" y="4" width="16" height="16" rx="2"/>
    <rect x="9" y="9" width="6" height="6"/>
    <line x1="9" y1="2" x2="9" y2="4"/><line x1="15" y1="2" x2="15" y2="4"/>
    <line x1="9" y1="20" x2="9" y2="22"/><line x1="15" y1="20" x2="15" y2="22"/>
    <line x1="2" y1="9" x2="4" y2="9"/><line x1="2" y1="15" x2="4" y2="15"/>
    <line x1="20" y1="9" x2="22" y2="9"/><line x1="20" y1="15" x2="22" y2="15"/>
  </svg>
);

const IconScroll = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6"
    strokeLinecap="round" strokeLinejoin="round" style={{width:16,height:16,flexShrink:0}}>
    <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
    <polyline points="14 2 14 8 20 8"/>
    <line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/>
    <polyline points="10 9 9 9 8 9"/>
  </svg>
);

const IconSettings = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6"
    strokeLinecap="round" strokeLinejoin="round" style={{width:16,height:16,flexShrink:0}}>
    <circle cx="12" cy="12" r="3"/>
    <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/>
  </svg>
);
```

#### Nav item styles

```tsx
// sb__item with icon
<Link
  to={item.path}
  className="sb__item"
  data-active={isActive(item.path) ? 'true' : undefined}
  style={{ textDecoration: 'none', display: 'flex', alignItems: 'center', gap: 8 }}
>
  <span style={{
    color: isActive(item.path) ? 'var(--accent-primary)' : 'var(--text-muted)',
    display: 'flex', alignItems: 'center',
  }}>
    {item.icon}
  </span>
  <span style={{ flex: 1 }}>{item.label}</span>
</Link>
```

#### WORKSPACE group divider

```tsx
// Between PRIMARY_NAV and WORKSPACE_NAV
<div style={{
  margin: '8px 8px 4px',
  borderTop: '1px solid var(--border-default)',
  paddingTop: 8,
}}>
  <div style={{
    fontSize: 9, fontWeight: 700, letterSpacing: '0.10em',
    textTransform: 'uppercase', color: 'var(--text-muted)',
    padding: '0 8px', marginBottom: 4,
  }}>
    Workspace
  </div>
  {WORKSPACE_NAV.map(item => <NavItem key={item.id} item={item} />)}
</div>
```

#### Source kind icons

```tsx
// In both Sidebar.tsx and Sources.tsx
const KIND_LABEL: Record<string, string> = {
  git_local:     'GIT',
  git_remote:    'GIT',
  openapi:       'API',
  confluence:    'CF ',
  db_migrations: 'SQL',
  github_prs:    'PR ',
  slack_channel: 'SLK',
  notion:        'NOT',
  jira:          'JRA',
};

function sourceKindLabel(kind: string): string {
  return KIND_LABEL[kind] ?? kind.slice(0, 3).toUpperCase();
}

// Usage: replace s.id.toUpperCase().slice(0, 2) with sourceKindLabel(s.kind)
```

---

### C2 — Ask view scope chip (new, above query bar)

**File:** `new-frontend/src/views/Ask.tsx`  
**Position:** Between TopBar and the query input — renders above the `<Suggested>` component

```tsx
// ScopeChip component — inline in Ask.tsx
function ScopeChip() {
  const { selectedRepo, selectedBranch, repos } = useRepoStore();

  // Case 1: no repos — show onboarding banner
  if (repos.length === 0) {
    return (
      <div style={{
        padding: '16px 20px', borderRadius: 10, marginBottom: 20,
        background: 'var(--accent-soft)',
        border: '1px solid var(--accent-soft-border)',
        display: 'flex', alignItems: 'center', gap: 14,
      }}>
        <div style={{ flex: 1 }}>
          <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--text-primary)', marginBottom: 4 }}>
            Your brain has no sources yet
          </div>
          <div style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
            Connect a repository to start asking questions about your codebase.
          </div>
        </div>
        <button
          onClick={() => navigate('/sources')}
          style={{
            height: 32, padding: '0 16px', borderRadius: 6,
            background: 'var(--accent-primary)', color: '#fff',
            border: 'none', fontSize: 13, fontWeight: 600, cursor: 'pointer',
            flexShrink: 0,
          }}
        >
          + Add your first repo
        </button>
      </div>
    );
  }

  // Case 2: repo selected — show scope info
  if (!selectedRepo) return null;

  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: 8,
      padding: '8px 14px', borderRadius: 8, marginBottom: 12,
      background: 'var(--bg-surface)',
      border: '1px solid var(--border-default)',
      fontSize: 12, color: 'var(--text-secondary)',
    }}>
      <IconGit2 />
      <span style={{ color: 'var(--text-primary)', fontWeight: 500 }}>
        {selectedRepo.display_name}
      </span>
      <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--text-tertiary)' }}>
        @{selectedBranch}
      </span>
      {selectedRepo.entity_count > 0 && (
        <>
          <span style={{ color: 'var(--border-default)' }}>·</span>
          <span>{selectedRepo.entity_count.toLocaleString()} entities</span>
        </>
      )}
      {selectedRepo.last_synced_at && (
        <>
          <span style={{ color: 'var(--border-default)' }}>·</span>
          <span>indexed {relativeTime(selectedRepo.last_synced_at)}</span>
        </>
      )}
      <span style={{ flex: 1 }} />
      <RepoPicker />  {/* existing component — floats right */}
    </div>
  );
}
```

---

### C3 — Sources view (update `Sources.tsx`)

**Header row:**
```tsx
<div style={{ display: 'flex', alignItems: 'center', marginBottom: 16 }}>
  <h2 style={{ margin: 0, fontSize: 14, fontWeight: 600, color: 'var(--text-primary)', flex: 1 }}>
    Sources
    {!loading && sources.length > 0 && (
      <span style={{ marginLeft: 8, fontSize: 12, fontWeight: 400, color: 'var(--text-tertiary)' }}>
        · {sources.length}
      </span>
    )}
  </h2>
  <button
    onClick={() => setAddModalOpen(true)}
    style={{
      height: 32, padding: '0 14px', borderRadius: 6,
      background: 'var(--accent-primary)', color: '#fff',
      border: 'none', fontSize: 13, fontWeight: 600, cursor: 'pointer',
      display: 'flex', alignItems: 'center', gap: 6,
    }}
  >
    + Add source
  </button>
</div>
```

**First-run empty state:**
```tsx
{!loading && !error && sources.length === 0 && (
  <div style={{
    padding: '32px 24px', borderRadius: 12,
    background: 'var(--warm-surface)',
    border: '1px solid var(--warm-line)',
  }}>
    <div style={{ fontSize: 16, fontWeight: 600, color: 'var(--text-primary)', marginBottom: 8 }}>
      Connect your first source
    </div>
    <div style={{ fontSize: 13, color: 'var(--text-secondary)', marginBottom: 20, lineHeight: 1.6 }}>
      Company Brain learns from your code, APIs, and docs. Connect a source to start answering
      questions about your engineering system.
    </div>
    <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
      {[
        { kind: 'git_local',  label: 'Git Repo'       },
        { kind: 'openapi',    label: 'OpenAPI Spec'   },
        { kind: 'confluence', label: 'Confluence'     },
      ].map(({ kind, label }) => (
        <button
          key={kind}
          onClick={() => { setSelectedKind(kind); setAddModalOpen(true); }}
          style={{
            height: 34, padding: '0 14px', borderRadius: 6, cursor: 'pointer',
            background: 'var(--bg-surface)',
            border: '1px solid var(--border-default)',
            fontSize: 13, fontWeight: 500, color: 'var(--text-secondary)',
            display: 'flex', alignItems: 'center', gap: 6,
          }}
        >
          + {label}
        </button>
      ))}
    </div>
  </div>
)}
```

**Source card additions (entity count + kind icon):**
```tsx
{/* Replace the UUID icon with kind icon */}
<div style={{
  width: 36, height: 36, borderRadius: 8, flexShrink: 0,
  background: 'var(--bg-surface)',
  display: 'grid', placeItems: 'center',
  fontSize: 10, fontWeight: 700,
  fontFamily: 'var(--font-mono)',
  color: 'var(--text-secondary)',
}}>
  {sourceKindLabel(s.kind)}  {/* ← was: s.id.toUpperCase().slice(0, 2) */}
</div>

{/* Add entity count badge */}
{s.entity_count != null && s.entity_count > 0 && (
  <span style={{
    fontSize: 11, fontWeight: 500, color: 'var(--text-muted)',
    background: 'var(--bg-surface)',
    border: '1px solid var(--border-default)',
    padding: '1px 7px', borderRadius: 4,
    fontVariantNumeric: 'tabular-nums',
  }}>
    {s.entity_count.toLocaleString()} entities
  </span>
)}
```

---

### C4 — AddSourceModal (new component)

**File:** `new-frontend/src/components/AddSourceModal.tsx`

Three-step modal. Renders as a centered overlay.

**Step 1 — Type picker**
```
┌────────────────────────────────────────────┐
│ Add a source                          [×]  │
├────────────────────────────────────────────┤
│                                            │
│  ┌──────────┐  ┌──────────┐              │
│  │  GIT     │  │  API     │              │
│  │ Git Repo │  │ OpenAPI  │              │
│  │ Local or │  │ Spec URL │              │
│  │ remote   │  │ or path  │              │
│  │ [READY]  │  │ [READY]  │              │
│  └──────────┘  └──────────┘              │
│                                            │
│  ┌──────────┐  ┌──────────┐              │
│  │   CF     │  │  SQL     │              │
│  │Confluence│  │ DB Migr. │              │
│  │ Wiki and │  │ Flyway / │              │
│  │ docs     │  │Liquibase │              │
│  │ [READY]  │  │ [READY]  │              │
│  └──────────┘  └──────────┘              │
│                                            │
│  ┌──────────┐  ┌──────────┐              │
│  │   PR     │  │  ···     │              │
│  │GitHub PRs│  │ Coming   │              │
│  │ Reviews  │  │ soon     │              │
│  │ history  │  │          │              │
│  │ [READY]  │  │          │              │
│  └──────────┘  └──────────┘              │
│                                            │
└────────────────────────────────────────────┘
```

Type card style:
```tsx
// Clickable, 2-col grid, 140×120px each
<div
  onClick={() => { setKind(type.kind); setStep(2); }}
  style={{
    padding: '16px 14px', borderRadius: 10, cursor: 'pointer',
    border: `1px solid ${selectedKind === type.kind ? 'var(--accent-primary)' : 'var(--border-default)'}`,
    background: selectedKind === type.kind ? 'var(--accent-soft)' : 'var(--bg-surface)',
    transition: 'all .15s',
  }}
>
  <div style={{ fontFamily: 'var(--font-mono)', fontSize: 11, fontWeight: 700,
    color: 'var(--text-secondary)', marginBottom: 6 }}>
    {KIND_LABEL[type.kind]}
  </div>
  <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-primary)', marginBottom: 4 }}>
    {type.label}
  </div>
  <div style={{ fontSize: 11, color: 'var(--text-tertiary)', lineHeight: 1.4 }}>
    {type.description}
  </div>
  <div style={{ marginTop: 8 }}>
    <span style={{
      fontSize: 10, fontWeight: 600, padding: '1px 7px', borderRadius: 4,
      background: type.available ? 'var(--success-soft)' : 'var(--bg-surface)',
      color: type.available ? 'var(--success)' : 'var(--text-muted)',
      border: `1px solid ${type.available ? 'var(--success-border)' : 'var(--border-default)'}`,
    }}>
      {type.available ? 'Ready' : 'Soon'}
    </span>
  </div>
</div>
```

**Step 2 — Config form** (Git Repo example)
```tsx
// Inputs use existing form styles — border, bg-surface, focus accent-primary
<label style={labelStyle}>Display name</label>
<input style={inputStyle} value={name} onChange={e => setName(e.target.value)}
  placeholder="e.g. payments-service" />

<label style={labelStyle}>Repository path</label>
<input style={inputStyle} value={repoPath} onChange={e => setRepoPath(e.target.value)}
  placeholder="/Users/you/code/payments" />

<label style={labelStyle}>Branch</label>
<input style={inputStyle} value={branch} onChange={e => setBranch(e.target.value)}
  defaultValue="main" />

<label style={{ ...labelStyle, display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer' }}>
  <input type="checkbox" checked={autoIndex} onChange={e => setAutoIndex(e.target.checked)} />
  Index immediately after adding
</label>

// Footer
<div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
  <button onClick={() => setStep(1)} style={secondaryBtnStyle}>← Back</button>
  <button onClick={handleSubmit} disabled={!name || !repoPath} style={primaryBtnStyle}>
    Add source {autoIndex && '& Index'}
  </button>
</div>
```

**Step 3 — Progress**
```tsx
// Only shown if autoIndex=true
<div style={{ padding: '24px 0', textAlign: 'center' }}>
  {indexStatus === 'running' && (
    <>
      <div style={{ fontSize: 14, fontWeight: 500, marginBottom: 12, color: 'var(--text-primary)' }}>
        Indexing {sourceName}…
      </div>
      <ProgressBar stage={currentStage} />
      <div style={{ fontSize: 12, color: 'var(--text-tertiary)', marginTop: 8, fontFamily: 'var(--font-mono)' }}>
        {currentStage}
      </div>
    </>
  )}
  {indexStatus === 'completed' && (
    <>
      <div style={{ fontSize: 28, marginBottom: 12 }}>✅</div>
      <div style={{ fontSize: 15, fontWeight: 600, color: 'var(--text-primary)' }}>
        Indexed {entityCount.toLocaleString()} entities
      </div>
      <div style={{ display: 'flex', gap: 8, justifyContent: 'center', marginTop: 20 }}>
        <button onClick={onClose} style={secondaryBtnStyle}>Done</button>
        <button onClick={() => { onClose(); navigate('/ask'); }} style={primaryBtnStyle}>
          Ask a question →
        </button>
      </div>
    </>
  )}
</div>
```

**Shared styles:**
```tsx
const labelStyle: React.CSSProperties = {
  display: 'block', fontSize: 12, fontWeight: 600,
  color: 'var(--text-secondary)', marginBottom: 4, marginTop: 12,
};
const inputStyle: React.CSSProperties = {
  width: '100%', height: 34, padding: '0 10px',
  background: 'var(--bg-surface)', border: '1px solid var(--border-default)',
  borderRadius: 6, color: 'var(--text-primary)', fontSize: 13,
  fontFamily: 'var(--font-mono)',
  outline: 'none',
};
const primaryBtnStyle: React.CSSProperties = {
  height: 32, padding: '0 16px', borderRadius: 6,
  background: 'var(--accent-primary)', color: '#fff',
  border: 'none', fontSize: 13, fontWeight: 600, cursor: 'pointer',
};
const secondaryBtnStyle: React.CSSProperties = {
  height: 32, padding: '0 14px', borderRadius: 6,
  background: 'transparent', color: 'var(--text-secondary)',
  border: '1px solid var(--border-default)', fontSize: 13, cursor: 'pointer',
};
```

**Modal overlay wrapper:**
```tsx
// position: fixed, full screen, z-index 9000
// centered card: max-width 520px, bg-overlay, border, border-radius 12, padding 24
// click-outside closes
```

---

### C5 — Settings view (new `Settings.tsx`)

```
/settings
┌─────────────────────────────────────────────────┐
│ Settings                                        │
├─────────────────────────────────────────────────┤
│ LIVE DATA                                       │
│                                                 │
│  Live queries       [toggle] Fetch answers      │
│                              from real brain    │
│  Streaming          [toggle] Use SSE streaming  │
│  Health check       [toggle] Auto-enable live   │
│  Blast radius       [toggle] Live graph data    │
│  Citations          [toggle] Live citations     │
│                                                 │
│  [Enable all live]   [Reset to mock]            │
├─────────────────────────────────────────────────┤
│ WORKSPACE                                       │
│                                                 │
│  ID:    00000000-0000-0000-0000-000000000001 [⎘]│
│  Name:  my-workspace                        [⎘]│
├─────────────────────────────────────────────────┤
│ USER                                            │
│                                                 │
│  Name:  Chinmay Jadhav                          │
│  Email: jadhavchinmay0007@gmail.com             │
└─────────────────────────────────────────────────┘
```

Toggle component:
```tsx
function Toggle({ checked, onChange, label, description }: ToggleProps) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '10px 0',
      borderBottom: '1px solid var(--border-default)' }}>
      <div style={{ flex: 1 }}>
        <div style={{ fontSize: 13, fontWeight: 500, color: 'var(--text-primary)' }}>{label}</div>
        <div style={{ fontSize: 11, color: 'var(--text-tertiary)', marginTop: 2 }}>{description}</div>
      </div>
      <button
        role="switch" aria-checked={checked} onClick={() => onChange(!checked)}
        style={{
          width: 36, height: 20, borderRadius: 10, border: 'none', cursor: 'pointer',
          background: checked ? 'var(--accent-primary)' : 'var(--border-default)',
          position: 'relative', transition: 'background .15s',
          flexShrink: 0,
        }}
      >
        <span style={{
          position: 'absolute', top: 2, left: checked ? 18 : 2,
          width: 16, height: 16, borderRadius: '50%', background: '#fff',
          transition: 'left .15s',
        }} />
      </button>
    </div>
  );
}
```

---

### C6 — Fix `useWorkspaceBootstrap.ts`

```tsx
// BEFORE (broken — calls getMe() twice)
export function useWorkspaceBootstrap() {
  const setMe = useWorkspaceStore((s) => s.setMe);
  const setRepos = useRepoStore((s) => s.setRepos);

  useEffect(() => {
    getHealth()
      .then(() => flags.setAll(true))
      .catch(() => {});

    getMe().then(setMe).catch(() => {});  // ← first call

    getMe()  // ← second call (race condition + wasted request)
      .then((me) => getRepos(me.workspace_id).then(setRepos))
      .catch(() => {});
  }, []);
}

// AFTER (correct — chains on a single getMe() call)
export function useWorkspaceBootstrap() {
  const setMe = useWorkspaceStore((s) => s.setMe);
  const setRepos = useRepoStore((s) => s.setRepos);

  useEffect(() => {
    getHealth()
      .then(() => flags.setAll(true))
      .catch(() => {});

    getMe()
      .then((me) => {
        setMe(me);
        return getRepos(me.workspace_id);
      })
      .then(setRepos)
      .catch(() => {});
  }, []); // eslint-disable-line react-hooks/exhaustive-deps
}
```

---

## Files to create / modify

| Action | File | Notes |
|---|---|---|
| MODIFY | `src/components/Sidebar.tsx` | Icons, nav groups, kind icons |
| MODIFY | `src/hooks/useWorkspace.ts` | Fix double getMe() |
| MODIFY | `src/views/Ask.tsx` | ScopeChip, onboarding banner |
| MODIFY | `src/views/Sources.tsx` | Header CTA, kind icons, entity count, onboarding card |
| CREATE | `src/components/AddSourceModal.tsx` | 3-step modal |
| CREATE | `src/views/Settings.tsx` | Flags + workspace metadata |
| MODIFY | `src/App.tsx` | Add /settings route |

## No changes needed

- `brain_client.ts` — already has all the API calls needed
- `workspace_store.ts` — already correct
- `repo_store.ts` — already correct
- `LiveModeChip.tsx` — keep as is (still useful as a quick toggle)
- `FlagOverlay.tsx` — keep as is, keyboard shortcut → also navigates to /settings
- `vite.config.ts` — proxy config is correct
- CSS / index.css — no token changes
