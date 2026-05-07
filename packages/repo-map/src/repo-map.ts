/**
 * packages/repo-map/src/repo-map.ts
 *
 * getRepoMap() — the main entry point.
 *
 * Algorithm:
 *   1. Query Neo4j for all File nodes in scope (with optional path filter)
 *   2. For each file, query its symbol nodes (Class, Function, Method, etc.)
 *   3. For each symbol, query its call-graph in-degree (incoming `calls` edges)
 *   4. Rank files by aggregate in-degree (most-referenced first)
 *   5. Format into a token-budgeted signature tree
 *
 * Performance target: <500ms for a 5000-file repo at 4000-token budget.
 * Achieved by: single-pass Cypher queries, parallel file batching.
 */

import type { GraphClient }    from "@company-brain/graph";
import { RepoMapInputSchema }  from "./types.js";
import { buildSignature, formatRepoMap } from "./formatter.js";
import type {
  RepoMapInput,
  RepoMapOutput,
  FileEntry,
  SymbolEntry,
} from "./types.js";

export { RepoMapInputSchema };
export type { RepoMapInput, RepoMapOutput };

// ── Symbol kinds that appear in the repo map ──────────────────────────────────
const MAP_KINDS = ["Class", "Interface", "TypeAlias", "Function", "Method", "Constant", "Decorator", "Module"];

// ── Main ──────────────────────────────────────────────────────────────────────

export async function getRepoMap(
  input: RepoMapInput,
  graph: GraphClient,
): Promise<RepoMapOutput> {
  const params = RepoMapInputSchema.parse(input);
  const t0 = Date.now();

  // ── 1. Fetch all File nodes in scope ────────────────────────────────────

  const pathFilter = params.pathFilter ? ` AND n.qualified_name STARTS WITH $pathFilter` : "";

  const fileRows = await graph.query<{
    id: string; path: string; language: string; lineCount: number; commitSha: string;
  }>(
    `MATCH (n:File)
     WHERE n.scope = $scope${pathFilter}
       AND (n.valid_to_commit IS NULL)
     RETURN n.id AS id,
            n.qualified_name AS path,
            n.attr_language  AS language,
            n.attr_line_count AS lineCount,
            n.valid_from_commit AS commitSha
     ORDER BY n.qualified_name`,
    { scope: params.scope, pathFilter: params.pathFilter ?? "" }
  );

  const totalFiles = fileRows.length;
  if (totalFiles === 0) {
    return emptyResult("scope_not_indexed");
  }

  const latestCommit = fileRows[fileRows.length - 1]?.commitSha ?? null;

  // ── 2. Fetch symbols for all files in one query ──────────────────────────

  const fileIds = fileRows.map(f => f.id);

  const symbolRows = await graph.query<{
    fileId:       string;
    symId:        string;
    name:         string;
    qualName:     string;
    kind:         string;
    exported:     boolean;
    lineStart:    number;
    confidence:   number;
    attributes:   Record<string, unknown>;
  }>(
    `MATCH (f:File)-[:contains]->(s:CBNode)
     WHERE f.id IN $fileIds
       AND s.type IN $kinds
       AND (s.valid_to_commit IS NULL)
       AND s.confidence >= $minConf
     RETURN f.id AS fileId,
            s.id AS symId,
            s.name AS name,
            s.qualified_name AS qualName,
            s.type AS kind,
            coalesce(s.attr_exported, false) AS exported,
            coalesce(s.source_range_start_line, 0) AS lineStart,
            s.confidence AS confidence,
            properties(s) AS attributes`,
    { fileIds, kinds: MAP_KINDS, minConf: params.minConfidence }
  );

  // ── 3. Fetch in-degrees for all symbols in one query ────────────────────

  const symIds = symbolRows.map(r => r.symId);

  let inDegreeMap = new Map<string, number>();

  if (symIds.length > 0) {
    const degRows = await graph.query<{ id: string; degree: number }>(
      `MATCH (caller:CBNode)-[:calls]->(s:CBNode)
       WHERE s.id IN $symIds
       RETURN s.id AS id, count(caller) AS degree`,
      { symIds }
    );
    for (const row of degRows) {
      inDegreeMap.set(row.id, Number(row.degree));
    }
  }

  // ── 4. Build FileEntry structures ────────────────────────────────────────

  // Group symbols by fileId
  const symsByFile = new Map<string, typeof symbolRows>();
  for (const row of symbolRows) {
    const arr = symsByFile.get(row.fileId) ?? [];
    arr.push(row);
    symsByFile.set(row.fileId, arr);
  }

  const fileEntries: FileEntry[] = fileRows.map(f => {
    const syms = symsByFile.get(f.id) ?? [];

    const symbolEntries: SymbolEntry[] = syms
      .filter(s => !params.exportedOnly || s.exported)
      .map(s => {
        const inDegree = inDegreeMap.get(s.symId) ?? 0;
        return {
          urn:           s.symId,
          name:          s.name,
          qualifiedName: s.qualName,
          kind:          s.kind,
          exported:      Boolean(s.exported),
          signature:     buildSignature(s.kind, s.name, s.attributes as Record<string, unknown>),
          lineStart:     Number(s.lineStart),
          inDegree,
          confidence:    Number(s.confidence),
        };
      });

    const totalInDegree = symbolEntries.reduce((s, sym) => s + sym.inDegree, 0);

    return {
      urn:          f.id,
      path:         f.path,
      language:     f.language ?? "unknown",
      lineCount:    Number(f.lineCount ?? 0),
      symbols:      symbolEntries,
      totalInDegree,
    };
  });

  // ── 5. Format ────────────────────────────────────────────────────────────

  const formatted = formatRepoMap(fileEntries, {
    exportedOnly:  params.exportedOnly,
    tokenBudget:   params.tokenBudget,
    includeHeader: true,
    scope:         params.scope,
  });

  return {
    text:              formatted.text,
    tokenCount:        formatted.tokenCount,
    filesIncluded:     formatted.filesIncluded,
    filesTotalInScope: totalFiles,
    symbolsIncluded:   formatted.symbolsIncluded,
    extractedAtCommit: latestCommit,
    truncated:         formatted.truncated,
  };
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function emptyResult(reason: string): RepoMapOutput {
  return {
    text:              `# No data found (${reason})`,
    tokenCount:        5,
    filesIncluded:     0,
    filesTotalInScope: 0,
    symbolsIncluded:   0,
    extractedAtCommit: null,
    truncated:         false,
  };
}
