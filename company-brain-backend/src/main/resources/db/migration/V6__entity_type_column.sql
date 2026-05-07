-- ============================================================
-- V6: Add constrained entity_type column derived from node_type.
-- See ADR-0013.
--
-- Note: The ADR numbers this V3, but V2–V4 already exist in
-- this project, so this is numbered V6 to avoid conflicts.
-- ============================================================

-- 1. Add the column (nullable initially so the UPDATE can run first).
ALTER TABLE nodes ADD COLUMN entity_type TEXT;

-- 2. Backfill from existing node_type values using the same taxonomy as V5.
UPDATE nodes SET entity_type = CASE node_type
  WHEN 'ApiEndpoint'       THEN 'api_contract'
  WHEN 'FrontendComponent' THEN 'component'
  WHEN 'SchemaField'       THEN 'data_model'
  WHEN 'DatabaseTable'     THEN 'data_model'
  WHEN 'DatabaseColumn'    THEN 'data_model'
  WHEN 'DatabaseQuery'     THEN 'data_model'
  WHEN 'SharedType'        THEN 'data_model'
  ELSE 'component'
END;

-- 3. Make NOT NULL and add the constraint for new inserts.
ALTER TABLE nodes
  ALTER COLUMN entity_type SET NOT NULL,
  ADD CONSTRAINT chk_entity_type
    CHECK (entity_type IN (
      'component', 'screen', 'api_contract', 'data_model',
      'assumption', 'business_context', 'function_node'
    ));

-- 4. Index for efficient per-workspace entity-type queries.
CREATE INDEX idx_nodes_entity_type ON nodes (workspace_id, entity_type);
