-- ADR-0049 C4: cross-job extraction_queue dedup.
--
-- result_json stores the serialised LLM extraction result so a second run can
-- reuse it without calling the LLM again (warm-rerun cost ≈ $0).
-- source_job_id tracks which job FIRST produced the result for lineage.
--
-- The idx_extraction_queue_done_by_hash index is used by the enqueue() dedup
-- lookup: "has this body_hash already been processed in this workspace?"
-- Filtering to status='done' AND result_json IS NOT NULL keeps the index
-- small (only terminal successful rows) and the lookup cheap.

ALTER TABLE extraction_queue
    ADD COLUMN IF NOT EXISTS result_json   TEXT,
    ADD COLUMN IF NOT EXISTS source_job_id UUID;

-- Index for the cross-job dedup lookup in enqueue().
CREATE INDEX IF NOT EXISTS idx_extraction_queue_done_by_hash
    ON extraction_queue (workspace_id, body_hash)
    WHERE status = 'done' AND result_json IS NOT NULL;
