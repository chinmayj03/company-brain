-- ADR-0052 P6: APScheduler-backed scheduled extractions.
--
-- The scheduler.py module uses APScheduler's SQLAlchemyJobStore which expects
-- a (id, next_run_time, job_state) shape. We add an audit column for last-run
-- bookkeeping so `brain schedule list` can show whether the most recent fire
-- succeeded without standing up a separate audit table.

CREATE TABLE IF NOT EXISTS scheduled_tasks (
    id              TEXT PRIMARY KEY,
    next_run_time   DOUBLE PRECISION,
    job_state       BYTEA NOT NULL,
    last_run_at     TIMESTAMPTZ,
    last_run_ok     BOOLEAN,
    last_run_error  TEXT
);

CREATE INDEX IF NOT EXISTS idx_scheduled_tasks_next_run
    ON scheduled_tasks(next_run_time);
