import { useState, useEffect, useRef } from 'react';
import Sidebar from '../components/Sidebar';
import TopBar from '../components/TopBar';
import { getMcpAgents, type McpAgent } from '../data/brain_client';
import { useWorkspaceStore } from '../store/workspace_store';

// ── Icons ─────────────────────────────────────────────────────────────────────

const IconCopy = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6"
    strokeLinecap="round" strokeLinejoin="round" style={{ width: 13, height: 13 }}>
    <rect x="9" y="9" width="13" height="13" rx="2"/>
    <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>
  </svg>
);

// ── Helpers ───────────────────────────────────────────────────────────────────

function StatusBadge({ status }: { status: McpAgent['status'] }) {
  const colors: Record<McpAgent['status'], { bg: string; border: string; text: string; dot: string }> = {
    live: { bg: 'var(--success-soft)', border: 'var(--success-border)', text: 'var(--success)', dot: 'var(--success)' },
    idle: { bg: 'var(--warning-soft)', border: 'var(--warning-border)', text: 'var(--warning)', dot: 'var(--warning)' },
    gone: { bg: 'var(--bg-surface)',   border: 'var(--border-default)', text: 'var(--text-muted)', dot: 'var(--text-muted)' },
  };
  const c = colors[status];
  return (
    <span style={{
      display: 'inline-flex', alignItems: 'center', gap: 5,
      fontSize: 11, fontWeight: 600, letterSpacing: '0.04em',
      padding: '2px 8px', borderRadius: 10,
      background: c.bg, border: `1px solid ${c.border}`, color: c.text,
    }}>
      <span style={{
        width: 6, height: 6, borderRadius: '50%',
        background: c.dot,
        boxShadow: status === 'live' ? `0 0 0 3px ${c.bg}` : 'none',
      }} />
      {status}
    </span>
  );
}

function relativeTime(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const mins  = Math.floor(diff / 60_000);
  const hours = Math.floor(diff / 3_600_000);
  const days  = Math.floor(diff / 86_400_000);
  if (mins < 1)   return 'just now';
  if (mins < 60)  return `${mins}m ago`;
  if (hours < 24) return `${hours}h ago`;
  return `${days}d ago`;
}

type Filter = 'all' | 'live' | 'idle';

// ── Main component ────────────────────────────────────────────────────────────

export default function AgentsMCP() {
  const workspaceId = useWorkspaceStore((s) => s.workspaceId);

  const [agents, setAgents]   = useState<McpAgent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError]     = useState<string | null>(null);
  const [filter, setFilter]   = useState<Filter>('all');
  const [copied, setCopied]   = useState(false);
  const intervalRef           = useRef<ReturnType<typeof setInterval> | null>(null);

  const mcpEndpoint = `${window.location.origin}/mcp`;

  function fetchAgents() {
    getMcpAgents(workspaceId)
      .then((data) => { setAgents(data); setLoading(false); setError(null); })
      .catch((err: unknown) => {
        setError(err instanceof Error ? err.message : 'Failed to load agents');
        setLoading(false);
      });
  }

  useEffect(() => {
    fetchAgents();
    intervalRef.current = setInterval(fetchAgents, 30_000);
    return () => { if (intervalRef.current) clearInterval(intervalRef.current); };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workspaceId]);

  function copyEndpoint() {
    navigator.clipboard.writeText(mcpEndpoint).catch(() => {});
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }

  const filtered = agents.filter((a) => filter === 'all' || a.status === filter);

  const filterBtn = (f: Filter, label: string) => (
    <button
      onClick={() => setFilter(f)}
      style={{
        height: 28, padding: '0 12px', border: '1px solid transparent',
        borderRadius: 4, fontSize: 12, fontWeight: 500, cursor: 'pointer',
        background: filter === f ? 'var(--accent-soft)' : 'transparent',
        borderColor: filter === f ? 'var(--accent-soft-border)' : 'var(--border-default)',
        color: filter === f ? 'var(--accent-primary)' : 'var(--text-secondary)',
      }}
    >
      {label}
      {f !== 'all' && (
        <span style={{ marginLeft: 5, fontSize: 10, opacity: 0.7 }}>
          ({agents.filter(a => a.status === f).length})
        </span>
      )}
    </button>
  );

  return (
    <div className="app">
      <Sidebar />
      <main className="main">
        <TopBar crumb="Agents · MCP" />
        <div className="qview">
          <div className="va-content">

            {/* MCP endpoint strip */}
            <div style={{
              display: 'flex', alignItems: 'center', gap: 12,
              padding: '12px 16px',
              background: 'var(--neutral-900)',
              borderRadius: 8, marginBottom: 20,
              fontFamily: 'var(--font-mono)', fontSize: 12,
              color: 'var(--neutral-400)',
            }}>
              <span style={{ color: '#6B8FE3' }}>MCP endpoint</span>
              <span style={{ color: 'var(--neutral-200)', flex: 1 }}>{mcpEndpoint}</span>
              <button
                onClick={copyEndpoint}
                style={{
                  height: 24, padding: '0 10px',
                  background: 'transparent', border: '1px solid var(--neutral-700)',
                  borderRadius: 3, color: copied ? 'var(--success)' : 'var(--neutral-300)',
                  fontSize: 11, cursor: 'pointer', display: 'inline-flex', alignItems: 'center', gap: 5,
                }}
              >
                <IconCopy /> {copied ? 'Copied!' : 'Copy'}
              </button>
            </div>

            {/* Header row with filters */}
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 16 }}>
              <h2 style={{ margin: 0, fontSize: 14, fontWeight: 600, color: 'var(--text-primary)', flex: 1 }}>
                Connected agents
                <span style={{ marginLeft: 8, fontSize: 12, fontWeight: 400, color: 'var(--text-tertiary)' }}>
                  · polls every 30s
                </span>
              </h2>
              <div style={{ display: 'flex', gap: 4 }}>
                {filterBtn('all', 'All')}
                {filterBtn('live', 'Live')}
                {filterBtn('idle', 'Idle')}
              </div>
            </div>

            {/* Loading */}
            {loading && (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                {[...Array(3)].map((_, i) => (
                  <div key={i} style={{
                    height: 72, borderRadius: 8,
                    background: 'var(--bg-surface)',
                    animation: 'pulse 1.6s ease-in-out infinite',
                    opacity: 0.6 - i * 0.1,
                  }} />
                ))}
              </div>
            )}

            {/* Error */}
            {!loading && error && (
              <div style={{
                padding: 20, borderRadius: 8,
                background: 'var(--danger-soft)', border: '1px solid var(--danger-border)',
                color: 'var(--danger)', fontSize: 13,
              }}>
                <strong>Failed to load agents</strong> — {error}
              </div>
            )}

            {/* Empty state */}
            {!loading && !error && filtered.length === 0 && (
              <div style={{
                textAlign: 'center', padding: '80px 0',
                color: 'var(--text-tertiary)', fontSize: 14,
              }}>
                <div style={{ fontSize: 16, color: 'var(--text-secondary)', marginBottom: 8, fontWeight: 500 }}>
                  {agents.length === 0
                    ? 'No agents connected yet.'
                    : `No ${filter} agents.`}
                </div>
                {agents.length === 0 && (
                  <div>Point a Cursor or Devin client at the MCP endpoint.</div>
                )}
              </div>
            )}

            {/* Agent cards */}
            {!loading && !error && filtered.length > 0 && (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                {filtered.map((agent) => (
                  <div key={agent.id} style={{
                    display: 'flex', alignItems: 'center', gap: 16,
                    padding: '14px 18px',
                    background: 'var(--warm-surface)',
                    border: '1px solid var(--warm-line)',
                    borderRadius: 8,
                  }}>
                    {/* Avatar */}
                    <div style={{
                      width: 36, height: 36, borderRadius: 8, flexShrink: 0,
                      background: 'var(--bg-surface)',
                      display: 'grid', placeItems: 'center',
                      fontFamily: 'var(--font-mono)', fontSize: 13, fontWeight: 700,
                      color: 'var(--text-secondary)',
                    }}>
                      {agent.agent_name.slice(0, 2).toUpperCase()}
                    </div>

                    {/* Name + client */}
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--text-primary)' }}>
                        {agent.agent_name}
                      </div>
                      <div style={{ fontSize: 11, color: 'var(--text-tertiary)', marginTop: 2, fontVariantNumeric: 'tabular-nums' }}>
                        {agent.client_id} · connected {relativeTime(agent.connected_at)} · last ping {relativeTime(agent.last_ping_at)}
                      </div>
                    </div>

                    {/* Stats */}
                    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 3 }}>
                      <div style={{ fontVariantNumeric: 'tabular-nums', fontSize: 13, fontWeight: 500, color: 'var(--text-primary)' }}>
                        {agent.query_count.toLocaleString()} queries
                      </div>
                      <div style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>
                        {agent.qpm.toFixed(1)} qpm
                      </div>
                    </div>

                    {/* Status badge */}
                    <div style={{ flexShrink: 0 }}>
                      <StatusBadge status={agent.status} />
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      </main>
    </div>
  );
}
