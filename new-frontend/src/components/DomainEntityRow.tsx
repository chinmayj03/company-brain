interface DomainEntity { name?: string; type?: string; urn?: string; [key: string]: unknown }
interface Props { entities: unknown[] }

export default function DomainEntityRow({ entities }: Props) {
  if (!entities.length) return null;
  return (
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginTop: 10, marginBottom: 2 }}>
      {(entities as DomainEntity[]).slice(0, 12).map((e, i) => (
        <span key={i} style={{
          display: 'inline-flex', alignItems: 'center', gap: 5,
          padding: '3px 9px', borderRadius: 4, fontSize: 11, fontWeight: 500,
          background: 'var(--accent-soft)', border: '1px solid var(--accent-soft-border)',
          color: 'var(--accent-primary)', fontFamily: 'var(--font-mono)',
          cursor: 'default',
        }}>
          {e.type && <span style={{ opacity: 0.6, fontWeight: 400 }}>{e.type}</span>}
          {e.name ?? e.urn ?? '?'}
        </span>
      ))}
      {entities.length > 12 && (
        <span style={{ fontSize: 11, color: 'var(--text-muted)', alignSelf: 'center' }}>+{entities.length - 12} more</span>
      )}
    </div>
  );
}
