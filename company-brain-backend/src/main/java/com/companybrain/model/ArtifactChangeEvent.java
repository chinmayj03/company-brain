package com.companybrain.model;

import jakarta.persistence.*;
import lombok.*;

import java.time.OffsetDateTime;
import java.util.UUID;

/**
 * Append-only dirty-set event.
 *
 * ArtifactWriterService emits one row here whenever an artifact is:
 *   created  — first ingest of this (workspace, kind, external_id) combination
 *   changed  — content_hash differs from the stored hash
 *   deleted  — artifact was removed from the source system
 *
 * DirtySetService scans for rows where consumed_at IS NULL, computes the
 * set of graph nodes that need re-extraction (via artifact_links + reverse
 * edge traversal), then marks events consumed.
 *
 * This table is append-only. Rows are never updated except to set consumed_at.
 *
 * See ADR-005: Artifact-Centric Knowledge Pipeline.
 */
@Entity
@Table(name = "artifact_change_events")
@Data
@Builder
@NoArgsConstructor
@AllArgsConstructor
public class ArtifactChangeEvent {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    @Column(name = "id", updatable = false, nullable = false)
    private Long id;

    @Column(name = "workspace_id", nullable = false)
    private UUID workspaceId;

    @Column(name = "artifact_id", nullable = false)
    private UUID artifactId;

    /** 'created' | 'changed' | 'deleted' */
    @Column(name = "event_kind", nullable = false)
    private String eventKind;

    /** Previous hash — null for 'created' events. */
    @Column(name = "old_hash")
    private String oldHash;

    /** New hash — null for 'deleted' events. */
    @Column(name = "new_hash")
    private String newHash;

    @Column(name = "occurred_at", nullable = false)
    private OffsetDateTime occurredAt;

    /**
     * Set by DirtySetService when this event has been folded into a pipeline run.
     * NULL = unconsumed (needs processing).
     */
    @Column(name = "consumed_at")
    private OffsetDateTime consumedAt;
}
