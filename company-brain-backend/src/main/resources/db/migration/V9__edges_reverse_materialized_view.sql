-- ============================================================
-- V9: ADR-0042 E9 — Reverse-edge pre-computation
--
-- edges_reverse is a materialized view of the edges table with
-- source_id and target_id swapped.  This makes "who calls X"
-- queries (blast-radius upstream side) a single index seek instead
-- of a full graph walk.
--
-- Refresh policy: REFRESH MATERIALIZED VIEW CONCURRENTLY edges_reverse
-- is called by PipelineService after every Stage 5 commit.
-- ============================================================

CREATE MATERIALIZED VIEW IF NOT EXISTS edges_reverse AS
SELECT
    workspace_id,
    target_id   AS source_id,    -- upstream caller
    source_id   AS target_id,    -- downstream callee (the "X" in "who calls X")
    edge_type,
    confidence,
    source,
    last_seen
FROM edges
WHERE NOT is_pruned;

-- Unique index required for REFRESH CONCURRENTLY (no lock on reads)
CREATE UNIQUE INDEX IF NOT EXISTS idx_edges_reverse_unique
    ON edges_reverse (workspace_id, source_id, target_id, edge_type);

-- Traversal index: "give me all upstreams of node X in workspace W"
CREATE INDEX IF NOT EXISTS idx_edges_reverse_target
    ON edges_reverse (workspace_id, target_id, edge_type);

-- Edge-type-specific index for blast-radius queries filtered by edge type
CREATE INDEX IF NOT EXISTS idx_edges_reverse_type
    ON edges_reverse (workspace_id, edge_type);

-- ============================================================
-- Grant read access to the application role
-- ============================================================
GRANT SELECT ON edges_reverse TO PUBLIC;
