/**
 * packages/repo-map/src/types.ts
 * Public types for the repo-map package.
 */

import { z } from "zod";

// ── Input ─────────────────────────────────────────────────────────────────────

export const RepoMapInputSchema = z.object({
  /** Repository scope: "<org>/<repo>", e.g. "acme/web" */
  scope:       z.string().min(1),
  /**
   * Maximum tokens to use for the output.
   * Token approximation: 1 token ≈ 4 characters.
   * Typical values: 2000 (tight), 4000 (standard), 8000 (large context).
   */
  tokenBudget: z.number().int().min(100).max(32_000).default(4000),
  /**
   * Optional path prefix filter — only include files under this path.
   * e.g. "src/billing" would restrict to files starting with that prefix.
   */
  pathFilter:  z.string().optional(),
  /**
   * Only include symbols that are exported. Reduces noise.
   * Default: true (only exported symbols).
   */
  exportedOnly: z.boolean().default(true),
  /**
   * Minimum confidence for symbols to be included.
   * Default: 0.85 (ast + framework_parser; excludes llm-inferred).
   */
  minConfidence: z.number().min(0).max(1).default(0.85),
});

export type RepoMapInput = z.infer<typeof RepoMapInputSchema>;

// ── Internal data structures ───────────────────────────────────────────────────

export interface SymbolEntry {
  urn:          string;
  name:         string;
  qualifiedName: string;
  kind:         string;   // "Class" | "Function" | "Method" | etc.
  exported:     boolean;
  signature:    string;   // compact one-liner: "async charge(req: ChargeRequest): Promise<ChargeResult>"
  lineStart:    number;
  inDegree:     number;   // number of incoming `calls` edges — used for ranking
  confidence:   number;
}

export interface FileEntry {
  urn:          string;
  path:         string;
  language:     string;
  lineCount:    number;
  symbols:      SymbolEntry[];
  /** Aggregate in-degree across all symbols — used to rank files. */
  totalInDegree: number;
}

// ── Output ────────────────────────────────────────────────────────────────────

export interface RepoMapOutput {
  /** The formatted, token-budgeted repo map text. */
  text:              string;
  /** Approximate token count of `text`. */
  tokenCount:        number;
  /** How many files were included out of total discovered. */
  filesIncluded:     number;
  filesTotalInScope: number;
  /** How many symbols were included. */
  symbolsIncluded:   number;
  /** The commit SHA of the extraction run (from most recent node). */
  extractedAtCommit: string | null;
  /** True if the budget forced truncation of some files. */
  truncated:         boolean;
}
