-- V19: Link workspace_sources to their most recent pipeline job.
--
-- Adds last_job_id so PipelineService can update sync_status when a job
-- completes or fails (fixing the stuck-in-syncing-forever bug where the
-- DB stays at sync_status='syncing' even after the job finishes).
--
-- Also adds entity_count and config columns that the model expects but
-- were not included in V18.

ALTER TABLE workspace_sources
    ADD COLUMN IF NOT EXISTS entity_count INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS config       JSONB,
    ADD COLUMN IF NOT EXISTS last_job_id  UUID REFERENCES pipeline_jobs(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_workspace_sources_last_job_id
    ON workspace_sources(last_job_id);
