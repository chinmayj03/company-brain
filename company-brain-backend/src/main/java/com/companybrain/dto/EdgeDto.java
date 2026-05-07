package com.companybrain.dto;

import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.time.OffsetDateTime;
import java.util.UUID;

@Data
@Builder
@NoArgsConstructor
@AllArgsConstructor
public class EdgeDto {

    private UUID id;
    private String edgeType;
    private UUID sourceId;
    private String sourceName;
    private UUID targetId;
    private String targetName;
    private Double confidence;
    private String source;
    private OffsetDateTime lastSeen;
}
