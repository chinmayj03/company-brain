interface RiskAlert { level: string; summary?: string; [key: string]: unknown }
interface Props { alerts: RiskAlert[] }

export default function RiskAlertBanner({ alerts }: Props) {
  if (!alerts.length) return null;
  return (
    <div style={{
      marginBottom: 14, padding: '10px 14px', borderRadius: 7,
      background: 'var(--danger-soft)', border: '1px solid var(--danger-border)',
      display: 'flex', flexDirection: 'column', gap: 4,
    }}>
      <div style={{ fontSize: 11, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.08em', color: 'var(--danger)', marginBottom: 2 }}>
        🔴 Risk alerts
      </div>
      {alerts.map((a, i) => (
        <div key={i} style={{ fontSize: 13, color: 'var(--text-secondary)' }}>
          <span style={{ fontWeight: 600, color: 'var(--danger)' }}>[{a.level}]</span>{' '}
          {a.summary ?? JSON.stringify(a)}
        </div>
      ))}
    </div>
  );
}
