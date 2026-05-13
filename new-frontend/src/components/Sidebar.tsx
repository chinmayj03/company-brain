import { navMain, sources, recents } from '../data/mock_fallback';

// Inline SVG helpers — replaces data.js ICONS references
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

const dotStyle = (state: string) => ({
  display: 'inline-block', width: 7, height: 7, borderRadius: '50%', flexShrink: 0,
  background: state === 'ok' ? 'var(--success)' : 'var(--text-muted)',
});

export default function Sidebar() {
  return (
    <aside className="sb">
      <div className="sb__brand">
        <div className="sb__mark">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M4 20V12"/><path d="M9 20V8"/><path d="M14 20v-9"/><path d="M19 20V5"/>
          </svg>
        </div>
        <div className="col">
          <span className="name">Company Brain</span>
          <span className="ws">acme · payments</span>
        </div>
      </div>

      <div className="sb__section">
        {navMain.map((n) => (
          <a key={n.id} className="sb__item" data-active={n.active ? 'true' : undefined} href={`#${n.id}`}>
            <span style={{ flex: 1 }}>{n.label}</span>
            {n.count && <span className="count">{n.count}</span>}
          </a>
        ))}
      </div>

      <div className="sb__section">
        <div className="sb__label">Sources</div>
        {sources.map((s) => (
          <div className="src-row" key={s.id} data-soon={s.state === 'soon' ? 'true' : undefined}>
            <div className="ico" style={{ background: 'var(--bg-surface)', color: 'var(--text-secondary)' }}>
              <span style={{ fontSize: 10, fontWeight: 600 }}>{s.id.toUpperCase().slice(0, 2)}</span>
            </div>
            <div className="col">
              <div className="nm">{s.name}</div>
              <div className="meta">{s.meta}</div>
            </div>
            <span style={dotStyle(s.state)} title={s.state} />
          </div>
        ))}
      </div>

      <div className="sb__section">
        <div className="sb__label">Recent</div>
        {recents.map((r, i) => (
          <a key={i} className="sb__item" href="#ask" style={{ alignItems: 'flex-start' }}>
            <IconClock />
            <div style={{ display: 'flex', flexDirection: 'column', minWidth: 0, lineHeight: 1.35 }}>
              <span style={{ fontSize: 12, color: 'var(--text-secondary)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', maxWidth: 180 }}>
                {r.q}
              </span>
              <span style={{ fontSize: 10.5, color: 'var(--text-muted)', marginTop: 1 }}>{r.when}</span>
            </div>
          </a>
        ))}
      </div>

      <div className="sb__user">
        <div className="av">TB</div>
        <div className="col">
          <span className="nm">Tom Blomfield</span>
          <span className="role">Owner · acme</span>
        </div>
        <span style={{ color: 'var(--text-tertiary)', marginLeft: 'auto' }}><IconChevron /></span>
      </div>
    </aside>
  );
}
