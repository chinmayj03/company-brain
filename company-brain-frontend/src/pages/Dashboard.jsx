import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { searchNodes } from '../api/client';
import DependencyGraph from '../components/graph/DependencyGraph';
import FlowView from '../components/graph/FlowView';
import BlastRadiusPanel from '../components/graph/BlastRadiusPanel';
import { Search, Info, GitMerge, Workflow } from 'lucide-react';

const DEV_WORKSPACE = '00000000-0000-0000-0000-000000000001';

const VIEW_MODES = [
  { id: 'graph', label: 'Dependency',  Icon: GitMerge  },
  { id: 'flow',  label: 'Call Flow',   Icon: Workflow  },
];

export default function Dashboard() {
  const [selectedNode, setSelectedNode] = useState(null);
  const [searchQ, setSearchQ] = useState('');
  const [debouncedQ, setDebouncedQ] = useState('');
  const [viewMode, setViewMode] = useState('flow');

  // Debounce search input
  const handleSearch = (v) => {
    setSearchQ(v);
    clearTimeout(window._cbSearchTimer);
    window._cbSearchTimer = setTimeout(() => setDebouncedQ(v), 300);
  };

  const { data: searchResults } = useQuery({
    queryKey: ['search', debouncedQ],
    queryFn: () => searchNodes(debouncedQ, { limit: 8 }),
    enabled: debouncedQ.length > 1,
  });

  return (
    <div className="flex h-full">
      {/* Left panel: search + node list */}
      <div className="w-72 flex-shrink-0 border-r border-slate-200 bg-white flex flex-col">
        <div className="p-4 border-b border-slate-100">
          <h1 className="text-sm font-semibold text-slate-800 mb-3">Service Map</h1>
          <div className="relative">
            <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
            <input
              value={searchQ}
              onChange={e => handleSearch(e.target.value)}
              placeholder="Search services, endpoints…"
              className="w-full pl-8 pr-3 py-2 text-sm border border-slate-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-brand-500"
            />
          </div>
        </div>

        <div className="flex-1 overflow-y-auto">
          {searchResults?.nodes?.length > 0 ? (
            <div className="p-2">
              <p className="text-xs text-slate-400 px-2 py-1">Results</p>
              {searchResults.nodes.map(node => (
                <button
                  key={node.id}
                  onClick={() => setSelectedNode(node)}
                  className={`w-full text-left px-3 py-2.5 rounded-lg mb-1 transition-colors ${
                    selectedNode?.id === node.id
                      ? 'bg-brand-50 border border-brand-200'
                      : 'hover:bg-slate-50'
                  }`}
                >
                  <div className="text-sm font-medium text-slate-800 truncate">{node.name}</div>
                  <div className="text-xs text-slate-400 mt-0.5">{node.nodeType}</div>
                </button>
              ))}
            </div>
          ) : (
            <div className="p-6 text-center text-sm text-slate-400">
              <Info size={20} className="mx-auto mb-2 opacity-50" />
              {debouncedQ
                ? 'No nodes found. Run the API Explorer pipeline first.'
                : 'Search to find services, endpoints, and components.'}
            </div>
          )}
        </div>
      </div>

      {/* Center: graph canvas */}
      <div className="flex-1 relative bg-slate-50 flex flex-col">
        {/* View toggle — shown only when a node is selected */}
        {selectedNode && (
          <div className="flex items-center gap-1 px-3 py-2 border-b border-slate-200 bg-white shrink-0">
            <span className="text-xs text-slate-500 mr-2 font-medium truncate max-w-xs">
              {selectedNode.name}
            </span>
            <div className="ml-auto flex gap-1">
              {VIEW_MODES.map(({ id, label, Icon }) => (
                <button
                  key={id}
                  onClick={() => setViewMode(id)}
                  className={`flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium transition-colors ${
                    viewMode === id
                      ? 'bg-brand-600 text-white'
                      : 'bg-slate-100 text-slate-600 hover:bg-slate-200'
                  }`}
                >
                  <Icon size={12} />
                  {label}
                </button>
              ))}
            </div>
          </div>
        )}

        <div className="flex-1 relative">
          {selectedNode ? (
            viewMode === 'flow' ? (
              <FlowView
                nodeId={selectedNode.id}
                nodeName={selectedNode.name}
              />
            ) : (
              <DependencyGraph
                workspaceId={DEV_WORKSPACE}
                nodeId={selectedNode.id}
                nodeName={selectedNode.name}
              />
            )
          ) : (
            <div className="absolute inset-0 flex items-center justify-center">
              <div className="text-center text-slate-400">
                <div className="text-4xl mb-3">⬡</div>
                <p className="text-sm">Search and select a node to visualize its call chain</p>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Right panel: blast radius */}
      {selectedNode && (
        <BlastRadiusPanel
          workspaceId={DEV_WORKSPACE}
          nodeId={selectedNode.id}
          nodeName={selectedNode.name}
          onClose={() => setSelectedNode(null)}
        />
      )}
    </div>
  );
}
