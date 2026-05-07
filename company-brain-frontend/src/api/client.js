/**
 * API client for the Company Brain backend.
 *
 * All requests are automatically scoped to the authenticated workspace
 * via the JWT Bearer token. The workspace_id is embedded in the JWT
 * and enforced via Postgres Row Level Security on the backend.
 */

import axios from 'axios';

const BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8080';

export const apiClient = axios.create({
  baseURL: `${BASE_URL}/v1`,
  headers: { 'Content-Type': 'application/json' },
  timeout: 10_000,
});

// Attach JWT from localStorage on every request
apiClient.interceptors.request.use((config) => {
  const token = localStorage.getItem('cb_token');
  if (token) config.headers.Authorization = `Bearer ${token}`;
  return config;
});

// Global error handling
apiClient.interceptors.response.use(
  (res) => res,
  (error) => {
    if (error.response?.status === 401) {
      localStorage.removeItem('cb_token');
      window.location.href = '/login';
    }
    return Promise.reject(error);
  }
);

// ────────────────────────────────────────────────────────────────
// Graph API
// ────────────────────────────────────────────────────────────────

/**
 * Compute blast radius for a node.
 * Returns all affected nodes with depth, edge type, owning team, confidence.
 */
export const getBlastRadius = (nodeId) =>
  apiClient.get(`/nodes/${nodeId}/blast-radius`).then((r) => r.data);

/**
 * Get all context entries for a node (git commits, PRs, annotations, LLM synthesis).
 */
export const getNodeContext = (nodeId, { page = 0, size = 20 } = {}) =>
  apiClient.get(`/nodes/${nodeId}/context`, { params: { page, size } }).then((r) => r.data);

/**
 * Get all nodes that depend on this node (inbound edges).
 */
export const getDependents = (nodeId, edgeType) =>
  apiClient.get(`/nodes/${nodeId}/dependents`, { params: { edgeType } }).then((r) => r.data);

/**
 * Get all nodes this node depends on (outbound edges).
 */
export const getDependencies = (nodeId, edgeType) =>
  apiClient.get(`/nodes/${nodeId}/dependencies`, { params: { edgeType } }).then((r) => r.data);

/**
 * Get 2-hop neighbourhood graph for a service node.
 * Returns nodes + edges in ReactFlow-compatible format.
 */
export const getServiceGraph = (nodeId) =>
  apiClient.get(`/services/${nodeId}/graph`).then((r) => r.data);

/**
 * Fuzzy search across node names.
 */
export const searchNodes = (q, { nodeType, limit = 20 } = {}) =>
  apiClient.get('/search', { params: { q, nodeType, limit } }).then((r) => r.data);

// ────────────────────────────────────────────────────────────────
// Annotation API (VS Code extension → dashboard)
// ────────────────────────────────────────────────────────────────

/**
 * Submit a user annotation anchored to a specific commit.
 *
 * annotationType: 'business_context' | 'invariant' | 'risk_flag' | 'deprecation_note'
 */
export const addAnnotation = (nodeId, payload) =>
  apiClient.post(`/nodes/${nodeId}/annotations`, payload).then((r) => r.data);

// ────────────────────────────────────────────────────────────────
// Pipeline API — now routed through Java backend (port 8080)
//
// Java is the authoritative orchestrator:
//   POST /v1/pipeline/start  → Java creates job, dispatches to AI service
//   GET  /v1/pipeline/jobs/{id} → Java serves status from DB (not Redis)
//
// The AI service (port 8000) is NOT called by the frontend directly.
// Java calls it internally for LLM inference, then writes results to DB.
// ────────────────────────────────────────────────────────────────

/**
 * Start the context pipeline.
 * Calls Java backend → Java delegates LLM work to AI service.
 * Returns { jobId, status: 'running' }
 */
export const startContextPipeline = (payload) =>
  apiClient.post('/pipeline/start', {
    endpointPath: payload.endpoint_path,
    httpMethod:   payload.http_method,
    branch:       payload.branch || 'main',
    repos: (payload.repos || []).map(r => ({
      localPath: r.local_path ?? null,
      url:       r.url ?? null,
      type:      r.type   || 'backend',
      branch:    r.branch || 'main',
    })),
  }).then((r) => r.data);

/**
 * Poll pipeline job status.
 * Returns { jobId, status, progress: { logs }, result: { entityCount, … } }
 */
export const getPipelineJob = (jobId) =>
  apiClient.get(`/pipeline/jobs/${jobId}`).then((r) => r.data);

// ────────────────────────────────────────────────────────────────
// AI service direct client — only for lightweight utilities
// (branch detection, health check). NOT for pipeline execution.
// ────────────────────────────────────────────────────────────────

const AI_BASE_URL = import.meta.env.VITE_AI_API_BASE_URL || '/ai';

export const aiClient = axios.create({
  baseURL: AI_BASE_URL,
  headers: { 'Content-Type': 'application/json' },
  timeout: 120_000,   // LLM synthesis on local Ollama can take 30-90s
});

aiClient.interceptors.request.use((config) => {
  const token = localStorage.getItem('cb_token');
  if (token) config.headers.Authorization = `Bearer ${token}`;
  return config;
});

/**
 * Ask a natural language question about the dependency graph.
 * Returns a grounded, LLM-synthesised answer with source citations.
 */
export const queryGraph = (payload) =>
  aiClient.post('/query', payload).then((r) => r.data);

// ────────────────────────────────────────────────────────────────
// Architecture API — ADR-006 Week 4
// ────────────────────────────────────────────────────────────────

/**
 * Get top-N hub nodes by degree (highest blast radius potential).
 */
export const getHubs = (workspaceId, topN = 20) =>
  apiClient.get(`/workspaces/${workspaceId}/graph/hubs`, { params: { topN } }).then(r => r.data);

/**
 * Get top-N bridge nodes by betweenness centrality (structural chokepoints).
 */
export const getBridges = (workspaceId, topN = 10) =>
  apiClient.get(`/workspaces/${workspaceId}/graph/bridges`, { params: { topN } }).then(r => r.data);

/**
 * List execution flows for a workspace, sorted by criticality desc.
 */
export const getFlows = (workspaceId, { minCriticality = 0 } = {}) =>
  apiClient.get(`/workspaces/${workspaceId}/flows`, { params: { minCriticality } }).then(r => r.data);

/**
 * Get the full node sequence of a specific flow.
 */
export const getFlow = (workspaceId, flowId) =>
  apiClient.get(`/workspaces/${workspaceId}/flows/${flowId}`).then(r => r.data);
