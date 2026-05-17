import React from 'react';
import Sidebar from '../components/Sidebar';
import TopBar from '../components/TopBar';
import { useFlags, useFlagToggle, type FlagName } from '../data/feature_flags';
import { useWorkspaceStore } from '../store/workspace_store';

const FLAG_META: Array<{ key: FlagName; label: string }> = [
  { key: 'LIVE_QUERY',     label: 'Live queries'   },
  { key: 'LIVE_STREAM',    label: 'Streaming'      },
  { key: 'LIVE_HEALTH',    label: 'Health check'   },
  { key: 'LIVE_BLAST',     label: 'Blast radius'   },
  { key: 'LIVE_CITATIONS', label: 'Citations'      },
];

const sectionHeader: React.CSSProperties = {
  fontSize: 11, fontWeight: 700, textTransform: 'uppercase',
  letterSpacing: '0.08em', color: 'var(--text-muted)', marginBottom: 12,
};

const row: React.CSSProperties = {
  display: 'flex', alignItems: 'center', justifyContent: 'space-between',
  padding: '10px 0', borderBottom: '1px solid var(--border-default)',
};

const labelText: React.CSSProperties = {
  fontSize: 13, color: 'var(--text-primary)',
};

const metaText: React.CSSProperties = {
  fontSize: 12, color: 'var(--text-muted)', fontFamily: 'var(--font-mono)',
};

export default function Settings() {
  const flags      = useFlags();
  const { toggle, toggleAll } = useFlagToggle();
  const me          = useWorkspaceStore((s) => s.me);

  const allOn = Object.values(flags).every(Boolean);

  return (
    <div className="app">
      <Sidebar />
      <main className="main">
        <TopBar crumb="Settings" />
        <div className="qview">
          <div style={{ maxWidth: 560, margin: '0 auto', padding: '32px 24px' }}>

            {/* Section A — Mode toggles */}
            <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', marginBottom: 12 }}>
              <div>
                <div style={sectionHeader}>Mode</div>
                <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: -8, marginBottom: 12 }}>
                  All off = mock-safe demo.&nbsp; All on = live backend.
                </div>
              </div>
              <button
                onClick={toggleAll}
                style={{
                  fontSize: 11, fontWeight: 600, padding: '4px 12px',
                  borderRadius: 20, cursor: 'pointer', border: '1px solid var(--border-default)',
                  background: allOn ? 'var(--success)' : 'var(--bg-surface)',
                  color: allOn ? '#fff' : 'var(--text-secondary)',
                }}
              >
                {allOn ? 'All live' : 'Toggle all'}
              </button>
            </div>

            <div style={{ border: '1px solid var(--border-default)', borderRadius: 8, padding: '0 16px', marginBottom: 32 }}>
              {FLAG_META.map(({ key, label }) => (
                <div key={key} style={row}>
                  <span style={labelText}>{label}</span>
                  <button
                    onClick={() => toggle(key)}
                    role="switch"
                    aria-checked={flags[key]}
                    style={{
                      width: 36, height: 20, borderRadius: 10, border: 'none',
                      cursor: 'pointer', position: 'relative', flexShrink: 0,
                      background: flags[key] ? 'var(--success)' : 'var(--bg-surface)',
                      boxShadow: '0 0 0 1px var(--border-default)',
                      transition: 'background .15s',
                    }}
                  >
                    <span style={{
                      position: 'absolute', top: 2,
                      left: flags[key] ? 18 : 2,
                      width: 16, height: 16, borderRadius: '50%', background: '#fff',
                      transition: 'left .15s', display: 'block',
                    }} />
                  </button>
                </div>
              ))}
            </div>

            {/* Section B — Workspace info */}
            <div style={{ borderTop: '1px solid var(--border-default)', paddingTop: 24, marginBottom: 32 }}>
              <div style={sectionHeader}>Workspace</div>
              <div style={{ border: '1px solid var(--border-default)', borderRadius: 8, padding: '0 16px' }}>
                {[
                  { label: 'Name',  value: me?.workspace_name ?? '—' },
                  { label: 'Email', value: me?.email ?? '—'          },
                  { label: 'ID',    value: me?.id ?? '—'             },
                ].map(({ label, value }) => (
                  <div key={label} style={{ ...row, gap: 12 }}>
                    <span style={{ ...labelText, width: 80, flexShrink: 0 }}>{label}</span>
                    <span style={metaText}>{value}</span>
                  </div>
                ))}
              </div>
            </div>

            {/* Section C — Keyboard shortcut */}
            <div style={{ borderTop: '1px solid var(--border-default)', paddingTop: 24 }}>
              <div style={sectionHeader}>Keyboard shortcut</div>
              <div style={{ fontSize: 13, color: 'var(--text-secondary)' }}>
                <code style={{ fontFamily: 'var(--font-mono)', background: 'var(--bg-surface)', padding: '2px 6px', borderRadius: 4 }}>
                  ⌘ Shift L
                </code>
                {' '}(Mac) /{' '}
                <code style={{ fontFamily: 'var(--font-mono)', background: 'var(--bg-surface)', padding: '2px 6px', borderRadius: 4 }}>
                  Ctrl Shift L
                </code>
                {' '}(Win) — toggle all flags from anywhere.
              </div>
            </div>

          </div>
        </div>
      </main>
    </div>
  );
}
