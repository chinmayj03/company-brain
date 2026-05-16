# Demo Priority Order — what to ship before the seed pitch

**Single question this doc answers**: of the 18+ ADRs in flight, which **MUST** ship before the seed demo, which **SHOULD**, and which **DEFER**? With a concrete week-by-week plan that gets you from today to investor-ready in **~3 weeks**.

**Constraint**: the demo must work flawlessly on Java/Spring repos (`network-iq-backend-java` + 1-2 famous OSS repos pre-extracted). Investor cares about: query quality, blast radius visualization, citations, MCP-into-Cursor moment. Investor does NOT care: framework breadth, brain-evolution background hygiene, lite distribution, CrewAI integration.

---

## The three buckets

### MUST ship — without these, the demo is fragile or fails (~4 weeks of work, parallelisable to ~10 days)

These are the ADRs whose failure or absence would cause the demo to fail OR materially weaken the investor pitch.

| Priority | ADR | Why MUST | Cost (days) |
|---|---|---|---|
| **P0** | **0054 — Demo Verification Gates** | Without this, you don't KNOW if the demo works; investor demos shouldn't be "fingers crossed" | 2 |
| **P0** | **0053 PR-A (prompt rewrites) + PR-B (verifier)** | Verifier kills the hallucinated-quotes class of failure, which has bitten us twice on the lob query | 6 |
| **P0** | **0055 — Cross-file cross-cutting pass** | The lob anti-pattern detection (16 DTOs use constant, 1 uses literal) is a marquee demo moment; doesn't work without this | 3 |
| **P0** | **0056 — Verifier loop + self-correction** | Same reason as PR-B; the demo MUST not hallucinate query_text or column references | 2 |
| **P0** | **0058 — Schema awareness (DDL + jOOQ)** | The lob query needs `DatabaseColumn` entities resolved from DDL; without this, "what tables have a lob column" answers wrong | 4 |
| **P0** | **0060 — BusinessContext v2 + few-shot library** | Generic "queries the database" `purpose` fields are demo-killers; investors notice instantly | 3 |
| **P0** | **0065 — Multi-graph RRF retrieval fusion** | 2 days work for the highest query-quality lift in the entire roadmap; no reason not to ship | 2 |
| **P0** | **NEW E2E test + fix session** | Find and fix actual pipeline failures BEFORE the investor sees them; uses real $5-10 budget | 1 |
| **P1** | **0061 — Iterative exploration (E1 ExplorationAgent only)** | When initial confidence < 0.6, agent re-explores; safety net for hard demo questions; ship ONLY E1, defer E2-E7 | 1.5 |
| **P1** | **0071 — Frontend rebuild + brain integration** | Investor demo IS the UI; the existing UI must support the 90-second demo script from SEED-FUNDING-PACKAGE | 5 |

**MUST total**: 29.5 person-days. With 4-6 parallel Claude Code sessions, **~7-10 calendar days**.

### SHOULD ship — strengthens the pitch but the demo survives without it (~3 weeks more, defer if no time)

Each of these answers a question an investor MIGHT ask but the demo doesn't depend on.

| Priority | ADR | Why SHOULD | Cost |
|---|---|---|---|
| **P2** | **0057 — Universal file extraction** | Lets you answer "what database is this?" / "what's the deployment story?" — strong answers, but you can workaround verbally | 3 |
| **P2** | **0059 — Temporal + domain inference** | Bus-factor + onboarding curriculum — Product 2 hook, makes the deck slide #4 stronger | 3 |
| **P2** | **0064 — Privacy & Audit Layer (PII detection only)** | If investor asks "what about secrets in code", say yes; defer typed TTLs + hash-chain to post-seed | 1.5 |
| **P3** | **0070 — PRD / document ingestion** (NEW; this doc proposes) | "Brain reads your PRDs too" is a wow moment but adds scope; ship ONLY if existing UI can show it | 3 |
| **P3** | **0066 — ExperientialMemory tier** | "The brain learns over time" is a moat-pitch slide but doesn't need to demo live | 3 |

**SHOULD total**: 13.5 person-days. With 3 parallel sessions, **~5 calendar days**.

### DEFER — post-seed entirely (don't do these until the round closes)

| Priority | ADR | Why DEFER |
|---|---|---|
| **D** | **0051 P5 / P6 / P7** (slash, marketplace, IDE polish) | Already covered by Wave 0; defer further polish |
| **D** | **0062 — Ecosystem calibration packs** | Java/Spring monoculture is fine for demo; ship after first non-Java customer signs |
| **D** | **0063 — Convention inference + augmentation + adaptive zone** | Demo repos are well-named; defer to first messy-repo customer |
| **D** | **0064 — Audit chain + retention sweeper** | Nobody asks investors to demo SOC2 audit logs; ship before first enterprise pilot |
| **D** | **0067 — Brain evolution background process** | Operational hygiene; pays off after 6 months of customer data |
| **D** | **0068 — Native langchain/crewai/autogen integrations** | Powerful GTM tool but distracts from "close the round" |
| **D** | **0069 — companybrain-lite SQLite distribution** | Series-B-prep; bottom-up motion not yet relevant |

**Save by deferring these**: ~32 person-days. **Use that time for**: customer development calls, deck iteration, demo rehearsals.

---

## Week-by-week plan (the 3-week sprint to demo-ready)

### Week 1 — fix the foundation (4 parallel Claude Code sessions)

| Session | Workstream | Day-by-day target |
|---|---|---|
| W1-A | **0054 demo verification gates** + **NEW e2e test + fix session ($5-10 budget)** | Mon: scaffold gates. Tue: run e2e on fixture. Wed: fix top 3 issues. Thu-Fri: stabilise |
| W1-B | **0053 PR-A (prompt rewrites) + PR-B (verifier)** | Mon-Tue: rewrite SpecialistAgent + ContextAgent + ContextSynthesizer prompts. Wed: golden-output regression suite. Thu-Fri: verifier deterministic + sub-agent modes |
| W1-C | **0055 cross-file pass** + **0056 verifier loop** | Mon-Tue: Pattern + SharedInvariant entities + idiom detector. Wed: anti-pattern detector (lob smoke test passes). Thu-Fri: verifier loop wraps everything |
| W1-D | **0060 BusinessContext v2 + few-shot library** | Mon: add 7 new typed fields. Tue-Thu: build 30-example library. Fri: golden fixtures + acceptance |

**End of Week 1**: brain answers the lob query correctly with citations + verifier-approved quotes. BENCHMARK pass rate: 5% → 30%+.

### Week 2 — schema + retrieval (5 parallel sessions)

| Session | Workstream | Day-by-day |
|---|---|---|
| W2-A | **0058 schema awareness (SQL DDL + jOOQ binding)** | Mon-Wed: SQL chunker + jOOQ Tables.java parser. Thu-Fri: cross-edges resolver (lob→DatabaseColumn) |
| W2-B | **0065 multi-graph RRF fusion** | Mon-Tue: 5 rankers + RRF + intent classifier. Wed: integration into SmartZone. Thu-Fri: A/B vs single-source baseline |
| W2-C | **0061 — E1 only (ExplorationAgent)** | Mon-Wed: agent + 6 tools. Thu-Fri: trigger logic when confidence < 0.6 |
| W2-D | **0064 PII detection (M1 only)** | Mon-Tue: regex catalog + scrubber + entity flag. Wed: wire into orchestrator. Thu-Fri: tests + polish |
| W2-E | **0071 frontend rebuild — Phase 1** | Mon-Tue: blast-radius react-flow visualization. Wed-Thu: side-by-side ChatGPT comparison panel. Fri: 90-second demo wiring |

**End of Week 2**: BENCHMARK pass rate: 30% → 65%+. Lob query is bulletproof. UI has the wow-moment visual.

### Week 3 — polish + rehearsal (2-3 parallel sessions + founder time)

| Session | Workstream | Day-by-day |
|---|---|---|
| W3-A | **0057 universal file extraction (limited scope: configs + Dockerfile + READMEs ONLY; defer the rest)** | Mon-Tue: 3 extractors. Wed: semantic_tags catalog. Thu-Fri: tests |
| W3-B | **0059 temporal ownership + domain inference (key features only — bus_factor + Payer/Plan/Provider DomainEntities for the demo deck slide)** | Mon-Wed: git blame + temporal pass. Thu-Fri: domain inference one-shot |
| W3-C | **0071 frontend rebuild — Phase 2 + 0070 PRD ingestion (defer if no time)** | Mon-Tue: time-travel slider + cost counter UI. Wed: PRD ingestion if there's room. Thu-Fri: polish + rehearse |
| Founder | **Demo prep + customer dev calls** | Mon: pre-extract 3 famous Java/Spring repos. Tue-Wed: rehearse 90-second demo 20× until perfect. Thu-Fri: send 30 cold emails (per SEED-FUNDING-PACKAGE) |

**End of Week 3**: demo is rehearsed. UI is polished. BENCHMARK pass rate: 65% → 75%+. Investor meetings begin.

---

## Critical-path Gantt (visual)

```
Week 1                Week 2                Week 3
─────────────────────────────────────────────────────
M T W T F             M T W T F             M T W T F
─────────────────────────────────────────────────────
[0054 verify        ] [                                   ]
[ NEW e2e test+fix  ]
[0053 PR-A + PR-B          ]
                      [0058 schema awareness         ]
                      [0065 RRF fusion          ]
                      [0061 ExplorationAgent      ]
                      [0064 PII detection only   ]
                      [0071 UI Phase 1                    ]
                                            [0057 limited       ]
                                            [0059 limited       ]
                                            [0071 UI Phase 2    ]
                                            [Founder: rehearse + cold emails]
─────────────────────────────────────────────────────
[0055 + 0056 cross-file + verifier   ]
[0060 BusinessContext v2 + few-shot library]
─────────────────────────────────────────────────────
                                            [Investor meetings start]
```

---

## What you're trading off by following this plan

**You GAIN**:
- Demo-ready brain in ~3 weeks
- Pass rate 5% → 75%+ on the BENCHMARK
- Lob query, blast radius visual, MCP-into-Cursor moment all working
- Time for 30 cold emails + 5-9 booked investor calls before round closes

**You LOSE** (deferring these is a strategic bet):
- Multi-language support (Java/Spring only at demo time)
- Compliance posture (PII detection yes; full audit chain no)
- Bottom-up developer adoption (no companybrain-lite yet)
- Agent ecosystem GTM (no langchain/crewai integrations yet)
- Operational long-running hygiene (no brain-evolution scheduler)
- Messy-repo handling (cherry-picked demo repos avoid this)

These are all **post-seed** — don't ship them before the round closes. Use the time saved for customer development.

---

## Risk register for the 3-week sprint

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| W1-C cross-file pass takes longer than 5 days | Medium | High (blocks demo wow moment) | Have founder shadow this session daily; cut scope to ONLY anti-pattern detection if running over |
| Verifier (W1-B) flags too many entities as hallucinated, brain returns sparse answers | Medium | Medium | Calibrate confidence thresholds during W2; can soft-launch verifier as warning-only initially |
| Schema extractor (W2-A) doesn't find jOOQ Tables.java in the demo repo | Low | High | Pre-verify by manually checking `find target/generated-sources -name 'Tables.java'` in network-iq-backend-java |
| RRF fusion (W2-B) makes some questions WORSE not better (rare but possible) | Medium | Medium | A/B telemetry; can disable per-question via flag |
| UI rebuild (W2-E + W3-C) takes 10 days instead of 7 | High | High (no demo without UI) | Cut Phase 2 features to time-travel slider + cost counter ONLY; defer everything else |
| Real e2e test session burns through $5-10 quickly without finding root causes | Medium | Low | Cap at $5; if root causes unclear, fall back to unit tests + manual debugging |
| BENCHMARK pass rate stalls at <60% after Week 2 | Low | High | Diagnostic deep-dive before Week 3; may need to add a fix-the-bug session |

---

## Specific questions you should answer in Week 0 (this Friday)

Before Monday's parallel sprint kicks off:

1. **Which 2 famous Java/Spring repos to pre-extract for the demo?** (I'd recommend `spring-projects/spring-petclinic` + `apache/dubbo` — both well-known, well-named, demo-friendly.)
2. **Which version of the existing UI is the baseline?** (`/Users/chinmayjadhav/Documents/Company Brain` per your message — confirm the path; if separate from `company-brain-frontend/`, mount it so I can see the prototype.)
3. **What's the cost ceiling for a single full-repo extraction during dev?** (Recommend $5/extraction during W1, $10 during W2 once costs settle.)
4. **Investor meetings: when's the first one?** Target it for end-of-Week-3. If sooner, compress to fewer features; if later, add 2-3 SHOULD items.
5. **Are you running these 4-6 sessions yourself in parallel, or do you have someone else helping?** Determines max parallelism.

---

## TL;DR for the founder

Three weeks. **10 ADRs in MUST**, **5 in SHOULD (cut if no time)**, **7 in DEFER (don't touch)**. Critical path is W1-C (cross-file pass) + W2-A (schema) + W2-E (UI Phase 1) — if any of those slip, the demo slips. Everything else is parallel-safe.

After Week 3:
- Brain answers the lob query with citations + verifier-approved quotes
- UI shows blast-radius visualization + side-by-side vs ChatGPT
- MCP server hooks Cursor for the strategic-flag demo moment
- Pre-extracted famous Java/Spring repos demoable in 90 seconds
- 30 cold emails sent, 5-9 investor meetings booked

That's the package. Ship it.
