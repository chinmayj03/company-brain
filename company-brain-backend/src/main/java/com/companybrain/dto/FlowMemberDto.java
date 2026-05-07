package com.companybrain.dto;

import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.math.BigDecimal;
import java.util.UUID;

/** A single node in a flow's ordered member sequence. */
@Data
@Builder
@NoArgsConstructor
@AllArgsConstructor
public class FlowMemberDto {

    private UUID       nodeId;
    private String     name;
    private String     nodeType;
    private String     qualifiedName;
    private int        position;
    private BigDecimal riskScore;
}
