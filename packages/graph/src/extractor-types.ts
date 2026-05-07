/**
 * Shared extractor plugin contract types (ADR-0003).
 * All extractors must implement ExtractorPlugin.
 */

import type { GraphClient } from "./client.js";

export interface ExtractorManifest {
  name:        string;
  version:     string;
  description: string;
  /** File glob patterns or language identifiers this extractor handles */
  handles?:    string[];
  /** Node type names this extractor emits */
  nodeTypes?:  readonly string[];
  /** Edge type names this extractor emits */
  edgeTypes?:  readonly string[];
  /** Language identifiers (for AST-based extractors) */
  languages?:  readonly string[];
  /** Kept for backwards compat */
  emits?:      string[];
}

export interface ExtractorLog {
  info:  (...args: unknown[]) => void;
  warn:  (...args: unknown[]) => void;
  error: (...args: unknown[]) => void;
}

export interface ExtractorContext {
  scope:     string;
  commitSha: string;
  repoRoot:  string;
  /** Changed file paths relative to repo root (empty = full scan) */
  dirtySet:  string[];
  graph:     GraphClient;
  log:       ExtractorLog;
}

export interface ExtractorResult {
  nodesWritten:     number;
  edgesWritten:     number;
  nodesInvalidated: number;
  warnings:         string[];
  durationMs:       number;
}

/**
 * The plugin interface all extractors must implement.
 * Return value is optional — simple extractors can return void.
 */
export interface ExtractorPlugin {
  readonly manifest: ExtractorManifest;
  extract(ctx: ExtractorContext): Promise<ExtractorResult | void>;
}
