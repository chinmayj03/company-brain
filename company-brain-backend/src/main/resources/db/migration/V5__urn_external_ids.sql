-- ============================================================
-- V5: Migrate Postgres external_id to canonical URN format
-- See ADR-0013.
--
-- Note: The ADR numbers this V2, but V2–V4 already exist in
-- this project, so this is numbered V5 to avoid conflicts.
-- ============================================================

-- 1. Add a new column for the URN; keep old external_id during the transition.
ALTER TABLE nodes ADD COLUMN urn TEXT;

-- 2. Backfill — every existing node gets a URN derived from its current
--    external_id. Workspace slug comes from workspaces.slug.
--    The qualified_name is the last segment after '::' (or the whole string
--    if '::' is absent). Special characters are percent-encoded to keep the
--    URN segment-safe.
UPDATE nodes n
SET urn = 'urn:cb:' || w.slug || ':code:monorepo:'
          || CASE n.node_type
               WHEN 'ApiEndpoint'       THEN 'api_contract'
               WHEN 'FrontendComponent' THEN 'component'
               WHEN 'SchemaField'       THEN 'data_model'
               WHEN 'DatabaseTable'     THEN 'data_model'
               WHEN 'DatabaseColumn'    THEN 'data_model'
               WHEN 'DatabaseQuery'     THEN 'data_model'
               WHEN 'SharedType'        THEN 'data_model'
               ELSE 'component'
             END
          || ':'
          || replace(replace(replace(
               CASE
                 WHEN position('::' IN n.external_id) > 0
                   THEN split_part(
                          n.external_id, '::',
                          array_length(string_to_array(n.external_id, '::'), 1)
                        )
                 ELSE n.external_id
               END,
             '%', '%25'),
             '/', '%2F'),
             ' ', '%20')
FROM workspaces w
WHERE n.workspace_id = w.id;

-- 3. Constraint: every URN must follow the canonical format.
--    Pattern: urn:cb:<tenant>:<domain>:<repo>:<entity_type>:<qname>
ALTER TABLE nodes ADD CONSTRAINT chk_urn_format
  CHECK (urn ~ '^urn:cb:[a-z0-9_-]+:[a-z]+:[a-zA-Z0-9_-]+:[a-z_]+:.+$');

-- 4. Make URN unique per workspace (prevents duplicate entities in the same
--    workspace from sharing a URN).
CREATE UNIQUE INDEX uq_nodes_urn ON nodes (workspace_id, urn);

-- 5. NOT NULL after backfill is complete.
ALTER TABLE nodes ALTER COLUMN urn SET NOT NULL;

-- 6. The old external_id column is retained for one release for rollback
--    safety. New code reads from urn. Remove external_id in V7+ after a
--    stable release.

-- ============================================================
-- Edges reference nodes by UUID FK (edges.source_id / edges.target_id).
-- The URN lives on the node side, so no change is needed to the edges table.
-- ============================================================
