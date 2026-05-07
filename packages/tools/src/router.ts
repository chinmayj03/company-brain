import { initTRPC } from "@trpc/server";
import { z } from "zod";
import type { GraphClient } from "@company-brain/graph";
import {
  findSymbol, findCallers, findCallees, getFunctionSignature,
  FindSymbolInputSchema, FindCallersInputSchema, FindCalleesInputSchema, GetFunctionSignatureInputSchema,
} from "./queries/symbols.js";
import { getFileSummary, listFilesIn, GetFileSummaryInputSchema, ListFilesInInputSchema } from "./queries/files.js";
import {
  getContractForEndpoint, listEndpointsImplementingContract, getDriftSignals,
  GetContractForEndpointInputSchema, ListEndpointsImplementingContractInputSchema, GetDriftSignalsInputSchema,
} from "./queries/contracts.js";
import {
  getTableForEntity, findColumnsWithPattern, getForeignKeys,
  GetTableForEntityInputSchema, FindColumnsWithPatternInputSchema, GetForeignKeysInputSchema,
} from "./queries/database.js";
import {
  JavaApiClient, hybridBlastRadius, hybridFindSymbol, hybridGetNodeContext,
  HybridBlastRadiusInputSchema,
} from "./queries/hybrid.js";

const t = initTRPC.create();

export type ToolRouter = typeof createToolRouter extends (...args: unknown[]) => infer R ? R : never;

export function createToolRouter(graph: GraphClient, javaApiUrl?: string) {
  const javaClient = new JavaApiClient(javaApiUrl ?? process.env["JAVA_API_URL"] ?? "http://localhost:8080");

  return t.router({
    // ── Symbol queries ──────────────────────────────────────────────────────
    findSymbol: t.procedure
      .input(FindSymbolInputSchema)
      .query(({ input }) => findSymbol(input, graph)),

    findCallers: t.procedure
      .input(FindCallersInputSchema)
      .query(({ input }) => findCallers(input, graph)),

    findCallees: t.procedure
      .input(FindCalleesInputSchema)
      .query(({ input }) => findCallees(input, graph)),

    getFunctionSignature: t.procedure
      .input(GetFunctionSignatureInputSchema)
      .query(({ input }) => getFunctionSignature(input, graph)),

    // ── File queries ────────────────────────────────────────────────────────
    getFileSummary: t.procedure
      .input(GetFileSummaryInputSchema)
      .query(({ input }) => getFileSummary(input, graph)),

    listFilesIn: t.procedure
      .input(ListFilesInInputSchema)
      .query(({ input }) => listFilesIn(input, graph)),

    // ── Contract + drift queries ────────────────────────────────────────────
    getContractForEndpoint: t.procedure
      .input(GetContractForEndpointInputSchema)
      .query(({ input }) => getContractForEndpoint(input, graph)),

    listEndpointsImplementingContract: t.procedure
      .input(ListEndpointsImplementingContractInputSchema)
      .query(({ input }) => listEndpointsImplementingContract(input, graph)),

    getDriftSignals: t.procedure
      .input(GetDriftSignalsInputSchema)
      .query(({ input }) => getDriftSignals(input, graph)),

    // ── Database queries ────────────────────────────────────────────────────
    getTableForEntity: t.procedure
      .input(GetTableForEntityInputSchema)
      .query(({ input }) => getTableForEntity(input, graph)),

    findColumnsWithPattern: t.procedure
      .input(FindColumnsWithPatternInputSchema)
      .query(({ input }) => findColumnsWithPattern(input, graph)),

    getForeignKeys: t.procedure
      .input(GetForeignKeysInputSchema)
      .query(({ input }) => getForeignKeys(input, graph)),

    // ── Hybrid queries (Neo4j + Postgres) ───────────────────────────────────
    hybridBlastRadius: t.procedure
      .input(HybridBlastRadiusInputSchema)
      .query(({ input }) => hybridBlastRadius(input, graph, javaClient)),

    hybridFindSymbol: t.procedure
      .input(FindSymbolInputSchema)
      .query(({ input }) => hybridFindSymbol(input, graph, javaClient)),

    hybridGetNodeContext: t.procedure
      .input(z.object({ scope: z.string().min(1), nodeIdOrName: z.string().min(1) }))
      .query(({ input }) => hybridGetNodeContext(input, graph, javaClient)),

    // ── Repo map ────────────────────────────────────────────────────────────
    getRepoMap: t.procedure
      .input(z.object({ scope: z.string().min(1), tokenBudget: z.number().int().min(100).max(32000).default(4000) }))
      .query(async ({ input }) => {
        const { scope, tokenBudget } = input;
        const charBudget = tokenBudget * 4;

        const files = await graph.runRead<Record<string, unknown>>(
          `MATCH (f { scope: $scope, type: "File" }) RETURN coalesce(f.path, f.id) AS path ORDER BY path LIMIT 2000`,
          { scope },
        );
        const lines: string[] = [];
        let used = 0;
        for (const f of files) {
          const line = (f["path"] as string) ?? "";
          if (used + line.length + 1 > charBudget) break;
          lines.push(line);
          used += line.length + 1;
        }
        return { text: lines.join("\n"), filesIncluded: lines.length, totalFiles: files.length, tokenBudget };
      }),

    // ── Health ──────────────────────────────────────────────────────────────
    health: t.procedure
      .input(z.object({ scope: z.string().min(1) }))
      .query(async ({ input }) => {
        const nodeCount = await graph.nodeCount(input.scope);
        const javaReachable = await javaClient.ping();
        return { nodeCount, scope: input.scope, javaApiReachable: javaReachable };
      }),
  });
}
