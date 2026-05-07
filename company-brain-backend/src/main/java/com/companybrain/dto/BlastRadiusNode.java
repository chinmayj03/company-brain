package com.companybrain.dto;

import com.fasterxml.jackson.annotation.JsonInclude;
import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.util.List;
import java.util.Map;
import java.util.UUID;

/**
 * DTO for a single node returned in a blast-radius response.
 *
 * ADR-006 §13: Added riskScore, riskFactors, flowMembership fields.
 * ADR-006 Week 2: Added direction field ("forward" | "reverse" | "origin").
 * These are populated by the structural layer (companybrain/structural/risk.py)
 * and stored in nodes.risk_score / nodes.risk_factors JSONB.
 *
 * Nodes that have not yet been through the structural parser will have null
 * riskScore and riskFactors — the frontend handles this gracefully.
 */
@Data
@Builder
@NoArgsConstructor
@AllArgsConstructor
@JsonInclude(JsonInclude.Include.NON_NULL)
public class BlastRadiusNode {

    // ── Existing fields (unchanged) ───────────────────────────────────────
    private UUID nodeId;
    private String nodeName;
    private String nodeType;
    private String viaEdgeType;
    private Double confidence;
    private Integer depth;
    private String owningTeam;

    /**
     * Traversal direction this node was reached by.
     * ADR-006 Week 2: populated by the bidirectional CTE.
     *   "forward"  — downstream dependent (this node uses the origin)
     *   "reverse"  — upstream caller (the origin uses this node)
     *   "origin"   — the seed node itself (depth == 0; not normally in results)
     * Null when using Direction.FORWARD or Direction.REVERSE (single-branch queries).
     */
    private String direction;

    // ── ADR-006 §13: Structural layer additions ───────────────────────────

    /**
     * Multi-factor risk score: 0.0 (low) – 1.0 (high).
     * Computed by companybrain/structural/risk.py; null until first structural scan.
     * Algorithm ported from tirth8205/code-review-graph (MIT License),
     * original: code_review_graph/changes.py::compute_risk_score.
     */
    private Double riskScore;

    /**
     * Per-factor breakdown of riskScore, for frontend explainer display.
     * Keys: "flow", "community", "tests", "security", "callers"
     * Values: each factor's individual contribution (0.0–1.0).
     * Null until structural scan has run for this node.
     */
    private Map<String, Double> riskFactors;

    /**
     * IDs of execution flows (flows table) this node participates in.
     * Empty until flow detection runs (week 4).
     * Included in the response so the frontend can link to flow details.
     */
    private List<UUID> flowMembership;
}
