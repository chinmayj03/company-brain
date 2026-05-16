import { useFlags, useFlagToggle } from '../data/feature_flags';

export default function LiveModeChip() {
  const f = useFlags();
  const { toggleAll } = useFlagToggle();
  const isLive = Object.values(f).every(Boolean);
  const isMock = !Object.values(f).some(Boolean);

  return (
    <button
      onClick={toggleAll}
      title={isLive ? 'Live mode — click to switch to mock' : 'Mock mode — click to switch to live'}
      style={{
        position: 'fixed', bottom: 12, right: 12, zIndex: 9998,
        display: 'flex', alignItems: 'center', gap: 6,
        padding: '4px 10px', borderRadius: 20,
        background: isLive ? 'var(--success-soft, #0F2B1E)' : isMock ? 'var(--bg-surface, #161B23)' : 'var(--bg-surface, #16213D)',
        border: `1px solid ${isLive ? 'var(--success-border, #1B5E40)' : 'var(--border-default, #2F394A)'}`,
        color: isLive ? 'var(--success, #4FB07D)' : 'var(--text-tertiary, #5B6678)',
        fontSize: 11, fontWeight: 600, cursor: 'pointer',
        fontFamily: 'var(--font-mono, monospace)',
        boxShadow: '0 2px 8px rgba(0,0,0,0.3)',
      }}
    >
      <span style={{
        width: 7, height: 7, borderRadius: '50%', flexShrink: 0,
        background: isLive ? 'var(--success, #4FB07D)' : 'var(--text-muted, #5B6678)',
        display: 'inline-block',
      }} />
      {isLive ? 'Live' : 'Mock'}
    </button>
  );
}
