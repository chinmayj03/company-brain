package com.companybrain.dto;

import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.util.List;
import java.util.Map;

/**
 * Response body for POST /v1/internal/artifacts/check-freshness.
 *
 * Each ArtifactStatus tells the AI service whether a given code unit is:
 *   - fresh: content hash unchanged, DB has existing nodes derived from it
 *            → the AI service can skip LLM extraction and reuse existingEntities
 *   - dirty: hash changed, never seen, or no nodes derived from it
 *            → the AI service MUST run LLM extraction
 *
 * existingEntities is only populated for fresh artifacts so the orchestrator can
 * reconstruct ExtractedEntity objects without hitting the LLM.
 *
 * See Task #24: Hash-based incremental pipeline processing.
 */
@Data
@Builder
@NoArgsConstructor
@AllArgsConstructor
public class ArtifactFreshnessResponse {

    private List<ArtifactStatus> results;

    @Data
    @Builder
    @NoArgsConstructor
    @AllArgsConstructor
    public static class ArtifactStatus {

        /** Mirrors the externalId from the request item. */
        private String externalId;

        /**
         * true  → content hash matches stored hash AND the graph has nodes
         *         derived from this artifact. AI service may skip extraction.
         * false → extraction is required (new file, changed file, or no nodes yet).
         */
        private boolean fresh;

        /**
         * Existing graph nodes derived from this artifact (populated only when fresh=true).
         * The AI service uses these to reconstruct ExtractedEntity objects so relationship
         * extraction and context synthesis can reference them.
         */
        private List<ExistingEntityDto> existingEntities;
    }

    /**
     * Minimal projection of a graph Node sufficient to reconstruct an ExtractedEntity
     * in the Python AI service.
     */
    @Data
    @Builder
    @NoArgsConstructor
    @AllArgsConstructor
    public static class ExistingEntityDto {
        private String nodeType;
        private String name;
        private String externalId;
        /** Full metadata map — includes signature, file, repo, confidence, etc. */
        private Map<String, Object> metadata;
    }
}
