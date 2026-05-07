# ADR-006 Implementation Handoffs — Index

**Purpose:** This is the entry point for executing ADR-006 across four sequenced fresh sessions. Read this first to understand how the handoffs work, then use the per-week documents to drive each session.

---

## The four handoffs

| Week | File | Focus | Demo at end |
|---|---|---|---|
| 1 | `HANDOFF-week1-implementation.md` | Tree-sitter parser + multi-factor risk score + frontend display | Risk-sorted blast radius with explainable factors |
| 2 | `HANDOFF-week2-implementation.md` | Hash-diff incremental indexing + bidirectional CTE | Edit-to-indexed in <5s; both upstream and downstream impact |
| 3 | `HANDOFF-week3-implementation.md` | MCP server + hints engine + 10 tools + minimal-context opener | Claude Code / Cursor connect via MCP and run a workflow |
| 4 | `HANDOFF-week4-implementation.md` | Skills + flows + topology + cleanup + validation benchmarks | Full skill-driven AI assistant workflows; benchmarks confirm the rollout |

Each is **self-contained**: a fresh Sonnet session can read one handoff and execute that week without reading the others. Each enforces a strict scope so it doesn't bleed into the next week's work.

---

## How to run the rollout

### Sequence

1. **Week 1.** Open a fresh Cowork session. Paste the prompt from `HANDOFF-week1-implementation.md`. Let the agent work; intervene only if it asks a clarifying question or violates an operating rule. Review its final report.
2. **Verify.** Before kicking off Week 2, do the spot-checks listed in the "Notes for the user" section of the Week 1 handoff. If anything is off, fix it now — don't carry it forward.
3. **Week 2.** Fresh session. Paste Week 2's prompt. Repeat the cycle.
4. **Continue through Week 4.** Each week starts fresh. No long-running session that accumulates state and drifts.

### Why fresh sessions per week

- Context windows are bounded; a four-week project doesn't fit in one session even with disciplined memory management.
- Each week's deliverables are reviewable on their own. A session that blends weeks produces tangled commits that are hard to audit.
- A failure in Week 2 doesn't poison Week 3 — the new session reads only what's been merged and reasons from there.
- Fresh sessions can't be confused by stale assumptions from earlier iterations of the plan.

### Why the prompts are long

Each prompt teaches the new session everything it needs:
- Which ADRs to read (in order)
- The exact action items in scope, by ADR-006 number
- What's explicitly out of scope
- Operating rules (TodoWrite, attribution, verification, no scope creep)
- The deliverable shape (so the agent's final report is consistent)

Shorter prompts produce vaguer work. The 4–6 minutes the agent spends reading the prompt is repaid many times over by not having to course-correct mid-week.

---

## What ADRs each week reads

The Week 1 prompt is the most exhaustive (the agent has no context). Subsequent weeks read fewer documents because more context is in the codebase by then.

| Document | Wk 1 | Wk 2 | Wk 3 | Wk 4 |
|---|:---:|:---:|:---:|:---:|
| `PROJECT_CONTEXT.md` | required | required | required | required |
| `ARCHITECTURE-company-brain-v2.md` | required | — | — | — |
| `ADR-001-graph-storage.md` | required | required | — | — |
| `ADR-003-multi-tenancy.md` | required | required | required | — |
| `ADR-005-artifact-centric-pipeline.md` | required | required | — | — |
| `ADR-006-adopt-crg-structural-and-mcp-layer.md` | required | required | required | required |
| `ANALYSIS-code-review-graph.md` | required | required | required | required |
| `TRADEOFFS-crg-vs-companybrain.md` | required | — | required | required |
| Prior week's handoff | — | — | — | wk 3 |

Don't add to the required reading lists when handing off. More reading = less doing. The handoff prompts are calibrated.

---

## Where each week's work lands

| Week | New Python modules | Java changes | Frontend changes | New tables / cols | New services |
|---|---|---|---|---|---|
| 1 | `structural/parser.py`, `structural/risk.py` | `BlastRadiusNode` DTO | `BlastRadiusPanel.jsx` (risk display) | columns on `nodes`; `flows`/`flow_memberships`/`communities`/`graph_metrics` tables (schema only) | — |
| 2 | `structural/changes.py`, `dependents.py`, `indexer.py` | `BlastRadiusService` (bidirectional CTE) | — | — | — |
| 3 | `companybrain_mcp/` (entire new service) | possibly new endpoints to back MCP tools | — | — | `company-brain-mcp` (new container) |
| 4 | `structural/flows.py`, `topology.py`, `communities.py` (low priority) | `@Scheduled` job for nightly metrics (or Python cron) | Architecture tab | populates `flows`, `flow_memberships`, `graph_metrics` rows | — |

Cleanup work in Week 4: deletion of regex blocks in `CodeTracer`, optimization of Pass 1 LLM extractor.

---

## Operating principles for every week

The handoff prompts encode these. Listed here so you know what to enforce if a prompt is reused or adapted.

1. **Read first, code second.** Required ADRs before any tool use beyond `Read`.
2. **TodoWrite from the start.** Tasks mirror ADR-006 action items by number.
3. **Clean-room port.** No vendoring CRG. Every ported module gets an attribution header.
4. **One commit per action item.** Format: `ADR-006 §<N>: <description>`.
5. **Verify before completing.** Tests pass / build green / spot-check works. No marking complete without evidence.
6. **No scope creep.** If the agent finds something out of scope, it writes it down for the next week. It does not silently expand.
7. **Stop at week boundary.** No starting next week's work even if there's time.
8. **Surface decisions, don't guess.** AskUserQuestion for design choices; assumptions for implementation choices.

---

## Common failure modes and how to catch them

**The agent skips required reading.** Tell from the first response: it'll start writing schema or code without referencing specific ADR sections. Stop it; tell it to actually read.

**The agent vendors CRG instead of porting.** Look for `import code_review_graph` or `pip install code-review-graph` in any commit. That's a port violation; have it rewritten.

**The agent's commits don't reference ADR-006 § numbers.** Ask for them to be amended. The numbered traceability matters when reviewing.

**The agent ships without an integration test.** Each week's verification rules require an end-to-end test. If the final report doesn't show it ran, ask for it before declaring the week done.

**The agent expands scope.** "I noticed X was also broken so I fixed it." Push back: was X listed in this week's action items? If no, undo the change and write it down for the next week.

**The agent finishes early and starts the next week.** Stop the session immediately. The week-by-week boundary is non-negotiable.

---

## Adapting if a week takes longer than expected

If Week N takes 8 days instead of 5, that's fine. **Don't compress later weeks to compensate.** The right response is:

- Finish Week N to the deliverable bar
- Take an extra day for spot-checking (real workspaces find real bugs)
- Start Week N+1 with the next session

Compressing weeks turns a 4-week plan into a 4-week disaster. Each week's deliverable is the unit of value; ship it cleanly, even slow.

---

## Adapting if a week's scope turns out to be wrong

It will happen. The most likely week is Week 3 — MCP transport choice (FastMCP vs hand-rolled with FastAPI) might force a change. The right response:

- Stop the session
- Update the relevant handoff doc with the new direction
- Update ADR-006 if the change is architectural
- Resume with the updated context

Do NOT just let the agent silently route around the spec. The handoffs are the contract; if the contract is wrong, fix the contract first.

---

## After Week 4

The four-week rollout ships:

- A working multi-language structural layer
- Sub-5-second incremental indexing
- An MCP server that any AI assistant can drive
- Six skills covering review/audit/onboard/debug/explore/impact
- Verifiable cost reduction (5x at p50 token consumption per question)

Then **stop and bake.** Do not immediately start the next set of ADRs. Run the system on real workspaces for at least 2 weeks. Find the real failure modes. The natural next ADRs (multi-domain collectors, Skills File API, universal knowledge schema migration, staleness engine) should be planned based on what shipping actually taught you, not on what we projected before shipping.

If after the bake-in period you want to continue: the next handoff index for the *next* set of ADRs lives in a future doc. Write it then; don't pre-write it now.

---

## Files in this rollout (reference)

```
docs/
├── HANDOFF-INDEX.md                              ← you are here
├── HANDOFF-week1-implementation.md
├── HANDOFF-week2-implementation.md
├── HANDOFF-week3-implementation.md
├── HANDOFF-week4-implementation.md
├── ADR-001-graph-storage.md
├── ADR-003-multi-tenancy.md
├── ADR-005-artifact-centric-pipeline.md          ← created during planning
├── ADR-006-adopt-crg-structural-and-mcp-layer.md ← THE plan
├── ANALYSIS-code-review-graph.md                 ← reference companion
├── TRADEOFFS-crg-vs-companybrain.md              ← reference companion
├── ARCHITECTURE-company-brain-v2.md
├── PIPELINE-api-context-builder.md
├── RETRIEVAL-ARCHITECTURE.md
└── (proposed-ADR-007 will be marked superseded in Week 4)
```

If `proposed-ADR-007` or any draft `ADR-008` files exist, they are not canonical reading. Week 4 marks them superseded.
