package com.companybrain.model;

import jakarta.persistence.*;
import lombok.*;
import org.hibernate.annotations.JdbcTypeCode;
import org.hibernate.type.SqlTypes;

import java.time.OffsetDateTime;
import java.util.Map;
import java.util.UUID;

@Entity
@Table(name = "edges")
@Getter @Setter @NoArgsConstructor @AllArgsConstructor @Builder
public class Edge {

    @Id
    @GeneratedValue(strategy = GenerationType.UUID)
    private UUID id;

    @Column(name = "workspace_id", nullable = false)
    private UUID workspaceId;

    /**
     * One of: CALLS | EXPOSES | CONSUMES_FIELD | READS_TABLE | WRITES_COLUMN |
     * OWNS | IMPORTS | RENDERS_FIELD | CALLS_ENDPOINT | VALIDATES | DEPENDS_ON
     */
    @Column(name = "edge_type", nullable = false)
    private String edgeType;

    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "source_id", nullable = false)
    private Node source;

    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "target_id", nullable = false)
    private Node target;

    /**
     * Confidence score [0.0, 1.0].
     * 1.0 = observed directly (runtime trace, OpenAPI spec)
     * ~0.7 = inferred by LLM with evidence
     * ~0.5 = inferred by static analysis (may not execute at runtime)
     */
    @Column(nullable = false)
    @Builder.Default
    private Double confidence = 1.0;

    /**
     * Which system produced this edge.
     * One of: opentelemetry | openapi | git | llm_extraction | static_analysis | iac | ci
     *
     * Renamed from "source" to "observedSource" to avoid collision with the Node source association.
     */
    @Column(name = "source", nullable = false)
    private String observedSource;

    @JdbcTypeCode(SqlTypes.JSON)
    @Column(columnDefinition = "jsonb")
    private Map<String, Object> metadata;

    @Column(name = "first_seen", nullable = false)
    private OffsetDateTime firstSeen;

    @Column(name = "last_seen", nullable = false)
    private OffsetDateTime lastSeen;

    /**
     * Pruned edges are kept for history but excluded from live traversal queries.
     * Set by the EdgePrunerJob when last_seen exceeds the staleness threshold.
     */
    @Column(name = "is_pruned", nullable = false)
    @Builder.Default
    private Boolean isPruned = false;

    @Column(name = "created_at", nullable = false, updatable = false)
    private OffsetDateTime createdAt;

    @PrePersist
    protected void onCreate() {
        createdAt = OffsetDateTime.now();
        if (firstSeen == null) firstSeen = OffsetDateTime.now();
        if (lastSeen == null) lastSeen = OffsetDateTime.now();
    }
}
