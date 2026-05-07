import { buildUrn } from "@company-brain/schema";
import type { ExtractorPlugin, ExtractorContext } from "@company-brain/graph";
import type { NodeEnvelope, EdgeEnvelope } from "@company-brain/schema";
import { createHash } from "node:crypto";

function sha256short(c: string) { return createHash("sha256").update(c).digest("hex").slice(0, 16); }

/**
 * DriftDetector: post-pass extractor.
 * Reads ContractEndpoint nodes and APIRoute nodes connected by implements_contract edges,
 * compares declared response schemas vs. actual HTTP methods, and emits DriftSignal nodes.
 *
 * Runs LAST in the pipeline — requires implements_contract edges to exist.
 */
export class DriftDetector implements ExtractorPlugin {
  readonly name = "drift-detector";
  readonly languages = ["*"];

  async extract(ctx: ExtractorContext): Promise<{ nodesWritten: number; edgesWritten: number }> {
    const { scope, commitSha, graph, log } = ctx;

    // Find all (APIRoute)-[:implements_contract]->(ContractEndpoint) pairs
    const pairs = await graph.runRead<{
      routeId: string; routePath: string; routeMethods: string[];
      endpointId: string; endpointPath: string; endpointMethod: string; operationId: string;
    }>(
      `MATCH (r { scope: $scope, type: "APIRoute" })-[:implements_contract]->(c { scope: $scope, type: "ContractEndpoint" })
       RETURN r.id AS routeId, r.route AS routePath, r.methods AS routeMethods,
              c.id AS endpointId, c.path AS endpointPath, c.method AS endpointMethod, c.operationId AS operationId`,
      { scope },
    );

    if (pairs.length === 0) {
      log.info("[drift-detector] no implements_contract pairs found, skipping");
      return { nodesWritten: 0, edgesWritten: 0 };
    }

    const nodes: NodeEnvelope[] = [];
    const edges: EdgeEnvelope[] = [];

    for (const pair of pairs) {
      const declaredMethod = pair.endpointMethod?.toUpperCase() ?? "";
      const implementedMethods = (pair.routeMethods ?? []).map((m: string) => m.toUpperCase());

      // Check: method declared in contract but not implemented
      if (declaredMethod && implementedMethods.length > 0 && !implementedMethods.includes(declaredMethod)) {
        const driftId = buildUrn("drift", scope, pair.operationId ?? pair.endpointId, "method-mismatch");
        const description = `Contract declares ${declaredMethod} but route implements [${implementedMethods.join(",")}]`;
        nodes.push({
          id: driftId, label: `Method mismatch: ${pair.operationId}`, type: "DriftSignal", scope,
          source: "drift", source_checksum: sha256short(description),
          confidence: 0.80, valid_from_commit: commitSha, valid_to_commit: null,
          properties: {
            operationId: pair.operationId, severity: "breaking",
            driftKind: "method_mismatch",
            contractMethod: declaredMethod, implementedMethods,
            contractPath: pair.endpointPath, routePath: pair.routePath,
            description,
          },
        });
        edges.push({ fromId: pair.endpointId, toId: driftId, type: "has_drift", source: "drift", confidence: 0.80 });
        edges.push({ fromId: pair.routeId, toId: driftId, type: "has_drift", source: "drift", confidence: 0.80 });
      }

      // Check: route path doesn't match contract path (ignoring param syntax differences)
      const normalizePathPattern = (p: string) => p.replace(/\{[^}]+\}|\[([^\]]+)\]/g, ":param").replace(/\/+$/, "");
      const contractNorm = normalizePathPattern(pair.endpointPath ?? "");
      const routeNorm = normalizePathPattern(pair.routePath ?? "");
      if (contractNorm && routeNorm && contractNorm !== routeNorm) {
        const driftId = buildUrn("drift", scope, pair.operationId ?? pair.endpointId, "path-mismatch");
        const description = `Contract path ${pair.endpointPath} ≠ route path ${pair.routePath}`;
        nodes.push({
          id: driftId, label: `Path mismatch: ${pair.operationId}`, type: "DriftSignal", scope,
          source: "drift", source_checksum: sha256short(description),
          confidence: 0.75, valid_from_commit: commitSha, valid_to_commit: null,
          properties: {
            operationId: pair.operationId, severity: "warning",
            driftKind: "path_mismatch",
            contractPath: pair.endpointPath, routePath: pair.routePath,
            description,
          },
        });
        edges.push({ fromId: pair.endpointId, toId: driftId, type: "has_drift", source: "drift", confidence: 0.75 });
      }
    }

    const nodesWritten = await graph.mergeNodes(nodes);
    const edgesWritten = await graph.mergeEdges(edges);
    log.info(`[drift-detector] wrote ${nodesWritten} drift signals`);
    return { nodesWritten, edgesWritten };
  }
}
