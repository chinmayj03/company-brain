package com.companybrain.service;

import com.companybrain.dto.*;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.math.BigDecimal;
import java.util.*;

/**
 * Architecture service — reads from graph_metrics, flows, and flow_memberships.
 *
 * These tables are populated by the Python structural layer:
 *   - graph_metrics  → companybrain/structural/topology.py  (TopologyAnalyser nightly job)
 *   - flows          → companybrain/structural/flows.py      (FlowDetector, run per pipeline)
 *
 * All queries respect Postgres RLS via the workspace_id session variable set by
 * JwtAuthFilter → WorkspaceContext.setWorkspaceId().
 *
 * ADR-006 Week 4.
 */
@Service
@RequiredArgsConstructor
@Slf4j
public class ArchitectureService {

    private final JdbcTemplate jdbcTemplate;

    // ----------------------------------------------------------------
    // Hubs — top-N nodes by normalised degree
    // ----------------------------------------------------------------

    @Transactional(readOnly = true)
    public List<HubNodeDto> getHubs(UUID workspaceId, int topN) {
        String sql = """
            SELECT gm.node_id, n.name, n.node_type, n.qualified_name,
                   gm.score, gm.rank, n.risk_score
            FROM graph_metrics gm
            JOIN nodes n ON n.id = gm.node_id
            WHERE gm.workspace_id = ?
              AND gm.metric_kind   = 'hub_degree'
            ORDER BY gm.rank ASC
            LIMIT ?
            """;
        return jdbcTemplate.query(sql, (rs, rowNum) -> HubNodeDto.builder()
                .nodeId(UUID.fromString(rs.getString("node_id")))
                .name(rs.getString("name"))
                .nodeType(rs.getString("node_type"))
                .qualifiedName(rs.getString("qualified_name"))
                .score(rs.getBigDecimal("score"))
                .rank(rs.getInt("rank"))
                .riskScore(rs.getBigDecimal("risk_score"))
                .build(), workspaceId, topN);
    }

    // ----------------------------------------------------------------
    // Bridges — top-N nodes by betweenness centrality
    // ----------------------------------------------------------------

    @Transactional(readOnly = true)
    public List<HubNodeDto> getBridges(UUID workspaceId, int topN) {
        String sql = """
            SELECT gm.node_id, n.name, n.node_type, n.qualified_name,
                   gm.score, gm.rank, n.risk_score
            FROM graph_metrics gm
            JOIN nodes n ON n.id = gm.node_id
            WHERE gm.workspace_id = ?
              AND gm.metric_kind   = 'bridge_betweenness'
            ORDER BY gm.rank ASC
            LIMIT ?
            """;
        return jdbcTemplate.query(sql, (rs, rowNum) -> HubNodeDto.builder()
                .nodeId(UUID.fromString(rs.getString("node_id")))
                .name(rs.getString("name"))
                .nodeType(rs.getString("node_type"))
                .qualifiedName(rs.getString("qualified_name"))
                .score(rs.getBigDecimal("score"))
                .rank(rs.getInt("rank"))
                .riskScore(rs.getBigDecimal("risk_score"))
                .build(), workspaceId, topN);
    }

    // ----------------------------------------------------------------
    // Flows — list with optional criticality filter
    // ----------------------------------------------------------------

    @Transactional(readOnly = true)
    public List<FlowSummaryDto> getFlows(UUID workspaceId, double minCriticality) {
        String sql = """
            SELECT f.id, f.name, f.entry_node_id, n.name AS entry_node_name,
                   f.depth, f.node_count, f.file_count, f.criticality
            FROM flows f
            JOIN nodes n ON n.id = f.entry_node_id
            WHERE f.workspace_id = ?
              AND f.criticality  >= ?
            ORDER BY f.criticality DESC
            """;
        return jdbcTemplate.query(sql, (rs, rowNum) -> FlowSummaryDto.builder()
                .id(UUID.fromString(rs.getString("id")))
                .name(rs.getString("name"))
                .entryNodeId(UUID.fromString(rs.getString("entry_node_id")))
                .entryNodeName(rs.getString("entry_node_name"))
                .depth(rs.getInt("depth"))
                .nodeCount(rs.getInt("node_count"))
                .fileCount(rs.getInt("file_count"))
                .criticality(rs.getBigDecimal("criticality"))
                .build(), workspaceId, minCriticality);
    }

    // ----------------------------------------------------------------
    // Flow detail — with ordered member sequence
    // ----------------------------------------------------------------

    @Transactional(readOnly = true)
    public Optional<FlowDetailDto> getFlow(UUID workspaceId, UUID flowId) {
        String flowSql = """
            SELECT f.id, f.name, f.entry_node_id, n.name AS entry_node_name,
                   f.depth, f.node_count, f.file_count, f.criticality
            FROM flows f
            JOIN nodes n ON n.id = f.entry_node_id
            WHERE f.workspace_id = ?
              AND f.id           = ?
            """;

        List<FlowDetailDto> flows = jdbcTemplate.query(flowSql, (rs, rowNum) -> FlowDetailDto.builder()
                .id(UUID.fromString(rs.getString("id")))
                .name(rs.getString("name"))
                .entryNodeId(UUID.fromString(rs.getString("entry_node_id")))
                .entryNodeName(rs.getString("entry_node_name"))
                .depth(rs.getInt("depth"))
                .nodeCount(rs.getInt("node_count"))
                .fileCount(rs.getInt("file_count"))
                .criticality(rs.getBigDecimal("criticality"))
                .build(), workspaceId, flowId);

        if (flows.isEmpty()) return Optional.empty();

        FlowDetailDto flow = flows.get(0);

        // Fetch ordered members
        String memberSql = """
            SELECT fm.node_id, n.name, n.node_type, n.qualified_name,
                   fm.position, n.risk_score
            FROM flow_memberships fm
            JOIN nodes n ON n.id = fm.node_id
            WHERE fm.flow_id = ?
            ORDER BY fm.position ASC
            """;

        List<FlowMemberDto> members = jdbcTemplate.query(memberSql, (rs, rowNum) -> FlowMemberDto.builder()
                .nodeId(UUID.fromString(rs.getString("node_id")))
                .name(rs.getString("name"))
                .nodeType(rs.getString("node_type"))
                .qualifiedName(rs.getString("qualified_name"))
                .position(rs.getInt("position"))
                .riskScore(rs.getBigDecimal("risk_score"))
                .build(), flowId);

        flow.setMembers(members);
        return Optional.of(flow);
    }
}
