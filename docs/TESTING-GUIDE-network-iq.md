# Testing Guide — network-iq-backend-java end-to-end demo

> **Goal.** Run the company-brain pipeline against
> `/Users/chinmayjadhav/Documents/network-iq-backend-java`, verify both CLI and
> UI surfaces produce the same data, ask a natural-language question, and stay
> under your $4 Anthropic credit cap.
>
> **Time:** ~45 min on a fresh machine, ~15 min on a warm one.
>
> **Companion files:** `Makefile.demo`, `scripts/upsert-env.sh`,
> `scripts/anthropic-spend.sh`, `scripts/anthropic-delta.sh`.

---

## TL;DR — the whole flow in eight commands

```bash
# 0. set ANTHROPIC_API_KEY in .env first, then:
make -f Makefile.demo doctor
make -f Makefile.demo guard
make -f Makefile.demo up-all      # then start backend / ai / cb-api / frontend in 4 more terminals
make -f Makefile.demo health
make -f Makefile.demo discover
make -f Makefile.demo run-cli ENDPOINT=/api/<your-endpoint> METHOD=GET
make -f Makefile.demo run-ui  ENDPOINT=/api/<your-endpoint> METHOD=GET
make -f Makefile.demo compare
make -f Makefile.demo ask Q='what does the <endpoint> handler do'
make -f Makefile.demo cost
```

If anything fails at any step, see the **Troubleshooting** section at the end.

---

## ADR-0029 question — should you implement it before testing?

**No, not the full ADR.** It's 5 days of work and your credit budget is $4.
Instead, do this 30-minute hardening manually before running. Implement full
ADR-0029 *after* a successful first demo, before any external demo.

Manual hardening checklist (covered by `make -f Makefile.demo guard`):

- [x] `LLM_PROVIDER=anthropic` in `.env`
- [x] `ANTHROPIC_MODEL_*=claude-haiku-4-5-20251001` for every role except QUERY
- [x] `ANTHROPIC_MODEL_QUERY=claude-sonnet-4-6` (used only by `/v1/ask` once
       you wire ADR-0030; until then Haiku is used everywhere)
- [x] `ANTHROPIC_PROMPT_CACHE=true` — cache_control:ephemeral on system prompts
- [x] `BRAIN_TOKEN_BUDGET=4000` — tighter smart-zone budget
- [x] `BRAIN_MAX_EXTRACTION_CONCURRENCY=2` — predictable cost per run
- [x] **Anthropic console budget alert at $3.50** (do this in the web console
       — the Makefile cannot do it for you)

This gives you ~80% of ADR-0029's protection at ~5% of the cost. Implement the
rest after you've shipped one successful demo.

---

## Step 0 — Pre-flight (5 min)

### 0.1 Get an Anthropic API key

1. Go to https://console.anthropic.com/settings/keys.
2. Create a new key with name `company-brain-demo`.
3. Set a workspace-level spend limit:
   https://console.anthropic.com/settings/billing → Spend Limits →
   **hard cap $3.50, alert at $2.50**.

### 0.2 Put it in `.env`

```bash
cd /Users/chinmayjadhav/Documents/Claude/Projects/company-brain
cp .env.example .env       # if .env doesn't exist
# Edit .env, set:
#   ANTHROPIC_API_KEY=sk-ant-api03-...
```

### 0.3 Run the doctor

```bash
make -f Makefile.demo doctor
```

**Expected output:**
```
→ Pre-flight checks
✓ pre-flight ok
  target repo: /Users/chinmayjadhav/Documents/network-iq-backend-java
  workspace:   00000000-0000-0000-0000-000000000001 (dev)
  cost cap:    $0.50
```

If you see `✗`, fix the listed issue and rerun.

### 0.4 Apply guards

```bash
make -f Makefile.demo guard
```

**Expected output:** `✓ .env updated` plus a reminder to set the Anthropic
console alert. The `.env` will now have Haiku set as the default for every
role and prompt caching turned on.

---

## Step 1 — Bring up infrastructure (10 min)

### 1.1 Start Docker services

```bash
make -f Makefile.demo up-all
```

This starts:
- Postgres on `:5432` (semantic graph + RLS)
- Redis on `:6379` (job state + cache)
- LocalStack on `:4566` (SQS emulation)
- Neo4j on `:7474` (browser) / `:7687` (bolt) — structural graph
- Qdrant on `:6333` (vectors)

**Expected output:** `✓ infrastructure up` followed by health check passing
for postgres / redis / neo4j / qdrant / localstack.

You'll be told to open four more terminals. Do that:

```bash
# Terminal 1 — Java backend (port 8080)
make backend
# Wait for: "Started CompanyBrainApplication in N seconds"

# Terminal 2 — Python AI service (port 8000)
make ai
# Wait for: "Application startup complete"

# Terminal 3 — Bun cb-api (port 8090)
make -f Makefile.demo cb-api
# Wait for: "Listening on http://localhost:8090"

# Terminal 4 — React frontend (port 5173)
make frontend
# Wait for: "ready in N ms"
```

### 1.2 Confirm everything is up

Back in the original terminal:

```bash
make -f Makefile.demo health
```

**Expected output:**
```
→ Health checks
  postgres   : ok
  redis      : ok
  neo4j      : ok
  qdrant     : ok
  localstack : ok
  java api   : ok
  python ai  : ok
  cb-api     : ok
  frontend   : ok
```

If any service is `down` or `not started yet`, go back to that terminal and
wait or check the log. The pipeline cannot run until all are `ok`.

---

## Step 2 — Discover endpoints in your repo (1 min, no LLM cost)

```bash
make -f Makefile.demo discover
```

**Expected output:**
```
→ Endpoints in /Users/chinmayjadhav/Documents/network-iq-backend-java:
  GET     /api/users/{id}                          src/main/java/.../UserController.java:42
  POST    /api/users                               src/main/java/.../UserController.java:67
  GET     /api/competitiveness/{payerId}           src/main/java/.../NiqController.java:28
  ...
```

**Pick the simplest endpoint first** — usually a `GET` on a small controller.
Avoid endpoints with deep service-chain handlers on your first run (they
cost more). Aim for a handler with maybe 3–5 dependencies.

---

## Step 3 — Run pipeline via CLI (3–8 min, costs ~$0.30–0.50)

```bash
make -f Makefile.demo run-cli \
  ENDPOINT=/api/competitiveness/{payerId} \
  METHOD=GET
```

**What happens:**
1. The Makefile POSTs to `http://localhost:8080/v1/pipeline/start` to enqueue
   the job (Java backend dispatches to Python).
2. The Python orchestrator runs:
   - Stage 0a: code tracing (NavigatorAgent walks the call chain) — 1 LLM call
   - Stage 0b: git history collection — no LLM
   - Stage 0c: freshness check (skip files unchanged since last run) — no LLM
   - Stage 1: entity extraction (one LLM call per code unit, 4–8 typically)
   - Stage 1.5: intent synthesis (one LLM call per function entity)
   - Stage 1.6: import-graph CALLS edges — no LLM
   - Stage 2: relationship extraction — 1 LLM call
   - Stage 3: business context synthesis — multiple LLM calls
   - Stage 3.5: T0/T1 memory tokenization — no LLM
   - Stage 4: gap detection — 1 LLM call
   - Stage 5: write to Postgres + Neo4j (via cb-api)
3. The Makefile polls `http://localhost:8000/pipeline/jobs/<id>` every 2 s
   and shows the current stage live.

**Expected progress trace:**
```
  status: running        stage: 0a              ← code tracing
  status: running        stage: 1               ← entity extraction
  status: running        stage: 1.5             ← intent synthesis
  status: running        stage: 2               ← relationships
  status: running        stage: 3               ← business context
  status: running        stage: 3.5             ← memory tokens
  status: running        stage: 4               ← gap detection
  status: running        stage: 5               ← persistence
  status: completed      stage: done
{
  "entity_count": 14,
  "edge_count": 23,
  "gap_count": 2,
  "code_units_found": 5,
  "files_traced": [
    "src/main/java/.../NiqController.java",
    "src/main/java/.../NiqService.java",
    ...
  ],
  ...
}
  spend after:  $0.32 ($0.32 for this run)
```

**If the run fails:**
- Check `logs/ai.log` for the stack trace.
- Run `make -f Makefile.demo diag` to dump everything to a single file.
- Common: `OLLAMA_NUM_CTX too small` → the Makefile already disables Ollama,
  so you should not see this. If you do, your `LLM_PROVIDER` isn't `anthropic`
  — re-run `guard`.
- Common: `429 rate_limit_exceeded` → wait 60 s and retry.
- Common: `pipeline_job_id not found` → the Java backend didn't dispatch.
  Check `make backend` terminal for an error.

The CLI job_id is saved to `/tmp/cb-demo-last-cli-job` for the comparison
step.

---

## Step 4 — Run the SAME endpoint via UI (3–8 min, ~$0.00 if Stage 0c skips)

Most or all files are already extracted, so this run should be near-free
thanks to the freshness pre-flight check.

```bash
make -f Makefile.demo run-ui \
  ENDPOINT=/api/competitiveness/{payerId} \
  METHOD=GET
```

The browser opens to `http://localhost:5173`. **In the UI:**

1. Click **API Explorer** in the navigation.
2. **Repo path:** `/Users/chinmayjadhav/Documents/network-iq-backend-java`
3. **Branch:** `main`
4. **Endpoint path:** `/api/competitiveness/{payerId}`
5. **HTTP method:** `GET`
6. Click **Run pipeline**.
7. Watch the live stage progress in the timeline panel.
8. When done, the result panel shows entity / edge counts and a job_id.
9. Copy the job_id and back in the terminal:

```bash
echo <paste-job-id-here> > /tmp/cb-demo-last-ui-job
```

**Expected UI behaviour:**
- Stage 0c shows `fresh: 5, dirty: 0` — every file was already extracted by
  the CLI run. LLM cost on this run should be near zero.
- Final entity_count and edge_count should match the CLI run within ±1 (any
  difference is from Stage 4 gap detection emitting different "gap" entities).

---

## Step 5 — Compare CLI vs UI (1 min)

```bash
make -f Makefile.demo compare
```

**Expected output:**
```
→ Comparing job_ids:
  CLI: 3f12...
  UI:  9a8b...

→ Postgres entity counts
  cli  | 14
  ui   | 14

→ Postgres totals (workspace-wide)
  ApiEndpoint        | 1
  Function           | 7
  Class              | 4
  DatabaseQuery      | 2
  ...

→ Neo4j totals
  Function           | 7
  Class              | 4
  Module             | 5
  ...
```

**What to verify:**
- CLI and UI counts match. If they don't, one of the runs failed silently
  (check the AI service log).
- Postgres `node_type` totals are non-zero across the expected categories.
- Neo4j shows the structural facts (`Function`, `Class`, `Module`).

If counts diverge, run `make -f Makefile.demo diag` and inspect the dumped
report.

---

## Step 6 — Ask the brain a question (1 min, ~$0.05–0.15)

```bash
make -f Makefile.demo ask Q='what does the competitiveness handler do and what tables does it read?'
```

**What happens:**
- The Python `/query` route retrieves relevant nodes from Postgres + Neo4j.
- Context is sent to Sonnet 4.6 (the only place Sonnet runs in this flow).
- The answer comes back as JSON.

**Expected output (shape, not exact text):**
```json
{
  "answer": "The competitiveness handler in NiqController.getByPayer takes a payerId path variable, validates it via Spring's @PathVariable binding, then delegates to NiqService.computeScore(...) which loads PayerData and applicable rules from the niq_payer_data and niq_rule_set tables. The result is wrapped in a CompetitivenessResponse DTO and returned with a 200 status.",
  "citations": [
    "NiqController::getByPayer",
    "NiqService::computeScore",
    "table:niq_payer_data",
    "table:niq_rule_set"
  ],
  "tokens_used": 4823,
  "cost_usd": 0.087
}
```

**The answer should reference real classes and tables from your code.** If
it sounds generic ("the handler validates the input and returns a result"),
the brain didn't surface enough context — likely because the pipeline
extracted few entities. Check Step 3's `entity_count`; below ~8 means the
NavigatorAgent only saw the controller.

---

## Step 7 — UI parity for the question (1 min, ~$0.05–0.15)

In the browser:

1. Click **Ask** in the navigation.
2. Type the same question: `what does the competitiveness handler do and
   what tables does it read?`
3. Click **Ask** or press Enter.
4. The UI shows the same answer (or one very close — the LLM is
   non-deterministic, so wording may differ but the cited entities should
   be the same).

**Why both surfaces work the same:** the React UI calls the Java backend's
`/v1/query` endpoint, which proxies to the Python `/query` route — exactly
what the Makefile hits via curl. Both surfaces share the same answer code
path. If they diverge, the Java backend's proxy is broken — check
`logs/backend.log` for a 500.

---

## Step 8 — Total cost check

```bash
make -f Makefile.demo cost
```

**Expected output:**
```
→ LLM spend (last 24h)
  spend_usd
  ----------
  0.4732
```

Plus a reminder to verify against the Anthropic console (the authoritative
source). Postgres-side numbers are best-effort — if any LLM call failed
before the cost telemetry was written, the local total will under-report.

After this whole flow you should have spent roughly:
- One CLI extraction:           $0.30–0.50
- One UI extraction (cached):   $0.00–0.05
- Two `/query` calls:           $0.10–0.30 total

Total for the demo:             **$0.40–0.85**.

That leaves ~$3 in the budget for live demo runs. If a single CLI run cost
more than $0.80, your repo is bigger than the Makefile assumes — either
pick a smaller endpoint or bump `BRAIN_MAX_EXTRACTION_CONCURRENCY` down to
`1` and rerun.

---

## What "good" looks like — a successful demo signature

| Check | Expected |
|---|---|
| `make -f Makefile.demo health` | every line `ok` |
| First CLI run | `entity_count >= 8`, completes in <8 min, costs <$0.50 |
| Second UI run on same endpoint | most files `fresh`, costs <$0.10 |
| `compare` | CLI and UI counts match within ±1 |
| `ask` | answer cites real classes / tables from your repo |
| `cost` | total <$1 for the dry run |
| Postgres `nodes` | >0 rows for each of `ApiEndpoint`, `Function`, `Class` |
| Neo4j browser at `http://localhost:7474` | shows function-level call graph |
| `.brain/index.json` (if Stage 1 ADRs are shipped) | matches Postgres counts |

If any of these fail, do not demo to a real audience. Run `diag`, fix the
issue, rerun.

---

## Troubleshooting

### `make backend` fails with "Flyway migration error"
```bash
make db-reset
make backend
```

### `make ai` fails with `OLLAMA_HOST connection refused`
You're still on Ollama. Re-run guards:
```bash
make -f Makefile.demo guard
# Then restart `make ai`.
```

### `make -f Makefile.demo cb-api` says `bun: command not found`
```bash
curl -fsSL https://bun.sh/install | bash
exec $SHELL -l    # reload PATH
make -f Makefile.demo cb-api
```

### Pipeline starts but immediately fails with `cb-api unreachable`
The orchestrator's `_trigger_structural_extraction()` hits cb-api at the end.
If cb-api is down:
- Check Terminal 3 for an error.
- Confirm `curl http://localhost:8090/health` returns 200.
- The structural step is non-fatal — the pipeline still completes, but the
  Neo4j graph won't update. The `compare` step will show empty Neo4j totals.

### Pipeline runs forever, no progress past Stage 1
The NavigatorAgent is in an LLM loop. Either:
- The endpoint path you typed doesn't match any route in the repo. Re-check
  `make -f Makefile.demo discover`.
- The repo's controllers use a non-standard pattern. Check
  `companybrain/collectors/code_tracer.py` regex coverage.

### `429 rate_limit_exceeded` from Anthropic
You're hitting the Anthropic per-minute cap. Either:
- Wait 60 s and rerun.
- Lower concurrency: `make -f Makefile.demo guard COST_CAP=0.50` then edit
  `.env` and set `BRAIN_MAX_EXTRACTION_CONCURRENCY=1`.

### `BudgetExceeded` after CostGuard
Good — that's the manual cost cap working. The pipeline halts with
`status=halted_budget`. To raise:
```bash
./scripts/upsert-env.sh BRAIN_JOB_BUDGET_USD 1.00
# Restart `make ai` for it to pick up the new value.
```

### CLI and UI counts disagree by a lot
Something silently failed mid-run on one of them. Check:
- `logs/ai.log` for the slower run's job_id — search for `[ERROR]`
- `psql -c "SELECT count(*) FROM nodes;"` between runs to detect partial writes
- Run `make -f Makefile.demo wipe` and try again from scratch

### Frontend shows "blank screen"
Hard-refresh (Cmd+Shift+R). If still blank:
- Check Terminal 4 for a Vite error.
- Confirm `curl http://localhost:5173` returns HTML.
- Check the browser console — usually a CORS or 4xx from the Java backend.

### "out of credits" mid-demo
You hit Anthropic's hard cap. Live answer:
1. Add $5 in the console and continue, OR
2. Switch to a free Groq fallback by editing `.env`:
   ```
   LLM_PROVIDER=groq
   GROQ_API_KEY=gsk_...
   ```
   Restart `make ai`. Quality drops slightly but the demo runs.

---

## What gets persisted where (mental model for the demo)

```
Your edit  ──►  CLI / UI starts pipeline
                ┌──────────────────────────────────────┐
                │  Java backend (8080)                 │
                │   ↓ dispatch                         │
                │  Python AI service (8000)            │
                │   ↓ extract                          │
                │   ↓ POST results back                │
                │  Java backend                        │
                │   ↓ writes                           │
                │  Postgres ── semantic graph + RLS    │
                │   + Neo4j (via cb-api at 8090)       │
                │   + Redis (job state)                │
                └──────────────────────────────────────┘

Question  ──►  CLI (curl) or UI
                ↓
                Java /v1/query  ──► Python /query
                                       ↓ retrieve
                                       Postgres + Neo4j
                                       ↓ assemble
                                       Anthropic (Sonnet)
                                       ↓ answer
                                    JSON back
```

If you keep this picture in your head, every "where did the data go?"
question has an obvious answer.

---

## After-demo cleanup

Stop services without losing data:
```bash
# Kill the four service terminals (Ctrl-C in each).
make -f Makefile.demo down-all
```

Reset everything (next demo starts clean):
```bash
make -f Makefile.demo wipe
make -f Makefile.demo down-all
docker volume rm cb-postgres-data cb-neo4j-data cb-qdrant-data 2>/dev/null
```

---

## What this guide does NOT do

- Implement Stage 1 ADRs 0011–0019 (those are designs; this guide tests the
  pre-Stage-1 pipeline that already exists).
- Implement ADR-0029 hardening fully (you have manual guards via
  `make guard`).
- Wire up the new MCP server (ADR-0019) — Claude Code integration comes
  after Stage 1 ships.
- Run the smart-zone assembler (ADR-0018) — `/query` uses the existing
  retrieve-then-prompt path.
- Touch the React frontend code — uses existing pages.

After a successful first demo, the next priorities (in order):
1. Implement ADR-0029 — reliability hardening.
2. Implement ADR-0011 (structural-first ordering) — the single biggest cost
   reduction lever.
3. Implement ADR-0030 — `/v1/ask` with Claude API for the headline demo
   chat experience.
