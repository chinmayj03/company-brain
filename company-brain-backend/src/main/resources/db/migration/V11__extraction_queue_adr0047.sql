-- ADR-0047: Add language, filter_reason columns and 'filtered' status to extraction_queue.
--
-- 'filtered' is a terminal status (like 'done') for chunks that were
-- classified as trivially irrelevant before any LLM call was made.
-- filter_reason records which filter tier dropped the chunk for telemetry.
-- language records the detected source language for cost/quality breakdowns.

ALTER TABLE extraction_queue
    ADD COLUMN IF NOT EXISTS language      TEXT NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS filter_reason TEXT NOT NULL DEFAULT '';

-- No CHECK constraint on status — allows forward-compatible extension without
-- a migration every time a new terminal state is added.
-- Existing values: pending | in_progress | done | failed
-- New value added: filtered

-- Index to support telemetry queries by status breakdown
CREATE INDEX IF NOT EXISTS idx_extraction_queue_filtered
    ON extraction_queue (workspace_id, job_id, status)
    WHERE status = 'filtered';

-- Index to support language-level cost/quality queries
CREATE INDEX IF NOT EXISTS idx_extraction_queue_language
    ON extraction_queue (workspace_id, job_id, language)
    WHERE language <> '';
