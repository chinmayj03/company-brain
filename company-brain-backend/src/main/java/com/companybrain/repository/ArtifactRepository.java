package com.companybrain.repository;

import com.companybrain.model.Artifact;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;
import org.springframework.stereotype.Repository;

import java.util.List;
import java.util.Optional;
import java.util.UUID;

@Repository
public interface ArtifactRepository extends JpaRepository<Artifact, UUID> {

    Optional<Artifact> findByWorkspaceIdAndKindAndExternalId(
            UUID workspaceId, String kind, String externalId);

    List<Artifact> findByWorkspaceIdAndKind(UUID workspaceId, String kind);

    /** Return all artifacts referenced by a set of artifact IDs (for dirty-set joins). */
    @Query("SELECT a FROM Artifact a WHERE a.id IN :ids")
    List<Artifact> findAllByIds(@Param("ids") List<UUID> ids);

    /**
     * Batch lookup for freshness checks — returns only artifacts whose externalId is in the given set.
     * Used by ArtifactWriterService.checkFreshness() to avoid N individual queries.
     */
    @Query("SELECT a FROM Artifact a WHERE a.workspaceId = :wid AND a.kind = :kind AND a.externalId IN :externalIds")
    List<Artifact> findByWorkspaceIdAndKindAndExternalIdIn(
            @Param("wid") UUID workspaceId,
            @Param("kind") String kind,
            @Param("externalIds") List<String> externalIds);
}
