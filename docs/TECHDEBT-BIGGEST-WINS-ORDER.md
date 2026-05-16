# Tech Debt — Biggest Wins Order

**The honest answer first**: you cannot ship all 24 unshipped ADRs (0061-0084) before the seed pitch. Trying to is the wrong goal. The right goal is to ship the **6 things that get the seed pitch demoable + defensible**, defer everything else, and present the deferred items as the funded roadmap.

This doc gives:
1. Brutal three-tier categorization of every unshipped ADR (24 of them) + the P0 bug bundle
2. Top 6 "biggest wins" in execution order with rationale
3. Pointers to the implementation prompts (one per Tier-1 item) to run in parallel sessions

---

## Tier categorization (all 24 unshipped ADRs + P0 bugs)

### Tier 1 — Ship now (seed cycle, ~6-8 weeks total work)

These six unblock the demo and deliver the seed pitch. Everything else is non-essential to closing the seed round.

| # | Item | Effort | Impact | Why it's Tier 1 |
|---|---|---|---|---|
| 1 | **P0 BUG BUNDLE** (from E2E session) | 1 week | 10/10 | Demo currently broken — 0-tool extraction, citations empty, schema mismatch, 17-43s latency |
| 2 | **ADR-0061 P1** iterative exploration patterns | 1 week | 9/10 | Improves every demo answer; cheap; designed already |
| 3 | **ADR-0071 P1** frontend rebuild + brain wire-up | 2 weeks | 10/10 | No UI, no demo. The 5,252-LOC React prototype already exists; needs wiring |
| 4 | **ADR-0079 P1** persona templates framework | 2 weeks | 10/10 | The multi-persona pitch *is* the seed differentiator |
| 5 | **ADR-0082 P1** drift as a first-class entity | 1.5 weeks | 7/10 | Cheap; high VP appeal; demoable as a dashboard |
| 6 | **ADR-0064 P1** privacy + audit minimum | 2 weeks | 7/10 | Required by any real enterprise customer; can't skip |

**Total: 9.5 engineer-weeks**. Two engineers in parallel = ~5 calendar weeks. Six prompts to run; mostly parallel-safe.

### Tier 2 — Next batch (seed-to-Series-A, after seed close)

These ship after the seed lands. They're important — they're the Series-A flywheel proof — but they don't block the seed pitch.

| ADR | What | Why deferred |
|---|---|---|
| 0070 P1 | Notion connector + ingest pipeline | "Architecture extends" proof — strong Series-A demo, not seed-blocking |
| 0072 P1 | Contradiction detector + salience scorer | Hallucination reduction; nice-to-have for seed; mandatory for enterprise |
| 0073 P1 | Event-stream foundation (M1 + M2) | Big lift; underpins continuous freshness + multi-repo; defer until seed-funded |
| 0080 P1 | Velocity / estimation model | PM persona depth; can land in T2 once 0079 is live |
| 0083 P1 | Catalog evolution & curation | Governance plane; needed once T1 templates start evolving |
| 0084 P1 | Compression & expansion (cards + tiering) | Scale plane; needed once multi-source ingestion turns on (T2 0070) |
| 0062 | Ecosystem calibration packs | Universality fix; only needed when expanding beyond Java/Spring |
| 0063 | Convention inference augmentation | Messy-repo handling; deferred — pick a clean demo repo |

### Tier 3 — Defer to Series A and after (don't touch now)

These are real strategic bets but should not consume engineering during the seed cycle. Keep in design state; don't implement. Most are documented in `STRATEGIC-PIVOT-COMPANY-WIDE-BRAIN.md` as the post-Series-A roadmap.

| ADR | What | Why defer |
|---|---|---|
| 0065 | Multi-graph RRF fusion | Quality improvement; current retrieval acceptable for demo |
| 0066 | Experiential memory tier | Closes a research loop; high-effort; pre-revenue |
| 0067 | Brain evolution background process | Coupled with 0066 + 0083; full pipeline is post-Series-A |
| 0068 | Native agent framework integrations | Distribution play; matters at scale, not at seed |
| 0069 | companybrain-lite SQLite distro | Sales/distribution surface; not seed-blocking |
| 0073 P2/P3 | Branch overlays, federated multi-repo, full causal graph | Series-A engineering for multi-repo enterprise customers |
| 0074-0078 | Domain-Entity-First, Multi-Source Connectors, Cross-Source Entity Res, Source-Aware Permissions, Cross-Source Salience | All explicitly post-Series-A in pivot doc |
| 0079 P2/P3 | Multi-vertical bindings + LLM classifier + refinement loop | Built on T1 P1 foundation |
| 0080 P2/P3 | LLM ambiguity judge, automated calibration | After P1 lands and accumulates data |
| 0081 | Cost & spend connectors | CFO unlock; biggest engineering effort; Series-A required |
| 0082 P2/P3 | Drift forecasting, $-translation | Built on P1 entity |
| 0083 P2/P3 | Vertical-pack registry, customer marketplace | Distribution; post-Series-A |
| 0084 P2/P3 | Implicit edge inference, expander, multi-hop retrieval | The actual moat; multi-month research-grade work |

---

## The Top 6 in execution order

Recommended sequence — driven by dependencies, not just impact:

```
WEEK 1                  WEEK 2-3                   WEEK 4-5                    WEEK 6-7
├── P0 BUGS ────┐                                                                
│   1 engineer  │                                                                
│   (blocks all)│                                                                
│               │                                                                
└── ADR-0061 ───┼── ADR-0071 ──────────────┐                                    
    1 engineer  │   2 engineers (FE+BE)    │                                    
    (parallel)  │                          │                                    
                │                          │                                    
                ├── ADR-0064 ──────────────┼── ADR-0079 ──────────────┐         
                │   1 engineer             │   2 engineers            │         
                │                          │                          │         
                │                          │                          │         
                └── ADR-0082 ──────────────┴──────────────────────────┴── DEMO  
                    1 engineer                                                  
```

**Why this order**:

1. **P0 BUG BUNDLE FIRST** — non-negotiable. Demo broken without it. ~1 week. Blocks every other Tier-1 item from being demoable.

2. **ADR-0061** — `iterative exploration & additional Claude Code patterns`. Already designed. Cheap. Improves every answer the brain returns. No dependencies. Run in parallel with bug fixes (different engineer).

3. **ADR-0071** — `frontend rebuild + brain integration`. The 5,252-LOC React prototype exists at `/Users/chinmayjadhav/Documents/Company brain/`; it needs wiring to the brain backend. Without this there's nothing to show in a demo. Two engineers (one frontend, one backend wire-up) can finish in 2 weeks.

4. **ADR-0064 P1** — `privacy + audit layer minimum`. PII detection + typed TTLs + hash-chained audit log. Required by any real enterprise customer; the bar to even start a SOC2 conversation. 2 weeks. Parallel-safe.

5. **ADR-0079 P1** — `persona templates framework`. The multi-persona surface IS the seed pitch differentiator. Phase 1 is M1 schema + M3 router + M4 formatters + M6 seed templates for healthcare-RCM (since network-iq is the demo repo). Depends on bugs being fixed first; then 2 weeks.

6. **ADR-0082 P1** — `drift as a first-class entity`. Cheap (1.5 weeks), high VP appeal, demoable as a dashboard widget. Phase 1 is M1 schema + M2 nightly snapshot + M3 per-domain scoring + M6 lifecycle. Parallel-safe.

---

## Implementation prompts to run

One Sonnet implementation prompt per Tier-1 item. Each is self-contained, branch-scoped, with explicit file ownership and acceptance criteria. Run in parallel where dependencies allow.

| # | Item | Prompt file |
|---|---|---|
| 1 | P0 Bug Bundle | [SONNET-IMPLEMENTATION-PROMPT-P0-BUG-BUNDLE.md](./adrs/SONNET-IMPLEMENTATION-PROMPT-P0-BUG-BUNDLE.md) |
| 2 | ADR-0061 P1 | [SONNET-IMPLEMENTATION-PROMPT-ADR-0061-P1.md](./adrs/SONNET-IMPLEMENTATION-PROMPT-ADR-0061-P1.md) |
| 3 | ADR-0071 P1 | [SONNET-IMPLEMENTATION-PROMPT-ADR-0071-P1.md](./adrs/SONNET-IMPLEMENTATION-PROMPT-ADR-0071-P1.md) |
| 4 | ADR-0064 P1 | [SONNET-IMPLEMENTATION-PROMPT-ADR-0064-P1.md](./adrs/SONNET-IMPLEMENTATION-PROMPT-ADR-0064-P1.md) |
| 5 | ADR-0079 P1 | [SONNET-IMPLEMENTATION-PROMPT-ADR-0079-P1.md](./adrs/SONNET-IMPLEMENTATION-PROMPT-ADR-0079-P1.md) |
| 6 | ADR-0082 P1 | [SONNET-IMPLEMENTATION-PROMPT-ADR-0082-P1.md](./adrs/SONNET-IMPLEMENTATION-PROMPT-ADR-0082-P1.md) |

---

## Cost & schedule envelope

| Item | Engineer-weeks | Sonnet $ budget |
|---|---|---|
| P0 Bug Bundle | 1 | $20 |
| ADR-0061 P1 | 1 | $15 |
| ADR-0071 P1 | 2 | $30 (mostly UI; cheap) |
| ADR-0064 P1 | 2 | $25 |
| ADR-0079 P1 | 2 | $30 |
| ADR-0082 P1 | 1.5 | $20 |
| **Total** | **9.5** | **$140** |

Two engineers running parallel sessions = 5 calendar weeks to fully demoable. Single engineer sequential = 9-10 weeks.

---

## What to NOT do (avoid scope creep)

These are the most common ways this plan dies:

1. **Don't try to ship all of 0073** during seed cycle. Phase 1 alone is 3-4 weeks; full implementation is 12+ weeks. Defer.
2. **Don't add Notion connector now** even though the pivot doc said to. The seed pitch survives without it; the engineering cost is real (3-4 weeks for production-quality). Move to T2.
3. **Don't try to do persona templates for all six personas** in P1. Ship 2-3 (Developer + PM + VP) — those are the seed-pitch personas. CFO/CEO templates wait for T2/T3.
4. **Don't start ADR-0083/0084 yet.** They're the long-term moat but the foundation (catalog state machine + card schema) requires the entity catalog to be stable, which it isn't yet at multi-source.
5. **Don't refactor anything 0048-0060 ships unless a P0 bug forces it.** Those are the working brain. Let them be.
6. **Don't pursue ADR-0062/0063** unless a customer trial demands universality. Stay focused on the demo repo (network-iq-backend-java).

---

## Post-seed checkpoint

After Tier 1 ships and the seed closes, run this same exercise again with Tier 2:

- ADR-0070 P1 (Notion) — first non-code connector (Series-A flywheel proof)
- ADR-0072 P1 (contradiction + salience) — hallucination reduction
- ADR-0073 P1 (event-stream foundation) — multi-repo + freshness
- ADR-0080 P1 (velocity model) — PM depth
- ADR-0083 P1 (catalog governance) — required as templates evolve
- ADR-0084 P1 (cards + tiering) — required as multi-source scales

Tier 3 stays in design state until Series-A close.

---

## TL;DR

1. **Six prompts to run.** Two engineers, ~5 calendar weeks. Total Sonnet cost ~$140.
2. **Run in this order**: P0 bugs FIRST (blocks everything). Then ADR-0061 + 0064 + 0082 in parallel (small + independent). Then 0071 + 0079 (UI + persona pitch). Demo-ready at end.
3. **Defer the other 18 ADRs.** Group them into Tier 2 (post-seed) and Tier 3 (post-Series-A). Present T2/T3 as the funded roadmap in the seed pitch.
4. **Don't try to ship all 24.** That's the trap. The seed pitch is six things, not twenty-four.
