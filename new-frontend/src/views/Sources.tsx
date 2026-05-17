import { useState, useEffect, useRef } from 'react';
import Sidebar from '../components/Sidebar';
import TopBar from '../components/TopBar';
import AddSourceModal from '../components/AddSourceModal';
import { getSources, triggerSync, getJobStatus, type WorkspaceSource } from '../data/brain_client';
import { useWorkspaceStore } from '../store/workspace_store';
import { sourceKindLabel } from '../utils/sourceKind';

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
  const colors: Record<WorkspaceSource['sync_status'], string> = {
    ok:      'var(--success)',
    syncing: 'var(--accent-primary)',
    error:   'var(--danger)',
    pending: 'var(--text-muted)',
  };
  return (
    <span title={status} style={{
      display: 'inline-block', width: 8, height: 8, borderRadius: '50%', flexShrink: 0,
      background: colors[status],
      animation: status === 'syncing' ? 'pulse 1.6s ease-in-out infinite' : 'none',
    }} />
  );
}

// ── Main component ────────────────────────────────────────────────────────────

export default function Sources() {
  const workspaceId = useWorkspaceStore((s) => s.workspaceId);

  const [sources, setSources] = useState<WorkspaceSource[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError]     = useState<string | null>(null);
  const [syncing, setSyncing] = useState<Set<string>>(new Set());
  const [syncErr, setSyncErr] = useState<Record<string, string>>({});
  const [syncStage, setSyncStage] = useState<Record<string, string>>({});
  const [modalOpen, setModalOpen] = useState(false);
  const pollRefs = useRef<Record<string, ReturnType<typeof setInterval>>>({});

  function fetchSources() {
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
  }

  useEffect(fetchSources, [workspaceId]);

  function stopPoll(sourceId: string) {
    if (pollRefs.current[sourceId]) {
      clearInterval(pollRefs.current[sourceId]);
      delete pollRefs.current[sourceId];
    }
  }

  async function handleSync(sourceId: string) {
    setSyncing((prev) => new Set(prev).add(sourceId));
    setSyncErr((prev) => { const n = { ...prev }; delete n[sourceId]; return n; });
    setSyncStage((prev) => { const n = { ...prev }; delete n[sourceId]; return n; });
    try {
      const resp = await triggerSync(workspaceId, sourceId);
      setSources((prev) => prev.map((s) =>
        s.id === sourceId ? { ...s, sync_status: 'syncing' } : s
      ));
      if (resp.job_id) {
        stopPoll(sourceId);
        pollRefs.current[sourceId] = setInterval(async () => {
          try {
            const job = await getJobStatus(resp.job_id!);
            setSyncStage((prev) => ({ ...prev, [sourceId]: job.progress?.current_stage ?? '' }));
            if (job.status === 'completed') {
              stopPoll(sourceId);
              setSyncing((prev) => { const n = new Set(prev); n.delete(sourceId); return n; });
              setSyncStage((prev) => { const n = { ...prev }; delete n[sourceId]; return n; });
              setSources((prev) => prev.map((s) =>
                s.id === sourceId
                  ? { ...s, sync_status: 'ok', entity_count: job.result?.entity_count ?? s.entity_count }
                  : s
              ));
            } else if (job.status === 'failed') {
              stopPoll(sourceId);
              setSyncing((prev) => { const n = new Set(prev); n.delete(sourceId); return n; });
              setSyncStage((prev) => { const n = { ...prev }; delete n[sourceId]; return n; });
              setSources((prev) => prev.map((s) =>
                s.id === sourceId ? { ...s, sync_status: 'error', error_message: job.error } : s
              ));
              setSyncErr((prev) => ({ ...prev, [sourceId]: job.error ?? 'Sync failed' }));
            }
          } catch { /* transient */ }
        }, 1500);
      } else {
        setSyncing((prev) => { const n = new Set(prev); n.delete(sourceId); return n; });
      }
    } catch (err) {
      setSyncErr((prev) => ({
        ...prev,
        [sourceId]: err instanceof Error ? err.message : 'Sync failed',
      }));
      setSyncing((prev) => { const n = new Set(prev); n.delete(sourceId); return n; });
    }
  }

  // Entity count — present on live backend rows once ADR-0074 migration runs
  function entityCount(s: WorkspaceSource): number | null {
    const v = (s as WorkspaceSource & { entity_count?: number }).entity_count;
    return typeof v === 'number' ? v : null;
  }

  return (
    <div className="app">
      <Sidebar />
      <main className="main">
        <TopBar crumb="Sources" />
        <div className="qview">
          <div className="va-content">

            {/* Header + Add button */}
            <div style={{ display: 'flex', alignItems: 'center', marginBottom: 20 }}>
              <h2 style={{ margin: 0, fontSize: 14, fontWeight: 600, color: 'var(--text-primary)', flex: 1 }}>
                Sources
                {!loading && sources.length > 0 && (
                  <span style={{ marginLeft: 8, fontSize: 12, fontWeight: 400, color: 'var(--text-tertiary)' }}>
                    · {sources.length}
                  </span>
                )}
              </h2>
              <button
                onClick={() => setModalOpen(true)}
                style={{
                  background: 'var(--accent-primary)', color: '#fff', border: 'none',
                  borderRadius: 6, padding: '7px 14px', fontSize: 13, cursor: 'pointer',
                }}
              >
                + Add source
              </button>
            </div>

            {/* Loading skeletons */}
            {loading && (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                {[...Array(3)].map((_, i) => (
                  <div key={i} style={{
                    height: 64, borderRadius: 8, background: 'var(--bg-surface)',
                    animation: 'pulse 1.6s ease-in-out infinite',
                    opacity: 0.6 - i * 0.12,
                  }} />
                ))}
              </div>
            )}

            {/* Error */}
            {!loading && error && (
              <div style={{
                padding: 20, borderRadius: 8, fontSize: 13,
                background: 'var(--danger-soft)', border: '1px solid var(--danger-border)',
                color: 'var(--danger)',
              }}>
                <strong>Failed to load sources</strong> — {error}
              </div>
            )}

            {/* Onboarding empty state */}
            {!loading && !error && sources.length === 0 && (
              <div style={{
                border: '1px dashed var(--border-default)',
                borderRadius: 10, padding: 40, textAlign: 'center',
              }}>
                <div style={{ fontSize: 16, fontWeight: 600, color: 'var(--text-primary)', marginBottom: 10 }}>
                  Connect your first source
                </div>
                <div style={{ fontSize: 13, color: 'var(--text-muted)', marginBottom: 24, maxWidth: 380, margin: '0 auto 24px' }}>
                  Company Brain indexes git repos, OpenAPI specs, Confluence spaces,
                  DB migrations, and more.
                </div>
                <button
                  onClick={() => setModalOpen(true)}
                  style={{
                    background: 'var(--accent-primary)', color: '#fff', border: 'none',
                    borderRadius: 6, padding: '9px 22px', fontSize: 14, fontWeight: 600,
                    cursor: 'pointer',
                  }}
                >
                  Add source →
                </button>
              </div>
            )}

            {/* Source list */}
            {!loading && !error && sources.length > 0 && (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                {sources.map((s) => {
                  const count = entityCount(s);
                  return (
                    <div key={s.id}>
                      <div style={{
                        display: 'flex', alignItems: 'center', gap: 14,
                        padding: '14px 18px', borderRadius: 8,
                        background: 'var(--warm-surface)',
                        border: `1px solid ${s.sync_status === 'error' ? 'var(--danger-border)' : 'var(--warm-line)'}`,
                      }}>
                        {/* Kind icon */}
                        <div style={{
                          width: 36, height: 36, borderRadius: 8, flexShrink: 0,
                          background: 'var(--bg-surface)',
                          display: 'grid', placeItems: 'center',
                          fontSize: 10, fontWeight: 700, fontFamily: 'var(--font-mono)',
                          color: 'var(--text-secondary)',
                        }}>
                          {sourceKindLabel(s.kind)}
                        </div>

                        {/* Name + meta */}
                        <div style={{ flex: 1, minWidth: 0 }}>
                          <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                            <span style={{ fontSize: 14, fontWeight: 600, color: 'var(--text-primary)' }}>
                              {s.display_name}
                            </span>
                            <span style={{
                              fontSize: 10, fontWeight: 600, letterSpacing: '0.06em',
                              textTransform: 'uppercase', padding: '2px 6px', borderRadius: 3,
                              background: 'var(--bg-surface)', color: 'var(--text-muted)',
                              border: '1px solid var(--border-default)',
                            }}>
                              {s.kind}
                            </span>
                            {count !== null && count > 0 && (
                              <span style={{
                                fontSize: 11, color: 'var(--text-muted)',
                                background: 'var(--bg-surface)',
                                border: '1px solid var(--border-default)',
                                padding: '1px 6px', borderRadius: 3,
                                fontVariantNumeric: 'tabular-nums',
                              }}>
                                {count.toLocaleString()} entities
                              </span>
                            )}
                          </div>
                          <div style={{ fontSize: 11, color: 'var(--text-tertiary)', marginTop: 3 }}>
                            {s.url && <span style={{ marginRight: 10 }}>{s.url}</span>}
                            synced {relativeTime(s.last_synced_at)}
                          </div>
                        </div>

                        <HealthDot status={s.sync_status} />

                        {(syncing.has(s.id) || s.sync_status === 'syncing') && syncStage[s.id] && (
                          <span style={{
                            fontSize: 11, color: 'var(--text-muted)',
                            fontFamily: 'var(--font-mono)', flexShrink: 0,
                          }}>
                            {syncStage[s.id]}
                          </span>
                        )}

                        <button
                          onClick={() => handleSync(s.id)}
                          disabled={syncing.has(s.id) || s.sync_status === 'syncing'}
                          style={{
                            height: 28, padding: '0 12px', borderRadius: 4, fontSize: 12,
                            fontWeight: 500, flexShrink: 0,
                            color: s.sync_status === 'error' && !syncing.has(s.id) ? 'var(--danger)' : 'var(--text-secondary)',
                            background: 'transparent',
                            border: `1px solid ${s.sync_status === 'error' && !syncing.has(s.id) ? 'var(--danger-border)' : 'var(--border-default)'}`,
                            cursor: syncing.has(s.id) || s.sync_status === 'syncing' ? 'not-allowed' : 'pointer',
                            opacity: syncing.has(s.id) || s.sync_status === 'syncing' ? 0.5 : 1,
                          }}
                        >
                          {syncing.has(s.id) || s.sync_status === 'syncing'
                            ? 'Syncing…'
                            : s.sync_status === 'error' ? 'Retry' : 'Sync'}
                        </button>
                      </div>

                      {(s.sync_status === 'error' || syncErr[s.id]) && (
                        <div style={{
                          marginTop: 2, padding: '6px 14px', fontSize: 12,
                          color: 'var(--danger)', background: 'var(--danger-soft)',
                          border: '1px solid var(--danger-border)',
                          borderRadius: '0 0 6px 6px',
                        }}>
                          {syncErr[s.id] ?? s.error_message ?? 'Sync error'}
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            )}

          </div>
        </div>
      </main>

      <AddSourceModal
        open={modalOpen}
        workspaceId={workspaceId}
        onClose={() => setModalOpen(false)}
        onDone={() => {
          setModalOpen(false);
          getSources(workspaceId).then(setSources).catch(() => {});
        }}
      />
    </div>
  );
}
