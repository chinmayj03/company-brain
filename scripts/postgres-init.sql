-- Run once on first Postgres startup.
-- Enables extensions needed by the schema.

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";    -- gen_random_uuid()
CREATE EXTENSION IF NOT EXISTS "pgcrypto";     -- AES encryption for node_context.body
CREATE EXTENSION IF NOT EXISTS "pg_trgm";      -- Fuzzy text search on node names
CREATE EXTENSION IF NOT EXISTS "btree_gin";    -- GIN indexes on JSONB metadata
