import { useState, useEffect, useRef } from 'react';
import { registerSource, triggerSync, getJobStatus } from '../data/brain_client';
import { sourceKindLabel, KIND_AVAILABLE } from '../utils/sourceKind';

// ── Types ─────────────────────────────────────────────────────────────────────

interface Props {
  open: boolean;
  workspaceId: string;
  onClose: () => void;
  onDone: (sourceId: string) => void;
}

// ── Kind catalogue ────────────────────────────────────────────────────────────

const KINDS = [
  'git_local', 'git_remote', 'openapi', 'confluence',
  'db_migrations', 'github_prs', 'slack_channel', 'notion',
] as const;

const KIND_DISPLAY: Record<string, string> = {
  git_local:     'Git Repo (local)',
  git_remote:    'Git Repo (remote)',
  openapi:       'OpenAPI Spec',
  confluence:    'Confluence',
  db_migrations: 'DB Migrations',
  github_prs:    'GitHub PRs',
  slack_channel: 'Slack Channel',
  notion:        'Notion',
};

// ── Config field definitions per kind ────────────────────────────────────────

type FieldDef = { key: string; label: string; placeholder: string; type?: string };

const FIELDS: Record<string, FieldDef[]> = {
  git_local:     [{ key: 'repo_path', label: 'Repo path', placeholder: '/path/to/repo' }],
  git_remote:    [
    { key: 'repo_url', label: 'Repository URL', placeholder: 'https://github.com/org/repo.git' },
    { key: 'branch',   label: 'Branch',          placeholder: 'main' },
    { key: 'pat',      label: 'Personal access token', placeholder: 'ghp_…', type: 'password' },
  ],
  openapi:       [{ key: 'spec_url', label: 'OpenAPI spec URL or path', placeholder: 'https://…/openapi.yaml' }],
  confluence:    [
    { key: 'base_url',  label: 'Base URL',    placeholder: 'https://acme.atlassian.net' },
    { key: 'space_key', label: 'Space key',   placeholder: 'ENG' },
    { key: 'api_token', label: 'API token',   placeholder: '…', type: 'password' },
  ],
  db_migrations: [{ key: 'migrations_dir', label: 'Migrations directory', placeholder: '/path/to/db/migrations' }],
  github_prs:    [
    { key: 'repo_slug', label: 'Repository (org/repo)', placeholder: 'acme/payments' },
    { key: 'pat',       label: 'Personal access token',  placeholder: 'ghp_…', type: 'password' },
  ],
};

// ── Shared styles ─────────────────────────────────────────────────────────────

const inputStyle: React.CSSProperties = {
  width: '100%', height: 34, padding: '0 10px', boxSizing: 'border-box',
  background: 'var(--bg-surface)', border: '1px solid var(--border-default)',
  borderRadius: 6, color: 'var(--text-primary)', fontSize: 13,
  fontFamily: 'var(--font-mono)', outline: 'none',
};

const labelStyle: React.CSSProperties = {
  display: 'block', fontSize: 11, fontWeight: 600, textTransform: 'uppercase',
  letterSpacing: '0.06em', color: 'var(--text-muted)', marginBottom: 4, marginTop: 14,
};

const primaryBtn: React.CSSProperties = {
  height: 32, padding: '0 16px', borderRadius: 6, border: 'none',
  background: 'var(--accent-primary)', color: '#fff',
  fontSize: 13, fontWeight: 600, cursor: 'pointer',
};

const secondaryBtn: React.CSSProperties = {
  height: 32, padding: '0 14px', borderRadius: 6,
  background: 'transparent', color: 'var(--text-secondary)',
  border: '1px solid var(--border-default)', fontSize: 13, cursor: 'pointer',
};

// ── Step dots ─────────────────────────────────────────────────────────────────

function StepDots({ step }: { step: 1 | 2 | 3 }) {
  return (
    <div style={{ display: 'flex', gap: 6, justifyContent: 'center', marginBottom: 20 }}>
      {([1, 2, 3] as const).map((n) => (
        <div key={n} style={{
          width: 7, height: 7, borderRadius: '50%',
          background: n <= step ? 'var(--accent-primary)' : 'var(--border-default)',
        }} />
      ))}
    </div>
  );
}

// ── Indeterminate progress bar ────────────────────────────────────────────────

function IndeterminateBar({ label }: { label: string }) {
  return (
    <div style={{ width: '100%', marginTop: 16 }}>
      <div style={{
        height: 4, borderRadius: 2, background: 'var(--border-default)', overflow: 'hidden',
      }}>
        <div style={{
          height: '100%', width: '40%', borderRadius: 2,
          background: 'var(--accent-primary)',
          animation: 'progress-slide 1.4s ease-in-out infinite',
        }} />
      </div>
      {label && (
        <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 8, fontFamily: 'var(--font-mono)' }}>
          {label}
        </div>
      )}
      <style>{`
        @keyframes progress-slide {
          0%   { transform: translateX(-100%); }
          100% { transform: translateX(350%); }
        }
      `}</style>
    </div>
  );
}

// ── Modal ─────────────────────────────────────────────────────────────────────

export default function AddSourceModal({ open, workspaceId, onClose, onDone }: Props) {
  const [step, setStep]           = useState<1 | 2 | 3>(1);
  const [kind, setKind]           = useState('git_local');
  const [displayName, setDisplayName] = useState('');
  const [config, setConfig]       = useState<Record<string, string>>({});
  const [autoIndex, setAutoIndex] = useState(true);
  const [stage, setStage]         = useState('');
  const [entityCount, setEntityCount] = useState(0);
  const [done, setDone]           = useState(false);
  const [failed, setFailed]       = useState<string | null>(null);
  const [registeredId, setRegisteredId] = useState('');
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    if (!open) {
      // reset when closed
      setStep(1); setKind('git_local'); setDisplayName(''); setConfig({});
      setAutoIndex(true); setStage(''); setEntityCount(0); setDone(false);
      setFailed(null); setRegisteredId('');
      if (pollRef.current) clearInterval(pollRef.current);
    }
  }, [open]);

  if (!open) return null;

  function setField(key: string, val: string) {
    setConfig((prev) => ({ ...prev, [key]: val }));
  }

  function startPolling(jobId: string) {
    if (pollRef.current) clearInterval(pollRef.current);
    pollRef.current = setInterval(async () => {
      try {
        const job = await getJobStatus(jobId);
        setStage(job.progress?.current_stage ?? '');
        if (job.status === 'completed') {
          clearInterval(pollRef.current!);
          setEntityCount(job.result?.entity_count ?? 0);
          setDone(true);
        } else if (job.status === 'failed') {
          clearInterval(pollRef.current!);
          setFailed(job.error ?? 'Indexing failed');
        }
      } catch { /* transient */ }
    }, 1500);
  }

  async function handleSubmit() {
    setFailed(null);
    try {
      const resp = await registerSource(workspaceId, {
        kind,
        display_name: displayName,
        config,
        auto_index: autoIndex,
      });
      setRegisteredId(resp.source.id);
      setStep(3);

      if (autoIndex && resp.job_id) {
        startPolling(resp.job_id);
      } else {
        setDone(true);
      }
    } catch (e) {
      setFailed(e instanceof Error ? e.message : 'Failed to add source');
    }
  }

  async function handleRetry() {
    if (!registeredId) return;
    setFailed(null);
    setStage('');
    setDone(false);
    try {
      const resp = await triggerSync(workspaceId, registeredId);
      if (resp.job_id) {
        startPolling(resp.job_id);
      } else {
        setDone(true);
      }
    } catch (e) {
      setFailed(e instanceof Error ? e.message : 'Retry failed');
    }
  }

  const fields = FIELDS[kind] ?? [
    { key: 'config_json', label: 'Config JSON', placeholder: '{"key":"value"}' },
  ];

  const canSubmit = displayName.trim().length > 0 &&
    fields.every((f) => f.key === 'branch' || (config[f.key] ?? '').trim().length > 0);

  return (
    <div
      style={{
        position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.55)',
        zIndex: 100, display: 'flex', alignItems: 'center', justifyContent: 'center',
      }}
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div style={{
        background: 'var(--warm-surface)', borderRadius: 12,
        width: 480, maxWidth: '94vw', padding: 28,
        border: '1px solid var(--border-default)',
        boxShadow: '0 8px 32px rgba(0,0,0,0.32)',
      }}>
        {/* Header */}
        <div style={{ display: 'flex', alignItems: 'center', marginBottom: 16 }}>
          <div style={{ flex: 1, fontSize: 15, fontWeight: 700, color: 'var(--text-primary)' }}>
            {step === 1 ? 'Choose source type' : step === 2 ? 'Configure source' : 'Indexing'}
          </div>
          <button onClick={onClose} style={{
            background: 'none', border: 'none', cursor: 'pointer',
            fontSize: 20, color: 'var(--text-muted)', lineHeight: 1, padding: '0 4px',
          }}>×</button>
        </div>

        <StepDots step={step} />

        {/* ── Step 1 ── */}
        {step === 1 && (
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
            {KINDS.map((k) => {
              const available = KIND_AVAILABLE[k] ?? false;
              return (
                <div
                  key={k}
                  onClick={() => { if (available) { setKind(k); setConfig({}); setStep(2); } }}
                  style={{
                    padding: '14px 12px', borderRadius: 8, cursor: available ? 'pointer' : 'not-allowed',
                    border: `1px solid ${kind === k ? 'var(--accent-primary)' : 'var(--border-default)'}`,
                    background: kind === k ? 'var(--accent-soft)' : 'var(--bg-surface)',
                    opacity: available ? 1 : 0.45,
                    pointerEvents: available ? 'auto' : 'none',
                    transition: 'border-color .12s, background .12s',
                  }}
                >
                  <div style={{
                    fontSize: 10, fontWeight: 700, fontFamily: 'var(--font-mono)',
                    color: 'var(--text-muted)', marginBottom: 4,
                  }}>
                    {sourceKindLabel(k)}
                  </div>
                  <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-primary)' }}>
                    {KIND_DISPLAY[k]}
                  </div>
                  {!available && (
                    <div style={{
                      marginTop: 4, fontSize: 10, color: 'var(--text-muted)',
                      background: 'var(--bg-surface)', display: 'inline-block',
                      padding: '1px 6px', borderRadius: 3,
                      border: '1px solid var(--border-default)',
                    }}>
                      coming soon
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}

        {/* ── Step 2 ── */}
        {step === 2 && (
          <div>
            <label style={labelStyle}>Display name</label>
            <input
              style={inputStyle}
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              placeholder="e.g. payments-service"
              autoFocus
            />

            {fields.map((f) => (
              <div key={f.key}>
                <label style={labelStyle}>{f.label}</label>
                {f.key === 'config_json' ? (
                  <textarea
                    style={{ ...inputStyle, height: 80, resize: 'vertical', paddingTop: 8 }}
                    value={config[f.key] ?? ''}
                    onChange={(e) => setField(f.key, e.target.value)}
                    placeholder={f.placeholder}
                  />
                ) : (
                  <input
                    style={inputStyle}
                    type={f.type ?? 'text'}
                    value={config[f.key] ?? ''}
                    onChange={(e) => setField(f.key, e.target.value)}
                    placeholder={f.placeholder}
                  />
                )}
              </div>
            ))}

            <label style={{
              display: 'flex', alignItems: 'center', gap: 8,
              marginTop: 16, fontSize: 13, color: 'var(--text-secondary)', cursor: 'pointer',
            }}>
              <input
                type="checkbox"
                checked={autoIndex}
                onChange={(e) => setAutoIndex(e.target.checked)}
              />
              Index immediately after adding
            </label>

            {failed && (
              <div style={{
                marginTop: 12, padding: '8px 12px', borderRadius: 6, fontSize: 12,
                background: 'var(--danger-soft)', border: '1px solid var(--danger-border)',
                color: 'var(--danger)',
              }}>
                {failed}
              </div>
            )}

            <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', marginTop: 20 }}>
              <button onClick={() => setStep(1)} style={secondaryBtn}>← Back</button>
              <button
                onClick={handleSubmit}
                disabled={!canSubmit}
                style={{ ...primaryBtn, opacity: canSubmit ? 1 : 0.5, cursor: canSubmit ? 'pointer' : 'not-allowed' }}
              >
                Add {autoIndex ? '& Index' : 'source'}
              </button>
            </div>
          </div>
        )}

        {/* ── Step 3 ── */}
        {step === 3 && (
          <div style={{ padding: '8px 0', textAlign: 'center' }}>
            {!done && !failed && (
              <>
                <div style={{ fontSize: 14, fontWeight: 500, color: 'var(--text-primary)', marginBottom: 4 }}>
                  {autoIndex ? `Indexing ${displayName}…` : `Source "${displayName}" added.`}
                </div>
                {autoIndex && <IndeterminateBar label={stage || 'Starting…'} />}
              </>
            )}

            {done && (
              <>
                <div style={{ fontSize: 28, marginBottom: 10 }}>✅</div>
                <div style={{ fontSize: 15, fontWeight: 600, color: 'var(--text-primary)', marginBottom: 4 }}>
                  {autoIndex ? `Indexed ${entityCount.toLocaleString()} entities` : 'Source added'}
                </div>
                <div style={{ marginTop: 20 }}>
                  <button onClick={() => { onDone(registeredId); onClose(); }} style={primaryBtn}>
                    Done
                  </button>
                </div>
              </>
            )}

            {failed && (
              <>
                <div style={{ fontSize: 28, marginBottom: 10 }}>⚠️</div>
                <div style={{ fontSize: 14, fontWeight: 500, color: 'var(--text-primary)', marginBottom: 8 }}>
                  Source registered, but indexing failed.
                </div>
                <div style={{ fontSize: 12, color: 'var(--danger)', fontFamily: 'var(--font-mono)', marginBottom: 16 }}>
                  {failed}
                </div>
                <div style={{ display: 'flex', gap: 8, justifyContent: 'center' }}>
                  <button onClick={handleRetry} style={primaryBtn}>
                    Retry indexing
                  </button>
                  <button onClick={() => { onDone(registeredId); onClose(); }} style={secondaryBtn}>
                    Close
                  </button>
                </div>
              </>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
