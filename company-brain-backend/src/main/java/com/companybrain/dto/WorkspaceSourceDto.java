package com.companybrain.dto;

import com.companybrain.model.WorkspaceSource;
import lombok.Builder;
import lombok.Data;

import java.time.OffsetDateTime;
import java.util.Map;
import java.util.UUID;

@Data
@Builder
public class WorkspaceSourceDto {

    private UUID id;
    private UUID workspaceId;
    private String kind;
    private String displayName;
    private String url;
    private OffsetDateTime lastSyncedAt;
    private String syncStatus;
    private String errorMessage;
    private Map<String, Object> meta;
    private Map<String, Object> config;
    private int entityCount;

    public static WorkspaceSourceDto from(WorkspaceSource s) {
        return WorkspaceSourceDto.builder()
                .id(s.getId())
                .workspaceId(s.getWorkspaceId())
                .kind(s.getKind())
                .displayName(s.getDisplayName())
                .url(s.getUrl())
                .lastSyncedAt(s.getLastSyncedAt())
                .syncStatus(s.getSyncStatus())
                .errorMessage(s.getErrorMessage())
                .meta(s.getMeta())
                .config(s.getConfig() != null ? s.getConfig() : Map.of())
                .entityCount(s.getEntityCount())
                .build();
    }
}
