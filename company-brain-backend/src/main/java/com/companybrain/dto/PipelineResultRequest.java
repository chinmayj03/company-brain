package com.companybrain.dto;

import lombok.Data;

import java.util.List;
import java.util.Map;
import java.util.UUID;

/**
 * AI service → Java: the full extraction result for one pipeline run.
 *
 * Posted to POST /v1/internal/pipeline-result after the AI service
 * finishes all LLM passes. Java writes entities/edges/contexts to the
 * graph DB and updates the job record.
 *
 * Auth: X-Internal-Key header (service-to-service, not user JWT).
 *
 * Updated (ADR-005): now includes artifacts[] and artifactLinks[] so that
 * provenance is persisted alongside graph nodes.
 */

/**
 * AI service → Java: the full extraction result for one pipeline run.
 *
 * Posted to POST /v1/internal/pipeline-result after the AI service
 * finishes all LLM passes. Java writes entities/edges/contexts to the
 * graph DB and updates the job record.
 *
 * Auth: X-Internal-Key header (service-to-service, not user JWT).
 */
@Data
public class PipelineResultRequest {

    /** Must match an existing pipeline_jobs.id. */
    private UUID jobId;
    private UUID workspaceId;

    private String status;       // completed | failed
    private String errorMessage;

    // ── Extraction results ────────────────────────────────────────────

    private List<EntityDto> entities;
    private List<RelationshipDto> relationships;
    private List<ContextDto> contexts;

    // ── Artifact provenance (ADR-005) ─────────────────────────────────
    // Artifacts are the raw inputs the LLM pipeline was run over.
    // artifactLinks maps entity external_id → list of source artifact IDs.

    /** Raw input artifacts emitted by collectors for this pipeline run. */
    private List<ArtifactDto> artifacts;

    /**
     * Provenance links: for each entity (by externalId), which artifact IDs
     * were it derived from.  Written as artifact_links rows with role='derived_from'.
     */
    private Map<String, List<String>> artifactLinks;  // entityExternalId → [artifactExternalId, ...]

    // ── ADR-003: Intent contexts (FunctionContext per entity) ─────────
    /**
     * Maps entity external_id → FunctionContext dict (from IntentSynthesizer Stage 1.5).
     * PipelineService merges these into each node's JSONB metadata under "functionContext".
     * The ContextAssemblerService reads them back for T2 block rendering in Ask queries.
     *
     * Keys are entity external_ids (e.g. "repo/src/Foo.java::chargePayment").
     * Values are the serialized FunctionContext schema fields.
     */
    private Map<String, Map<String, Object>> intentContexts;

    // ── Pipeline diagnostics (stored on the job record) ───────────────

    private int codeUnitsFound;
    private int gitCommitsFound;
    private List<String> filesTraced;
    private List<Object> stagesSummary;
    private List<Object> progressLogs;

    // ── Nested DTOs ───────────────────────────────────────────────────

    @Data
    public static class EntityDto {
        private String entityType;    // Function | Class | ApiEndpoint | etc.
        private String name;
        private String file;
        private String repo;
        private String signature;
        private Double confidence;
        private String firstAppearedCommit;
        private String lastModifiedCommit;
        /** Raw SQL/JPQL string for DatabaseQuery entities. */
        private String queryText;
    }

    @Data
    public static class RelationshipDto {
        private String fromEntity;    // external_id
        private String fromType;
        private String edgeType;      // CALLS | READS_COLUMN | RENDERS_FIELD | etc.
        private String toEntity;      // external_id
        private String toType;
        private Double confidence;
        private String evidence;
    }

    @Data
    public static class ContextDto {
        private String entityExternalId;
        private String purpose;
        private String historySummary;
        private List<String> invariants;
        private String changeRisk;
        private String changeRiskReason;
        private String sourceConfidence;
        private String ownerTeam;
        private List<String> externalDependencies;
        private List<String> gaps;
    }

    // ── ADR-005: Artifact DTOs ────────────────────────────────────────

    /**
     * One artifact emitted by a collector.
     * The AI service includes these alongside entities so ArtifactWriterService
     * can upsert them and emit change events.
     */
    @Data
    public static class ArtifactDto {
        /** Artifact kind: source_file | pr | commit | annotation | ticket | ... */
        private String kind;

        /**
         * Stable source-derived identifier.
         * Must be unique within (workspace_id, kind).
         * e.g. "repo/src/main/java/Foo.java" for source_file,
         *      "myrepo::abc123" for a commit,
         *      "jira::CB-1234" for a ticket.
         */
        private String externalId;

        /** Raw content (for inline storage). Null for large blobs. */
        private String content;

        /** Canonical back-link URL. */
        private String sourceUri;

        /** Human or system author. */
        private String author;

        /** Kind-specific extra fields. */
        private Map<String, Object> metadata;
    }
}
