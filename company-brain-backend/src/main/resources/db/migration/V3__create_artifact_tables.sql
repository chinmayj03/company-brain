-- ============================================================
-- V3: Artifact-Centric Knowledge Pipeline (ADR-005)
--
-- Introduces three tables:
--   artifacts             — every ingested unit of knowledge, content-addressed
--   artifact_links        — provenance: which nodes were derived from which artifacts
--   artifact_change_events — append-only dirty-set log for incremental re-extraction
--
-- Design rationale: ADR-005-artifact-centric-knowledge-pipeline.md
-- ============================================================

-- ── artifacts ────────────────────────────────────────────────────────────────
-- Every ingested unit, regardless of source (source_file, pr, commit,
-- ticket, slack_thread, annotation, doc_page, spec, ...).
-- Content-addressed: (workspace_id, kind, external_id) is the dedup key.

CREATE TABLE artifacts (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id    UUID        NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,

    -- What kind of artifact this is.  Not an enum — collector kinds evolve without migrations.
    -- Current values: 'source_file' | 'pr' | 'commit' | 'annotation' |
    --                 'ticket' | 'slack_thread' | 'doc_page' | 'spec' | 'legacy'
    kind            TEXT        NOT NULL,

    -- Stable, source-derived identifier.
    -- source_file  → repo/relative/path/to/File.java
    -- commit       → <repo>::<commit_hash>
    -- pr           → <repo>::<pr_number>
    -- ticket       → <system>::<ticket_id>   (e.g. "jira::CB-1234")
    -- annotation   → <workspace_id>::<node_id>::<annotation_type>
    external_id     TEXT        NOT NULL,

    -- sha256 over normalised content (UTF-8, trimmed).
    -- Used for change detection — if hash matches last ingest, skip.
    content_hash    TEXT        NOT NULL,

    -- Canonical link back to the origin system (browsable URL).
    -- Null for local-only artifacts (local file paths, annotations).
    source_uri      TEXT,

    -- For small artifacts (<64 KB): store inline as UTF-8 text.
    -- For large artifacts: set content_ref = 's3://bucket/key' and leave null.
    content_inline  TEXT,
    content_ref     TEXT,       -- S3/GCS pointer for large blobs

    -- Who produced this artifact (human author or system).
    author          TEXT,

    -- When we last fetched/confirmed this version.
    fetched_at      TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- Previous hash before the most recent change; null on first ingest.
    -- Kept for one-hop diff without querying the event log.
    last_seen_hash  TEXT,

    -- Kind-specific extra fields (e.g. PR number, ticket severity, language).
    metadata        JSONB       NOT NULL DEFAULT '{}'::jsonb,

    CONSTRAINT uq_artifact_identity UNIQUE (workspace_id, kind, external_id)
);

-- ── artifact_links ────────────────────────────────────────────────────────────
-- Provenance edges: which graph nodes were derived from (or cite) which artifacts.
-- Every node MUST have at least one link with link_role = 'derived_from'.
-- Invariant enforced at application layer (PipelineService.applyResult).

CREATE TABLE artifact_links (
    artifact_id     UUID        NOT NULL REFERENCES artifacts(id) ON DELETE CASCADE,
    workspace_id    UUID        NOT NULL,
    node_id         UUID        NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,

    -- How this artifact relates to the node:
    --   'derived_from'     — node was extracted/synthesised from this artifact
    --   'cited_in_context' — artifact was included in the LLM context for synthesis
    --   'invalidates'      — reserved: explicit human override of a derived fact
    link_role       TEXT        NOT NULL,

    confidence      NUMERIC(3,2) NOT NULL DEFAULT 1.0,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),

    PRIMARY KEY (artifact_id, node_id, link_role)
);

-- ── artifact_change_events ────────────────────────────────────────────────────
-- Append-only dirty-set log.  ArtifactWriterService emits here on every
-- create / change / delete.  DirtySetService reads unconsumed events to build
-- the set of nodes that need re-extraction.

CREATE TABLE artifact_change_events (
    id              BIGSERIAL    PRIMARY KEY,
    workspace_id    UUID         NOT NULL,
    artifact_id     UUID         NOT NULL REFERENCES artifacts(id) ON DELETE CASCADE,

    -- 'created' | 'changed' | 'deleted'
    event_kind      TEXT         NOT NULL,

    old_hash        TEXT,        -- null for 'created' events
    new_hash        TEXT,        -- null for 'deleted' events

    occurred_at     TIMESTAMPTZ  NOT NULL DEFAULT now(),

    -- Set by DirtySetService when this event has been folded into a pipeline run.
    consumed_at     TIMESTAMPTZ
);

-- ── Indexes ───────────────────────────────────────────────────────────────────

-- artifact lookups
CREATE INDEX idx_artifacts_workspace_kind
    ON artifacts (workspace_id, kind);

CREATE INDEX idx_artifacts_workspace_external
    ON artifacts (workspace_id, external_id);

-- provenance traversal: node → its source artifacts
CREATE INDEX idx_artifact_links_node
    ON artifact_links (workspace_id, node_id);

-- provenance traversal: artifact → nodes derived from it
CREATE INDEX idx_artifact_links_artifact
    ON artifact_links (workspace_id, artifact_id);

-- dirty-set engine: fast scan of unconsumed events
CREATE INDEX idx_change_events_unconsumed
    ON artifact_change_events (workspace_id, occurred_at)
    WHERE consumed_at IS NULL;

-- ── Row Level Security ────────────────────────────────────────────────────────
-- Same pattern as V1 (nodes/edges/node_context).

ALTER TABLE artifacts               ENABLE ROW LEVEL SECURITY;
ALTER TABLE artifact_links          ENABLE ROW LEVEL SECURITY;
ALTER TABLE artifact_change_events  ENABLE ROW LEVEL SECURITY;

CREATE POLICY workspace_isolation ON artifacts
    USING (workspace_id = current_setting('app.workspace_id', true)::UUID);

CREATE POLICY workspace_isolation ON artifact_links
    USING (workspace_id = current_setting('app.workspace_id', true)::UUID);

CREATE POLICY workspace_isolation ON artifact_change_events
    USING (workspace_id = current_setting('app.workspace_id', true)::UUID);

ALTER TABLE artifacts               FORCE ROW LEVEL SECURITY;
ALTER TABLE artifact_links          FORCE ROW LEVEL SECURITY;
ALTER TABLE artifact_change_events  FORCE ROW LEVEL SECURITY;
