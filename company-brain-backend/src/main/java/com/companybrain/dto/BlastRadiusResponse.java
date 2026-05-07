package com.companybrain.dto;

import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.util.List;
import java.util.UUID;

@Data
@Builder
@NoArgsConstructor
@AllArgsConstructor
public class BlastRadiusResponse {

    private UUID originNodeId;
    private UUID workspaceId;
    private List<BlastRadiusNode> affectedNodes;
    private Integer traversalDepth;
    private Long queryDurationMs;
}
