package com.companybrain.model;

import jakarta.persistence.*;
import lombok.*;
import org.hibernate.annotations.JdbcTypeCode;
import org.hibernate.type.SqlTypes;

import java.time.OffsetDateTime;
import java.util.Map;
import java.util.UUID;

/**
 * A content-addressed unit of knowledge ingested by any Collector.
 *
 * Every input that flows through the extraction pipeline — source files,
 * PR descriptions, git commits, Jira tickets, Slack threads, annotations,
 * OpenAPI specs — is stored as an Artifact before the LLM ever sees it.
 *
 * The dedup key is (workspace_id, kind, external_id). If the content_hash
 * matches the stored hash, no change event is emitted. If it differs,
 * ArtifactWriterService updates the row and emits a 'changed' event into
 * artifact_change_events so the dirty-set engine can schedule re-extraction.
 *
 * See ADR-005: Artifact-Centric Knowledge Pipeline.
 */
@Entity
@Table(name = "artifacts")
@Data
@Builder
@NoArgsConstructor
@AllArgsConstructor
public class Artifact {

    @Id
    @Column(name = "id", updatable = false, nullable = false)
    private UUID id;

    @Column(name = "workspace_id", nullable = false)
    private UUID workspaceId;

    /**
     * Kind tag — collector-assigned.
     * Current values: source_file | pr | commit | annotation |
     *                 ticket | slack_thread | doc_page | spec | legacy
     */
    @Column(name = "kind", nullable = false)
    private String kind;

    /**
     * Stable, source-derived identifier within this workspace+kind.
     * source_file  → repo/relative/path/to/File.java
     * commit       → repoName::commitHash
     * pr           → repoName::prNumber
     * ticket       → system::ticketId  (e.g. "jira::CB-1234")
     * annotation   → workspaceId::nodeId::annotationType
     */
    @Column(name = "external_id", nullable = false)
    private String externalId;

    /**
     * SHA-256 over normalised (UTF-8 trimmed) content.
     * Used by ArtifactWriterService for change detection.
     */
    @Column(name = "content_hash", nullable = false)
    private String contentHash;

    /** Canonical link back to the origin system (browsable URL). */
    @Column(name = "source_uri")
    private String sourceUri;

    /** Inline content for small artifacts (<64 KB). */
    @Column(name = "content_inline", columnDefinition = "TEXT")
    private String contentInline;

    /** S3/GCS pointer for large blobs. Mutually exclusive with contentInline. */
    @Column(name = "content_ref")
    private String contentRef;

    @Column(name = "author")
    private String author;

    @Column(name = "fetched_at", nullable = false)
    private OffsetDateTime fetchedAt;

    /**
     * Previous hash before the most recent change.
     * Null on first ingest. Kept for cheap one-hop diff.
     */
    @Column(name = "last_seen_hash")
    private String lastSeenHash;

    /**
     * Kind-specific extra fields (e.g. PR number, ticket severity, language).
     */
    @JdbcTypeCode(SqlTypes.JSON)
    @Column(name = "metadata", columnDefinition = "jsonb")
    private Map<String, Object> metadata;
}
