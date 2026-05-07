import * as fs from "node:fs";
import * as path from "node:path";
import { createHash } from "node:crypto";
import { buildUrn } from "@company-brain/schema";
import type { ExtractorPlugin, ExtractorContext } from "@company-brain/graph";
import type { NodeEnvelope, EdgeEnvelope } from "@company-brain/schema";

function sha256short(c: string) { return createHash("sha256").update(c).digest("hex").slice(0, 16); }

function findFiles(dir: string, pattern: RegExp): string[] {
  const results: string[] = [];
  const skip = new Set(["node_modules", ".git", "dist", ".next"]);
  function walk(d: string) {
    if (!fs.existsSync(d)) return;
    for (const entry of fs.readdirSync(d, { withFileTypes: true })) {
      if (entry.isDirectory() && !skip.has(entry.name)) walk(path.join(d, entry.name));
      else if (entry.isFile() && pattern.test(entry.name)) results.push(path.join(d, entry.name));
    }
  }
  walk(dir);
  return results;
}

export class FrameworkNextExtractor implements ExtractorPlugin {
  readonly name = "framework-next";
  readonly languages = ["typescript", "javascript"];

  async extract(ctx: ExtractorContext): Promise<{ nodesWritten: number; edgesWritten: number }> {
    const { scope, commitSha, repoRoot, graph, log } = ctx;

    // Only activate if there's an app/ or src/app/ directory
    const appDir = [
      path.join(repoRoot, "app"),
      path.join(repoRoot, "src", "app"),
    ].find(d => fs.existsSync(d));

    if (!appDir) {
      log.info("[framework-next] no app/ directory found, skipping");
      return { nodesWritten: 0, edgesWritten: 0 };
    }

    const nodes: NodeEnvelope[] = [];
    const edges: EdgeEnvelope[] = [];

    const pageFiles = findFiles(appDir, /^(page|layout)\.[jt]sx?$/);
    const apiFiles  = findFiles(appDir, /^route\.[jt]sx?$/);

    for (const absPath of pageFiles) {
      const relPath = path.relative(repoRoot, absPath);
      const content = fs.readFileSync(absPath, "utf8");
      const checksum = sha256short(content);
      const isLayout = path.basename(absPath).startsWith("layout");
      const nodeType = isLayout ? "Layout" : "Screen";
      const id = buildUrn("ts-ast", scope, relPath);
      // derive route from file path
      const routeSegment = path.relative(appDir, path.dirname(absPath)).replace(/\\/g, "/") || "/";
      nodes.push({
        id, label: relPath, type: nodeType, scope,
        source: "ts-ast", source_checksum: checksum,
        confidence: 0.95, valid_from_commit: commitSha, valid_to_commit: null,
        properties: { path: relPath, route: routeSegment },
      });
    }

    for (const absPath of apiFiles) {
      const relPath = path.relative(repoRoot, absPath);
      const content = fs.readFileSync(absPath, "utf8");
      const checksum = sha256short(content);
      const id = buildUrn("ts-ast", scope, relPath);
      const routeDir = path.relative(appDir, path.dirname(absPath)).replace(/\\/g, "/");
      const route = "/" + routeDir.replace(/^api\//, "");
      // detect exported HTTP methods
      const methods = ["GET","POST","PUT","PATCH","DELETE"].filter(m =>
        new RegExp(`export\\s+(?:async\\s+)?function\\s+${m}\\b`).test(content) ||
        new RegExp(`export\\s+const\\s+${m}\\s*=`).test(content)
      );
      nodes.push({
        id, label: relPath, type: "APIRoute", scope,
        source: "ts-ast", source_checksum: checksum,
        confidence: 0.95, valid_from_commit: commitSha, valid_to_commit: null,
        properties: { path: relPath, route, methods },
      });
    }

    const nodesWritten = await graph.mergeNodes(nodes);
    const edgesWritten = await graph.mergeEdges(edges);
    log.info(`[framework-next] wrote ${nodesWritten} nodes`);
    return { nodesWritten, edgesWritten };
  }
}
