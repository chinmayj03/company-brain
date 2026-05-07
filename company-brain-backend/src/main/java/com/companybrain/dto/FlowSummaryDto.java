package com.companybrain.dto;

import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.math.BigDecimal;
import java.util.UUID;

/**
 * Summary of a single execution flow — used in the list endpoint.
 * Full node sequence is in FlowDetailDto (fetched on demand).
 */
@Data
@Builder
@NoArgsConstructor
@AllArgsConstructor
public class FlowSummaryDto {

    private UUID       id;
    private String     name;
    private UUID       entryNodeId;
    private String     entryNodeName;
    private int        depth;
    private int        nodeCount;
    private int        fileCount;
    private BigDecimal criticality;
}
