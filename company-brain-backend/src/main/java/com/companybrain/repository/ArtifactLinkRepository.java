package com.companybrain.repository;

import com.companybrain.model.ArtifactLink;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;
import org.springframework.stereotype.Repository;

import java.util.List;
import java.util.UUID;

@Repository
public interface ArtifactLinkRepository extends JpaRepository<ArtifactLink, ArtifactLink.PK> {

    /** All links for a given node — used to find sources when explaining a node. */
    List<ArtifactLink> findByWorkspaceIdAndNodeId(UUID workspaceId, UUID nodeId);

    /** All nodes derived from a given artifact — used by dirty-set engine. */
    List<ArtifactLink> findByWorkspaceIdAndArtifactIdAndLinkRoleIn(
            UUID workspaceId, UUID artifactId, List<String> linkRoles);

    /** All node IDs derived from a set of artifact IDs — batched dirty-set query. */
    @Query("""
            SELECT DISTINCT al.nodeId FROM ArtifactLink al
            WHERE al.workspaceId = :workspaceId
              AND al.artifactId IN :artifactIds
              AND al.linkRole IN ('derived_from', 'cited_in_context')
            """)
    List<UUID> findDirtyNodeIds(
            @Param("workspaceId") UUID workspaceId,
            @Param("artifactIds") List<UUID> artifactIds);

    /** Delete all links for a node (called before re-inserting fresh provenance). */
    void deleteByWorkspaceIdAndNodeIdAndLinkRole(
            UUID workspaceId, UUID nodeId, String linkRole);
}
