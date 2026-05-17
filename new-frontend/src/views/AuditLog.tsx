import { useState, useEffect } from 'react';
import Sidebar from '../components/Sidebar';
import TopBar from '../components/TopBar';
import { getConversations, type ConversationSummary } from '../data/brain_client';
import { useWorkspaceStore } from '../store/workspace_store';

// ── Helpers ───────────────────────────────────────────────────────────────────

function truncate(s: string, max: number): string {
  return s.length > max ? s.slice(0, max) + '…' : s;
}

function formatTimestamp(iso: string): string {
  return new Date(iso).toLocaleString(undefined, {
    year: 'numeric', month: 'short', day: 'numeric',
    hour: '2-digit', minute: '2-digit',
  });
}

function buildCsv(rows: ConversationSummary[]): string {
  const header = ['ID', 'Actor', 'Question', 'Asked At', 'Saved'];
  const escape = (v: string) => `"${v.replace(/"/g, '""')}"`;
  const lines = [
    header.join(','),
    ...rows.map((r) => [
      escape(r.id),
      escape(r.actor_id ?? r.actor_kind ?? '—'),
      escape(r.question),
      escape(r.asked_at),
      r.saved ? 'true' : 'false',
    ].join(',')),
  ];
  return lines.join('\n');
}

function downloadCsv(data: string, filename: string) {
  const blob = new Blob([data], { type: 'text/csv;charset=utf-8;' });
  const url  = URL.createObjectURL(blob);
  const a    = document.createElement('a');
  a.href     = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

const PAGE_SIZE = 20;

// ── Icons ─────────────────────────────────────────────────────────────────────

const IconDownload = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6"
    strokeLinecap="round" strokeLinejoin="round" style={{ width: 13, height: 13 }}>
    <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
    <polyline points="7 10 12 15 17 10"/>
    <line x1="12" y1="15" x2="12" y2="3"/>
  </svg>
);

// ── Main component ────────────────────────────────────────────────────────────

export default function AuditLog() {
  const workspaceId = useWorkspaceStore((s) => s.workspaceId);

  const [rows, setRows]       = useState<ConversationSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError]     = useState<string | null>(null);
  const [page, setPage]       = useState(0);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    getConversations(workspaceId)
      .then((data) => { if (!cancelled) { setRows(data); setLoading(false); } })
      .catch((err: unknown) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : 'Failed to load');
          setLoading(false);
        }
      });
    return () => { cancelled = true; };
  }, [workspaceId]);

  const totalPages = Math.ceil(rows.length / PAGE_SIZE);
  const pageRows   = rows.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);

  const thStyle: React.CSSProperties = {
    padding: '10px 14px', textAlign: 'left' as const,
    fontSize: 11, fontWeight: 600, letterSpacing: '0.06em',
    textTransform: 'uppercase' as const,
    color: 'var(--text-tertiary)',
    borderBottom: '1px solid var(--warm-line-2)',
    background: 'var(--bg-page)',
    whiteSpace: 'nowrap' as const,
  };
  const tdStyle: React.CSSProperties = {
    padding: '10px 14px',
    fontSize: 13, color: 'var(--text-secondary)',
    borderBottom: '1px solid var(--warm-line)',
    verticalAlign: 'middle' as const,
  };

  function handleExport() {
    const csv = buildCsv(rows);
    downloadCsv(csv, `audit-log-${new Date().toISOString().slice(0, 10)}.csv`);
  }

  return (
    <div className="app">
      <Sidebar />
      <main className="main">
        <TopBar crumb="Audit Log" />
        <div className="qview">
          <div className="va-content">

            {/* Header */}
            <div style={{ display: 'flex', alignItems: 'center', marginBottom: 16 }}>
              <h2 style={{ margin: 0, fontSize: 14, fontWeight: 600, color: 'var(--text-primary)', flex: 1 }}>
                Query audit log
                {!loading && rows.length > 0 && (
                  <span style={{ marginLeft: 8, fontSize: 12, fontWeight: 400, color: 'var(--text-tertiary)' }}>
                    · {rows.length} entries
                  </span>
                )}
              </h2>
              {!loading && rows.length > 0 && (
                <button
                  onClick={handleExport}
                  style={{
                    height: 30, padding: '0 12px',
                    display: 'inline-flex', alignItems: 'center', gap: 6,
                    background: 'transparent',
                    border: '1px solid var(--border-default)',
                    borderRadius: 4, fontSize: 12, fontWeight: 500,
                    color: 'var(--text-secondary)', cursor: 'pointer',
                  }}
                >
                  <IconDownload /> Export CSV
                </button>
              )}
            </div>

            {/* Loading */}
            {loading && (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 1 }}>
                {[...Array(8)].map((_, i) => (
                  <div key={i} style={{
                    height: 44, background: 'var(--bg-surface)',
                    animation: 'pulse 1.6s ease-in-out infinite',
                    opacity: 0.5 - i * 0.04,
                    borderRadius: i === 0 ? '8px 8px 0 0' : i === 7 ? '0 0 8px 8px' : 0,
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
                <strong>Failed to load audit log</strong> — {error}
              </div>
            )}

            {/* Empty state */}
            {!loading && !error && rows.length === 0 && (
              <div style={{
                textAlign: 'center', padding: '80px 0',
                color: 'var(--text-tertiary)', fontSize: 14,
              }}>
                <div style={{ fontSize: 16, color: 'var(--text-secondary)', marginBottom: 8, fontWeight: 500 }}>
                  No queries logged yet.
                </div>
              </div>
            )}

            {/* Table */}
            {!loading && !error && rows.length > 0 && (
              <>
                <div style={{
                  background: 'var(--warm-surface)',
                  border: '1px solid var(--warm-line)',
                  borderRadius: 10, overflow: 'hidden',
                }}>
                  <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                    <thead>
                      <tr>
                        <th style={thStyle}>Actor</th>
                        <th style={{ ...thStyle, width: '55%' }}>Question</th>
                        <th style={{ ...thStyle, textAlign: 'right' as const }}>Timestamp</th>
                      </tr>
                    </thead>
                    <tbody>
                      {pageRows.map((r) => (
                        <tr key={r.id} style={{ transition: 'background .1s' }}
                          onMouseEnter={(e) => (e.currentTarget.style.background = 'var(--bg-hover)')}
                          onMouseLeave={(e) => (e.currentTarget.style.background = 'transparent')}
                        >
                          <td style={tdStyle}>
                            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                              <div style={{
                                width: 26, height: 26, borderRadius: '50%', flexShrink: 0,
                                background: 'var(--bg-surface)',
                                display: 'grid', placeItems: 'center',
                                fontSize: 10, fontWeight: 600, color: 'var(--text-tertiary)',
                              }}>
                                {(r.actor_id ?? r.actor_kind ?? '?').slice(0, 2).toUpperCase()}
                              </div>
                              <span style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
                                {r.actor_id ?? r.actor_kind ?? '—'}
                              </span>
                            </div>
                          </td>
                          <td style={{ ...tdStyle, color: 'var(--text-primary)' }}>
                            {truncate(r.question, 80)}
                          </td>
                          <td style={{ ...tdStyle, textAlign: 'right' as const, whiteSpace: 'nowrap', fontVariantNumeric: 'tabular-nums', fontSize: 11 }}>
                            {formatTimestamp(r.asked_at)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>

                {/* Pagination */}
                {totalPages > 1 && (
                  <div style={{
                    display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                    marginTop: 12, fontSize: 12, color: 'var(--text-tertiary)',
                  }}>
                    <span>
                      Page {page + 1} of {totalPages} · {rows.length} total
                    </span>
                    <div style={{ display: 'flex', gap: 6 }}>
                      <button
                        onClick={() => setPage((p) => Math.max(0, p - 1))}
                        disabled={page === 0}
                        style={{
                          height: 28, padding: '0 12px', cursor: page === 0 ? 'not-allowed' : 'pointer',
                          background: 'transparent', border: '1px solid var(--border-default)',
                          borderRadius: 4, fontSize: 12, color: 'var(--text-secondary)',
                          opacity: page === 0 ? 0.4 : 1,
                        }}
                      >
                        ← Prev
                      </button>
                      <button
                        onClick={() => setPage((p) => Math.min(totalPages - 1, p + 1))}
                        disabled={page >= totalPages - 1}
                        style={{
                          height: 28, padding: '0 12px', cursor: page >= totalPages - 1 ? 'not-allowed' : 'pointer',
                          background: 'transparent', border: '1px solid var(--border-default)',
                          borderRadius: 4, fontSize: 12, color: 'var(--text-secondary)',
                          opacity: page >= totalPages - 1 ? 0.4 : 1,
                        }}
                      >
                        Next →
                      </button>
                    </div>
                  </div>
                )}
              </>
            )}
          </div>
        </div>
      </main>
    </div>
  );
}
