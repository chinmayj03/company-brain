interface OPath { title?: string; description?: string; steps?: string[]; [key: string]: unknown }
interface Props { paths: unknown[] }

export default function OnboardingPaths({ paths }: Props) {
  if (!paths.length) return null;
  return (
    <div style={{
      marginTop: 16, padding: '14px 16px', borderRadius: 8,
      background: 'var(--info-soft)', border: '1px solid var(--info-border)',
    }}>
      <div style={{ fontSize: 11, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.08em', color: 'var(--info)', marginBottom: 10 }}>
        📚 Learn this
      </div>
      {(paths as OPath[]).map((p, i) => (
        <div key={i} style={{ marginBottom: 12 }}>
          {p.title && <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-primary)', marginBottom: 3 }}>{p.title}</div>}
          {p.description && <div style={{ fontSize: 12, color: 'var(--text-secondary)', lineHeight: 1.5 }}>{p.description}</div>}
          {p.steps?.length && (
            <ol style={{ margin: '6px 0 0', paddingLeft: 18 }}>
              {p.steps.map((s, j) => <li key={j} style={{ fontSize: 12, color: 'var(--text-secondary)', marginBottom: 2 }}>{s}</li>)}
            </ol>
          )}
        </div>
      ))}
    </div>
  );
}
