/**
 * DependencyGraph — ReactFlow canvas showing a 2-hop neighbourhood.
 * Nodes are coloured by type; edges are labelled with their relationship type.
 */

import { useCallback } from 'react';
import { useQuery } from '@tanstack/react-query';
import ReactFlow, {
  Background, Controls, MiniMap,
  useNodesState, useEdgesState,
  MarkerType,
} from 'reactflow';
import 'reactflow/dist/style.css';
import { getServiceGraph } from '../../api/client';
import { Loader } from 'lucide-react';

const TYPE_COLOURS = {
  Service:           '#4f6ef7',
  ApiEndpoint:       '#7c3aed',
  FrontendComponent: '#0891b2',
  DatabaseTable:     '#b45309',
  DatabaseColumn:    '#d97706',
  SchemaField:       '#9333ea',
  CodeFunction:      '#16a34a',
  Team:              '#dc2626',
  ExternalService:   '#6b7280',
};

function nodeColour(type) { return TYPE_COLOURS[type] || '#6b7280'; }

function toFlowNodes(nodes) {
  return nodes.map((n, i) => ({
    id: n.id,
    type: 'default',
    position: {
      x: 250 * Math.cos((2 * Math.PI * i) / nodes.length) + 400,
      y: 250 * Math.sin((2 * Math.PI * i) / nodes.length) + 300,
    },
    data: { label: n.name },
    style: {
      background: nodeColour(n.nodeType),
      color: '#fff',
      border: 'none',
      borderRadius: 8,
      fontSize: 11,
      fontWeight: 600,
      padding: '6px 12px',
      maxWidth: 160,
    },
  }));
}

function toFlowEdges(edges) {
  return edges.map(e => ({
    id: e.id,
    source: e.sourceId,
    target: e.targetId,
    label: e.edgeType,
    labelStyle: { fontSize: 9, fill: '#94a3b8' },
    labelBgStyle: { fill: '#f8fafc' },
    style: { stroke: '#cbd5e1', strokeWidth: 1.5 },
    markerEnd: { type: MarkerType.ArrowClosed, color: '#cbd5e1' },
  }));
}

export default function DependencyGraph({ workspaceId, nodeId, nodeName }) {
  const { data, isLoading, isError } = useQuery({
    queryKey: ['service-graph', workspaceId, nodeId],
    queryFn: () => getServiceGraph(nodeId),
    enabled: !!nodeId,
  });

  const flowNodes = data ? toFlowNodes(data.nodes || []) : [];
  const flowEdges = data ? toFlowEdges(data.edges || []) : [];

  const [nodes, , onNodesChange] = useNodesState(flowNodes);
  const [edges, , onEdgesChange] = useEdgesState(flowEdges);

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
          <div className="text-2xl mb-2">⬡</div>
          <p>No graph data for <strong>{nodeName}</strong> yet.</p>
          <p className="text-xs mt-1">Run the context pipeline in API Explorer first.</p>
        </div>
      </div>
    );
  }

  return (
    <ReactFlow
      nodes={nodes}
      edges={edges}
      onNodesChange={onNodesChange}
      onEdgesChange={onEdgesChange}
      fitView
      fitViewOptions={{ padding: 0.3 }}
    >
      <Background color="#e2e8f0" gap={20} />
      <Controls />
      <MiniMap nodeColor={n => n.style?.background || '#6b7280'} />
    </ReactFlow>
  );
}
