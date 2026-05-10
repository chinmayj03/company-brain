import * as vscode from 'vscode';
import {
  BrainCallers,
  BrainDependencies,
  BrainEntityList,
  BrainEntityRecord,
  BrainQueryResult,
  JsonRpcResponse,
} from './types';

// Thin client around the harness MCP server's JSON-RPC bridge (POST /mcp).
//
// The server is built in company-brain-ai/src/companybrain/harness/mcp_server.py.
// Wire format: {jsonrpc, id, method: "tools/call", params: {name, arguments}}
// → {result: {content: [{type:"text", text: <json>}], isError, structuredContent}}
//
// We prefer `structuredContent` (the parsed dict) and fall back to parsing
// `content[0].text` when older servers don't include it.

export class BrainClient {
  private readonly mcpUrl: string;
  private readonly workspaceId: string;
  private nextId = 1;

  constructor(cfg?: { mcpUrl?: string; workspaceId?: string }) {
    const settings = vscode.workspace?.getConfiguration?.('companyBrain');
    this.mcpUrl =
      cfg?.mcpUrl ??
      settings?.get<string>('mcpUrl') ??
      'http://localhost:8765';
    this.workspaceId =
      cfg?.workspaceId ??
      settings?.get<string>('workspaceId') ??
      '';
  }

  get baseUrl(): string {
    return this.mcpUrl;
  }

  async query(question: string): Promise<BrainQueryResult> {
    return this.callTool<BrainQueryResult>('query_brain', { question });
  }

  async readEntity(urn: string): Promise<BrainEntityRecord> {
    return this.callTool<BrainEntityRecord>('read_entity', { urn });
  }

  async listEntitiesByFile(file: string): Promise<BrainEntityList> {
    return this.callTool<BrainEntityList>('list_entities_by_file', { file });
  }

  async findCallers(urn: string): Promise<BrainCallers> {
    return this.callTool<BrainCallers>('find_callers', { urn });
  }

  async findDependencies(urn: string): Promise<BrainDependencies> {
    return this.callTool<BrainDependencies>('find_dependencies', { urn });
  }

  async listTools(): Promise<string[]> {
    const resp = await this.rpc('tools/list', {});
    const tools = resp.result?.tools ?? [];
    return tools.map((t) => t.name);
  }

  private async callTool<T>(
    name: string,
    args: Record<string, unknown>,
  ): Promise<T> {
    const resp = await this.rpc<T>('tools/call', { name, arguments: args });
    if (resp.error) {
      throw new Error(`MCP error ${resp.error.code}: ${resp.error.message}`);
    }
    const payload = resp.result;
    if (!payload) {
      throw new Error(`MCP call ${name}: empty result`);
    }
    if (payload.isError) {
      const msg = payload.content?.[0]?.text ?? 'unknown tool error';
      throw new Error(`MCP tool ${name} failed: ${msg}`);
    }
    if (payload.structuredContent !== undefined) {
      return payload.structuredContent as T;
    }
    const text = payload.content?.[0]?.text;
    if (typeof text === 'string') {
      return JSON.parse(text) as T;
    }
    throw new Error(`MCP call ${name}: no parseable payload`);
  }

  private async rpc<T = unknown>(
    method: string,
    params: Record<string, unknown>,
  ): Promise<JsonRpcResponse<T>> {
    const id = this.nextId++;
    const body = JSON.stringify({ jsonrpc: '2.0', id, method, params });
    const url = `${this.mcpUrl.replace(/\/$/, '')}/mcp`;
    const headers: Record<string, string> = { 'Content-Type': 'application/json' };
    if (this.workspaceId) {
      headers['X-Workspace-Id'] = this.workspaceId;
    }
    const resp = await fetch(url, { method: 'POST', headers, body });
    if (!resp.ok) {
      throw new Error(`MCP HTTP ${resp.status} on ${method}`);
    }
    return (await resp.json()) as JsonRpcResponse<T>;
  }
}
