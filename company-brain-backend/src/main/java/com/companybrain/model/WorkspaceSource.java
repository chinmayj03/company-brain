package com.companybrain.model;

import jakarta.persistence.*;
import lombok.*;
import org.hibernate.annotations.JdbcTypeCode;
import org.hibernate.type.SqlTypes;

import java.time.OffsetDateTime;
import java.util.Map;
import java.util.UUID;

@Entity
@Table(name = "workspace_sources")
@Getter @Setter @NoArgsConstructor @Builder
@AllArgsConstructor
public class WorkspaceSource {

    @Id
    @GeneratedValue(strategy = GenerationType.UUID)
    private UUID id;

    @Column(name = "workspace_id", nullable = false)
    private UUID workspaceId;

    @Column(nullable = false)
    private String kind;

    @Column(name = "display_name", nullable = false)
    private String displayName;

    @Column
    private String url;

    @Column(name = "last_synced_at")
    private OffsetDateTime lastSyncedAt;

    /** pending | syncing | ok | error */
    @Column(name = "sync_status", nullable = false)
    @Builder.Default
    private String syncStatus = "pending";

    @Column(name = "error_message")
    private String errorMessage;

    @JdbcTypeCode(SqlTypes.JSON)
    @Column(columnDefinition = "jsonb")
    private Map<String, Object> meta;

    @JdbcTypeCode(SqlTypes.JSON)
    @Column(columnDefinition = "jsonb")
    @Builder.Default
    private Map<String, Object> config = Map.of();

    @Column(name = "entity_count")
    @Builder.Default
    private int entityCount = 0;
}
