-- ADR-0046: Adaptive chunking + relevance-first extraction.
-- Adds strategy and relevance tracking columns to extraction_queue.
-- Uses IF NOT EXISTS guards so re-running is safe in CI.

ALTER TABLE extraction_queue
    ADD COLUMN IF NOT EXISTS strategy TEXT NOT NULL DEFAULT 'per_method';
-- strategy: whole_file | batched_methods | per_method

ALTER TABLE extraction_queue
    ADD COLUMN IF NOT EXISTS relevance_skipped BOOLEAN NOT NULL DEFAULT false;

ALTER TABLE extraction_queue
    ADD COLUMN IF NOT EXISTS relevance_reason TEXT;
-- reason: lombok_trivial | object_override | empty_or_stub |
--         pure_delegation | deprecated | test_method

-- Index to let operators grep filtered chunks by reason in pg.
CREATE INDEX IF NOT EXISTS idx_extraction_queue_filtered
    ON extraction_queue (workspace_id, job_id, relevance_reason)
    WHERE relevance_skipped = true;

-- Allow 'filtered' as a valid status for chunks that were dropped by
-- the relevance filter but persisted as stub entities (ADR-0046 D3).
-- The status check constraint in V10 only allows pending|in_progress|done|failed;
-- we extend it here.  If there's no explicit constraint, this is a no-op.
DO $$
BEGIN
    ALTER TABLE extraction_queue
        DROP CONSTRAINT IF EXISTS extraction_queue_status_check;
    ALTER TABLE extraction_queue
        ADD CONSTRAINT extraction_queue_status_check
        CHECK (status IN ('pending','in_progress','done','failed','filtered'));
EXCEPTION WHEN others THEN
    -- Constraint didn't exist or couldn't be modified — non-fatal.
    NULL;
END;
$$;
