package com.companybrain.repository;

import com.companybrain.model.Node;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

import java.util.Collection;
import java.util.List;
import java.util.Optional;
import java.util.UUID;

public interface NodeRepository extends JpaRepository<Node, UUID> {

    boolean existsByIdAndWorkspaceId(UUID id, UUID workspaceId);

    Optional<Node> findByWorkspaceIdAndNodeTypeAndExternalId(UUID workspaceId, String nodeType, String externalId);

    /** Used by PipelineService to resolve external_id → node without knowing the node_type. */
    Optional<Node> findByWorkspaceIdAndExternalId(UUID workspaceId, String externalId);

    @Query("SELECT n FROM Node n WHERE n.workspaceId = :wid AND LOWER(n.name) LIKE LOWER(CONCAT('%', :q, '%')) ORDER BY n.name")
    List<Node> searchByName(@Param("wid") UUID wid, @Param("q") String q, Pageable pageable);

    @Query("SELECT n FROM Node n WHERE n.workspaceId = :wid AND n.nodeType = :type ORDER BY n.name")
    List<Node> findByWorkspaceIdAndNodeType(@Param("wid") UUID wid, @Param("type") String type, Pageable pageable);

    /**
     * Bulk fetch by external_id — used by PipelineService to pre-populate its node cache in ONE query
     * instead of N individual findByWorkspaceIdAndExternalId calls.
     */
    List<Node> findByWorkspaceIdAndExternalIdIn(UUID workspaceId, Collection<String> externalIds);

    /**
     * Batch lookup for freshness checks — returns all nodes derived from the given artifact external_ids.
     *
     * We use the artifact_links join to find nodes whose source artifact externalId is in the given set.
     * This enables the freshness endpoint to return existingEntities without N+1 queries.
     */
    @Query("""
            SELECT DISTINCT n FROM Node n
            JOIN ArtifactLink al ON al.nodeId = n.id
            JOIN Artifact a ON a.id = al.artifactId
            WHERE n.workspaceId = :wid
              AND a.workspaceId = :wid
              AND a.externalId IN :artifactExternalIds
            """)
    List<Node> findByWorkspaceIdAndArtifactExternalIdIn(
            @Param("wid") UUID wid,
            @Param("artifactExternalIds") List<String> artifactExternalIds);
}
