import type {
  BrainEntity,
  BrainRepo,
  DriftSnapshot,
  EntityListResponse,
  PersonaId,
  QueryResponse,
} from "./types";

const API_BASE = import.meta.env.VITE_BRAIN_API_BASE_URL || "/api";
const WORKSPACE_ID =
  import.meta.env.VITE_WORKSPACE_ID || "00000000-0000-0000-0000-000000000001";
const REPO_PATH = import.meta.env.VITE_DEMO_REPO_PATH || undefined;

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers || {}),
    },
  });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(detail || `Request failed: ${response.status}`);
  }
  return response.json() as Promise<T>;
}

export const brainClient = {
  listRepos: async (): Promise<BrainRepo[]> => {
    const data = await request<{ repos: BrainRepo[] }>("/repos");
    return data.repos;
  },

  repoSummary: (repoId: string): Promise<BrainRepo> =>
    request(`/repos/${encodeURIComponent(repoId)}/brain/summary`),

  listEntities: (params: {
    q?: string;
    type?: string;
    repo_id?: string;
    page?: number;
    page_size?: number;
  } = {}): Promise<EntityListResponse> => {
    const search = new URLSearchParams();
    Object.entries(params).forEach(([key, value]) => {
      if (value !== undefined && value !== "") search.set(key, String(value));
    });
    return request(`/entities?${search.toString()}`);
  },

  entityDetail: (urn: string): Promise<BrainEntity> =>
    request(`/entities/${encodeURIComponent(urn)}`),

  query: (question: string, persona?: PersonaId): Promise<QueryResponse> =>
    request("/query", {
      method: "POST",
      body: JSON.stringify({
        question,
        persona,
        workspace_id: WORKSPACE_ID,
        repo_path: REPO_PATH,
        actor_id: "local-demo",
        actor_kind: "user",
      }),
    }),

  latestDrift: (): Promise<DriftSnapshot> => request("/drift/snapshot/latest"),
};
