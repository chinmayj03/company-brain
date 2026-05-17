package com.example.repository;

import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;
import java.util.UUID;
import java.util.Optional;

public interface ArtifactRepository extends JpaRepository<Artifact, UUID> {

    @Query("SELECT a FROM Artifact a WHERE a.id IN :ids")
    List<Artifact> findAllByIds(@Param("ids") List<UUID> ids);
}
