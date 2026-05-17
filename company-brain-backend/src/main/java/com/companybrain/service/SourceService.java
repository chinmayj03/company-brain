package com.companybrain.service;

import com.companybrain.dto.PipelineStartRequest;
import com.companybrain.dto.RegisterSourceRequest;
import com.companybrain.dto.RegisterSourceResponse;
import com.companybrain.dto.WorkspaceSourceDto;
import com.companybrain.model.PipelineJob;
import com.companybrain.model.WorkspaceSource;
import com.companybrain.repository.WorkspaceSourceRepository;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.List;
import java.util.Map;
import java.util.NoSuchElementException;
import java.util.UUID;

@Service
@RequiredArgsConstructor
@Slf4j
public class SourceService {

    private final WorkspaceSourceRepository sourceRepository;
    private final PipelineService           pipelineService;

    // ── List ──────────────────────────────────────────────────────────────────

    public List<WorkspaceSourceDto> listSources(UUID workspaceId) {
        return sourceRepository.findByWorkspaceIdOrderByDisplayNameAsc(workspaceId)
                .stream()
                .map(WorkspaceSourceDto::from)
                .toList();
    }

    // ── Register ─────────────────────────────────────────────────────────────

    @Transactional
    public RegisterSourceResponse registerSource(UUID workspaceId, RegisterSourceRequest req) {
        String url = extractUrl(req.getConfig());

        WorkspaceSource source = WorkspaceSource.builder()
                .workspaceId(workspaceId)
                .kind(req.getKind())
                .displayName(req.getDisplayName())
                .url(url)
                .syncStatus("pending")
                .config(req.getConfig())
                .build();
        source = sourceRepository.save(source);
        log.info("[sources] Registered source={}  kind={}  workspace={}", source.getId(), source.getKind(), workspaceId);

        UUID jobId = null;
        if (req.isAutoIndex() && isIndexableKind(req.getKind())) {
            jobId = dispatchIndexJob(source, workspaceId);
        }

        return RegisterSourceResponse.builder()
                .source(WorkspaceSourceDto.from(source))
                .jobId(jobId)
                .build();
    }

    // ── Delete ────────────────────────────────────────────────────────────────

    @Transactional
    public void deleteSource(UUID workspaceId, UUID sourceId) {
        WorkspaceSource source = sourceRepository.findByIdAndWorkspaceId(sourceId, workspaceId)
                .orElseThrow(() -> new NoSuchElementException("Source not found: " + sourceId));
        sourceRepository.delete(source);
        log.info("[sources] Deleted source={}  workspace={}", sourceId, workspaceId);
    }

    // ── Sync ──────────────────────────────────────────────────────────────────

    @Transactional
    public UUID triggerSync(UUID workspaceId, UUID sourceId) {
        WorkspaceSource source = sourceRepository.findByIdAndWorkspaceId(sourceId, workspaceId)
                .orElseThrow(() -> new NoSuchElementException("Source not found: " + sourceId));

        UUID jobId = dispatchIndexJob(source, workspaceId);
        log.info("[sources] Sync triggered  source={}  job={}  workspace={}", sourceId, jobId, workspaceId);
        return jobId;
    }

    // ── Cancel ────────────────────────────────────────────────────────────────

    @Transactional
    public void cancelSync(UUID workspaceId, UUID sourceId) {
        WorkspaceSource source = sourceRepository.findByIdAndWorkspaceId(sourceId, workspaceId)
                .orElseThrow(() -> new NoSuchElementException("Source not found: " + sourceId));
        if (!"syncing".equals(source.getSyncStatus())) {
            return; // nothing to cancel
        }
        sourceRepository.updateSyncStatus(sourceId, "error", "Sync cancelled");
        log.info("[sources] Sync cancelled  source={}  workspace={}", sourceId, workspaceId);
    }

    // ── Internal helpers ──────────────────────────────────────────────────────

    private UUID dispatchIndexJob(WorkspaceSource source, UUID workspaceId) {
        Map<String, Object> config = source.getConfig() != null ? source.getConfig() : Map.of();
        String repoPath = configStr(config, "repo_path", configStr(config, "clone_url", ""));
        String branch   = configStr(config, "branch", "main");

        PipelineStartRequest req = new PipelineStartRequest();
        req.setEndpointPath(repoPath.isBlank() ? source.getDisplayName() : repoPath);
        req.setHttpMethod("SCAN");
        req.setBranch(branch);

        PipelineStartRequest.RepoInput repoInput = new PipelineStartRequest.RepoInput();
        repoInput.setLocalPath(repoPath.isBlank() ? null : repoPath);
        repoInput.setUrl(configStr(config, "clone_url", null));
        repoInput.setType("backend");
        repoInput.setBranch(branch);
        req.setRepos(List.of(repoInput));

        // Mark syncing before dispatch so the UI reflects the state immediately
        sourceRepository.updateSyncStatus(source.getId(), "syncing", null);

        PipelineJob job = pipelineService.createJob(workspaceId, req);
        sourceRepository.updateLastJobId(source.getId(), job.getId());
        pipelineService.dispatchToAi(job.getId(), workspaceId, req);
        return job.getId();
    }

    private static boolean isIndexableKind(String kind) {
        return "git_local".equals(kind) || "git_remote".equals(kind);
    }

    private static String extractUrl(Map<String, Object> config) {
        if (config == null) return null;
        for (String key : List.of("spec_path_or_url", "clone_url", "repo_path")) {
            Object v = config.get(key);
            if (v instanceof String s && !s.isBlank()) return s;
        }
        return null;
    }

    private static String configStr(Map<String, Object> config, String key, String fallback) {
        Object v = config.get(key);
        return (v instanceof String s && !s.isBlank()) ? s : fallback;
    }
}
