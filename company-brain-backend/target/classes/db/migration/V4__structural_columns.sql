-- ============================================================
-- V4: Structural Layer — ADR-006 (CRG structural substrate)
--
-- Adds columns to the existing `nodes` table for tree-sitter-derived
-- structural metadata, and creates five new tables for flows,
-- communities, and topology metrics.
--
-- Week 1 note: Only the `nodes` column additions and `flows`/
-- `flow_memberships`/`communities`/`node_communities`/`graph_metrics`
-- schema are shipped this week. Actual data is written by the new
-- structural parser and risk scorer (companybrain/structural/).
-- The flow, community and topology tables are schema-only this week;
-- data population happens in weeks 2–4.
--
-- Design rationale: docs/ADR-006-adopt-crg-structural-and-mcp-layer.md
-- Algorithm source: tirth8205/code-review-graph (MIT License)
-- ============================================================


-- ── Structural columns on nodes ──────────────────────────────────────────────

-- Qualified name: canonical, parser-derived primary key for structural identity.
-- Format: path/to/file.py::ClassName.method_name  (mirrors CRG's qualified_name scheme)
-- Non-code nodes (Policy, Ticket, Team) leave this null.
ALTER TABLE nodes ADD COLUMN IF NOT EXISTS qualified_name  TEXT;

-- SHA-256 of the source file bytes at parse time.
-- Used by the hash-diff incremental engine (week 2) to skip unchanged files.
ALTER TABLE nodes ADD COLUMN IF NOT EXISTS file_hash       TEXT;

-- Source location — populated by tree-sitter, null for non-code nodes.
ALTER TABLE nodes ADD COLUMN IF NOT EXISTS line_start      INTEGER;
ALTER TABLE nodes ADD COLUMN IF NOT EXISTS line_end        INTEGER;

-- Multi-factor risk score: 0.0 (low risk) – 1.0 (high risk).
-- Computed by companybrain/structural/risk.py; null until first structural scan.
ALTER TABLE nodes ADD COLUMN IF NOT EXISTS risk_score      NUMERIC(3,2)
    CHECK (risk_score IS NULL OR (risk_score >= 0 AND risk_score <= 1));

-- Per-factor breakdown stored as JSONB so the frontend can render the explainer.
-- Shape: {"tests": 0.30, "callers": 0.08, "security": 0.20, "flow": 0.15, "community": 0.05}
ALTER TABLE nodes ADD COLUMN IF NOT EXISTS risk_factors    JSONB;


-- ── Index on qualified_name ───────────────────────────────────────────────────
-- Partial index: only rows that have been structurally parsed.
-- Used by the MCP structural tools and equivalence tests.
CREATE INDEX IF NOT EXISTS idx_nodes_qualified
    ON nodes (workspace_id, qualified_name)
    WHERE qualified_name IS NOT NULL;


-- ── flows ────────────────────────────────────────────────────────────────────
-- Named execution paths traced from framework entry-points via BFS over CALLS edges.
-- Ported from CRG's flows.py (detect_entry_points + trace_flows + compute_criticality).
-- Schema-only in week 1; populated by companybrain/structural/flows.py in week 4.

CREATE TABLE IF NOT EXISTS flows (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id    UUID        NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,

    -- Human-readable name derived from the entry-point: e.g. "POST /api/v1/charge"
    name            TEXT        NOT NULL,

    -- The entry-point node that roots this flow (framework endpoint / scheduler / CLI command).
    entry_node_id   UUID        NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,

    -- BFS depth reached from the entry point.
    depth           INTEGER     NOT NULL,

    -- Total nodes and distinct files spanned by the flow.
    node_count      INTEGER     NOT NULL,
    file_count      INTEGER     NOT NULL,

    -- Criticality: 0.0–1.0.  Derived from caller count × centrality × test gap.
    criticality     NUMERIC(4,3) NOT NULL DEFAULT 0,

    -- Full ordered path as a JSON array of qualified_names.
    path_json       JSONB       NOT NULL DEFAULT '[]'::jsonb,

    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_flows_workspace
    ON flows (workspace_id);

CREATE INDEX IF NOT EXISTS idx_flows_entry_node
    ON flows (entry_node_id);


-- ── flow_memberships ──────────────────────────────────────────────────────────
-- Which nodes participate in which flows (N:N).
-- Used by risk.py to compute flow_participation factor and by MCP list_flows.

CREATE TABLE IF NOT EXISTS flow_memberships (
    flow_id         UUID        NOT NULL REFERENCES flows(id) ON DELETE CASCADE,
    node_id         UUID        NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
    position        INTEGER     NOT NULL,   -- 0-based BFS traversal order
    PRIMARY KEY (flow_id, node_id)
);

CREATE INDEX IF NOT EXISTS idx_flow_memberships_node
    ON flow_memberships (node_id);


-- ── communities ──────────────────────────────────────────────────────────────
-- Leiden-algorithm call-graph clusters (or file-prefix fallback).
-- Ported from CRG's communities.py.
-- Schema-only in week 1; populated in week 4.

CREATE TABLE IF NOT EXISTS communities (
    id                  UUID    PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id        UUID    NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,

    -- Auto-generated or human-assigned name for this community.
    name                TEXT    NOT NULL,

    -- Internal edge density / cohesion score (0.0–1.0).
    cohesion            NUMERIC(4,3),

    -- Number of nodes in the community.
    size                INTEGER,

    -- Most common language among member nodes.
    dominant_language   TEXT,

    -- LLM-generated one-paragraph description of what this community does.
    description         TEXT
);

CREATE INDEX IF NOT EXISTS idx_communities_workspace
    ON communities (workspace_id);


-- ── node_communities ──────────────────────────────────────────────────────────
-- Maps each node to its community (1:1 — each node belongs to exactly one community).
-- Populated when Leiden runs; before that, nodes have no community row.

CREATE TABLE IF NOT EXISTS node_communities (
    node_id         UUID    NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
    community_id    UUID    NOT NULL REFERENCES communities(id) ON DELETE CASCADE,
    PRIMARY KEY (node_id)
);

CREATE INDEX IF NOT EXISTS idx_node_communities_community
    ON node_communities (community_id);


-- ── graph_metrics ─────────────────────────────────────────────────────────────
-- Nightly-computed topology scores per node.
-- metric_kind values:
--   'hub_degree'          — in+out degree; top 50 per workspace
--   'bridge_betweenness'  — betweenness centrality; top 25 per workspace (sampled at >5K)
-- Populated by companybrain/structural/topology.py in week 4.

CREATE TABLE IF NOT EXISTS graph_metrics (
    workspace_id    UUID        NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    node_id         UUID        NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
    metric_kind     TEXT        NOT NULL,
    score           NUMERIC,
    rank            INTEGER,
    computed_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (workspace_id, node_id, metric_kind)
);

CREATE INDEX IF NOT EXISTS idx_graph_metrics_workspace_kind
    ON graph_metrics (workspace_id, metric_kind);


-- ── Row Level Security ────────────────────────────────────────────────────────
-- All new tables follow the ADR-003 pattern: scoped by workspace_id via RLS.
-- Application sets: SET LOCAL app.workspace_id = '<uuid>' before every query.

ALTER TABLE flows               ENABLE ROW LEVEL SECURITY;
ALTER TABLE flow_memberships    ENABLE ROW LEVEL SECURITY;
ALTER TABLE communities         ENABLE ROW LEVEL SECURITY;
ALTER TABLE node_communities    ENABLE ROW LEVEL SECURITY;
ALTER TABLE graph_metrics       ENABLE ROW LEVEL SECURITY;

CREATE POLICY workspace_isolation ON flows
    USING (workspace_id = current_setting('app.workspace_id', true)::UUID);

-- flow_memberships inherits workspace scope via flows JOIN, but we add a direct
-- RLS guard for any direct-access queries.
CREATE POLICY workspace_isolation ON flow_memberships
    USING (
        EXISTS (
            SELECT 1 FROM flows f
            WHERE f.id = flow_id
              AND f.workspace_id = current_setting('app.workspace_id', true)::UUID
        )
    );

CREATE POLICY workspace_isolation ON communities
    USING (workspace_id = current_setting('app.workspace_id', true)::UUID);

CREATE POLICY workspace_isolation ON node_communities
    USING (
        EXISTS (
            SELECT 1 FROM communities c
            WHERE c.id = community_id
              AND c.workspace_id = current_setting('app.workspace_id', true)::UUID
        )
    );

CREATE POLICY workspace_isolation ON graph_metrics
    USING (workspace_id = current_setting('app.workspace_id', true)::UUID);

ALTER TABLE flows               FORCE ROW LEVEL SECURITY;
ALTER TABLE flow_memberships    FORCE ROW LEVEL SECURITY;
ALTER TABLE communities         FORCE ROW LEVEL SECURITY;
ALTER TABLE node_communities    FORCE ROW LEVEL SECURITY;
ALTER TABLE graph_metrics       FORCE ROW LEVEL SECURITY;
