package com.companybrain.controller;

import com.companybrain.dto.*;
import com.companybrain.security.WorkspaceContext;
import com.companybrain.service.BlastRadiusService;
import com.companybrain.service.ContextAssemblerService;
import com.companybrain.service.GraphService;
import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.util.UUID;

/**
 * REST API for the dependency graph.
 *
 * All endpoints are scoped to the authenticated workspace.
 * The workspace_id is extracted from the JWT claim and set as
 * a Postgres session variable (app.workspace_id) via WorkspaceContext.
 * This activates Row Level Security on all tables — see ADR-003.
 */
@RestController
@RequestMapping("/v1")
@RequiredArgsConstructor
public class GraphController {

    private final BlastRadiusService      blastRadiusService;
    private final GraphService            graphService;
    private final ContextAssemblerService contextAssemblerService;
    private final WorkspaceContext        workspaceContext;

    @Value("${app.internal-api-key:dev-internal-key}")
    private String internalApiKey;

    // ----------------------------------------------------------------
    // Blast radius — the core product query
    // ----------------------------------------------------------------

    /**
     * GET /v1/nodes/{nodeId}/blast-radius
     *
     * Returns all nodes affected if the given node changes.
     * Traverses up to 5 hops via dependency edges.
     * Joins with OWNS edges to return the responsible team per node.
     *
     * Cached in Redis for 5 minutes. Cache is invalidated when
     * the graph builder upserts an edge touching this node.
     */
    @GetMapping("/nodes/{nodeId}/blast-radius")
    public ResponseEntity<BlastRadiusResponse> getBlastRadius(@PathVariable UUID nodeId) {
        return ResponseEntity.ok(
                blastRadiusService.compute(workspaceContext.getWorkspaceId(), nodeId)
        );
    }

    // ----------------------------------------------------------------
    // Node context — git history, PR descriptions, annotations
    // ----------------------------------------------------------------

    /**
     * GET /v1/nodes/{nodeId}/context
     *
     * Returns all context entries for a node:
     * git commits, PR descriptions, ticket summaries, user annotations,
     * LLM-synthesised business context, invariants, and risk flags.
     *
     * Entries are returned newest-first.
     * Body text is decrypted at the service layer for enterprise workspaces.
     */
    @GetMapping("/nodes/{nodeId}/context")
    public ResponseEntity<NodeContextListResponse> getNodeContext(
            @PathVariable UUID nodeId,
            @RequestParam(defaultValue = "0") int page,
            @RequestParam(defaultValue = "20") int size) {
        return ResponseEntity.ok(
                graphService.getNodeContext(workspaceContext.getWorkspaceId(), nodeId, page, size)
        );
    }

    // ----------------------------------------------------------------
    // Dependency navigation
    // ----------------------------------------------------------------

    /**
     * GET /v1/nodes/{nodeId}/dependents
     *
     * Returns all nodes that depend on the given node (inbound edges).
     * "Who calls this service?", "What uses this API field?"
     */
    @GetMapping("/nodes/{nodeId}/dependents")
    public ResponseEntity<NodeListResponse> getDependents(
            @PathVariable UUID nodeId,
            @RequestParam(required = false) String edgeType) {
        return ResponseEntity.ok(
                graphService.getDependents(workspaceContext.getWorkspaceId(), nodeId, edgeType)
        );
    }

    /**
     * GET /v1/nodes/{nodeId}/dependencies
     *
     * Returns all nodes the given node depends on (outbound edges).
     * "What does this service call?", "What tables does this endpoint read?"
     */
    @GetMapping("/nodes/{nodeId}/dependencies")
    public ResponseEntity<NodeListResponse> getDependencies(
            @PathVariable UUID nodeId,
            @RequestParam(required = false) String edgeType) {
        return ResponseEntity.ok(
                graphService.getDependencies(workspaceContext.getWorkspaceId(), nodeId, edgeType)
        );
    }

    // ----------------------------------------------------------------
    // Service graph — neighbourhood view for the dashboard
    // ----------------------------------------------------------------

    /**
     * GET /v1/services/{nodeId}/graph
     *
     * Returns a 2-hop neighbourhood graph centred on the given service node.
     * Includes nodes and edges in a format suitable for graph visualisation.
     */
    @GetMapping("/services/{nodeId}/graph")
    public ResponseEntity<GraphResponse> getServiceGraph(@PathVariable UUID nodeId) {
        return ResponseEntity.ok(
                graphService.getServiceGraph(workspaceContext.getWorkspaceId(), nodeId)
        );
    }

    // ----------------------------------------------------------------
    // Search
    // ----------------------------------------------------------------

    /**
     * GET /v1/search?q=...
     *
     * Fuzzy search across node names within the workspace.
     * Used by the VS Code extension hover and the dashboard search bar.
     */
    @GetMapping("/search")
    public ResponseEntity<NodeListResponse> search(
            @RequestParam String q,
            @RequestParam(required = false) String nodeType,
            @RequestParam(defaultValue = "20") int limit) {
        return ResponseEntity.ok(
                graphService.search(workspaceContext.getWorkspaceId(), q, nodeType, limit)
        );
    }

    // ----------------------------------------------------------------
    // User annotations — submitted from VS Code extension
    // ----------------------------------------------------------------

    /**
     * POST /v1/nodes/{nodeId}/annotations
     *
     * Submit a user annotation for a node (anchored to a specific commit).
     * Annotation types: business_context | invariant | risk_flag | deprecation_note
     */
    @PostMapping("/nodes/{nodeId}/annotations")
    public ResponseEntity<AnnotationResponse> addAnnotation(
            @PathVariable UUID nodeId,
            @Valid @RequestBody AnnotationRequest request) {
        return ResponseEntity.ok(
                graphService.addAnnotation(workspaceContext.getWorkspaceId(), nodeId, request)
        );
    }

    // ── Internal: context assembly for AI Ask ─────────────────────────────────

    /**
     * POST /v1/internal/assemble-context
     *
     * Called by the Python AI service BEFORE the LLM Ask call.
     *
     * The Python service supplies a focal node (ID or externalId), the user's
     * question, and traversal parameters.  Java assembles tiered context from the
     * graph (T2 full blocks + T1 summaries + T0 one-liners) within the token budget
     * and returns it as a ready-to-use Markdown string.
     *
     * The Python service inserts contextText verbatim as the "KNOWLEDGE BASE" section
     * in its LLM system prompt before calling the model.
     *
     * See ContextAssemblerService and ADR-004: Tiered Memory & Context Assembly.
     */
    @PostMapping("/internal/assemble-context")
    public ResponseEntity<AssembledContext> assembleContext(
            @RequestHeader("X-Internal-Key") String key,
            @RequestBody ContextAssemblyRequest request) {

        if (!internalApiKey.equals(key)) {
            return ResponseEntity.status(HttpStatus.UNAUTHORIZED).build();
        }

        AssembledContext assembled = contextAssemblerService.assemble(request);
        return ResponseEntity.ok(assembled);
    }
}
