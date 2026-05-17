-- Clean up pruned edges older than 30 days
DELETE FROM edges
WHERE is_pruned = true
  AND updated_at < now() - interval '30 days';

-- Clean up orphaned artifact links
DELETE FROM artifact_links al
WHERE NOT EXISTS (
    SELECT 1 FROM nodes n WHERE n.id = al.node_id
);
