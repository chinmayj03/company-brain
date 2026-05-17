interface Props { caveats: string[] }

export default function CaveatBanner({ caveats }: Props) {
  if (!caveats.length) return null;
  return (
    <div style={{
      marginTop: 12, padding: '10px 14px', borderRadius: 7,
      background: 'var(--warning-soft)', border: '1px solid var(--warning-border)',
      display: 'flex', flexDirection: 'column', gap: 4,
    }}>
      <div style={{ fontSize: 11, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.08em', color: 'var(--warning)', marginBottom: 4 }}>
        ⚠ Caveats
      </div>
      {caveats.map((c, i) => (
        <div key={i} style={{ fontSize: 13, color: 'var(--text-secondary)', lineHeight: 1.5 }}>· {c}</div>
      ))}
    </div>
  );
}
