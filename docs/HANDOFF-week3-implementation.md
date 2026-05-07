# Implementation Handoff — Week 3 of ADR-006

**Purpose:** Brief a fresh Claude/Sonnet session to execute Week 3. Weeks 1 and 2 have shipped. This week stands up the MCP server, ports the hints engine, and wraps the existing REST surface with the first batch of MCP tools.

**Prerequisites:** Weeks 1 and 2 must be complete and merged. Verify before starting:

- All Week 1 deliverables (parser, risk score, frontend display)
- All Week 2 deliverables (incremental indexer wired to `artifact_change_events`, bidirectional `BlastRadiusService` CTE)
- A test workspace exists where editing a file triggers re-indexing in <5 seconds
- The Spring Boot REST API at the standard URL (`/api/v1/...`) is running locally and answering basic queries

If any are missing, stop and finish the prior weeks first.

---

## How to use this doc

1. Open a fresh Cowork session in the `company-brain` project.
2. Paste the prompt below as the first message.
3. The agent reads the required ADRs, sets up a task list, starts work.

---

## The prompt to paste (copy verbatim)

```
You are implementing Week 3 of ADR-006. Weeks 1 and 2 have shipped (the
structural layer is live and incremental indexing works). The decision is
final; you are executing, not designing.

This week is the first user-facing milestone of the ADR-006 rollout: an MCP
server that any AI assistant (Claude Code, Cursor, Windsurf, Codex) can
connect to and drive. By end of week, a developer should be able to point
Claude Code at this workspace and have it call MCP tools to do real work.

REQUIRED READING (in this order, before writing any code):

1. /Users/chinmayjadhav/Documents/Claude/Projects/company-brain/PROJECT_CONTEXT.md
2. /Users/chinmayjadhav/Documents/Claude/Projects/company-brain/docs/ADR-003-multi-tenancy.md
   ← critical for MCP auth — the new server needs JWT + workspace scoping
3. /Users/chinmayjadhav/Documents/Claude/Projects/company-brain/docs/ADR-006-adopt-crg-structural-and-mcp-layer.md
   ← THE plan. This week = Week 3 in § Implementation Phases.
   Read § "MCP server design" carefully — it specifies the tool list.
4. /Users/chinmayjadhav/Documents/Claude/Projects/company-brain/docs/ANALYSIS-code-review-graph.md
   ← reference for what to port from CRG.
5. /Users/chinmayjadhav/Documents/Claude/Projects/company-brain/docs/TRADEOFFS-crg-vs-companybrain.md
   § "Hints — the tool-call graph the LLM walks" and § "The MCP tool surface
   as an LLM-facing API" are the conceptual frame for this week.

VERIFY WEEK 1 + 2 STATE BEFORE WRITING CODE:

  - git log --oneline | head -30   # confirm Week 1 + 2 commits present
  - companybrain/structural/ should contain parser.py, risk.py, changes.py,
    dependents.py, indexer.py
  - BlastRadiusService.java should support a `direction` parameter
  - Run a smoke test against the running REST API to confirm
    GET /api/v1/blast-radius/<node-id> returns risk-scored nodes from
    both directions.

REFERENCE REPO TO PORT FROM:

Clone CRG (MIT, source for the algorithms):

  cd /tmp && rm -rf code-review-graph
  git clone --depth 1 https://github.com/tirth8205/code-review-graph.git

This week you'll port from these CRG files:
  - code_review_graph/main.py — MCP tool registration patterns (uses
    @mcp.tool() decorator from FastMCP). Note: CRG uses stdio transport;
    we need HTTP+SSE for SaaS deployments.
  - code_review_graph/hints.py — the _INTENT_TOOLS and _WORKFLOW maps,
    the session tracking, the _hints injection.
  - code_review_graph/tools/context.py — get_minimal_context.
  - code_review_graph/tools/query.py — query_graph (callers_of, callees_of,
    imports_of, importers_of, children_of, tests_for, etc.) and
    semantic_search_nodes (we'll wire to FTS for now; embeddings later).
  - code_review_graph/tools/review.py — get_review_context, detect_changes
    pattern (this week we wire to existing endpoints, don't re-implement).

Clean-room port. Attribution header required:

  # Algorithm ported from tirth8205/code-review-graph (MIT License).
  # Original: <relative path in CRG>

WEEK 3 SCOPE (from ADR-006 § Implementation Phases):

Goal: a running MCP server, 10 tools, hint-driven, demo-able. By end of
week a developer should connect Claude Code or Cursor and run a workflow.

Action items from ADR-006 to complete this week (numbers match ADR-006):

  [3]  Create company-brain-mcp directory.
       - FastAPI/Starlette skeleton OR FastMCP if it supports HTTP+SSE
         (check current FastMCP version; CRG uses stdio because it's a
         CLI; we need both transports).
       - Project layout:
           company-brain-mcp/
             pyproject.toml
             Dockerfile
             src/companybrain_mcp/
               __init__.py
               server.py          # MCP entry point, transport selection
               auth.py            # JWT validation + workspace scoping
               rest_client.py     # HTTP client to company-brain-backend
               hints.py           # ported from CRG
               tools/
                 __init__.py
                 context.py       # get_minimal_context
                 structural.py    # impact, query_graph, large_funcs, hubs, bridges
                 semantic.py      # business_context, audit_business_rule
                 workflow.py      # detect_changes, get_review_context
                 flows.py         # list_flows, get_flow (week 4 finishes flows)
       - Add to docker-compose.infra.yml as a new service. Expose on port 8765.
       - JWT validation reuses the same key as company-brain-backend.
         Tools must extract workspace_id from the token claims and pass
         it to every REST call.

  [15] companybrain_mcp/server.py
       - Two transports: HTTP+SSE (default, for SaaS) and stdio (for
         on-prem / local agent tier).
       - Register all tools (start with the 10 listed below).
       - Wrap every tool response with the hints middleware (§16).
       - Health endpoint at /healthz that pings the backend REST API.
       - Use uvicorn for HTTP transport.

  [16] companybrain_mcp/hints.py
       - Port _INTENT_TOOLS dict — adapt the values to OUR tool names:
           "reviewing":   {"detect_changes", "get_review_context",
                           "get_business_context", "get_impact_radius"}
           "debugging":   {"query_graph", "get_flow", "semantic_search_nodes",
                           "get_business_context"}
           "auditing":    {"audit_business_rule", "get_business_context",
                           "list_artifacts"}      # NEW intent for our domain
           "exploring":   {"get_minimal_context", "find_hubs", "find_bridges",
                           "list_flows"}
       - Port _WORKFLOW dict — for each tool, list 2–3 follow-on tools with
         one-line suggestions. Keep CRG's structure; just rename tools.
       - Add a SessionState class that tracks recent tool calls in-memory
         (~60s TTL) so hints can be biased toward the current intent.
       - Provide a get_hints(tool_name, session_id) function. The middleware
         in server.py calls this for every response and appends _hints.

  [17] companybrain_mcp/tools/context.py — get_minimal_context
       - Accepts: task (str), changed_files (list[str] | None),
         workspace_id (auto from JWT)
       - Calls backend REST endpoints to gather:
           • workspace stats (total nodes, edges, files)
           • risk score for any current changes (if changed_files provided
             OR git status detects changes)
           • top 3 communities (will return empty until Week 4 ships them)
           • top 3 critical flows (will return empty until Week 4 ships them)
           • top 5 affected entity names
           • test_gap_count
       - Suggests next tools based on task keywords (port CRG's logic in
         tools/context.py:88+).
       - Returns ~100 token compact response. Must include _hints.

  [18] companybrain_mcp/tools/structural.py — five tools
       a) get_impact_radius(node_id_or_qualified_name, max_depth=2,
          direction="both", detail_level="standard")
          → calls GET /api/v1/blast-radius/<id>?direction=both&depth=2
       b) query_graph(pattern, target, limit=20)
          patterns: callers_of, callees_of, imports_of, importers_of,
                    children_of, tests_for, inheritors_of, file_summary
          → maps each pattern to existing GraphController endpoints
       c) find_large_functions(threshold_lines=80, limit=20)
          → calls GET /api/v1/nodes?kind=Function&min_lines=80
          (add the endpoint in GraphController if not present;
          straightforward query)
       d) find_hubs(top_n=10)
          → calls a new endpoint backed by graph_metrics table
          (will be empty until Week 4 populates; return an empty list with
          a note "topology metrics not yet computed for this workspace")
       e) find_bridges(top_n=10)
          → same pattern as find_hubs

  [19] companybrain_mcp/tools/semantic.py — three tools
       a) get_business_context(node_id_or_qualified_name)
          → calls GET /api/v1/nodes/<id>/context
          Returns the node_context entries (annotations, PR text, ticket
          links, LLM synthesis), formatted compactly.
       b) audit_business_rule(rule_keyword)
          → semantic_search across node_context entries with annotation_type
          = 'business_context' or 'invariant' matching the keyword
       c) suggest_drifts(workspace_id_implicit)
          → for now, return a stub that lists nodes whose latest
          node_context entry is older than 90 days. Full drift detection
          comes later. Mark as preview / experimental in the tool docstring.

  [20] companybrain_mcp/tools/workflow.py — two tools
       a) detect_changes(base="HEAD~1", changed_files=None)
          → calls existing /api/v1/changes/analyze (or equivalent;
          if not present, add a thin endpoint that calls the existing
          changes-analysis logic).
          Returns risk-scored change list.
       b) get_review_context(base="HEAD~1", max_depth=2,
          detail_level="standard")
          → calls /api/v1/review-context (add this endpoint; it composes
          impact + business context + diff snippets).

  [21] companybrain_mcp/tools/flows.py — three stub tools
       Add the function signatures and route them to /api/v1/flows
       endpoints (not yet implemented). Tools should return a clear
       "flows not yet computed" response with an empty list. Week 4
       will populate them.
       a) list_flows(limit=20)
       b) get_flow(flow_id)
       c) get_affected_flows(changed_files=None)

  [37] docs/MCP-INTEGRATION.md
       - How to connect Claude Code to a CB workspace via MCP.
       - How to connect Cursor.
       - How to connect Windsurf.
       - Auth flow: where to get the JWT, how to scope to a workspace.
       - Example .mcp.json configurations for each platform.
       - Troubleshooting: most common connection failures.

OPERATING RULES:

1. Use TodoWrite to create a task list before starting.

2. Read the listed ADRs before writing code. ADR-006's "MCP server design"
   section is the spec — follow it.

3. Ask clarifying questions via AskUserQuestion if any of the following is
   unclear:
   - Whether company-brain-backend already exposes the REST endpoints the
     MCP tools call. Some may need to be added (this is fine, do it as
     part of the corresponding tool's commit).
   - Whether to use FastMCP (https://github.com/jlowin/fastmcp) or a hand-
     rolled MCP server with FastAPI. FastMCP is canonical but check if it
     supports HTTP+SSE transport at the version we'd pin. If not, hand-roll.
   - Where the JWT signing key lives (env var, config file, KMS).
   - Whether to deploy MCP behind the same load balancer as the REST
     backend or separate it.

4. Make small commits. Format: "ADR-006 §<N>: <description>".

5. Verify before marking each task complete:
   - server.py runs, /healthz returns 200, MCP handshake completes.
   - hints.py: unit tests show that calling list_flows then get_flow
     returns hints biased toward the "exploring" intent's neighborhood.
   - Each tool: integration test that calls the tool through the MCP
     handler (not directly), confirms the response shape matches the MCP
     spec, and confirms _hints is present.
   - End-to-end smoke test: connect a real MCP client (use `mcp` CLI or
     a minimal Python MCP client) to localhost:8765, call
     get_minimal_context, confirm the response is ~100 tokens and
     includes _hints.

6. NO copy-paste from CRG. Read, understand, write our version. Attribution
   headers required.

7. Do NOT start Week 4 work (skills, flow detection, hubs/bridges
   computation, frontend Architecture tab). Stop and report.

8. Surface design decisions via AskUserQuestion, not assumptions.

DELIVERABLE AT END OF WEEK 3:

A demo-able state where:
- The MCP server runs locally on port 8765, healthz green.
- Connecting Claude Code or Cursor with the documented .mcp.json picks up
  all 10 (+3 stub) tools and their descriptions.
- A user can ask Claude Code "review my current branch" and it walks
  through get_minimal_context → detect_changes → get_review_context →
  query_graph (for high-risk functions) using only the hints provided in
  the responses. No human guidance needed.
- The MCP-INTEGRATION.md guide walks a fresh user from "I don't know what
  this is" to "I'm using it" in <10 minutes.

Final message at the end of the week should include:
- Summary of which ADR-006 action items completed (by number)
- Diff / list of new/modified files (the new company-brain-mcp service
  is a substantial addition — call it out as a top-level deliverable)
- A demo recording or transcript of an MCP client driving the server end-
  to-end on a real workspace (text transcript is fine; video optional)
- Any deferred decisions / known issues
- "Ready for Week 4" note describing repo state

Begin by reading the required ADRs, verifying Week 1+2 state, then create
your task list, then ask any clarifying questions before writing code.
```

---

## Notes for the user (Chinmay)

**FastMCP versus hand-rolled.** As of writing, FastMCP supports HTTP+SSE in recent versions but has had churn. The agent should check the latest. If FastMCP is solid, use it — much less code. If not, FastAPI + the MCP protocol spec works fine, just a couple hundred more lines. Either way, the MCP-side architecture (tools, hints, transport) is identical.

**The JWT integration is where things get sticky.** ADR-003 specifies workspace scoping via `current_setting('app.workspace_id')`. The MCP server needs to: validate the JWT, extract the workspace_id claim, and pass it on every REST call to the backend (which sets the session variable for RLS). The agent will likely need to ask whether to reuse the existing JWT issuer or stand up a separate one for MCP — the answer is "reuse" but they should ask rather than guess.

**The stub tools (`find_hubs`, `find_bridges`, `list_flows`, etc.) are intentional.** Returning an empty list with a "not yet computed" note is correct behavior for Week 3. It lets clients call them without errors; Week 4 fills in the actual data. The agent might be tempted to skip them entirely — push them not to. Empty-but-callable stubs are the right way to define the surface this week.

**On hint maintenance.** Adding a new intent (`auditing`) for our domain is the kind of small invention I want the agent to make. The CRG hints engine has four intents (reviewing, debugging, refactoring, exploring); we need a fifth for business-context audits. The prompt seeds this; if the agent invents another (`onboarding`?) that's also fine.

**Things to spot-check after the week.**

- Connect a real MCP client (the `claude mcp` CLI works for this) and confirm tool discovery works.
- Confirm `_hints` appears in every response and the suggestions look sensible.
- Check that workspace_id from the JWT is actually being passed to REST calls (look at backend logs).
- Test the JWT failure cases: missing token, expired token, wrong workspace claim.

**If Week 3 finishes early.** The MCP-INTEGRATION.md doc is the obvious place to put extra polish. Better docs for this layer pay off through every subsequent month of usage. Don't start Week 4.

---

## What's deferred to Week 4 (do not start)

- Skill markdown files (`/skills/*.md`)
- Flow detection algorithms (entry-point detection, BFS tracing, criticality)
- Hub + bridge nightly computation job
- Frontend Architecture tab
- Cleanup of regex blocks in CodeTracer
- Pass-1 LLM extractor optimization (consume structural entities)
- Validation benchmarks (token reduction, latency)
- Documentation finalization
