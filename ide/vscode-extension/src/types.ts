// Shared types for the company-brain VS Code extension.
//
// These mirror the JSON shapes returned by the harness MCP server in
// company-brain-ai/src/companybrain/harness/mcp_server.py. Keep them in sync
// when that server adds fields; the extension never reads anything not
// declared here.

export interface BrainMatch {
  urn: string;
  entity_type: string;
  qualified_name: string;
  file: string;
  summary: string;
}

export interface BrainQueryResult {
  question: string;
  matches: BrainMatch[];
  match_count: number;
}

export interface BrainEntityRecord {
  urn: string;
  found: boolean;
  entity?: Record<string, unknown>;
}

export interface BrainEntityList {
  file: string;
  urns: string[];
  count: number;
}

export interface BrainEdge {
  target: string;
  edge_type: string;
  confidence?: number;
}

export interface BrainDependencies {
  urn: string;
  found: boolean;
  edges: BrainEdge[];
  count: number;
}

export interface BrainCallers {
  target: string;
  callers: string[];
  count: number;
}

export interface JsonRpcResponse<T> {
  jsonrpc: '2.0';
  id: number | string | null;
  result?: {
    content?: Array<{ type: string; text: string }>;
    isError?: boolean;
    structuredContent?: T;
    // tools/list returns { tools: [...] } directly here
    tools?: Array<{ name: string; description: string; inputSchema: unknown }>;
  };
  error?: { code: number; message: string };
}
