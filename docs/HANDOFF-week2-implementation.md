# Implementation Handoff — Week 2 of ADR-006

**Purpose:** Brief a fresh Claude/Sonnet session to execute Week 2 of the ADR-006 rollout. Week 1 has already shipped. This week wires incremental indexing and adds bidirectional blast-radius traversal.

**Prerequisites:** Week 1 must be complete and merged. Verify before starting:

- `V3__structural_columns.sql` is applied
- `companybrain/structural/parser.py` exists and produces structural rows
- `companybrain/structural/risk.py` populates `risk_score` on `nodes`
- `BlastRadiusNode.java` has `riskScore` and `riskFactors` fields
- Frontend `BlastRadiusPanel.jsx` shows risk-sorted nodes

If any of those aren't in place, stop and finish Week 1 first.

---

## How to use this doc

1. Open a fresh Cowork session in the `company-brain` project.
2. Paste the prompt below as the first message.
3. The agent reads the required ADRs, sets up a task list, starts work.

---

## The prompt to paste (copy verbatim)

```
You are implementing Week 2 of ADR-006 in this project. Week 1 has already
shipped (structural parser + per-node risk score + frontend display). The
decision is final; you are executing, not designing.

REQUIRED READING (in this order, before writing any code):

1. /Users/chinmayjadhav/Documents/Claude/Projects/company-brain/PROJECT_CONTEXT.md
2. /Users/chinmayjadhav/Documents/Claude/Projects/company-brain/docs/ADR-001-graph-storage.md
3. /Users/chinmayjadhav/Documents/Claude/Projects/company-brain/docs/ADR-003-multi-tenancy.md
4. /Users/chinmayjadhav/Documents/Claude/Projects/company-brain/docs/ADR-005-artifact-centric-pipeline.md
   ← critical: this week wires Week 1's parser into ADR-005's
   artifact_change_events. Read it carefully.
5. /Users/chinmayjadhav/Documents/Claude/Projects/company-brain/docs/ADR-006-adopt-crg-structural-and-mcp-layer.md
   ← THE plan. This week = Week 2 in § Implementation Phases.
6. /Users/chinmayjadhav/Documents/Claude/Projects/company-brain/docs/ANALYSIS-code-review-graph.md
   ← reference for what to port from CRG and where each algorithm lives.

VERIFY WEEK 1 STATE BEFORE WRITING CODE:

Run these checks first. If any fails, stop and report.

  - git log --oneline | head -20    # confirm Week 1 commits are present
  - Look for companybrain/structural/parser.py
  - Look for companybrain/structural/risk.py
  - Inspect the latest Flyway migration; confirm V3__structural_columns.sql
    has been applied (added qualified_name, file_hash, line_start, line_end,
    risk_score, risk_factors to nodes)
  - Confirm BlastRadiusNode.java has riskScore and riskFactors fields
  - Confirm at least one workspace has nodes.qualified_name populated

REFERENCE REPO TO PORT FROM:

Clone CRG locally (MIT licensed, source for the algorithms you're porting):

  cd /tmp && git clone --depth 1 https://github.com/tirth8205/code-review-graph.git

This week you'll port from these CRG files:
  - code_review_graph/incremental.py
      • get_changed_files (line ~409)
      • find_dependents + _single_hop_dependents (line ~624)
      • incremental_update (line ~818) — the main routine
  - code_review_graph/graph.py
      • get_impact_radius_sql (line ~625) — bidirectional recursive CTE

Clean-room port. Do NOT vendor CRG. Add this attribution header to each
ported module:

  # Algorithm ported from tirth8205/code-review-graph (MIT License).
  # Original: <relative path in CRG>

WEEK 2 SCOPE (from ADR-006 § Implementation Phases):

Goal: incremental indexing + bidirectional blast radius, end-to-end,
demo-able. Editing a file should re-parse only that file and its dependents
in <5 seconds. Blast radius should now show both upstream and downstream
impact.

Action items from ADR-006 to complete this week (numbers match ADR-006):

  [5]  companybrain/structural/changes.py
       - Port get_changed_files: shells out to `git diff --name-only <base>`,
         returns list of changed file paths relative to repo root.
       - Add SVN support if straightforward (CRG's _get_svn_changed_files);
         otherwise stub it and note the gap.

  [6]  companybrain/structural/dependents.py
       - Port _single_hop_dependents: given a file_path, walk our edges
         table for IMPORTS_FROM/CALLS/INHERITS/IMPLEMENTS edges where the
         target is in this file (or any node in this file).
       - Port find_dependents: BFS up to 2 hops, capped at 500 files,
         returns DependentList-equivalent (a list with a `truncated` flag).
       - Postgres adaptation: use SQL queries against our edges table,
         not CRG's SQLite-shaped helper methods.

  [7]  companybrain/structural/indexer.py
       - Port incremental_update as the core routine.
       - Critical wiring: instead of taking a `base` git ref directly,
         CONSUME from artifact_change_events (per ADR-005). Source-file
         artifacts whose hash changed are the dirty set; reverse-import
         expansion adds dependents.
       - Use ProcessPoolExecutor for parallel parse, matching CRG's
         pattern. Cap at min(8, cpu_count()).
       - Mark consumed events: set artifact_change_events.consumed_at
         when the file's structural row has been re-written.
       - Skip unchanged files via SHA-256 hash check (already on
         nodes.file_hash from Week 1).
       - Recompute risk_score for any node whose file changed, plus any
         node within 1 hop of a changed node (reverse callers).

  [12] BlastRadiusService.java — bidirectional traversal
       - Modify the recursive CTE: add a UNION ALL branch that also
         walks edges in reverse (target_id = br.id).
       - Add a `direction` parameter to the public method:
         "forward" | "reverse" | "both", default "both".
       - Wire the parameter through GraphController endpoint as a query
         string. Default behavior unchanged (both = reverse + forward).
       - Add a feature flag (Spring @Value("${app.blast-radius.bidirectional:true}"))
         so the new behavior can be toggled off in production if needed.
       - Update BlastRadiusResponse.builder calls and unit tests.

OPERATING RULES:

1. Use TodoWrite to create a task list before starting. Mirror the action
   items above. Mark in_progress / completed as you go.

2. Read every required ADR in the listed order before writing code. The
   ADR-005 artifact-centric pipeline section is the most important context
   for this week — the indexer wires *into* its event stream, not directly
   to git.

3. Ask clarifying questions via AskUserQuestion before starting if any of
   the following is unclear:
   - Whether artifact_change_events is already populated for source files,
     or whether you also need to wire up an ArtifactWriter for files first
     (likely yes if Week 1 didn't do it).
   - Whether the test workspace has multiple files with cross-file imports
     (needed to test dependent expansion meaningfully).
   - Whether the existing pipeline currently writes to nodes/edges directly
     or whether there's an Orchestrator gating that needs updating.

4. Make small, focused commits. One per action item.
   Format: "ADR-006 §<N>: <description>". Examples:
   - "ADR-006 §5: port get_changed_files (git + svn fallback)"
   - "ADR-006 §12: add bidirectional CTE to BlastRadiusService"

5. Verify before marking each task complete:
   - For Python ports: pytest passes; integration test confirms a file
     edit triggers re-parse of only that file + dependents in <5 seconds
     against a 500-file fixture workspace.
   - For Java change: existing BlastRadiusService unit tests still pass;
     new test confirms bidirectional traversal returns both upstream
     callers and downstream callees for a known-graph fixture.
   - For dirty-set wiring: write a test that creates an artifact_change_event
     for a source file, runs the indexer, and confirms (a) the file's
     nodes are re-parsed, (b) the event is marked consumed, (c) at least
     one dependent file is re-parsed too.

6. Do NOT lift CRG code by copy-paste. Read it, understand it, write our
   version against our Postgres schema. Attribution headers required.

7. Do NOT start Week 3 work (MCP server, hints, skills). Stop and report
   when Week 2 is complete.

8. If you hit a design decision (not just an implementation choice), stop
   and surface it via AskUserQuestion rather than guessing.

DELIVERABLE AT END OF WEEK 2:

A demo-able state where:
- Editing a file in a connected workspace triggers re-indexing of that
  file plus its dependents in under 5 seconds (measure on a 500-file
  fixture).
- artifact_change_events is the trigger source; events are consumed,
  not just read.
- BlastRadiusService returns nodes from BOTH directions when called with
  default args. The frontend (already from Week 1) renders both upstream
  and downstream impacted nodes.
- A test workspace shows risk scores updating correctly when a file's
  caller graph changes (dependent nodes' risk_score reflects the new
  caller count after a re-index).
- Integration test demonstrating the full loop: file edit → artifact
  event → dirty set → re-parse → updated risk score → blast radius
  reflects the change. Document the timing.

Final message at the end of the week should include:
- A summary of which ADR-006 action items you completed (by number)
- The diff of changes (or list of new/modified files)
- The integration-test timing numbers (full loop wall clock)
- Any deferred decisions or known issues
- A short "ready for week 3" note describing the current repo state

Begin by reading the required ADRs, verifying Week 1 state, then create
your task list, then ask any clarifying questions before writing code.
```

---

## Notes for the user (Chinmay)

**The most likely failure mode this week is wiring confusion.** ADR-005's
artifact-centric pipeline says source-file changes flow through `artifact_change_events`. If Week 1's implementation skipped that wiring (just wrote directly to `nodes`), Week 2's indexer has nothing to consume from. The verification step in the prompt asks the agent to check this; if it's missing, the right answer is to wire `ArtifactWriter` for source files in Week 2 before doing anything else, then use the indexer as the consumer. Add ~1 day to the week.

**The bidirectional CTE is small but easy to get wrong.** The risk is that a naive `UNION ALL` over both directions can produce exponential results in tightly-connected graphs. CRG handles this with `LIMIT max_nodes + len(seeds)` after the CTE. Make sure the agent ports that limit too — it's not just an optimization, it's a correctness guard.

**On risk-score recomputation.** When a file is re-parsed, the file's own nodes get fresh risk scores trivially. But callers of those nodes can also have their `caller_count` factor change, so their `risk_score` should be recomputed too. The prompt asks for "1 hop reverse" recomputation. If that's too expensive on real graphs, the agent will surface it — that's fine, it's the right place to make a perf call.

**Things to spot-check after the week.**

- Run `EXPLAIN ANALYZE` on the new bidirectional CTE on a realistic workspace. If p95 > 500ms, ADR-001 says we should be considering Neo4j — flag it but don't act on it this week.
- Confirm that pruned edges (`is_pruned = true`) are still excluded in the reverse branch, not just the forward one.
- Confirm the feature flag actually works (deploy with it off, verify old behavior).

**If Week 2 finishes early.** Resist the temptation to start Week 3. The MCP server is enough work to deserve its own session with fresh context.

---

## What's deferred to Week 3 (do not start)

- MCP server scaffolding
- Hints engine port
- `get_minimal_context` tool
- First 10 MCP tools
- Authentication for MCP endpoints
