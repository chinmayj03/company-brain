package com.companybrain.repository;

import com.companybrain.model.WorkspaceSource;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

import java.util.List;
import java.util.Optional;
import java.util.UUID;

public interface WorkspaceSourceRepository extends JpaRepository<WorkspaceSource, UUID> {

    List<WorkspaceSource> findByWorkspaceIdOrderByDisplayNameAsc(UUID workspaceId);

    Optional<WorkspaceSource> findByIdAndWorkspaceId(UUID id, UUID workspaceId);

    @Modifying
    @Query("UPDATE WorkspaceSource s SET s.syncStatus = :status, s.errorMessage = :error WHERE s.id = :id")
    void updateSyncStatus(@Param("id") UUID id,
                          @Param("status") String status,
                          @Param("error") String error);

    @Modifying
    @Query("UPDATE WorkspaceSource s SET s.syncStatus = 'ok', s.lastSyncedAt = CURRENT_TIMESTAMP, s.entityCount = :count, s.errorMessage = null WHERE s.id = :id")
    void markSyncOk(@Param("id") UUID id, @Param("count") int entityCount);

    @Modifying
    @Query("UPDATE WorkspaceSource s SET s.lastJobId = :jobId WHERE s.id = :id")
    void updateLastJobId(@Param("id") UUID id, @Param("jobId") UUID jobId);

    Optional<WorkspaceSource> findByLastJobId(UUID lastJobId);
}
