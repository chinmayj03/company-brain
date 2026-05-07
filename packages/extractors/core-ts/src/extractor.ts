/**
 * packages/extractors/core-ts/src/extractor.ts
 *
 * CoreTsExtractor — Phase 1 TypeScript/JavaScript AST extractor.
 *
 * Emits node types:
 *   File, Directory, Module, Class, Interface, TypeAlias,
 *   Function, Method, Constant, Decorator, ExternalDependency
 *
 * Emits edge types:
 *   contains, declared_in, imports, extends, implements
 *
 * Strategy (two logical passes per file):
 *   1. tree-sitter pass  — fast syntax extraction, no tsconfig required
 *   2. envelope builder  — pure conversion to NodeEnvelope/EdgeEnvelope
 *
 * Incremental: uses dirtySet to re-extract only changed files.
 * When dirtySet is empty → full extraction of all .ts/.tsx/.js/.jsx files.
 */

import path from "path";
import { glob } from "glob";
import { runTreeSitterPass } from "./passes/tree-sitter-pass.js";
import { buildEnvelopes }    from "./passes/envelope-builder.js";
import {
  buildSymbolTable,
  buildCallEdges,
  type CallSiteRecord,
} from "./passes/call-edge-pass.js";
import type { GraphClient }  from "@company-brain/graph";
import type { FilePassResult } from "./types.js";

// ── Extractor contract types (matching ADR-0003) ───────────────────────────────

export interface ExtractorManifest {
  name:        string;
  version:     string;
  description: string;
  handles:     string[];
  emits:       string[];
}

export interface ExtractorContext {
  scope:     string;
  commitSha: string;
  repoRoot:  string;
  dirtySet:  string[];
  graph:     GraphClient;
  log:       {
    info:  (...a: unknown[]) => void;
    warn:  (...a: unknown[]) => void;
    error: (...a: unknown[]) => void;
  };
}

export interface ExtractorResult {
  nodesWritten:     number;
  edgesWritten:     number;
  nodesInvalidated: number;
  warnings:         string[];
  durationMs:       number;
}

// ── CoreTsExtractor ───────────────────────────────────────────────────────────

/** How many files to process in parallel (tree-sitter is CPU-bound). */
const CONCURRENCY = 8;

/** Extensions this extractor handles. */
const HANDLED_EXTS = [".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"];

/** Directories to always skip. */
const SKIP_DIRS = new Set([
  "node_modules", ".git", "dist", "build", ".next", ".turbo",
  "coverage", ".nyc_output", "out", ".cache",
]);

export class CoreTsExtractor {
  readonly manifest: ExtractorManifest = {
    name:        "core-ts",
    version:     "0.1.0",
    description: "TypeScript/JavaScript AST extractor (tree-sitter syntax pass). Extracts file structure, classes, interfaces, functions, imports.",
    handles:     ["typescript_file", "javascript_file"],
    emits: [
      "File", "Directory", "Module", "Class", "Interface",
      "TypeAlias", "Function", "Method", "Constant", "Decorator",
      "ExternalDependency",
    ],
  };

  async extract(ctx: ExtractorContext): Promise<ExtractorResult> {
    const t0 = Date.now();
    const warnings: string[] = [];
    let nodesWritten    = 0;
    let edgesWritten    = 0;
    let nodesInvalidated = 0;

    const extractor = { name: this.manifest.name, version: this.manifest.version };

    // ── Determine which files to process ──────────────────────────────────

    let filesToProcess: string[];

    if (ctx.dirtySet.length > 0) {
      // Incremental: only re-extract files in the dirty set + their importers
      filesToProcess = ctx.dirtySet.filter(f => HANDLED_EXTS.some(e => f.endsWith(e)));
      ctx.log.info(`core-ts: incremental mode — ${filesToProcess.length} dirty files`);
    } else {
      // Full extraction: discover all relevant files
      filesToProcess = await discoverFiles(ctx.repoRoot);
      ctx.log.info(`core-ts: full extraction — ${filesToProcess.length} files found`);
    }

    if (filesToProcess.length === 0) {
      ctx.log.info("core-ts: no files to process");
      return { nodesWritten: 0, edgesWritten: 0, nodesInvalidated: 0, warnings, durationMs: Date.now() - t0 };
    }

    // ── Invalidate nodes from files we're about to re-extract ──────────────

    if (ctx.dirtySet.length > 0) {
      for (const relPath of filesToProcess) {
        const fileUrn = `urn:cb:file:${ctx.scope}:${relPath}`;
        try {
          const n = await ctx.graph.invalidateByPrefix(fileUrn, ctx.commitSha);
          nodesInvalidated += n;
          // Also invalidate symbols in this file
          const symPrefix = `urn:cb:symbol:${ctx.scope}:${relPath}`;
          const sn = await ctx.graph.invalidateByPrefix(symPrefix, ctx.commitSha);
          nodesInvalidated += sn;
        } catch (e) {
          warnings.push(`invalidate ${relPath}: ${e}`);
        }
      }
    }

    // ── Process files with bounded concurrency ─────────────────────────────

    // Collect all envelopes + raw pass results (for call-edge pass)
    const allNodes: import("@company-brain/schema").NodeEnvelope[] = [];
    const allEdges: import("@company-brain/schema").EdgeEnvelope[] = [];
    const passResults: FilePassResult[] = [];

    // Process in chunks to avoid overwhelming tree-sitter
    for (let i = 0; i < filesToProcess.length; i += CONCURRENCY) {
      const chunk = filesToProcess.slice(i, i + CONCURRENCY);
      const results = await Promise.allSettled(
        chunk.map(relPath => this._processFile(relPath, ctx, extractor, warnings))
      );
      for (const r of results) {
        if (r.status === "fulfilled" && r.value) {
          allNodes.push(...r.value.batch.nodes);
          allEdges.push(...r.value.batch.edges);
          passResults.push(r.value.passResult);
        } else if (r.status === "rejected") {
          warnings.push(String(r.reason));
        }
      }
    }

    ctx.log.info(`core-ts: ${allNodes.length} nodes, ${allEdges.length} edges — building call graph...`);

    // ── Call-edge pass (post-file-processing) ─────────────────────────────
    // Build a symbol table from all pass results, then resolve call sites.

    const symbolTable = buildSymbolTable(
      ctx.scope,
      passResults.map(r => ({ filePath: r.file.filePath, symbols: r.symbols }))
    );

    const callSiteRecords: CallSiteRecord[] = passResults.flatMap(r => {
      const fileUrn = `urn:cb:file:${ctx.scope}:${r.file.filePath}`;
      return r.callSites.map(cs => ({
        callerFileUrn: fileUrn,
        callerSymUrn:  cs.callerName
          ? `urn:cb:symbol:${ctx.scope}:${r.file.filePath}:${cs.callerName}`
          : undefined,
        calleeText:  cs.calleeText,
        sourceUri:   `${ctx.repoRoot}/${r.file.filePath}`,
        startLine:   cs.range.startLine,
        commitSha:   ctx.commitSha,
      }));
    });

    const callEdges = buildCallEdges(callSiteRecords, symbolTable, extractor);
    allEdges.push(...callEdges);

    ctx.log.info(`core-ts: ${allNodes.length} nodes, ${allEdges.length} edges (incl. ${callEdges.length} call edges) — writing to graph...`);

    // ── Batch write ────────────────────────────────────────────────────────

    const { written: nw, errors: ne } = await ctx.graph.upsertNodes(allNodes);
    nodesWritten += nw;
    warnings.push(...ne);

    const { written: ew, errors: ee } = await ctx.graph.upsertEdges(allEdges);
    edgesWritten += ew;
    warnings.push(...ee);

    return {
      nodesWritten,
      edgesWritten,
      nodesInvalidated,
      warnings,
      durationMs: Date.now() - t0,
    };
  }

  private async _processFile(
    relPath:   string,
    ctx:       ExtractorContext,
    extractor: { name: string; version: string },
    warnings:  string[],
  ): Promise<{ batch: import("../types.js").WriteBatch; passResult: FilePassResult } | null> {
    const absPath = path.join(ctx.repoRoot, relPath);
    try {
      const passResult = await runTreeSitterPass(absPath, relPath);
      if (!passResult) return null;

      const batch = buildEnvelopes(passResult, ctx.scope, ctx.commitSha, extractor, ctx.repoRoot);
      return { batch, passResult };
    } catch (e) {
      warnings.push(`core-ts: failed to process ${relPath}: ${e}`);
      return null;
    }
  }
}

// ── File discovery ────────────────────────────────────────────────────────────

/**
 * Recursively find all TypeScript/JavaScript source files in repoRoot,
 * excluding ignored directories.
 *
 * Returns repo-relative paths, e.g. ["src/index.ts", "src/utils/format.ts"]
 */
export async function discoverFiles(repoRoot: string): Promise<string[]> {
  const pattern = `**/*.{ts,tsx,js,jsx,mjs,cjs}`;
  const ignore  = [...SKIP_DIRS].map(d => `**/${d}/**`);

  const absolute = await glob(pattern, {
    cwd:    repoRoot,
    ignore,
    absolute: false,
    nodir:    true,
  });

  return absolute.sort();
}
