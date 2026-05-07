package com.companybrain.dto;

import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.math.BigDecimal;
import java.util.List;
import java.util.UUID;

/**
 * Full detail for a single execution flow, including the ordered node sequence.
 */
@Data
@Builder
@NoArgsConstructor
@AllArgsConstructor
public class FlowDetailDto {

    private UUID             id;
    private String           name;
    private UUID             entryNodeId;
    private String           entryNodeName;
    private int              depth;
    private int              nodeCount;
    private int              fileCount;
    private BigDecimal       criticality;

    /** Nodes in BFS traversal order (position 0 = entry point). */
    private List<FlowMemberDto> members;
}
