# Master Orchestrator Prompt — Parallel V2 Build + Consolidated PR

**You are the orchestrator for the complete V2 build of company-brain.** This prompt coordinates **45 sub-sessions** across **Tier-1 (seed cycle) + Track A Waves 1/2/3 + Track B Waves 1/2/3 + Tech-debt batch**. Drop this prompt into a Sonnet session; the orchestrator will spawn parallel sub-sessions per the schedule, sync at gates, and converge to one consolidated PR (or per-Wave PRs, recommended).

This is the **complete V2 build plan**. ~45 sub-sessions; ~$1,150 total Sonnet cost; ~6 months calendar time with 2-3 engineers; converges to one merged release branch on `main`.

---

## What this prompt does

The orchestrator (you, the Sonnet session this prompt is given to) will:

1. Read all individual implementation prompts (listed below)
2. Build a dependency DAG across all sub-sessions
3. Open parallel branches per the parallelism cap (default 6 concurrent)
4. Spawn parallel agent sub-sessions, one per branch, each given its sub-prompt
5. Sync at gate boundaries (Wave 1 complete → Wave 2 can start, etc.)
6. Run integration tests at each gate
7. Merge sub-session branches → Wave integration branches → release branch → main
8. Generate the consolidated PR description on completion

**It does not** rewrite the per-sub-session prompts; it orchestrates them.

---

## All 45 sub-sessions, complete index

### Tier 1 — Seed-cycle (6 sub-sessions, run in seed window)
T1.1 [P0 Bug Bundle](adrs/SONNET-IMPLEMENTATION-PROMPT-P0-BUG-BUNDLE.md) — 1w, $20
T1.2 [ADR-0061 P1 iterative exploration](adrs/SONNET-IMPLEMENTATION-PROMPT-ADR-0061-P1.md) — 1w, $15
T1.3 [ADR-0071 P1 frontend](adrs/SONNET-IMPLEMENTATION-PROMPT-ADR-0071-P1.md) — 2w, $30
T1.4 [ADR-0064 P1 privacy + audit](adrs/SONNET-IMPLEMENTATION-PROMPT-ADR-0064-P1.md) — 2w, $25
T1.5 [ADR-0079 P1 persona templates](adrs/SONNET-IMPLEMENTATION-PROMPT-ADR-0079-P1.md) — 2w, $30
T1.6 [ADR-0082 P1 drift entity](adrs/SONNET-IMPLEMENTATION-PROMPT-ADR-0082-P1.md) — 1.5w, $20

### Track A Wave 1 — Code Quality Foundations (8 sub-sessions, parallel with Tier 1)
A1.1 SQL Deep Extractor — 2w, $25 (within [Wave-1 master](adrs/SONNET-IMPLEMENTATION-PROMPT-V2-WAVE-1.md))
A1.2 Hybrid Retrieval (BM25+dense+RRF+BGE) — 2w, $20
A1.3 Anthropic Prompt Caching + GPTCache — 1w, $10
A1.4 Verbalized Confidence + Multi-Signal Aggregator — 1w, $15
A1.5 Streaming + Parallel Retrieval — 0.5w, $10
A1.6 Glossary Auto-Discovery — 1.5w, $20
A1.7 Few-Shot Bank — 1w, $15
A1.8 Quality Regression Harness — 0.5w, $10

### Track A Wave 2 — Deep Code Foundations (8 sub-sessions, post-A1)
A2.1 SCIP Indexers (Java + Python + TS) — 2w, $25 (within [Wave-2 master](adrs/SONNET-IMPLEMENTATION-PROMPT-V2-WAVE-2.md))
A2.2 Stack Graphs (build-less resolution) — 1.5w, $20
A2.3 Joern CPG (critical paths) — 2w, $30
A2.4 Blast Radius V2 (annealed + dataflow) — 1.5w, $20
A2.5 LazyGraphRAG Layer — 2w, $25
A2.6 Chain-of-Verification (CoVe) — 1w, $20
A2.7 Per-Workspace Confidence Calibrator — 1.5w, $25
A2.8 Per-Workspace Reranker LTR — 2w, $30

### Track A Wave 3 — Research-Grade (5 sub-sessions, post-A2)
A3.1 Speculative Decoding — 2w, $40 (within [Wave-3 master](adrs/SONNET-IMPLEMENTATION-PROMPT-V2-WAVE-3.md))
A3.2 HippoRAG Multi-Hop — 2w, $40
A3.3 AGSER Self-Reflection — 1.5w, $30
A3.4 Per-Workspace Embedding Fine-Tune — 3w, $60
A3.5 Persona Template Auto-Variants — 2.5w, $50

### Track B Wave 1 — Breadth Foundations (5 sub-sessions, parallel with A1)
B1.1 ADR-0074 Domain-Entity-First — 0.5w, $10 (within [Wave-B1 master](adrs/SONNET-IMPLEMENTATION-PROMPT-V2-WAVE-B1.md))
B1.2 ADR-0075 Connector Framework + skeleton — 1.5w, $25
B1.3 ADR-0076 Cross-Source Entity Resolution P1 — 2w, $40
B1.4 Notion Connector — 2w, $40
B1.5 Event-Stream P1 (M1+M2) — 2w, $50

### Track B Wave 2 — Connector Breadth + Permissions/Salience/Provenance (7 sub-sessions, post-B1)
B2.1 Slack Connector — 2w, $40 (within [Wave-B2 master](adrs/SONNET-IMPLEMENTATION-PROMPT-V2-WAVE-B2.md))
B2.2 Confluence Connector — 1.5w, $30
B2.3 Salesforce Connector — 2w, $40
B2.4 ADR-0077 Source-Aware Permissions — 1.5w, $30
B2.5 ADR-0078 Cross-Source Salience — 1.5w, $25
B2.6 ADR-0086 Cross-Source Provenance — 1w, $20
B2.7 Persona Answers Span Sources — 2w, $40

### Track B Wave 3 — Calls/Email + Health/Quality + Cross-Source Reasoning (6 sub-sessions, post-B2 + A2)
B3.1 Call-Transcript Connector — 2w, $40 (within [Wave-B3 master](adrs/SONNET-IMPLEMENTATION-PROMPT-V2-WAVE-B3.md))
B3.2 Email Connector — 2w, $40
B3.3 ADR-0087 Source Health & Freshness — 1.5w, $30
B3.4 ADR-0088 Non-Code Extraction Quality — 2w, $40
B3.5 Cross-Source Multi-Hop Reasoning — 3w, $60
B3.6 Per-Persona Source-Mix Budgets — 1.5w, $30

**Total: 45 sub-sessions; ~$1,150; ~145 engineer-weeks if sequential.**

---

## Dependency DAG (machine-readable)

```yaml
# All edges are "must finish before". Anything without an incoming edge can start day-1 of its Wave.

# === Tier 1 (seed window, week 0-7) ===
T1.1_p0_bugs:
  blocks: [T1.2, T1.4, T1.5, T1.6, A1.*, B1.*]   # P0 bugs block everything else
T1.2_adr0061_iterative:
  depends: [T1.1]
T1.3_adr0071_frontend:
  depends: [T1.1]
T1.4_adr0064_privacy:
  depends: [T1.1]
T1.5_adr0079_personas:
  depends: [T1.1]
T1.6_adr0082_drift:
  depends: [T1.1]

# === Track A Wave 1 (week 0-5; parallel with T1) ===
A1.1_sql_deep:      { depends: [T1.1] }
A1.2_hybrid_retr:   { depends: [T1.1] }
A1.3_caching:       { depends: [T1.1] }
A1.4_verb_conf:     { depends: [T1.1] }
A1.5_streaming:     { depends: [T1.1, A1.2] }
A1.6_glossary:      { depends: [T1.1, T1.4] }       # tuning store needs privacy
A1.7_few_shot:      { depends: [A1.6, T1.4] }
A1.8_quality_harness:{ depends: [A1.1, A1.2, A1.4] }

# === Track B Wave 1 (week 0-6; parallel with A1) ===
B1.1_adr0074:       { depends: [T1.1] }
B1.2_adr0075:       { depends: [T1.1, B1.1] }
B1.3_adr0076:       { depends: [B1.2] }
B1.4_notion:        { depends: [B1.2] }
B1.5_event_stream:  { depends: [T1.1, B1.1] }

# Gate: SEED-WINDOW CLOSE (week 7) — all Tier-1, A1, B1 merged

# === Track A Wave 2 (week 8-15) ===
A2.1_scip:          { depends: [A1.1, A1.2] }
A2.2_stack_graphs:  { depends: [A2.1] }
A2.3_joern:         { depends: [A1.1] }
A2.4_blast_v2:      { depends: [A2.3] }
A2.5_lazy_graphrag: { depends: [A2.1, A1.2] }
A2.6_cove:          { depends: [A1.4, T1.5] }
A2.7_workspace_calib:{ depends: [A1.4, A1.7] }
A2.8_workspace_ltr: { depends: [A1.2, A1.7] }

# === Track B Wave 2 (week 8-14; parallel with A2) ===
B2.1_slack:         { depends: [B1.2, B1.4] }
B2.2_confluence:    { depends: [B1.2, B1.4] }
B2.3_salesforce:    { depends: [B1.2, B1.4] }
B2.4_adr0077_perms: { depends: [B2.1, B2.2, B2.3] }
B2.5_adr0078_salience:{ depends: [B1.5] }
B2.6_adr0086_provenance:{ depends: [B1.4, B2.1] }
B2.7_persona_cross_source:{ depends: [B2.1, B2.2, B2.3, B2.4, B2.5, B2.6, T1.5] }

# === Track A Wave 3 (week 16-25; Series-A bucket) ===
A3.1_spec_decoding: { depends: [A1.5] }
A3.2_hipporag:      { depends: [A2.5] }
A3.3_agser:         { depends: [A2.6, A2.7] }
A3.4_emb_finetune:  { depends: [A2.7, A2.8, B1.5, "≥6-months-data"] }
A3.5_persona_auto_variants:{ depends: [T1.5, "≥3-months-ADR0079-M5-data"] }

# === Track B Wave 3 (week 16-24; Series-A bucket) ===
B3.1_calls:         { depends: [B1.2] }
B3.2_email:         { depends: [B1.2] }
B3.3_adr0087_health:{ depends: [B1.2, B2.7] }
B3.4_adr0088_quality:{ depends: [A1.8, B3.1, B3.2] }
B3.5_cross_source_multihop:{ depends: [A2.5, A3.2, B2.7] }
B3.6_persona_source_mix_budgets:{ depends: [B2.7, B3.4] }

# Gate: SERIES-A DEMO (week 26) — all merged
```

---

## Parallelism map (recommended — 2 engineers + 1 part-time researcher)

```
            Engineer 1 (full-time)          Engineer 2 (full-time)          Researcher (part-time)
            ────────────────────────        ────────────────────────        ──────────────────────
Week 0      T1.1 P0 bugs                    -                               -
Week 1      A1.1 SQL deep                   A1.2 hybrid retr                T1.2 iter explore (Sonnet)
Week 2      A1.1 cont'd, A1.4 verb conf     A1.2 cont'd, A1.6 glossary      T1.3 frontend kick
Week 3      T1.5 personas, B1.1 ADR-0074    A1.3 caching, A1.5 streaming    T1.3 cont'd, T1.4 privacy
Week 4      B1.2 connector framework        T1.6 drift, A1.7 few-shot        B1.5 event-stream P1
Week 5      B1.3 entity resolution          B1.4 Notion, A1.8 quality harness B1.5 cont'd
Week 6      Integration + acceptance        Integration                      Reviews
Week 7      Seed demo polish                Seed demo polish                 Seed demo polish
            ════════════════════════════════════════════════════════════════════════════════════
                              GATE 1: SEED-WINDOW PR TO MAIN
            ════════════════════════════════════════════════════════════════════════════════════
Week 8-15   A2.x (SCIP, Joern, Blast v2, LazyGraphRAG, CoVe, calibrator, LTR)
            B2.x (Slack, Confluence, Salesforce, permissions, salience, provenance, persona cross-source)
            ════════════════════════════════════════════════════════════════════════════════════
                            GATE 2: SEED-TO-SERIES-A PR TO MAIN
            ════════════════════════════════════════════════════════════════════════════════════
Week 16-25  A3.x (spec decoding, HippoRAG, AGSER, embedding FT, persona auto-variants)
            B3.x (calls, email, source health, quality framework, cross-source multihop, source-mix budgets)
            ════════════════════════════════════════════════════════════════════════════════════
                              GATE 3: SERIES-A DEMO PR TO MAIN
            ════════════════════════════════════════════════════════════════════════════════════
```

**Parallelism cap**: 6 concurrent sub-sessions max. Higher than that overwhelms reviewers and integration testing.

---

## The orchestrator's algorithm (you run this)

```
state = {
  completed: set(),
  in_progress: set(),
  queue: ordered_by_DAG_topological_sort(all_45_sub_sessions)
}

while state.queue or state.in_progress:
  # 1. Drain any completed sessions
  for s in state.in_progress.copy():
    if session_complete(s):
      verify_acceptance_gates(s)
      if all_pass:
        merge_to_wave_integration_branch(s)
        state.completed.add(s)
        state.in_progress.remove(s)
      else:
        flag_for_rework(s)

  # 2. Spawn up to (PARALLELISM_CAP - len(in_progress)) new sessions from queue
  while len(state.in_progress) < PARALLELISM_CAP and state.queue:
    next_session = next_with_all_dependencies_in_completed(state.queue)
    if not next_session:
      break  # blocked; wait for in-progress to drain
    spawn_sonnet_session(
      branch=f"feature/{next_session.id}",
      prompt=load_prompt(next_session.prompt_file),
      sub_session_id=next_session.id
    )
    state.in_progress.add(next_session)
    state.queue.remove(next_session)

  # 3. Wave-gate check: if all sub-sessions in a Wave are completed, merge Wave integration branch
  for wave in [T1, A1, B1, A2, B2, A3, B3]:
    if all_complete(wave):
      run_integration_tests(wave)
      if pass:
        merge_wave_branch_to_release(wave)
        create_PR(release → main)
        if PR_approved:
          merge_to_main()

  # 4. Sleep before next polling round
  sleep(...)
```

In practice this is human-driven: an engineering lead picks the next sub-session from the queue, kicks off a Sonnet session per the prompt, reviews + merges when done, and unblocks downstream items.

---

## Branching strategy & consolidated-PR options

### Option A — One mega-PR to main (USER REQUESTED but RISKY)

```
main
 └─ release/v2-complete                         (long-lived integration branch)
     ├─ feature/T1.1_p0_bugs                    (merges in)
     ├─ feature/A1.1_sql_deep                   (merges in)
     ├─ ... (43 more sub-session branches)      (each merges in)
     └─ ALL 45 → single PR → main
```

**Pros**: one PR description telling the whole story; clean main history (one giant merge commit per Wave or globally)
**Cons**: PR is unreviewable (hundreds of files; thousands of lines); rollback is all-or-nothing; integration bugs only discovered at the end

### Option B — Per-Wave PRs to main (RECOMMENDED)

```
main
 └─ release/v2-seed-window                      (Wave T1 + A1 + B1 — week 7)
     ├─ feature/T1.*  (6 branches)
     ├─ feature/A1.*  (8 branches)
     ├─ feature/B1.*  (5 branches)
     └─ release/v2-seed-window → PR → main      (single Gate-1 PR; reviewable)

 └─ release/v2-seed-to-series-a                 (Wave A2 + B2 — week 15)
     ├─ feature/A2.*  (8)
     ├─ feature/B2.*  (7)
     └─ release/v2-seed-to-series-a → PR → main (Gate-2 PR)

 └─ release/v2-series-a-demo                    (Wave A3 + B3 — week 26)
     ├─ feature/A3.*  (5)
     ├─ feature/B3.*  (6)
     └─ release/v2-series-a-demo → PR → main    (Gate-3 PR)
```

**Pros**: each PR is large but reviewable; per-Wave rollback possible; quality gates per Wave; demoable milestones aligned with funding events
**Cons**: three PRs instead of one (but that's a feature, not a bug)

**Recommendation**: Option B. The "one consolidated PR" pattern in Option A is genuinely operationally bad practice at this scale.

### Option C — Per-sub-session PRs (industry standard)

Each of 45 sub-sessions = own PR straight to main. No release branches.

**Pros**: smallest reviewable units; standard practice
**Cons**: no clear Wave gating; integration tests have to live inline per-PR; no "we shipped Wave 1" narrative

Use Option C **inside** Option B — i.e., per-sub-session PRs to the Wave's release branch, then Wave release branch to main as one PR.

---

## Integration testing at each Wave gate

Before merging a release branch to main, run:

1. **Quality regression harness** (`tests/quality/`) — all metrics for the Wave's scope must pass
2. **Cross-Wave acceptance tests** — items that depend across Tracks must still work (e.g., persona templates from T1.5 work with cross-source from B2.7)
3. **Production-like smoke** — run the demo question set end-to-end against a Wave-merged staging environment
4. **Cost telemetry verification** — per-query Sonnet cost must stay within target ($0.01-0.05 typical)
5. **Latency regression** — P50/P90 must meet the per-Wave targets in ADR-0085

Block release-branch-to-main merge if any gate fails.

---

## Cost & wall-time envelope (complete V2 build)

| Wave / Tier | Sub-sessions | Engineer-weeks | Sonnet $ |
|---|---|---|---|
| Tier 1 | 6 | 9.5 | $140 |
| Track A Wave 1 | 8 | 9.5 | $125 |
| Track A Wave 2 | 8 | 13.5 | $195 |
| Track A Wave 3 | 5 | 11 | $220 |
| Track B Wave 1 | 5 | 8 | $165 |
| Track B Wave 2 | 7 | 11.5 | $225 |
| Track B Wave 3 | 6 | 12 | $240 |
| **Total** | **45** | **75** | **$1,310** |

With 2 engineers + 1 part-time researcher (~2.3 FTE): ~26 weeks (6 months) calendar time. Three gate PRs to main (weeks 7, 15, 26).

---

## What to drop into a single Sonnet session today

If the user wants ONE prompt to give to one Sonnet session right now and have it kick off the entire build:

```
You are the V2 build orchestrator. Read docs/MASTER-ORCHESTRATOR-PROMPT.md and
the dependency DAG within. Your job today:

1. Verify the state of main vs the DAG (which sub-sessions are already done?
   Grep ship-log + git log for ADR references).
2. Pick the next 3-6 sub-sessions whose dependencies are met but are not in
   progress.
3. For each, open a branch named per the orchestrator naming convention
   (feature/<sub-session-id>), and post a kickoff PR with the sub-session prompt
   pasted as the PR description.
4. Tag the engineering lead for review.
5. Write a status comment on a tracking issue: "Wave X status: N completed,
   M in progress, K queued".
6. Re-run on a schedule (daily) to drain in-progress and spawn new branches.

The orchestrator is responsible for SEQUENCING and STATUS REPORTING only; it
does NOT write code itself. Each sub-session is run by a separate Sonnet
session via its own prompt.
```

This is the "give me one master prompt" deliverable. In practice the orchestrator needs human-in-the-loop review gates per PR.

---

## Definition of done (entire V2 build)

After all three Gate PRs land on main:

- **Quality** (V2 ADR-0085 targets fully met):
  - SQL coverage > 90%, blast radius recall > 85%, multi-hop accuracy > 75%
  - P50 latency < 2s, P90 < 5s
  - ECE per workspace < 0.08; hallucination -55% vs baseline
- **Breadth**: 6+ non-code sources live, cross-source persona answers, source-aware permissions + salience + provenance + health
- **Per-company tuning**: glossary, few-shot bank, calibrator, reranker LTR, embedding FT, persona auto-variants — all operating per-workspace
- **Spec-driven dev**: every shipped feature has a spec folder; quality regression in CI; reviewable PR per Wave
- **Pitch**: ready for Series-A close ("company brain with 6 sources, calibrated per company, reasoning across the unified graph; demoable to CFO/CEO/CS personas")
- **Series-B narrative ready**: "Palantir Foundry for AI-native institutional memory" supported by shipped architecture

---

## TL;DR

1. **45 sub-sessions total**; ~75 engineer-weeks; ~$1,310 Sonnet cost; ~6 months calendar time with 2-3 engineers.
2. **Three Wave gates** with corresponding PRs to main: Seed window (week 7), Seed-to-Series-A (week 15), Series-A demo (week 26).
3. **Six concurrent sub-sessions max** per parallelism cap; orchestrator topologically sorts the DAG and dispatches as dependencies clear.
4. **Recommended branching**: Option B (per-Wave release branch → PR to main). Option A (single mega-PR) is operationally bad practice at this scale even though the user asked for it.
5. **The orchestrator's job is sequencing + status reporting**, NOT writing code. Each sub-session runs in its own Sonnet session with its own prompt (all 45 prompts are linked at the top of this doc).
6. **Definition of done = three Gate PRs merged + V2 ADR-0085 quality targets met + Series-A pitch ready.**
