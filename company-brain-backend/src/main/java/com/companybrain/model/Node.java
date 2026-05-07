package com.companybrain.model;

import jakarta.persistence.*;
import lombok.*;
import org.hibernate.annotations.JdbcTypeCode;
import org.hibernate.type.SqlTypes;

import java.time.OffsetDateTime;
import java.util.*;

@Entity
@Table(name = "nodes")
@Getter @Setter @NoArgsConstructor @AllArgsConstructor @Builder
public class Node {

    @Id
    @GeneratedValue(strategy = GenerationType.UUID)
    private UUID id;

    @Column(name = "workspace_id", nullable = false)
    private UUID workspaceId;

    /**
     * Type discriminator. One of:
     * Service | ApiEndpoint | SchemaField | DatabaseTable | DatabaseColumn |
     * CodeFunction | FrontendComponent | ExternalService | Team
     */
    @Column(name = "node_type", nullable = false)
    private String nodeType;

    /**
     * Stable identifier from the source system.
     * e.g. "backend/src/services/payment.service.ts::chargePayment"
     * Unique within (workspace_id, node_type).
     */
    @Column(name = "external_id", nullable = false)
    private String externalId;

    @Column(nullable = false)
    private String name;

    /**
     * Type-specific metadata stored as JSONB.
     * Example for CodeFunction: {signature, file_path, repo, first_appeared_commit}
     * Encrypted at rest for enterprise workspaces (handled at service layer).
     */
    @JdbcTypeCode(SqlTypes.JSON)
    @Column(columnDefinition = "jsonb")
    private Map<String, Object> metadata;

    @Column(name = "created_at", nullable = false, updatable = false)
    private OffsetDateTime createdAt;

    @Column(name = "updated_at", nullable = false)
    private OffsetDateTime updatedAt;

    @OneToMany(mappedBy = "source", fetch = FetchType.LAZY)
    @Builder.Default
    private List<Edge> outboundEdges = new ArrayList<>();

    @OneToMany(mappedBy = "target", fetch = FetchType.LAZY)
    @Builder.Default
    private List<Edge> inboundEdges = new ArrayList<>();

    @OneToMany(mappedBy = "node", fetch = FetchType.LAZY)
    @Builder.Default
    private List<NodeContext> contextEntries = new ArrayList<>();

    @PrePersist
    protected void onCreate() {
        createdAt = OffsetDateTime.now();
        updatedAt = OffsetDateTime.now();
    }

    @PreUpdate
    protected void onUpdate() {
        updatedAt = OffsetDateTime.now();
    }
}
