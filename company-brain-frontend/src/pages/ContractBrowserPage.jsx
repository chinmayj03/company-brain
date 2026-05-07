import { useState, useEffect } from 'react';
import { getContractForEndpoint, listEndpointsImplementingContract, getDriftSignals } from '../api/trpc-client';

// ── Severity helpers ──────────────────────────────────────────────────────────

const SEVERITY_CONFIG = {
  breaking: { label: 'Breaking', dot: '🔴', bg: 'bg-red-100',    text: 'text-red-800',    border: 'border-red-200'    },
  warning:  { label: 'Warning',  dot: '🟡', bg: 'bg-yellow-100', text: 'text-yellow-800', border: 'border-yellow-200' },
  info:     { label: 'Info',     dot: '🔵', bg: 'bg-blue-100',   text: 'text-blue-800',   border: 'border-blue-200'   },
};

const METHOD_COLORS = {
  GET:    'bg-green-100 text-green-800',
  POST:   'bg-blue-100 text-blue-800',
  PUT:    'bg-yellow-100 text-yellow-800',
  DELETE: 'bg-red-100 text-red-800',
  PATCH:  'bg-purple-100 text-purple-800',
};

const HTTP_METHODS = ['GET', 'POST', 'PUT', 'DELETE', 'PATCH'];
const DRIFT_TABS   = ['All', 'Breaking', 'Warning', 'Info'];

// ── Main page ─────────────────────────────────────────────────────────────────

export default function ContractBrowserPage() {
  // Contract search state
  const [endpointPath,  setEndpointPath]  = useState('');
  const [method,        setMethod]        = useState('GET');
  const [contract,      setContract]      = useState(null);
  const [implementations, setImplementations] = useState(null);
  const [contractLoading,  setContractLoading]  = useState(false);
  const [contractError,    setContractError]    = useState(null);

  // Drift signals state
  const [driftSignals,  setDriftSignals]  = useState(null);
  const [driftLoading,  setDriftLoading]  = useState(false);
  const [driftError,    setDriftError]    = useState(null);
  const [activeTab,     setActiveTab]     = useState('All');

  // Load drift signals on mount
  useEffect(() => {
    async function loadDrift() {
      setDriftLoading(true);
      setDriftError(null);
      try {
        const data = await getDriftSignals({});
        setDriftSignals(data?.signals ?? data ?? []);
      } catch (err) {
        setDriftError(err.message ?? 'Failed to load drift signals');
      } finally {
        setDriftLoading(false);
      }
    }
    loadDrift();
  }, []);

  // ── Contract search ─────────────────────────────────────────────────────────

  async function handleSearch(e) {
    e.preventDefault();
    if (!endpointPath.trim()) return;

    setContractLoading(true);
    setContractError(null);
    setContract(null);
    setImplementations(null);

    try {
      const result = await getContractForEndpoint({ path: endpointPath.trim(), method });
      setContract(result);

      if (result?.operationId) {
        const impls = await listEndpointsImplementingContract({ operationId: result.operationId });
        setImplementations(impls?.endpoints ?? impls ?? []);
      }
    } catch (err) {
      setContractError(err.message ?? 'Failed to fetch contract');
    } finally {
      setContractLoading(false);
    }
  }

  // ── Drift filter ────────────────────────────────────────────────────────────

  const filteredDrift = Array.isArray(driftSignals)
    ? activeTab === 'All'
      ? driftSignals
      : driftSignals.filter(s => s.severity?.toLowerCase() === activeTab.toLowerCase())
    : [];

  // ── Render ──────────────────────────────────────────────────────────────────

  return (
    <div className="p-6 space-y-8 max-w-5xl mx-auto">
      <div>
        <h1 className="text-xl font-semibold text-slate-900">Contract Browser</h1>
        <p className="text-sm text-slate-500 mt-1">
          Explore API contracts, implementations, and active drift signals.
        </p>
      </div>

      {/* ── Contract Search ── */}
      <section className="bg-white rounded-xl border border-slate-200 p-5 shadow-sm">
        <h2 className="text-sm font-semibold text-slate-800 mb-4">Search Endpoint Contract</h2>

        <form onSubmit={handleSearch} className="flex gap-3">
          <select
            value={method}
            onChange={e => setMethod(e.target.value)}
            className="flex-shrink-0 px-3 py-2 text-sm border border-slate-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-brand-500 bg-white"
          >
            {HTTP_METHODS.map(m => (
              <option key={m} value={m}>{m}</option>
            ))}
          </select>

          <input
            type="text"
            value={endpointPath}
            onChange={e => setEndpointPath(e.target.value)}
            placeholder="/api/v1/users/{id}"
            className="flex-1 px-3 py-2 text-sm border border-slate-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-brand-500"
          />

          <button
            type="submit"
            disabled={contractLoading || !endpointPath.trim()}
            className="px-4 py-2 text-sm font-medium bg-brand-600 text-white rounded-lg hover:bg-brand-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            {contractLoading ? 'Searching…' : 'Search'}
          </button>
        </form>

        {contractError && (
          <div className="mt-4 p-3 bg-red-50 border border-red-200 rounded-lg text-sm text-red-700">
            {contractError}
          </div>
        )}

        {contractLoading && (
          <div className="mt-4 flex items-center gap-2 text-sm text-slate-500">
            <span className="animate-spin">⟳</span> Loading contract…
          </div>
        )}

        {/* Contract card */}
        {contract && !contractLoading && (
          <div className="mt-5 space-y-4">
            <ContractCard contract={contract} method={method} path={endpointPath} />

            {/* Implementations */}
            {implementations !== null && (
              <div className="mt-4">
                <h3 className="text-xs font-semibold text-slate-600 uppercase tracking-wide mb-2">
                  Implementations
                </h3>
                {Array.isArray(implementations) && implementations.length > 0 ? (
                  <ul className="space-y-1">
                    {implementations.map((impl, i) => (
                      <li
                        key={i}
                        className="flex items-center gap-2 px-3 py-2 bg-slate-50 rounded-lg text-sm text-slate-700 border border-slate-100"
                      >
                        <span className="text-slate-400">→</span>
                        <span className="font-mono text-xs">{impl.filePath ?? impl.urn ?? impl}</span>
                        {impl.handlerName && (
                          <span className="text-slate-500">· {impl.handlerName}</span>
                        )}
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="text-sm text-slate-400 italic">No implementations found in the graph.</p>
                )}
              </div>
            )}
          </div>
        )}

        {contract === null && !contractLoading && endpointPath && !contractError && (
          <p className="mt-4 text-sm text-slate-400 italic">
            No contract found for {method} {endpointPath}.
          </p>
        )}
      </section>

      {/* ── Active Drift Signals ── */}
      <section className="bg-white rounded-xl border border-slate-200 p-5 shadow-sm">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-sm font-semibold text-slate-800">Active Drift Signals</h2>
          {Array.isArray(driftSignals) && (
            <span className="text-xs text-slate-500">{driftSignals.length} total</span>
          )}
        </div>

        {/* Severity tabs */}
        <div className="flex gap-1 mb-4 border-b border-slate-100 pb-3">
          {DRIFT_TABS.map(tab => (
            <button
              key={tab}
              onClick={() => setActiveTab(tab)}
              className={`px-3 py-1.5 text-xs font-medium rounded-md transition-colors ${
                activeTab === tab
                  ? 'bg-brand-600 text-white'
                  : 'text-slate-600 hover:bg-slate-100'
              }`}
            >
              {tab}
              {tab !== 'All' && Array.isArray(driftSignals) && (
                <span className="ml-1.5 opacity-75">
                  ({driftSignals.filter(s => s.severity?.toLowerCase() === tab.toLowerCase()).length})
                </span>
              )}
            </button>
          ))}
        </div>

        {driftError && (
          <div className="p-3 bg-red-50 border border-red-200 rounded-lg text-sm text-red-700">
            {driftError}
          </div>
        )}

        {driftLoading && (
          <div className="flex items-center gap-2 text-sm text-slate-500">
            <span className="animate-spin">⟳</span> Loading drift signals…
          </div>
        )}

        {!driftLoading && !driftError && filteredDrift.length === 0 && (
          <div className="py-8 text-center text-sm text-slate-400">
            {activeTab === 'All'
              ? 'No active drift signals — everything looks consistent.'
              : `No ${activeTab.toLowerCase()} drift signals.`}
          </div>
        )}

        {!driftLoading && filteredDrift.length > 0 && (
          <div className="space-y-3">
            {filteredDrift.map((signal, i) => (
              <DriftSignalCard key={signal.urn ?? i} signal={signal} />
            ))}
          </div>
        )}
      </section>
    </div>
  );
}

// ── Sub-components ────────────────────────────────────────────────────────────

function ContractCard({ contract, method, path }) {
  const methodColor = METHOD_COLORS[method] ?? 'bg-slate-100 text-slate-700';

  return (
    <div className="rounded-lg border border-slate-200 p-4 bg-slate-50">
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-center gap-2 flex-wrap">
          <span className={`px-2 py-0.5 text-xs font-semibold rounded ${methodColor}`}>
            {method}
          </span>
          <code className="text-sm font-mono text-slate-700">{path}</code>
          {contract.deprecated && (
            <span className="px-2 py-0.5 text-xs font-semibold rounded bg-orange-100 text-orange-800 border border-orange-200">
              DEPRECATED
            </span>
          )}
        </div>
        {contract.operationId && (
          <span className="text-xs font-mono text-slate-400 flex-shrink-0">{contract.operationId}</span>
        )}
      </div>

      {contract.summary && (
        <p className="mt-2 text-sm text-slate-700">{contract.summary}</p>
      )}

      {Array.isArray(contract.tags) && contract.tags.length > 0 && (
        <div className="mt-3 flex flex-wrap gap-1.5">
          {contract.tags.map(tag => (
            <span
              key={tag}
              className="px-2 py-0.5 text-xs rounded-full bg-white border border-slate-200 text-slate-600"
            >
              {tag}
            </span>
          ))}
        </div>
      )}

      {contract.description && (
        <p className="mt-3 text-xs text-slate-500 leading-relaxed">{contract.description}</p>
      )}
    </div>
  );
}

function DriftSignalCard({ signal }) {
  const cfg = SEVERITY_CONFIG[signal.severity?.toLowerCase()] ?? SEVERITY_CONFIG.info;

  return (
    <div className={`rounded-lg border p-4 ${cfg.bg} ${cfg.border}`}>
      <div className="flex items-center gap-2 mb-1">
        <span>{cfg.dot}</span>
        <span className={`text-xs font-semibold uppercase tracking-wide ${cfg.text}`}>
          {cfg.label}
        </span>
        {signal.urn && (
          <span className="ml-auto text-xs font-mono text-slate-400 truncate max-w-xs">
            {signal.urn}
          </span>
        )}
      </div>

      <p className={`text-sm ${cfg.text} mt-1`}>{signal.description}</p>

      {Array.isArray(signal.detectedFields) && signal.detectedFields.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1.5">
          <span className="text-xs text-slate-500 mr-1">Fields:</span>
          {signal.detectedFields.map(f => (
            <span
              key={f}
              className="px-1.5 py-0.5 text-xs font-mono rounded bg-white border border-slate-200 text-slate-600"
            >
              {f}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
