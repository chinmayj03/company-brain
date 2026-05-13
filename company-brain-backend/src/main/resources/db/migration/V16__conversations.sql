-- ADR-0072 A1 (Conversation History) + A2 (Saved Queries) + A5 (Audit Log)
-- Persists every /query call so the UI can render History, Saved, and Audit tabs.

CREATE TABLE conversations (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  question     TEXT NOT NULL,
  answer_md    TEXT,
  summary_json JSONB,
  asked_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  saved        BOOLEAN NOT NULL DEFAULT false,
  title        TEXT,
  actor_id     TEXT,
  actor_kind   TEXT DEFAULT 'user'
);

-- History tab: last 50 queries per workspace ordered by recency
CREATE INDEX ON conversations(workspace_id, asked_at DESC);

-- Saved tab: fast filter for saved=true rows (partial index keeps it small)
CREATE INDEX ON conversations(workspace_id, saved) WHERE saved = true;
