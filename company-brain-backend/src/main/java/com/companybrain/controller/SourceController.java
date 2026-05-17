package com.companybrain.controller;

import com.companybrain.dto.RegisterSourceRequest;
import com.companybrain.dto.RegisterSourceResponse;
import com.companybrain.dto.WorkspaceSourceDto;
import com.companybrain.security.WorkspaceContext;
import com.companybrain.service.SourceService;
import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.util.List;
import java.util.Map;
import java.util.NoSuchElementException;
import java.util.UUID;

/**
 * Source registry — CRUD + sync trigger.
 *
 * GET    /v1/workspaces/{workspaceId}/sources
 * POST   /v1/workspaces/{workspaceId}/sources
 * DELETE /v1/workspaces/{workspaceId}/sources/{sourceId}
 * POST   /v1/workspaces/{workspaceId}/sources/{sourceId}/sync
 *
 * All endpoints are JWT-authenticated (covered by /v1/** in SecurityConfig).
 * The workspace is read from the JWT via WorkspaceContext; the path param is
 * validated against it so users can only touch their own workspace's sources.
 */
@RestController
@RequiredArgsConstructor
@Slf4j
public class SourceController {

    private final SourceService   sourceService;
    private final WorkspaceContext workspaceContext;

    @GetMapping("/v1/workspaces/{workspaceId}/sources")
    public List<WorkspaceSourceDto> listSources(@PathVariable UUID workspaceId) {
        assertWorkspace(workspaceId);
        return sourceService.listSources(workspaceId);
    }

    @PostMapping("/v1/workspaces/{workspaceId}/sources")
    public ResponseEntity<RegisterSourceResponse> registerSource(
            @PathVariable UUID workspaceId,
            @Valid @RequestBody RegisterSourceRequest req) {
        assertWorkspace(workspaceId);
        RegisterSourceResponse resp = sourceService.registerSource(workspaceId, req);
        log.info("[sources] POST register  workspace={}  kind={}  source={}",
                workspaceId, req.getKind(), resp.getSource().getId());
        return ResponseEntity.status(HttpStatus.CREATED).body(resp);
    }

    @DeleteMapping("/v1/workspaces/{workspaceId}/sources/{sourceId}")
    public ResponseEntity<Void> deleteSource(
            @PathVariable UUID workspaceId,
            @PathVariable UUID sourceId) {
        assertWorkspace(workspaceId);
        try {
            sourceService.deleteSource(workspaceId, sourceId);
            return ResponseEntity.noContent().build();
        } catch (NoSuchElementException e) {
            return ResponseEntity.notFound().build();
        }
    }

    @PostMapping("/v1/workspaces/{workspaceId}/sources/{sourceId}/sync")
    public ResponseEntity<Map<String, Object>> triggerSync(
            @PathVariable UUID workspaceId,
            @PathVariable UUID sourceId) {
        assertWorkspace(workspaceId);
        try {
            UUID jobId = sourceService.triggerSync(workspaceId, sourceId);
            return ResponseEntity.accepted().body(Map.of(
                    "status",    "accepted",
                    "source_id", sourceId.toString(),
                    "job_id",    jobId.toString()
            ));
        } catch (NoSuchElementException e) {
            return ResponseEntity.notFound().build();
        }
    }

    @PostMapping("/v1/workspaces/{workspaceId}/sources/{sourceId}/sync/cancel")
    public ResponseEntity<Void> cancelSync(
            @PathVariable UUID workspaceId,
            @PathVariable UUID sourceId) {
        assertWorkspace(workspaceId);
        try {
            sourceService.cancelSync(workspaceId, sourceId);
            log.info("[sources] POST cancel  workspace={}  source={}", workspaceId, sourceId);
            return ResponseEntity.ok().build();
        } catch (NoSuchElementException e) {
            return ResponseEntity.notFound().build();
        }
    }

    private void assertWorkspace(UUID pathWorkspaceId) {
        UUID tokenWorkspaceId = workspaceContext.getWorkspaceId();
        if (tokenWorkspaceId != null && !tokenWorkspaceId.equals(pathWorkspaceId)) {
            throw new org.springframework.security.access.AccessDeniedException(
                    "Workspace mismatch: token=" + tokenWorkspaceId + " path=" + pathWorkspaceId);
        }
    }
}
