package com.companybrain.model;

import jakarta.persistence.*;
import lombok.*;

import java.io.Serializable;
import java.time.OffsetDateTime;
import java.util.UUID;

/**
 * Provenance edge: records that a graph node was derived from (or cited) an Artifact.
 *
 * Every node MUST have at least one ArtifactLink with link_role = 'derived_from'.
 * This invariant is enforced in PipelineService.applyResult().
 *
 * Link roles:
 *   derived_from      — the node was extracted/synthesised from this artifact
 *   cited_in_context  — the artifact was included in the LLM context prompt
 *   invalidates       — (reserved) explicit human override of a derived fact
 *
 * See ADR-005: Artifact-Centric Knowledge Pipeline.
 */
@Entity
@Table(name = "artifact_links")
@Data
@Builder
@NoArgsConstructor
@AllArgsConstructor
@IdClass(ArtifactLink.PK.class)
public class ArtifactLink {

    @Id
    @Column(name = "artifact_id", nullable = false)
    private UUID artifactId;

    @Id
    @Column(name = "node_id", nullable = false)
    private UUID nodeId;

    @Id
    @Column(name = "link_role", nullable = false)
    private String linkRole;

    @Column(name = "workspace_id", nullable = false)
    private UUID workspaceId;

    @Column(name = "confidence", nullable = false, columnDefinition = "NUMERIC(3,2)")
    private java.math.BigDecimal confidence;

    @Column(name = "created_at", nullable = false)
    private OffsetDateTime createdAt;

    // ── Composite PK class ────────────────────────────────────────────────────

    @Data
    @NoArgsConstructor
    @AllArgsConstructor
    public static class PK implements Serializable {
        private UUID artifactId;
        private UUID nodeId;
        private String linkRole;
    }
}
