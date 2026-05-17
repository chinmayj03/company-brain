-- Complex SELECT with CTEs and window functions
WITH workspace_stats AS (
    SELECT
        ws.id AS workspace_id,
        ws.name,
        COUNT(DISTINCT n.id)  AS node_count,
        COUNT(DISTINCT e.id)  AS edge_count
    FROM workspaces ws
    LEFT JOIN nodes n ON n.workspace_id = ws.id
    LEFT JOIN edges e ON e.workspace_id = ws.id AND e.is_pruned = false
    GROUP BY ws.id, ws.name
),
ranked AS (
    SELECT *,
           RANK() OVER (ORDER BY node_count DESC) AS rank_by_nodes
    FROM workspace_stats
)
SELECT workspace_id, name, node_count, edge_count, rank_by_nodes
FROM ranked
WHERE rank_by_nodes <= 10
ORDER BY rank_by_nodes;
