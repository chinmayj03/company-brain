package com.companybrain.model;

import jakarta.persistence.*;
import lombok.*;
import org.hibernate.annotations.JdbcTypeCode;
import org.hibernate.type.SqlTypes;

import java.time.OffsetDateTime;
import java.util.Map;
import java.util.UUID;

@Entity
@Table(name = "node_context")
@Getter @Setter @NoArgsConstructor @AllArgsConstructor @Builder
public class NodeContext {

    @Id
    @GeneratedValue(strategy = GenerationType.UUID)
    private UUID id;

    @Column(name = "workspace_id", nullable = false)
    private UUID workspaceId;

    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "node_id", nullable = false)
    private Node node;

    /**
     * One of: git_commit | pull_request | ticket | user_annotation |
     * llm_synthesis | invariant | risk_flag
     */
    @Column(name = "context_type", nullable = false)
    private String contextType;

    private String title;

    /**
     * Encrypted at rest (AES-256-GCM) for enterprise workspaces.
     * Decryption handled at service layer using workspace KMS key.
     * Stores: PR description, commit message, ticket summary, annotation text.
     */
    @Column(columnDefinition = "bytea")
    private byte[] body;

    private String author;

    @Column(name = "source_url")
    private String sourceUrl;

    /**
     * External identifier: commit hash, ticket ID, PR number, etc.
     */
    @Column(name = "source_id")
    private String sourceId;

    /**
     * For user_annotation type: 'business_context' | 'invariant' | 'risk_flag' | 'deprecation_note'
     */
    @Column(name = "annotation_type")
    private String annotationType;

    /**
     * Which schema fields this context entry specifically addresses.
     * e.g. ["charge.amount", "charge.currency"]
     */
    @Column(name = "applies_to_fields", columnDefinition = "text[]")
    private String[] appliesToFields;

    /**
     * Signal quality: 'high' (user annotated) | 'medium' (PR text) | 'low' (inferred by LLM)
     */
    private String confidence;

    @Column(name = "occurred_at")
    private OffsetDateTime occurredAt;

    @Column(name = "created_at", nullable = false, updatable = false)
    private OffsetDateTime createdAt;

    @JdbcTypeCode(SqlTypes.JSON)
    @Column(columnDefinition = "jsonb")
    private Map<String, Object> metadata;

    @PrePersist
    protected void onCreate() {
        createdAt = OffsetDateTime.now();
    }
}
