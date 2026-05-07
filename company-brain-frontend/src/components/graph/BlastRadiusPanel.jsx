// ADR-006 §14: Updated to render risk-sorted nodes with structural risk scores.
// Risk scores and factor breakdowns are populated by the structural layer
// (companybrain/structural/risk.py, ported from tirth8205/code-review-graph MIT License).
// Nodes without a structural scan yet render without a risk badge (graceful fallback).

import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { getBlastRadius } from '../../api/client';
import { X, Zap, Loader, AlertTriangle, ShieldAlert, Info } from 'lucide-react';
import clsx from 'clsx';

// ── Risk badge colours ─────────────────────────────────────────────────────
// Bucketed by risk_score: 0–0.2 (low), 0.2–0.4, 0.4–0.6, 0.6–0.8, 0.8–1.0 (critical)
function riskBucket(score) {
  if (score == null) return null;
  if (score >= 0.8) return 'critical';
  if (score >= 0.6) return 'high';
  if (score >= 0.4) return 'medium';
  if (score >= 0.2) return 'low';
  return 'minimal';
}

const RISK_BADGE = {
  critical: 'bg-red-100 text-red-800 border border-red-300',
  high:     'bg-orange-50 text-orange-700 border border-orange-200',
  medium:   'bg-yellow-50 text-yellow-700 border border-yellow-200',
  low:      'bg-blue-50 text-blue-600 border border-blue-200',
  minimal:  'bg-slate-100 text-slate-500',
};

// ── Factor names for the explainer tooltip ────────────────────────────────
const FACTOR_LABELS = {
  flow:      'Flow criticality',
  community: 'Cross-module calls',
  tests:     'Test coverage gap',
  security:  'Security sensitivity',
  callers:   'Caller count',
};

// ── Per-depth fallback colours (used when risk_score is absent) ───────────
const DEPTH_COLOURS = {
  1: 'bg-slate-100 text-slate-600',
  2: 'bg-yellow-50 text-yellow-700 border border-yellow-200',
  3: 'bg-orange-50 text-orange-700 border border-orange-200',
  4: 'bg-red-50 text-red-700 border border-red-200',
  5: 'bg-red-100 text-red-800 border border-red-300',
};

// ── Risk factor breakdown tooltip ─────────────────────────────────────────
function RiskFactorTooltip({ factors }) {
  if (!factors || Object.keys(factors).length === 0) return null;

  const entries = Object.entries(factors)
    .filter(([, v]) => v > 0)
    .sort(([, a], [, b]) => b - a);

  if (entries.length === 0) return null;

  return (
    <div className="absolute z-10 left-0 mt-1 w-52 bg-white border border-slate-200 rounded-lg shadow-lg p-2 text-xs">
      <p className="font-semibold text-slate-700 mb-1.5">Risk factors</p>
      {entries.map(([key, val]) => (
        <div key={key} className="flex items-center justify-between mb-1">
          <span className="text-slate-500">{FACTOR_LABELS[key] ?? key}</span>
          <div className="flex items-center gap-1.5">
            <div className="w-16 h-1.5 bg-slate-100 rounded-full overflow-hidden">
              <div
                className="h-full bg-red-400 rounded-full"
                style={{ width: `${Math.min(Math.round((val / 0.3) * 100), 100)}%` }}
              />
            </div>
            <span className="font-mono text-slate-600">{val.toFixed(2)}</span>
          </div>
        </div>
      ))}
    </div>
  );
}

// ── Single affected node card ──────────────────────────────────────────────
function NodeCard({ node, depth }) {
  const [showFactors, setShowFactors] = useState(false);
  const bucket = riskBucket(node.riskScore);
  const colourClass = bucket
    ? RISK_BADGE[bucket]
    : (DEPTH_COLOURS[depth] ?? DEPTH_COLOURS[1]);

  return (
    <div className={clsx('mb-2 px-3 py-2 rounded-lg text-xs relative', colourClass)}>
      <div className="font-medium truncate">{node.nodeName}</div>
      <div className="flex items-center justify-between mt-0.5 gap-1 flex-wrap">
        <span className="opacity-70">{node.nodeType}</span>

        <div className="flex items-center gap-1.5 ml-auto">
          {node.riskScore != null && (
            <span className="flex items-center gap-0.5 font-mono font-semibold">
              <ShieldAlert size={10} />
              {node.riskScore.toFixed(2)}
            </span>
          )}

          {node.riskFactors && Object.keys(node.riskFactors).length > 0 && (
            <button
              className="opacity-60 hover:opacity-100 relative"
              onClick={() => setShowFactors(v => !v)}
              aria-label="Show risk factor breakdown"
            >
              <Info size={11} />
              {showFactors && <RiskFactorTooltip factors={node.riskFactors} />}
            </button>
          )}

          {node.owningTeam && (
            <span className="opacity-70">👤 {node.owningTeam}</span>
          )}
        </div>
      </div>

      {node.viaEdgeType && (
        <div className="opacity-60 mt-0.5">via {node.viaEdgeType}</div>
      )}
    </div>
  );
}

// ── Main panel ─────────────────────────────────────────────────────────────
export default function BlastRadiusPanel({ workspaceId, nodeId, nodeName, onClose }) {
  const { data, isLoading } = useQuery({
    queryKey: ['blast-radius', workspaceId, nodeId],
    queryFn: () => getBlastRadius(nodeId),
    enabled: !!nodeId,
  });

  // Nodes come pre-sorted by risk_score DESC from BlastRadiusService (ADR-006 §13).
  const affected = data?.affectedNodes || [];
  const hasRiskScores = affected.some(n => n.riskScore != null);
  const criticalCount = affected.filter(n => n.riskScore != null && n.riskScore >= 0.6).length;

  // Group by depth, preserving risk-sorted order within each group.
  const byDepth = affected.reduce((acc, n) => {
    (acc[n.depth] = acc[n.depth] || []).push(n);
    return acc;
  }, {});

  return (
    <div className="w-72 flex-shrink-0 border-l border-slate-200 bg-white flex flex-col">
      {/* Header */}
      <div className="px-4 py-3 border-b border-slate-100 flex items-center justify-between">
        <div className="flex items-center gap-2 text-sm font-semibold text-slate-800">
          <Zap size={14} className="text-amber-500" />
          Blast Radius
        </div>
        <button onClick={onClose} className="text-slate-400 hover:text-slate-700 p-0.5">
          <X size={14} />
        </button>
      </div>

      <div className="px-4 py-2 bg-slate-50 border-b border-slate-100">
        <p className="text-xs text-slate-500">
          Changing <span className="font-medium text-slate-800">{nodeName}</span> affects:
        </p>
        {hasRiskScores && criticalCount > 0 && (
          <p className="text-xs text-red-600 mt-0.5 flex items-center gap-1">
            <ShieldAlert size={10} />
            {criticalCount} high-risk node{criticalCount > 1 ? 's' : ''} affected
          </p>
        )}
        {hasRiskScores && (
          <p className="text-xs text-slate-400 mt-0.5">Sorted by risk score ↓</p>
        )}
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto">
        {isLoading && (
          <div className="flex items-center justify-center py-12">
            <Loader size={16} className="animate-spin text-slate-400" />
          </div>
        )}

        {!isLoading && affected.length === 0 && (
          <div className="py-8 px-4 text-center text-sm text-slate-400">
            <AlertTriangle size={20} className="mx-auto mb-2 opacity-40" />
            No dependents found yet. Run the context pipeline to map dependencies.
          </div>
        )}

        {!isLoading && Object.entries(byDepth).map(([depth, nodes]) => (
          <div key={depth} className="px-4 py-3 border-b border-slate-50 last:border-0">
            <div className="text-xs text-slate-400 font-medium mb-2">
              {depth === '1' ? 'Direct dependents' : `Hop ${depth}`}
            </div>
            {nodes.map(n => (
              <NodeCard key={n.nodeId} node={n} depth={parseInt(depth, 10)} />
            ))}
          </div>
        ))}
      </div>

      {/* Footer */}
      {data && (
        <div className="px-4 py-2 border-t border-slate-100 text-xs text-slate-400 bg-slate-50 flex items-center justify-between">
          <span>{affected.length} affected nodes · {data.queryDurationMs}ms</span>
          {hasRiskScores && (
            <span className="flex items-center gap-1">
              <ShieldAlert size={10} /> risk scored
            </span>
          )}
        </div>
      )}
    </div>
  );
}
