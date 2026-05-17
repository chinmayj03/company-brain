# Claude Code Takeover Prompt — V2 Build, End-to-End

**Drop this prompt into Claude Code as your operating brief. It is self-contained: read it, verify state, pick the next item, execute. No further orchestration prompts needed.**

You are taking over the company-brain V2 build. Your job is to ship 45 sub-sessions across 3 Wave-gate PRs to `main` over ~5.5 months, with discipline (spec-driven, surgical changes, evidence-or-it-didn't-happen) and aggressive scope control (no research-grade fine-tuning; no scope creep).

---

## 0 — Mission

Ship company-brain V2: a company-wide knowledge brain where code is the wedge and the architecture extends to all enterprise sources (Notion, Slack, Confluence, Salesforce, calls, email). Per-company tuning is the moat. The seed pitch is "best code memory; same architecture extends to your whole company." The Series-A pitch is "Glean is enterprise search; we're enterprise reasoning."

You will land:
- **Tier 1** (seed window, week 0-9): demo-ready brain with persona answers + frontend wired + privacy + drift + event-stream substrate + source registry
- **Wave A2 + Wave B2** (week 10-17): deep code (SCIP, Joern, blast-radius v2, LazyGraphRAG, CoVe, calibrator, LTR) + breadth (Slack/Confluence/Salesforce + permissions/salience/provenance + cross-source personas)
- **Wave B3** (week 18-24): calls + email + source health + non-code quality framework + cross-source multi-hop + per-persona source-mix budgets
- **CUT (don't build)**: Wave A3 research-grade items (speculative decoding optional; HippoRAG, AGSER, embedding fine-tune, persona auto-variants all CUT)

Three Wave-gate PRs to main: Week 9, Week 17, Week 24. ~$1,165 total Sonnet cost. ~69 engineer-weeks (24 calendar weeks with 2 engineers).

---

## 1 — Read these first, in this order

Before any code action, read every file listed. They are the operating context.

**Vision + strategy** (read once on session start):
1. `docs/REBALANCING-V2-CODE-IS-THE-WEDGE.md` — why two tracks
2. `docs/STRATEGIC-PIVOT-COMPANY-WIDE-BRAIN.md` — the product framing
3. `docs/LEAN-PLAN-RECONCILED-WITH-EXISTING-ADRS.md` — the actual plan (THIS is the source of truth)
4. `docs/ARCHITECTURE-V2-QUALITY-AND-DOMAIN-TUNING.md` — the 5-pillar V2

**Process** (read once):
5. `docs/SPEC-DRIVEN-DEV-FRAMEWORK.md` — gate discipline (SPECIFY → PLAN → TASKS → IMPLEMENT)

**Orchestration** (read once):
6. `docs/MASTER-ORCHESTRATOR-PROMPT.md` — DAG, parallelism, branching options

**ADRs (read on-demand as you start each sub-session)**:
- Foundation: `docs/adrs/ADR-0001*.md` through `ADR-0073*.md` for context
- User's frontend ADRs (canonical 0072-0076): `ADR-0072-frontend-product-completion-apis.md`, `ADR-0073-frontend-demo-liveup.md`, `ADR-0074-source-registry-and-ingestion-pivot.md`, `ADR-0075-ux-navigation-and-product-surface-redesign.md`, `ADR-0076-frontend-rendering-and-library-architecture.md`
- Architecture V2: `ADR-0085-architecture-v2-quality-and-per-company-tuning.md`
- Persona / drift / templates: `ADR-0079`, `ADR-0080`, `ADR-0082`, `ADR-0083`, `ADR-0084`
- (My proposed 0072-0078 renumbered to 0089-0095; design them inline when needed per Wave prompts)

**Wave implementation prompts (your operating manuals)**:
- `docs/adrs/SONNET-IMPLEMENTATION-PROMPT-P0-BUG-BUNDLE.md`
- `docs/adrs/SONNET-IMPLEMENTATION-PROMPT-ADR-0061-P1.md`
- `docs/adrs/SONNET-IMPLEMENTATION-PROMPT-ADR-0064-P1.md`
- `docs/adrs/SONNET-IMPLEMENTATION-PROMPT-ADR-0071-P1.md` (note: superseded by user's ADRs 0072-0076)
- `docs/adrs/SONNET-IMPLEMENTATION-PROMPT-ADR-0079-P1.md`
- `docs/adrs/SONNET-IMPLEMENTATION-PROMPT-ADR-0082-P1.md`
- `docs/adrs/SONNET-IMPLEMENTATION-PROMPT-V2-WAVE-1.md`
- `docs/adrs/SONNET-IMPLEMENTATION-PROMPT-V2-WAVE-2.md`
- `docs/adrs/SONNET-IMPLEMENTATION-PROMPT-V2-WAVE-B1.md`
- `docs/adrs/SONNET-IMPLEMENTATION-PROMPT-V2-WAVE-B2.md`
- `docs/adrs/SONNET-IMPLEMENTATION-PROMPT-V2-WAVE-B3.md`
- (Wave-3 prompt exists at `SONNET-IMPLEMENTATION-PROMPT-V2-WAVE-3.md` but those sub-sessions are CUT — only A3.1 optional)

---

## 2 — Current state — verify on takeover

Run these checks before any other action. They establish where you actually are vs. where the docs say you are.

```bash
# Where is main?
git log --oneline main | head -20

# What's been shipped recently?
cat docs/SHIP-LOG.md 2>/dev/null || echo "SHIP-LOG.md does not exist — CREATE IT"

# What branches exist?
git branch -a | grep -E "feature/(adr-|v2|fix/p0)" | sort

# What ADRs exist?
ls docs/adrs/ADR-*.md | sort

# Are the P0 bugs from the E2E session still present?
cd companybrain && python -m companybrain.cli --help 2>&1 | head -5
# Run one demo query and check: cost_usd, cited_entity_urns, latency, confidence shape

# Frontend state
ls -la "/Users/chinmayjadhav/Documents/Company brain" 2>/dev/null  # prototype
ls -la frontend/ 2>/dev/null  # wired version, if exists
```

Map the state of every Tier-1 item, every Wave A1 sub-session, every Wave B1 sub-session against the canonical list in `LEAN-PLAN-RECONCILED-WITH-EXISTING-ADRS.md` Section 4. Produce a status table and write it to `docs/STATUS-WEEK-N.md` where N is the calendar week.

If `SHIP-LOG.md` doesn't exist, **create it now** with a row per shipped ADR (compute by grepping git log for ADR numbers + verifying artifacts exist).

---

## 3 — The 45 sub-sessions (canonical list, lean)

Source of truth: `LEAN-PLAN-RECONCILED-WITH-EXISTING-ADRS.md` Section 4.

```
TIER 1 (seed window, week 0-9) — 11 items
─────────────────────────────────────────
T1.1   P0 Bug Bundle                                                    1w   $20
T1.2   ADR-0061 P1 iterative exploration                                1w   $15
T1.3   ADR-0073 (user) Frontend Demo Live-Up                           0.5w  $15
T1.4   ADR-0072 (user) Frontend Product Completion APIs                1.5w  $20
T1.5   ADR-0074 (user) Source Registry Pivot                            2w   $30
T1.6   ADR-0075 (user) UX Navigation Redesign                          1.5w  $25
T1.7   ADR-0076 (user) Frontend Rendering & Library Architecture        2w   $25
T1.8   ADR-0064 P1 Privacy + Audit                                      2w   $25
T1.9   ADR-0079 P1 Persona Templates (3 personas)                       2w   $30
T1.10  ADR-0082 P1 Drift Entity                                        1.5w  $20
T1.11  ADR-0090 P1 Event-Stream M1+M2 (was ADR-0073 mine)               2w   $50

TRACK A WAVE 1 (parallel with T1) — 8 items
─────────────────────────────────────────
A1.1   SQL Deep Extractor (sqlglot + tree-sitter embedded scan)         2w   $25
A1.2   Hybrid Retrieval (BM25+dense+RRF+BGE)                            2w   $20
A1.3   Anthropic Prompt Caching + GPTCache                              1w   $10
A1.4   Verbalized Confidence + Multi-Signal Aggregator                  1w   $15
A1.5   Streaming + Parallel Retrieval                                  0.5w  $10
A1.6   Glossary Auto-Discovery                                         1.5w  $20
A1.7   Few-Shot Bank                                                    1w   $15
A1.8   Quality Regression Harness                                      0.5w  $10

TRACK B WAVE 1 (parallel with T1) — 4 items
─────────────────────────────────────────
B1.1   ADR-0091 (was 0074-mine) Domain-Entity-First framing            0.5w  $10
B1.2   ADR-0092 (was 0075-mine) Connector Framework (extends ADR-0074)  1w   $15
B1.3   ADR-0093 (was 0076-mine) Cross-Source Entity Resolution P1       2w   $40
B1.4   Notion connector (built on ADR-0074 + ADR-0092)                  2w   $40

═══════════════════════════════════════════════════════════
  GATE 1 (week 9) — release/v2-seed-window → PR to main
═══════════════════════════════════════════════════════════

TRACK A WAVE 2 (post-seed, week 10-17) — 9 items
─────────────────────────────────────────────
A2.1   SCIP Indexers (Java only in Wave 2)                            1.5w   $20
A2.2   Stack Graphs (Python + JS)                                     1.5w   $20
A2.3   Joern CPG (critical paths)                                       2w   $30
A2.4   Blast Radius V2 (annealed + dataflow)                          1.5w   $20
A2.5   LazyGraphRAG Layer                                               2w   $25
A2.6   Chain-of-Verification (CoVe) — persona-gated                     1w   $20
A2.7   Per-Workspace Confidence Calibrator                            1.5w   $25
A2.8   Per-Workspace Reranker LTR                                       2w   $30
A2.9   ADR-0066 Experiential Memory + ADR-0067 Brain Evolution        1.5w   $25
       (needed by A2.7/A2.8; was deferred — moved into A2)

TRACK B WAVE 2 (parallel with A2, week 10-17) — 7 items
─────────────────────────────────────────────────────
B2.1   Slack Connector                                                  2w   $40
B2.2   Confluence Connector                                           1.5w   $30
B2.3   Salesforce Connector                                             2w   $40
B2.4   ADR-0094 (was 0077-mine) Source-Aware Permissions              1.5w   $30
B2.5   ADR-0095 (was 0078-mine) Cross-Source Salience                 1.5w   $25
B2.6   ADR-0086 Cross-Source Provenance + Citation                      1w   $20
B2.7   Persona Answers Span Sources                                     2w   $40

═══════════════════════════════════════════════════════════════════════
  GATE 2 (week 17) — release/v2-seed-to-series-a → PR to main
═══════════════════════════════════════════════════════════════════════

TRACK A WAVE 3 (post-Series-A) — CUT (all sub-sessions are research-grade)
──────────────────────────────────────────────────────────────────────
A3.1   Speculative decoding                          OPTIONAL — only if P50 must be <2s
A3.2   HippoRAG                                      CUT — LazyGraphRAG sufficient
A3.3   AGSER                                         CUT — CoVe sufficient
A3.4   Per-workspace embedding fine-tune             CUT — calibrator + LTR sufficient
A3.5   Persona auto-variants                         CUT — manual curation sufficient

TRACK B WAVE 3 (Series-A bucket, week 18-24) — 6 items
────────────────────────────────────────────────────
B3.1   Call-Transcript Connector (Fireflies/Otter/Gong)                 2w   $40
B3.2   Email Connector (Gmail/Outlook)                                  2w   $40
B3.3   ADR-0087 Source Health & Freshness Monitoring                  1.5w   $30
B3.4   ADR-0088 Non-Code Extraction Quality Framework                   2w   $40
B3.5   Cross-Source Multi-Hop (LazyGraphRAG only; no HippoRAG)          2w   $40
B3.6   Per-Persona Source-Mix Budgets                                 1.5w   $30

═══════════════════════════════════════════════════════════
  GATE 3 (week 24) — release/v2-series-a-demo → PR to main
═══════════════════════════════════════════════════════════
```

**TOTAL: 45 sub-sessions, 69 engineer-weeks, $1,165 Sonnet cost.**

---

## 4 — Your action loop (run this for every session)

```
LOOP:
  1. STATE CHECK
     - git status; git log --oneline main | head -5
     - cat docs/STATUS-WEEK-N.md (if exists)
     - Identify completed / in-progress / queued items
     
  2. PICK NEXT
     - From queued items, pick one whose dependencies are ALL in completed
     - Dependency DAG: see MASTER-ORCHESTRATOR-PROMPT.md Section "Dependency DAG"
     - Tier 1 items are dispatched first; A1/B1 in parallel with T1; A2/B2 after Gate 1; B3 after Gate 2
     - Max 6 concurrent in-flight branches (parallelism cap)
     
  3. LOAD CONTEXT FOR PICKED ITEM
     - Read the sub-session's prompt file (canonical list in Section 1 above)
     - Read the referenced ADR (or design it inline if the sub-session creates the ADR)
     - Read any other ADRs the sub-session "Builds on"
     
  4. SPEC-DRIVEN GATES (per SPEC-DRIVEN-DEV-FRAMEWORK.md)
     - Create specs/<sub-session-slug>/00-spec.md (problem, outcomes, non-goals, success criteria)
     - Create specs/<sub-session-slug>/01-plan.md (architecture, touched surfaces, rollback)
     - Create specs/<sub-session-slug>/02-tasks.md (each task ≤0.5d, acceptance criteria, rollback)
     - DO NOT skip gates. If you can't fill the spec, the item is under-defined; STOP and report.
     
  5. BRANCH + IMPLEMENT
     - git checkout -b feature/<sub-session-id> from main (or from the Wave's release branch if mid-Wave)
     - For each task: TDD — write the failing test first, then implement
     - Per acceptance criteria: capture evidence (test output, screenshot, log)
     - Surgical-change discipline: every touched file must be in 01-plan.md's "touched surfaces" list
     
  6. ACCEPTANCE GATE
     - All tests pass
     - All acceptance criteria evidence captured
     - PR description has the spec link + per-criterion evidence
     - Self-review: anti-rationalization check ("does this PR description say 'should work' anywhere?")
     - If self-review fails, fix before opening PR
     
  7. OPEN PR + REQUEST REVIEW
     - PR target: release/v2-<wave-name> branch (NOT main directly)
     - Tag the engineering lead human reviewer
     - Wait for human approval before merging
     
  8. MERGE + UPDATE SHIP-LOG
     - Once approved, merge sub-session branch to Wave's release branch
     - Append entry to docs/SHIP-LOG.md: timestamp + sub-session-id + commit hash + evidence link
     - Update docs/STATUS-WEEK-N.md
     
  9. WAVE-GATE CHECK
     - If all sub-sessions in current Wave are completed:
       - Run full integration test suite against release/v2-<wave>
       - Run quality regression harness (tests/quality/)
       - Verify Wave-specific quality targets met
       - If all pass: open release-branch → main PR with consolidated description
       - If any fail: flag for rework, do NOT merge release branch

  10. LOOP
```

---

## 5 — Branching + PR strategy (Option B — recommended)

```
main
 ├─ release/v2-seed-window               # Wave T1 + A1 + B1
 │   ├─ feature/T1.1_p0_bugs (PR)        → merged into release/v2-seed-window
 │   ├─ feature/T1.2_adr0061 (PR)        → merged
 │   ├─ ... (17 more T1+A1+B1 PRs)       → merged
 │   └─ release/v2-seed-window (PR)      → merged to main at GATE 1
 │
 ├─ release/v2-seed-to-series-a          # Wave A2 + B2
 │   ├─ feature/A2.1_scip (PR)           → merged into release/v2-seed-to-series-a
 │   ├─ ... (15 more A2+B2 PRs)          → merged
 │   └─ release/v2-seed-to-series-a (PR) → merged to main at GATE 2
 │
 └─ release/v2-series-a-demo             # Wave B3
     ├─ feature/B3.1_calls (PR)          → merged
     ├─ ... (5 more B3 PRs)              → merged
     └─ release/v2-series-a-demo (PR)    → merged to main at GATE 3
```

Per-sub-session PRs are reviewable (small diffs). Release-branch-to-main PRs at each Gate are the "consolidated" PRs aligned with funding events. User asked for "one consolidated PR to main" — Option A (single 45-sub-session mega-PR) is operationally bad practice; **use Option B** (three per-Wave consolidated PRs).

---

## 6 — Quality gates per Wave

Before merging any release branch to main:

```
GATE 1 (week 9) — Seed Window
- SQL coverage > 75% (was ~15%)
- P50 query latency < 3s (was 17s); P90 < 8s (was 43s)
- Citations: ≥ 1 per answer (was < 1)
- Confidence: distribution moves; ≥ 20% reach "high"
- Frontend: demo flow end-to-end (Repos → Brain Browser → Query → Drift → CitationsPanel renders Markdown)
- Privacy: PII detector flags ≥ 90% of synthetic test PII
- ≥ 1 non-code source (Notion) live and queryable
- Quality regression harness gates CI

GATE 2 (week 17) — Seed-to-Series-A
- SQL coverage > 90% (Joern catches dynamic)
- Blast radius recall > 80%
- Multi-hop accuracy > 60%
- ECE per workspace < 0.10
- Hallucination rate -40% on adversarial set
- 4 non-code sources live (Notion + Slack + Confluence + Salesforce)
- Permissions: same query with two user identities returns different evidence
- Cross-source persona answers: CS persona traces customer escalation across Slack + SF + Notion + code

GATE 3 (week 24) — Series-A Demo
- 6 non-code sources live (+ Calls + Email)
- Per-source extraction quality measured and gated
- Source health monitored; staleness warnings in answers
- Cross-source multi-hop ≥ 65% accuracy
- Per-persona source-mix budgets tuned per workspace
- CFO-persona Series-A demo question works end-to-end with 5+ sources cited

If a Gate fails, do not merge to main. Rework, re-test, re-gate.
```

---

## 7 — Anti-patterns (REJECT these; do NOT do)

```
× Trying to ship all 24 unshipped ADRs (impossible; not aligned with V2 scope)
× Shipping Wave A3 research-grade items (CUT per user direction)
× Ship LazyGraphRAG before A1.2 hybrid retrieval is stable (dependency violation)
× Run Joern inline on query path (always background — Wave A2.3 acceptance)
× Enable per-workspace calibrator before 50 labels (overfit; use global default until threshold)
× Enable per-workspace LTR before 200 labels (same)
× Enable CoVe for Dev/PM personas by default (latency cost; persona-gated to CS/CFO/CEO only)
× Refactor adjacent code in a PR not justified by acceptance criteria (surgical-change discipline)
× Combine multiple sub-sessions into one PR (each sub-session = own PR)
× Skip spec-driven gates ("it's obvious") — if it's obvious, the spec takes 20 minutes
× Mark a task complete without evidence in PR description
× Approve your own PR (single-author + single-reviewer = blocker)
× Add Notion connector outside of Wave B1 (don't reorder; dependency violation)
× Build distribution/marketplace work (companybrain-lite ADR-0069, agent frameworks ADR-0068) until post-Series-A
× Tune confidence calibrator per source-pair before Wave B2 (data not available yet)
× Pursue ADR-0062/0063 (ecosystem packs, convention inference) until customer demand
```

---

## 8 — Budget enforcement

```
Per-sub-session: budget specified in the LEAN-PLAN list (Section 3 above)
Hard cap: per-sub-session budget × 1.5 (50% buffer). Beyond cap → STOP, report, request re-scoping.

Per-Wave totals:
- GATE 1 (T1 + A1 + B1):                ~$505
- GATE 2 (A2 + B2):                     ~$440
- GATE 3 (B3 + optional A3.1):          ~$220 (+ $40 if A3.1)
- Spec-driven overhead (specs + reviews): ~10% of above
- Quality harness + integration tests:    ~5% of above

Total budget cap: $1,300. If on track to exceed, STOP and report rather than ship lower quality.
```

---

## 9 — Spec-driven discipline (non-negotiable)

Every sub-session creates:

```
specs/2026-Q<n>-<wave>-<sub-session-slug>/
├── 00-spec.md             # problem, outcomes, non-goals, success criteria, assumptions, risks
├── 01-plan.md             # architecture diagram, touched surfaces, dependencies, phasing, rollback
├── 02-tasks.md            # tasks (≤0.5d each), acceptance criteria, rollback per task
├── 03-evidence/           # PR links, test outputs, screenshots, perf measurements
└── 04-retrospective.md    # after merge: what went well, what surprised, what to apply next
```

A sub-session that doesn't have all five files at merge time is INCOMPLETE — even if code shipped.

Lightweight version (PR-description-only spec) is allowed for sub-sessions ≤1 day of work (typo fix, version bump, copy edit). T1.3 ADR-0073 user (4-6h, branched) qualifies for lightweight; everything else uses the full folder.

---

## 10 — Status reporting

Weekly:

```
docs/STATUS-WEEK-<N>.md:
- Wave progress: T1 = X/11 done; A1 = X/8; B1 = X/4; etc.
- Sub-sessions in flight (with owner + ETA)
- Sub-sessions blocked (with blocker)
- Cost burn this week vs budget
- Quality metrics this week (from quality harness)
- Risks raised / mitigations
- Next week's pick list (next 3-6 sub-sessions)
```

---

## 11 — Definition of done (entire V2 build)

- All 45 sub-sessions merged (lean plan)
- Three Wave-Gate PRs merged to main (week 9 / 17 / 24)
- Quality targets (ADR-0085) met:
  - SQL coverage > 90%
  - Blast radius recall > 80%
  - Multi-hop accuracy > 65%
  - P50 < 3s (cold cache); < 1s (warm cache)
  - P90 < 8s
  - ECE per workspace < 0.10
  - Hallucination -40% from baseline
  - 6+ non-code sources live with permissions + salience + provenance + health
  - Per-workspace tuning operational (glossary + few-shot + calibrator + LTR)
- Demo: CFO-persona Series-A demo question — "what is feature F costing us, and is it justified given customer revenue?" — answered end-to-end with 5+ sources cited, calibrated confidence per claim
- SHIP-LOG.md up-to-date
- Quality regression harness gating CI
- Spec folders complete for every shipped sub-session
- Series-A pitch deck supportable by shipped architecture

---

## 12 — START COMMAND (what to do RIGHT NOW)

Step 1 — Orient (15 minutes):
```
1. Read all docs in Section 1 (orientation files)
2. Run state-check commands in Section 2
3. If SHIP-LOG.md doesn't exist, create it (grep git log for "ADR-" mentions, verify artifacts)
4. Write docs/STATUS-WEEK-0.md with the actual state vs the canonical plan in Section 3
```

Step 2 — Decompose Tier 1 (30 minutes):
```
1. For each of T1.1 through T1.11 (11 sub-sessions), confirm dependencies via the DAG
2. Identify which 3-6 can start immediately (P0 bugs is always first; others depend on it)
3. Create specs/2026-Q2-tier1-<slug>/ folder for the first 3 you'll dispatch
4. Write 00-spec.md for each (if you can't write the spec, the item is under-defined — STOP)
```

Step 3 — Dispatch T1.1 P0 Bug Bundle (the actual first action):
```
1. git checkout -b fix/p0-demo-bugs from main
2. Read docs/adrs/SONNET-IMPLEMENTATION-PROMPT-P0-BUG-BUNDLE.md fully
3. Follow the 6-bug priority order: B1 (zero tool calls) → B5 (confidence schema) → B4 (citations) → B2 (cost telemetry) → B3 (rebuild local-only) → B6 (latency)
4. Spec-driven gates per sub-session (00-spec, 01-plan, 02-tasks)
5. Test-first per bug
6. Open PR to release/v2-seed-window (create the release branch from main first if it doesn't exist)
7. Tag for human review (you do NOT auto-merge)
```

Step 4 — While P0 bugs are in review, dispatch parallel-safe items:
```
- T1.3 ADR-0073 user (Frontend Live-Up; 4-6h on existing branch feature/adr-0073-frontend-liveup)
- A1.1 SQL Deep Extractor (independent)
- A1.2 Hybrid Retrieval (independent)
- B1.1 ADR-0091 framing (writing only)
```

Step 5 — Re-loop. Run the action loop in Section 4 every working day.

---

## TL;DR for the operator

1. **Read Section 1 docs first.** No exceptions.
2. **Verify state per Section 2.** Don't trust the plan over reality.
3. **Pick from Section 3 list per Section 4 algorithm.** Six concurrent max.
4. **Branch per Section 5; merge per Section 5 gating.** No direct-to-main commits.
5. **Quality gates per Section 6.** No Gate-PR merges without passing.
6. **Anti-patterns per Section 7.** Reject scope creep aggressively.
7. **Budget per Section 8.** Stop at 1.5× per-session cap.
8. **Spec-driven per Section 9.** Five files per sub-session at merge time.
9. **Report weekly per Section 10.** Status doc + ship log + risks.
10. **Done per Section 11.** All three Gates merged + quality targets + demo.

Your single first action: read the orientation docs in Section 1, run the state-check in Section 2, then dispatch T1.1 P0 Bug Bundle. Everything else cascades from there.

The plan is in `LEAN-PLAN-RECONCILED-WITH-EXISTING-ADRS.md`. The orchestration patterns are in `MASTER-ORCHESTRATOR-PROMPT.md`. The discipline is in `SPEC-DRIVEN-DEV-FRAMEWORK.md`. The sub-session prompts are the files listed in Section 1.

You have everything you need. Begin.
