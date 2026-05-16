export type RepoStatus = "extracted" | "extracting" | "empty" | "error";

export type BrainRepo = {
  id: string;
  name: string;
  path: string;
  status: RepoStatus;
  entity_count: number;
  edge_count: number;
  entity_types: Record<string, number>;
  last_extracted?: string | null;
};

export type Citation = {
  urn: string;
  name: string;
  file?: string;
  line?: string | number | null;
  why_relevant?: string;
  confidence?: number;
};

export type BrainEdge = {
  target_id?: string;
  to_entity?: string;
  to?: string;
  edge_type?: string;
  type?: string;
  confidence?: number;
  source?: string;
};

export type BrainEntity = {
  urn: string;
  name: string;
  type: string;
  repo_id: string;
  file: string;
  summary: string;
  role: string;
  risk: string;
  last_updated?: string;
  metadata: Record<string, unknown>;
  edges: BrainEdge[];
  citations: Citation[];
  related_entities?: BrainEntity[];
};

export type EntityListResponse = {
  items: BrainEntity[];
  page: number;
  page_size: number;
  total: number;
  types: string[];
};

export type PersonaId = "developer" | "pm" | "vp_eng";

export type QueryResponse = {
  summary: string;
  summary_md?: string;
  raw_markdown?: string;
  affected_entities?: Citation[];
  call_chain?: Array<{ urn: string; name: string; one_liner?: string; role?: string }>;
  cited_entity_urns?: string[];
  confidence?: { level: "high" | "medium" | "low"; rationale: string };
  caveats?: string[];
  follow_up_questions?: string[];
  ambiguity?: boolean;
  interpretations?: Array<{ id: string; description: string }>;
};

export type DriftDomain = {
  domain: string;
  severity: "low" | "medium" | "high";
  count: number;
};

export type DriftItem = {
  id: string;
  domain: string;
  severity: "low" | "medium" | "high";
  title: string;
  state: string;
  entity_urn?: string;
  history: Array<{ at?: string | null; event: string }>;
};

export type DriftSnapshot = {
  mock: boolean;
  as_of?: string | null;
  domains: DriftDomain[];
  items: DriftItem[];
};
