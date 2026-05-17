-- Migration: add columns to pipeline_jobs table
ALTER TABLE pipeline_jobs ADD COLUMN gap_count int NOT NULL DEFAULT 0;
ALTER TABLE pipeline_jobs ADD COLUMN code_units_found int;
ALTER TABLE pipeline_jobs ADD COLUMN git_commits_found int;
ALTER TABLE pipeline_jobs ADD COLUMN stages_summary jsonb;
ALTER TABLE pipeline_jobs ADD COLUMN files_traced jsonb;
