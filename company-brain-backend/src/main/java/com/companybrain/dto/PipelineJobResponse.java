package com.companybrain.dto;

import com.companybrain.model.PipelineJob;
import lombok.Builder;
import lombok.Data;

import java.time.OffsetDateTime;
import java.util.List;
import java.util.UUID;

/** Java → Frontend: job status + live progress logs. */
@Data @Builder
public class PipelineJobResponse {

    private UUID jobId;
    private String status;       // queued | running | completed | failed
    private String error;

    // Live progress (populated while running and after completion)
    private Progress progress;

    // Rich result (populated when completed)
    private Result result;

    private OffsetDateTime createdAt;
    private OffsetDateTime startedAt;
    private OffsetDateTime completedAt;

    @Data @Builder
    public static class Progress {
        private List<Object> logs;
        private String currentStage;
    }

    @Data @Builder
    public static class Result {
        private int entityCount;
        private int edgeCount;
        private int gapCount;
        private int codeUnitsFound;
        private int gitCommitsFound;
        private List<String> filesTraced;
        private List<Object> stagesSummary;
    }

    /** Map a PipelineJob entity to the response DTO. */
    public static PipelineJobResponse from(PipelineJob job) {
        var builder = PipelineJobResponse.builder()
                .jobId(job.getId())
                .status(job.getStatus())
                .error(job.getErrorMessage())
                .createdAt(job.getCreatedAt())
                .startedAt(job.getStartedAt())
                .completedAt(job.getCompletedAt());

        if (job.getProgressLogs() != null) {
            builder.progress(Progress.builder()
                    .logs(job.getProgressLogs())
                    .currentStage("completed".equals(job.getStatus()) ? "done" : "running")
                    .build());
        }

        if ("completed".equals(job.getStatus())) {
            builder.result(Result.builder()
                    .entityCount(job.getEntityCount() != null ? job.getEntityCount() : 0)
                    .edgeCount(job.getEdgeCount() != null ? job.getEdgeCount() : 0)
                    .gapCount(job.getGapCount() != null ? job.getGapCount() : 0)
                    .codeUnitsFound(job.getCodeUnitsFound() != null ? job.getCodeUnitsFound() : 0)
                    .gitCommitsFound(job.getGitCommitsFound() != null ? job.getGitCommitsFound() : 0)
                    .filesTraced(job.getFilesTraced())
                    .stagesSummary(job.getStagesSummary())
                    .build());
        }

        return builder.build();
    }
}
