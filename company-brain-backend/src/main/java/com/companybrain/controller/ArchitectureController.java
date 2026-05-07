package com.companybrain.controller;

import com.companybrain.dto.*;
import com.companybrain.security.WorkspaceContext;
import com.companybrain.service.ArchitectureService;
import lombok.RequiredArgsConstructor;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.util.List;
import java.util.UUID;

/**
 * Architecture API — ADR-006 Week 4.
 *
 * Serves topology metrics (hubs, bridges) and execution flows to the
 * Architecture tab in the React frontend and to the MCP architecture tools.
 *
 * All data is computed by the Python structural layer and stored in:
 *   graph_metrics  (hub_degree, bridge_betweenness)
 *   flows / flow_memberships
 *
 * Endpoints are workspace-scoped via JWT → RLS (see ADR-003).
 */
@RestController
@RequestMapping("/v1/workspaces/{workspaceId}")
@RequiredArgsConstructor
public class ArchitectureController {

    private final ArchitectureService architectureService;
    private final WorkspaceContext    workspaceContext;

    // ----------------------------------------------------------------
    // Hub nodes — highest degree (most connections)
    // ----------------------------------------------------------------

    /**
     * GET /v1/workspaces/{workspaceId}/graph/hubs?topN=20
     *
     * Returns the top-N nodes by normalised in+out degree.
     * These are the highest-blast-radius nodes in the graph.
     * Populated nightly by TopologyAnalyser.
     */
    @GetMapping("/graph/hubs")
    public ResponseEntity<List<HubNodeDto>> getHubs(
            @PathVariable UUID workspaceId,
            @RequestParam(defaultValue = "20") int topN) {

        return ResponseEntity.ok(architectureService.getHubs(workspaceContext.getWorkspaceId(), topN));
    }

    // ----------------------------------------------------------------
    // Bridge nodes — highest betweenness (structural chokepoints)
    // ----------------------------------------------------------------

    /**
     * GET /v1/workspaces/{workspaceId}/graph/bridges?topN=10
     *
     * Returns the top-N nodes by betweenness centrality.
     * These are chokepoints — removing them would disconnect large parts of the graph.
     * Populated nightly by TopologyAnalyser.
     */
    @GetMapping("/graph/bridges")
    public ResponseEntity<List<HubNodeDto>> getBridges(
            @PathVariable UUID workspaceId,
            @RequestParam(defaultValue = "10") int topN) {

        return ResponseEntity.ok(architectureService.getBridges(workspaceContext.getWorkspaceId(), topN));
    }

    // ----------------------------------------------------------------
    // Execution flows
    // ----------------------------------------------------------------

    /**
     * GET /v1/workspaces/{workspaceId}/flows?minCriticality=0
     *
     * Lists all execution flows sorted by criticality (highest first).
     * Optional minCriticality filter (0.0–1.0).
     * Populated by FlowDetector during each pipeline run.
     */
    @GetMapping("/flows")
    public ResponseEntity<List<FlowSummaryDto>> getFlows(
            @PathVariable UUID workspaceId,
            @RequestParam(defaultValue = "0") double minCriticality) {

        return ResponseEntity.ok(architectureService.getFlows(workspaceContext.getWorkspaceId(), minCriticality));
    }

    /**
     * GET /v1/workspaces/{workspaceId}/flows/{flowId}
     *
     * Returns the full node sequence for a specific flow.
     * Members are ordered by BFS traversal position (0 = entry point).
     */
    @GetMapping("/flows/{flowId}")
    public ResponseEntity<FlowDetailDto> getFlow(
            @PathVariable UUID workspaceId,
            @PathVariable UUID flowId) {

        return architectureService.getFlow(workspaceContext.getWorkspaceId(), flowId)
                .map(ResponseEntity::ok)
                .orElse(ResponseEntity.notFound().build());
    }
}
