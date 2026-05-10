-- ADR-0052 P6: Per-entity sticky notes.
--
-- Free-form annotations attached to a node URN that the harness surfaces in
-- `/query` responses next to the cited entity. Use cases: "Deprecated 2026-Q4",
-- "Owned by payments-team", "Slow under high load — see incident #4421".
--
-- One row per (workspace, urn, note); a single entity can have many notes.

CREATE TABLE IF NOT EXISTS entity_notes (
    id          BIGSERIAL PRIMARY KEY,
    workspace_id UUID NOT NULL,
    entity_urn  TEXT NOT NULL,
    note        TEXT NOT NULL,
    author      TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_entity_notes_urn
    ON entity_notes(workspace_id, entity_urn);
