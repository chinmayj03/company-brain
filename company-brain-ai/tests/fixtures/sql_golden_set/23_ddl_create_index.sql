-- Indexes for the nodes table
CREATE UNIQUE INDEX idx_nodes_workspace_external ON nodes (workspace_id, external_id);
CREATE INDEX idx_nodes_workspace_type ON nodes (workspace_id, node_type);
CREATE INDEX idx_nodes_name_lower ON nodes (workspace_id, lower(name));
CREATE UNIQUE INDEX idx_edges_workspace_src_tgt_type
    ON edges (workspace_id, source_id, target_id, edge_type)
    WHERE is_pruned = false;
