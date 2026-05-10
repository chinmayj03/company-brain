-- ADR-0052 P6: Pin / propose flags on graph nodes.
--
--   pinned   — the entity is human-curated; rebuild passes must NOT overwrite
--              its row (last_modified_commit, summary, etc. are frozen).
--   proposed — the entity is a draft suggestion; queries hide it unless the
--              caller passes --include-proposed.
--
-- Both default FALSE so existing rows behave identically to pre-P6 code.

ALTER TABLE nodes
    ADD COLUMN IF NOT EXISTS pinned   BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS proposed BOOLEAN NOT NULL DEFAULT FALSE;
