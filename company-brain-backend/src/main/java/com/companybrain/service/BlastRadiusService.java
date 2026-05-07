package com.companybrain.service;

import com.companybrain.dto.BlastRadiusNode;
import com.companybrain.dto.BlastRadiusResponse;
import com.companybrain.exception.NodeNotFoundException;
import com.companybrain.repository.NodeRepository;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.cache.annotation.Cacheable;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;

import java.util.Collections;
import java.util.List;
import java.util.Map;
import java.util.UUID;

/**
 * BlastRadiusService executes the core graph traversal query:
 * given a starting node, find all nodes reachable within N hops.
 *
 * Uses PostgreSQL recursive CTEs (see SYSTEM_DESIGN.md Section 5).
 * Results are cached in Redis with a 5-minute TTL.
 *
 * ADR-001: We use Postgres recursive CTEs, not a graph database, until
 * p95 traversal time exceeds 200ms under realistic load.
 *
 * ADR-006 §13: Result rows now include risk_score and risk_factors from
 * the structural layer, populated by companybrain/structural/risk.py.
 * Nodes without a structural scan yet return null for these fields.
 */
@Service
@RequiredArgsConstructor
@Slf4j
public class BlastRadiusService {

    private final JdbcTemplate jdbcTemplate;
    private final NodeRepository nodeRepository;
    private final ObjectMapper objectMapper;

    @Value("${app.blast-radius.max-depth:5}")
    private int maxDepth;

    @Value("${app.blast-radius.min-confidence:0.5}")
    private double minConfidence;

    @Value("${app.blast-radius.stale-edge-days:30}")
    private int staleEdgeDays;

    /**
     * ADR-006 Week 2: enable bidirectional CTE (upstream + downstream).
     * Feature-flag: set structural.blast-radius.bidirectional=false to revert
     * to forward-only traversal for debugging or performance comparison.
     */
    @Value("${structural.blast-radius.bidirectional:true}")
    private boolean bidirectional;

    /**
     * Traversal direction.  Passed in from the REST layer; defaults to BOTH.
     *   FORWARD  — downstream dependents only  (original behaviour)
     *   REVERSE  — upstream callers only
     *   BOTH     — full bidirectional blast radius (default)
     */
    public enum Direction { FORWARD, REVERSE, BOTH }

    /**
     * Compute the blast radius for a given node.
     * Returns all nodes reachable via dependency edges, with their depth,
     * owning team, confidence score, and structural risk data.
     *
     * Result is cached per (workspaceId, nodeId, direction) with 5-minute TTL.
     * ADR-006 Week 2: direction parameter added; default BOTH.
     */
    @Cacheable(value = "blast-radius", key = "#workspaceId + ':' + #nodeId + ':' + #direction")
    public BlastRadiusResponse compute(UUID workspaceId, UUID nodeId, Direction direction) {
        log.debug("Computing blast radius for node {} in workspace {} direction={}",
                nodeId, workspaceId, direction);

        if (!nodeRepository.existsByIdAndWorkspaceId(nodeId, workspaceId)) {
            throw new NodeNotFoundException(nodeId);
        }

        long start = System.currentTimeMillis();

        // Use bidirectional CTE only when feature flag is on and direction allows it.
        List<BlastRadiusNode> affected;
        if (bidirectional && direction == Direction.BOTH) {
            affected = runBidirectionalTraversal(workspaceId, nodeId);
        } else {
            affected = runTraversal(workspaceId, nodeId, direction);
        }

        long durationMs = System.currentTimeMillis() - start;

        if (durationMs > 200) {
            log.warn("Blast radius traversal slow: nodeId={} direction={} took {}ms " +
                     "(threshold=200ms). Consider pre-computing for high-traffic nodes.",
                     nodeId, direction, durationMs);
        }

        return BlastRadiusResponse.builder()
                .originNodeId(nodeId)
                .workspaceId(workspaceId)
                .affectedNodes(affected)
                .traversalDepth(maxDepth)
                .queryDurationMs(durationMs)
                .build();
    }

    /** Convenience overload: defaults to Direction.BOTH. */
    @Cacheable(value = "blast-radius", key = "#workspaceId + ':' + #nodeId + ':BOTH'")
    public BlastRadiusResponse compute(UUID workspaceId, UUID nodeId) {
        return compute(workspaceId, nodeId, Direction.BOTH);
    }

    // ── Forward-only traversal (original behaviour, kept for Direction.FORWARD) ─

    /**
     * Forward-only recursive CTE: follows outbound edges (source → target).
     * This is the original Week 1 implementation, preserved for Direction.FORWARD
     * and for fallback when the feature flag is off.
     *
     * Also handles Direction.REVERSE by reversing the JOIN direction.
     */
    private List<BlastRadiusNode> runTraversal(UUID workspaceId, UUID nodeId, Direction direction) {
        // For REVERSE, swap source_id / target_id in the recursive join.
        String edgeJoin = (direction == Direction.REVERSE)
                ? "JOIN edges e ON e.target_id = br.id AND e.source_id = target.id"
                : "JOIN edges e ON e.source_id = br.id AND e.target_id = target.id";

        // language=SQL
        String sql = """
            WITH RECURSIVE blast_radius AS (
                SELECT
                    n.id,
                    n.name,
                    n.node_type,
                    NULL::TEXT AS via_edge_type,
                    1.0::FLOAT AS confidence,
                    0 AS depth,
                    'origin'::TEXT AS direction,
                    ARRAY[n.id] AS visited_path
                FROM nodes n
                WHERE n.id = ?::UUID
                  AND n.workspace_id = ?::UUID

                UNION ALL

                SELECT
                    target.id,
                    target.name,
                    target.node_type,
                    e.edge_type,
                    e.confidence,
                    br.depth + 1,
                    ?::TEXT,
                    br.visited_path || target.id
                FROM blast_radius br
                JOIN edges e ON e.workspace_id = ?::UUID
                    AND e.is_pruned = false
                    AND e.confidence >= ?
                    AND e.last_seen > NOW() - INTERVAL '1 day' * ?
                    AND (
                        (? = 'FORWARD' AND e.source_id = br.id)
                     OR (? = 'REVERSE' AND e.target_id = br.id)
                    )
                JOIN nodes target ON target.id = CASE
                    WHEN ? = 'FORWARD' THEN e.target_id
                    ELSE e.source_id END
                WHERE br.depth < ?
                  AND NOT (target.id = ANY(br.visited_path))
            )
            SELECT DISTINCT ON (br.id)
                br.id              AS node_id,
                br.name            AS node_name,
                br.node_type,
                br.via_edge_type,
                br.confidence,
                br.depth,
                br.direction,
                owner.name         AS owning_team,
                n.risk_score,
                n.risk_factors::TEXT AS risk_factors_json
            FROM blast_radius br
            JOIN nodes n ON n.id = br.id
            LEFT JOIN edges own_edge ON own_edge.target_id = br.id
                AND own_edge.edge_type = 'OWNS'
                AND own_edge.workspace_id = ?::UUID
                AND own_edge.is_pruned = false
            LEFT JOIN nodes owner ON owner.id = own_edge.source_id
                AND owner.node_type = 'Team'
            WHERE br.depth > 0
            ORDER BY br.id, br.depth ASC
            """;

        String dir = direction.name();
        List<BlastRadiusNode> nodes = jdbcTemplate.query(
                sql,
                rowMapper(),
                nodeId, workspaceId,
                dir,
                workspaceId, minConfidence, staleEdgeDays,
                dir, dir, dir,
                maxDepth,
                workspaceId
        );

        sortByRisk(nodes);
        return nodes;
    }

    // ── Bidirectional traversal (ADR-006 Week 2) ──────────────────────────────

    /**
     * Bidirectional recursive CTE: follows BOTH outbound (downstream dependents)
     * and inbound (upstream callers) edges in a single query.
     *
     * The CTE uses two recursive branches unified by UNION ALL:
     *   Branch A (FORWARD)  — follows e.source_id = br.id  (who depends on me?)
     *   Branch B (REVERSE)  — follows e.target_id = br.id  (who do I depend on?)
     *
     * Each result row carries a 'direction' column ('forward' | 'reverse') so
     * the frontend can distinguish downstream impact from upstream context.
     *
     * ADR-006 §12: This is the Week 2 extension of BlastRadiusService.
     */
    private List<BlastRadiusNode> runBidirectionalTraversal(UUID workspaceId, UUID nodeId) {
        // language=SQL
        String sql = """
            WITH RECURSIVE blast_radius AS (
                -- Base: the origin node
                SELECT
                    n.id,
                    n.name,
                    n.node_type,
                    NULL::TEXT AS via_edge_type,
                    1.0::FLOAT AS confidence,
                    0 AS depth,
                    'origin'::TEXT AS direction,
                    ARRAY[n.id] AS visited_path
                FROM nodes n
                WHERE n.id = ?::UUID
                  AND n.workspace_id = ?::UUID

                UNION ALL

                -- Branch A: FORWARD — downstream dependents (source → target)
                SELECT
                    target.id,
                    target.name,
                    target.node_type,
                    e.edge_type,
                    e.confidence,
                    br.depth + 1,
                    'forward'::TEXT,
                    br.visited_path || target.id
                FROM blast_radius br
                JOIN edges e ON e.source_id = br.id
                    AND e.workspace_id = ?::UUID
                    AND e.is_pruned = false
                    AND e.confidence >= ?
                    AND e.last_seen > NOW() - INTERVAL '1 day' * ?
                JOIN nodes target ON target.id = e.target_id
                WHERE br.depth < ?
                  AND br.direction IN ('origin', 'forward')
                  AND NOT (target.id = ANY(br.visited_path))

                UNION ALL

                -- Branch B: REVERSE — upstream callers (target → source)
                SELECT
                    caller.id,
                    caller.name,
                    caller.node_type,
                    e.edge_type,
                    e.confidence,
                    br.depth + 1,
                    'reverse'::TEXT,
                    br.visited_path || caller.id
                FROM blast_radius br
                JOIN edges e ON e.target_id = br.id
                    AND e.workspace_id = ?::UUID
                    AND e.is_pruned = false
                    AND e.confidence >= ?
                    AND e.last_seen > NOW() - INTERVAL '1 day' * ?
                JOIN nodes caller ON caller.id = e.source_id
                WHERE br.depth < ?
                  AND br.direction IN ('origin', 'reverse')
                  AND NOT (caller.id = ANY(br.visited_path))
            )
            SELECT DISTINCT ON (br.id)
                br.id              AS node_id,
                br.name            AS node_name,
                br.node_type,
                br.via_edge_type,
                br.confidence,
                br.depth,
                br.direction,
                owner.name         AS owning_team,
                n.risk_score,
                n.risk_factors::TEXT AS risk_factors_json
            FROM blast_radius br
            JOIN nodes n ON n.id = br.id
            LEFT JOIN edges own_edge ON own_edge.target_id = br.id
                AND own_edge.edge_type = 'OWNS'
                AND own_edge.workspace_id = ?::UUID
                AND own_edge.is_pruned = false
            LEFT JOIN nodes owner ON owner.id = own_edge.source_id
                AND owner.node_type = 'Team'
            WHERE br.depth > 0
            ORDER BY br.id, br.depth ASC
            """;

        List<BlastRadiusNode> nodes = jdbcTemplate.query(
                sql,
                rowMapper(),
                // Base case
                nodeId, workspaceId,
                // Branch A (forward)
                workspaceId, minConfidence, staleEdgeDays, maxDepth,
                // Branch B (reverse)
                workspaceId, minConfidence, staleEdgeDays, maxDepth,
                // Outer SELECT ownership join
                workspaceId
        );

        sortByRisk(nodes);
        return nodes;
    }

    // ── Shared helpers ────────────────────────────────────────────────────────

    private org.springframework.jdbc.core.RowMapper<BlastRadiusNode> rowMapper() {
        return (rs, rowNum) -> {
            Map<String, Double> riskFactors = null;
            String rfJson = rs.getString("risk_factors_json");
            if (rfJson != null && !rfJson.isBlank()) {
                try {
                    riskFactors = objectMapper.readValue(
                            rfJson, new TypeReference<Map<String, Double>>() {});
                } catch (Exception e) {
                    log.debug("Failed to parse risk_factors JSON for node {}: {}",
                            rs.getString("node_id"), e.getMessage());
                }
            }

            Double riskScore = rs.getObject("risk_score") != null
                    ? rs.getDouble("risk_score") : null;

            return BlastRadiusNode.builder()
                    .nodeId(UUID.fromString(rs.getString("node_id")))
                    .nodeName(rs.getString("node_name"))
                    .nodeType(rs.getString("node_type"))
                    .viaEdgeType(rs.getString("via_edge_type"))
                    .confidence(rs.getDouble("confidence"))
                    .depth(rs.getInt("depth"))
                    .direction(rs.getString("direction"))    // 'forward' | 'reverse' | 'origin'
                    .owningTeam(rs.getString("owning_team"))
                    .riskScore(riskScore)
                    .riskFactors(riskFactors)
                    .flowMembership(Collections.emptyList()) // populated week 4
                    .build();
        };
    }

    private static void sortByRisk(List<BlastRadiusNode> nodes) {
        nodes.sort((a, b) -> {
            if (a.getRiskScore() == null && b.getRiskScore() == null) return 0;
            if (a.getRiskScore() == null) return 1;
            if (b.getRiskScore() == null) return -1;
            return Double.compare(b.getRiskScore(), a.getRiskScore());
        });
    }
}
