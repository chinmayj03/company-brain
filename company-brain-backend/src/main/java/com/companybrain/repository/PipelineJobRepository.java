package com.companybrain.repository;

import com.companybrain.model.PipelineJob;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

import java.util.List;
import java.util.Optional;
import java.util.UUID;

public interface PipelineJobRepository extends JpaRepository<PipelineJob, UUID> {

    Optional<PipelineJob> findByIdAndWorkspaceId(UUID id, UUID workspaceId);
    List<PipelineJob> findByWorkspaceIdOrderByStartedAtDesc(UUID workspaceId);

    /**
     * Update only the progress_logs column — no SELECT needed.
     * Avoids the fetch-then-save pattern that fires a SELECT on every progress push.
     */
    @Modifying
    @Query(value = "UPDATE pipeline_jobs SET progress_logs = CAST(:logs AS jsonb) WHERE id = :jobId",
           nativeQuery = true)
    void updateProgressLogs(@Param("jobId") UUID jobId, @Param("logs") String logsJson);

    /**
     * Mark a job as completed in a single UPDATE — no SELECT + save round-trip.
     * stage_summary and progress_logs are JSONB columns; Spring passes them as text
     * which Postgres CAST handles automatically.
     */
    @Modifying
    @Query(value = """
            UPDATE pipeline_jobs SET
                status            = 'completed',
                completed_at      = now(),
                entity_count      = :entityCount,
                edge_count        = :edgeCount,
                gap_count         = :gapCount,
                code_units_found  = :codeUnits,
                git_commits_found = :gitCommits,
                files_traced      = CAST(:filesTraced  AS jsonb),
                stages_summary    = CAST(:stagesSummary AS jsonb),
                progress_logs     = CAST(:progressLogs  AS jsonb)
            WHERE id = :jobId
            """, nativeQuery = true)
    void markCompleted(
            @Param("jobId")         UUID    jobId,
            @Param("entityCount")   int     entityCount,
            @Param("edgeCount")     int     edgeCount,
            @Param("gapCount")      int     gapCount,
            @Param("codeUnits")     Integer codeUnits,
            @Param("gitCommits")    Integer gitCommits,
            @Param("filesTraced")   String  filesTraced,   // JSON array e.g. ["Foo.java","Bar.java"]
            @Param("stagesSummary") String  stagesSummary,
            @Param("progressLogs")  String  progressLogs
    );

    /**
     * Mark a job as failed in a single UPDATE — no SELECT + save round-trip.
     */
    @Modifying
    @Query(value = """
            UPDATE pipeline_jobs SET
                status        = 'failed',
                completed_at  = now(),
                error_message = :errorMessage,
                progress_logs = CAST(:progressLogs AS jsonb)
            WHERE id = :jobId
            """, nativeQuery = true)
    void markFailed(
            @Param("jobId")        UUID   jobId,
            @Param("errorMessage") String errorMessage,
            @Param("progressLogs") String progressLogs
    );
}
