/**
 * ApiExplorer — endpoint-to-context pipeline launcher.
 *
 * Key features:
 *  • Per-repo branch selection — each repo has its own branch field
 *  • Auto-detect branches — click "Detect" on a local path to fetch real git branches
 *  • Live log stream — polls every 2s and shows each pipeline stage as it progresses
 *  • Rich completion view — files traced, code units, git commits, entities, stage summary
 */

import { useState, useEffect, useRef } from 'react';
import { useQuery, useMutation } from '@tanstack/react-query';
import { startContextPipeline, getPipelineJob } from '../api/client';
import { usePipelineStore } from '../store';
import {
  Plus, Trash2, Play, Loader, CheckCircle, AlertCircle,
  GitBranch, RefreshCw,
} from 'lucide-react';

const DEV_WORKSPACE = '00000000-0000-0000-0000-000000000001';
const REPO_TYPES = ['backend', 'frontend', 'shared'];

// ── Main component ────────────────────────────────────────────────────────────

export default function ApiExplorer() {
  const {
    endpoint, setEndpoint,
    method,   setMethod,
    repos,
    jobId,    setJobId,
    addRepo, removeRepo, updateRepo,
  } = usePipelineStore();

  // Per-repo detected branches — keyed by repo index
  const [repoBranches, setRepoBranches] = useState({});
  const [branchLoading, setBranchLoading] = useState({});

  const fetchBranchesForRepo = async (index, path) => {
    const val = path?.trim();
    if (!val || (!val.startsWith('/') && !val.startsWith('~'))) return;
    setBranchLoading(p => ({ ...p, [index]: true }));
    try {
      const res = await window.fetch(`/ai/repo/branches?local_path=${encodeURIComponent(val)}`);
      const data = await res.json();
      if (!data.error && data.branches?.length) {
        setRepoBranches(p => ({ ...p, [index]: data }));
        // Auto-fill branch if it's still the default placeholder
        const current = repos[index]?.branch;
        if (!current || current === 'main') {
          updateRepo(index, 'branch', data.active || data.branches[0]);
        }
      }
    } catch (_) {}
    setBranchLoading(p => ({ ...p, [index]: false }));
  };

  const buildRepoPayload = (repo) => {
    const val = repo.url.trim();
    const isLocal = val.startsWith('/') || val.startsWith('~') || val.startsWith('./');
    return {
      type: repo.type,
      branch: repo.branch || 'main',
      ...(isLocal ? { local_path: val } : { url: val }),
    };
  };

  const startMutation = useMutation({
    mutationFn: () => {
      setJobId(null);
      return startContextPipeline({
        endpoint_path: endpoint,
        http_method: method,
        branch: 'main',   // global fallback — per-repo branches come from repos[]
        repos: repos.filter(r => r.url.trim()).map(buildRepoPayload),
        workspace_id: DEV_WORKSPACE,
      });
    },
    onSuccess: (data) => setJobId(data.jobId),
  });

  const { data: job } = useQuery({
    queryKey: ['pipeline-job', jobId],
    queryFn: () => getPipelineJob(jobId),
    enabled: !!jobId,
    refetchInterval: (query) =>
      query.state.data?.status === 'running' ? 30_000 : false,
  });

  const isRunning = startMutation.isPending || job?.status === 'running';
  const canStart  = endpoint.trim() && repos.some(r => r.url.trim()) && !isRunning;

  return (
    <div className="max-w-3xl mx-auto p-8">
      <h1 className="text-xl font-semibold text-slate-900 mb-1">API Context Builder</h1>
      <p className="text-sm text-slate-500 mb-8">
        Enter an endpoint and its repos. The pipeline traces handler → service → repository → models
        and builds a dependency knowledge graph.
      </p>

      {/* Endpoint */}
      <section className="mb-6">
        <label className="block text-xs font-medium text-slate-600 mb-2 uppercase tracking-wide">
          API Endpoint
        </label>
        <div className="flex gap-2">
          <select
            value={method}
            onChange={e => setMethod(e.target.value)}
            className="border border-slate-200 rounded-lg px-3 py-2 text-sm bg-white focus:outline-none focus:ring-2 focus:ring-brand-500"
          >
            {['GET','POST','PUT','PATCH','DELETE'].map(m => <option key={m}>{m}</option>)}
          </select>
          <input
            value={endpoint}
            onChange={e => setEndpoint(e.target.value)}
            placeholder="/api/v1/payments/charge"
            className="flex-1 border border-slate-200 rounded-lg px-3 py-2 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-brand-500"
          />
        </div>
      </section>

      {/* Repos */}
      <section className="mb-6">
        <label className="block text-xs font-medium text-slate-600 mb-2 uppercase tracking-wide">
          Repositories
        </label>
        <p className="text-xs text-slate-400 mb-3">
          Paste a local path or GitHub URL. Each repo can be on a different branch —
          click <strong>Detect</strong> on a local path to auto-populate from git.
        </p>
        <div className="flex flex-col gap-3">
          {repos.map((repo, i) => {
            const isLocal = repo.url.trim().startsWith('/') || repo.url.trim().startsWith('~');
            return (
              <RepoRow
                key={i}
                repo={repo}
                isLocal={isLocal}
                detectedBranches={repoBranches[i]}
                branchLoading={!!branchLoading[i]}
                onTypeChange={val => updateRepo(i, 'type', val)}
                onUrlChange={val => updateRepo(i, 'url', val)}
                onUrlBlur={() => { if (isLocal) fetchBranchesForRepo(i, repo.url); }}
                onBranchChange={val => updateRepo(i, 'branch', val)}
                onDetectClick={() => fetchBranchesForRepo(i, repo.url)}
                onRemove={repos.length > 1 ? () => removeRepo(i) : null}
              />
            );
          })}
        </div>
        <button
          onClick={addRepo}
          className="mt-2 flex items-center gap-1.5 text-sm text-brand-600 hover:text-brand-700"
        >
          <Plus size={14} /> Add repo
        </button>
      </section>

      {/* Run button */}
      <div className="flex items-center gap-3">
        <button
          onClick={() => startMutation.mutate()}
          disabled={!canStart}
          className="flex items-center gap-2 px-5 py-2.5 bg-brand-600 text-white rounded-lg text-sm font-medium
                     hover:bg-brand-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
        >
          {isRunning
            ? <><Loader size={14} className="animate-spin" /> Running pipeline…</>
            : <><Play size={14} /> Run Context Pipeline</>
          }
        </button>
        {jobId && !isRunning && (
          <button
            onClick={() => setJobId(null)}
            className="text-xs text-slate-400 hover:text-slate-600 underline"
          >
            Clear
          </button>
        )}
      </div>

      {startMutation.isError && (
        <div className="mt-4 flex items-center gap-2 text-sm text-red-600 bg-red-50 border border-red-200 rounded-lg px-4 py-3">
          <AlertCircle size={14} /> {startMutation.error?.message || 'Pipeline failed to start'}
        </div>
      )}

      {job && (
        <div className="mt-6">
          <JobOutput job={job} />
        </div>
      )}
    </div>
  );
}

// ── Repo row with per-repo branch ─────────────────────────────────────────────

function RepoRow({
  repo, isLocal, detectedBranches, branchLoading,
  onTypeChange, onUrlChange, onUrlBlur, onBranchChange, onDetectClick, onRemove,
}) {
  const branches   = detectedBranches?.branches || [];
  const hasBranches = branches.length > 0;

  return (
    <div className="border border-slate-200 rounded-lg p-3 bg-slate-50/60">
      {/* Path / URL row */}
      <div className="flex gap-2 items-center">
        <select
          value={repo.type}
          onChange={e => onTypeChange(e.target.value)}
          className="border border-slate-200 rounded-lg px-2 py-2 text-sm bg-white focus:outline-none shrink-0"
        >
          {REPO_TYPES.map(t => <option key={t}>{t}</option>)}
        </select>

        <div className="flex-1 relative">
          <input
            value={repo.url}
            onChange={e => onUrlChange(e.target.value)}
            onBlur={onUrlBlur}
            placeholder="/Users/you/my-service  or  https://github.com/org/repo"
            className="w-full border border-slate-200 rounded-lg px-3 py-2 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-brand-500 pr-20"
          />
          {repo.url.trim() && (
            <span className={`absolute right-2 top-1/2 -translate-y-1/2 text-xs px-1.5 py-0.5 rounded font-medium ${
              isLocal ? 'bg-emerald-100 text-emerald-700' : 'bg-blue-100 text-blue-700'
            }`}>
              {isLocal ? '🔒 local' : '☁ remote'}
            </span>
          )}
        </div>

        {onRemove && (
          <button onClick={onRemove} className="text-slate-400 hover:text-red-500 p-1 shrink-0">
            <Trash2 size={14} />
          </button>
        )}
      </div>

      {/* Branch row */}
      <div className="flex items-center gap-2 mt-2">
        <GitBranch size={12} className="text-slate-400 shrink-0" />
        <span className="text-xs text-slate-500 shrink-0 w-12">Branch</span>

        {hasBranches ? (
          <select
            value={repo.branch || 'main'}
            onChange={e => onBranchChange(e.target.value)}
            className="flex-1 border border-slate-200 rounded px-2 py-1 text-xs bg-white focus:outline-none focus:ring-1 focus:ring-brand-500"
          >
            {branches.map(b => <option key={b} value={b}>{b}</option>)}
          </select>
        ) : (
          <input
            value={repo.branch || 'main'}
            onChange={e => onBranchChange(e.target.value)}
            placeholder="main"
            className="flex-1 border border-slate-200 rounded px-2 py-1 text-xs font-mono focus:outline-none focus:ring-1 focus:ring-brand-500"
          />
        )}

        {isLocal && repo.url.trim() && (
          <button
            onClick={onDetectClick}
            disabled={branchLoading}
            title="Auto-detect branches from git"
            className="flex items-center gap-1 text-xs text-brand-600 hover:text-brand-700 border border-brand-200 rounded px-2 py-1 shrink-0 disabled:opacity-50 bg-white"
          >
            {branchLoading
              ? <Loader size={11} className="animate-spin" />
              : <RefreshCw size={11} />
            }
            {hasBranches ? 'Refresh' : 'Detect'}
          </button>
        )}

        {detectedBranches?.active && (
          <span className="text-[11px] text-slate-400 shrink-0 hidden sm:inline">
            HEAD: <span className="font-mono text-slate-600">{detectedBranches.active}</span>
          </span>
        )}
      </div>
    </div>
  );
}

// ── Job output ────────────────────────────────────────────────────────────────

function JobOutput({ job }) {
  const logRef = useRef(null);
  const logs = job?.progress?.logs || [];

  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [logs.length]);

  if (job.status === 'running') {
    return (
      <div className="rounded-lg border border-blue-200 overflow-hidden">
        <div className="bg-blue-50 px-4 py-2 flex items-center gap-2 text-sm text-blue-700 font-medium border-b border-blue-200">
          <Loader size={14} className="animate-spin" /> Pipeline running…
        </div>
        <LogStream logs={logs} logRef={logRef} />
      </div>
    );
  }

  if (job.status === 'failed') {
    return (
      <div className="rounded-lg border border-red-200 overflow-hidden">
        <div className="bg-red-50 px-4 py-2 flex items-center gap-2 text-sm text-red-700 font-medium border-b border-red-200">
          <AlertCircle size={14} /> Pipeline failed
        </div>
        <LogStream logs={logs} logRef={logRef} />
        {job.error && (
          <div className="px-4 py-3 bg-red-50 text-xs text-red-600 font-mono border-t border-red-200">{job.error}</div>
        )}
      </div>
    );
  }

  if (job.status === 'completed') {
    const r = job.result || {};
    return (
      <div className="rounded-lg border border-emerald-200 overflow-hidden">
        <div className="bg-emerald-50 px-4 py-2 flex items-center gap-2 text-sm text-emerald-700 font-medium border-b border-emerald-200">
          <CheckCircle size={14} /> Pipeline complete
        </div>

        {/* Stats grid */}
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-px bg-slate-100">
          {[
            { label: 'Entities',      value: r.entityCount      ?? 0, color: 'emerald' },
            { label: 'Edges',         value: r.edgeCount         ?? 0, color: 'blue'    },
            { label: 'Code units',    value: r.codeUnitsFound   ?? 0, color: 'violet'  },
            { label: 'Git commits',   value: r.gitCommitsFound  ?? 0, color: 'amber'   },
          ].map(({ label, value, color }) => (
            <div key={label} className="bg-white px-4 py-3 text-center">
              <div className={`text-2xl font-bold text-${color}-600`}>{value}</div>
              <div className="text-xs text-slate-500 mt-0.5">{label}</div>
            </div>
          ))}
        </div>

        {/* Files traced */}
        {r.filesTraced?.length > 0 && (
          <div className="px-4 py-3 border-t border-slate-100">
            <div className="text-xs font-semibold text-slate-500 mb-2 uppercase tracking-wide">Files traced by code tracer</div>
            <div className="flex flex-col gap-1">
              {r.filesTraced.map((f, i) => (
                <span key={i} className="text-xs font-mono text-slate-600 bg-slate-50 px-2 py-0.5 rounded border border-slate-100 truncate">
                  {f}
                </span>
              ))}
            </div>
          </div>
        )}

        {/* Stage summary */}
        {r.stagesSummary?.length > 0 && (
          <div className="px-4 py-3 border-t border-slate-100">
            <div className="text-xs font-semibold text-slate-500 mb-2 uppercase tracking-wide">Stage summary</div>
            <div className="flex flex-col gap-1.5">
              {r.stagesSummary.map((s, i) => (
                <div key={i} className="flex items-center gap-2 text-xs">
                  <span className="w-5 h-5 rounded-full bg-emerald-100 text-emerald-700 flex items-center justify-center font-bold text-[10px] shrink-0">
                    {s.stage}
                  </span>
                  <span className="font-medium text-slate-700 w-36 shrink-0">{s.label}</span>
                  <span className="text-slate-400">
                    {s.entities   != null && `${s.entities} entities`}
                    {s.edges      != null && `${s.edges} edges`}
                    {s.contexts   != null && `${s.contexts} contexts`}
                    {s.gaps       != null && `${s.gaps} gaps`}
                    {s.commits    != null && `${s.commits} commits in ${s.clusters ?? '?'} clusters`}
                    {s.code_units != null && `${s.code_units} code units`}
                    {s.skipped    && `skipped — ${s.reason ?? ''}`}
                    {s.status === 'done' && 'done'}
                  </span>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Log collapsible */}
        <details className="border-t border-slate-100">
          <summary className="px-4 py-2 text-xs text-slate-400 cursor-pointer hover:text-slate-600 select-none">
            Show pipeline logs ({logs.length} entries)
          </summary>
          <LogStream logs={logs} logRef={logRef} maxH="max-h-64" />
        </details>

        <div className="px-4 py-3 bg-emerald-50 border-t border-emerald-100">
          <p className="text-xs text-emerald-700">
            Knowledge graph ready — head to <strong>Service Map</strong> or <strong>Ask AI</strong> to explore.
          </p>
        </div>
      </div>
    );
  }

  return null;
}

// ── Log stream ────────────────────────────────────────────────────────────────

function LogStream({ logs, logRef, maxH = 'max-h-80' }) {
  if (!logs.length) {
    return (
      <div className="h-16 flex items-center justify-center bg-slate-900 text-xs text-slate-500 font-mono">
        Waiting…
      </div>
    );
  }
  return (
    <div ref={logRef} className={`${maxH} overflow-y-auto bg-slate-900 px-4 py-3 font-mono text-xs space-y-1`}>
      {logs.map((entry, i) => <LogEntry key={i} entry={entry} />)}
    </div>
  );
}

function LogEntry({ entry }) {
  const ts = entry.ts ? new Date(entry.ts).toLocaleTimeString() : '';
  const isError   = entry.stage === 'error' || entry.emoji === '❌';
  const isWarning = entry.emoji === '⚠️';
  const isSuccess = entry.emoji === '✅' || entry.emoji === '🎉';

  const textColor = isError ? 'text-red-400' : isWarning ? 'text-amber-400' : isSuccess ? 'text-emerald-400' : 'text-slate-300';

  // Extra detail shown inline (files found, units list)
  const detail = entry.units
    ? entry.units.slice(0, 4).join(', ') + (entry.units.length > 4 ? ` +${entry.units.length - 4}` : '')
    : entry.files?.length
    ? entry.files.slice(0, 3).join(', ') + (entry.files.length > 3 ? ` +${entry.files.length - 3}` : '')
    : entry.entities?.length
    ? entry.entities.slice(0, 3).join(', ') + (entry.entities.length > 3 ? ` +${entry.entities.length - 3}` : '')
    : '';

  return (
    <div className={`flex gap-2 leading-relaxed ${textColor}`}>
      <span className="text-slate-600 shrink-0 select-none tabular-nums">{ts}</span>
      <span className="shrink-0">{entry.emoji}</span>
      <span className="shrink-0 text-slate-500 w-6">[{entry.stage}]</span>
      <span className="flex-1">{entry.message}</span>
      {detail && <span className="text-slate-600 truncate max-w-xs">{detail}</span>}
    </div>
  );
}
