package com.example.repository;

import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;
import java.time.OffsetDateTime;
import java.util.UUID;

public interface EdgeRepository extends JpaRepository<Edge, UUID> {

    @Modifying
    @Query("UPDATE Edge e SET e.isPruned = true WHERE e.workspaceId = :wid AND e.lastSeen < :cutoff AND e.isPruned = false")
    int pruneStaleEdges(@Param("wid") UUID wid, @Param("cutoff") OffsetDateTime cutoff);
}
