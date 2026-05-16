import { useState, useEffect } from 'react';
import { Link, useLocation } from 'react-router-dom';
import { getSources, getConversations, type WorkspaceSource, type ConversationSummary } from '../data/brain_client';
import { useWorkspaceStore } from '../store/workspace_store';

// ── Inline SVG helpers ────────────────────────────────────────────────────────

const IconClock = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" style={{ width: 14, height: 14 }}>
    <circle cx="12" cy="12" r="9"/><polyline points="12 7 12 12 15 14"/>
  </svg>
);
const IconChevron = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" style={{ width: 13, height: 13 }}>
    <polyline points="9 18 15 12 9 6"/>
  </svg>
);

// Spinner for loading state
const IconSpinner = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"
    style={{ width: 12, height: 12, animation: 'spin 1s linear infinite' }}>
    <path d="M12 2a10 10 0 0 1 10 10"/>
  </svg>
);

// ── Nav config ────────────────────────────────────────────────────────────────

const navItems = [
  { id: 'ask',     label: 'Ask',          path: '/ask'     },
  { id: 'history', label: 'History',      path: '/history' },
  { id: 'saved',   label: 'Saved',        path: '/saved'   },
  { id: 'agents',  label: 'Agents · MCP', path: '/agents'  },
  { id: 'audit',   label: 'Audit Log',    path: '/audit'   },
  { id: 'sources', label: 'Sources',      path: '/sources' },
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
  const location = useLocation();
  const me = useWorkspaceStore((s) => s.me);
  const workspaceId = useWorkspaceStore((s) => s.workspaceId);

  const [sources, setSources]       = useState<WorkspaceSource[]>([]);
  const [sourcesLoading, setSourcesLoading] = useState(true);
  const [sourcesError, setSourcesError]     = useState(false);

  const [recents, setRecents] = useState<ConversationSummary[]>([]);

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

  return (
    <aside className="sb">
      {/* Brand */}
      <div className="sb__brand">
        <div className="sb__mark">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M4 20V12"/><path d="M9 20V8"/><path d="M14 20v-9"/><path d="M19 20V5"/>
          </svg>
        </div>
        <div className="col">
          <span className="name">Company Brain</span>
          <span className="ws">{me?.workspace_name ?? '…'}</span>
        </div>
      </div>

      {/* Main nav */}
      <div className="sb__section">
        {navItems.map((item) => (
          <Link
            key={item.id}
            to={item.path}
            className="sb__item"
            data-active={isActive(item.path) ? 'true' : undefined}
            style={{ textDecoration: 'none' }}
          >
            <span style={{ flex: 1 }}>{item.label}</span>
          </Link>
        ))}
      </div>

      {/* Sources section */}
      <div className="sb__section">
        <div className="sb__label">Sources</div>

        {sourcesLoading && (
          <div style={{
            display: 'flex', alignItems: 'center', gap: 8,
            padding: '8px 8px', fontSize: 12, color: 'var(--text-tertiary)',
          }}>
            <IconSpinner />
            Loading…
          </div>
        )}

        {!sourcesLoading && (sourcesError || sources.length === 0) && (
          <div style={{
            padding: '6px 8px', fontSize: 12,
            color: 'var(--text-muted)', fontStyle: 'italic',
          }}>
            No sources
          </div>
        )}

        {!sourcesLoading && !sourcesError && sources.map((s) => (
          <div className="src-row" key={s.id}>
            <div className="ico" style={{ background: 'var(--bg-surface)', color: 'var(--text-secondary)' }}>
              <span style={{ fontSize: 10, fontWeight: 600 }}>{s.id.toUpperCase().slice(0, 2)}</span>
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

        {/* Fallback to keep section visible when sources are loading/error - show a placeholder row */}
        {sourcesLoading && (
          <>
            {[...Array(3)].map((_, i) => (
              <div key={i} style={{
                height: 42, borderRadius: 6, margin: '2px 0',
                background: 'var(--bg-surface)', opacity: 0.5 - i * 0.1,
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
              <IconClock />
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

      {/* Inline keyframe for spinner */}
      <style>{`@keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }`}</style>
    </aside>
  );
}
