package com.example.repository;

import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;
import java.util.UUID;
import java.util.List;

public interface ArtifactLinkRepository extends JpaRepository<ArtifactLink, ArtifactLink.PK> {

    @Query("""
            SELECT DISTINCT al.nodeId FROM ArtifactLink al
            WHERE al.workspaceId = :workspaceId
              AND al.artifactId IN :artifactIds
              AND al.linkRole IN ('derived_from', 'cited_in_context')
            """)
    List<UUID> findDirtyNodeIds(
            @Param("workspaceId") UUID workspaceId,
            @Param("artifactIds") List<UUID> artifactIds);
}
