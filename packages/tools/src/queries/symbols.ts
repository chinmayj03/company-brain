import { z } from "zod";
import type { GraphClient } from "@company-brain/graph";
import { success, absent, type ToolResult } from "../contract.js";

export const FindSymbolInputSchema = z.object({
  scope:   z.string().min(1),
  pattern: z.string().min(1),
  kind:    z.enum(["Class","Interface","Function","Method","TypeAlias","Variable","Screen","APIRoute","Layout"]).optional(),
});
export type FindSymbolInput = z.infer<typeof FindSymbolInputSchema>;

export const FindCallersInputSchema = z.object({
  scope:           z.string().min(1),
  symbolIdOrName:  z.string().min(1),
});
export type FindCallersInput = z.infer<typeof FindCallersInputSchema>;

export const FindCalleesInputSchema = z.object({
  scope:           z.string().min(1),
  symbolIdOrName:  z.string().min(1),
});
export type FindCalleesInput = z.infer<typeof FindCalleesInputSchema>;

export const GetFunctionSignatureInputSchema = z.object({
  scope:           z.string().min(1),
  symbolIdOrName:  z.string().min(1),
});

export interface SymbolRecord {
  id:         string;
  label:      string;
  type:       string;
  filePath:   string;
  confidence: number;
  properties: Record<string, unknown>;
}

export interface FunctionSignature {
  id:          string;
  label:       string;
  paramNames:  string[];
  returnType?: string;
  filePath:    string;
  confidence:  number;
}

export async function findSymbol(
  input: FindSymbolInput,
  graph: GraphClient,
): Promise<ToolResult<SymbolRecord[]>> {
  const { scope, pattern, kind } = input;

  const whereClauses = [
    "n.scope = $scope",
    "n.label =~ $pattern",
  ];
  if (kind) whereClauses.push("n.type = $kind");

  const cypher = `
    MATCH (n)
    WHERE ${whereClauses.join(" AND ")}
    RETURN n.id AS id, n.label AS label, n.type AS type,
           coalesce(n.filePath, n.path, '') AS filePath,
           n.confidence AS confidence,
           properties(n) AS props
    LIMIT 50
  `;

  const rows = await graph.runRead<Record<string, unknown>>(cypher, {
    scope,
    pattern: `(?i).*${pattern.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}.*`,
    kind: kind ?? null,
  });

  if (rows.length === 0) return absent("no_match", `No symbols matching '${pattern}'`);

  const results: SymbolRecord[] = rows.map((r: Record<string, unknown>) => ({
    id:         r["id"] as string,
    label:      r["label"] as string,
    type:       r["type"] as string,
    filePath:   r["filePath"] as string,
    confidence: r["confidence"] as number ?? 0,
    properties: r["props"] as Record<string, unknown> ?? {},
  }));

  const confidence = results.reduce((sum: number, r: SymbolRecord) => sum + r.confidence, 0) / results.length;
  return success(results, confidence);
}

export async function findCallers(
  input: FindCallersInput,
  graph: GraphClient,
): Promise<ToolResult<SymbolRecord[]>> {
  const { scope, symbolIdOrName } = input;

  const cypher = `
    MATCH (target { scope: $scope })
    WHERE target.id = $symbolIdOrName OR target.label = $symbolIdOrName
    WITH target LIMIT 1
    MATCH (caller)-[:calls]->(target)
    WHERE caller.scope = $scope
    RETURN caller.id AS id, caller.label AS label, caller.type AS type,
           coalesce(caller.filePath, caller.path, '') AS filePath,
           caller.confidence AS confidence,
           properties(caller) AS props
    LIMIT 50
  `;

  const rows = await graph.runRead<Record<string, unknown>>(cypher, { scope, symbolIdOrName });
  if (rows.length === 0) return absent("no_match", `No callers found for '${symbolIdOrName}'`);

  const results: SymbolRecord[] = rows.map((r: Record<string, unknown>) => ({
    id:         r["id"] as string,
    label:      r["label"] as string,
    type:       r["type"] as string,
    filePath:   r["filePath"] as string,
    confidence: r["confidence"] as number ?? 0,
    properties: r["props"] as Record<string, unknown> ?? {},
  }));
  return success(results, 0.85);
}

export async function findCallees(
  input: FindCalleesInput,
  graph: GraphClient,
): Promise<ToolResult<SymbolRecord[]>> {
  const { scope, symbolIdOrName } = input;

  const cypher = `
    MATCH (source { scope: $scope })
    WHERE source.id = $symbolIdOrName OR source.label = $symbolIdOrName
    WITH source LIMIT 1
    MATCH (source)-[:calls]->(callee)
    WHERE callee.scope = $scope
    RETURN callee.id AS id, callee.label AS label, callee.type AS type,
           coalesce(callee.filePath, callee.path, '') AS filePath,
           callee.confidence AS confidence,
           properties(callee) AS props
    LIMIT 50
  `;

  const rows = await graph.runRead<Record<string, unknown>>(cypher, { scope, symbolIdOrName });
  if (rows.length === 0) return absent("no_match", `No callees found for '${symbolIdOrName}'`);

  const results: SymbolRecord[] = rows.map((r: Record<string, unknown>) => ({
    id:         r["id"] as string,
    label:      r["label"] as string,
    type:       r["type"] as string,
    filePath:   r["filePath"] as string,
    confidence: r["confidence"] as number ?? 0,
    properties: r["props"] as Record<string, unknown> ?? {},
  }));
  return success(results, 0.85);
}

export async function getFunctionSignature(
  input: z.infer<typeof GetFunctionSignatureInputSchema>,
  graph: GraphClient,
): Promise<ToolResult<FunctionSignature>> {
  const { scope, symbolIdOrName } = input;

  const cypher = `
    MATCH (n { scope: $scope })
    WHERE (n.id = $symbolIdOrName OR n.label = $symbolIdOrName)
      AND n.type IN ["Function","Method"]
    RETURN n.id AS id, n.label AS label,
           coalesce(n.paramNames, []) AS paramNames,
           n.returnType AS returnType,
           coalesce(n.filePath, n.path, '') AS filePath,
           n.confidence AS confidence
    LIMIT 1
  `;

  const rows = await graph.runRead<Record<string, unknown>>(cypher, { scope, symbolIdOrName });
  if (rows.length === 0) return absent("no_match", `No function/method named '${symbolIdOrName}'`);

  const r = rows[0]!;
  return success({
    id:          r["id"] as string,
    label:       r["label"] as string,
    paramNames:  (r["paramNames"] as string[]) ?? [],
    returnType:  r["returnType"] as string | undefined,
    filePath:    r["filePath"] as string,
    confidence:  r["confidence"] as number ?? 0,
  }, r["confidence"] as number ?? 0.85);
}
