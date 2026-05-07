package com.companybrain.dto;

import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.util.List;
import java.util.UUID;

/**
 * Tiered context assembled by ContextAssemblerService for a single Ask query.
 *
 * The Python AI service calls POST /v1/internal/assemble-context and receives this
 * DTO.  It then constructs an LLM prompt using contextText as the knowledge block
 * and sends the user's question alongside it.
 *
 * Tier definitions per ADR-004:
 *   T2 — full FunctionContext (~600 tokens):  focal node + closest neighbours
 *   T1 — summary block     (~100 tokens):  mid-range neighbours
 *   T0 — one-liner         (~15  tokens):  far neighbours (names only)
 *
 * See ADR-004: Tiered Memory & Context Assembly.
 */
@Data
@Builder
@NoArgsConstructor
@AllArgsConstructor
public class AssembledContext {

    // ── Rendered context ready for the LLM prompt ────────────────────────────

    /**
     * Full rendered context text, structured as Markdown sections.
     * Fits within the configured token budget (default 4096 tokens).
     * The Python service can insert this verbatim as the "KNOWLEDGE BASE" block
     * in its system prompt.
     */
    private String contextText;

    /**
     * Estimated token count for contextText.
     * Approximated as ceil(chars / 4) — good enough for budget decisions.
     */
    private int estimatedTokens;

    // ── Traversal metadata (for logging + frontend display) ──────────────────

    /** The focal node the query is centred on. */
    private UUID focalNodeId;
    private String focalNodeName;
    private String focalNodeType;

    /** Total nodes visited during BFS (before budget trimming). */
    private int nodesTraversed;

    /** Nodes whose T2/T1/T0 content was actually included in contextText. */
    private int nodesIncluded;

    /** Max hop distance traversed from focal node. */
    private int maxHopsUsed;

    /** Per-tier breakdown for observability. */
    private TierSummary tierSummary;

    @Data
    @Builder
    @NoArgsConstructor
    @AllArgsConstructor
    public static class TierSummary {
        private int t2Count;   // full FunctionContext blocks
        private int t1Count;   // summary blocks
        private int t0Count;   // one-liner mentions
        private int t2Tokens;
        private int t1Tokens;
        private int t0Tokens;
    }
}
