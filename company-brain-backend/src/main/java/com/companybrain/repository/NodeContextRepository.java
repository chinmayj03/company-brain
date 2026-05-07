package com.companybrain.repository;

import com.companybrain.model.NodeContext;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.repository.JpaRepository;

import java.util.List;
import java.util.UUID;

public interface NodeContextRepository extends JpaRepository<NodeContext, UUID> {

    List<NodeContext> findByWorkspaceIdAndNode_IdOrderByOccurredAtDesc(UUID workspaceId, UUID nodeId, Pageable pageable);

    long countByWorkspaceIdAndNode_Id(UUID workspaceId, UUID nodeId);

    /**
     * Batch fetch all context entries for a set of nodes in one query.
     * Used by ContextAssemblerService to avoid N+1 queries during graph traversal.
     * Results are ordered confidence-descending so highest-signal entries come first
     * when we truncate to the token budget.
     */
    @org.springframework.data.jpa.repository.Query("""
            SELECT nc FROM NodeContext nc
            WHERE nc.workspaceId = :wid
              AND nc.node.id IN :nodeIds
            ORDER BY CASE nc.confidence
                WHEN 'high'   THEN 1
                WHEN 'medium' THEN 2
                ELSE               3
            END, nc.occurredAt DESC
            """)
    List<NodeContext> findByWorkspaceIdAndNodeIdIn(
            @org.springframework.data.repository.query.Param("wid") UUID workspaceId,
            @org.springframework.data.repository.query.Param("nodeIds") java.util.Collection<UUID> nodeIds);
}
