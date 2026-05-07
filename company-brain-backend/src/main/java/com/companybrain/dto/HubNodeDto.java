package com.companybrain.dto;

import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.math.BigDecimal;
import java.util.UUID;

/**
 * A hub or bridge node as returned by the Architecture endpoints.
 *
 * Hubs   — top-N nodes by normalised in+out degree (graph_metrics, metric_kind='hub_degree').
 * Bridges— top-N nodes by betweenness centrality   (graph_metrics, metric_kind='bridge_betweenness').
 */
@Data
@Builder
@NoArgsConstructor
@AllArgsConstructor
public class HubNodeDto {

    private UUID   nodeId;
    private String name;
    private String nodeType;
    private String qualifiedName;

    /** Normalised topology score (0.0–1.0). */
    private BigDecimal score;

    /** Rank within the workspace for this metric kind (1 = highest). */
    private Integer rank;

    /** Risk score from the structural layer (null until first scan). */
    private BigDecimal riskScore;
}
