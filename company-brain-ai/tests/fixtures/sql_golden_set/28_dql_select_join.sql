-- Find all nodes with their source artifacts
SELECT
    n.id,
    n.node_type,
    n.name,
    n.external_id,
    a.kind        AS artifact_kind,
    a.external_id AS artifact_external_id,
    al.link_role
FROM nodes n
JOIN artifact_links al ON al.node_id = n.id
JOIN artifacts a       ON a.id = al.artifact_id
WHERE n.workspace_id = '00000000-0000-0000-0000-000000000001'
  AND al.link_role = 'derived_from'
ORDER BY n.node_type, n.name;
