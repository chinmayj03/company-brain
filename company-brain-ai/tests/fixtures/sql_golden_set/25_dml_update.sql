-- Mark stale pipeline jobs as failed
UPDATE pipeline_jobs
SET status = 'failed',
    error_message = 'Timed out after 3600s',
    completed_at = now()
WHERE status = 'running'
  AND started_at < now() - interval '1 hour';
