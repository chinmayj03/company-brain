package com.example.repository;

import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;
import java.util.UUID;
import java.util.List;

public interface ArtifactRepository extends JpaRepository<Artifact, UUID> {

    @Query("SELECT a FROM Artifact a WHERE a.workspaceId = :wid AND a.kind = :kind AND a.externalId IN :externalIds")
    List<Artifact> findByWorkspaceIdAndKindAndExternalIdIn(
            @Param("wid") UUID workspaceId,
            @Param("kind") String kind,
            @Param("externalIds") List<String> externalIds);
}
