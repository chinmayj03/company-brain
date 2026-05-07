-- ============================================================
-- V7: Promote assumption + business_context to first-class nodes
-- See ADR-0017.
--
-- Note: The ADR numbers this V4, but V4–V6 already exist in
-- this project, so this is numbered V7 to avoid conflicts.
--
-- V6 already added 'assumption' and 'business_context' to the
-- entity_type CHECK constraint on the nodes table, so new rows
-- for these entity types are already accepted.
-- ============================================================

-- 1. Migrate existing node_context rows of types 'invariant' and 'business_context'
--    into the nodes table as first-class entities.
WITH source AS (
  SELECT
    nc.id            AS old_ctx_id,
    nc.workspace_id,
    n.urn            AS parent_urn,
    n.repo           AS parent_repo,
    nc.context_type,
    nc.title         AS qualified_name,
    encode(nc.body, 'escape') AS body_text,
    nc.confidence,
    nc.metadata,
    nc.created_at
  FROM node_context nc
  JOIN nodes n ON n.id = nc.node_id
  WHERE nc.context_type IN ('invariant', 'business_context', 'llm_synthesis')
)
INSERT INTO nodes (
    id, workspace_id, node_type, entity_type, external_id, urn, name, metadata
)
SELECT
    gen_random_uuid(),
    s.workspace_id,
    CASE WHEN s.context_type = 'invariant' THEN 'Assumption'
         ELSE 'BusinessContext' END        AS node_type,
    CASE WHEN s.context_type = 'invariant' THEN 'assumption'
         ELSE 'business_context' END       AS entity_type,
    -- legacy external_id placeholder; URN is the canonical key
    'legacy_' || s.old_ctx_id::text        AS external_id,
    -- New URN: derive from parent's URN tenant + entity_type + slugged title
    'urn:cb:' ||
      split_part(s.parent_urn, ':', 3) || ':' ||   -- tenant segment
      'code:' ||
      s.parent_repo || ':' ||
      CASE WHEN s.context_type = 'invariant' THEN 'assumption'
           ELSE 'business_context' END || ':' ||
      regexp_replace(
        coalesce(s.qualified_name, 'untitled-' || s.old_ctx_id::text),
        '[^A-Za-z0-9._-]+', '_', 'g'
      )                                    AS urn,
    coalesce(s.qualified_name, 'untitled') AS name,
    jsonb_build_object(
      'body',                  s.body_text,
      'confidence',            s.confidence,
      'origin',                'migrated_from_node_context',
      'old_node_context_id',   s.old_ctx_id::text,
      'created_at',            s.created_at
    ) || coalesce(s.metadata, '{}'::jsonb)
FROM source s
ON CONFLICT (workspace_id, node_type, external_id) DO NOTHING;

-- 2. Document the new edge types allowed on the edges table.
--    The edges.edge_type column is TEXT with no check constraint;
--    the allowed values are documented here and enforced in application code.
--    New types added by ADR-0017:
--      RELIES_ON  — entity → assumption  (entity depends on the invariant holding)
--      EXPLAINS   — business_context → entity  (provides rationale / context)
COMMENT ON COLUMN edges.edge_type IS
  'CALLS | EXPOSES | CONSUMES_FIELD | READS_TABLE | WRITES_COLUMN | OWNS | IMPORTS '
  '| RENDERS_FIELD | CALLS_ENDPOINT | VALIDATES | DEPENDS_ON | RELIES_ON | EXPLAINS';

-- 3. Existing node_context rows are NOT deleted — kept for rollback safety.
--    A follow-up ADR will drop the invariant/business_context context_types from
--    node_context once the new node-based system is stable.
