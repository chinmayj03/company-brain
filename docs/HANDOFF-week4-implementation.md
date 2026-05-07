# Implementation Handoff — Week 4 of ADR-006

**Purpose:** Brief a fresh Claude/Sonnet session to execute Week 4 — the final week of the ADR-006 rollout. This week ships skills, flows, topology metrics, frontend architecture views, and finishes cleanup.

**Prerequisites:** Weeks 1, 2, and 3 must be complete. Verify before starting:

- All Week 1 deliverables (parser, risk score, frontend display)
- All Week 2 deliverables (incremental indexer wired to artifact_change_events, bidirectional CTE)
- All Week 3 deliverables (MCP server running, 10+ tools, hints engine, MCP-INTEGRATION.md)
- An MCP client (Claude Code or Cursor) can connect and drive a workflow end-to-end
- The `flows`, `flow_memberships`, `communities`, `node_communities`, `graph_metrics` tables exist (created in Week 1's migration; populated this week)

If any are missing, stop and finish prior weeks first.

---

## How to use this doc

1. Open a fresh Cowork session in the `company-brain` project.
2. Paste the prompt below as the first message.
3. The agent reads the required ADRs, sets up a task list, starts work.

---

## The prompt to paste (copy verbatim)

```
You are implementing Week 4 of ADR-006 — the final week of the rollout.
Weeks 1, 2, and 3 have shipped (structural layer + incremental indexing
+ MCP server with hints). The decision is final; you are executing.

This week is about finishing what the prior weeks set up: write the agent
skills that drive the MCP surface, populate flows + topology so the stub
tools from Week 3 return real data, surface architecture views in the
frontend, delete the regex blocks the structural layer obsoletes, and
run the validation benchmarks.

REQUIRED READING (in this order, before writing any code):

1. /Users/chinmayjadhav/Documents/Claude/Projects/company-brain/PROJECT_CONTEXT.md
2. /Users/chinmayjadhav/Documents/Claude/Projects/company-brain/docs/ADR-006-adopt-crg-structural-and-mcp-layer.md
   ← THE plan. This week = Week 4 in § Implementation Phases.
   Read § "What we lift from CRG" carefully — the flows + topology entries
   are the primary references.
3. /Users/chinmayjadhav/Documents/Claude/Projects/company-brain/docs/ANALYSIS-code-review-graph.md
   ← reference for what to port from CRG (especially the flows section).
4. /Users/chinmayjadhav/Documents/Claude/Projects/company-brain/docs/TRADEOFFS-crg-vs-companybrain.md
   § "Skills — agent playbooks as markdown" is the reference for the skill
   format we're committing to.
5. /Users/chinmayjadhav/Documents/Claude/Projects/company-brain/docs/HANDOFF-week3-implementation.md
   ← context on what Week 3 left in the codebase. The stub tools
   (list_flows, find_hubs, find_bridges) need their backing endpoints
   and data populated this week.

VERIFY WEEK 1+2+3 STATE BEFORE WRITING CODE:

  - git log --oneline | head -50    # confirm prior weeks' commits
  - The MCP server runs on localhost:8765, /healthz returns 200
  - Connect any MCP client and call get_minimal_context — should work
  - companybrain/structural/{parser,risk,changes,dependents,indexer}.py
    all exist
  - BlastRadiusService.java accepts direction parameter
  - flows, flow_memberships, communities, graph_metrics tables exist
    (schema-only from Week 1; this week populates them)

REFERENCE REPO TO PORT FROM:

Clone CRG (MIT, source for the algorithms):

  cd /tmp && rm -rf code-review-graph
  git clone --depth 1 https://github.com/tirth8205/code-review-graph.git

This week you'll port from these CRG files:
  - code_review_graph/flows.py
      • _FRAMEWORK_DECORATOR_PATTERNS (line ~26) — ~30 regex patterns
        for Spring, FastAPI, Express, Django, Celery, Click, Pydantic,
        Android, Kotlin etc. Lift the whole list.
      • detect_entry_points (line ~150)
      • _trace_single_flow (line ~206)
      • trace_flows (line ~268)
      • compute_criticality (line ~308)
      • store_flows (line ~385)
      • get_affected_flows (line ~658)
  - code_review_graph/analysis.py
      • find_hub_nodes (line ~14) — degree centrality, top N
      • find_bridge_nodes (line ~58) — betweenness centrality via
        NetworkX, sample-based at >5K nodes
  - code_review_graph/communities.py
      • detect_communities (line ~565) — Leiden via igraph, file-based
        fallback. LOWER priority this week — only port if time allows.
  - code_review_graph/skills/*.md — the SKILL.md format and structure.
    Six skill files in CRG. Use as a template; rewrite content for our
    domain (which includes business context, not just code).

Clean-room. Attribution header required:

  # Algorithm ported from tirth8205/code-review-graph (MIT License).
  # Original: <relative path in CRG>

WEEK 4 SCOPE (from ADR-006 § Implementation Phases):

Goal: ship skills + flows + topology + cleanup + validation. End-of-week
deliverable is a fully functional system where AI assistants connect via
MCP, follow skill-driven workflows, and the system is measurably faster
and cheaper than before the ADR-006 rollout.

Action items from ADR-006 to complete this week (numbers match ADR-006):

  [9]  companybrain/structural/flows.py
       - Port detect_entry_points: function/test nodes that have no
         incoming CALLS edges, OR have a framework decorator (lift CRG's
         regex set wholesale), OR match a conventional name (main,
         handler, etc.).
       - Port trace_flows: BFS forward through CALLS edges from each
         entry point, max depth 15, collect path.
       - Port compute_criticality: combines node count, file count,
         depth, security keywords, public-API status. Returns 0.0–1.0.
       - Write to flows + flow_memberships tables (schema from Week 1).
       - Add an Orchestrator hook: after structural indexing completes,
         re-run flow detection if any of the changed files contain
         entry points (use incremental_trace_flows pattern from CRG
         flows.py:442).

  [10] companybrain/structural/topology.py
       - Port find_hub_nodes: pure SQL query against edges, top N by
         (in_degree + out_degree).
       - Port find_bridge_nodes: build NetworkX graph from edges,
         compute betweenness centrality, sample at k=500 if node count
         exceeds 5000.
       - Write to graph_metrics table with metric_kind in
         ('hub_degree', 'bridge_betweenness'), populate rank within
         workspace.
       - Add a Spring Boot @Scheduled job in company-brain-backend
         (or a Python cron in company-brain-ai) that runs nightly per
         workspace, recomputes the metrics, replaces graph_metrics rows
         for that workspace transactionally. Cron expression: 0 3 * * *
         (3 AM workspace local time, or UTC if simpler).

  [11] companybrain/structural/communities.py — LOWER priority
       - Port detect_communities. Use igraph's Leiden if installed;
         otherwise file-based fallback (group nodes by file path prefix
         to a chosen depth).
       - Write to communities + node_communities tables.
       - If you run out of time, defer to next iteration. Better to ship
         flows + hubs + bridges solidly than half-done communities.

  [22] /skills/review-pr/SKILL.md
       Adapted from CRG's review-pr skill, but with our MCP tool names.
       Workflow:
         1. get_minimal_context(task="review PR #N")
         2. detect_changes
         3. get_review_context
         4. For each high-risk function: query_graph(pattern="callers_of")
            and get_business_context to check for impacted business rules
         5. get_affected_flows
         6. Generate structured output (template included)

  [23] /skills/audit-business-rule/SKILL.md
       NEW skill, CB-specific. The killer demo for our domain.
       Workflow:
         1. get_minimal_context(task="audit refund policy")
         2. audit_business_rule(rule_keyword="refund")
         3. For each matching node: get_business_context, check
            annotation freshness; if stale, flag.
         4. get_impact_radius on each matched node to find all code
            that implements the rule.
         5. suggest_drifts to find stale annotations.
         6. Generate structured output: rule definition, implementing
            code locations, drift status, recommended next actions.

  [24] /skills/onboard-engineer/SKILL.md
       NEW skill, CB-specific.
       Workflow:
         1. get_minimal_context(task="onboard to <domain>")
         2. find_hubs(top_n=5) — the architectural keystones
         3. find_bridges(top_n=5) — the critical paths
         4. list_flows — the named workflows in the system
         5. For top 3 hubs: get_business_context to read the why
         6. Generate a 1-page onboarding summary

  [25] /skills/debug-incident/SKILL.md
       NEW skill, CB-specific.
       Workflow:
         1. get_minimal_context(task="debug <symptom>")
         2. semantic_search_nodes by symptom keywords
         3. For each candidate: query_graph(pattern="callers_of") and
            query_graph(pattern="callees_of")
         4. get_affected_flows for the candidate file
         5. detect_changes to check if recent commits touched this code
         6. get_business_context to understand the intended behavior
         7. Recommend the minimal set of files to read

  [26] /skills/explore-codebase/SKILL.md
       Adapted from CRG.
       Workflow:
         1. get_minimal_context(task="understand <area>")
         2. find_hubs + find_bridges for architectural skeleton
         3. list_flows
         4. get_architecture_overview (if community detection landed;
            else skip)
         5. Sample 3–5 nodes from key communities, get_business_context
         6. Output: architectural summary

  [27] /skills/impact-analysis/SKILL.md
       NEW skill, CB-specific.
       Workflow:
         1. get_minimal_context(task="impact of <change>")
         2. get_impact_radius(direction="both")
         3. For each impacted node: get_business_context to check if
            any business rules are affected
         4. get_affected_flows
         5. suggest_drifts to flag annotations that need updating
         6. Output: impact report grouped by risk level

       Each SKILL.md has front-matter (name, description, argument-hint).
       Each ends with a "Token Efficiency Rules" block matching CRG's
       format: "Always start with get_minimal_context. Use detail_level
       minimal first. Target ≤5 tool calls and ≤800 tokens per task."

  [28] Delete regex blocks in CodeTracer (company-brain-ai)
       - Find _TS_API_CALL_RE, _PY_ROUTE_RE, _TS_IMPORT_RE,
         _PY_IMPORT_RE, _JAVA_MAPPING_RE
       - Replace each usage with a query against the structural index
         (nodes table, filtering by decorator field which the parser
         populates)
       - Keep the old regex code in a deleted-but-recoverable git
         commit so we can fall back if the structural index has gaps
       - Run the existing pipeline tests; fix anything that broke

  [29] Pass 1 LLM extractor optimization
       - Pass 1 (entity extraction) currently re-discovers entities by
         scanning code. After this ADR's structural layer, entities are
         already known with confidence: 1.0.
       - Modify Pass 1 to accept a list of structural entities as input.
         The LLM only adds purpose, dataReads, dataWrites, riskFlags,
         changeRisk — NOT name, type, location.
       - Measure the prompt-token reduction. Target: 30–50% drop on
         Pass 1 prompt size.

  [32] Equivalence + token benchmarks
       - Build a fixture set: 10 representative AI Ask questions across
         your test workspaces (mix of structural, semantic, business-
         context questions).
       - Measure tokens consumed per question BEFORE this whole rollout
         (use a git revert + run baseline) and AFTER (current state).
       - Report: token reduction at p50, p90, p99 — target 5x at p50.

  [33] Latency benchmark
       - Measure incremental update latency for a 500-file workspace.
       - Target: <5 seconds (matches CRG's benchmark on similar repo
         sizes).
       - Measure end-to-end MCP tool latency: get_minimal_context,
         get_impact_radius, query_graph. Target p95 < 500ms (excluding
         LLM call latency).

  [34] License audit
       - Confirm every ported file has the attribution header
       - Run a script: grep -L "ported from tirth8205/code-review-graph"
         across companybrain/structural/, companybrain_mcp/. Anything
         that returns is a port that's missing attribution; fix it.
       - Add a NOTICES file in repo root listing all third-party
         attributions.

  [35] Update ARCHITECTURE-company-brain-v2.md
       - Add the L0–L5 layered diagram from ADR-006
       - Update the system overview to reflect MCP as the AI-assistant
         surface
       - Update the data flow section to mention structural layer's
         role

  [36] Mark proposed ADR-007 as superseded
       - If a proposed-ADR-007 file exists in /docs (the original
         merkle/tree-sitter doc), update its Status to "Superseded by
         ADR-006" and add a link.
       - Same for any ADR-008-tiered-memory draft if present —
         depends-on ADR-006 should be added.

  [38] /docs/SKILLS.md
       - Skill format reference: front-matter fields, body structure,
         token-efficiency rules.
       - How to add a new skill (1-page guide).
       - How to test a skill (point an MCP client at the workspace,
         invoke the skill, count tool calls and total tokens).

OPERATING RULES:

1. Use TodoWrite. Mirror the action items above; mark in_progress /
   completed.

2. Read the required ADRs before writing code.

3. This week is wide. If you can't finish everything, prioritize:
   FIRST  — flows (§9) and topology (§10): Week 3's stub tools depend
            on these returning real data
   SECOND — skills (§22–27): the user-visible payoff of MCP
   THIRD  — cleanup (§28–29): obsoletes old code paths
   FOURTH — validation (§32–34): proves the rollout worked
   FIFTH  — docs (§35–38): finalizes the record
   communities (§11) is explicitly LOWEST priority — defer if needed.

4. Ask clarifying questions via AskUserQuestion if any of these are
   unclear:
   - Where the validation fixture workspace lives, and how to take a
     baseline measurement (will you set up a separate branch?)
   - Whether the @Scheduled topology job should live in Java or Python
     (either works; Java if you want it to share the existing scheduler;
     Python if you want it close to the algorithm code)
   - Whether the parser is already extracting decorator strings into
     a node field that the cleanup step can query (it should be,
     from Week 1; if not, add it now)

5. Make small commits, format "ADR-006 §<N>: <description>".

6. Verify before marking each task complete:
   - Skills: run a fresh MCP session, invoke the skill via /skill-name,
     count tool calls. Target: ≤5 calls per skill workflow.
   - Flows / topology: confirm the data populates correctly on the test
     workspace; the MCP stub tools from Week 3 now return real data.
   - Cleanup: existing pipeline tests pass after regex deletion.
   - Pass 1 optimization: capture prompts before/after, diff the size.
   - Benchmarks: numbers match or beat the targets in the action items.

7. NO copy-paste. Read, understand, write our version. Attribution.

8. NO scope creep. If you find an issue out of scope, write it down for
   the next iteration; do not silently expand.

DELIVERABLE AT END OF WEEK 4:

A complete, demo-able state where:
- An MCP client (Claude Code, Cursor) connects to the workspace and
  invokes the /skill:review-pr workflow end-to-end. The workflow uses
  real flow data, real topology, real business-context lookups.
- The /skill:audit-business-rule workflow demonstrates the cross-domain
  capability: starting from a policy keyword, walks to all implementing
  code, flags drift.
- Frontend has an Architecture tab showing top hubs, top bridges, and
  named flows with criticality scores.
- The five regex blocks in CodeTracer are gone; pipeline tests still pass.
- Pass 1 of the LLM extractor consumes structural entities; prompt size
  dropped 30–50%.
- Validation benchmarks: incremental update <5s on 500-file fixture,
  per-question token consumption down 5x at p50, MCP tool p95 <500ms.
- Documentation: ARCHITECTURE-v2 updated, SKILLS.md added, NOTICES file
  added, superseded ADRs marked.

Final message at the end of the week should include:
- Summary of completed action items by number
- Diff / list of new/modified files
- Benchmark results (the actual numbers, with comparison to targets)
- A demo transcript of one full skill-driven workflow (text is fine)
- A "what was deferred" list — anything from §11 (communities) and any
  scope-creep items you wrote down
- A short "ADR-006 rollout: complete" wrap-up note suitable for
  including in a PR description

If communities (§11) was deferred or if any benchmark missed its target,
flag clearly. The user will decide whether to do a Week 5 cleanup pass
or call it done.

Begin by reading the required ADRs, verifying Week 1+2+3 state, then
create your task list, then ask any clarifying questions before writing
code.
```

---

## Notes for the user (Chinmay)

**The big risk this week is sprawl.** Six skill files, two algorithm ports, a cleanup pass, an LLM-pipeline modification, and benchmarking — that's a lot of work. The prompt explicitly priority-orders the action items so the agent shipping less but right beats shipping all but half-finished. Communities (§11) is marked lowest priority on purpose; Leiden is finicky and not on the critical path for the demo.

**The hubs + bridges nightly job placement.** Java vs. Python is a coin flip. Java gives you the existing Spring Scheduler and lifecycle. Python keeps the algorithm code (which uses NetworkX) in the same module as the rest of the structural layer. Slight preference for Python — keeps the structural layer cohesive — but the agent should ask.

**The Pass 1 optimization is where the real cost savings are.** Today every endpoint extraction runs Pass 1 over raw code, paying ~3K tokens per endpoint to discover entities the parser already knows about. After this change, Pass 1 receives a structured list of entities and only adds semantics. On a 200-endpoint workspace this is roughly $30–$60 in saved API spend per full extraction run, and ~50% faster wall clock. Worth getting right.

**Skills are markdown — easy to write, easy to get wrong.** The most common skill failure mode is "too many tool calls." Re-test each skill after writing by invoking it through an MCP client and counting calls. If a skill takes 10 tool calls when 5 would do, the skill is bloated, not the model.

**Things to spot-check at the end of the rollout.**

- Run an AI Ask query that previously took 30 seconds and 8K tokens. If it's now under 5 seconds and 2K tokens, the rollout worked. If not, find out why.
- Does the frontend Architecture tab actually load fast? It's reading `graph_metrics` which should be small (top N), but the JOINs to nodes/communities can be slow if not indexed.
- Connect a fresh MCP client (clean state) and run /skill:onboard-engineer on a new-to-you area of the codebase. If you actually learn something from the output, the system works. If it gives you generic structural facts you could've grepped for, the skill or the underlying flows need more work.

**If things go badly.** The most likely failure is the validation benchmarks coming in worse than projected. If incremental update is 20s instead of 5s, or token reduction is 2x instead of 5x, the rollout still works — it's just slower / more expensive than the projection. That's a finding, not a bug. Document it; don't paper over it. The team will decide whether to invest in optimization or accept the numbers.

**After Week 4 — what's next.**

The four-week ADR-006 rollout is done. The natural next ADRs (in priority order):

- **Multi-domain collectors** (Slack, Zendesk, Confluence) — promised by ADR-005, unlocks the business-context vision
- **Skills File API** — the structured-knowledge-as-product vision from ARCHITECTURE-v2
- **Universal knowledge schema** (existing ADR-004) — promote to accepted, plan migration
- **Staleness engine** — drift detection that suggest_drifts stubs out

Don't write the next-ADR-rollout handoff yet. Get this one shipped, run it on real workspaces for at least 2 weeks, then plan based on what you learn.

---

## What's done at the end of Week 4 — full ADR-006 rollout summary

When this week ships, the layered architecture from ADR-006 is real:

- **L0** (Artifact Pipeline) — wired to source files via Week 2's indexer
- **L1** (Structural Graph) — parser, risk, flows, topology all live
- **L2** (Semantic Graph) — existing, now feeds off L1's entities (Pass 1 optimization)
- **L3** (Retrieval + Synthesis) — existing, unchanged for this rollout
- **L4** (MCP Tool Surface) — 13+ tools, hint-driven
- **L5** (Skills + Hints) — 6 skill files, hints engine bias-tracking sessions

Every cost-tier path described in ADR-006 § "Cost-tiered retrieval flow" is functional. The cheap paths run first; the LLM is involved only when needed.
