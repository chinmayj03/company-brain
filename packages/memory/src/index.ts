/**
 * packages/memory/src/index.ts
 *
 * Public API for @company-brain/memory — the shared memory layer.
 *
 * Usage:
 *   import { SnapshotBuilder, computeStalenessScore, isStale } from "@company-brain/memory";
 */

// Types
export type {
  MemoryTier,
  ScopeSnapshot,
  HotTier,
  WarmTier,
  ColdTier,
  FileMemory,
  SymbolMemory,
  SymbolKind,
  RecentCommit,
  DriftSignalSummary,
  ApiContractSummary,
  DataAssumption,
  KnownIssue,
  StalenessReport,
} from "./types.js";

// Zod schemas (for external validation / tRPC output schemas)
export {
  ScopeSnapshotSchema,
  StalenessReportSchema,
  FileMemorySchema,
  SymbolMemorySchema,
  HotTierSchema,
  WarmTierSchema,
  ColdTierSchema,
  RecentCommitSchema,
  DriftSignalSummarySchema,
  ApiContractSummarySchema,
} from "./types.js";

// Snapshot builder
export { SnapshotBuilder } from "./snapshot-builder.js";

// Staleness utilities
export {
  computeStalenessScore,
  computeStalenessScoreFromDistance,
  isStale,
  isRefreshCandidate,
  buildStalenessReport,
} from "./staleness.js";
