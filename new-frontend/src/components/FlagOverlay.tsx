/**
 * FlagOverlay — floating HUD in the bottom-right corner.
 *
 * Shows current flag state and lets you toggle individual flags or flip all.
 * Visible only in dev mode (import.meta.env.DEV) so it never ships to prod.
 *
 * Magic key reminder: Ctrl+Shift+L = toggle ALL live flags at once.
 */
import { useState } from 'react';
import { useFlags, useFlagToggle, type FlagName } from '../data/feature_flags';

const FLAG_LABELS: Record<FlagName, string> = {
  LIVE_QUERY:     'Query  /query',
  LIVE_STREAM:    'Stream /query/stream',
  LIVE_HEALTH:    'Health /health',
  LIVE_BLAST:     'Blast  affected_entities',
  LIVE_CITATIONS: 'Cites  cited_entity_urns',
};

const FLAG_ORDER: FlagName[] = [
  'LIVE_QUERY', 'LIVE_STREAM', 'LIVE_HEALTH', 'LIVE_BLAST', 'LIVE_CITATIONS',
];

export default function FlagOverlay() {
  const f = useFlags();
  const { toggle, toggleAll } = useFlagToggle();
  const [open, setOpen] = useState(false);

  if (!import.meta.env.DEV) return null;

  const anyOn  = FLAG_ORDER.some(k => f[k]);
  const allOn  = FLAG_ORDER.every(k => f[k]);
  const allOff = !anyOn;

  return (
    <div style={{
      position: 'fixed', bottom: 16, right: 16, zIndex: 9999,
      display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 6,
      fontFamily: 'var(--font-mono, monospace)', fontSize: 11,
    }}>
      {open && (
        <div style={{
          background: 'var(--neutral-0, #0E1116)',
          border: '1px solid var(--neutral-300, #2F394A)',
          borderRadius: 8, padding: '10px 12px',
          boxShadow: '0 8px 24px rgba(0,0,0,0.5)',
          minWidth: 230,
        }}>
          <div style={{ color: 'var(--text-muted, #5B6678)', marginBottom: 8, fontSize: 10 }}>
            BRAIN FLAGS · ⌘+Shift+L = flip all
          </div>

          {FLAG_ORDER.map(k => (
            <div key={k} style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
              <button
                onClick={() => toggle(k)}
                style={{
                  width: 28, height: 16, borderRadius: 8, border: 'none', cursor: 'pointer',
                  background: f[k] ? '#4FB07D' : '#2F394A',
                  position: 'relative', transition: 'background 0.15s',
                  flexShrink: 0,
                }}
                title={`Toggle ${k}`}
              >
                <span style={{
                  position: 'absolute', top: 2, left: f[k] ? 14 : 2,
                  width: 12, height: 12, borderRadius: '50%',
                  background: '#fff', transition: 'left 0.15s',
                }} />
              </button>
              <span style={{ color: f[k] ? '#4FB07D' : 'var(--text-muted, #5B6678)', letterSpacing: 0 }}>
                {FLAG_LABELS[k]}
              </span>
            </div>
          ))}

          <div style={{ borderTop: '1px solid var(--neutral-300, #2F394A)', marginTop: 8, paddingTop: 8, display: 'flex', gap: 6 }}>
            <button
              onClick={toggleAll}
              style={{
                flex: 1, padding: '4px 0', borderRadius: 4, border: 'none', cursor: 'pointer',
                background: allOn ? '#2F394A' : '#4FB07D',
                color: allOn ? '#5B6678' : '#fff', fontSize: 11, fontFamily: 'inherit',
              }}
            >
              {allOn ? 'All → Mock' : 'All → Live'}
            </button>
            <a
              href={`${window.location.pathname}?demo=${allOff ? 'live' : 'mock'}`}
              style={{
                flex: 1, padding: '4px 0', borderRadius: 4, border: '1px solid #2F394A',
                color: '#7A8497', fontSize: 11, textDecoration: 'none',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
              }}
              title="Share demo URL with flags baked in"
            >
              Copy ?demo= link
            </a>
          </div>
        </div>
      )}

      {/* Pill toggle */}
      <button
        onClick={() => setOpen(o => !o)}
        style={{
          display: 'flex', alignItems: 'center', gap: 6,
          padding: '5px 10px', borderRadius: 20,
          background: allOff ? '#161B23' : allOn ? '#0F2B1E' : '#16213D',
          border: `1px solid ${allOff ? '#2F394A' : allOn ? '#1B5E40' : '#25407A'}`,
          color:  allOff ? '#5B6678' : allOn ? '#4FB07D' : '#6F94F1',
          cursor: 'pointer', fontSize: 11, fontFamily: 'inherit',
          boxShadow: '0 2px 8px rgba(0,0,0,0.4)',
        }}
        title="Toggle brain feature flags (⌘+Shift+L)"
      >
        <span style={{ width: 7, height: 7, borderRadius: '50%', background: allOff ? '#5B6678' : allOn ? '#4FB07D' : '#6F94F1', flexShrink: 0 }} />
        {allOff ? 'mock' : allOn ? 'live' : 'partial'}
        {' · brain'}
      </button>
    </div>
  );
}
