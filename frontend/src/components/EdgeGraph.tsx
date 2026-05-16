import ReactFlow, { Background, Controls, type Edge, type Node } from "reactflow";
import type { BrainEntity } from "../api/types";

export default function EdgeGraph({ entity }: { entity: BrainEntity }) {
  const related = entity.related_entities || [];
  const nodes: Node[] = [
    {
      id: entity.urn,
      position: { x: 260, y: 120 },
      data: { label: entity.name },
      className: "graph-node graph-node-root",
    },
    ...related.slice(0, 8).map((item, index) => {
      const angle = (Math.PI * 2 * index) / Math.max(related.length, 1);
      return {
        id: item.urn,
        position: {
          x: 260 + Math.cos(angle) * 210,
          y: 120 + Math.sin(angle) * 105,
        },
        data: { label: item.name },
        className: "graph-node",
      };
    }),
  ];

  const edges: Edge[] = related.slice(0, 8).map((item, index) => ({
    id: `${entity.urn}-${item.urn}-${index}`,
    source: entity.urn,
    target: item.urn,
    animated: index < 3,
    label: entity.edges[index]?.edge_type || entity.edges[index]?.type || "RELATES_TO",
  }));

  if (!related.length && !entity.edges.length) {
    return <div className="empty-state graph-empty">No edge data was stored for this entity.</div>;
  }

  return (
    <div className="edge-graph" data-testid="edge-graph">
      <ReactFlow nodes={nodes} edges={edges} fitView nodesDraggable={false}>
        <Background gap={18} size={1} />
        <Controls showInteractive={false} />
      </ReactFlow>
    </div>
  );
}
