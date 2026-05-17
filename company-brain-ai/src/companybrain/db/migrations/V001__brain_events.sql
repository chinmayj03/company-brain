-- ADR-0090 P1 — brain_events table (M1 event-stream storage)
--
-- Postgres: partitioned by RANGE (occurred_at), one partition per month.
-- Monthly partitions are pre-created 12 months out; a maintenance job
-- (not in P1 scope) should add new partitions each month.
--
-- SQLite: the CREATE TABLE IF NOT EXISTS below is used directly (no
-- partitioning syntax); the EventStore detects the engine dialect and
-- uses the appropriate DDL.

-- ── Parent table (Postgres) ───────────────────────────────────────────────────
-- The IF NOT EXISTS check prevents double-apply on re-runs.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_class WHERE relname = 'brain_events'
    ) THEN
        CREATE TABLE brain_events (
            id            TEXT        NOT NULL,
            workspace_id  TEXT        NOT NULL,
            repo          TEXT        NOT NULL DEFAULT '',
            branch        TEXT        NOT NULL DEFAULT '',
            event_type    TEXT        NOT NULL,
            payload       JSONB       NOT NULL DEFAULT '{}',
            occurred_at   TIMESTAMPTZ NOT NULL,
            recorded_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            causal_parents TEXT[]     NOT NULL DEFAULT '{}',
            actors        TEXT[]      NOT NULL DEFAULT '{}',
            urn_affected  TEXT
        ) PARTITION BY RANGE (occurred_at);

        -- Hot indexes: entity timeline + workspace timeline
        CREATE INDEX brain_events_urn_time
            ON brain_events (urn_affected, occurred_at)
            WHERE urn_affected IS NOT NULL;

        CREATE INDEX brain_events_workspace_time
            ON brain_events (workspace_id, occurred_at);

        CREATE INDEX brain_events_event_type
            ON brain_events (event_type, occurred_at);
    END IF;
END
$$;

-- ── Monthly child partitions (current month + next 12) ───────────────────────
-- Generated for 2026-05 through 2027-04. Extend annually via a cron job.
DO $$
DECLARE
    part_start DATE;
    part_end   DATE;
    part_name  TEXT;
    mo         INT;
BEGIN
    -- Only create partitions if the parent table was just created (first run).
    -- Check by looking for any existing partition.
    IF NOT EXISTS (
        SELECT 1 FROM pg_class c
        JOIN pg_inherits i ON i.inhrelid = c.oid
        JOIN pg_class p ON p.oid = i.inhparent
        WHERE p.relname = 'brain_events'
    ) AND EXISTS (
        SELECT 1 FROM pg_class WHERE relname = 'brain_events'
    ) THEN
        FOR mo IN 0..12 LOOP
            part_start := DATE_TRUNC('month', NOW()) + (mo || ' months')::INTERVAL;
            part_end   := part_start + '1 month'::INTERVAL;
            part_name  := 'brain_events_' || TO_CHAR(part_start, 'YYYY_MM');

            EXECUTE format(
                'CREATE TABLE IF NOT EXISTS %I PARTITION OF brain_events
                 FOR VALUES FROM (%L) TO (%L)',
                part_name, part_start, part_end
            );
        END LOOP;
    END IF;
END
$$;
