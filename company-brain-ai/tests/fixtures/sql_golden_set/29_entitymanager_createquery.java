package com.example.service;

import jakarta.persistence.EntityManager;
import jakarta.persistence.PersistenceContext;
import jakarta.persistence.TypedQuery;
import java.util.List;
import java.util.UUID;

public class NodeQueryService {

    @PersistenceContext
    private EntityManager entityManager;

    public List<Node> findReachableNodes(UUID workspaceId, UUID startNodeId) {
        TypedQuery<Node> query = entityManager.createQuery(
            "SELECT n FROM Node n " +
            "WHERE n.workspaceId = :wid " +
            "AND EXISTS (SELECT e FROM Edge e WHERE e.sourceId = :startId AND e.targetId = n.id)",
            Node.class
        );
        query.setParameter("wid", workspaceId);
        query.setParameter("startId", startNodeId);
        return query.getResultList();
    }

    public List<Object[]> countNodesByType(UUID workspaceId) {
        return entityManager.createNativeQuery(
            "SELECT node_type, COUNT(*) FROM nodes WHERE workspace_id = ? GROUP BY node_type ORDER BY 2 DESC"
        )
        .setParameter(1, workspaceId)
        .getResultList();
    }
}
