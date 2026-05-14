import { graph, type GraphNode } from '../data/mock_fallback';

interface BlastRadiusProps {
  dense?: boolean;
  /** When provided (live mode), replaces MOCK_GRAPH nodes. Center node is preserved. */
  liveNodes?: GraphNode[];
}

export default function BlastRadius({ dense, liveNodes }: BlastRadiusProps) {
  const W = 720, H = 360;
  const px = (n: number) => (n / 100) * W;
  const py = (n: number) => (n / 100) * H;
  const cx = px(graph.center.x), cy = py(graph.center.y);

  const nodes = liveNodes ?? graph.nodes;
  const ring1 = nodes.filter((n) => n.ring === 1);
  const ring2 = nodes.filter((n) => n.ring === 2);
  const byId = Object.fromEntries(nodes.map((n) => [n.id, n]));

  const edgeClass = (w: string) =>
    w === 'high' ? 'bg-edge bg-edge--high' : w === 'med' ? 'bg-edge bg-edge--med' : 'bg-edge';

  return (
    <svg
      className="blast-radius"
      viewBox={`0 0 ${W} ${H}`}
      width="100%"
      height={dense ? 240 : 360}
      preserveAspectRatio="xMidYMid meet"
    >
      {/* concentric rings */}
      <circle cx={cx} cy={cy} r={92}  fill="none" stroke="var(--warm-line-2)" strokeDasharray="2 4" />
      <circle cx={cx} cy={cy} r={170} fill="none" stroke="var(--warm-line)"   strokeDasharray="2 4" />

      {/* ring-1 → center edges */}
      {ring1.map((n) => (
        <line key={`e-${n.id}`} x1={cx} y1={cy} x2={px(n.x)} y2={py(n.y)} className={edgeClass(n.weight)} />
      ))}

      {/* ring-2 → ring-1 edges */}
      {ring2.map((n) => {
        const via = n.via ? byId[n.via] : undefined;
        if (!via) return null;
        return (
          <line
            key={`e2-${n.id}`}
            x1={px(via.x)} y1={py(via.y)}
            x2={px(n.x)}   y2={py(n.y)}
            className={edgeClass(n.weight)}
            strokeDasharray={n.weight === 'low' ? '3 3' : undefined}
          />
        );
      })}

      {/* ring-2 nodes */}
      {ring2.map((n) => (
        <g key={`r2-${n.id}`}>
          <rect x={px(n.x) - 56} y={py(n.y) - 14} width="112" height="28" rx="4"
            fill="var(--warm-surface)" stroke="var(--warm-line-2)" strokeWidth="1" />
          <text x={px(n.x)} y={py(n.y) - 1}  textAnchor="middle" className="bg-label">{n.label}</text>
          <text x={px(n.x)} y={py(n.y) + 10} textAnchor="middle" className="bg-label-sub">{n.sub}</text>
        </g>
      ))}

      {/* ring-1 nodes */}
      {ring1.map((n) => (
        <g key={`r1-${n.id}`}>
          <rect
            x={px(n.x) - 64} y={py(n.y) - 16} width="128" height="32" rx="5"
            fill="var(--warm-surface)"
            stroke={n.weight === 'high' ? 'var(--danger)' : 'var(--warm-line-2)'}
            strokeWidth={n.weight === 'high' ? 1.4 : 1}
          />
          <text x={px(n.x)} y={py(n.y) - 1}  textAnchor="middle" className="bg-label" style={{ fontWeight: 600 }}>{n.label}</text>
          <text x={px(n.x)} y={py(n.y) + 11} textAnchor="middle" className="bg-label-sub">{n.sub}</text>
        </g>
      ))}

      {/* center node */}
      <g>
        <circle cx={cx} cy={cy} r={42} className="bg-node-c" />
        <text x={cx} y={cy - 3}  textAnchor="middle" className="bg-center-label">{graph.center.label}</text>
        <text x={cx} y={cy + 13} textAnchor="middle" className="bg-center-label" style={{ fontWeight: 400, opacity: 0.7, fontSize: 9.5 }}>{graph.center.sub}</text>
      </g>
    </svg>
  );
}
