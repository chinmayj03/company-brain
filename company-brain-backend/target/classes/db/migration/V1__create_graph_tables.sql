-- ============================================================
-- V1: Core dependency graph schema
-- See SYSTEM_DESIGN.md Section 4 for full rationale
-- ============================================================

-- Workspaces (one per company / tenant)
CREATE TABLE workspaces (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name        TEXT NOT NULL,
    slug        TEXT UNIQUE NOT NULL,
    kms_key_id  TEXT,                        -- AWS KMS key ARN for BYOK (nullable = platform-managed)
    plan        TEXT NOT NULL DEFAULT 'free', -- 'free' | 'team' | 'enterprise'
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Every entity in the dependency graph
CREATE TABLE nodes (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    node_type   TEXT NOT NULL,    -- 'Service' | 'ApiEndpoint' | 'SchemaField' | 'DatabaseTable' |
                                  -- 'DatabaseColumn' | 'CodeFunction' | 'FrontendComponent' |
                                  -- 'ExternalService' | 'Team'
    external_id TEXT NOT NULL,    -- Stable identifier from source (e.g. "backend/src/payment.ts::chargePayment")
    name        TEXT NOT NULL,
    metadata    JSONB,            -- Type-specific fields; encrypted for sensitive workspaces
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_node_identity UNIQUE (workspace_id, node_type, external_id)
);

-- Every dependency/relationship between nodes
CREATE TABLE edges (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    edge_type    TEXT NOT NULL,    -- 'CALLS' | 'EXPOSES' | 'CONSUMES_FIELD' | 'READS_TABLE' |
                                   -- 'WRITES_COLUMN' | 'OWNS' | 'IMPORTS' | 'RENDERS_FIELD' |
                                   -- 'CALLS_ENDPOINT' | 'VALIDATES' | 'DEPENDS_ON'
    source_id    UUID NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
    target_id    UUID NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
    confidence   FLOAT NOT NULL DEFAULT 1.0 CHECK (confidence >= 0 AND confidence <= 1),
    source       TEXT NOT NULL,    -- 'opentelemetry' | 'openapi' | 'git' | 'llm_extraction' |
                                   -- 'static_analysis' | 'iac' | 'ci'
    metadata     JSONB,            -- call_frequency, evidence_snippet, method, etc.
    first_seen   TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen    TIMESTAMPTZ NOT NULL DEFAULT now(),
    is_pruned    BOOLEAN NOT NULL DEFAULT false,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_edge_identity UNIQUE (workspace_id, edge_type, source_id, target_id, source)
);

-- Immutable event log: every change to the graph is recorded here
-- Enables time-travel queries and audit trail
CREATE TABLE edge_events (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id UUID NOT NULL,
    event_type   TEXT NOT NULL,   -- 'upserted' | 'pruned' | 'confidence_updated' | 'restored'
    edge_id      UUID NOT NULL,
    source_event_id TEXT,         -- Idempotency key from the ingestion agent
    payload      JSONB NOT NULL,
    occurred_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Rich context attached to nodes: git history, PR descriptions, ticket summaries, user annotations
CREATE TABLE node_context (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    node_id      UUID NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
    context_type TEXT NOT NULL,   -- 'git_commit' | 'pull_request' | 'ticket' |
                                  -- 'user_annotation' | 'llm_synthesis' | 'invariant' | 'risk_flag'
    title        TEXT,
    body         BYTEA,           -- AES-256-GCM encrypted; plaintext for non-sensitive workspaces
    author       TEXT,
    source_url   TEXT,
    source_id    TEXT,            -- External ID in source system (commit hash, ticket ID, etc.)
    annotation_type TEXT,         -- For user_annotation: 'business_context' | 'invariant' | 'risk_flag'
    applies_to_fields TEXT[],     -- Optional: specific schema fields this context covers
    confidence   TEXT,            -- 'high' (user annotated) | 'medium' (PR text) | 'low' (inferred)
    occurred_at  TIMESTAMPTZ,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    metadata     JSONB
);

-- ============================================================
-- Indexes — critical for traversal performance
-- ============================================================

-- Node lookups by external identifier (used by agent upserts)
CREATE INDEX idx_nodes_external_id ON nodes (workspace_id, node_type, external_id);
CREATE INDEX idx_nodes_name        ON nodes (workspace_id, name);
CREATE INDEX idx_nodes_type        ON nodes (workspace_id, node_type);

-- Traversal indexes: forward and reverse edge lookups
CREATE INDEX idx_edges_source      ON edges (workspace_id, source_id, edge_type) WHERE NOT is_pruned;
CREATE INDEX idx_edges_target      ON edges (workspace_id, target_id, edge_type) WHERE NOT is_pruned;
CREATE INDEX idx_edges_type        ON edges (workspace_id, edge_type) WHERE NOT is_pruned;
CREATE INDEX idx_edges_last_seen   ON edges (workspace_id, last_seen);
CREATE INDEX idx_edges_confidence  ON edges (workspace_id, confidence DESC) WHERE NOT is_pruned;

-- Context lookups
CREATE INDEX idx_node_context_node ON node_context (workspace_id, node_id, context_type);
CREATE INDEX idx_node_context_src  ON node_context (workspace_id, source_id);

-- Event log
CREATE INDEX idx_edge_events_edge  ON edge_events (edge_id, occurred_at DESC);
CREATE INDEX idx_edge_events_ws    ON edge_events (workspace_id, occurred_at DESC);

-- ============================================================
-- Row Level Security
-- ADR-003: all customer data is scoped to workspace_id via RLS
-- Application sets: SET LOCAL app.workspace_id = '<uuid>'
-- ============================================================

ALTER TABLE nodes        ENABLE ROW LEVEL SECURITY;
ALTER TABLE edges        ENABLE ROW LEVEL SECURITY;
ALTER TABLE node_context ENABLE ROW LEVEL SECURITY;

CREATE POLICY workspace_isolation ON nodes
    USING (workspace_id = current_setting('app.workspace_id', true)::UUID);

CREATE POLICY workspace_isolation ON edges
    USING (workspace_id = current_setting('app.workspace_id', true)::UUID);

CREATE POLICY workspace_isolation ON node_context
    USING (workspace_id = current_setting('app.workspace_id', true)::UUID);

-- Platform admin role bypasses RLS for analytics queries
CREATE ROLE platform_admin;
ALTER TABLE nodes        FORCE ROW LEVEL SECURITY;
ALTER TABLE edges        FORCE ROW LEVEL SECURITY;
ALTER TABLE node_context FORCE ROW LEVEL SECURITY;

-- ============================================================
-- Seed data: default workspace for development
-- ============================================================

INSERT INTO workspaces (id, name, slug, plan)
VALUES ('00000000-0000-0000-0000-000000000001', 'Development', 'dev', 'team');
