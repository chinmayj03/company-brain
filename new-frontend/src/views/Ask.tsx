import { useState, useMemo, useEffect, useRef, useCallback } from 'react';
import Sidebar from '../components/Sidebar';
import TopBar from '../components/TopBar';
import Suggested from '../components/Suggested';
import BlastRadiusGraph from '../components/BlastRadiusGraph';
import AffectedBreakdownList from '../components/AffectedBreakdownList';
import CitationList from '../components/CitationList';
import AnswerMarkdown from '../components/AnswerMarkdown';
import CaveatBanner from '../components/CaveatBanner';
import RiskAlertBanner from '../components/RiskAlertBanner';
import CallChainTree from '../components/CallChainTree';
import OnboardingPaths from '../components/OnboardingPaths';
import { extractCitations, type LiveCitation } from '../data/brain_client';
import TimeTravel from '../components/TimeTravel';
import Compare from '../components/Compare';
import MCP from '../components/MCP';
import { useFlags } from '../data/feature_flags';
import {
  queryBrain,
  queryBrainStream,
  entitiesToGraphNodes,
  riskToStats,
  getEntityOwners,
  type QueryResponse,
  type AffectedEntity,
  type RiskAssessment,
  type EntityOwner,
} from '../data/brain_client';
import {
  timeTravelStates,
  owners as MOCK_OWNERS,
  graph as MOCK_GRAPH,
  events,
  type GraphNode,
  type TimeTravelState,
} from '../data/mock_fallback';
import { useRepoStore } from '../store/repo_store';
import { useWorkspaceStore } from '../store/workspace_store';

// ── Shared answer shape (union of mock + live data) ───────────────────────────

interface AnswerState {
  summary: string;
  verdictStats?: { affected: number; dirs: number; teams: number; risk: 'LOW' | 'MED' | 'HIGH' };
  verdictNote?: string;
  graphNodes?: GraphNode[];
  confidence?: string;
  followUps?: string[];
  isStreaming: boolean;
  error?: string;
  // pipeline fields
  caveats?: string[];
  riskAlerts?: unknown[];
  callChain?: unknown[];
  domainEntities?: unknown[];
  onboardingPaths?: unknown[];
  liveCitations?: LiveCitation[];
  affectedEntities?: AffectedEntity[];
}

// ── Icons (inline SVG, no external dep) ──────────────────────────────────────

const IconBolt = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" style={{ width: 13, height: 13 }}>
    <polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/>
  </svg>
);
const IconGit2 = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" style={{ width: 13, height: 13 }}>
    <circle cx="12" cy="6" r="2"/><circle cx="6" cy="18" r="2"/><circle cx="18" cy="18" r="2"/>
    <path d="M12 8v6"/><path d="M12 14a6 6 0 0 0-6 4M12 14a6 6 0 0 1 6 4"/>
  </svg>
);
const IconCopy = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" style={{ width: 14, height: 14 }}>
    <rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>
  </svg>
);
const IconShare = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" style={{ width: 14, height: 14 }}>
    <circle cx="18" cy="5" r="3"/><circle cx="6" cy="12" r="3"/><circle cx="18" cy="19" r="3"/>
    <line x1="8.6" y1="13.5" x2="15.4" y2="17.5"/><line x1="15.4" y1="6.5" x2="8.6" y2="10.5"/>
  </svg>
);
const IconFlow = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" style={{ width: 14, height: 14 }}>
    <circle cx="5" cy="6" r="2"/><circle cx="19" cy="12" r="2"/><circle cx="5" cy="18" r="2"/>
    <path d="M7 6h6a2 2 0 0 1 2 2v0"/><path d="M7 18h6a2 2 0 0 0 2-2v0"/>
  </svg>
);
const IconThumbUp = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" style={{ width: 14, height: 14 }}>
    <path d="M7 10v12"/><path d="M15 5.88 14 10h5.83a2 2 0 0 1 1.92 2.56l-2.33 8A2 2 0 0 1 17.5 22H7"/>
  </svg>
);
const IconThumbDown = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" style={{ width: 14, height: 14 }}>
    <path d="M17 14V2"/><path d="M9 18.12 10 14H4.17a2 2 0 0 1-1.92-2.56l2.33-8A2 2 0 0 1 6.5 2H17"/>
  </svg>
);
const IconRisk = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" style={{ width: 14, height: 14 }}>
    <path d="M12 9v4"/><path d="M12 17h.01"/>
    <path d="M10.3 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.7 3.86a2 2 0 0 0-3.4 0z"/>
  </svg>
);
const IconADR = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" style={{ width: 14, height: 14 }}>
    <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
    <path d="M14 2v6h6"/><path d="M9 14l2 2 4-4"/>
  </svg>
);
const OWNER_COLORS: Record<string, string> = { JM: '#2E5C8A', PA: '#C8553D', SK: '#588B6F' };

// ── StreamingSkeleton — shown while SSE stream is in-flight ──────────────────

function StreamingSkeleton() {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8, padding: '4px 0 12px' }}>
      {[100, 82, 90, 60].map((w, i) => (
        <div key={i} style={{
          height: 14, borderRadius: 4, width: `${w}%`,
          background: 'var(--warm-line-2)',
          animation: `blink ${1.2 + i * 0.15}s ease-in-out infinite`,
        }} />
      ))}
      <div style={{ fontSize: 12, color: 'var(--text-muted)', marginTop: 4, display: 'flex', alignItems: 'center', gap: 6 }}>
        <span className="streaming"><span /><span /><span /></span>
        Thinking…
      </div>
    </div>
  );
}

// ── ScopeChip — dropdown repo picker ─────────────────────────────────────────

function ScopeChip() {
  const repos       = useRepoStore((s) => s.repos);
  const selectedRepo = useRepoStore((s) => s.selectedRepo);
  const selectRepo  = useRepoStore((s) => s.selectRepo);
  const [open, setOpen] = useState(false);

  return (
    <div style={{ position: 'relative', display: 'inline-block' }}>
      <button
        onClick={() => setOpen((o) => !o)}
        style={{
          display: 'flex', alignItems: 'center', gap: 5,
          background: 'var(--bg-surface)', border: '1px solid var(--border-default)',
          borderRadius: 20, padding: '3px 10px 3px 8px',
          fontSize: 12, color: 'var(--text-secondary)', cursor: 'pointer',
        }}
      >
        <span style={{ fontSize: 10, color: 'var(--text-muted)' }}>scope</span>
        <span style={{ fontWeight: 500 }}>{selectedRepo?.display_name ?? 'All sources'}</span>
        <span style={{ fontSize: 10, color: 'var(--text-muted)' }}>▾</span>
      </button>
      {open && (
        <div style={{
          position: 'absolute', top: '110%', left: 0, zIndex: 20,
          background: 'var(--warm-surface)', border: '1px solid var(--border-default)',
          borderRadius: 8, minWidth: 200, boxShadow: '0 4px 16px rgba(0,0,0,0.18)',
          overflow: 'hidden',
        }}>
          <div
            style={{ padding: '8px 12px', fontSize: 12, cursor: 'pointer', color: 'var(--text-secondary)' }}
            onClick={() => { useRepoStore.setState({ selectedRepo: null }); setOpen(false); }}
          >
            All sources
          </div>
          {repos.map((r) => (
            <div
              key={r.id}
              style={{
                padding: '8px 12px', fontSize: 12, cursor: 'pointer',
                background: selectedRepo?.id === r.id ? 'var(--bg-surface)' : 'transparent',
                color: 'var(--text-primary)',
              }}
              onClick={() => { selectRepo(r); setOpen(false); }}
            >
              {r.display_name}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Main component ────────────────────────────────────────────────────────────

export default function Ask() {
  const f = useFlags();

  const { selectedRepo } = useRepoStore();
  const workspaceId = useWorkspaceStore((s) => s.workspaceId);

  const [query, setQuery]         = useState('If I rename the customer_id column, what breaks?');
  const [submitted, setSubmitted] = useState(false);
  const [position, setPosition]   = useState(1.0);
  const [vizTab, setVizTab]       = useState<'graph' | 'tree' | 'files'>('graph');
  const [answer, setAnswer]       = useState<AnswerState | null>(null);
  const [liveResp, setLiveResp]   = useState<QueryResponse | null>(null);
  const streamCleanupRef          = useRef<(() => void) | null>(null);

  // Live owners state (Phase 7)
  const [liveOwners, setLiveOwners]   = useState<EntityOwner[] | null>(null);
  const [liveBusFactor, setLiveBusFactor] = useState<number | null>(null);

  // Mock time-travel state (used when LIVE_QUERY is off, or as fallback label)
  const mockState = useMemo<TimeTravelState>(() => {
    return timeTravelStates.reduce((acc, s) =>
      Math.abs(s.atFrac - position) < Math.abs(acc.atFrac - position) ? s : acc,
      timeTravelStates[0]
    );
  }, [position]);

  // Build answer from mock data
  const buildMockAnswer = useCallback((streaming = false): AnswerState => ({
    summary:      mockState.summary,
    verdictStats: mockState.stats,
    verdictNote:  mockState.verdictNote,
    graphNodes:   undefined, // BlastRadius uses MOCK_GRAPH directly
    confidence:   'high',
    followUps:    [],
    isStreaming:  streaming,
  }), [mockState]);

  // Build answer from live QueryResponse
  const buildLiveAnswer = useCallback((resp: QueryResponse, streaming = false): AnswerState => {
    const nodes: GraphNode[] | undefined =
      f.LIVE_BLAST && resp.affected_entities.length > 0
        ? entitiesToGraphNodes(resp.affected_entities as AffectedEntity[])
        : undefined;

    const stats = f.LIVE_BLAST && resp.change_risk
      ? riskToStats(resp.change_risk as RiskAssessment)
      : undefined;

    return {
      summary:         resp.summary_md ?? resp.summary,
      verdictStats:    stats,
      verdictNote:     resp.change_risk ? `${resp.change_risk.affected_count} entities` : undefined,
      graphNodes:      nodes,
      confidence:      resp.confidence?.level,
      followUps:       resp.follow_up_questions,
      isStreaming:     streaming,
      caveats:          resp.caveats ?? [],
      riskAlerts:       resp.risk_alerts ?? [],
      callChain:        resp.call_chain ?? [],
      domainEntities:   resp.domain_entities ?? [],
      onboardingPaths:  resp.onboarding_paths ?? [],
      liveCitations:    f.LIVE_CITATIONS ? extractCitations(resp) : undefined,
      affectedEntities: resp.affected_entities as AffectedEntity[],
    };
  }, [f.LIVE_BLAST, f.LIVE_CITATIONS]);

  const ask = useCallback(async (q: string) => {
    // Abort any in-flight stream
    streamCleanupRef.current?.();
    streamCleanupRef.current = null;

    setQuery(q);
    setSubmitted(true);
    setLiveResp(null);
    setLiveOwners(null);
    setLiveBusFactor(null);

    if (!f.LIVE_QUERY) {
      // Pure mock — instant fake "streaming"
      setAnswer({ ...buildMockAnswer(true) });
      const t = setTimeout(() => setAnswer(prev => prev ? { ...prev, isStreaming: false } : prev), 900);
      return () => clearTimeout(t);
    }

    // Resolve as_of_date from timeline position (Phase 9)
    const asOfDate: string | undefined = (() => {
      if (position >= 1.0) return undefined;
      const nearest = events.reduce((acc, e) =>
        Math.abs(e.at - position) < Math.abs(acc.at - position) ? e : acc
      );
      try {
        return new Date(nearest.date.replace("'", '20')).toISOString().slice(0, 10);
      } catch {
        return undefined;
      }
    })();

    const reqBase = {
      question: q,
      workspace_id: workspaceId,
      repo_path: selectedRepo?.repo_path,
      as_of_date: asOfDate,
    };

    if (f.LIVE_STREAM) {
      // SSE streaming
      let accumulated = '';
      setAnswer({ summary: '', isStreaming: true });

      const cleanup = queryBrainStream(
        reqBase,
        (delta) => {
          accumulated += delta;
          setAnswer(prev => prev ? { ...prev, summary: accumulated, isStreaming: true } : prev);
        },
        (_full) => {
          // Stream complete — fetch full structured response for verdict/citations
          queryBrain(reqBase)
            .then(resp => {
              setLiveResp(resp);
              setAnswer(buildLiveAnswer(resp, false));
            })
            .catch(() => {
              // Streaming gave us the text at least; show it without structure
              setAnswer(prev => prev ? { ...prev, isStreaming: false } : prev);
            });
        },
        (err) => {
          setAnswer({ summary: '', isStreaming: false, error: err.message });
        }
      );
      streamCleanupRef.current = cleanup;
    } else {
      // Non-streaming live call
      setAnswer({ summary: '…', isStreaming: true });
      try {
        const resp = await queryBrain(reqBase);
        setLiveResp(resp);
        setAnswer(buildLiveAnswer(resp, false));
      } catch (err) {
        setAnswer({
          summary: '',
          isStreaming: false,
          error: err instanceof Error ? err.message : 'Query failed',
        });
      }
    }
  }, [f.LIVE_QUERY, f.LIVE_STREAM, buildMockAnswer, buildLiveAnswer, position, workspaceId, selectedRepo]);

  // When time-travel position changes and we're in mock mode, refresh answer
  useEffect(() => {
    if (submitted && !f.LIVE_QUERY) {
      setAnswer(buildMockAnswer(false));
    }
  }, [mockState, submitted, f.LIVE_QUERY, buildMockAnswer]);

  // Cleanup stream on unmount
  useEffect(() => () => { streamCleanupRef.current?.(); }, []);

  // Fetch live owners when a live response arrives (Phase 7)
  useEffect(() => {
    if (!liveResp) return;
    const urn = liveResp.cited_entity_urns?.[0]
      ?? (liveResp.affected_entities?.[0] as AffectedEntity | undefined)?.id;
    if (!urn) return;
    getEntityOwners(urn)
      .then((res) => {
        setLiveOwners(res.owners);
        setLiveBusFactor(res.bus_factor);
      })
      .catch(() => {}); // fall back to mock silently
  }, [liveResp]);

  // Effective verdict label
  const verdictLabel = f.LIVE_QUERY
    ? (liveResp ? 'Live · brain' : 'Querying brain…')
    : mockState.label;

  // Effective citations (live URNs → future CitationList; for now fall back to mock)
  const showLiveCitations = f.LIVE_CITATIONS && !!liveResp?.cited_entity_urns?.length;

  return (
    <div className="app">
      <Sidebar />
      <main className="main">
        <TopBar crumb="Codebase impact" />
        <div className="qview">
          <div className="va-content">

            {/* Scope chip — repo picker dropdown */}
            <div style={{ marginBottom: 8 }}>
              <ScopeChip />
            </div>

            {/* Query bar */}
            <div className="qbar">
              <span className="ask">Ask the brain</span>
              <input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && ask(query)}
                placeholder="What would break if…"
              />
              <button className="send" onClick={() => ask(query)} disabled={answer?.isStreaming}>
                <IconBolt /> Ask
              </button>
            </div>

            {/* Suggested questions — live follow-ups or mock chips */}
            {(!submitted || answer?.followUps?.length) ? (
              <Suggested
                onAsk={ask}
                overrides={answer?.followUps?.length ? answer.followUps : undefined}
              />
            ) : null}

            {/* Answer */}
            {submitted && answer && (
              <div className="va-grid">
                {/* Left column */}
                <div className="left">
                  {answer.error ? (
                    <div style={{ padding: '24px', background: 'var(--danger-soft)', border: '1px solid var(--danger-border)', borderRadius: 8, color: 'var(--danger)', fontSize: 13 }}>
                      <strong>Brain unreachable</strong> — {answer.error}
                      <br /><small style={{ opacity: 0.7 }}>Toggle to mock mode with Ctrl+Shift+L</small>
                    </div>
                  ) : (
                    <div className="answer">
                      <div className="ans-head">
                        <div className="q">
                          <span className="you">You</span>
                          <span>{query}</span>
                          {answer.isStreaming && (
                            <span className="streaming"><span /><span /><span /></span>
                          )}
                        </div>
                        <span className="meta">{verdictLabel}</span>
                      </div>

                      <div className="ans-body">
                        {answer.isStreaming && f.LIVE_STREAM ? (
                          <StreamingSkeleton />
                        ) : (
                          <AnswerMarkdown content={answer.summary} />
                        )}

                        {/* Risk alerts — above everything else */}
                        <RiskAlertBanner alerts={(answer.riskAlerts ?? []) as Array<{ level: string; summary?: string }> } />

                        {/* Verdict stats */}
                        {answer.verdictStats && (
                          <div className="verdict">
                            <div className="cell">
                              <span className="k">Files affected</span>
                              <span className="v"><span>{answer.verdictStats.affected}</span></span>
                              <span className="sub">{answer.verdictNote ?? ''}</span>
                            </div>
                            <div className="cell">
                              <span className="k">Directories</span>
                              <span className="v"><span>{answer.verdictStats.dirs}</span></span>
                            </div>
                            <div className="cell">
                              <span className="k">Teams</span>
                              <span className="v"><span>{answer.verdictStats.teams}</span></span>
                            </div>
                            <div className="cell">
                              <span className="k">Rollout risk</span>
                              <span className={`v risk-${answer.verdictStats.risk.toLowerCase()}`}>
                                <span>{answer.verdictStats.risk}</span>
                              </span>
                            </div>
                          </div>
                        )}

                        {/* What breaks — human-readable entity breakdown */}
                        {answer.affectedEntities?.length ? (
                          <AffectedBreakdownList entities={answer.affectedEntities} />
                        ) : null}

                        {/* Blast radius */}
                        <div className="viz">
                          <div className="viz-head">
                            <h3>Blast radius · 2 hops</h3>
                            <div className="toggle">
                              <button data-active={vizTab === 'graph' ? 'true' : undefined} onClick={() => setVizTab('graph')}>Graph</button>
                              <button data-active={vizTab === 'tree' ? 'true' : undefined} onClick={() => setVizTab('tree')}>Tree</button>
                              <button data-active={vizTab === 'files' ? 'true' : undefined} onClick={() => setVizTab('files')}>Files</button>
                            </div>
                          </div>
                          {vizTab === 'graph' && (
                            <BlastRadiusGraph
                              liveEntities={f.LIVE_BLAST && liveResp ? liveResp.affected_entities as AffectedEntity[] : undefined}
                              centerLabel={query.split(' ').slice(-1)[0]}
                              compact={false}
                            />
                          )}
                          {vizTab === 'tree' && (
                            <CallChainTree chain={answer.callChain ?? []} />
                          )}
                          {vizTab === 'files' && (
                            <div style={{ padding: '8px 0', maxHeight: 320, overflowY: 'auto' }}>
                              {(f.LIVE_BLAST && liveResp?.affected_entities?.length
                                ? (liveResp.affected_entities as AffectedEntity[]).map((e) => e.id)
                                : MOCK_GRAPH.nodes.map((n) => n.label)
                              ).map((file, i) => (
                                <div key={i} style={{ padding: '4px 0', fontSize: 12, fontFamily: 'var(--font-mono)', color: 'var(--text-secondary)', borderBottom: '1px solid var(--warm-line)' }}>
                                  {file}
                                </div>
                              ))}
                            </div>
                          )}
                          <div className="legend">
                            <span className="l"><span className="s" style={{ background: 'var(--danger)' }} /> high-impact dep</span>
                            <span className="l"><span className="s" style={{ background: 'var(--warning)' }} /> medium</span>
                            <span className="l"><span className="s" style={{ background: 'var(--text-muted)' }} /> low</span>
                            <span className="l" style={{ marginLeft: 'auto' }}>
                              {f.LIVE_BLAST && liveResp?.affected_entities?.length
                                ? `${liveResp.affected_entities.length} nodes · live`
                                : `${MOCK_GRAPH.nodes.length + 1} nodes · 18 edges`}
                            </span>
                          </div>
                        </div>

                        {/* Caveats — below the graph */}
                        <CaveatBanner caveats={answer.caveats ?? []} />

                        {/* Citations */}
                        <CitationList
                          liveCitations={answer.liveCitations}
                          liveUrns={showLiveCitations && !answer.liveCitations ? liveResp?.cited_entity_urns : undefined}
                        />

                        {/* Onboarding paths — below citations */}
                        <OnboardingPaths paths={answer.onboardingPaths ?? []} />
                      </div>

                      <div className="ans-foot">
                        <button onClick={() => navigator.clipboard.writeText(answer.summary).catch(() => {})}><IconCopy /> Copy answer</button>
                        <button><IconShare /> Share</button>
                        <span className="grow" />
                        <button><IconThumbDown /></button>
                        <button><IconThumbUp /></button>
                        <button className="primary"><IconFlow /> Create migration plan</button>
                      </div>
                    </div>
                  )}

                  <TimeTravel position={position} setPosition={setPosition} />
                  <Compare />
                  <MCP />
                </div>

                {/* Right rail */}
                <div className="right">
                  <div className="rail">
                    <h4>
                      <IconGit2 /> Owners (git blame)
                      {f.LIVE_QUERY && !liveOwners && <span style={{ fontSize: 10, color: 'var(--text-muted)', marginLeft: 6, fontWeight: 400 }}>(estimated)</span>}
                    </h4>
                    <div className="owners">
                      {(f.LIVE_QUERY && liveOwners ? liveOwners.map((o) => {
                        const initials = o.name.split(' ').map((p) => p[0]).join('').slice(0, 2).toUpperCase();
                        return (
                          <div key={o.email} className="owner">
                            <div className="av" style={{ background: '#2E5C8A' }}>{initials}</div>
                            <div className="col">
                              <span className="nm">{o.name}</span>
                              <span className="sub">{o.email}</span>
                              <div className="owner-bar">
                                <div className="fill" style={{ width: `${o.pct}%`, background: '#2E5C8A' }} />
                              </div>
                            </div>
                            <span className="pct">{o.pct}%</span>
                          </div>
                        );
                      }) : MOCK_OWNERS.map((o) => (
                        <div key={o.initials} className="owner">
                          <div className="av" style={{ background: OWNER_COLORS[o.initials] ?? '#888' }}>{o.initials}</div>
                          <div className="col">
                            <span className="nm">{o.name}</span>
                            <span className="sub">{o.team} · {o.last}</span>
                            <div className="owner-bar">
                              <div className="fill" style={{ width: `${o.pct}%`, background: OWNER_COLORS[o.initials] ?? '#888' }} />
                            </div>
                          </div>
                          <span className="pct">{o.pct}%</span>
                        </div>
                      )))}
                    </div>
                  </div>

                  <div className="rail">
                    <h4><IconRisk /> Bus factor</h4>
                    <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
                      <span className="metric-lg" style={{ color: 'var(--warning)' }}>
                        {f.LIVE_QUERY && liveBusFactor !== null ? liveBusFactor : 2}
                      </span>
                      <span style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>
                        knowledge concentrated in {f.LIVE_QUERY && liveBusFactor !== null ? liveBusFactor : 2} engineer{(f.LIVE_QUERY && liveBusFactor !== null ? liveBusFactor : 2) !== 1 ? 's' : ''}
                      </span>
                    </div>
                  </div>

                  {/* Related docs (Phase 8) — only shown when live data has notes */}
                  {(() => {
                    interface NoteEntry { urn?: string; label?: string; kind?: string; }
                    const relatedDocs = f.LIVE_QUERY
                      ? ((liveResp?.notes ?? []) as NoteEntry[])
                          .filter((n) => ['adr', 'doc', 'notion'].includes(n.kind ?? ''))
                          .slice(0, 4)
                      : [];
                    if (!f.LIVE_QUERY || relatedDocs.length === 0) return null;
                    return (
                      <div className="rail">
                        <h4><IconADR /> Related docs</h4>
                        <div className="related">
                          {relatedDocs.map((n, i) => (
                            <a key={i} href={n.urn ?? '#'} target="_blank" rel="noreferrer">
                              <IconADR /> {n.label ?? n.urn} <span className="arr">→</span>
                            </a>
                          ))}
                        </div>
                      </div>
                    );
                  })()}

                  {/* Live confidence badge when brain is on */}
                  {f.LIVE_QUERY && answer.confidence && !answer.isStreaming && (
                    <div className="rail">
                      <h4>Confidence</h4>
                      <span style={{
                        display: 'inline-block', fontSize: 12, fontWeight: 600,
                        padding: '4px 10px', borderRadius: 12,
                        background: answer.confidence === 'high' ? 'var(--success-soft)' : answer.confidence === 'medium' ? 'var(--warning-soft)' : 'var(--danger-soft)',
                        color:      answer.confidence === 'high' ? 'var(--success)'      : answer.confidence === 'medium' ? 'var(--warning)'      : 'var(--danger)',
                        border: `1px solid ${answer.confidence === 'high' ? 'var(--success-border)' : answer.confidence === 'medium' ? 'var(--warning-border)' : 'var(--danger-border)'}`,
                      }}>
                        {answer.confidence}
                      </span>
                    </div>
                  )}
                </div>
              </div>
            )}

            {!submitted && (
              <div style={{ marginTop: 60, textAlign: 'center', color: 'var(--text-tertiary)', fontSize: 14, padding: '40px 0' }}>
                <div style={{ fontSize: 16, color: 'var(--text-secondary)', marginBottom: 6, fontWeight: 500 }}>
                  Ask anything about the codebase, docs, or its history.
                </div>
                <div>Answers cite file:line. Most queries return in &lt; 2 seconds.</div>
              </div>
            )}
          </div>
        </div>
      </main>
    </div>
  );
}
