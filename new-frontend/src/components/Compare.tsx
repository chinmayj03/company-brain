interface CompareProps {
  ms?: number;
}

export default function Compare({ ms = 1840 }: CompareProps) {
  return (
    <div className="compare">
      <div className="cmp-pane">
        <div className="cmp-head">
          Company Brain <span className="badge ok">grounded · cited</span>
        </div>
        <p>
          <b>47 files affected</b> across 6 directories. 12 SQL chains read{' '}
          <span className="mono" style={{ background: 'var(--bg-surface)', padding: '0 4px', borderRadius: 3 }}>customer_id</span>{' '}
          via the jOOQ wrapper introduced in ADR-0042. 4 webhook handlers parse it from JSON; the public API exposes it on{' '}
          <span className="mono" style={{ background: 'var(--bg-surface)', padding: '0 4px', borderRadius: 3 }}>/v3/customers</span>{' '}
          — renaming is a breaking SDK change.
        </p>
        <div className="ms-meta">{ms} ms · 7 citations · graph nodes: 13</div>
      </div>
      <div className="cmp-pane">
        <div className="cmp-head">
          GPT-4o (no codebase) <span className="badge bad">generic</span>
        </div>
        <p className="gen">
          "I don't have access to your specific codebase. Generally, renaming a database column requires updating
          all references in your ORM models, query files, type definitions, and any API responses that expose
          the field. You may also want to check for any external integrations or webhooks…"
        </p>
        <div className="ms-meta">3,210 ms · 0 citations · no context</div>
      </div>
    </div>
  );
}
