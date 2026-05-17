import { useEffect, useCallback } from 'react';
import {
  ReactFlow, Background, Controls, MiniMap,
  useNodesState, useEdgesState, MarkerType,
  type Node, type Edge, type NodeTypes,
} from '@xyflow/react';
import type { AffectedEntity } from '../data/brain_client';
import { graph as MOCK_GRAPH } from '../data/mock_fallback';
import { inferRole, confidenceToWeight } from '../utils/urn';
import * as d3 from 'd3-force';

// ── Custom node types ─────────────────────────────────────────────────────────

function CenterNode({ data }: { data: { label: string; sub: string } }) {
  return (
    <div style={{
      width: 100, height: 100, borderRadius: '50%',
      background: 'var(--neutral-900)', color: '#fff',
      display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
      fontSize: 11, fontFamily: 'var(--font-mono)', fontWeight: 600,
      textAlign: 'center', padding: 8, lineHeight: 1.3,
      boxShadow: '0 0 0 3px var(--warm-line-2)',
    }}>
      <span>{data.label}</span>
      <span style={{ fontSize: 9, opacity: 0.7, fontWeight: 400, marginTop: 3 }}>{data.sub}</span>
    </div>
  );
}

function Ring1Node({ data }: { data: { label: string; sub: string; weight: string; selected?: boolean } }) {
  const isHigh = data.weight === 'high';
  return (
    <div style={{
      padding: '8px 14px', borderRadius: 6, minWidth: 140, maxWidth: 180,
      background: 'var(--warm-surface)',
      border: `1.5px solid ${isHigh ? 'var(--danger)' : 'var(--warm-line-2)'}`,
      boxShadow: data.selected ? '0 0 0 2px var(--accent-primary)' : undefined,
      cursor: 'pointer', textAlign: 'center',
    }}>
      <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-primary)', lineHeight: 1.3 }}>{data.label}</div>
      <div style={{ fontSize: 10, color: isHigh ? 'var(--danger)' : 'var(--text-tertiary)', fontFamily: 'var(--font-mono)', marginTop: 2 }}>{data.sub}</div>
    </div>
  );
}

function Ring2Node({ data }: { data: { label: string; sub: string; weight: string } }) {
  return (
    <div style={{
      padding: '5px 10px', borderRadius: 5, minWidth: 110, maxWidth: 160,
      background: 'var(--warm-surface)',
      border: '1px solid var(--warm-line-2)',
      cursor: 'pointer', textAlign: 'center', opacity: 0.85,
    }}>
      <div style={{ fontSize: 11, fontWeight: 500, color: 'var(--text-secondary)', lineHeight: 1.3 }}>{data.label}</div>
      <div style={{ fontSize: 9.5, color: 'var(--text-tertiary)', fontFamily: 'var(--font-mono)', marginTop: 1 }}>{data.sub}</div>
    </div>
  );
}

const NODE_TYPES: NodeTypes = {
  center: CenterNode,
  ring1:  Ring1Node,
  ring2:  Ring2Node,
};

// ── Force layout ──────────────────────────────────────────────────────────────

interface SimNode extends d3.SimulationNodeDatum {
  id: string;
}

interface SimLink extends d3.SimulationLinkDatum<SimNode> {
  strength: number;
}

function runForceLayout(
  ids: string[],
  links: Array<{ source: string; target: string; strength: number }>,
  centerX: number, centerY: number,
): Map<string, { x: number; y: number }> {
  const simNodes: SimNode[] = [
    { id: '__center__', x: centerX, y: centerY, fx: centerX, fy: centerY },
    ...ids.map((id, i) => ({
      id,
      x: centerX + Math.cos(i * 0.8) * 150,
      y: centerY + Math.sin(i * 0.8) * 150,
    })),
  ];

  const simLinks: SimLink[] = links.map((l) => ({ ...l, strength: l.strength }));

  const sim = d3.forceSimulation<SimNode>(simNodes)
    .force('link', d3.forceLink<SimNode, SimLink>(simLinks)
      .id((d) => d.id)
      .distance((l) => l.strength > 0.5 ? 120 : 180))
    .force('charge', d3.forceManyBody<SimNode>().strength(-200))
    .force('collision', d3.forceCollide<SimNode>(70))
    .stop();

  sim.tick(120); // synchronous — no animation needed for layout

  const map = new Map<string, { x: number; y: number }>();
  simNodes.forEach((n) => map.set(n.id, { x: n.x ?? 0, y: n.y ?? 0 }));
  return map;
}

// ── Converters ────────────────────────────────────────────────────────────────

function mockToFlow(): { nodes: Node[]; edges: Edge[] } {
  const mockLinks = MOCK_GRAPH.nodes.map((n) => ({
    source: n.via ?? '__center__',
    target: n.id,
    strength: n.ring === 1 ? 0.6 : 0.3,
  }));

  const positions = runForceLayout(
    MOCK_GRAPH.nodes.map((n) => n.id),
    mockLinks,
    360, 180,
  );

  const centerPos = positions.get('__center__') ?? { x: 360, y: 180 };

  const nodes: Node[] = [
    {
      id: '__center__',
      type: 'center',
      position: centerPos,
      data: { label: MOCK_GRAPH.center.label, sub: MOCK_GRAPH.center.sub },
      draggable: false,
    },
    ...MOCK_GRAPH.nodes.map((n) => ({
      id: n.id,
      type: n.ring === 1 ? 'ring1' : 'ring2',
      position: positions.get(n.id) ?? { x: 0, y: 0 },
      data: { label: n.label, sub: n.sub, weight: n.weight },
    })),
  ];

  const edges: Edge[] = MOCK_GRAPH.nodes.map((n) => ({
    id: `e-${n.id}`,
    source: n.via ?? '__center__',
    target: n.id,
    style: {
      stroke: n.weight === 'high' ? 'var(--danger)' : n.weight === 'med' ? 'var(--warning)' : 'var(--text-muted)',
      strokeWidth: n.weight === 'high' ? 1.8 : 1.2,
      opacity: n.weight === 'low' ? 0.5 : 0.8,
      strokeDasharray: n.weight === 'low' ? '4 3' : undefined,
    },
    markerEnd: { type: MarkerType.ArrowClosed, width: 8, height: 8, color: n.weight === 'high' ? 'var(--danger)' : 'var(--text-muted)' },
  }));

  return { nodes, edges };
}

function liveToFlow(
  liveNodes: AffectedEntity[],
  centerLabel: string,
): { nodes: Node[]; edges: Edge[] } {
  const nodeIds = liveNodes.map((e, i) => e.urn ?? e.id ?? `node-${i}`);

  const liveLinks = nodeIds.map((id) => ({
    source: '__center__',
    target: id,
    strength: 0.5,
  }));

  const positions = runForceLayout(nodeIds, liveLinks, 360, 180);

  const centerPos = positions.get('__center__') ?? { x: 360, y: 180 };

  const nodes: Node[] = [
    {
      id: '__center__',
      type: 'center',
      position: centerPos,
      data: { label: centerLabel.slice(0, 20), sub: 'queried entity' },
      draggable: false,
    },
    ...liveNodes.map((e, i) => {
      const id    = e.urn ?? e.id ?? `node-${i}`;
      const depth = e.depth ?? (i < Math.ceil(liveNodes.length * 0.5) ? 1 : 2);
      const weight: 'high' | 'med' | 'low' =
        e.weight ?? (e.confidence != null ? confidenceToWeight(e.confidence) : depth === 1 ? 'med' : 'low');
      return {
        id,
        type: depth === 1 ? 'ring1' : 'ring2',
        position: positions.get(id) ?? { x: 0, y: 0 },
        data: {
          label:  e.name,
          sub:    e.type ?? inferRole(e.name),
          weight,
        },
      };
    }),
  ];

  const edges: Edge[] = liveNodes.map((e, i) => {
    const id     = e.urn ?? e.id ?? `node-${i}`;
    const weight: 'high' | 'med' | 'low' =
      e.weight ?? (e.confidence != null ? confidenceToWeight(e.confidence) : 'med');
    return {
      id: `e-${id}`,
      source: '__center__',
      target: id,
      style: {
        stroke: weight === 'high' ? 'var(--danger)' : weight === 'med' ? 'var(--warning)' : 'var(--text-muted)',
        strokeWidth: weight === 'high' ? 1.8 : 1.2,
        opacity: 0.75,
      },
      markerEnd: { type: MarkerType.ArrowClosed, width: 8, height: 8 },
    };
  });

  return { nodes, edges };
}

// ── Main component ────────────────────────────────────────────────────────────

interface BlastRadiusGraphProps {
  /** Live entities from QueryResponse. If undefined, use mock graph. */
  liveEntities?: AffectedEntity[];
  /** Label for the center node in live mode (the query subject). */
  centerLabel?: string;
  /** Called when the user clicks a node. */
  onNodeClick?: (entity: AffectedEntity | null, nodeId: string) => void;
  compact?: boolean;
}

export default function BlastRadiusGraph({
  liveEntities, centerLabel = 'Entity', onNodeClick, compact,
}: BlastRadiusGraphProps) {
  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);

  useEffect(() => {
    if (liveEntities) {
      const { nodes: n, edges: e } = liveToFlow(liveEntities, centerLabel);
      setNodes(n);
      setEdges(e);
    } else {
      const { nodes: n, edges: e } = mockToFlow();
      setNodes(n);
      setEdges(e);
    }
  }, [liveEntities, centerLabel]);

  const handleNodeClick = useCallback((_: React.MouseEvent, node: Node) => {
    if (node.id === '__center__') return;
    const entity = liveEntities?.find((e) => (e.urn ?? e.id) === node.id) ?? null;
    onNodeClick?.(entity, node.id);
  }, [liveEntities, onNodeClick]);

  return (
    <div style={{ height: compact ? 260 : 380, borderRadius: 8, overflow: 'hidden', border: '1px solid var(--warm-line)' }}>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onNodeClick={handleNodeClick}
        nodeTypes={NODE_TYPES}
        fitView
        fitViewOptions={{ padding: 0.2 }}
        attributionPosition="bottom-right"
        proOptions={{ hideAttribution: true }}
        style={{ background: 'var(--warm-page)' }}
      >
        <Background color="var(--warm-line)" gap={20} size={1} />
        <Controls style={{ background: 'var(--warm-surface)', border: '1px solid var(--warm-line)' }} />
        <MiniMap
          nodeColor={(n) => n.type === 'center' ? 'var(--neutral-900)' : n.data?.weight === 'high' ? 'var(--danger)' : 'var(--warm-line-2)'}
          style={{ background: 'var(--warm-surface)', border: '1px solid var(--warm-line)' }}
        />
      </ReactFlow>
    </div>
  );
}
