import * as fs from "node:fs";
import * as path from "node:path";
import { createHash } from "node:crypto";
import yaml from "js-yaml";
import { buildUrn } from "@company-brain/schema";
import type { ExtractorPlugin, ExtractorContext } from "@company-brain/graph";
import type { NodeEnvelope, EdgeEnvelope } from "@company-brain/schema";

function sha256short(c: string) { return createHash("sha256").update(c).digest("hex").slice(0, 16); }

// Paths to search for OpenAPI specs (polyglot — covers Spring Boot, Rails, JS/TS, Python)
const SPEC_PATTERNS = [
  "openapi.yaml","openapi.yml","openapi.json",
  "swagger.yaml","swagger.yml","swagger.json",
  "api/openapi.yaml","api/openapi.json",
  "docs/openapi.yaml","docs/openapi.json",
  "spec/openapi.yaml","spec/openapi.json",
  "contracts/openapi.yaml",
  "src/main/resources/static/v3/api-docs.json",
  "src/main/resources/static/swagger.json",
];

function findOpenApiFiles(repoRoot: string): string[] {
  const found: string[] = [];
  // Try known paths first
  for (const p of SPEC_PATTERNS) {
    const abs = path.join(repoRoot, p);
    if (fs.existsSync(abs)) found.push(abs);
  }
  // Also do a shallow recursive search for *openapi*.yaml|json files
  function walk(dir: string, depth: number) {
    if (depth > 4) return;
    if (!fs.existsSync(dir)) return;
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      if (entry.isDirectory() && !["node_modules",".git","dist","build",".next"].includes(entry.name)) {
        walk(path.join(dir, entry.name), depth + 1);
      } else if (entry.isFile() && /openapi|swagger/i.test(entry.name) && /\.(yaml|yml|json)$/.test(entry.name)) {
        const abs = path.join(dir, entry.name);
        if (!found.includes(abs)) found.push(abs);
      }
    }
  }
  walk(repoRoot, 0);
  return [...new Set(found)];
}

export class FrameworkOpenApiExtractor implements ExtractorPlugin {
  readonly name = "framework-openapi";
  readonly languages = ["*"];

  async extract(ctx: ExtractorContext): Promise<{ nodesWritten: number; edgesWritten: number }> {
    const { scope, commitSha, repoRoot, graph, log } = ctx;
    const nodes: NodeEnvelope[] = [];
    const edges: EdgeEnvelope[] = [];

    const specFiles = findOpenApiFiles(repoRoot);
    if (specFiles.length === 0) {
      log.info("[framework-openapi] no OpenAPI specs found, skipping");
      return { nodesWritten: 0, edgesWritten: 0 };
    }

    for (const absPath of specFiles) {
      const relPath = path.relative(repoRoot, absPath);
      const content = fs.readFileSync(absPath, "utf8");
      const checksum = sha256short(content);

      let spec: Record<string, unknown>;
      try {
        spec = (absPath.endsWith(".json") ? JSON.parse(content) : yaml.load(content)) as Record<string, unknown>;
      } catch (e) {
        log.warn(`[framework-openapi] failed to parse ${relPath}: ${e}`);
        continue;
      }

      const info = spec["info"] as Record<string,unknown> | undefined;
      const title = (info?.["title"] as string) ?? relPath;
      const version = (info?.["version"] as string) ?? "unknown";

      const docId = buildUrn("openapi", scope, relPath);
      nodes.push({
        id: docId, label: title, type: "ContractDocument", scope,
        source: "openapi", source_checksum: checksum,
        confidence: 0.90, valid_from_commit: commitSha, valid_to_commit: null,
        properties: { path: relPath, title, version },
      });

      const paths = spec["paths"] as Record<string, Record<string, unknown>> | undefined;
      if (!paths) continue;

      for (const [routePath, methods] of Object.entries(paths)) {
        for (const [method, operation] of Object.entries(methods ?? {})) {
          if (!["get","post","put","patch","delete","head","options"].includes(method)) continue;
          const op = operation as Record<string,unknown>;
          const operationId = (op["operationId"] as string | undefined) ?? `${method}_${routePath.replace(/[^a-zA-Z0-9]/g, "_")}`;
          const summary = (op["summary"] as string | undefined) ?? operationId;
          const tags = (op["tags"] as string[] | undefined) ?? [];

          const endpointId = buildUrn("openapi", scope, relPath, operationId);
          nodes.push({
            id: endpointId, label: `${method.toUpperCase()} ${routePath}`, type: "ContractEndpoint", scope,
            source: "openapi", source_checksum: checksum,
            confidence: 0.90, valid_from_commit: commitSha, valid_to_commit: null,
            properties: {
              operationId, path: routePath, method: method.toUpperCase(),
              summary, tags,
              requestBody: op["requestBody"] ?? null,
              responses: op["responses"] ?? {},
            },
          });
          edges.push({ fromId: docId, toId: endpointId, type: "has_endpoint", source: "openapi", confidence: 0.90 });
        }
      }
    }

    const nodesWritten = await graph.mergeNodes(nodes);
    const edgesWritten = await graph.mergeEdges(edges);
    log.info(`[framework-openapi] wrote ${nodesWritten} nodes, ${edgesWritten} edges`);
    return { nodesWritten, edgesWritten };
  }
}
