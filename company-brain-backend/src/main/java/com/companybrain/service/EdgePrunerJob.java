package com.companybrain.service;

import com.companybrain.repository.EdgeRepository;
import com.companybrain.repository.WorkspaceRepository;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Transactional;

import java.time.OffsetDateTime;

@Component
@RequiredArgsConstructor
@Slf4j
public class EdgePrunerJob {

    private final EdgeRepository edgeRepository;
    private final WorkspaceRepository workspaceRepository;

    /**
     * Runs daily at 2:00 AM.
     * For each workspace, prunes edges that have not been observed for 30 days.
     * Pruned edges are kept in the table but excluded from live traversal queries.
     */
    @Scheduled(cron = "0 0 2 * * *")
    @Transactional
    public void pruneStaleEdges() {
        OffsetDateTime cutoff = OffsetDateTime.now().minusDays(30);
        log.info("EdgePrunerJob starting. Pruning edges last seen before {}", cutoff);

        workspaceRepository.findAll().forEach(workspace -> {
            try {
                int pruned = edgeRepository.pruneStaleEdges(workspace.getId(), cutoff);
                if (pruned > 0) {
                    log.info("Pruned {} stale edges for workspace {} ({})",
                            pruned, workspace.getId(), workspace.getSlug());
                }
            } catch (Exception e) {
                log.error("Failed to prune edges for workspace {}: {}",
                        workspace.getId(), e.getMessage());
            }
        });

        log.info("EdgePrunerJob complete");
    }
}
