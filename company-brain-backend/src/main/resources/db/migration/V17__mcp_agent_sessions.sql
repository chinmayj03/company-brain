CREATE TABLE IF NOT EXISTS mcp_agent_sessions (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id    UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  agent_name      TEXT NOT NULL,
  client_id       TEXT NOT NULL,
  connected_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_ping_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  disconnected_at TIMESTAMPTZ,
  query_count     INT NOT NULL DEFAULT 0
);
CREATE INDEX ON mcp_agent_sessions(workspace_id, disconnected_at NULLS FIRST);
