package com.example.repository;

import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;
import org.springframework.transaction.annotation.Transactional;
import java.util.UUID;

public interface PipelineJobRepository extends JpaRepository<PipelineJob, UUID> {

    @Transactional
    @Modifying
    @Query(value = """
            UPDATE pipeline_jobs SET
                status            = 'completed',
                completed_at      = now(),
                entity_count      = :entityCount,
                edge_count        = :edgeCount
            WHERE id = :jobId
            """, nativeQuery = true)
    void markCompleted(
            @Param("jobId")       UUID jobId,
            @Param("entityCount") int entityCount,
            @Param("edgeCount")   int edgeCount);
}
