-- Create core tables for the workspace management schema

CREATE TABLE workspaces (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name        text NOT NULL,
    slug        text NOT NULL UNIQUE,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE workspace_sources (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id    uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    kind            text NOT NULL,
    display_name    text NOT NULL,
    sync_status     text NOT NULL DEFAULT 'pending',
    entity_count    int  NOT NULL DEFAULT 0,
    last_synced_at  timestamptz,
    error_message   text,
    created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX idx_workspace_sources_workspace_id ON workspace_sources (workspace_id);
