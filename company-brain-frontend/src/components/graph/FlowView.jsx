/**
 * FlowView — layered call-chain visualization.
 *
 * Organizes extracted entities into swim-lane columns based on their
 * architectural role: Entry → Controller → Service → Repository → DB/External.
 *
 * Data comes from the same GET /v1/services/{nodeId}/graph endpoint as the
 * DependencyGraph — no new API needed. Node layer is inferred from:
 *   1. node.metadata.role  (set by NavigatorAgent classify pass)
 *   2. node.nodeType       (ApiEndpoint, DatabaseTable, etc.)
 *   3. Name heuristics     (contains "Controller", "Service", "Repo", etc.)
 */

import { useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import ReactFlow, {
  Background,
  Controls,
  MarkerType,
  Handle,
  Position,
} from 'reactflow';
import 'reactflow/dist/style.css';
import { getServiceGraph } from '../../api/client';
import { Loader } from 'lucide-react';

// ── Layer definitions ────────────────────────────────────────────────────────

const LAYERS = [
  { label: 'Entry',       color: '#7c3aed', light: '#f5f3ff' },
  { label: 'Controller',  color: '#2563eb', light: '#eff6ff' },
  { label: 'Service',     color: '#16a34a', light: '#f0fdf4' },
  { label: 'Repository',  color: '#b45309', light: '#fffbeb' },
  { label: 'DB / Ext',    color: '#6b7280', light: '#f9fafb' },
];

function getLayer(node) {
  const role = (node.metadata?.role || '').toLowerCase();
  const type  = node.nodeType || '';
  const name  = (node.name   || '').toLowerCase();

  if (type === 'ApiEndpoint')                                              return 0;
  if (role === 'controller' || name.includes('controller'))               return 1;
  if (role === 'service' || role === 'event_handler'
      || name.includes('service') || name.includes('facade'))             return 2;
  if (role === 'repository' || name.includes('repo')
      || name.includes('dao') || name.includes('mapper'))                 return 3;
  if (role === 'model' || type === 'DatabaseTable'
      || type === 'DatabaseColumn' || type === 'ExternalService'
      || role === 'client')                                                return 4;

  // last-resort name hints
  if (name.includes('entity') || name.includes('table'))                  return 4;
  return 2;
}

// ── Layout engine ────────────────────────────────────────────────────────────

const LANE_W   = 220;   // column width (px)
const LANE_PAD = 20;    // left padding per lane
const NODE_H   = 70;    // estimated node height
const NODE_GAP = 30;    // vertical gap between nodes in same lane
const TOP_PAD  = 20;    // top margin inside canvas

function buildLayout(apiNodes, apiEdges) {
  // 1. Assign each node a layer
  const withLayer = apiNodes.map(n => ({ ...n, layer: getLayer(n) }));

  // 2. Group by layer; keep only layers that have nodes
  const byLayer = new Map();
  for (const n of withLayer) {
    if (!byLayer.has(n.layer)) byLayer.set(n.layer, []);
    byLayer.get(n.layer).push(n);
  }

  // 3. Compact column indices (so unused layers don't leave blank gaps)
  const activeLayers = [...byLayer.keys()].sort((a, b) => a - b);
  const colOf = Object.fromEntries(activeLayers.map((l, i) => [l, i]));

  // 4. Compute max column height for vertical centering
  const maxNodes = Math.max(...[...byLayer.values()].map(a => a.length));

  // 5. Build ReactFlow node array
  const flowNodes = [];
  for (const [layer, nodes] of byLayer) {
    const col = colOf[layer];
    const x   = col * LANE_W + LANE_PAD;
    const colH   = nodes.length * (NODE_H + NODE_GAP) - NODE_GAP;
    const maxH   = maxNodes   * (NODE_H + NODE_GAP) - NODE_GAP;
    const startY = TOP_PAD + (maxH - colH) / 2;   // vertically centre each column

    nodes.forEach((node, i) => {
      flowNodes.push({
        id:   String(node.id),
        type: 'flowNode',
        position: { x, y: startY + i * (NODE_H + NODE_GAP) },
        data: {
          label: node.name,
          layer,
          file:   node.metadata?.file,
          role:   node.metadata?.role,
          type:   node.nodeType,
        },
      });
    });
  }

  // 6. Build ReactFlow edge array
  const nodeIds = new Set(flowNodes.map(n => n.id));
  const flowEdges = (apiEdges || [])
    .filter(e => nodeIds.has(String(e.sourceId)) && nodeIds.has(String(e.targetId)))
    .map(e => ({
      id:     String(e.id),
      source: String(e.sourceId),
      target: String(e.targetId),
      label:  (e.edgeType || '').replace(/_/g, ' ').toLowerCase(),
      labelStyle:   { fontSize: 9, fill: '#64748b' },
      labelBgStyle: { fill: '#ffffff', fillOpacity: 0.95 },
      labelBgPadding: [3, 6],
      style: { stroke: '#94a3b8', strokeWidth: 1.5 },
      markerEnd: { type: MarkerType.ArrowClosed, color: '#94a3b8', width: 14, height: 14 },
      type: 'smoothstep',
    }));

  // 7. Build lane-background nodes (rendered as large coloured rectangles behind nodes)
  const canvasH = TOP_PAD + maxNodes * (NODE_H + NODE_GAP) + TOP_PAD;
  const laneNodes = activeLayers.map(layer => {
    const col    = colOf[layer];
    const lDef   = LAYERS[layer] || LAYERS[4];
    return {
      id:   `__lane-${layer}`,
      type: 'laneNode',
      position: { x: col * LANE_W, y: -TOP_PAD },
      selectable: false,
      draggable:  false,
      data: { label: lDef.label, color: lDef.color, bg: lDef.light, height: canvasH + TOP_PAD * 2, width: LANE_W },
    };
  });

  return { flowNodes: [...laneNodes, ...flowNodes], flowEdges, activeLayers, colOf };
}

// ── Custom node types ─────────────────────────────────────────────────────────

/** Lane background — a full-height coloured strip with a header label */
function LaneNode({ data }) {
  return (
    <div
      style={{
        width:  data.width,
        height: data.height,
        background: data.bg,
        borderRight: `1px solid ${data.color}22`,
        pointerEvents: 'none',
        userSelect: 'none',
      }}
    >
      <div
        style={{
          padding:    '8px 12px',
          fontSize:   10,
          fontWeight: 700,
          letterSpacing: '0.07em',
          textTransform: 'uppercase',
          color: data.color,
          borderBottom: `1px solid ${data.color}33`,
          background: `${data.color}11`,
        }}
      >
        {data.label}
      </div>
    </div>
  );
}

/** Entity node — shows name, role badge, and file hint */
function FlowNode({ data }) {
  const lDef = LAYERS[data.layer] || LAYERS[2];
  const filename = data.file
    ? data.file.split(/[\\/]/).pop().replace(/\.java$/, '')
    : null;

  return (
    <div
      style={{
        background: lDef.color,
        color:       '#fff',
        borderRadius: 8,
        padding:     '8px 12px',
        minWidth:    160,
        maxWidth:    200,
        fontSize:    11,
        fontWeight:  600,
        boxShadow:   '0 2px 8px rgba(0,0,0,0.18)',
        position:    'relative',
      }}
    >
      <Handle
        type="target"
        position={Position.Left}
        style={{ background: 'rgba(255,255,255,0.45)', width: 8, height: 8 }}
      />

      {/* Role badge */}
      {(data.role || data.type) && (
        <div
          style={{
            fontSize:       9,
            opacity:        0.8,
            marginBottom:   4,
            textTransform: 'uppercase',
            letterSpacing: '0.06em',
          }}
        >
          {data.role || data.type}
        </div>
      )}

      {/* Node name */}
      <div style={{ wordBreak: 'break-word', lineHeight: 1.3 }}>{data.label}</div>

      {/* File hint */}
      {filename && filename !== data.label && (
        <div
          style={{
            marginTop:   4,
            fontSize:    9,
            opacity:     0.65,
            fontFamily: 'monospace',
            fontWeight:  400,
          }}
        >
          {filename}.java
        </div>
      )}

      <Handle
        type="source"
        position={Position.Right}
        style={{ background: 'rgba(255,255,255,0.45)', width: 8, height: 8 }}
      />
    </div>
  );
}

const NODE_TYPES = {
  flowNode: FlowNode,
  laneNode: LaneNode,
};

// ── Main component ────────────────────────────────────────────────────────────

export default function FlowView({ nodeId, nodeName }) {
  const { data, isLoading, isError } = useQuery({
    queryKey: ['service-graph', nodeId],
    queryFn: () => getServiceGraph(nodeId),
    enabled: !!nodeId,
    staleTime: 60_000,
  });

  const { flowNodes, flowEdges } = useMemo(() => {
    if (!data?.nodes?.length) return { flowNodes: [], flowEdges: [] };
    return buildLayout(data.nodes, data.edges || []);
  }, [data]);

  if (isLoading) {
    return (
      <div className="absolute inset-0 flex items-center justify-center">
        <Loader size={20} className="animate-spin text-slate-400" />
      </div>
    );
  }

  if (isError || !data?.nodes?.length) {
    return (
      <div className="absolute inset-0 flex items-center justify-center text-center text-slate-400 text-sm">
        <div>
          <div className="text-4xl mb-2">⬡</div>
          <p>No call-chain data for <strong>{nodeName}</strong> yet.</p>
          <p className="text-xs mt-1 opacity-70">Run the context pipeline in API Explorer first.</p>
        </div>
      </div>
    );
  }

  return (
    <ReactFlow
      nodes={flowNodes}
      edges={flowEdges}
      nodeTypes={NODE_TYPES}
      fitView
      fitViewOptions={{ padding: 0.1 }}
      nodesDraggable={false}
      nodesConnectable={false}
      elementsSelectable={true}
      minZoom={0.3}
    >
      <Background color="#e2e8f0" gap={24} />
      <Controls showInteractive={false} />
    </ReactFlow>
  );
}
