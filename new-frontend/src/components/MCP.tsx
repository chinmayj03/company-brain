import { agents } from '../data/mock_fallback';

const IconPlug = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" style={{ width: 16, height: 16 }}>
    <path d="M9 2v6"/><path d="M15 2v6"/>
    <path d="M8 8h8a2 2 0 0 1 2 2v4a4 4 0 0 1-4 4h0a4 4 0 0 1-4-4v-4a2 2 0 0 1 2-2z"/>
    <path d="M12 18v4"/>
  </svg>
);

export default function MCP() {
  return (
    <div className="mcp">
      <div className="mcp-head">
        <div className="ico"><IconPlug /></div>
        <div className="col">
          <span className="nm">Brain as MCP — agents connected</span>
          <span className="sub">Any agent that speaks MCP can query the brain over your code, docs &amp; ADRs.</span>
        </div>
        <span className="gauge">128k queries · last 7d</span>
      </div>
      <div className="agents">
        {agents.map((a) => (
          <div key={a.name} className="agent" data-state={a.state}>
            <div className="nm">
              <span className="mk" style={{ background: a.color, color: '#fff' }}>{a.mk}</span>
              {a.name}
            </div>
            <div className="stat">
              <span className={`d ${a.state === 'live' ? 'live' : ''}`} />
              <span data-agent-state={a.state}>{a.state === 'live' ? 'live' : 'ready'}</span>
              <span style={{ marginLeft: 'auto', color: 'var(--text-tertiary)' }}>{a.qps}</span>
            </div>
          </div>
        ))}
      </div>
      <div className="mcp-cmd">
        <span className="p">$</span>
        <span>cursor mcp add</span>
        <span className="arg">company-brain</span>
        <span className="cm">  # wires brain into agent context · per-tenant isolated</span>
        <button className="cp" onClick={() => navigator.clipboard.writeText('cursor mcp add company-brain')}>Copy</button>
      </div>
    </div>
  );
}
