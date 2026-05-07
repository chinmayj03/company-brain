# Implementation Handoff — Week 1 of ADR-006

**Purpose:** Brief a fresh Claude/Sonnet session that will build the structural-layer rollout described in ADR-006. The new session has zero context from prior conversations. Everything it needs is here or in the docs it's pointed to.

---

## How to use this doc

1. Open a fresh chat session in Cowork mode against this `company-brain` project folder.
2. Paste the prompt below as the first message.
3. The agent will read the required ADRs, set up a task list, and start work.

---

## The prompt to paste (copy this verbatim)

```
You are implementing Week 1 of ADR-006 in this project. The decision has been
made; do not relitigate it. Your job is to execute.

REQUIRED READING (in this exact order, before writing any code):

1. /Users/chinmayjadhav/Documents/Claude/Projects/company-brain/PROJECT_CONTEXT.md
2. /Users/chinmayjadhav/Documents/Claude/Projects/company-brain/docs/ARCHITECTURE-company-brain-v2.md
3. /Users/chinmayjadhav/Documents/Claude/Projects/company-brain/docs/ADR-001-graph-storage.md
4. /Users/chinmayjadhav/Documents/Claude/Projects/company-brain/docs/ADR-003-multi-tenancy.md
5. /Users/chinmayjadhav/Documents/Claude/Projects/company-brain/docs/ADR-005-artifact-centric-pipeline.md
6. /Users/chinmayjadhav/Documents/Claude/Projects/company-brain/docs/ADR-006-adopt-crg-structural-and-mcp-layer.md   ← this is THE plan
7. /Users/chinmayjadhav/Documents/Claude/Projects/company-brain/docs/ANALYSIS-code-review-graph.md
8. /Users/chinmayjadhav/Documents/Claude/Projects/company-brain/docs/TRADEOFFS-crg-vs-companybrain.md

Skim these for orientation only (don't block on them):
- docs/SYSTEM_DESIGN.md
- docs/PIPELINE-api-context-builder.md
- docs/RETRIEVAL-ARCHITECTURE.md
- docs/schema.md (if present)
- company-brain-backend/src/main/resources/db/migration/V1__create_graph_tables.sql
- company-brain-backend/src/main/java/com/companybrain/service/BlastRadiusService.java
- company-brain-ai/src/companybrain/ (whole tree — understand the existing pipeline)

REFERENCE REPO TO PORT FROM:

The structural layer ports algorithms (clean-room, with attribution) from
https://github.com/tirth8205/code-review-graph (MIT licensed). Clone it
locally before you start porting:

  cd /tmp && git clone --depth 1 https://github.com/tirth8205/code-review-graph.git

The exact files to reference are mapped in ADR-006's "What we lift from CRG"
table. Do NOT vendor CRG as a dependency. Read its code, understand the
algorithm, write a clean Python module against our Postgres schema.
Add an attribution header to each ported module:

  # Algorithm ported from tirth8205/code-review-graph (MIT License).
  # Original: <relative path in CRG repo>

WEEK 1 SCOPE (from ADR-006 § Implementation Phases):

Goal: ship structural parser + risk score, end-to-end, demo-able.

Action items from ADR-006 to complete this week (numbers match ADR-006):
  [1]  Flyway migration V3__structural_columns.sql
       - Add columns to nodes: qualified_name, file_hash, line_start,
         line_end, risk_score, risk_factors
       - Create flows, flow_memberships, communities, node_communities,
         graph_metrics tables (week 1 only writes to nodes; the rest are
         schema-only this week)
       - All new tables get RLS policies consistent with ADR-003
       - Add idx_nodes_qualified
  [2]  Add tree-sitter-languages to company-brain-ai/pyproject.toml.
       Verify grammars for Java, TS, JS, Python, Go.
  [4]  companybrain/structural/parser.py — tree-sitter walker, qualified-name
       scheme (path::Class.method), emit (NodeInfo, EdgeInfo) records
  [8]  companybrain/structural/risk.py — port compute_risk_score from
       CRG's changes.py. Multi-factor: tests + callers + security keywords +
       flow membership + cross-community calls. Returns 0.0–1.0 with
       per-factor breakdown stored in risk_factors JSONB.
  [13] BlastRadiusNode.java DTO — add riskScore (Double), riskFactors (Map),
       flowMembership (List<UUID>) fields
  [14] BlastRadiusPanel.jsx — render affected nodes sorted by risk_score
       descending; show factor breakdown on hover
  [30] One-time backfill: run the new parser against an existing test
       workspace, populate qualified_name + file_hash + line_start/end on
       nodes; compute and store risk_score for every node.
  [31] Equivalence tests — parser output vs. existing regex output on a
       Java + TS + Python fixture. Document any differences. Targets:
       parity or strictly better entity recall.

Deferred to weeks 2–4 (do NOT do these now):
  - Hash-diff incremental indexing (week 2)
  - Bidirectional blast-radius CTE (week 2)
  - MCP server, hints, skills (weeks 3–4)
  - Flow detection / hub-bridge metrics / communities (week 4+)

OPERATING RULES:

1. Use TodoWrite to create a task list before starting. Mirror the
   action items above. Mark in_progress / completed as you go.
2. Read every ADR in the order listed before writing any code. The
   architectural reasoning is dense and not optional context.
3. Ask clarifying questions via AskUserQuestion before starting if any
   of the following is unclear: target Postgres version, Java version,
   Python version, where the test workspace lives, whether to run the
   migration against a real DB or generate-only.
4. Make small, focused commits. One commit per action item, prefixed
   with "ADR-006 §<N>: <description>" so the audit trail back to the
   ADR is obvious.
5. Verify before marking complete:
   - For schema work: the migration runs cleanly against a local Postgres
   - For Python ports: pytest passes; equivalence tests against the
     existing regex output show parity or improvement
   - For Java DTO/frontend changes: the project compiles and the existing
     tests still pass
6. Do NOT lift CRG code by copy-paste. Read it, understand it, write our
   version against our schema. Attribution headers are required.
7. Do NOT start week 2 work, even if you finish week 1 early. Stop and
   report. The user will hand off again to a new session for week 2.
8. If you hit a blocker that requires a design decision (not just an
   implementation choice), stop and surface it via AskUserQuestion rather
   than guessing.

DELIVERABLE AT END OF WEEK 1:

A demo-able state where:
- The migration is applied
- A test workspace has been re-indexed with the new structural parser
- Every node in that workspace has a populated qualified_name, file_hash,
  line_start/end, risk_score, and risk_factors
- The frontend BlastRadiusPanel shows risk-sorted nodes with factor
  breakdown
- An equivalence-test report exists comparing new parser output vs. old
  regex output on the fixture workspace

Final message at the end of the week should include:
- A summary of which ADR-006 action items you completed (by number)
- The diff of changes (or a list of new/modified files)
- Any deferred decisions or known issues to surface to the next session
- A short list of "ready for week 2" — what state the codebase is in
  and what week 2 should build on

Begin by reading the required ADRs, then create your task list, then
ask any clarifying questions before writing code.
```

---

## Notes for the user (Chinmay)

**Why this prompt is shaped the way it is.** Fresh sessions often:

- Skip reading docs and start coding immediately. The prompt forces the reading order.
- Try to scope-creep into adjacent work. The prompt explicitly forbids weeks 2–4 work.
- Lose track of multi-step plans. The prompt requires TodoWrite from the start.
- Vendor third-party code rather than port it. The prompt is explicit on attribution and clean-room.
- Skip verification. The prompt requires per-step verification before marking complete.

**Why hand off week-by-week instead of all four at once.** Each week's deliverable is a checkpoint. Reviewing one week's output before kicking off the next prevents compounding mistakes. After week 1, write a fresh handoff prompt for week 2 that points at the actual state of the repo — don't reuse week 1's prompt verbatim.

**ADRs the new session must NOT skip.**

- ADR-001: tells them why we're on Postgres and not Neo4j
- ADR-003: tells them about RLS — every new table needs the policy
- ADR-005: the artifact-centric pipeline; the structural layer ports run *off* artifact change events, not git directly
- ADR-006: the actual plan
- ANALYSIS-code-review-graph.md: tells them what to lift and what file in CRG to read for each ported module
- TRADEOFFS-crg-vs-companybrain.md: tells them why we're skipping certain CRG things (SQLite store, registry, daemon) so they don't accidentally port those

**ADRs they can skim or skip on the first read.**

- ADR-002 (ingestion pipeline / SQS) — relevant later when wiring incremental updates, not week 1
- ADR-004 (universal knowledge schema) — long-term schema direction; week 1 doesn't need it
- Any proposed-but-not-accepted "ADR-007" or "ADR-008" drafts in the docs folder are NOT canonical. ADR-006 supersedes proposed ADR-007. Tell the agent explicitly: only read the numbered ADR-001 through ADR-006 files.

**Things to check before pasting the prompt.**

- The CRG repo URL still resolves (it did when this was written)
- Postgres is running locally so the migration step has a target
- The test workspace exists and is referenced in code (the agent will ask about location)
- The user has a Java/Maven and Python/uv toolchain ready

**If the agent asks where the test workspace lives.** Look in `company-brain-ai/tests/fixtures/` first. If empty, point it at any small Java Spring Boot or Python FastAPI repo — the equivalence test only needs ~50 endpoints to be meaningful.

**After week 1.** Review:

1. Did the equivalence tests pass? Any divergence is a finding, not necessarily a bug — note it for the ADR-006 living-doc.
2. Are the ported modules attributed?
3. Does the migration run reversibly?
4. Does the frontend actually render the risk score?

Then write a `HANDOFF-week2-implementation.md` mirroring this doc, scoped to the week 2 action items in ADR-006 (incremental indexing + bidirectional CTE), and pointing the next session at this week's commits as starting state.
