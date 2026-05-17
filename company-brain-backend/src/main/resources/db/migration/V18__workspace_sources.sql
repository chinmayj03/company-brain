CREATE TABLE IF NOT EXISTS workspace_sources (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id   UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  kind           TEXT NOT NULL,
  display_name   TEXT NOT NULL,
  url            TEXT,
  last_synced_at TIMESTAMPTZ,
  sync_status    TEXT NOT NULL DEFAULT 'pending',
  error_message  TEXT,
  meta           JSONB
);
CREATE INDEX ON workspace_sources(workspace_id);
