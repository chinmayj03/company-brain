package com.example.repository;

import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;
import java.util.UUID;
import java.util.List;

public interface NodeRepository extends JpaRepository<Node, UUID> {

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
