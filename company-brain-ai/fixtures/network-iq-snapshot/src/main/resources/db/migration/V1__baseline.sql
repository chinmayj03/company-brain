-- Flyway baseline for the Network IQ ADR-0058 fixture.
-- Tiny but contains every shape the schema_sql extractor cares about:
--   * a regular table with single-column PK
--   * a multi-column FK constraint emitted at the table level
--   * a column whose type is a Postgres array (the LOB column ADR-0058 turns on)
--   * a partitioned table
--   * a partial UNIQUE index

CREATE TABLE plan_info (
    payer_plan_id varchar(64) PRIMARY KEY,
    plan_name text NOT NULL,
    is_current boolean NOT NULL DEFAULT TRUE
);

CREATE TABLE comp_providers (
    id uuid PRIMARY KEY,
    payer_id text[] NOT NULL,
    lob text,
    name text NOT NULL DEFAULT '',
    plan_info_id varchar(64) REFERENCES plan_info(payer_plan_id),
    created_at timestamp with time zone DEFAULT NOW()
);

CREATE UNIQUE INDEX idx_comp_providers_payer
    ON comp_providers (payer_id)
    WHERE payer_id IS NOT NULL;

CREATE TABLE provider_metrics (
    id uuid,
    captured_at timestamp with time zone,
    metric_name text,
    metric_value numeric(20, 4)
) PARTITION BY RANGE (captured_at);
