# Post-Merge Runbook — ADR-0044 Chunked Extraction

## What changed

ADR-0044 replaces the per-file LLM extraction call with a **per-method chunk queue**.
Every source file is split into individual method chunks before any LLM call is made.
Each chunk gets one focused LLM call (max 600 output tokens). No file is truncated.

---

## Environment flags

| Variable | Default | Effect |
|----------|---------|--------|
| `BRAIN_USE_CHUNK_QUEUE` | `true` | Route extraction through the chunk queue. Set `false` to temporarily disable. |
| `BRAIN_LEGACY_EXTRACT` | `false` | Force the old per-file path. Use when debugging regressions. |
| `BRAIN_JOB_BUDGET_USD` | `0.50` | Pipeline aborts if cumulative cost exceeds this. Remaining chunks stay `pending`. |
| `BRAIN_CHUNK_QUEUE_MAX_WORKERS` | `4` | Number of parallel extraction workers per run. |

---

## Rollback procedure

If chunked extraction causes issues, set `BRAIN_LEGACY_EXTRACT=true` in your `.env`
(or Kubernetes configmap) and restart the AI service. No code change required.

```bash
# Immediate rollback — no restart of DB needed
BRAIN_LEGACY_EXTRACT=true uvicorn companybrain.api.main:app
```

To make the rollback permanent until a fix ships:

```env
# .env
BRAIN_USE_CHUNK_QUEUE=true
BRAIN_LEGACY_EXTRACT=true   # forces legacy path even when chunk queue is enabled
```

---

## Running the database migration

The extraction queue table is created by:

```
company-brain-backend/src/main/resources/db/migration/V10__extraction_queue.sql
```

Flyway runs this automatically on backend startup. To run it manually:

```bash
make migrate
# or
./mvnw flyway:migrate -pl company-brain-backend
```

---

## Retrying failed chunks

If some chunks failed (network error, rate limit, budget exhaustion), use:

```bash
brain enrich --retry-failed --workspace-id <ws-uuid> --job-id <job-uuid>
```

This resets all `failed` rows to `pending` for re-processing. Workers pick them up
on the next `brain enrich` run automatically.

In Python directly:

```python
import asyncio
from companybrain.pipeline.queue import retry_failed

asyncio.run(retry_failed(job_id="...", workspace_id="..."))
```

---

## Querying telemetry

Every chunk emits a structured log line with key `extraction_chunk`. To sum costs by file:

```bash
grep "extraction_chunk" /tmp/run.log | \
  jq -s 'group_by(.file) | map({file: .[0].file, total_cost: map(.cost_usd) | add})'
```

To find slow chunks:

```bash
grep "extraction_chunk" /tmp/run.log | jq 'select(.latency_ms > 3000)'
```

To count failures:

```bash
grep "extraction_chunk" /tmp/run.log | jq 'select(.status == "failed")' | wc -l
```

---

## Monitoring the queue

Check queue state for a specific job:

```sql
SELECT status, count(*) FROM extraction_queue
WHERE job_id = '<job-uuid>'
GROUP BY status;
```

Find stuck `in_progress` rows (worker crashed mid-chunk):

```sql
UPDATE extraction_queue
SET status = 'pending', started_at = NULL
WHERE job_id = '<job-uuid>'
  AND status = 'in_progress'
  AND started_at < now() - interval '10 minutes';
```

---

## Acceptance test

The no-truncation guarantee is validated by:

```bash
cd company-brain-ai
pytest tests/acceptance/test_no_truncation.py -v
```

This generates a synthetic 30-method class (~120k chars) and asserts that all 30
SQL queries appear verbatim in chunk bodies with no truncation.

---

## Architecture reference

See [docs/adrs/ADR-0044-chunked-extraction-no-truncation.md](docs/adrs/ADR-0044-chunked-extraction-no-truncation.md)
for the full design rationale and 6-PR implementation plan.
