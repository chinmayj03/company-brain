#!/usr/bin/env bun
/**
 * Company Brain CLI — cb
 *
 * Commands:
 *   cb explore <git-url-or-local-path>   Clone/open repo, run all extractors
 *   cb knowledge-query <question>        Query the graph via tRPC
 *   cb health [--scope <scope>]          Check graph + Java API health
 */
import { execSync, spawnSync } from "node:child_process";
import * as fs   from "node:fs";
import * as os   from "node:os";
import * as path from "node:path";
import { GraphClient } from "@company-brain/graph";
import { slugifyScope } from "@company-brain/schema";
import { createToolRouter } from "@company-brain/tools";

// ── Helpers ──────────────────────────────────────────────────────────────────

function log(...a: unknown[]) { console.log("[cb]", ...a); }

function usage() {
  console.log(`
Company Brain CLI

Usage:
  cb explore <url-or-path>           Extract knowledge from a codebase
  cb knowledge-query <question>      Ask a natural-language question
  cb health [--scope <scope>]        Show graph health stats

Environment:
  CB_API_URL       tRPC API endpoint  (default: http://localhost:8090)
  NEO4J_URI        Neo4j bolt URI     (default: bolt://localhost:7687)
  NEO4J_USER       Neo4j username     (default: neo4j)
  NEO4J_PASSWORD   Neo4j password     (default: password)
  JAVA_API_URL     Java API endpoint  (default: http://localhost:8080)
`);
}

// ── Commands ──────────────────────────────────────────────────────────────────

async function cmdExplore(target: string) {
  let repoRoot: string;

  if (target.startsWith("http") || target.startsWith("git@")) {
    // Clone into a temp directory
    const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "cb-explore-"));
    log(`Cloning ${target} into ${tmpDir} ...`);
    const res = spawnSync("git", ["clone", "--depth=1", target, tmpDir], { stdio: "inherit" });
    if (res.status !== 0) { console.error("git clone failed"); process.exit(1); }
    repoRoot = tmpDir;
  } else {
    repoRoot = path.resolve(target);
    if (!fs.existsSync(repoRoot)) { console.error(`Path not found: ${repoRoot}`); process.exit(1); }
  }

  const scope = slugifyScope(repoRoot);
  log(`Scope: ${scope}`);

  // Check if tRPC API is available — if so, delegate to POST /extract
  const apiUrl = process.env["CB_API_URL"] ?? "http://localhost:8090";
  try {
    const res = await fetch(`${apiUrl}/health`);
    if (res.ok) {
      log(`Delegating to API at ${apiUrl}/extract ...`);
      const extractRes = await fetch(`${apiUrl}/extract`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ repoRoot, scope }),
      });
      const result = await extractRes.json();
      log(`Done: ${JSON.stringify(result, null, 2)}`);
      return;
    }
  } catch { /* API not available, run locally */ }

  // Run extractors locally
  log("API not available — running extractors in-process ...");
  const { EXTRACTORS } = await import("../../extractor-worker/src/registry.js");
  const graph = new GraphClient({
    uri:      process.env["NEO4J_URI"]      ?? "bolt://localhost:7687",
    user:     process.env["NEO4J_USER"]     ?? "neo4j",
    password: process.env["NEO4J_PASSWORD"] ?? "password",
  });

  let commitSha = "HEAD";
  try { commitSha = execSync("git rev-parse HEAD", { cwd: repoRoot, encoding: "utf8" }).trim(); } catch { /**/ }

  const extractLog = { info: log, warn: (...a: unknown[]) => console.warn(...a), error: (...a: unknown[]) => console.error(...a) };

  for (const extractor of EXTRACTORS) {
    const r = await extractor.extract({ scope, commitSha, repoRoot, dirtySet: new Set(), graph, log: extractLog });
    log(`${extractor.name}: ${r.nodesWritten} nodes, ${r.edgesWritten} edges`);
  }
  await graph.close();
  log("Extraction complete.");
}

async function cmdKnowledgeQuery(question: string) {
  const graph = new GraphClient({
    uri:      process.env["NEO4J_URI"]      ?? "bolt://localhost:7687",
    user:     process.env["NEO4J_USER"]     ?? "neo4j",
    password: process.env["NEO4J_PASSWORD"] ?? "password",
  });
  const router = createToolRouter(graph);

  // Try to answer via symbol search
  const scope = process.env["CB_SCOPE"] ?? "";
  if (!scope) { console.error("Set CB_SCOPE env var to the scope slug before querying."); process.exit(1); }

  const caller = await router.createCaller({});
  const result = await caller.findSymbol({ scope, pattern: question });
  console.log(JSON.stringify(result, null, 2));
  await graph.close();
}

async function cmdHealth(scope: string) {
  const graph = new GraphClient({
    uri:      process.env["NEO4J_URI"]      ?? "bolt://localhost:7687",
    user:     process.env["NEO4J_USER"]     ?? "neo4j",
    password: process.env["NEO4J_PASSWORD"] ?? "password",
  });
  const router = createToolRouter(graph);
  const caller = await router.createCaller({});
  const result = await caller.health({ scope });
  console.log(JSON.stringify(result, null, 2));
  await graph.close();
}

// ── Entry point ───────────────────────────────────────────────────────────────

const [,, cmd, ...args] = process.argv;

switch (cmd) {
  case "explore":
    if (!args[0]) { usage(); process.exit(1); }
    await cmdExplore(args[0]);
    break;
  case "knowledge-query":
    if (!args[0]) { usage(); process.exit(1); }
    await cmdKnowledgeQuery(args.join(" "));
    break;
  case "health": {
    const scopeIdx = args.indexOf("--scope");
    const scope = scopeIdx >= 0 ? (args[scopeIdx + 1] ?? "") : (process.env["CB_SCOPE"] ?? "");
    if (!scope) { console.error("Provide --scope or set CB_SCOPE"); process.exit(1); }
    await cmdHealth(scope);
    break;
  }
  default:
    usage();
}
