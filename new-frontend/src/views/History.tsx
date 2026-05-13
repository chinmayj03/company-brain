import { useState, useEffect } from 'react';
import { Link } from 'react-router-dom';
import Sidebar from '../components/Sidebar';
import TopBar from '../components/TopBar';
import { getConversations, type ConversationSummary, type ConversationDetail } from '../data/brain_client';

// ── Helpers ───────────────────────────────────────────────────────────────────

function relativeTime(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const mins  = Math.floor(diff / 60_000);
  const hours = Math.floor(diff / 3_600_000);
  const days  = Math.floor(diff / 86_400_000);
  if (mins < 1)   return 'just now';
  if (mins < 60)  return `${mins} min ago`;
  if (hours < 24) return `${hours}h ago`;
  if (days === 1) return 'yesterday';
  return `${days} days ago`;
}

function truncate(s: string, max: number): string {
  return s.length > max ? s.slice(0, max) + '…' : s;
}

// ── ConversationRow (exported so Saved.tsx can reuse) ─────────────────────────

export interface ConversationRowProps {
  conv: ConversationSummary;
  isSelected: boolean;
  onClick: () => void;
}

export function ConversationRow({ conv, isSelected, onClick }: ConversationRowProps) {
  return (
    <div
      onClick={onClick}
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 12,
        padding: '10px 16px',
        borderRadius: 6,
        cursor: 'pointer',
        background: isSelected ? 'var(--accent-soft)' : 'transparent',
        border: `1px solid ${isSelected ? 'var(--accent-soft-border)' : 'transparent'}`,
        transition: 'background .12s',
      }}
      onMouseEnter={(e) => {
        if (!isSelected) (e.currentTarget as HTMLDivElement).style.background = 'var(--bg-hover)';
      }}
      onMouseLeave={(e) => {
        if (!isSelected) (e.currentTarget as HTMLDivElement).style.background = 'transparent';
      }}
    >
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{
          fontSize: 13,
          color: isSelected ? 'var(--accent-primary)' : 'var(--text-primary)',
          fontWeight: 500,
          whiteSpace: 'nowrap',
          overflow: 'hidden',
          textOverflow: 'ellipsis',
        }}>
          {truncate(conv.question, 80)}
        </div>
        <div style={{ fontSize: 11, color: 'var(--text-tertiary)', marginTop: 2, fontVariantNumeric: 'tabular-nums' }}>
          {relativeTime(conv.asked_at)}
          {conv.actor_kind && <span style={{ marginLeft: 8, opacity: 0.7 }}>{conv.actor_kind}</span>}
        </div>
      </div>
      {conv.saved && (
        <span style={{
          fontSize: 10,
          fontWeight: 600,
          letterSpacing: '0.05em',
          textTransform: 'uppercase',
          padding: '2px 6px',
          borderRadius: 3,
          background: 'var(--accent-soft)',
          color: 'var(--accent-primary)',
          border: '1px solid var(--accent-soft-border)',
          flexShrink: 0,
        }}>
          Saved
        </span>
      )}
    </div>
  );
}

// ── Skeleton placeholder ──────────────────────────────────────────────────────

function Skeleton() {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
      {[...Array(6)].map((_, i) => (
        <div key={i} style={{
          height: 52,
          borderRadius: 6,
          background: 'var(--bg-surface)',
          animation: 'pulse 1.6s ease-in-out infinite',
          opacity: 0.6 - i * 0.07,
        }} />
      ))}
    </div>
  );
}

// ── Detail panel ──────────────────────────────────────────────────────────────

function DetailPanel({ conv }: { conv: ConversationSummary | ConversationDetail }) {
  const detail = conv as ConversationDetail;
  const resp = detail.summary_json as { summary_md?: string; summary?: string } | undefined;
  const body = resp?.summary_md ?? resp?.summary ?? null;

  return (
    <div style={{
      background: 'var(--warm-surface)',
      border: '1px solid var(--warm-line)',
      borderRadius: 10,
      overflow: 'hidden',
    }}>
      <div style={{
        padding: '14px 20px',
        borderBottom: '1px solid var(--warm-line)',
        display: 'flex',
        alignItems: 'center',
        gap: 10,
      }}>
        <div style={{ flex: 1 }}>
          <div style={{ fontSize: 14, fontWeight: 500, color: 'var(--text-primary)' }}>
            {conv.question}
          </div>
          <div style={{ fontSize: 11, color: 'var(--text-tertiary)', marginTop: 3 }}>
            {new Date(conv.asked_at).toLocaleString()}
            {conv.actor_id && <span style={{ marginLeft: 8 }}>by {conv.actor_id}</span>}
          </div>
        </div>
        {conv.saved && (
          <span style={{
            fontSize: 10, fontWeight: 600, letterSpacing: '0.05em', textTransform: 'uppercase' as const,
            padding: '2px 6px', borderRadius: 3,
            background: 'var(--accent-soft)', color: 'var(--accent-primary)',
            border: '1px solid var(--accent-soft-border)',
          }}>Saved</span>
        )}
      </div>
      <div style={{ padding: '20px 24px' }}>
        {body ? (
          <p
            style={{ margin: 0, fontSize: 14, lineHeight: '22px', color: 'var(--text-secondary)' }}
            dangerouslySetInnerHTML={{ __html: body }}
          />
        ) : (
          <p style={{ margin: 0, fontSize: 13, color: 'var(--text-muted)', fontStyle: 'italic' }}>
            No answer stored for this query.
          </p>
        )}
      </div>
    </div>
  );
}

// ── Main History view ─────────────────────────────────────────────────────────

interface HistoryViewProps {
  savedOnly?: boolean;
  title?: string;
  emptyMessage?: string;
}

export function HistoryView({ savedOnly = false, title = 'History', emptyMessage }: HistoryViewProps) {
  const workspaceId = (window as unknown as { __WORKSPACE_ID__?: string }).__WORKSPACE_ID__ ?? 'default';

  const [convs, setConvs]       = useState<ConversationSummary[]>([]);
  const [loading, setLoading]   = useState(true);
  const [error, setError]       = useState<string | null>(null);
  const [selected, setSelected] = useState<ConversationSummary | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    getConversations(workspaceId, savedOnly ? true : undefined)
      .then((data) => { if (!cancelled) { setConvs(data); setLoading(false); } })
      .catch((err: unknown) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : 'Failed to load');
          setLoading(false);
        }
      });
    return () => { cancelled = true; };
  }, [workspaceId, savedOnly]);

  const empty = savedOnly
    ? (emptyMessage ?? 'No saved queries yet. Thumbs-up an answer to save it.')
    : (emptyMessage ?? 'No questions yet. Ask your first question →');

  return (
    <div className="app">
      <Sidebar />
      <main className="main">
        <TopBar crumb={title} />
        <div className="qview">
          <div className="va-content">
            {loading && <Skeleton />}

            {!loading && error && (
              <div style={{
                padding: 20, borderRadius: 8,
                background: 'var(--danger-soft)', border: '1px solid var(--danger-border)',
                color: 'var(--danger)', fontSize: 13,
              }}>
                <strong>Failed to load {title.toLowerCase()}</strong> — {error}
              </div>
            )}

            {!loading && !error && convs.length === 0 && (
              <div style={{
                textAlign: 'center', padding: '80px 0',
                color: 'var(--text-tertiary)', fontSize: 14,
              }}>
                <div style={{ fontSize: 16, color: 'var(--text-secondary)', marginBottom: 8, fontWeight: 500 }}>
                  {savedOnly ? 'No saved queries yet.' : 'No questions yet.'}
                </div>
                {savedOnly ? (
                  <div>{empty}</div>
                ) : (
                  <Link to="/ask" style={{ color: 'var(--accent-primary)', textDecoration: 'none' }}>
                    Ask your first question →
                  </Link>
                )}
              </div>
            )}

            {!loading && !error && convs.length > 0 && (
              <div style={{ display: 'grid', gridTemplateColumns: '340px 1fr', gap: 20, alignItems: 'start' }}>
                {/* Left: conversation list */}
                <div style={{
                  background: 'var(--warm-surface)',
                  border: '1px solid var(--warm-line)',
                  borderRadius: 10,
                  padding: '8px',
                  display: 'flex',
                  flexDirection: 'column',
                  gap: 1,
                  position: 'sticky',
                  top: 24,
                  maxHeight: 'calc(100vh - 120px)',
                  overflowY: 'auto',
                }}>
                  <div style={{
                    padding: '6px 8px 10px',
                    fontSize: 11, fontWeight: 600, letterSpacing: '0.06em',
                    textTransform: 'uppercase', color: 'var(--text-tertiary)',
                  }}>
                    {convs.length} {title.toLowerCase()} {convs.length === 1 ? 'entry' : 'entries'}
                  </div>
                  {convs.map((c) => (
                    <ConversationRow
                      key={c.id}
                      conv={c}
                      isSelected={selected?.id === c.id}
                      onClick={() => setSelected(c)}
                    />
                  ))}
                </div>

                {/* Right: detail panel */}
                <div>
                  {selected ? (
                    <DetailPanel conv={selected} />
                  ) : (
                    <div style={{
                      textAlign: 'center', padding: '60px 0',
                      color: 'var(--text-muted)', fontSize: 13,
                    }}>
                      Select a conversation to view details
                    </div>
                  )}
                </div>
              </div>
            )}
          </div>
        </div>
      </main>
    </div>
  );
}

export default function History() {
  return <HistoryView savedOnly={false} title="History" />;
}
