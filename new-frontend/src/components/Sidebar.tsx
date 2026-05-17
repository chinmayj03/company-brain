import React, { useState, useEffect } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { getSources, getConversations, type WorkspaceSource, type ConversationSummary } from '../data/brain_client';
import { useWorkspaceStore } from '../store/workspace_store';
import { sourceKindLabel } from '../utils/sourceKind';

// ── Icons (16×16, stroke 1.6, linecap round) ─────────────────────────────────

const IconSearch = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6"
    strokeLinecap="round" strokeLinejoin="round" style={{ width: 15, height: 15, flexShrink: 0 }}>
    <circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>
  </svg>
);
const IconClock = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6"
    strokeLinecap="round" strokeLinejoin="round" style={{ width: 15, height: 15, flexShrink: 0 }}>
    <circle cx="12" cy="12" r="9"/><polyline points="12 7 12 12 15 14"/>
  </svg>
);
const IconBookmark = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6"
    strokeLinecap="round" strokeLinejoin="round" style={{ width: 15, height: 15, flexShrink: 0 }}>
    <path d="M19 21l-7-5-7 5V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z"/>
  </svg>
);
const IconDatabase = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6"
    strokeLinecap="round" strokeLinejoin="round" style={{ width: 15, height: 15, flexShrink: 0 }}>
    <ellipse cx="12" cy="5" rx="9" ry="3"/>
    <path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3"/>
    <path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5"/>
  </svg>
);
const IconCpu = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6"
    strokeLinecap="round" strokeLinejoin="round" style={{ width: 15, height: 15, flexShrink: 0 }}>
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
    strokeLinecap="round" strokeLinejoin="round" style={{ width: 15, height: 15, flexShrink: 0 }}>
    <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
    <polyline points="14 2 14 8 20 8"/>
    <line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/>
    <polyline points="10 9 9 9 8 9"/>
  </svg>
);
const IconSettings = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6"
    strokeLinecap="round" strokeLinejoin="round" style={{ width: 15, height: 15, flexShrink: 0 }}>
    <circle cx="12" cy="12" r="3"/>
    <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/>
  </svg>
);
const IconChevron = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"
    strokeLinecap="round" strokeLinejoin="round" style={{ width: 13, height: 13 }}>
    <polyline points="9 18 15 12 9 6"/>
  </svg>
);
const IconSpinner = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"
    style={{ width: 12, height: 12, animation: 'spin 1s linear infinite' }}>
    <path d="M12 2a10 10 0 0 1 10 10"/>
  </svg>
);
const IconClockSmall = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6"
    strokeLinecap="round" strokeLinejoin="round" style={{ width: 14, height: 14 }}>
    <circle cx="12" cy="12" r="9"/><polyline points="12 7 12 12 15 14"/>
  </svg>
);

// ── Nav config ────────────────────────────────────────────────────────────────

const PRIMARY_NAV = [
  { id: 'ask',     label: 'Ask',     path: '/ask',     icon: <IconSearch /> },
  { id: 'history', label: 'History', path: '/history', icon: <IconClock /> },
  { id: 'saved',   label: 'Saved',   path: '/saved',   icon: <IconBookmark /> },
];

const WORKSPACE_NAV = [
  { id: 'sources',  label: 'Sources',  path: '/sources',  icon: <IconDatabase /> },
  { id: 'agents',   label: 'Agents',   path: '/agents',   icon: <IconCpu /> },
  { id: 'audit',    label: 'Audit',    path: '/audit',    icon: <IconScroll /> },
  { id: 'settings', label: 'Settings', path: '/settings', icon: <IconSettings /> },
];

// ── Source dot ────────────────────────────────────────────────────────────────

function sourceDotStyle(status: WorkspaceSource['sync_status']): React.CSSProperties {
  const colors: Record<WorkspaceSource['sync_status'], string> = {
    ok:      'var(--success)',
    syncing: 'var(--accent-primary)',
    error:   'var(--danger)',
    pending: 'var(--text-muted)',
  };
  return {
    display: 'inline-block', width: 7, height: 7,
    borderRadius: '50%', flexShrink: 0,
    background: colors[status],
  };
}

// ── Sidebar component ─────────────────────────────────────────────────────────

export default function Sidebar() {
  const location  = useLocation();
  const navigate  = useNavigate();
  const me        = useWorkspaceStore((s) => s.me);
  const workspaceId = useWorkspaceStore((s) => s.workspaceId);

  const [sources, setSources]               = useState<WorkspaceSource[]>([]);
  const [sourcesLoading, setSourcesLoading] = useState(true);
  const [sourcesError, setSourcesError]     = useState(false);
  const [recents, setRecents]               = useState<ConversationSummary[]>([]);

  useEffect(() => {
    let cancelled = false;
    getSources(workspaceId)
      .then((data) => { if (!cancelled) { setSources(data); setSourcesLoading(false); } })
      .catch(() => { if (!cancelled) { setSourcesError(true); setSourcesLoading(false); } });
    return () => { cancelled = true; };
  }, [workspaceId]);

  useEffect(() => {
    let cancelled = false;
    getConversations(workspaceId)
      .then((data) => { if (!cancelled) setRecents(data.slice(0, 5)); })
      .catch(() => {});
    return () => { cancelled = true; };
  }, [workspaceId]);

  function isActive(path: string): boolean {
    return location.pathname === path || location.pathname.startsWith(path + '/');
  }

  function NavItem({ item }: { item: { id: string; label: string; path: string; icon: React.ReactNode } }) {
    const active = isActive(item.path);
    return (
      <Link
        to={item.path}
        className="sb__item"
        data-active={active ? 'true' : undefined}
        style={{ textDecoration: 'none', display: 'flex', alignItems: 'center', gap: 8 }}
      >
        <span style={{
          color: active ? 'var(--accent-primary)' : 'var(--text-muted)',
          display: 'flex', alignItems: 'center', flexShrink: 0,
          transition: 'color .15s',
        }}>
          {item.icon}
        </span>
        <span style={{ flex: 1 }}>{item.label}</span>
      </Link>
    );
  }

  return (
    <aside className="sb">
      {/* Brand */}
      <div className="sb__brand">
        <div className="sb__mark">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2"
            strokeLinecap="round" strokeLinejoin="round">
            <path d="M4 20V12"/><path d="M9 20V8"/><path d="M14 20v-9"/><path d="M19 20V5"/>
          </svg>
        </div>
        <div className="col">
          <span className="name">Company Brain</span>
          <span className="ws">{me?.workspace_name ?? '…'}</span>
        </div>
      </div>

      {/* Primary nav */}
      <div className="sb__section">
        {PRIMARY_NAV.map((item) => <NavItem key={item.id} item={item} />)}
      </div>

      {/* Workspace nav with divider */}
      <div className="sb__section" style={{ marginTop: 4 }}>
        <div style={{
          fontSize: 9, fontWeight: 700, letterSpacing: '0.10em',
          textTransform: 'uppercase', color: 'var(--text-muted)',
          padding: '4px 8px 6px', marginBottom: 2,
          borderTop: '1px solid var(--border-default)', paddingTop: 12,
        }}>
          Workspace
        </div>
        {WORKSPACE_NAV.map((item) => <NavItem key={item.id} item={item} />)}
      </div>

      {/* Sources section */}
      <div className="sb__section">
        <div className="sb__label" style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <span>Sources</span>
          <button
            onClick={() => navigate('/sources')}
            title="Add source"
            style={{
              background: 'transparent', border: 'none', cursor: 'pointer',
              color: 'var(--text-muted)', fontSize: 16, lineHeight: 1,
              padding: '0 2px', display: 'flex', alignItems: 'center',
            }}
          >+</button>
        </div>

        {sourcesLoading && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '8px 8px', fontSize: 12, color: 'var(--text-tertiary)' }}>
            <IconSpinner /> Loading…
          </div>
        )}

        {!sourcesLoading && (sourcesError || sources.length === 0) && (
          <div
            onClick={() => navigate('/sources')}
            style={{ padding: '6px 8px', fontSize: 12, color: 'var(--text-muted)', fontStyle: 'italic', cursor: 'pointer' }}
          >
            No sources · Add one →
          </div>
        )}

        {!sourcesLoading && !sourcesError && sources.map((s) => (
          <div className="src-row" key={s.id}>
            <div className="ico" style={{ background: 'var(--bg-surface)', color: 'var(--text-secondary)' }}>
              <span style={{ fontSize: 9, fontWeight: 700, fontFamily: 'var(--font-mono)', letterSpacing: '0.02em' }}>
                {sourceKindLabel(s.kind)}
              </span>
            </div>
            <div className="col">
              <div className="nm">{s.display_name}</div>
              <div className="meta">
                {s.last_synced_at
                  ? `synced ${new Date(s.last_synced_at).toLocaleDateString()}`
                  : s.sync_status}
              </div>
            </div>
            <span style={sourceDotStyle(s.sync_status)} title={s.sync_status} />
          </div>
        ))}

        {sourcesLoading && (
          <>
            {[...Array(2)].map((_, i) => (
              <div key={i} style={{
                height: 42, borderRadius: 6, margin: '2px 0',
                background: 'var(--bg-surface)', opacity: 0.5 - i * 0.15,
                animation: 'pulse 1.6s ease-in-out infinite',
              }} />
            ))}
          </>
        )}
      </div>

      {/* Recent queries */}
      {recents.length > 0 && (
        <div className="sb__section">
          <div className="sb__label">Recent</div>
          {recents.map((r) => (
            <Link key={r.id} to="/history" className="sb__item" style={{ alignItems: 'flex-start', textDecoration: 'none' }}>
              <IconClockSmall />
              <div style={{ display: 'flex', flexDirection: 'column', minWidth: 0, lineHeight: 1.35 }}>
                <span style={{ fontSize: 12, color: 'var(--text-secondary)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', maxWidth: 180 }}>
                  {r.title ?? r.question}
                </span>
                <span style={{ fontSize: 10.5, color: 'var(--text-muted)', marginTop: 1 }}>
                  {new Date(r.asked_at).toLocaleDateString()}
                </span>
              </div>
            </Link>
          ))}
        </div>
      )}

      {/* User */}
      <div className="sb__user">
        <div className="av">
          {(me?.display_name ?? 'You').slice(0, 2).toUpperCase()}
        </div>
        <div className="col">
          <span className="nm">{me?.display_name ?? 'You'}</span>
          <span className="role">{me?.email ?? ''}</span>
        </div>
        <span style={{ color: 'var(--text-tertiary)', marginLeft: 'auto' }}><IconChevron /></span>
      </div>

      <style>{`
        @keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }
      `}</style>
    </aside>
  );
}
