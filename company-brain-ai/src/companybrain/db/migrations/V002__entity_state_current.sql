-- ADR-0090 P1 — entity_state_current table (M2 V1 EntityState cache)
--
-- Stores the most-recently-materialized state for each (urn, branch) pair.
-- Refreshed on every new BrainEvent that touches the entity.
-- Stale entries (last_refreshed_at < NOW() - interval '60 seconds') are
-- marked as needing re-derivation at query time.

CREATE TABLE IF NOT EXISTS entity_state_current (
    urn             TEXT        NOT NULL,
    branch          TEXT        NOT NULL DEFAULT 'main',
    workspace_id    TEXT        NOT NULL DEFAULT '',
    repo            TEXT        NOT NULL DEFAULT '',
    -- Denormalised snapshot of the entity at last-known-good state.
    -- Stored as JSONB so schema evolution doesn't require migrations here.
    snapshot        JSONB       NOT NULL DEFAULT '{}',
    -- Timestamp of the most recent BrainEvent that updated this row.
    last_event_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- When this cache row was last refreshed by EntityStateCacheV1.refresh().
    last_refreshed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- How many events have been applied to derive this state.
    event_count     BIGINT      NOT NULL DEFAULT 0,

    PRIMARY KEY (urn, branch)
);

CREATE INDEX IF NOT EXISTS entity_state_workspace_idx
    ON entity_state_current (workspace_id, urn);

CREATE INDEX IF NOT EXISTS entity_state_freshness_idx
    ON entity_state_current (last_event_at DESC);
