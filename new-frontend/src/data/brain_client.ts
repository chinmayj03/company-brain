/**
 * brain_client.ts — typed wrapper for all live brain API calls.
 *
 * Routes:
 *   AI service (FastAPI, port 8000) → proxied via Vite at /ai/*
 *     POST /ai/query
 *     GET  /ai/query/stream   (SSE)
 *     GET  /ai/health
 *
 *   Java backend (Spring, port 8080) → proxied via Vite at /api/*
 *     GET  /api/v1/nodes/{id}/blast-radius
 *     GET  /api/v1/search
 *
 * All functions throw on network/HTTP error so callers can catch and fall back.
 */

// ── Request / Response types (matching FastAPI schemas) ──────────────────────

export interface QueryRequest {
  question: string;
  workspace_id?: string;
  repo_path?: string;
  context_symbol?: string;
  file_path?: string;
  max_hops?: number;
  include_unverified?: boolean;
  as_of_date?: string; // YYYY-MM-DD — for time-travel
}

export interface Confidence {
  level: 'high' | 'medium' | 'low';
  rationale: string;
}

export interface RiskAssessment {
  level: 'LOW' | 'MED' | 'HIGH';
  affected_count: number;
  dirs_count: number;
  teams_count: number;
  summary: string;
}

export interface LiveCitation {
  urn: string;
  file?: string;
  line_range?: string;
  snippet?: string;
  label?: string;
  kind?: string; // 'ts' | 'sql' | 'adr' | 'notion' | ...
}

export interface AffectedEntity {
  id: string;
  name: string;
  type: string;
  depth: number;
  weight?: 'high' | 'med' | 'low';
  x?: number;
  y?: number;
}

export interface QueryResponse {
  summary: string;
  summary_md?: string;
  raw_markdown: string;
  confidence: Confidence;
  change_risk?: RiskAssessment;
  affected_entities: AffectedEntity[];
  call_chain: unknown[];
  sql_quotes: unknown[];
  caveats: string[];
  follow_up_questions: string[];
  notes: unknown[];
  risk_alerts: unknown[];
  domain_entities: unknown[];
  onboarding_paths: unknown[];
  // cited entity URNs — mapped to Citations panel
  cited_entity_urns?: string[];
}

export interface HealthResponse {
  status: string;
  llm_provider: 'ollama' | 'anthropic' | 'openai';
  version?: string;
}

// ── Helpers ───────────────────────────────────────────────────────────────────

const DEV_WORKSPACE = '00000000-0000-0000-0000-000000000001';

async function post<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText);
    throw new Error(`${res.status} ${path}: ${text}`);
  }
  return res.json() as Promise<T>;
}

async function get<T>(path: string): Promise<T> {
  const res = await fetch(path);
  if (!res.ok) throw new Error(`${res.status} ${path}`);
  return res.json() as Promise<T>;
}

// ── Public API ────────────────────────────────────────────────────────────────

export function queryBrain(req: QueryRequest): Promise<QueryResponse> {
  return post<QueryResponse>('/ai/query', {
    workspace_id: DEV_WORKSPACE,
    max_hops: 3,
    ...req,
  });
}

export function getHealth(): Promise<HealthResponse> {
  return get<HealthResponse>('/ai/health');
}

/**
 * Streaming query — yields answer text deltas via SSE.
 * onDelta is called with each streamed text chunk.
 * Returns a cleanup function (call to abort the stream).
 */
export function queryBrainStream(
  req: QueryRequest,
  onDelta: (delta: string) => void,
  onDone: (full: string) => void,
  onError: (err: Error) => void,
): () => void {
  const ctrl = new AbortController();
  let full = '';

  const body = JSON.stringify({
    workspace_id: req.workspace_id ?? DEV_WORKSPACE,
    max_hops: 3,
    ...req,
  });

  fetch('/ai/query/stream', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body,
    signal: ctrl.signal,
  })
    .then(async (res) => {
      if (!res.ok || !res.body) throw new Error(`${res.status} /ai/query/stream`);
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buf = '';
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        const lines = buf.split('\n');
        buf = lines.pop() ?? '';
        for (const line of lines) {
          if (!line.startsWith('data:')) continue;
          const payload = line.slice(5).trim();
          if (payload === '[DONE]') { onDone(full); return; }
          try {
            const { delta } = JSON.parse(payload) as { delta: string };
            full += delta;
            onDelta(delta);
          } catch { /* skip malformed chunk */ }
        }
      }
      onDone(full);
    })
    .catch((err: unknown) => {
      if ((err as Error).name !== 'AbortError') {
        onError(err instanceof Error ? err : new Error(String(err)));
      }
    });

  return () => ctrl.abort();
}

// ── Conversation types ────────────────────────────────────────────────────────

export interface ConversationSummary {
  id: string;
  question: string;
  title?: string;
  asked_at: string;  // ISO timestamp
  saved: boolean;
  actor_id?: string;
  actor_kind?: string;
}

export interface ConversationDetail extends ConversationSummary {
  summary_json?: unknown;  // full QueryResponse
}

// ── MCP Agent types ───────────────────────────────────────────────────────────

export interface McpAgent {
  id: string;
  agent_name: string;
  client_id: string;
  connected_at: string;
  last_ping_at: string;
  query_count: number;
  qpm: number;
  status: 'live' | 'idle' | 'gone';
}

// ── Source types ──────────────────────────────────────────────────────────────

export interface WorkspaceSource {
  id: string;
  kind: string;
  display_name: string;
  url?: string;
  last_synced_at?: string;
  sync_status: 'ok' | 'syncing' | 'error' | 'pending';
  error_message?: string;
  entity_count?: number;
  config?: Record<string, unknown>;
}

// ── Suggestion type ───────────────────────────────────────────────────────────

export interface Suggestion {
  question: string;
}

// ── ADR-0073: Repo / branch / me / workspace / owners types ──────────────────

export interface Repo {
  id: string;
  display_name: string;
  repo_path: string;
  default_branch: string;
  current_branch: string;
  last_synced_at?: string;
  entity_count: number;
  sync_status: 'ok' | 'syncing' | 'error' | 'pending';
}

export interface BranchList {
  current: string;
  branches: string[];
}

export interface EntityOwner {
  email: string;
  name: string;
  commit_count: number;
  last_commit_at: string;
  pct: number;
}

export interface OwnersResponse {
  urn: string;
  owners: EntityOwner[];
  bus_factor: number;
}

export interface MeResponse {
  id: string;
  display_name: string;
  email: string;
  workspace_id: string;
  workspace_name: string;
}

export interface WorkspaceMeta {
  id: string;
  name: string;
  slug: string;
  repo_count: number;
  source_count: number;
}

// ── New API functions ─────────────────────────────────────────────────────────

export async function getConversations(workspaceId: string, saved?: boolean): Promise<ConversationSummary[]> {
  const params = new URLSearchParams({ workspace_id: workspaceId });
  if (saved !== undefined) params.set('saved', String(saved));
  return get<ConversationSummary[]>(`/ai/conversations?${params}`);
}

export async function getConversation(id: string): Promise<ConversationDetail> {
  return get<ConversationDetail>(`/ai/conversations/${id}`);
}

export async function patchConversation(id: string, patch: { saved?: boolean; title?: string }): Promise<void> {
  const res = await fetch(`/ai/conversations/${id}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(patch),
  });
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText);
    throw new Error(`${res.status} PATCH /ai/conversations/${id}: ${text}`);
  }
}

export async function getMcpAgents(workspaceId: string): Promise<McpAgent[]> {
  return get<McpAgent[]>(`/ai/mcp/agents?workspace_id=${workspaceId}`);
}

export async function getSources(workspaceId: string): Promise<WorkspaceSource[]> {
  return get<WorkspaceSource[]>(`/api/v1/workspaces/${workspaceId}/sources`);
}

export async function triggerSync(workspaceId: string, sourceId: string): Promise<{ job_id?: string }> {
  const res = await fetch(`/api/v1/workspaces/${workspaceId}/sources/${sourceId}/sync`, {
    method: 'POST',
  });
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText);
    throw new Error(`${res.status} POST sync: ${text}`);
  }
  return res.json().catch(() => ({}));
}

export async function getSuggestions(workspaceId: string, repoPath?: string): Promise<Suggestion[]> {
  const params = new URLSearchParams({ workspace_id: workspaceId });
  if (repoPath) params.set('repo_path', repoPath);
  return get<Suggestion[]>(`/ai/suggestions?${params}`);
}

// ── ADR-0073: new API functions ───────────────────────────────────────────────

export const getMe = (): Promise<MeResponse> =>
  get<MeResponse>('/ai/me');

export const getWorkspace = (id: string): Promise<WorkspaceMeta> =>
  get<WorkspaceMeta>(`/ai/workspaces/${id}`);

export const getRepos = (workspaceId: string): Promise<Repo[]> =>
  get<Repo[]>(`/ai/workspaces/${workspaceId}/repos`);

export const getBranches = (workspaceId: string, repoId: string): Promise<BranchList> =>
  get<BranchList>(`/ai/workspaces/${workspaceId}/repos/${repoId}/branches`);

export const getEntityOwners = (urn: string): Promise<OwnersResponse> =>
  get<OwnersResponse>(`/ai/entities/${encodeURIComponent(urn)}/owners`);

// ── Mapping helpers: QueryResponse → component props ─────────────────────────

/**
 * Map live affected_entities to the mock GraphNode structure BlastRadius expects.
 * Falls back gracefully when entities have no layout coords.
 */
export function entitiesToGraphNodes(
  entities: AffectedEntity[],
): import('./mock_fallback').GraphNode[] {
  // Simple radial layout when x/y aren't provided by the server.
  return entities.map((e, i) => {
    const angle = (i / Math.max(entities.length, 1)) * 2 * Math.PI;
    const r = e.depth === 1 ? 30 : 50; // radius % units
    return {
      id: e.id,
      label: e.name,
      sub: e.type,
      weight: (e.weight ?? (e.depth === 1 ? 'high' : 'med')) as 'high' | 'med' | 'low',
      ring: (e.depth <= 1 ? 1 : 2) as 1 | 2,
      x: 50 + r * Math.cos(angle),
      y: 50 + r * Math.sin(angle),
    };
  });
}

// ── Source registration + job polling (ADR-0074) ─────────────────────────────

export interface RegisterSourceRequest {
  kind: string;
  display_name: string;
  config: Record<string, string>;
  auto_index?: boolean;
}

export interface RegisterSourceResponse {
  source: WorkspaceSource;
  job_id?: string;
}

export const registerSource = (
  workspaceId: string,
  body: RegisterSourceRequest,
): Promise<RegisterSourceResponse> =>
  post<RegisterSourceResponse>(`/api/v1/workspaces/${workspaceId}/sources`, body);

export const deleteSource = (workspaceId: string, sourceId: string): Promise<void> =>
  fetch(`/api/v1/workspaces/${workspaceId}/sources/${sourceId}`, { method: 'DELETE' })
    .then((r) => { if (!r.ok && r.status !== 204) throw new Error(`${r.status}`); });

export interface JobStatus {
  status: 'running' | 'completed' | 'failed';
  job_id: string;
  error?: string;
  result?: { entity_count: number; edge_count: number; gap_count?: number };
  progress?: { current_stage: string; logs: unknown[] };
}

export const getJobStatus = (jobId: string): Promise<JobStatus> =>
  get<JobStatus>(`/api/v1/pipeline/jobs/${jobId}`);

// ─────────────────────────────────────────────────────────────────────────────

/**
 * Map change_risk → verdict stats the Ask view displays.
 */
export function riskToStats(risk: RiskAssessment) {
  return {
    affected: risk.affected_count,
    dirs:     risk.dirs_count,
    teams:    risk.teams_count,
    risk:     risk.level as 'LOW' | 'MED' | 'HIGH',
  };
}
