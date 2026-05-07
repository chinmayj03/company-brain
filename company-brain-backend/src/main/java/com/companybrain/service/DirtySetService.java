package com.companybrain.service;

import com.companybrain.model.ArtifactChangeEvent;
import com.companybrain.model.Edge;
import com.companybrain.repository.ArtifactChangeEventRepository;
import com.companybrain.repository.ArtifactLinkRepository;
import com.companybrain.repository.EdgeRepository;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.OffsetDateTime;
import java.util.*;
import java.util.stream.Collectors;

/**
 * Computes the set of graph nodes that need re-extraction after artifact changes.
 *
 * Algorithm:
 *   1. Read all unconsumed artifact_change_events for the workspace
 *   2. Find directly dirty nodes: those with an artifact_link to any changed artifact
 *   3. Expand transitively: nodes whose call chain reaches a directly-dirty node
 *      (bounded reverse traversal over CALLS and READS_TABLE edges, depth ≤ 2)
 *   4. Mark the consumed events so they aren't processed again
 *
 * This is kind-agnostic — a changed Jira ticket invalidates nodes through
 * the same code path as a changed Java source file.
 *
 * See ADR-005: Artifact-Centric Knowledge Pipeline.
 */
@Service
@RequiredArgsConstructor
@Slf4j
public class DirtySetService {

    /** Edge types that propagate dirtiness upstream in the call chain. */
    private static final Set<String> PROPAGATING_EDGE_TYPES = Set.of("CALLS", "READS_TABLE", "READS_COLUMN");

    /** How many hops upstream we propagate dirtiness. */
    private static final int MAX_PROPAGATION_DEPTH = 2;

    private final ArtifactChangeEventRepository changeEventRepository;
    private final ArtifactLinkRepository        artifactLinkRepository;
    private final EdgeRepository                edgeRepository;

    // ── Public API ────────────────────────────────────────────────────────────

    /**
     * Compute the dirty node set and mark events consumed.
     *
     * @param workspaceId  Workspace to process
     * @return             Set of node UUIDs that need re-extraction.
     *                     Empty if nothing has changed since the last run.
     */
    @Transactional
    public Set<UUID> computeAndConsume(UUID workspaceId) {
        List<ArtifactChangeEvent> events =
                changeEventRepository.findByWorkspaceIdAndConsumedAtIsNullOrderByOccurredAtAsc(workspaceId);

        if (events.isEmpty()) {
            log.debug("[dirty-set] No unconsumed events  workspace={}", workspaceId);
            return Set.of();
        }

        log.info("[dirty-set] Processing {} unconsumed events  workspace={}", events.size(), workspaceId);

        // Extract artifact IDs from the change events
        List<UUID> changedArtifactIds = events.stream()
                .map(ArtifactChangeEvent::getArtifactId)
                .distinct()
                .toList();

        // Step 1: Direct dirty nodes — those linked to a changed artifact
        List<UUID> directDirty = artifactLinkRepository.findDirtyNodeIds(workspaceId, changedArtifactIds);

        if (directDirty.isEmpty()) {
            markConsumed(events);
            log.info("[dirty-set] No nodes linked to changed artifacts  workspace={}", workspaceId);
            return Set.of();
        }

        // Step 2: Transitive dirty nodes — nodes upstream in the call chain
        Set<UUID> allDirty = new HashSet<>(directDirty);
        Set<UUID> frontier = new HashSet<>(directDirty);

        for (int depth = 0; depth < MAX_PROPAGATION_DEPTH && !frontier.isEmpty(); depth++) {
            Set<UUID> nextFrontier = new HashSet<>();
            for (UUID nodeId : frontier) {
                // Find nodes that CALL or READ this dirty node (reverse traversal)
                List<Edge> incomingEdges = edgeRepository.findByWorkspaceIdAndTarget_IdAndEdgeTypeIn(
                        workspaceId, nodeId, PROPAGATING_EDGE_TYPES);
                for (Edge edge : incomingEdges) {
                    UUID upstreamId = edge.getSource().getId();
                    if (allDirty.add(upstreamId)) {     // add returns false if already present
                        nextFrontier.add(upstreamId);
                    }
                }
            }
            frontier = nextFrontier;
        }

        markConsumed(events);

        log.info("[dirty-set] Dirty set computed  workspace={}  direct={}  total={}",
                workspaceId, directDirty.size(), allDirty.size());

        return Collections.unmodifiableSet(allDirty);
    }

    /**
     * Quick check: how many unconsumed events exist for a workspace.
     * Used by health / monitoring endpoints.
     */
    public long pendingEventCount(UUID workspaceId) {
        return changeEventRepository.countByWorkspaceIdAndConsumedAtIsNull(workspaceId);
    }

    // ── Internal helpers ──────────────────────────────────────────────────────

    private void markConsumed(List<ArtifactChangeEvent> events) {
        List<Long> ids = events.stream()
                .map(ArtifactChangeEvent::getId)
                .toList();
        changeEventRepository.markConsumed(ids, OffsetDateTime.now());
    }
}
