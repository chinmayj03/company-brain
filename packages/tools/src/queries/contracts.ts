import { z } from "zod";
import type { GraphClient } from "@company-brain/graph";
import { success, absent, type ToolResult } from "../contract.js";

export const GetContractForEndpointInputSchema = z.object({
  scope:  z.string().min(1),
  path:   z.string().min(1),
  method: z.enum(["GET","POST","PUT","PATCH","DELETE","HEAD","OPTIONS"]),
});
export type GetContractForEndpointInput = z.infer<typeof GetContractForEndpointInputSchema>;

export const ListEndpointsImplementingContractInputSchema = z.object({
  scope:      z.string().min(1),
  contractId: z.string().min(1),
  limit:      z.number().int().min(1).max(100).default(20),
});
export type ListEndpointsImplementingContractInput = z.infer<typeof ListEndpointsImplementingContractInputSchema>;

export const GetDriftSignalsInputSchema = z.object({
  scope:    z.string().min(1),
  severity: z.enum(["breaking","warning","info"]).optional(),
  limit:    z.number().int().min(1).max(100).default(20),
});
export type GetDriftSignalsInput = z.infer<typeof GetDriftSignalsInputSchema>;

export interface ContractEndpointRecord {
  id:          string;
  operationId: string;
  path:        string;
  method:      string;
  summary:     string;
  tags:        string[];
  requestBody: unknown;
  responses:   unknown;
  confidence:  number;
}

export interface DriftSignalRecord {
  urn:          string;
  operationId:  string;
  severity:     string;
  driftKind:    string;
  description:  string;
  contractPath: string;
  confidence:   number;
}

export interface EndpointImplementationRecord {
  routeId:    string;
  routeLabel: string;
  routePath:  string;
  methods:    string[];
  filePath:   string;
  confidence: number;
}

export async function getContractForEndpoint(
  input: GetContractForEndpointInput,
  graph: GraphClient,
): Promise<ToolResult<ContractEndpointRecord>> {
  const { scope, path: routePath, method } = input;

  const rows = await graph.runRead<Record<string, unknown>>(
    `MATCH (c { scope: $scope, type: "ContractEndpoint" })
     WHERE c.path = $path AND c.method = $method
     RETURN c.id AS id, c.operationId AS operationId, c.path AS path, c.method AS method,
            coalesce(c.summary, '') AS summary, coalesce(c.tags, []) AS tags,
            c.requestBody AS requestBody, c.responses AS responses, c.confidence AS confidence
     LIMIT 1`,
    { scope, path: routePath, method },
  );

  if (rows.length === 0) return absent("no_match", `No contract for ${method} ${routePath}`);
  const r = rows[0]!;
  return success({
    id:          r["id"] as string,
    operationId: r["operationId"] as string,
    path:        r["path"] as string,
    method:      r["method"] as string,
    summary:     r["summary"] as string,
    tags:        (r["tags"] as string[]) ?? [],
    requestBody: r["requestBody"],
    responses:   r["responses"],
    confidence:  r["confidence"] as number ?? 0.90,
  }, r["confidence"] as number ?? 0.90);
}

export async function listEndpointsImplementingContract(
  input: ListEndpointsImplementingContractInput,
  graph: GraphClient,
): Promise<ToolResult<EndpointImplementationRecord[]>> {
  const { scope, contractId, limit } = input;

  const rows = await graph.runRead<Record<string, unknown>>(
    `MATCH (r { scope: $scope, type: "APIRoute" })-[:implements_contract]->(c { scope: $scope })
     WHERE c.id = $contractId OR c.operationId = $contractId
     RETURN r.id AS routeId, r.label AS routeLabel, r.route AS routePath,
            coalesce(r.methods, []) AS methods,
            coalesce(r.path, r.filePath, '') AS filePath,
            r.confidence AS confidence
     LIMIT $limit`,
    { scope, contractId, limit },
  );

  if (rows.length === 0) return absent("no_match", `No implementations for contract '${contractId}'`);

  return success(rows.map((r: Record<string, unknown>) => ({
    routeId:    r["routeId"] as string,
    routeLabel: r["routeLabel"] as string,
    routePath:  r["routePath"] as string,
    methods:    (r["methods"] as string[]) ?? [],
    filePath:   r["filePath"] as string,
    confidence: r["confidence"] as number ?? 0,
  })), 0.85);
}

export async function getDriftSignals(
  input: GetDriftSignalsInput,
  graph: GraphClient,
): Promise<ToolResult<DriftSignalRecord[]>> {
  const { scope, severity, limit } = input;

  const severityClause = severity ? "AND d.severity = $severity" : "";
  const rows = await graph.runRead<Record<string, unknown>>(
    `MATCH (d { scope: $scope, type: "DriftSignal" })
     WHERE d.valid_to_commit IS NULL ${severityClause}
     RETURN d.id AS urn, coalesce(d.operationId,'') AS operationId,
            d.severity AS severity, d.driftKind AS driftKind,
            coalesce(d.description,'') AS description,
            coalesce(d.contractPath,'') AS contractPath,
            d.confidence AS confidence
     ORDER BY d.confidence DESC
     LIMIT $limit`,
    { scope, severity: severity ?? null, limit },
  );

  if (rows.length === 0) return absent("no_match", "No drift signals found");

  return success(rows.map((r: Record<string, unknown>) => ({
    urn:          r["urn"] as string,
    operationId:  r["operationId"] as string,
    severity:     r["severity"] as string,
    driftKind:    r["driftKind"] as string,
    description:  r["description"] as string,
    contractPath: r["contractPath"] as string,
    confidence:   r["confidence"] as number ?? 0,
  })), 0.75);
}
