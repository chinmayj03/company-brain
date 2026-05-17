import { useState } from 'react';
import type { AffectedEntity } from '../data/brain_client';
import { stripUrn, inferRole, confidenceToWeight } from '../utils/urn';

interface Props {
  entities: AffectedEntity[];
}

const ROLE_COLORS: Record<string, string> = {
  controller: 'var(--accent-primary)',
  repository: 'var(--c-sage)',
  service:    'var(--c-amber)',
  DTO:        'var(--accent-primary)',
  'ORM entity': 'var(--danger)',
  mapper:     'var(--text-tertiary)',
  config:     'var(--text-tertiary)',
  filter:     'var(--c-terracotta)',
  class:      'var(--text-muted)',
};

const WEIGHT_META: Record<string, { label: string; color: string; bg: string; border: string }> = {
  high: { label: 'HIGH',   color: 'var(--danger)',         bg: 'var(--danger-soft)',   border: 'var(--danger-border)' },
  med:  { label: 'MED',    color: 'var(--warning)',        bg: 'var(--warning-soft)',  border: 'var(--warning-border)' },
  low:  { label: 'LOW',    color: 'var(--text-tertiary)',  bg: 'var(--bg-surface)',    border: 'var(--border-default)' },
};

function EntityRow({ entity }: { entity: AffectedEntity; index: number }) {
  const [expanded, setExpanded] = useState(false);

  const id     = entity.urn ?? entity.id ?? entity.name;
  const label  = entity.name ?? stripUrn(id);
  const role   = entity.type ?? inferRole(label);
  const weight = entity.weight ?? (entity.confidence != null ? confidenceToWeight(entity.confidence) : 'med');
  const meta   = WEIGHT_META[weight] ?? WEIGHT_META.med;
  const roleColor = ROLE_COLORS[role] ?? 'var(--text-muted)';
  const hasDetail = !!entity.why_relevant;

  return (
    <div
      style={{
        display: 'flex', flexDirection: 'column',
        border: '1px solid var(--warm-line-2)',
        background: 'var(--bg-page)',
        borderRadius: 6, marginBottom: 6, overflow: 'hidden',
        cursor: hasDetail ? 'pointer' : 'default',
        transition: 'border-color .12s',
        ...(expanded ? { borderColor: 'var(--accent-primary)' } : {}),
      }}
      onClick={() => hasDetail && setExpanded((e) => !e)}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '9px 12px' }}>
        {/* Risk badge */}
        <span style={{
          flexShrink: 0, minWidth: 40, textAlign: 'center',
          fontSize: 10, fontWeight: 700, letterSpacing: '0.07em',
          padding: '2px 6px', borderRadius: 4,
          background: meta.bg, color: meta.color, border: `1px solid ${meta.border}`,
        }}>
          {meta.label}
        </span>

        {/* Entity name */}
        <span style={{
          flex: 1, fontFamily: 'var(--font-mono)', fontSize: 12,
          fontWeight: 600, color: 'var(--text-primary)',
          overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
        }}>
          {label}
        </span>

        {/* Role badge */}
        <span style={{
          flexShrink: 0, fontSize: 10, fontWeight: 500,
          padding: '2px 7px', borderRadius: 4,
          background: `color-mix(in srgb, ${roleColor} 12%, transparent)`,
          color: roleColor,
          border: `1px solid color-mix(in srgb, ${roleColor} 25%, transparent)`,
        }}>
          {role}
        </span>

        {/* Confidence */}
        {entity.confidence != null && (
          <span style={{ flexShrink: 0, fontSize: 10, color: 'var(--text-muted)', fontVariantNumeric: 'tabular-nums' }}>
            {Math.round(entity.confidence * 100)}%
          </span>
        )}

        {/* Expand chevron */}
        {hasDetail && (
          <span style={{ flexShrink: 0, fontSize: 10, color: 'var(--text-muted)', transform: expanded ? 'rotate(180deg)' : undefined, transition: 'transform .12s' }}>▾</span>
        )}
      </div>

      {/* Expanded why_relevant */}
      {expanded && entity.why_relevant && (
        <div style={{
          padding: '8px 12px 10px 68px',
          borderTop: '1px solid var(--warm-line)',
          fontSize: 12, color: 'var(--text-secondary)', lineHeight: 1.55,
          background: 'var(--warm-surface)',
        }}>
          {entity.why_relevant}
        </div>
      )}
    </div>
  );
}

export default function AffectedBreakdownList({ entities }: Props) {
  if (!entities.length) return null;

  // Sort: high → med → low
  const sorted = [...entities].sort((a, b) => {
    const order = { high: 0, med: 1, low: 2 };
    const wa = a.weight ?? (a.confidence != null ? confidenceToWeight(a.confidence) : 'med');
    const wb = b.weight ?? (b.confidence != null ? confidenceToWeight(b.confidence) : 'med');
    return (order[wa] ?? 1) - (order[wb] ?? 1);
  });

  const highCount = sorted.filter((e) => {
    const w = e.weight ?? (e.confidence != null ? confidenceToWeight(e.confidence) : 'med');
    return w === 'high';
  }).length;

  return (
    <div style={{ marginTop: 18, marginBottom: 18 }}>
      <div style={{
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        marginBottom: 10,
      }}>
        <h3 style={{
          margin: 0, fontSize: 12, fontWeight: 600,
          letterSpacing: '0.06em', textTransform: 'uppercase',
          color: 'var(--text-tertiary)',
        }}>
          What breaks · {entities.length} {entities.length === 1 ? 'entity' : 'entities'}
        </h3>
        {highCount > 0 && (
          <span style={{
            fontSize: 11, fontWeight: 600, padding: '2px 8px', borderRadius: 4,
            background: 'var(--danger-soft)', color: 'var(--danger)',
            border: '1px solid var(--danger-border)',
          }}>
            {highCount} high risk
          </span>
        )}
      </div>
      {sorted.map((e, i) => (
        <EntityRow key={(e.urn ?? e.id ?? e.name) + i} entity={e} index={i} />
      ))}
      {entities.some((e) => e.why_relevant) && (
        <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 4 }}>
          Click any row to see why it's affected.
        </div>
      )}
    </div>
  );
}
