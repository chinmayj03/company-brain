package com.companybrain.model;

import jakarta.persistence.*;
import lombok.*;
import org.hibernate.annotations.JdbcTypeCode;
import org.hibernate.type.SqlTypes;

import java.time.OffsetDateTime;
import java.util.List;
import java.util.UUID;

/**
 * Tracks one pipeline run (one API endpoint → knowledge graph population).
 * The AI service writes back results via POST /v1/internal/pipeline-result;
 * this table is the single source of truth for job status.
 */
@Entity
@Table(name = "pipeline_jobs")
@Getter @Setter @NoArgsConstructor @Builder
@AllArgsConstructor
public class PipelineJob {

    @Id
    @GeneratedValue(strategy = GenerationType.UUID)
    private UUID id;

    @Column(name = "workspace_id", nullable = false)
    private UUID workspaceId;

    @Column(name = "endpoint_path", nullable = false)
    private String endpointPath;

    @Column(name = "http_method")
    private String httpMethod;

    /** queued | running | completed | failed */
    @Column(nullable = false)
    private String status;

    @Column(name = "error_message")
    private String errorMessage;

    // ── Result summary ────────────────────────────────────────────────
    @Column(name = "entity_count")
    private Integer entityCount;

    @Column(name = "edge_count")
    private Integer edgeCount;

    @Column(name = "gap_count")
    private Integer gapCount;

    @Column(name = "code_units_found")
    private Integer codeUnitsFound;

    @Column(name = "git_commits_found")
    private Integer gitCommitsFound;

    @JdbcTypeCode(SqlTypes.JSON)
    @Column(name = "files_traced", columnDefinition = "jsonb")
    private List<String> filesTraced;

    @JdbcTypeCode(SqlTypes.JSON)
    @Column(name = "stages_summary", columnDefinition = "jsonb")
    private List<Object> stagesSummary;

    @JdbcTypeCode(SqlTypes.JSON)
    @Column(name = "progress_logs", columnDefinition = "jsonb")
    private List<Object> progressLogs;

    // ── Timestamps ────────────────────────────────────────────────────
    @Column(name = "created_at")
    private OffsetDateTime createdAt;

    @Column(name = "started_at")
    private OffsetDateTime startedAt;

    @Column(name = "completed_at")
    private OffsetDateTime completedAt;

    @PrePersist
    void prePersist() {
        if (createdAt == null) createdAt = OffsetDateTime.now();
        if (status == null) status = "queued";
    }
}
