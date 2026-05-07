/**
 * packages/memory/src/staleness.ts
 *
 * Staleness scoring for memory nodes.
 *
 * A node becomes stale when it was extracted at an old commit and the extractor
 * had low confidence.  High-confidence nodes tolerate more drift; low-confidence
 * nodes are marked stale sooner.
 *
 * Score formula:  min(1.0, commitDistance / 10.0) * (1.0 - confidence)
 *
 * Thresholds (default):
 *   stale            ≥ 0.70  — must refresh before use
 *   refresh candidate ≥ 0.40 — should refresh soon
 */

import type { GraphClient } from "@company-brain/graph";
import type { ScopeSnapshot, StalenessReport } from "./types.js";

// ── Core scoring ──────────────────────────────────────────────────────────────

/**
 * Compute a staleness score for a memory node.
 *
 * @param lastCommit    - Commit SHA when the node was last extracted
 * @param currentCommit - HEAD commit SHA of the current branch
 * @param confidence    - Extractor confidence at time of extraction (0–1)
 * @returns             Score in [0, 1].  Higher = more stale.
 */
export function computeStalenessScore(
  lastCommit:     string,
  currentCommit:  string,
  confidence:     number,
): number {
  // When commits are equal the node is perfectly fresh regardless of confidence
  if (lastCommit === currentCommit) return 0;

  // We don't have a live Git log here, so we estimate distance from the SHA
  // suffix distance (lexicographic difference of the last 4 hex digits).
  // This is a heuristic; callers that have real commit-distance data should
  // pass commitDistance directly via computeStalenessScoreFromDistance().
  const distanceEstimate = _estimateCommitDistance(lastCommit, currentCommit);
  return computeStalenessScoreFromDistance(distanceEstimate, confidence);
}

/**
 * Compute staleness from a known integer commit distance.
 *
 * @param commitDistance - Number of commits between lastCommit and HEAD
 * @param confidence     - Extractor confidence (0–1)
 * @returns              Score in [0, 1]
 */
export function computeStalenessScoreFromDistance(
  commitDistance: number,
  confidence:     number,
): number {
  if (commitDistance <= 0) return 0;
  const distanceFactor = Math.min(1.0, commitDistance / 10.0);
  return distanceFactor * (1.0 - Math.max(0, Math.min(1, confidence)));
}

/**
 * Returns true when a score meets or exceeds the stale threshold.
 *
 * @param score     - Value from computeStalenessScore()
 * @param threshold - Override the default 0.7 stale threshold
 */
export function isStale(score: number, threshold = 0.7): boolean {
  return score >= threshold;
}

/**
 * Returns true when a score meets or exceeds the refresh-candidate threshold
 * but is not yet fully stale.
 */
export function isRefreshCandidate(
  score:              number,
  staleThreshold    = 0.7,
  candidateThreshold = 0.4,
): boolean {
  return score >= candidateThreshold && score < staleThreshold;
}

// ── Report builder ────────────────────────────────────────────────────────────

/**
 * Build a staleness report for a ScopeSnapshot.
 *
 * Walks hot + warm tier nodes, collects URNs that are stale or approaching
 * the threshold.  Queries Neo4j for any drift signals not already in the
 * snapshot to include their URNs in the stale list.
 *
 * @param snapshot - Current ScopeSnapshot
 * @param graph    - Connected GraphClient (read-only queries only)
 */
export async function buildStalenessReport(
  snapshot: ScopeSnapshot,
  graph:    GraphClient,
): Promise<StalenessReport> {
  const staleNodes:        string[] = [];
  const refreshCandidates: string[] = [];

  // -- Hot tier: file memories --------------------------------------------------
  for (const file of snapshot.hotTier.activeFiles) {
    if (isStale(file.stalenessScore)) {
      staleNodes.push(`file:${file.filePath}`);
    } else if (isRefreshCandidate(file.stalenessScore)) {
      refreshCandidates.push(`file:${file.filePath}`);
    }
  }

  // -- Warm tier: symbol memories -----------------------------------------------
  for (const sym of snapshot.warmTier.frequentSymbols) {
    if (isStale(sym.stalenessScore)) {
      staleNodes.push(sym.urn);
    } else if (isRefreshCandidate(sym.stalenessScore)) {
      refreshCandidates.push(sym.urn);
    }
  }

  // -- Graph: drift signals that are open but not in snapshot -------------------
  try {
    const driftRows = await graph.query<{ urn: string }>(
      `MATCH (d:CBNode { type: "DriftSignal" })
       WHERE d.scope = $scope
         AND (d.status IS NULL OR d.status <> "resolved")
       RETURN d.id AS urn`,
      { scope: snapshot.scope },
    );

    const knownDriftUrns = new Set(
      snapshot.hotTier.openDriftSignals.map(s => s.urn),
    );
    for (const row of driftRows) {
      if (!knownDriftUrns.has(row.urn)) {
        // Drift signal exists in graph but not in snapshot → snapshot is stale
        staleNodes.push(row.urn);
      }
    }
  } catch {
    // Graph may be unreachable in unit tests — degrade gracefully
  }

  // -- Estimated token cost (300 tokens per node to re-extract + summarise) -----
  const TOKENS_PER_NODE = 300;
  const estimatedTokenCost = staleNodes.length * TOKENS_PER_NODE;

  return { staleNodes, refreshCandidates, estimatedTokenCost };
}

// ── Internal helpers ──────────────────────────────────────────────────────────

/**
 * Estimate commit distance as the absolute difference of the integer value
 * of the last 4 hex characters of each SHA.  This is a rough heuristic for
 * cases where we don't have a live git log.
 */
function _estimateCommitDistance(shaA: string, shaB: string): number {
  if (!shaA || !shaB || shaA === shaB) return 0;
  const tailA = parseInt(shaA.slice(-4), 16);
  const tailB = parseInt(shaB.slice(-4), 16);
  if (isNaN(tailA) || isNaN(tailB)) return 5; // default mid-range estimate
  return Math.min(20, Math.abs(tailA - tailB) % 20 + 1);
}
