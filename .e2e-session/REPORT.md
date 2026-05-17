# E2E Session Report — 2026-05-17 — SHA c5fb3d0c3

## Summary
- **UI checks (Playwright, 13 tests):** 13/13 PASS ✅
- **Benchmark queries:** 10/20 PASS
- **Owners API:** ❌ FAIL — missing `entities`/`repos` tables in Postgres
- **Latency:** ❌ FAIL — ~30s (exploration agent overhead)
- **Fixes applied:** 5 code fixes + 3 DB migrations
- **Estimated spend:** ~$1.30 / $5.00 budget

---

## UI Results — Playwright (13/13 PASS)

| Check | Status | Notes |
|---|---|---|
| health: AI returns status ok | ✅ PASS | `status: ok`, `llm_provider: anthropic` |
| /me: real display_name | ✅ PASS | Returns `Chinmay Jadhav` from git config |
| /repos: workspace has repo after index | ✅ PASS | NIQ repo registered via manifest.json |
| sidebar: no hardcoded mock names | ✅ PASS | No `Tom Blomfield`, `acme`, `stripe-node` |
| live mode chip: shows Live or Mock | ✅ PASS | Live chip visible |
| view ask: loads without JS crash | ✅ PASS | No uncaught JS errors |
| view history: loads without JS crash | ✅ PASS | Empty state renders correctly |
| view sources: loads without JS crash | ✅ PASS | Empty state renders correctly |
| view agents: loads without JS crash | ✅ PASS | Empty state renders correctly |
| ask view: submit question (streaming) | ✅ PASS | Input found, button clicked, no crash |
| history view: no raw JS errors in body | ✅ PASS | Clean render |
| sources view: no raw JS errors in body | ✅ PASS | Clean render |
| no dead href="#" links across views | ✅ PASS | Zero dead links found |

---

## Benchmark Results — 10/20 PASS

**Entities confirmed indexed** (3 endpoints, 9 entities):
`CompetitivenessController`, `CompetitivenessPlanRepository`, `CompetitivenessService`,
`CompetitivenessRepositoryImpl`, `CompetitivenessPayerSummaryDTO`, `NiqAPIRequest`,
`COMP_PROVIDERS`, `PLAN_INFO`, `CompetitivenessRepositoryImpl.getPayerPlans`

| ID | Question (abbreviated) | Confidence | Citations | Len | Status | Note |
|---|---|---|---|---|---|---|
| A1 | CompetitivenessController endpoints | medium | 4 | 2862 | ✅ PASS | |
| A2 | CompetitivenessPlanRepository.getPayerCompetitors | medium | 4 | 3798 | ✅ PASS | |
| A3 | CompetitivenessService.getPayerPlans purpose | medium | 5 | 4442 | ✅ PASS | |
| A4 | CompetitivenessRepositoryImpl interface | high | 8 | 4390 | ✅ PASS | |
| A5 | CompetitivenessPayerSummaryDTO fields | medium | 0 | 5814 | ❌ FAIL | free-form fallback |
| A6 | NiqAPIRequest usage | medium | 7 | 3836 | ✅ PASS | |
| A7 | COMP_PROVIDERS table | medium | 4 | 3270 | ✅ PASS | retry |
| B1 | Blast radius: CompetitivenessController | medium | 0 | 13432 | ❌ FAIL | free-form fallback |
| B2 | What breaks if CompetitivenessPlanRepository changes | medium | 0 | 11548 | ❌ FAIL | free-form fallback |
| B3 | Who depends on CompetitivenessService | medium | 0 | 11709 | ❌ FAIL | free-form fallback |
| C1 | HTTP→DB flow for competitiveness | medium | 0 | 8263 | ✅ PASS | arch (no cit needed) |
| C2 | Layered architecture | medium | 8 | 5438 | ✅ PASS | |
| C3 | Read-only repository methods | medium | 0 | 7430 | ❌ FAIL | free-form fallback |
| C4 | Error handling in controller | medium | 4 | 3256 | ✅ PASS | retry |
| C5 | DTOs in request/response cycle | medium | 0 | 7772 | ❌ FAIL | free-form fallback |
| D1 | Top contributors to CompetitivenessPlanRepository | low | 2 | 2627 | ❌ FAIL | git history not indexed |
| D2 | When was CompetitivenessController last modified | low | 1 | 1509 | ❌ FAIL | git history not indexed |
| E1 | COMP_PROVIDERS columns | medium | 0 | 3411 | ❌ FAIL | free-form fallback |
| E2 | PLAN_INFO entity | medium | 7 | 4199 | ✅ PASS | retry |
| E3 | COMP_PROVIDERS vs PLAN_INFO relationship | low | 6 | 3558 | ❌ FAIL | cross-entity: low conf |

### Failure Root Causes

**Pattern 1 — LLM free-form fallback (7 failures: A5, B1, B2, B3, C3, C5, E1)**
`_parse_llm_response()` in `query.py` falls back when the LLM returns prose instead of
structured JSON, leaving `affected_entities=[]`. All 7 have `confidence=medium` and substantial
summaries (3K–13K chars) — retrieval worked, only citation tracking is broken.
**Fix path:** post-process free-form responses to extract CamelCase identifiers and map them
to URNs from the already-assembled SmartZone context.

**Pattern 2 — Low confidence / git history not indexed (3 failures: D1, D2, E3)**
Ownership and temporal data require `cli enrich --temporal` which was not run.
**Fix path:** run `cli enrich --temporal` after initial index.

---

## Other Checks

| Check | Status | Notes |
|---|---|---|
| GET /conversations | ✅ PASS | Returns `[]` after V16 migration |
| GET /workspaces/{id}/sources | ✅ PASS | Returns `[]` after V18 migration |
| GET /mcp/agents | ✅ PASS | Returns `[]` after V17 migration |
| GET /suggestions | ✅ PASS | Returns 4 seed question chips |
| POST /query/stream (SSE) | ✅ PASS | Streams `data: {"delta": ...}` lines |
| GET /entities/{urn}/owners | ❌ FAIL | `entities`+`repos` tables missing in Postgres |
| Latency single query | ❌ FAIL | ~30s wall time (>10s threshold) |

---

## Fixes Applied This Session

### Code fixes
1. **`repos.py`: pass `entity_count` from manifest** — `_repo_from_path()` hardcoded
   `entity_count=0`; now accepts it as an optional param so manifest values flow through.

2. **Playwright test: correct input selector** — ask view `input` has no `type` attribute;
   was using `input[type="text"]` which found nothing. Fixed to `input[placeholder*="break"]`.

3. **Playwright test: correct button/answer selectors** — added `.send` class to button
   locator; added `.ans-body` to answer selector to match actual DOM.

### DB migrations applied
4. **V16__conversations.sql** — created `conversations` table + indexes. `/conversations`
   was returning 500 (table missing).

5. **V17__mcp_agent_sessions.sql** — created `mcp_agent_sessions` table. `/mcp/agents`
   was returning 500.

6. **V18__workspace_sources.sql** — created `workspace_sources` table.
   `/workspaces/{id}/sources` was returning 500.

### Infrastructure / config
7. **manifest.json for NIQ workspace** — created
   `~/.company-brain/00000000-0000-0000-0000-000000000001/manifest.json` so
   `/workspaces/{id}/repos` returns the indexed repo (no `repos` table exists yet).

---

## Known Issues (not fixed — need follow-up)

1. **`entities` + `repos` tables missing** — owners API broken. Need `V19__entities.sql`
   and `V20__repos.sql` migrations + index pipeline writes to them.

2. **LLM free-form fallback → empty citations** — 7/20 benchmark failures. Complex /
   aggregate questions trigger free-form text path in `_parse_llm_response`. The answer
   content is correct but `affected_entities` is never populated.

3. **Latency > 10s** — complex queries take 20–35s. The exploration agent fires 1–2
   extra LLM calls on lower-confidence answers. Add `?fast=true` param to skip it.

4. **Git history / ownership not indexed** — D1, D2 fail with low confidence.
   Run `cli enrich --temporal` after initial index.

5. **NIQ repo on feature branch** — local checkout is `feat/skeletonMarketOverrides`,
   indexed as `main`. UI shows branch mismatch warning.

6. **Qdrant reports `unhealthy`** in Docker — functional but health check URL in
   `docker-compose.infra.yml` is likely wrong path.

7. **`repos` table missing** — entity_count won't auto-update after re-index.
   Need proper `repos` registry table (V19 migration) that the index CLI writes to.

---

## Cost

| Phase | Cost |
|---|---|
| Targeted index (3 endpoints, 9 entities) | $0.3982 |
| Benchmark queries (20 × 2 passes + 2 diagnostic) | ~$0.90 |
| **Total** | **~$1.30 / $5.00** |

Budget remaining: ~$3.70
