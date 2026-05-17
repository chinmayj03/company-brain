package com.example.repository;

import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;
import java.util.UUID;

public interface WorkspaceSourceRepository extends JpaRepository<WorkspaceSource, UUID> {

    @Modifying
    @Query("UPDATE WorkspaceSource s SET s.syncStatus = :status, s.errorMessage = :error WHERE s.id = :id")
    void updateSyncStatus(@Param("id") UUID id,
                          @Param("status") String status,
                          @Param("error") String error);

    @Modifying
    @Query("UPDATE WorkspaceSource s SET s.syncStatus = 'ok', s.lastSyncedAt = CURRENT_TIMESTAMP, s.entityCount = :count, s.errorMessage = null WHERE s.id = :id")
    void markSyncOk(@Param("id") UUID id, @Param("count") int entityCount);
}
