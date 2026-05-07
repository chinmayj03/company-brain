package com.companybrain.dto;

import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.time.OffsetDateTime;
import java.util.Map;
import java.util.UUID;

@Data
@Builder
@NoArgsConstructor
@AllArgsConstructor
public class NodeDto {

    private UUID id;
    private String nodeType;
    private String externalId;
    private String urn;          // canonical URN per ADR-0013; null during transition
    private String name;
    private Map<String, Object> metadata;
    private OffsetDateTime updatedAt;
}
