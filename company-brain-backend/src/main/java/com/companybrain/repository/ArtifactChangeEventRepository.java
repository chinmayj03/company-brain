package com.companybrain.repository;

import com.companybrain.model.ArtifactChangeEvent;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;
import org.springframework.stereotype.Repository;

import java.time.OffsetDateTime;
import java.util.List;
import java.util.UUID;

@Repository
public interface ArtifactChangeEventRepository extends JpaRepository<ArtifactChangeEvent, Long> {

    /** All unconsumed events for a workspace — consumed by DirtySetService. */
    List<ArtifactChangeEvent> findByWorkspaceIdAndConsumedAtIsNullOrderByOccurredAtAsc(
            UUID workspaceId);

    /** Mark a batch of events as consumed. */
    @Modifying
    @Query("""
            UPDATE ArtifactChangeEvent e
            SET e.consumedAt = :now
            WHERE e.id IN :ids
            """)
    void markConsumed(@Param("ids") List<Long> ids, @Param("now") OffsetDateTime now);

    /** Count unconsumed events — used by health/monitoring endpoints. */
    long countByWorkspaceIdAndConsumedAtIsNull(UUID workspaceId);
}
