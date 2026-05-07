/**
 * packages/memory/src/types.ts
 *
 * Type definitions for the shared memory layer — tiered context compression
 * system that solves context window rot and context re-discovery waste.
 *
 * Architecture:
 *   Hot  tier — immediately injected into agent context (~2000 tokens)
 *   Warm tier — loaded on demand when hot tier has gaps
 *   Cold tier — archived URN references only; fetched individually if needed
 */

import { z } from "zod";

// ── Memory tiers ─────────────────────────────────────────────────────────────

export type MemoryTier = "hot" | "warm" | "cold";

// ── File-level memory ────────────────────────────────────────────────────────

export interface FileMemory {
  /** Absolute or repo-relative file path */
  filePath: string;
  /** One-line purpose description */
  purpose: string;
  /** Exported symbol names (functions, classes, constants) */
  exports: string[];
  /** Key symbols the file references from other modules */
  keySymbols: string[];
  /** Git commit SHA when this file was last modified */
  lastModifiedCommit: string;
  /** 0–1: how out-of-date this memory entry is */
  stalenessScore: number;
}

// ── Symbol-level memory ──────────────────────────────────────────────────────

export type SymbolKind =
  | "function"
  | "class"
  | "method"
  | "constant"
  | "interface"
  | "type"
  | "enum";

export interface SymbolMemory {
  /** Full Company Brain URN — urn:cb:symbol:<scope>:<file>:<qualifiedName> */
  urn: string;
  /** Short unqualified name */
  name: string;
  kind: SymbolKind;
  /** TypeScript / language-level signature */
  signature: string;
  /** URNs of symbols that call this one */
  callers: string[];
  /** URNs of symbols this one calls */
  callees: string[];
  /** LLM-authored one-paragraph context summary */
  contextSummary: string;
  /** Extractor confidence at time of last extraction (0–1) */
  confidence: number;
  /** 0–1 staleness score */
  stalenessScore: number;
}

// ── Tiers ────────────────────────────────────────────────────────────────────

export interface HotTier {
  /** Top 30 files by edge-count / recent activity */
  activeFiles: FileMemory[];
  /** Commit SHAs + messages for the last 7 days */
  recentCommits: RecentCommit[];
  /** Open drift signals — schema/contract mismatches detected */
  openDriftSignals: DriftSignalSummary[];
  /** Free-form project context (tech stack, key invariants, etc.) */
  projectContext: string;
  /** One-line summaries of known API contracts */
  apiContracts: ApiContractSummary[];
}

export interface RecentCommit {
  sha: string;
  message: string;
  author: string;
  timestamp: string;
}

export interface DriftSignalSummary {
  urn: string;
  severity: "breaking" | "warning" | "info";
  description: string;
  /** Field names that triggered the drift detection */
  detectedFields: string[];
}

export interface ApiContractSummary {
  operationId: string;
  method: string;
  path: string;
  /** One-line description */
  summary: string;
  deprecated: boolean;
}

export interface WarmTier {
  /** Top 50 symbols by caller count */
  frequentSymbols: SymbolMemory[];
  /** Known data shape assumptions (e.g. nullable fields, enums) */
  dataAssumptions: DataAssumption[];
  /** Tracked bugs / TODOs / tech-debt notes */
  knownIssues: KnownIssue[];
}

export interface DataAssumption {
  subject: string;
  assumption: string;
  source: string;
  confidence: number;
}

export interface KnownIssue {
  id: string;
  title: string;
  severity: "critical" | "high" | "medium" | "low";
  affectedUrns: string[];
  notes: string;
}

export interface ColdTier {
  /**
   * URNs of archived context nodes.
   * Full content NOT stored here — fetch individually via GraphClient.getNode().
   */
  archivedContextUrns: string[];
}

// ── Top-level snapshot ───────────────────────────────────────────────────────

export interface ScopeSnapshot {
  /** Schema version — bump on breaking changes */
  version: "1";
  /** Org/repo scope: e.g. "acme/web" */
  scope: string;
  /** Git commit SHA when this snapshot was generated */
  generatedAtCommit: string;
  /** ISO-8601 timestamp */
  generatedAt: string;
  hotTier: HotTier;
  warmTier: WarmTier;
  coldTier: ColdTier;
  /**
   * ISO-8601 timestamp after which this snapshot should be re-generated.
   * Set to null if snapshot is still fresh.
   */
  staleSince: string | null;
}

// ── Staleness report ─────────────────────────────────────────────────────────

export interface StalenessReport {
  /** URNs whose staleness score exceeds the stale threshold */
  staleNodes: string[];
  /** URNs that are approaching the threshold and should be refreshed soon */
  refreshCandidates: string[];
  /** Rough token cost to refresh all stale nodes (estimated at 300 tokens/node) */
  estimatedTokenCost: number;
}

// ── Zod schemas (for snapshot persistence / validation) ──────────────────────

export const RecentCommitSchema = z.object({
  sha:       z.string(),
  message:   z.string(),
  author:    z.string(),
  timestamp: z.string(),
});

export const DriftSignalSummarySchema = z.object({
  urn:            z.string(),
  severity:       z.enum(["breaking", "warning", "info"]),
  description:    z.string(),
  detectedFields: z.array(z.string()),
});

export const ApiContractSummarySchema = z.object({
  operationId: z.string(),
  method:      z.string(),
  path:        z.string(),
  summary:     z.string(),
  deprecated:  z.boolean(),
});

export const FileMemorySchema = z.object({
  filePath:           z.string(),
  purpose:            z.string(),
  exports:            z.array(z.string()),
  keySymbols:         z.array(z.string()),
  lastModifiedCommit: z.string(),
  stalenessScore:     z.number().min(0).max(1),
});

export const SymbolMemorySchema = z.object({
  urn:            z.string(),
  name:           z.string(),
  kind:           z.enum(["function", "class", "method", "constant", "interface", "type", "enum"]),
  signature:      z.string(),
  callers:        z.array(z.string()),
  callees:        z.array(z.string()),
  contextSummary: z.string(),
  confidence:     z.number().min(0).max(1),
  stalenessScore: z.number().min(0).max(1),
});

export const HotTierSchema = z.object({
  activeFiles:      z.array(FileMemorySchema),
  recentCommits:    z.array(RecentCommitSchema),
  openDriftSignals: z.array(DriftSignalSummarySchema),
  projectContext:   z.string(),
  apiContracts:     z.array(ApiContractSummarySchema),
});

export const WarmTierSchema = z.object({
  frequentSymbols: z.array(SymbolMemorySchema),
  dataAssumptions: z.array(z.object({
    subject:    z.string(),
    assumption: z.string(),
    source:     z.string(),
    confidence: z.number().min(0).max(1),
  })),
  knownIssues: z.array(z.object({
    id:           z.string(),
    title:        z.string(),
    severity:     z.enum(["critical", "high", "medium", "low"]),
    affectedUrns: z.array(z.string()),
    notes:        z.string(),
  })),
});

export const ColdTierSchema = z.object({
  archivedContextUrns: z.array(z.string()),
});

export const ScopeSnapshotSchema = z.object({
  version:           z.literal("1"),
  scope:             z.string(),
  generatedAtCommit: z.string(),
  generatedAt:       z.string(),
  hotTier:           HotTierSchema,
  warmTier:          WarmTierSchema,
  coldTier:          ColdTierSchema,
  staleSince:        z.string().nullable(),
});

export const StalenessReportSchema = z.object({
  staleNodes:         z.array(z.string()),
  refreshCandidates:  z.array(z.string()),
  estimatedTokenCost: z.number().int().nonnegative(),
});
