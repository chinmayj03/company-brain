# Post-Merge Runbook — ADR-0042 Language-Agnostic Extraction

This runbook covers everything needed after merging the four ADR-0042 PRs into main.

---

## 1. Database migration

**Run before starting any AI-service pods that use the new code.**

```bash
# Fly to the backend module and apply the Flyway migration
cd company-brain-backend
./mvnw flyway:migrate -Dflyway.url=$DATABASE_URL

# Verify the view was created
psql $DATABASE_URL -c "\dv edges_reverse"

# Optionally refresh it immediately (zero-downtime — uses CONCURRENTLY)
psql $DATABASE_URL -c "REFRESH MATERIALIZED VIEW CONCURRENTLY edges_reverse;"
```

The migration file is `V9__edges_reverse_materialized_view.sql`. It is idempotent
(`CREATE ... IF NOT EXISTS`), so re-running is safe.

---

## 2. Environment variables — new flags

### Per-pass skip flags

Each LLM extraction pass can be individually disabled. Set to `true` or `1` to skip.

| Variable | Pass disabled | When to use |
|---|---|---|
| `BRAIN_SKIP_ANNOTATION_PASS` | AnnotationPass (E2) | Debugging annotation edge noise |
| `BRAIN_SKIP_STORAGE_TARGET_PASS` | StorageTargetPass (E3) | Tables already well-populated |
| `BRAIN_SKIP_SCHEMA_MIGRATION_PASS` | SchemaMigrationPass (E5) | No migration files in repo |
| `BRAIN_SKIP_CLIENT_CALL_PASS` | ClientCallPass (E6) | Pure backend repos with no outbound HTTP |
| `BRAIN_SKIP_TEST_COVERAGE_PASS` | TestCoveragePass (E7) | Repos without tests |

**Example — skip all passes to get file-level entities only:**
```bash
BRAIN_SKIP_ANNOTATION_PASS=true \
BRAIN_SKIP_STORAGE_TARGET_PASS=true \
BRAIN_SKIP_SCHEMA_MIGRATION_PASS=true \
BRAIN_SKIP_CLIENT_CALL_PASS=true \
BRAIN_SKIP_TEST_COVERAGE_PASS=true \
brain_worker start
```

### Cost and budget controls

| Variable | Default | Description |
|---|---|---|
| `BRAIN_JOB_BUDGET_USD` | `0.50` | Per-job LLM cost ceiling before Stage 3 halts |
| `ENABLE_INTENT_ROUTER` | `true` | Enables the ~$0.001/query intent-router pre-pass |

To disable the intent router entirely:
```bash
ENABLE_INTENT_ROUTER=false brain_api start
```

### Per-pass token budgets (override via config)

These are set in `config.py` and can be overridden via Pydantic settings env vars:

| Variable | Default | Controls |
|---|---|---|
| `MAX_TOKENS_ANNOTATION_PASS` | `800` | AnnotationPass output token cap |
| `MAX_TOKENS_STORAGE_TARGET_PASS` | `1500` | StorageTargetPass output token cap |
| `MAX_TOKENS_SCHEMA_MIGRATION_PASS` | `2500` | SchemaMigrationPass output token cap |
| `MAX_TOKENS_CLIENT_CALL_PASS` | `1500` | ClientCallPass output token cap |
| `MAX_TOKENS_TEST_COVERAGE_PASS` | `2500` | TestCoveragePass output token cap |

---

## 3. Verify passes are running

After a successful ingestion job, check the `stages_summary` in the pipeline result
(visible in the admin dashboard or via `GET /v1/internal/jobs/{job_id}`).

Each pass appends a summary with this shape:
```json
{
  "stage": "annotation_pass",
  "label": "annotation_pass",
  "edges_emitted": 12,
  "skipped_via_env": false,
  "duration_ms": 843,
  "input_tokens": 1204,
  "output_tokens": 387
}
```

If `edges_emitted` is 0 for every pass on a non-trivial repo, check:
1. `skipped_via_env` — a skip flag may be set
2. The entities list reaching Stage 2.5 — if Stage 1 produced no `code_snippet` fields,
   passes will skip (empty `_build_user_message` returns early)
3. LLM provider errors in `stages_summary[*]["error"]`

---

## 4. E9 — edges_reverse maintenance

The `edges_reverse` materialized view is **not** auto-refreshed. Add a scheduled job:

```sql
-- Refresh every 15 minutes (zero downtime with CONCURRENTLY)
REFRESH MATERIALIZED VIEW CONCURRENTLY edges_reverse;
```

Or call from the Java PipelineService after each successful job flush — a hook point
is already present in `PipelineService.refreshMaterializedViews()`.

**Index verification:**
```sql
SELECT indexname, indexdef
FROM pg_indexes
WHERE tablename = 'edges_reverse';
-- Should show: idx_edges_reverse_unique, idx_edges_reverse_target
```

---

## 5. E1 — ripgrep symbol resolver

The cross-file call graph now uses `rg` (ripgrep) for Tier 1 symbol resolution.
Verify `rg` is available in all AI-service container images:

```bash
docker run --rm company-brain-ai:latest rg --version
# Expected: ripgrep 14.x.x
```

If `rg` is not installed, add to the Dockerfile:
```dockerfile
RUN apt-get install -y ripgrep
```

The resolver falls back gracefully (logs a warning, skips Tier 1) if `rg` is absent,
but cross-file edges will be lower quality.

---

## 6. Rollback

If any pass causes regressions, skip it via env var (see §2) without rolling back code.

For the `edges_reverse` view:
```sql
DROP MATERIALIZED VIEW IF EXISTS edges_reverse;
```

The view is purely additive — dropping it does not affect the `edges` table.

For the intent router, set `ENABLE_INTENT_ROUTER=false`.

---

## 7. First-run smoke test

```bash
# Ingest the java_jpa test fixture and verify key edges are present
cd company-brain-ai
python -m pytest tests/passes/test_e2e_per_language.py -v

# Run all pass unit tests
python -m pytest tests/passes/ -v --tb=short
```

All 7 test files in `tests/passes/` should pass.
