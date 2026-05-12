# Implementation Order V2 — seven new Claude Code sessions to close the benchmark gap

This document supersedes `IMPLEMENTATION-ORDER.md` for the work driven by `BENCHMARK-NETWORK-IQ.md` + `learnings.md`. The original order (ADR-0048 → 0052) is the harness migration; this V2 set (ADR-0055 → 0061) is **quality + extraction-coverage** work that lands ON TOP of the harness.

---

## The seven sessions

| Session | ADR | Title | Effort | Independent? |
|---|---|---|---|---|
| S55 | 0055 | Cross-file cross-cutting extraction (Stage 2.5) | 3d | YES (after 0048+0050) |
| S56 | 0056 | Verifier sub-agent + self-correction loop | 2d | YES |
| S57 | 0057 | Universal file extraction (docs/configs/infra/CI) | 3d | YES |
| S58 | 0058 | Generated-code & schema awareness (SQL DDL, OpenAPI, Proto, GraphQL, jOOQ Tables.java) | 4d | YES (best after 0057) |
| S59 | 0059 | Temporal ownership + domain inference passes | 3d | YES (after 0055 ships DomainEntity) |
| S60 | 0060 | BusinessContext v2 + 30-example few-shot library | 3d | YES |
| S61 | 0061 | Iterative exploration + 7 remaining Claude Code patterns | 5d | LAST (composes the rest) |

**Total**: 23 person-days of work. With 6 parallel Claude Code sessions running, real wall-clock time is **~5-7 days** until S61 starts, then 5 more days.

---

## Dependency graph

```
                  (already merged)
                  ┌─────────────────┐
                  │  ADR-0048/49/50 │   harness foundation
                  │  ADR-0051/52    │   (P1-P7 + extensions)
                  └────────┬────────┘
                           │
              ┌────────────┼─────────────────┐
              ▼            ▼                 ▼
        ┌─────────┐  ┌─────────┐       ┌─────────┐
        │ ADR-0055│  │ ADR-0056│       │ ADR-0057│
        │ cross-  │  │ verifier│       │ univ.   │
        │ file    │  │ loop    │       │ extract │
        └────┬────┘  └─────────┘       └────┬────┘
             │                              │
             ▼                              ▼
        ┌─────────┐                    ┌─────────┐
        │ ADR-0059│                    │ ADR-0058│
        │ temporal│                    │ schema  │
        │ +domain │                    │ aware   │
        └─────────┘                    └─────────┘
                ─────────┬─────────────
                         ▼
                    ┌─────────┐
                    │ ADR-0060│  BC v2 + few-shot
                    │ (any t) │  (loose dep on others)
                    └────┬────┘
                         ▼
                    ┌─────────┐
                    │ ADR-0061│  iterative exploration
                    │ LAST    │  (composes E1-E7)
                    └─────────┘
```

**Free-to-start in parallel right now**: 0055, 0056, 0057, 0060.
**Wait for 0055** (DomainEntity dataclass): 0059.
**Wait for 0057** (extractors/dispatch.py): 0058 (technically can start; just needs to coordinate the dispatcher append).
**Wait for everything**: 0061.

---

## File ownership table (zero merge conflicts)

| File | 0055 | 0056 | 0057 | 0058 | 0059 | 0060 | 0061 |
|---|---|---|---|---|---|---|---|
| `pipeline/cross_file_pass.py` | OWNS | — | — | — | — | — | — |
| `pipeline/idiom_detector.py` | OWNS | — | — | — | — | — | — |
| `pipeline/antipattern_detector.py` | OWNS | — | — | — | — | — | — |
| `pipeline/invariant_inferrer.py` | OWNS | — | — | — | — | — | — |
| `pipeline/domain_inferrer.py` | OWNS | — | — | — | shares | — | — |
| `pipeline/verifier_loop.py` | — | OWNS | — | — | — | — | — |
| `pipeline/verifier_deterministic.py` | — | OWNS | — | — | — | — | — |
| `agents/verifier_agent.py` | — | OWNS | — | — | — | — | — |
| `pipeline/self_correction.py` | — | OWNS | — | — | — | — | — |
| `extractors/` (NEW dir) | — | — | OWNS | extends | — | — | extends |
| `extractors/doc_extractor.py` | — | — | OWNS | — | — | — | — |
| `extractors/config_extractor.py` | — | — | OWNS | — | — | — | — |
| `extractors/manifest_extractor.py` | — | — | OWNS | — | — | — | — |
| `extractors/infra_extractor.py` | — | — | OWNS | — | — | — | — |
| `extractors/ci_extractor.py` | — | — | OWNS | — | — | — | — |
| `extractors/javadoc_extractor.py` | — | — | OWNS | — | — | — | — |
| `extractors/test_spec_extractor.py` | — | — | OWNS | — | — | — | — |
| `extractors/dispatch.py` | — | — | OWNS | append | — | — | — |
| `extractors/semantic_tags.py` | — | — | OWNS | — | — | — | — |
| `extractors/schema_sql.py` | — | — | — | OWNS | — | — | — |
| `extractors/jooq_binding.py` | — | — | — | OWNS | — | — | — |
| `extractors/schema_openapi.py` | — | — | — | OWNS | — | — | — |
| `extractors/schema_proto.py` | — | — | — | OWNS | — | — | — |
| `extractors/schema_graphql.py` | — | — | — | OWNS | — | — | — |
| `extractors/schema_resolver.py` | — | — | — | OWNS | — | — | — |
| `extractors/diagram_extractor.py` | — | — | — | — | — | — | OWNS |
| `pipeline/temporal_pass.py` | — | — | — | — | OWNS | — | — |
| `pipeline/git_blame_aggregator.py` | — | — | — | — | OWNS | — | — |
| `pipeline/risk_alert_detector.py` | — | — | — | — | OWNS | — | — |
| `pipeline/domain_inference_pass.py` | — | — | — | — | OWNS | — | — |
| `pipeline/onboarding_path_builder.py` | — | — | — | — | OWNS | — | — |
| `pipeline/few_shot_library.py` | — | — | — | — | — | OWNS | — |
| `pipeline/business_context_v2_prompt.py` | — | — | — | — | — | OWNS | — |
| `cli_helpers/upgrade_business_context.py` | — | — | — | — | — | OWNS | — |
| `agents/exploration_agent.py` | — | — | — | — | — | — | OWNS |
| `api/routes/query_reread.py` | — | — | — | — | — | — | OWNS |
| `api/routes/clarification.py` | — | — | — | — | — | — | OWNS |
| `mcp/tools/trace_exception.py` | — | — | — | — | — | — | OWNS |
| `mcp/tools/diff_since.py` | — | — | — | — | — | — | OWNS |
| `retrieval/cross_repo_similarity.py` | — | — | — | — | — | — | OWNS |
| **Append-only files** | | | | | | | |
| `models/entities.py` | append | append | append | append | append | append | append |
| `pipeline/orchestrator.py` | append | append | append | append | append | — | — |
| `pipeline/file_walker.py` | — | — | append | — | — | — | — |
| `api/routes/query.py` | — | append | — | — | append | — | append |
| `pipeline/context_synthesizer.py` | — | — | — | — | — | append | — |
| `mcp/server.py` | — | — | — | — | — | — | append |
| `pyproject.toml` | — | — | — | append | append | — | — |

**The append-only files are the danger zone.** Each ADR adds new dataclasses or registration calls; conflicts arise if two ADRs try to modify the SAME line in `models/entities.py` or `orchestrator.py`. Mitigation: each ADR's session adds its types in a clearly-bounded section with a comment header `# ── ADR-NNNN additions ──────────────────────────`. Manual rebase is trivial because the additions don't touch each other's lines.

---

## Recommended execution order (calendar week)

**Week 1 (parallel)** — fire 4 Claude Code sessions simultaneously:

1. **Session S55 (cross-file pass)** — biggest quality lift.
2. **Session S56 (verifier)** — biggest reliability lift.
3. **Session S57 (universal extraction)** — biggest coverage lift.
4. **Session S60 (BC v2 + few-shot)** — biggest answer-quality lift.

By end of week 1: 4 PRs in review, ~10 days of net work landed in 5 calendar days.

**Week 2 (sequenced because of light dependencies)**:

5. **Session S58 (schema awareness)** — start once S57's `extractors/dispatch.py` is reviewable (day 6 or 7).
6. **Session S59 (temporal + domain)** — start once S55's `DomainEntity` dataclass is reviewable.

By end of week 2: 6 of 7 PRs landed.

**Week 3**:

7. **Session S61 (iterative exploration)** — composes the rest. Lands in ~5 days.

**Total wall clock: ~3 weeks for all 7 PRs to merge.**

---

## How each session is launched

For each one, in a fresh Claude Code session:

```bash
cd /Users/chinmayjadhav/Documents/Claude/Projects/company-brain
claude code "Read docs/adrs/ADR-00NN-*.md and land that PR end-to-end. Use git worktree for parallel safety. Run the acceptance tests before opening the PR. The file ownership table is in docs/adrs/IMPLEMENTATION-ORDER-V2.md — strictly respect it."
```

Each session creates its own worktree:

```bash
git worktree add .claude/worktrees/adr-NNNN feature/adr-NNNN-...
```

Worktrees are independent working copies; parallel sessions can't corrupt each other's files. After each PR merges, `git worktree remove .claude/worktrees/adr-NNNN` cleans up.

---

## Acceptance — how to know you're done

Re-run `BENCHMARK-NETWORK-IQ.md` after all 7 PRs land. Pass-rate should move from **5% → 75%+**.

Per-ADR contributions to the pass rate:

| ADR | Benchmark questions improved | Pass-rate lift |
|---|---|---|
| 0055 | A1, A2, A5, B11, A14, B19 (cross-file patterns) | +10% |
| 0056 | A20, every "hallucinated SQL" failure (~15 questions partial) | +5% |
| 0057 | A11, C7, C8, C9, C10, C11, C12 (config + docs) | +12% |
| 0058 | A8, A9, A12, A17, B15, C19 (schema + DDL) | +12% |
| 0059 | A19, B6, C13, C4 (temporal + domain) | +8% |
| 0060 | A6, A19, A20, B14, A5 (BC v2 fields) | +10% |
| 0061 | B2, B3, B6, B16, B20, C5 (exploration + global queries) | +13% |

Sum: roughly 70% lift. Combined with the existing 5% baseline → **target ~75% benchmark pass rate**.

If after all 7 land the pass rate is below 60%, something else is broken — re-run the benchmark with verbose telemetry and add a new ADR for the gap.

---

## What to NOT do during this rollout

- **Do not skip ADR-0056 (verifier).** Without it, every other extraction improvement gets undermined by hallucinations. It's the quality floor.
- **Do not let 0057 + 0058 drift apart in design.** They share `extractors/` directory and the same registration pattern. If two agents work on them simultaneously, both should reference each other's `dispatch.py` registration shape so the merge is trivial.
- **Do not start 0061 early.** Its E1 (ExplorationAgent) needs the better-extracted graph from 0055 + 0057 + 0058 to actually be useful. Starting it on the current sparse graph means redoing it.
- **Do not optimise demos before all 7 land.** The benchmark itself is the measure; investor-demo prep waits.

---

## TL;DR for the founder

- **7 ADRs, 23 person-days of work, 3 calendar weeks** with 6 parallel Claude Code sessions.
- File-ownership table guarantees zero merge conflicts across sessions.
- Expected outcome: brain benchmark pass rate **5% → 75%**.
- The lob query, the architecture-explanation query, the bus-factor query, the "what database" query — all FAIL today; all PASS after this set lands.
- **After this lands AND demo verification (ADR-0054) is green, the brain is fundable-demo-ready.** Until then, prep the demo on canned questions and pre-extracted Stripe/Vercel/Anthropic-MCP fixtures per the SEED-FUNDING-PACKAGE plan.
