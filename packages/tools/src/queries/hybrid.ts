import { z } from "zod";
import type { GraphClient } from "@company-brain/graph";
import { success, absent, type ToolResult } from "../contract.js";
import { findSymbol, type SymbolRecord, FindSymbolInputSchema } from "./symbols.js";

// ── Java API Client ───────────────────────────────────────────────────────────

export interface JavaSearchResult {
  nodeId:    string;
  label:     string;
  type:      string;
  scope:     string;
  riskScore: number;
  semanticSummary?: string;
}

export interface JavaBlastRadiusResult {
  rootNodeId: string;
  nodes: Array<{
    nodeId:    string;
    label:     string;
    type:      string;
    depth:     number;
    riskScore: number;
    edgeType:  string;
  }>;
}

export interface JavaNodeContextResult {
  nodeId:   string;
  label:    string;
  facts:    string[];
  summary?: string;
  riskScore: number;
}

export class JavaApiClient {
  constructor(private readonly baseUrl: string) {}

  async searchNodes(
    q: string,
    nodeType?: string,
    limit = 20,
  ): Promise<JavaSearchResult[]> {
    const url = new URL("/api/nodes/search", this.baseUrl);
    url.searchParams.set("q", q);
    if (nodeType) url.searchParams.set("type", nodeType);
    url.searchParams.set("limit", String(limit));

    try {
      const res = await fetch(url, { signal: AbortSignal.timeout(5000) });
      if (!res.ok) return [];
      const body = await res.json() as { nodes?: JavaSearchResult[] };
      return body.nodes ?? [];
    } catch {
      return [];
    }
  }

  async getBlastRadius(nodeId: string, depth = 3): Promise<JavaBlastRadiusResult | null> {
    const url = new URL(`/api/nodes/${encodeURIComponent(nodeId)}/blast-radius`, this.baseUrl);
    url.searchParams.set("depth", String(depth));
    try {
      const res = await fetch(url, { signal: AbortSignal.timeout(8000) });
      if (!res.ok) return null;
      return await res.json() as JavaBlastRadiusResult;
    } catch {
      return null;
    }
  }

  async getNodeContext(nodeId: string): Promise<JavaNodeContextResult | null> {
    try {
      const res = await fetch(
        new URL(`/api/nodes/${encodeURIComponent(nodeId)}/context`, this.baseUrl),
        { signal: AbortSignal.timeout(5000) },
      );
      if (!res.ok) return null;
      return await res.json() as JavaNodeContextResult;
    } catch {
      return null;
    }
  }

  async ping(): Promise<boolean> {
    try {
      const res = await fetch(new URL("/actuator/health", this.baseUrl), { signal: AbortSignal.timeout(2000) });
      return res.ok;
    } catch {
      return false;
    }
  }
}

// ── Type definitions ──────────────────────────────────────────────────────────

export const HybridBlastRadiusInputSchema = z.object({
  scope:       z.string().min(1),
  nodeIdOrName: z.string().min(1),
  depth:       z.number().int().min(1).max(5).default(3),
});
export type HybridBlastRadiusInput = z.infer<typeof HybridBlastRadiusInputSchema>;

export interface HybridBlastRadiusNode {
  nodeId:    string;
  label:     string;
  type:      string;
  depth:     number;
  edgeType:  string;
  riskScore: number;
  isBreaking: boolean;
}

export interface HybridBlastRadiusResult {
  rootNodeId: string;
  rootLabel:  string;
  nodes:      HybridBlastRadiusNode[];
  source:     "neo4j" | "java" | "hybrid";
}

export interface HybridNodeContextFacts {
  nodeId:    string;
  label:     string;
  type:      string;
  filePath:  string;
  confidence: number;
}

export interface HybridSemanticContext {
  facts:    string[];
  summary?: string;
  riskScore: number;
  source:   "java" | "unavailable";
}

export interface HybridNodeContext {
  structural: HybridNodeContextFacts;
  semantic:   HybridSemanticContext;
}

export interface HybridGetNodeContextInput {
  scope:       string;
  nodeIdOrName: string;
}

// ── hybridBlastRadius ─────────────────────────────────────────────────────────

const BREAKING_EDGE_TYPES = new Set(["implements_contract", "handles_route", "calls"]);

export async function hybridBlastRadius(
  input: HybridBlastRadiusInput,
  graph: GraphClient,
  javaClient: JavaApiClient,
): Promise<ToolResult<HybridBlastRadiusResult>> {
  const { scope, nodeIdOrName, depth } = input;

  // Resolve root node
  const rootRows = await graph.runRead<Record<string, unknown>>(
    `MATCH (n { scope: $scope })
     WHERE n.id = $q OR n.label = $q
     RETURN n.id AS id, n.label AS label, n.type AS type
     LIMIT 1`,
    { scope, q: nodeIdOrName },
  );

  if (rootRows.length === 0) {
    // Try Java fallback
    const javaResults = await javaClient.searchNodes(nodeIdOrName, undefined, 1);
    if (javaResults.length === 0) return absent("no_match", `Node not found: ${nodeIdOrName}`);
    const jr = javaResults[0]!;
    const javaBlast = await javaClient.getBlastRadius(jr.nodeId, depth);
    if (!javaBlast) return absent("no_match", `Node not found: ${nodeIdOrName}`);
    return success({
      rootNodeId: jr.nodeId, rootLabel: jr.label,
      nodes: javaBlast.nodes.map(n => ({
        ...n, isBreaking: BREAKING_EDGE_TYPES.has(n.edgeType),
      })),
      source: "java",
    }, 0.70);
  }

  const root = rootRows[0]!;
  const rootId = root["id"] as string;

  // Neo4j structural blast radius (BFS up to `depth` hops)
  const neoRows = await graph.runRead<Record<string, unknown>>(
    `MATCH path = (root { id: $rootId })-[r*1..$depth]->(downstream)
     WHERE all(n IN nodes(path) WHERE n.scope = $scope)
     WITH downstream, relationships(path) AS rels, length(path) AS d
     RETURN downstream.id AS nodeId, downstream.label AS label, downstream.type AS type,
            d AS depth, type(last(rels)) AS edgeType
     ORDER BY d, downstream.type
     LIMIT 200`,
    { rootId, scope, depth },
  );

  // Enrich with Java risk scores (fire-and-forget, don't fail if Java is down)
  const nodeIds = neoRows.map((r: Record<string, unknown>) => r["nodeId"] as string);
  const riskMap = new Map<string, number>();

  if (nodeIds.length > 0) {
    try {
      const javaBlast = await javaClient.getBlastRadius(rootId, depth);
      if (javaBlast) {
        for (const n of javaBlast.nodes) riskMap.set(n.nodeId, n.riskScore);
      }
    } catch {
      // Java unavailable — proceed with Neo4j only
    }
  }

  const hybridNodes: HybridBlastRadiusNode[] = neoRows.map((r: Record<string, unknown>) => {
    const nodeId = r["nodeId"] as string;
    const edgeType = (r["edgeType"] as string) ?? "";
    return {
      nodeId, label: r["label"] as string,
      type:      r["type"] as string,
      depth:     r["depth"] as number,
      edgeType,
      riskScore: riskMap.get(nodeId) ?? 0,
      isBreaking: BREAKING_EDGE_TYPES.has(edgeType),
    };
  });

  // Sort: breaking first, then by riskScore desc, then depth asc
  hybridNodes.sort((a, b) => {
    if (a.isBreaking !== b.isBreaking) return a.isBreaking ? -1 : 1;
    if (b.riskScore !== a.riskScore) return b.riskScore - a.riskScore;
    return a.depth - b.depth;
  });

  return success({
    rootNodeId: rootId, rootLabel: root["label"] as string,
    nodes: hybridNodes,
    source: riskMap.size > 0 ? "hybrid" : "neo4j",
  }, 0.85);
}

// ── hybridFindSymbol ──────────────────────────────────────────────────────────

export async function hybridFindSymbol(
  input: z.infer<typeof FindSymbolInputSchema>,
  graph: GraphClient,
  javaClient: JavaApiClient,
): Promise<ToolResult<SymbolRecord[]>> {
  // Try Neo4j first
  const neoResult = await findSymbol(input, graph);
  if (neoResult.kind === "success") {
    // Enrich with Java risk scores
    const javaResults = await javaClient.searchNodes(input.pattern, input.kind, 20).catch(() => []);
    const riskMap = new Map(javaResults.map(r => [r.nodeId, r.riskScore]));
    if (riskMap.size > 0) {
      for (const sym of neoResult.result) {
        const risk = riskMap.get(sym.id);
        if (risk !== undefined) sym.properties["riskScore"] = risk;
      }
    }
    return neoResult;
  }

  // Fallback to Java
  const javaResults = await javaClient.searchNodes(input.pattern, input.kind, 20).catch(() => []);
  if (javaResults.length === 0) return absent("no_match", `Symbol not found: ${input.pattern}`);

  return success(javaResults.map(r => ({
    id: r.nodeId, label: r.label, type: r.type,
    filePath: "", confidence: r.riskScore > 0 ? 0.70 : 0.60,
    properties: { riskScore: r.riskScore, semanticSummary: r.semanticSummary ?? "" },
  })), 0.70);
}

// ── hybridGetNodeContext ──────────────────────────────────────────────────────

export async function hybridGetNodeContext(
  input: HybridGetNodeContextInput,
  graph: GraphClient,
  javaClient: JavaApiClient,
): Promise<ToolResult<HybridNodeContext>> {
  const { scope, nodeIdOrName } = input;

  const neoRows = await graph.runRead<Record<string, unknown>>(
    `MATCH (n { scope: $scope })
     WHERE n.id = $q OR n.label = $q
     RETURN n.id AS id, n.label AS label, n.type AS type,
            coalesce(n.filePath, n.path, '') AS filePath, n.confidence AS confidence
     LIMIT 1`,
    { scope, q: nodeIdOrName },
  );

  if (neoRows.length === 0) return absent("no_match", `Node not found: ${nodeIdOrName}`);

  const r = neoRows[0]!;
  const nodeId = r["id"] as string;

  const structural: HybridNodeContextFacts = {
    nodeId, label: r["label"] as string,
    type:      r["type"] as string,
    filePath:  r["filePath"] as string,
    confidence: r["confidence"] as number ?? 0.85,
  };

  // Semantic context from Java
  const javaCtx = await javaClient.getNodeContext(nodeId).catch(() => null);
  const semantic: HybridSemanticContext = javaCtx
    ? { facts: javaCtx.facts, summary: javaCtx.summary, riskScore: javaCtx.riskScore, source: "java" }
    : { facts: [], riskScore: 0, source: "unavailable" };

  return success({ structural, semantic }, structural.confidence);
}
