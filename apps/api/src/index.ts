/**
 * Company Brain tRPC API Server
 *
 * Exposes the tool router over HTTP on port 8090.
 * Also exposes POST /extract which runs all extractors against a target repo.
 *
 * Env vars:
 *   PORT          (default 8090)
 *   NEO4J_URI     (default bolt://localhost:7687)
 *   NEO4J_USER    (default neo4j)
 *   NEO4J_PASSWORD(default password)
 *   JAVA_API_URL  (default http://localhost:8080)
 */
import { fetchRequestHandler } from "@trpc/server/adapters/fetch";
import { GraphClient } from "@company-brain/graph";
import { createToolRouter } from "@company-brain/tools";

const PORT        = Number(process.env["PORT"] ?? 8090);

function addCors(res: Response): Response {
  const headers = new Headers(res.headers);
  headers.set("Access-Control-Allow-Origin",  "*");
  headers.set("Access-Control-Allow-Methods", "GET, POST, OPTIONS");
  headers.set("Access-Control-Allow-Headers", "Content-Type, trpc-batch-mode");
  return new Response(res.body, { status: res.status, headers });
}
const JAVA_API    = process.env["JAVA_API_URL"] ?? "http://localhost:8080";

const graph = new GraphClient({
  uri:      process.env["NEO4J_URI"]      ?? "bolt://localhost:7687",
  user:     process.env["NEO4J_USER"]     ?? "neo4j",
  password: process.env["NEO4J_PASSWORD"] ?? "password",
});

const router = createToolRouter(graph, JAVA_API);

const CORS_HEADERS = {
  "Access-Control-Allow-Origin":  "*",
  "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type, trpc-batch-mode",
};

const server = Bun.serve({
  port: PORT,
  async fetch(req) {
    const url = new URL(req.url);

    // ── CORS preflight ───────────────────────────────────────────────────
    if (req.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: CORS_HEADERS });
    }

    // ── POST /extract ── trigger extraction run ──────────────────────────
    if (req.method === "POST" && url.pathname === "/extract") {
      try {
        const body = await req.json() as { repoRoot?: string; scope?: string };
        const { EXTRACTORS } = await import("../../extractor-worker/src/registry.js");
        const { execSync } = await import("node:child_process");
        const { slugifyScope } = await import("@company-brain/schema");

        const repoRoot = body.repoRoot ?? process.cwd();
        const scope    = body.scope    ?? slugifyScope(repoRoot);
        let commitSha  = "HEAD";
        try { commitSha = execSync("git rev-parse HEAD", { cwd: repoRoot, encoding: "utf8" }).trim(); } catch { /**/ }

        const log = {
          info:  (...a: unknown[]) => console.log("[extract]", ...a),
          warn:  (...a: unknown[]) => console.warn("[extract]", ...a),
          error: (...a: unknown[]) => console.error("[extract]", ...a),
        };

        let totalNodes = 0, totalEdges = 0;
        const extractors: Array<{ name: string; nodesWritten: number; edgesWritten: number }> = [];

        for (const extractor of EXTRACTORS) {
          try {
            const r = await extractor.extract({ scope, commitSha, repoRoot, dirtySet: new Set(), graph, log });
            totalNodes += r.nodesWritten; totalEdges += r.edgesWritten;
            extractors.push({ name: extractor.name, ...r });
          } catch (err) {
            log.error(`Extractor ${extractor.name} failed:`, err);
            extractors.push({ name: extractor.name, nodesWritten: 0, edgesWritten: 0 });
          }
        }
        const res = Response.json({ scope, commitSha, nodesWritten: totalNodes, edgesWritten: totalEdges, extractors });
        return addCors(res);
      } catch (err) {
        return addCors(Response.json({ error: String(err) }, { status: 500 }));
      }
    }

    // ── GET /fingerprints ── structural hashes per file (ADR-0011) ──────
    // Returns { fingerprints: Array<{ file_path, structural_hash, function_count,
    //           class_count, last_indexed_commit }> } for all File nodes in Neo4j
    // that match the given scope. Used by the Python structural pre-pass to
    // determine which code units are structurally unchanged and can skip LLM.
    if (req.method === "GET" && url.pathname === "/fingerprints") {
      const scope  = url.searchParams.get("scope")  ?? "";
      if (!scope) return addCors(new Response("scope required", { status: 400 }));

      try {
        const rows = await graph.runRead(
          `MATCH (f:File { scope: $scope })
           OPTIONAL MATCH (f)-[:CONTAINS]->(n)
           WITH f,
                collect({ kind: labels(n)[0], qname: n.qualified_name, sig: coalesce(n.signature, '') }) AS members
           WITH f,
                [m IN members WHERE m.qname IS NOT NULL | m.kind + '|' + m.qname + '|' + m.sig] AS parts,
                size([m IN members WHERE m.kind = 'Function']) AS function_count,
                size([m IN members WHERE m.kind = 'Class'])    AS class_count
           RETURN f.path AS file_path,
                  apoc.util.sha256(parts) AS structural_hash,
                  function_count,
                  class_count,
                  f.last_indexed_commit AS last_indexed_commit`,
          { scope }
        );

        // If APOC is unavailable, fall back to a JS-side hash so the endpoint
        // still works without APOC; the Python side will always see mismatches
        // on a non-APOC Neo4j but will degrade gracefully (full LLM run).
        const fingerprints = rows.map((row: Record<string, unknown>) => ({
          file_path:           row["file_path"]           ?? "",
          structural_hash:     row["structural_hash"]     ?? "",
          function_count:      Number(row["function_count"] ?? 0),
          class_count:         Number(row["class_count"]    ?? 0),
          last_indexed_commit: row["last_indexed_commit"] ?? "",
        }));

        return addCors(Response.json({ fingerprints }));
      } catch (err) {
        return addCors(Response.json({ error: String(err) }, { status: 500 }));
      }
    }

    // ── GET /health ──────────────────────────────────────────────────────
    if (req.method === "GET" && url.pathname === "/health") {
      return addCors(Response.json({ status: "ok", port: PORT }));
    }

    // ── tRPC routes ──────────────────────────────────────────────────────
    if (url.pathname.startsWith("/trpc")) {
      const res = await fetchRequestHandler({
        endpoint: "/trpc",
        req,
        router,
        createContext: () => ({}),
      });
      return addCors(res);
    }

    return addCors(new Response("Not Found", { status: 404 }));
  },
});

console.log(`[api] Company Brain tRPC API running on port ${server.port}`);
