/**
 * Extractor worker entry point.
 * Can be invoked as a one-shot CLI or imported as a module.
 *
 * Usage:
 *   CB_SCOPE=my-org/my-repo REPO_ROOT=/path/to/repo bun run src/index.ts
 */
import { execSync } from "node:child_process";
import { GraphClient } from "@company-brain/graph";
import { slugifyScope } from "@company-brain/schema";
import { EXTRACTORS } from "./registry.js";

const log = {
  info:  (...a: unknown[]) => console.log("[worker]", ...a),
  warn:  (...a: unknown[]) => console.warn("[worker]", ...a),
  error: (...a: unknown[]) => console.error("[worker]", ...a),
};

async function main() {
  const repoRoot = process.env["REPO_ROOT"] ?? process.cwd();
  const scopeEnv = process.env["CB_SCOPE"] ?? "";
  const scope    = scopeEnv || slugifyScope(repoRoot);

  let commitSha = "HEAD";
  try {
    commitSha = execSync("git rev-parse HEAD", { cwd: repoRoot, encoding: "utf8" }).trim();
  } catch {
    log.warn("Could not determine HEAD commit SHA — using 'HEAD'");
  }

  const graph = new GraphClient({
    uri:      process.env["NEO4J_URI"]      ?? "bolt://localhost:7687",
    user:     process.env["NEO4J_USER"]     ?? "neo4j",
    password: process.env["NEO4J_PASSWORD"] ?? "password",
  });

  log.info(`Starting extraction: scope=${scope} commit=${commitSha} root=${repoRoot}`);
  log.info(`Registered extractors: ${EXTRACTORS.map(e => e.name).join(", ")}`);

  let totalNodes = 0, totalEdges = 0;
  const results: Array<{ name: string; nodesWritten: number; edgesWritten: number; durationMs: number }> = [];

  for (const extractor of EXTRACTORS) {
    const start = Date.now();
    try {
      const { nodesWritten, edgesWritten } = await extractor.extract({
        scope, commitSha, repoRoot,
        dirtySet: new Set(),
        graph, log,
      });
      totalNodes += nodesWritten;
      totalEdges += edgesWritten;
      results.push({ name: extractor.name, nodesWritten, edgesWritten, durationMs: Date.now() - start });
    } catch (err) {
      log.error(`Extractor ${extractor.name} failed:`, err);
      results.push({ name: extractor.name, nodesWritten: 0, edgesWritten: 0, durationMs: Date.now() - start });
    }
  }

  await graph.close();

  log.info(`Extraction complete: ${totalNodes} nodes, ${totalEdges} edges`);
  for (const r of results) {
    log.info(`  ${r.name}: ${r.nodesWritten} nodes, ${r.edgesWritten} edges in ${r.durationMs}ms`);
  }

  return { scope, commitSha, totalNodes, totalEdges, extractors: results };
}

main().catch(err => {
  console.error("Fatal:", err);
  process.exit(1);
});
