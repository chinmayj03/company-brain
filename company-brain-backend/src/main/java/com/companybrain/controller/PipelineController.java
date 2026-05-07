package com.companybrain.controller;

import com.companybrain.dto.ArtifactFreshnessRequest;
import com.companybrain.dto.ArtifactFreshnessResponse;
import com.companybrain.dto.PipelineJobResponse;
import com.companybrain.dto.PipelineResultRequest;
import com.companybrain.dto.PipelineStartRequest;
import com.companybrain.model.PipelineJob;
import com.companybrain.repository.PipelineJobRepository;
import com.companybrain.security.WorkspaceContext;
import com.companybrain.service.PipelineService;
import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.util.UUID;

/**
 * Pipeline REST API — all pipeline lifecycle endpoints in one place.
 *
 * Public (JWT-authenticated) endpoints used by the frontend:
 *   POST /v1/pipeline/start      — create a job and kick off AI processing
 *   GET  /v1/pipeline/jobs/{id}  — poll job status + live progress logs
 *
 * Internal (API-key-authenticated) endpoints used by the AI service:
 *   POST /v1/internal/pipeline-result   — AI service posts back extracted graph
 *   POST /v1/internal/pipeline-progress — AI service pushes live log entries mid-run
 *
 * Security note:
 *   /v1/internal/** is open to requests carrying X-Internal-Key matching
 *   app.internal-api-key from application.yml.
 *   In production this should be network-restricted to the AI service's subnet.
 */
@RestController
@RequiredArgsConstructor
@Slf4j
public class PipelineController {

    private final PipelineService              pipelineService;
    private final PipelineJobRepository        jobRepository;
    private final WorkspaceContext             workspaceContext;
    private final com.companybrain.service.ArtifactWriterService artifactWriterService;

    @Value("${app.internal-api-key:dev-internal-key}")
    private String internalApiKey;

    // ── Frontend-facing endpoints ─────────────────────────────────────────────

    /**
     * POST /v1/pipeline/start
     *
     * Creates a job record immediately (so the frontend gets a job_id to poll),
     * then dispatches to the AI service asynchronously.
     * Returns 202 Accepted.
     */
    @PostMapping("/v1/pipeline/start")
    public ResponseEntity<PipelineJobResponse> startPipeline(
            @Valid @RequestBody PipelineStartRequest request) {

        UUID workspaceId = workspaceContext.getWorkspaceId();
        PipelineJob job  = pipelineService.createJob(workspaceId, request);

        // Async — AI service handles the work and posts back results
        pipelineService.dispatchToAi(job.getId(), workspaceId, request);

        log.info("[pipeline] Started  jobId={}  endpoint={}  workspace={}",
                job.getId(), request.getEndpointPath(), workspaceId);

        return ResponseEntity
                .status(HttpStatus.ACCEPTED)
                .body(PipelineJobResponse.from(job));
    }

    /**
     * GET /v1/pipeline/jobs/{jobId}
     *
     * Returns the current job state including live progress logs.
     * The frontend polls this every 2s while status=running.
     */
    @GetMapping("/v1/pipeline/jobs/{jobId}")
    public ResponseEntity<PipelineJobResponse> getJob(@PathVariable UUID jobId) {
        UUID workspaceId = workspaceContext.getWorkspaceId();
        return jobRepository
                .findByIdAndWorkspaceId(jobId, workspaceId)
                .map(job -> ResponseEntity.ok(PipelineJobResponse.from(job)))
                .orElse(ResponseEntity.notFound().build());
    }

    // ── Internal endpoints (AI service → Java) ────────────────────────────────

    /**
     * POST /v1/internal/pipeline-result
     *
     * Called by the AI service when it finishes all LLM passes.
     * Writes entities/edges/contexts to the graph DB and marks the job done.
     */
    @PostMapping("/v1/internal/pipeline-result")
    public ResponseEntity<Void> pipelineResult(
            @RequestHeader("X-Internal-Key") String key,
            @RequestBody PipelineResultRequest result) {

        if (!internalApiKey.equals(key)) {
            log.warn("[pipeline] Rejected internal call — bad key");
            return ResponseEntity.status(HttpStatus.UNAUTHORIZED).build();
        }

        log.info("[pipeline] Received result  jobId={}  status={}",
                result.getJobId(), result.getStatus());
        pipelineService.applyResult(result);
        return ResponseEntity.ok().build();
    }

    /**
     * POST /v1/internal/pipeline-progress
     *
     * Called by the AI service after each stage to push live log entries.
     * The frontend sees these entries within 2s (next poll).
     */
    @PostMapping("/v1/internal/pipeline-progress")
    public ResponseEntity<Void> pipelineProgress(
            @RequestHeader("X-Internal-Key") String key,
            @RequestBody ProgressUpdate update) {

        if (!internalApiKey.equals(key)) {
            return ResponseEntity.status(HttpStatus.UNAUTHORIZED).build();
        }

        pipelineService.updateProgress(update.jobId(), update.logs());
        return ResponseEntity.ok().build();
    }

    /**
     * POST /v1/internal/artifacts/check-freshness
     *
     * Called by the AI service at pipeline start (pre-flight), BEFORE any LLM calls.
     *
     * The AI service sends the list of source_file artifacts it discovered and their
     * current content hashes.  Java responds with which are fresh (hash unchanged,
     * graph nodes already exist) vs dirty (extraction required).
     *
     * This is the key hook for hash-based incremental processing (Task #24).
     * On average, 80-90% of files are unchanged between pipeline runs, so the LLM
     * is only invoked for the changed subset.
     */
    @PostMapping("/v1/internal/artifacts/check-freshness")
    public ResponseEntity<ArtifactFreshnessResponse> checkArtifactFreshness(
            @RequestHeader("X-Internal-Key") String key,
            @RequestBody ArtifactFreshnessRequest request) {

        if (!internalApiKey.equals(key)) {
            log.warn("[freshness] Rejected check-freshness call — bad key");
            return ResponseEntity.status(HttpStatus.UNAUTHORIZED).build();
        }

        log.info("[freshness] check-freshness  workspace={}  artifacts={}",
                request.getWorkspaceId(),
                request.getArtifacts() != null ? request.getArtifacts().size() : 0);

        ArtifactFreshnessResponse response = artifactWriterService.checkFreshness(request);
        return ResponseEntity.ok(response);
    }

    record ProgressUpdate(UUID jobId, java.util.List<Object> logs) {}
}
