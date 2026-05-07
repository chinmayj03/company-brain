package com.companybrain.dto;

import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.util.List;
import java.util.UUID;

/**
 * Request body for POST /v1/internal/artifacts/check-freshness.
 *
 * The AI service sends this before running any LLM passes.
 * Each item represents one code unit (source_file artifact) it is about to process.
 * Java responds with which are fresh (hash unchanged, existing entities in graph)
 * and which are dirty (hash changed or not yet seen — LLM extraction required).
 *
 * See Task #24: Hash-based incremental pipeline processing.
 */
@Data
@Builder
@NoArgsConstructor
@AllArgsConstructor
public class ArtifactFreshnessRequest {

    /** Workspace scope for this check. */
    private UUID workspaceId;

    /** One entry per code unit the AI service is considering processing. */
    private List<ArtifactCheck> artifacts;

    @Data
    @Builder
    @NoArgsConstructor
    @AllArgsConstructor
    public static class ArtifactCheck {
        /** Artifact kind — always "source_file" for code units. */
        private String kind;

        /**
         * Stable source-derived identifier — e.g. "repo/src/main/java/Foo.java".
         * Matches Artifact.externalId in the DB.
         */
        private String externalId;

        /**
         * SHA-256 of the file's current content as computed by the AI service.
         * If this matches the stored content_hash, the artifact is fresh.
         */
        private String contentHash;
    }
}
