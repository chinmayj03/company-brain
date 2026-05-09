-- V8: LLM call log table for cost telemetry (ADR-0040 Tier 0.B)
--
-- Every LLM call in the Python AI service writes one row here so that
-- make -f Makefile.demo cost can query per-run spend without relying on
-- edge_events (which only tracks graph events, not LLM calls).

CREATE TABLE IF NOT EXISTS llm_call_log (
    id                    UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id          UUID,
    job_id                TEXT,
    stage                 TEXT,
    provider              TEXT,
    model                 TEXT,
    role                  TEXT,
    input_tokens          INT,
    output_tokens         INT,
    cache_read_tokens     INT,
    cache_creation_tokens INT,
    cost_usd              NUMERIC(12, 8),
    latency_ms            INT,
    occurred_at           TIMESTAMPTZ   NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_llm_call_log_ws_time
    ON llm_call_log (workspace_id, occurred_at DESC);

CREATE INDEX IF NOT EXISTS idx_llm_call_log_job
    ON llm_call_log (job_id, occurred_at DESC);
