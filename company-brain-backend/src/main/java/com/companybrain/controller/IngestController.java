package com.companybrain.controller;

import com.companybrain.dto.IngestRequest;
import com.companybrain.dto.IngestResponse;
import com.companybrain.service.IngestService;
import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

/**
 * IngestController receives metadata event batches from the metadata agent.
 *
 * Flow (see ADR-002):
 * 1. Validate HMAC-SHA256 signature (agent sends X-Agent-Signature header)
 * 2. Check rate limit for this workspace
 * 3. Enqueue to SQS asynchronously
 * 4. Return 202 Accepted immediately — the agent does not wait for graph processing
 *
 * The graph builder worker (in company-brain-ai) consumes from SQS and
 * performs the actual upsert into the graph.
 *
 * Note: this endpoint does NOT require JWT auth — it uses HMAC signature auth
 * so agents can authenticate without a user session.
 */
@RestController
@RequestMapping("/v1/ingest")
@RequiredArgsConstructor
public class IngestController {

    private final IngestService ingestService;

    /**
     * POST /v1/ingest
     *
     * Agent payload: signed batch of graph events (node upserts, edge observations, context).
     * Max batch size: 1MB. Max 100 requests/minute per workspace.
     *
     * Returns 202 Accepted immediately. The batch is queued for async processing.
     * Returns 400 if signature is invalid or payload is malformed.
     * Returns 429 if rate limit is exceeded.
     */
    @PostMapping
    public ResponseEntity<IngestResponse> ingest(
            @RequestHeader("X-Workspace-Id") String workspaceId,
            @RequestHeader("X-Agent-Signature") String signature,
            @RequestHeader("X-Agent-Version") String agentVersion,
            @Valid @RequestBody IngestRequest request) {

        ingestService.acceptBatch(workspaceId, signature, agentVersion, request);

        return ResponseEntity
                .status(HttpStatus.ACCEPTED)
                .body(IngestResponse.builder()
                        .status("queued")
                        .eventCount(request.getEvents().size())
                        .build());
    }
}
