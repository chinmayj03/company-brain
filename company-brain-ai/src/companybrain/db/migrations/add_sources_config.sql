-- Migration: workspace_sources config column
ALTER TABLE workspace_sources
  ADD COLUMN IF NOT EXISTS config JSONB NOT NULL DEFAULT '{}';

ALTER TABLE workspace_sources
  ADD COLUMN IF NOT EXISTS entity_count INTEGER NOT NULL DEFAULT 0;
