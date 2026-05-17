import { useState, useEffect } from 'react';
import Sidebar from '../components/Sidebar';
import TopBar from '../components/TopBar';
import { getSources, triggerSync, type WorkspaceSource } from '../data/brain_client';
import { useWorkspaceStore } from '../store/workspace_store';

// ── Helpers ───────────────────────────────────────────────────────────────────

function relativeTime(iso: string | undefined): string {
  if (!iso) return 'never';
  const diff = Date.now() - new Date(iso).getTime();
  const mins  = Math.floor(diff / 60_000);
  const hours = Math.floor(diff / 3_600_000);
  const days  = Math.floor(diff / 86_400_000);
  if (mins < 1)   return 'just now';
  if (mins < 60)  return `${mins}m ago`;
  if (hours < 24) return `${hours}h ago`;
  return `${days}d ago`;
}

function HealthDot({ status }: { status: WorkspaceSource['sync_status'] }) {
  const map: Record<WorkspaceSource['sync_status'], { color: string; shadow?: string; title: string }> = {
    ok:      { color: 'var(--success)', shadow: '0 0 0 3px rgba(27,123,69,0.14)', title: 'OK' },
    syncing: { color: 'var(--accent-primary)', title: 'Syncing' },
    error:   { color: 'var(--danger)', shadow: '0 0 0 3px rgba(224,119,100,0.14)', title: 'Error' },
    pending: { color: 'var(--text-muted)', title: 'Pending' },
  };
  const m = map[status];
  return (
    <span
      title={m.title}
      style={{
        display: 'inline-block',
        width: 8, height: 8, borderRadius: '50%', flexShrink: 0,
        background: m.color,
        boxShadow: m.shadow ?? 'none',
        animation: status === 'syncing' ? 'pulse 1.6s ease-in-out infinite' : 'none',
      }}
    />
  );
}

function KindBadge({ kind }: { kind: string }) {
  return (
    <span style={{
      fontSize: 10, fontWeight: 600, letterSpacing: '0.06em',
      textTransform: 'uppercase',
      padding: '2px 7px', borderRadius: 3,
      background: 'var(--bg-surface)', color: 'var(--text-secondary)',
      border: '1px solid var(--border-default)',
      flexShrink: 0,
    }}>
      {kind}
    </span>
  );
}

// ── Main component ────────────────────────────────────────────────────────────

export default function Sources() {
  const workspaceId = useWorkspaceStore((s) => s.workspaceId);

  const [sources, setSources]   = useState<WorkspaceSource[]>([]);
  const [loading, setLoading]   = useState(true);
  const [error, setError]       = useState<string | null>(null);
  const [syncing, setSyncing]   = useState<Set<string>>(new Set());
  const [syncErr, setSyncErr]   = useState<Record<string, string>>({});

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    getSources(workspaceId)
      .then((data) => { if (!cancelled) { setSources(data); setLoading(false); } })
      .catch((err: unknown) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : 'Failed to load sources');
          setLoading(false);
        }
      });
    return () => { cancelled = true; };
  }, [workspaceId]);

  async function handleSync(sourceId: string) {
    setSyncing((prev) => new Set(prev).add(sourceId));
    setSyncErr((prev) => { const n = { ...prev }; delete n[sourceId]; return n; });
    try {
      await triggerSync(workspaceId, sourceId);
      // Optimistically mark as syncing in local state
      setSources((prev) => prev.map((s) =>
        s.id === sourceId ? { ...s, sync_status: 'syncing' } : s
      ));
    } catch (err) {
      setSyncErr((prev) => ({
        ...prev,
        [sourceId]: err instanceof Error ? err.message : 'Sync failed',
      }));
    } finally {
      setSyncing((prev) => { const n = new Set(prev); n.delete(sourceId); return n; });
    }
  }

  return (
    <div className="app">
      <Sidebar />
      <main className="main">
        <TopBar crumb="Sources" />
        <div className="qview">
          <div className="va-content">

            <div style={{ display: 'flex', alignItems: 'center', marginBottom: 16 }}>
              <h2 style={{ margin: 0, fontSize: 14, fontWeight: 600, color: 'var(--text-primary)', flex: 1 }}>
                Indexed sources
                {!loading && sources.length > 0 && (
                  <span style={{ marginLeft: 8, fontSize: 12, fontWeight: 400, color: 'var(--text-tertiary)' }}>
                    · {sources.length} source{sources.length !== 1 ? 's' : ''}
                  </span>
                )}
              </h2>
            </div>

            {/* Loading */}
            {loading && (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                {[...Array(4)].map((_, i) => (
                  <div key={i} style={{
                    height: 68, borderRadius: 8,
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
                <strong>Failed to load sources</strong> — {error}
              </div>
            )}

            {/* Empty state */}
            {!loading && !error && sources.length === 0 && (
              <div style={{
                textAlign: 'center', padding: '80px 0',
                color: 'var(--text-tertiary)', fontSize: 14,
              }}>
                <div style={{ fontSize: 16, color: 'var(--text-secondary)', marginBottom: 8, fontWeight: 500 }}>
                  No sources indexed yet.
                </div>
                <div>Run the pipeline to populate.</div>
              </div>
            )}

            {/* Source list */}
            {!loading && !error && sources.length > 0 && (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                {sources.map((s) => (
                  <div key={s.id}>
                    <div style={{
                      display: 'flex', alignItems: 'center', gap: 14,
                      padding: '14px 18px',
                      background: 'var(--warm-surface)',
                      border: `1px solid ${s.sync_status === 'error' ? 'var(--danger-border)' : 'var(--warm-line)'}`,
                      borderRadius: 8,
                    }}>
                      {/* Icon */}
                      <div style={{
                        width: 36, height: 36, borderRadius: 8, flexShrink: 0,
                        background: 'var(--bg-surface)',
                        display: 'grid', placeItems: 'center',
                        fontSize: 11, fontWeight: 700,
                        fontFamily: 'var(--font-mono)',
                        color: 'var(--text-secondary)',
                      }}>
                        {s.id.toUpperCase().slice(0, 2)}
                      </div>

                      {/* Name + meta */}
                      <div style={{ flex: 1, minWidth: 0 }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                          <span style={{ fontSize: 14, fontWeight: 600, color: 'var(--text-primary)' }}>
                            {s.display_name}
                          </span>
                          <KindBadge kind={s.kind} />
                        </div>
                        <div style={{ fontSize: 11, color: 'var(--text-tertiary)', marginTop: 2, fontVariantNumeric: 'tabular-nums' }}>
                          {s.url && <span style={{ marginRight: 8 }}>{s.url}</span>}
                          Last synced: {relativeTime(s.last_synced_at)}
                        </div>
                      </div>

                      {/* Health dot */}
                      <HealthDot status={s.sync_status} />

                      {/* Sync button */}
                      <button
                        onClick={() => handleSync(s.id)}
                        disabled={syncing.has(s.id) || s.sync_status === 'syncing'}
                        style={{
                          height: 28, padding: '0 12px',
                          background: 'transparent',
                          border: '1px solid var(--border-default)',
                          borderRadius: 4, fontSize: 12, fontWeight: 500,
                          color: 'var(--text-secondary)',
                          cursor: (syncing.has(s.id) || s.sync_status === 'syncing') ? 'not-allowed' : 'pointer',
                          opacity: (syncing.has(s.id) || s.sync_status === 'syncing') ? 0.5 : 1,
                          flexShrink: 0,
                        }}
                      >
                        {syncing.has(s.id) || s.sync_status === 'syncing' ? 'Syncing…' : 'Sync'}
                      </button>
                    </div>

                    {/* Error message */}
                    {(s.sync_status === 'error' || syncErr[s.id]) && (
                      <div style={{
                        marginTop: 4, padding: '6px 14px',
                        fontSize: 12, color: 'var(--danger)',
                        background: 'var(--danger-soft)',
                        border: '1px solid var(--danger-border)',
                        borderRadius: '0 0 6px 6px',
                      }}>
                        {syncErr[s.id] ?? s.error_message ?? 'Sync error'}
                      </div>
                    )}
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
