package com.companybrain.dto;

import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.util.UUID;

/**
 * Request body for POST /v1/internal/assemble-context.
 *
 * Called by the Python AI service before the LLM Ask call.
 * The Python service supplies:
 *   - workspaceId: tenant scope
 *   - focalNodeId or focalExternalId: the node the question is about
 *   - question: the user's question (used for keyword-aware ranking in future)
 *   - maxHops: traversal depth (1–5)
 *   - tokenBudget: max tokens for assembled context (default 4096)
 *
 * See ADR-004: Tiered Memory & Context Assembly.
 */
@Data
@Builder
@NoArgsConstructor
@AllArgsConstructor
public class ContextAssemblyRequest {

    private UUID workspaceId;

    /**
     * The node to centre the context assembly on.
     * Provide EITHER focalNodeId (DB UUID) OR focalExternalId (e.g. "repo/Foo.java::chargePayment").
     * focalNodeId takes priority if both are provided.
     */
    private UUID focalNodeId;

    /**
     * Alternative: locate the focal node by external_id.
     * Used when the Python service knows the code symbol but not the DB UUID.
     */
    private String focalExternalId;

    /**
     * The user's question — used for logging and future relevance-based ranking.
     * Not required for assembly; may be null.
     */
    private String question;

    /**
     * BFS traversal depth from the focal node.
     * Clamped to [1, 5] by the service.
     * Default: 3.
     */
    @Builder.Default
    private int maxHops = 3;

    /**
     * Maximum token budget for the assembled context block.
     * Service will trim nodes to stay within this budget.
     * Default: 4096 (leaves room for system prompt + user question in an 8k context window).
     */
    @Builder.Default
    private int tokenBudget = 4096;
}
