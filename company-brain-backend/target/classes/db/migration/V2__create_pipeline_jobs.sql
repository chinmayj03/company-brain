-- Pipeline jobs table
-- Tracks the lifecycle of each AI pipeline run.
-- The Java backend owns this table; the AI service writes results via
-- POST /v1/internal/pipeline-result rather than hitting Postgres directly.

CREATE TABLE pipeline_jobs (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id  UUID NOT NULL REFERENCES workspaces(id),

    -- Input
    endpoint_path TEXT        NOT NULL,
    http_method   TEXT        NOT NULL DEFAULT 'GET',

    -- Status: queued | running | completed | failed
    status        TEXT        NOT NULL DEFAULT 'queued',
    error_message TEXT,

    -- Result summary (populated when status = completed)
    entity_count      INT  DEFAULT 0,
    edge_count        INT  DEFAULT 0,
    gap_count         INT  DEFAULT 0,
    code_units_found  INT  DEFAULT 0,
    git_commits_found INT  DEFAULT 0,

    -- Rich detail for the UI
    files_traced      JSONB DEFAULT '[]'::jsonb,
    stages_summary    JSONB DEFAULT '[]'::jsonb,
    progress_logs     JSONB DEFAULT '[]'::jsonb,

    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at  TIMESTAMPTZ,
    completed_at TIMESTAMPTZ
);

CREATE INDEX idx_pipeline_jobs_workspace ON pipeline_jobs(workspace_id);
CREATE INDEX idx_pipeline_jobs_status    ON pipeline_jobs(status);

-- Row-level security — jobs are scoped to their workspace
ALTER TABLE pipeline_jobs ENABLE ROW LEVEL SECURITY;

CREATE POLICY pipeline_jobs_workspace_isolation ON pipeline_jobs
    USING (workspace_id::text = current_setting('app.workspace_id', true));
