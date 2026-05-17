import { stripUrn } from '../utils/urn';

// ── Real API call_chain node format ───────────────────────────────────────────
interface RealCallNode {
  ord?: number;
  urn?: string;
  name?: string;
  role?: string;
  edge_in?: string | null;
  one_liner?: string;
  annotations?: unknown[];
}

// ── Legacy tree-child format (fallback) ───────────────────────────────────────
interface TreeCallNode {
  name?: string;
  label?: string;
  depth?: number;
  children?: TreeCallNode[];
  [key: string]: unknown;
}

interface Props { chain: unknown[] }

const ROLE_COLORS: Record<string, string> = {
  controller: 'var(--accent-primary)',
  service:    'var(--c-sage)',
  repository: 'var(--c-amber)',
  dto:        'var(--accent-primary)',
};

function RoleBadge({ role }: { role: string }) {
  const color = ROLE_COLORS[role.toLowerCase()] ?? 'var(--text-muted)';
  return (
    <span style={{
      fontSize: 10, fontWeight: 600, padding: '1px 7px', borderRadius: 4,
      background: `color-mix(in srgb, ${color} 12%, transparent)`,
      color,
      border: `1px solid color-mix(in srgb, ${color} 25%, transparent)`,
      marginLeft: 6, flexShrink: 0,
    }}>
      {role}
    </span>
  );
}

function RealChainList({ nodes }: { nodes: RealCallNode[] }) {
  return (
    <div style={{ padding: '4px 0' }}>
      {nodes.map((node, i) => {
        const displayName = node.name ?? stripUrn(node.urn ?? '');
        const isLast = i === nodes.length - 1;
        return (
          <div key={i}>
            {/* Edge arrow from previous step */}
            {node.edge_in && (
              <div style={{
                display: 'flex', alignItems: 'center', gap: 6,
                paddingLeft: 20, paddingBottom: 3, paddingTop: 1,
              }}>
                <div style={{ width: 1, height: 18, background: 'var(--border-default)', marginLeft: 10 }} />
                <span style={{
                  fontSize: 10, fontFamily: 'var(--font-mono)',
                  color: 'var(--text-muted)', letterSpacing: '0.04em',
                }}>
                  {node.edge_in}
                </span>
              </div>
            )}

            {/* Node card */}
            <div style={{
              display: 'flex', flexDirection: 'column', gap: 3,
              padding: '8px 12px', borderRadius: 6, marginBottom: isLast ? 0 : 2,
              background: 'var(--warm-surface)',
              border: '1px solid var(--warm-line-2)',
            }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                {/* Step number */}
                <span style={{
                  flexShrink: 0, width: 20, height: 20, borderRadius: '50%',
                  background: 'var(--neutral-900)', color: '#fff',
                  fontSize: 10, fontWeight: 700,
                  display: 'grid', placeItems: 'center',
                  fontVariantNumeric: 'tabular-nums',
                }}>
                  {node.ord ?? i + 1}
                </span>

                {/* Name */}
                <span style={{
                  flex: 1, fontSize: 12, fontWeight: 600,
                  color: 'var(--text-primary)', fontFamily: 'var(--font-mono)',
                  overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                }}>
                  {displayName}
                </span>

                {/* Role badge */}
                {node.role && <RoleBadge role={node.role} />}
              </div>

              {/* One-liner description */}
              {node.one_liner && (
                <div style={{
                  paddingLeft: 26,
                  fontSize: 12, color: 'var(--text-secondary)', lineHeight: 1.5,
                }}>
                  {node.one_liner}
                </div>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}

function LegacyTreeItem({ node, depth = 0 }: { node: TreeCallNode; depth?: number }) {
  const label = node.name ?? node.label ?? JSON.stringify(node);
  return (
    <div style={{ paddingLeft: depth * 16 }}>
      <div style={{
        display: 'flex', alignItems: 'center', gap: 6, padding: '5px 0',
        borderLeft: depth > 0 ? '1px solid var(--border-default)' : 'none',
        paddingLeft: depth > 0 ? 12 : 0,
        fontSize: 12, color: 'var(--text-secondary)', fontFamily: 'var(--font-mono)',
      }}>
        {depth > 0 && <span style={{ color: 'var(--text-muted)' }}>└─</span>}
        {label}
      </div>
      {node.children?.map((c, i) => <LegacyTreeItem key={i} node={c} depth={depth + 1} />)}
    </div>
  );
}

export default function CallChainTree({ chain }: Props) {
  if (!chain.length) {
    return (
      <div style={{ fontSize: 13, color: 'var(--text-muted)', padding: '16px 0', fontStyle: 'italic' }}>
        No call chain data for this query.
      </div>
    );
  }

  // Detect real API format: has `ord` or `urn` or `one_liner`
  const first = chain[0] as Record<string, unknown>;
  const isRealFormat = 'ord' in first || 'urn' in first || 'one_liner' in first;

  return (
    <div style={{ maxHeight: 380, overflowY: 'auto' }}>
      {isRealFormat
        ? <RealChainList nodes={chain as RealCallNode[]} />
        : (chain as TreeCallNode[]).map((n, i) => <LegacyTreeItem key={i} node={n} depth={0} />)
      }
    </div>
  );
}
