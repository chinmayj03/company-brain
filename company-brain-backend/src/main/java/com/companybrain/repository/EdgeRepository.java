package com.companybrain.repository;

import com.companybrain.model.Edge;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

import java.time.OffsetDateTime;
import java.util.List;
import java.util.Optional;
import java.util.UUID;

public interface EdgeRepository extends JpaRepository<Edge, UUID> {

    /** Used by PipelineService to upsert edges — find by workspace + source + target + type. */
    Optional<Edge> findByWorkspaceIdAndSource_IdAndTarget_IdAndEdgeType(
            UUID workspaceId, UUID sourceId, UUID targetId, String edgeType);

    List<Edge> findByWorkspaceIdAndSource_IdAndIsPrunedFalse(UUID workspaceId, UUID sourceId);

    List<Edge> findByWorkspaceIdAndTarget_IdAndIsPrunedFalse(UUID workspaceId, UUID targetId);

    List<Edge> findByWorkspaceIdAndSource_IdAndEdgeTypeAndIsPrunedFalse(UUID workspaceId, UUID sourceId, String edgeType);

    List<Edge> findByWorkspaceIdAndTarget_IdAndEdgeTypeAndIsPrunedFalse(UUID workspaceId, UUID targetId, String edgeType);

    @Modifying
    @Query("UPDATE Edge e SET e.isPruned = true WHERE e.workspaceId = :wid AND e.lastSeen < :cutoff AND e.isPruned = false")
    int pruneStaleEdges(@Param("wid") UUID wid, @Param("cutoff") OffsetDateTime cutoff);

    /**
     * Reverse traversal for DirtySetService: find edges that TARGET a given node
     * and have one of the propagating edge types (CALLS, READS_TABLE, READS_COLUMN).
     * Returns upstream callers so dirtiness can bubble up the call chain.
     */
    List<Edge> findByWorkspaceIdAndTarget_IdAndEdgeTypeIn(
            UUID workspaceId, UUID targetId, java.util.Collection<String> edgeTypes);
}
