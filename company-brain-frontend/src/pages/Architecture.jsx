// ADR-006 Week 4: Architecture tab — hubs, bridges, and execution flows.
// Shows the structural skeleton of the workspace: which nodes are most
// connected (hubs), which are structural chokepoints (bridges), and which
// execution paths exist (flows with criticality scores).

import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { getHubs, getBridges, getFlows, getFlow } from '../api/client';
import {
  Network, GitMerge, Zap, AlertTriangle, ChevronDown, ChevronRight,
  Loader, Info,
} from 'lucide-react';
import clsx from 'clsx';

// ── Shared workspace ID (from JWT / env for dev) ───────────────────────────
const WORKSPACE_ID = import.meta.env.VITE_WORKSPACE_ID || 'demo';

// ── Risk colour helper (shared with BlastRadiusPanel) ─────────────────────
function riskColour(score) {
  if (score == null) return 'text-slate-400';
  if (score >= 0.8)  return 'text-red-600 font-semibold';
  if (score >= 0.6)  return 'text-orange-500';
  if (score >= 0.4)  return 'text-yellow-600';
  return 'text-slate-500';
}

function riskBadge(score) {
  if (score == null) return null;
  const label = score >= 0.8 ? 'critical' : score >= 0.6 ? 'high' : score >= 0.4 ? 'medium' : 'low';
  const cls = {
    critical: 'bg-red-100 text-red-700 border border-red-300',
    high:     'bg-orange-50 text-orange-600 border border-orange-200',
    medium:   'bg-yellow-50 text-yellow-700 border border-yellow-200',
    low:      'bg-slate-100 text-slate-500',
  }[label];
  return <span className={clsx('text-xs px-1.5 py-0.5 rounded font-medium', cls)}>{label}</span>;
}

// ── Criticality bar ────────────────────────────────────────────────────────
function CriticalityBar({ value }) {
  const pct = Math.round((value ?? 0) * 100);
  const colour = value >= 0.7 ? 'bg-red-500' : value >= 0.4 ? 'bg-orange-400' : 'bg-emerald-400';
  return (
    <div className="flex items-center gap-2">
      <div className="w-24 h-1.5 rounded-full bg-slate-200 overflow-hidden">
        <div className={clsx('h-full rounded-full', colour)} style={{ width: `${pct}%` }} />
      </div>
      <span className="text-xs text-slate-500 tabular-nums">{pct}%</span>
    </div>
  );
}

// ── Section wrapper ────────────────────────────────────────────────────────
function Section({ icon: Icon, title, colour = 'text-brand-600', children, count }) {
  return (
    <section className="bg-white rounded-xl border border-slate-200 overflow-hidden">
      <header className="flex items-center justify-between px-5 py-3.5 border-b border-slate-100">
        <div className="flex items-center gap-2">
          <Icon size={16} className={colour} />
          <span className="font-semibold text-sm text-slate-800">{title}</span>
          {count != null && (
            <span className="text-xs text-slate-400 bg-slate-100 px-2 py-0.5 rounded-full">
              {count}
            </span>
          )}
        </div>
      </header>
      <div className="p-4">{children}</div>
    </section>
  );
}

// ── Empty / loading states ─────────────────────────────────────────────────
function Loading() {
  return (
    <div className="flex items-center gap-2 text-slate-400 py-6 justify-center">
      <Loader size={16} className="animate-spin" />
      <span className="text-sm">Loading…</span>
    </div>
  );
}

function Empty({ msg = 'No data yet. Run the structural indexer first.' }) {
  return (
    <div className="flex items-center gap-2 text-slate-400 py-6 justify-center text-sm">
      <Info size={14} />
      {msg}
    </div>
  );
}

// ── Hub nodes panel ────────────────────────────────────────────────────────
function HubsPanel() {
  const { data, isLoading } = useQuery({
    queryKey: ['hubs', WORKSPACE_ID],
    queryFn: () => getHubs(WORKSPACE_ID, 20),
    staleTime: 5 * 60_000,
  });

  const hubs = data?.hubs ?? [];

  return (
    <Section icon={Network} title="Hub Nodes" colour="text-blue-600" count={hubs.length}>
      {isLoading ? <Loading /> : hubs.length === 0 ? <Empty /> : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs text-slate-500 border-b border-slate-100">
                <th className="pb-2 font-medium pr-4">Rank</th>
                <th className="pb-2 font-medium pr-4">Node</th>
                <th className="pb-2 font-medium pr-4">Type</th>
                <th className="pb-2 font-medium pr-4">Degree</th>
                <th className="pb-2 font-medium">Risk</th>
              </tr>
            </thead>
            <tbody>
              {hubs.map((hub, i) => (
                <tr key={hub.nodeId} className="border-b border-slate-50 hover:bg-slate-50">
                  <td className="py-2 pr-4 text-slate-400 tabular-nums">#{i + 1}</td>
                  <td className="py-2 pr-4">
                    <span className="font-mono text-xs text-slate-800 truncate max-w-xs block">
                      {hub.nodeName}
                    </span>
                  </td>
                  <td className="py-2 pr-4">
                    <span className="text-xs bg-slate-100 text-slate-600 px-1.5 py-0.5 rounded">
                      {hub.nodeType ?? '—'}
                    </span>
                  </td>
                  <td className="py-2 pr-4 text-slate-600 tabular-nums">{hub.degree ?? '—'}</td>
                  <td className="py-2">{riskBadge(hub.riskScore)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Section>
  );
}

// ── Bridge nodes panel ────────────────────────────────────────────────────
function BridgesPanel() {
  const { data, isLoading } = useQuery({
    queryKey: ['bridges', WORKSPACE_ID],
    queryFn: () => getBridges(WORKSPACE_ID, 10),
    staleTime: 5 * 60_000,
  });

  const bridges = data?.bridges ?? [];

  return (
    <Section icon={GitMerge} title="Bridge Nodes" colour="text-purple-600" count={bridges.length}>
      {isLoading ? <Loading /> : bridges.length === 0 ? (
        <Empty msg="No bridge data yet. The nightly topology job has not run." />
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs text-slate-500 border-b border-slate-100">
                <th className="pb-2 font-medium pr-4">Rank</th>
                <th className="pb-2 font-medium pr-4">Node</th>
                <th className="pb-2 font-medium pr-4">Betweenness</th>
                <th className="pb-2 font-medium">Risk</th>
              </tr>
            </thead>
            <tbody>
              {bridges.map((b, i) => (
                <tr key={b.nodeId} className="border-b border-slate-50 hover:bg-slate-50">
                  <td className="py-2 pr-4 text-slate-400 tabular-nums">#{i + 1}</td>
                  <td className="py-2 pr-4">
                    <span className="font-mono text-xs text-slate-800 truncate max-w-xs block">
                      {b.nodeName}
                    </span>
                  </td>
                  <td className="py-2 pr-4 text-slate-600 tabular-nums">
                    {b.betweenness != null ? (b.betweenness * 100).toFixed(1) + '%' : '—'}
                  </td>
                  <td className="py-2">{riskBadge(b.riskScore)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Section>
  );
}

// ── Expandable flow row ────────────────────────────────────────────────────
function FlowRow({ flow, workspaceId }) {
  const [open, setOpen] = useState(false);

  const { data, isLoading } = useQuery({
    queryKey: ['flow', workspaceId, flow.id],
    queryFn: () => getFlow(workspaceId, flow.id),
    enabled: open,
    staleTime: 5 * 60_000,
  });

  const nodes = data?.nodes ?? [];

  return (
    <>
      <tr
        className="border-b border-slate-50 hover:bg-slate-50 cursor-pointer"
        onClick={() => setOpen(o => !o)}
      >
        <td className="py-2.5 pr-4 pl-1">
          {open
            ? <ChevronDown size={14} className="text-slate-400" />
            : <ChevronRight size={14} className="text-slate-400" />}
        </td>
        <td className="py-2.5 pr-4 font-mono text-xs text-slate-800">{flow.name}</td>
        <td className="py-2.5 pr-4">
          <CriticalityBar value={flow.criticality} />
        </td>
        <td className="py-2.5 pr-4 text-slate-500 tabular-nums text-xs">{flow.nodeCount}</td>
        <td className="py-2.5 pr-4 text-slate-500 tabular-nums text-xs">{flow.fileCount}</td>
        <td className="py-2.5 text-slate-500 tabular-nums text-xs">{flow.depth}</td>
      </tr>
      {open && (
        <tr>
          <td colSpan={6} className="pb-3 pl-6 pr-4">
            {isLoading ? (
              <Loading />
            ) : (
              <ol className="border-l-2 border-slate-200 ml-2 space-y-1 mt-1">
                {nodes.map((n, idx) => (
                  <li key={n.nodeId} className="pl-4 relative">
                    <span className="absolute -left-[9px] top-[5px] w-3.5 h-3.5 rounded-full bg-white border-2 border-slate-300 flex items-center justify-center">
                      <span className="text-[8px] text-slate-500">{idx + 1}</span>
                    </span>
                    <span className="font-mono text-xs text-slate-700">{n.nodeName}</span>
                    {n.riskScore != null && (
                      <span className={clsx('ml-2 text-xs', riskColour(n.riskScore))}>
                        ▲{(n.riskScore * 100).toFixed(0)}
                      </span>
                    )}
                  </li>
                ))}
              </ol>
            )}
          </td>
        </tr>
      )}
    </>
  );
}

// ── Flows panel ────────────────────────────────────────────────────────────
function FlowsPanel() {
  const { data, isLoading } = useQuery({
    queryKey: ['flows', WORKSPACE_ID],
    queryFn: () => getFlows(WORKSPACE_ID, { minCriticality: 0 }),
    staleTime: 5 * 60_000,
  });

  const flows = (data?.flows ?? []).sort((a, b) => (b.criticality ?? 0) - (a.criticality ?? 0));

  return (
    <Section icon={Zap} title="Execution Flows" colour="text-amber-500" count={flows.length}>
      {isLoading ? <Loading /> : flows.length === 0 ? (
        <Empty msg="No flows detected yet. The structural indexer must run flow detection first." />
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs text-slate-500 border-b border-slate-100">
                <th className="pb-2 pl-1 w-6" />
                <th className="pb-2 font-medium pr-4">Flow (entry-point)</th>
                <th className="pb-2 font-medium pr-4">Criticality</th>
                <th className="pb-2 font-medium pr-4">Nodes</th>
                <th className="pb-2 font-medium pr-4">Files</th>
                <th className="pb-2 font-medium">Depth</th>
              </tr>
            </thead>
            <tbody>
              {flows.map(flow => (
                <FlowRow key={flow.id} flow={flow} workspaceId={WORKSPACE_ID} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Section>
  );
}

// ── Page ───────────────────────────────────────────────────────────────────
export default function Architecture() {
  return (
    <div className="p-6 max-w-6xl mx-auto space-y-6">
      {/* Header */}
      <div>
        <h1 className="text-xl font-bold text-slate-900 flex items-center gap-2">
          <Network size={20} className="text-brand-600" />
          Architecture
        </h1>
        <p className="text-sm text-slate-500 mt-1">
          Structural topology: hub nodes, bridge chokepoints, and execution flows
          computed by the structural indexer.
        </p>
      </div>

      {/* Warning if no workspace configured */}
      {WORKSPACE_ID === 'demo' && (
        <div className="flex items-start gap-3 bg-amber-50 border border-amber-200 rounded-lg p-3 text-sm text-amber-800">
          <AlertTriangle size={16} className="mt-0.5 flex-shrink-0" />
          <span>
            <strong>Demo mode.</strong> Set <code className="bg-amber-100 px-1 rounded">VITE_WORKSPACE_ID</code> in
            your <code className="bg-amber-100 px-1 rounded">.env</code> to connect to a real workspace.
          </span>
        </div>
      )}

      {/* Panels */}
      <HubsPanel />
      <BridgesPanel />
      <FlowsPanel />
    </div>
  );
}
