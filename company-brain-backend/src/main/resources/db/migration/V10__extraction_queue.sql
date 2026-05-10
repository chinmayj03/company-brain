-- ADR-0044: per-method extraction queue.
-- Workers pull one chunk at a time via SELECT ... FOR UPDATE SKIP LOCKED.
-- The UNIQUE constraint on (workspace_id, job_id, file_path, qname, body_hash)
-- makes enqueue idempotent: re-inserting the same chunk is a silent no-op.

CREATE TABLE IF NOT EXISTS extraction_queue (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id    UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    job_id          UUID NOT NULL,
    repo            TEXT NOT NULL,
    file_path       TEXT NOT NULL,
    qname           TEXT NOT NULL,            -- e.g. "ClassName.methodName"
    body_hash       TEXT NOT NULL,            -- sha256 of method body
    chunk_kind      TEXT NOT NULL DEFAULT 'method',  -- method | top_decl | schema_block
    header_context  TEXT NOT NULL DEFAULT '',  -- class signature + fields
    import_context  TEXT NOT NULL DEFAULT '',
    body            TEXT NOT NULL,            -- full method body, no truncation
    status          TEXT NOT NULL DEFAULT 'pending',
                                              -- pending | in_progress | done | failed
    attempt_count   INT  NOT NULL DEFAULT 0,
    last_error      TEXT,
    cost_usd        NUMERIC(10,6) NOT NULL DEFAULT 0,
    input_tokens    INT NOT NULL DEFAULT 0,
    output_tokens   INT NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at      TIMESTAMPTZ,
    finished_at     TIMESTAMPTZ,
    UNIQUE (workspace_id, job_id, file_path, qname, body_hash)
);

CREATE INDEX idx_extraction_queue_pending
    ON extraction_queue (workspace_id, job_id, status)
    WHERE status = 'pending';
