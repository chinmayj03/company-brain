# Architectural Trade-offs: code-review-graph vs company-brain

**Date:** 2026-04-28
**Scope:** Full architecture comparison — not just blast radius. Covers MCP/tool surface, skills, workflows, hints/agents, daemon model, multi-repo, distribution, cost, multi-tenancy, observability.

---

## The two architectures in one frame

These systems made nearly opposite bets at every fork in the road. That's why integration is interesting — neither is wrong, they're optimized for different constraints.

```
                  CRG                              company-brain
                  (local-first, structural)        (cloud-first, semantic)
                  ─────────────────────────        ─────────────────────────
  Audience        individual dev, AI assistant     team, AI assistant, PM/EM
  Distribution    pip install + MCP stdio          self-hosted backend + UI
  State           SQLite per repo                  Postgres + RLS multi-tenant
  Truth source    file system + git                file system + git + tickets +
                                                    PRs + annotations + LLM synthesis
  Index update    file-watch / hook / git diff     batch pipeline run
  Latency target  sub-second per tool              seconds (graph) to minutes (extract)
  Reasoning       tool-driven (LLM picks tools)    pre-computed (graph + retrieval)
  Cost model      $0 (no LLM in pipeline)          $$ per node extracted
```

Both systems claim to solve "give the AI better code context." CRG solves it by **making the LLM read less by giving it sharper tools**. Company-brain solves it by **making the LLM read less by pre-computing semantic answers**. The combination is more powerful than either, which is the thesis of this whole integration push.

---

## Trade-off matrix (the big picture)

| Dimension | code-review-graph | company-brain | Trade-off |
|---|---|---|---|
| **Storage** | SQLite, single-file, per-repo | Postgres + RLS, multi-tenant cloud | CRG: zero ops, no multi-user. CB: real ops cost, real isolation |
| **Parsing** | tree-sitter, 23 languages, structural | Regex + 4-pass LLM, code-only today | CRG: deterministic, cheap, language-broad. CB: extracts meaning, expensive, narrow |
| **Update model** | Hash-diff, dependent expansion, sub-2s on 2,900 files | Full re-extraction, no incremental | CRG: live. CB: batch |
| **Cost / tokens** | Zero LLM cost in indexing | $0.30–$1+ per endpoint extracted | CRG: free; CB: needs token budget |
| **Cold-start latency** | <10s for 500 files | Minutes to hours for first ingest | Asymmetric — CB front-loads cost for richer answers |
| **Query latency** | 0.4–1.5ms semantic search; ~50ms blast radius | ~150ms blast radius + retrieval | CRG: faster (in-process SQLite) |
| **Cross-repo** | Multi-repo registry, stitch at query time | Workspace = N repos, single graph | CB: tighter joins; CRG: looser, simpler |
| **Multi-tenancy** | None (single-user, local) | RLS at DB layer, encryption at rest | CRG: no tenant story; CB: enterprise-grade |
| **Annotations / business context** | None | First-class (`node_context`, encrypted) | Pure asymmetry — only CB has this |
| **Skills / workflows** | 7 markdown skill files w/ tool sequences | None today | CRG ships agent playbooks |
| **MCP tool surface** | 28 tools, hint-driven progression | None — REST endpoints only | CRG: MCP-native; CB: API-native |
| **Hints / next-step agents** | `_hints` field appended to every response | None | CRG: tool-call graph for the LLM |
| **Risk scoring** | Multi-factor (security, tests, callers, flows, communities) | Edge `confidence` only | CRG materially better at this |
| **Refactoring** | `rename` / `dead_code` / `suggest_refactorings` tools | None | CRG ships actionable mutation tools |
| **Wiki / docs** | Auto-generated from communities | Manual + LLM synthesis | CRG: free, structural docs |
| **Distribution** | One pip install, auto-detects 11 AI tools | Docker compose stack (4+ services) | CRG: friction-zero. CB: heavyweight |
| **Self-host viability** | Trivial — already local-first | Designed for it — but real ops |  |
| **Observability** | None (local CLI) | Structured logging + JWT audit | CB has the production hooks |

The entries that are pure asymmetry — where one system has zero of something the other has plenty of — are where the integration value is highest. That's annotations, multi-tenancy, encryption, audit (CB-only) and skills, hints, MCP, refactor tools, structural risk scoring (CRG-only).

---

## The eight architectural patterns worth comparing in detail

### 1. The MCP tool surface as an LLM-facing API

**CRG model.** 28 MCP tools, each registered with `@mcp.tool()` in a single `main.py`. Each tool is small, single-purpose, and returns structured JSON with a `_hints` field telling the LLM what to call next. The tool surface is the contract; everything in CRG exists to serve it. Examples worth noting:

- `get_minimal_context(task="review PR #42")` — *the* entry-point tool. Returns ~100 tokens covering stats, risk score, top 3 communities, top 3 flows, and **suggested next tools based on task keywords**. Every skill says "always call this first."
- `query_graph(pattern="callers_of", target="chargePayment")` — one tool with eight patterns (`callers_of`, `callees_of`, `imports_of`, `importers_of`, `children_of`, `tests_for`, `inheritors_of`, `file_summary`) instead of eight tools. Reduces the tool-discovery burden on the LLM.
- `get_review_context(detail_level="standard"|"minimal")` — same tool, two verbosity levels. The LLM picks based on token budget.
- `refactor(mode="rename"|"dead_code"|"suggest_refactorings")` — unified mutation tool with mode dispatch. Pairs with `apply_refactor(refactor_id)` for stage-then-commit.

**CB model.** REST endpoints in `GraphController`, `IngestController`, hand-rolled query routes. No MCP layer. The frontend calls REST; AI Ask runs server-side and dumps graph dictionaries into prompts.

**Trade-off.** CRG's surface is *designed for an LLM*: small, parameterized, hint-emitting. CB's surface is *designed for a frontend*: REST, paginated, web-shaped. The CRG style is materially better for AI assistants because the LLM doesn't have to discover state — it gets told what to do next in every response. The CB style is better for human users.

**The integration move.** Add a parallel MCP server in front of the existing REST API. Same backing services, two interfaces. The MCP layer wraps each REST endpoint with `_hints` and `detail_level` parameters. We don't lose REST; we gain MCP.

### 2. Hints — the tool-call graph the LLM walks

**CRG model.** `hints.py` defines two structures:

```python
_INTENT_TOOLS = {
    "reviewing":   {"detect_changes", "get_review_context", ...},
    "debugging":   {"query_graph", "get_flow", "semantic_search_nodes"},
    "refactoring": {"refactor", "find_dead_code", "suggest_refactorings"},
    "exploring":   {"list_communities", "get_architecture_overview", ...},
}

_WORKFLOW = {  # for each tool, which tools come next
    "list_flows": [
        {"tool": "get_flow",            "suggestion": "Drill into a specific flow"},
        {"tool": "get_affected_flows",  "suggestion": "Check which flows are affected"},
        ...
    ],
    "get_flow": [...],
    ...
}
```

Every tool response gets a `_hints` field appended with the top 2–3 next tools and one-line suggestions. The session also tracks which intent the agent is in based on which tools it's been calling, and biases hints accordingly.

**CB model.** None. The LLM gets graph data and is left to figure out the next move on its own.

**Trade-off.** CRG's hints are a **statically encoded LLM workflow graph**. The system designer says "when you've just listed flows, the next useful thing is to get one or to see which are affected." This dramatically shortens the LLM's exploration loop. The cost is that hints are hand-crafted and don't generalize beyond the workflows the designer thought of.

CB has none of this. AI Ask is a one-shot prompt with no notion of multi-turn tool progression.

**The integration move.** Steal `hints.py` wholesale. Adapt the `_INTENT_TOOLS` and `_WORKFLOW` maps to whatever tools we expose. Make `_hints` a standard field on every MCP response. This is maybe two days of work and changes the ergonomics of every AI-assistant interaction with our system.

### 3. Skills — agent playbooks as markdown

**CRG model.** Seven `SKILL.md` files in `/skills`: `review-pr`, `review-changes`, `review-delta`, `refactor-safely`, `debug-issue`, `explore-codebase`, `build-graph`. Each is a markdown file with front-matter (`name`, `description`) and a step-by-step tool sequence the AI assistant should follow. Example from `review-pr`:

```
1. Identify the changes (git diff main...<branch>)
2. Update the graph (build_or_update_graph_tool)
3. Get review context (get_review_context_tool)
4. Analyze impact (get_impact_radius_tool)
5. Deep-dive each changed file
6. Generate structured review output (template provided)

Token Efficiency Rules:
- ALWAYS start with get_minimal_context(task=...).
- Use detail_level="minimal" first; escalate only if insufficient.
- Target ≤5 tool calls and ≤800 tokens per task.
```

These are `Claude Code` skills — slot into the same skill mechanism Claude Code already supports.

**CB model.** None today. AI Ask is a single prompt template.

**Trade-off.** Skills are a **portable, version-controllable, token-efficient way to teach an AI how to use a tool surface**. They work because they live alongside the tool surface, both maintained together. Without the tool surface, skills have nowhere to land.

**The integration move.** Once we have an MCP server (move #1) and hints (move #2), write skills for the workflows we care about: `review-pr`, `onboard-new-engineer`, `impact-analysis`, `debug-incident`, `audit-business-rule`. These should live in our repo at `/skills` and ship with our plugin. Steal the structure from CRG verbatim — the format is already proven.

### 4. The minimal-context entry point

**CRG model.** `get_minimal_context(task="...")` is the first tool every skill calls. ~100-token response with: graph stats, risk score, top 3 communities, top 3 flows, top 5 affected entities, test gap count, and task-keyword-based next-tool suggestions. It's an **agent-shaped opening move** — gives the LLM enough situational awareness to make every subsequent tool call cheaper.

**CB model.** No equivalent. Each tool call is independent; there's no pre-loaded "where am I?" briefing.

**Trade-off.** This pattern is small but disproportionately valuable. It collapses the "explore the system" phase from many tool calls into one. The cost is that the response is heuristic — risk score and top items are chosen, not asked for.

**The integration move.** Build `get_minimal_context` for our system. Returns workspace stats + recent changes risk + top owners + top flows + top business-context annotations + suggested next tools based on task keywords. This is the front door to AI-Ask-as-MCP-tool. ~1 day of work.

### 5. The daemon / watch / hook model

**CRG model.** Three layers of automation:

- **`daemon.py`** — multi-repo watch daemon. Reads `~/.code-review-graph/watch.toml`, spawns a `code-review-graph watch` subprocess per repo, monitors them, restarts on death. PID file at `~/.code-review-graph/daemon.pid`. No tmux dependency.
- **`incremental.py:watch()`** — per-repo file watcher using `watchdog`, debounced at 300ms.
- **Claude Code hooks** — `hooks.json` registers `PostToolUse` (matcher: `Write|Edit|Bash`) → runs `code-review-graph update` automatically. SessionStart hook prints graph status to remind Claude the graph is available.

Net effect: the graph stays continuously fresh without the user thinking about it. Edit a file, hit save, the graph updates; ask the AI a question 100ms later, it gets fresh data.

**CB model.** Pipelines triggered manually (or via scheduled batch). No file-watching, no automatic refresh. No hooks into the user's editor. The VS Code extension is read-only against the backend; it doesn't refresh the graph.

**Trade-off.** Continuous-incremental updates are great for individual-developer workflows. They're harder in multi-tenant cloud — every workspace running file watchers means a watcher process per workspace, which doesn't scale. But our **collector pattern (per ADR-005) is the cloud-equivalent**: schedule collectors instead of watching files. The daemon gives us a great pattern for the **self-hosted enterprise** deployment, where each customer might want a local agent watching their own repos and feeding the platform.

**The integration move.** Adopt the daemon model for the **on-prem / self-hosted agent tier** of company-brain. The watch daemon becomes the local "agent" that pushes Artifacts (per ADR-005) up to the cloud backend. For SaaS, stick with scheduled collectors. The two models converge on the same `Artifact` contract.

### 6. The refactor tools — actionable, not just informational

**CRG model.** `refactor(mode="...")` is a *mutation* tool with three modes:

- `mode="rename"` — preview-only. Returns a list of every file/line that would change for a rename, plus a `refactor_id`. Doesn't write.
- `mode="dead_code"` — finds unreferenced functions/classes. Filters out entry points (framework decorators), tests, and type-referenced names.
- `mode="suggest_refactorings"` — community-driven suggestions: oversized communities, low-cohesion clusters, high-bridge nodes that should be decomposed.

Then `apply_refactor(refactor_id)` executes the previewed change. Stage-then-commit pattern. Refactor IDs expire (default 1h) so old previews don't get applied to a changed codebase.

**CB model.** None. Company-brain is read-only. It tells you what *is*, not what to *do*.

**Trade-off.** Refactor tools take a knowledge graph from "library to look things up in" to "advisor that tells you what to fix." For a code-review or tech-debt audit use case, this is the difference between "here's what your codebase looks like" and "here are the 5 specific things to refactor first, here's the diff."

We can't lift these directly because they target file-system mutations, and CB is server-side. But the **pattern of preview → ID → apply** is generalizable. Imagine company-brain having `business_rule_drift_detector(mode="suggest")` that finds policies that conflict with code, returns a `drift_id`, and `acknowledge_drift(drift_id)` that records the human decision. Same pattern, different domain.

**The integration move.** Add a "suggest" tier to AI Ask. Instead of just answering questions, periodically the system surfaces: "5 functions match security keywords but lack tests"; "3 endpoints have no business context annotation"; "the refund policy was last reviewed 14 months ago and the related code has changed 8 times since." Each suggestion gets an ID and an `acknowledge` action.

### 7. Multi-repo registry vs workspace model

**CRG model.** `~/.code-review-graph/registry.json` lists every repo the user has indexed. `cross_repo_search` runs the same query against every registered DB and stitches results. Each repo has its own SQLite file. No cross-repo edges — joins happen at query time, not in the graph.

**CB model.** A `Workspace` is the unit of multi-repo. Multiple repo paths feed one workspace, into one set of `nodes`/`edges` tables. Cross-repo edges (frontend calling backend, shared schemas) are first-class. RLS scopes everything to the workspace.

**Trade-off.** CRG's model is simpler and easier to deploy (no schema changes per repo, just register a new path). CB's model is correct for the cross-repo edge case (which is most of our value — "who calls this API across all repos?"). The cost of CB's model is that adding a new repo requires re-indexing the workspace; CRG's model lets you add a repo without touching others.

These aren't really in conflict. CRG would benefit from cross-repo edges; we'd benefit from CRG's lightweight registry pattern for the **on-prem agent** that registers local repos and pushes them to the cloud workspace.

### 8. Cost shape — where each system spends

**CRG cost shape.** Storage: a few MB to ~100MB per repo (SQLite). Compute: tree-sitter parse on edit, recursive CTE on query. **No LLM cost**. Total monthly cost: $0.

**CB cost shape.** Storage: Postgres at scale. Compute: 4-pass LLM extraction per endpoint. At 200 endpoints × 4 passes × ~3K tokens × Sonnet pricing, the order of magnitude is $50–$200 per workspace per full run, more for big workspaces. Plus Redis, plus AI Ask runtime tokens, plus embedding compute when pgvector lands.

**Trade-off.** CB's cost is justified *only* if the semantic synthesis is the value-add. If the question can be answered structurally — and many can — running the LLM was waste. CRG's structural-first approach is the cost-control discipline we don't have.

**The integration move (cost-aware).** Layer the systems so the cheap path runs first. Every AI Ask question goes through:

1. Try structural answer (CRG-style traversal). If sufficient, return. Cost: $0.
2. Try retrieval against pre-extracted business context. If sufficient, return. Cost: ~$0.001 (embedding lookup).
3. Fall through to LLM synthesis with retrieved context. Cost: ~$0.01–$0.05 per question.
4. Fall through to full LLM extraction (only for unindexed material). Cost: ~$0.30+.

Today company-brain effectively does step 4 every time. Adding step 1 (CRG structural layer) and step 2 (retrieval per ADR-008) reduces per-question cost by 1–2 orders of magnitude.

---

## Where each system has the wrong answer

It's worth saying this directly.

**CRG's wrong answers:**

- **No business context.** A graph that knows `chargePayment` calls `creditCardClient` but cannot tell you the refund policy is fundamentally one-eyed. Most "why does this code exist?" questions need ticket text, PR descriptions, owner annotations — none of which CRG ingests.
- **Single-user.** The whole architecture assumes one person on one machine. No team graph, no shared annotations, no audit log. Useless for the EM/PM use cases we care about.
- **No staleness model.** All edges are equal weight; nothing decays. A function that was renamed last week and the function it used to be still both register as live nodes if neither file was re-parsed. Hash-diff catches file changes but not semantic drift.
- **Risk scoring is purely structural.** "Test gap + caller count + security keyword" misses "this function implements the refund policy that legal cares about" — the highest-risk class of change in any real org.

**Company-brain's wrong answers:**

- **No live updates.** Pipelines are batch; the graph is stale by definition. Every minute between commits and re-run is a minute the AI is answering against outdated state.
- **Token waste on structure.** Asking the LLM "what does this file import?" is a category error. CRG's parser answers in microseconds at zero cost; we're spending 3K tokens to do worse.
- **No tool surface for AI assistants.** REST is the wrong shape. The MCP world has standardized; we should plug in.
- **No agent workflows.** Each AI Ask is a one-shot prompt. No multi-turn tool progression, no skills, no hints.
- **No actionable suggestions.** "Tell me about my codebase" gets analysis. It never gets "here are the 3 things to fix this week" — which is what users actually want to act on.
- **Cold start is brutal.** First-time ingestion of a 200-endpoint workspace is hours, not seconds. CRG's 10-second build for 500 files is the bar.

A combined system fixes both lists.

---

## What an integrated architecture looks like

Stacked, not blended:

```
┌────────────────────────────────────────────────────────────────────┐
│  Layer 5: Skills + Hints (CRG patterns, our domain)                │
│  /skills/{review-pr, audit-policy, onboard-engineer, ...}          │
│  Hint graph in code: every MCP response includes _hints            │
└─────────────────────────────────┬──────────────────────────────────┘
┌─────────────────────────────────▼──────────────────────────────────┐
│  Layer 4: MCP Tool Surface (CRG style)                             │
│  20–30 tools wrapping our REST API, with detail_level params       │
│  get_minimal_context, query_graph, get_impact_radius,              │
│  get_review_context, audit_business_rule, suggest_drifts, ...      │
└─────────────────────────────────┬──────────────────────────────────┘
┌─────────────────────────────────▼──────────────────────────────────┐
│  Layer 3: Retrieval + Synthesis (our existing + ADR-008)           │
│  Graph traversal, full-text search, embeddings, tiered memory      │
│  LLM synthesis for questions that need it                          │
└─────────────────────────────────┬──────────────────────────────────┘
┌─────────────────────────────────▼──────────────────────────────────┐
│  Layer 2: Semantic Graph (our existing)                            │
│  nodes / edges / node_context — business meaning, annotations      │
│  4-pass LLM extraction over Artifacts (per ADR-005)                │
└─────────────────────────────────┬──────────────────────────────────┘
┌─────────────────────────────────▼──────────────────────────────────┐
│  Layer 1: Structural Graph (CRG layer, ported)                     │
│  tree-sitter parse → CALLS / IMPORTS / INHERITS / TESTED_BY        │
│  Multi-factor risk score, flows, communities, hubs, bridges        │
│  SHA-256 dirty-set, bidirectional CTE blast radius                 │
└─────────────────────────────────┬──────────────────────────────────┘
┌─────────────────────────────────▼──────────────────────────────────┐
│  Layer 0: Artifact Pipeline (per ADR-005)                          │
│  Files, tickets, PRs, annotations, Slack — all hashable Artifacts  │
│  Collectors emit, ArtifactWriter dedups, dirty-set drives both     │
│  Layer 1 (structural re-parse) and Layer 2 (LLM re-extract)        │
└────────────────────────────────────────────────────────────────────┘
```

Layer 0 is ADR-005 (already drafted). Layer 1 is what we lift from CRG. Layer 2 is what we already have. Layer 3 is mostly ADR-008 (tiered memory). Layers 4 and 5 are net-new and are the largest user-visible win — they're how AI assistants actually drive the system.

---

## The trade-offs to make consciously

Things we **gain by integrating** that we should be honest are also **commitments**:

- **MCP protocol commitment.** Once we expose MCP, MCP becomes part of our public surface. Versioning, deprecation, documentation — the same discipline as a REST API.
- **Skill maintenance.** Skills are durable artifacts that drift if not maintained. Each skill is a small workflow we're committing to support across model upgrades.
- **Hint maintenance.** The `_WORKFLOW` map has to be updated every time a tool is added. Easy to neglect; degrades silently.
- **Two parsers.** Tree-sitter (Layer 1) for structure, LLM (Layer 2) for meaning. They have to agree on entity identity (the qualified-name scheme is what makes this work). Drift between them is a real risk.
- **Two cost shapes.** Free structural updates and expensive LLM updates running on different schedules creates operational complexity. The dirty-set engine has to decide which is needed for which artifact.

Things we **don't gain** by integrating, in case anyone is hoping:

- **CRG's UX simplicity.** We're a multi-tenant cloud system. The "one pip install" experience isn't replicable. The on-prem agent gets close, but it's a different product surface.
- **CRG's $0 cost shape.** We have an LLM in our pipeline by design. Integration reduces the LLM's *workload* but doesn't remove it.
- **CRG's correctness on adversarial inputs.** Tree-sitter is great but not perfect; CRG's heuristics fail on dynamically generated code, runtime route registration, etc. Adopting CRG inherits these failure modes.

---

## Concrete decision recommendations

In priority order, what to do about all this:

**1. Yes, lift CRG's Layer 1 wholesale.** The case is overwhelming. Tree-sitter parser, hash-diff incremental, multi-factor risk score, flow detection, hubs/bridges, bidirectional blast-radius CTE. Roughly 4 weeks of porting work for ~10x improvement in structural intelligence and cost reduction across every downstream LLM call.

**2. Yes, add an MCP layer in front of REST.** This is the single biggest UX change for AI-assistant users. ~2 weeks of work to wrap existing endpoints. Gives us instant compatibility with Claude Code, Cursor, Windsurf, etc. without changing our backend.

**3. Yes, port the hints + minimal-context patterns.** Cheap (1 week), large effect on AI conversation quality. Stops the LLM from getting lost in our system.

**4. Maybe, for skills.** Worth doing once #2 lands. Skills without the MCP surface are pointless; skills with it are high-leverage. Aim for 5–6 skills covering review/audit/onboard/debug/refactor/explore.

**5. No, on the daemon model for SaaS.** Stick with scheduled collectors. Adopt the daemon for the on-prem/self-hosted agent tier *only*.

**6. No, on the SQLite store and registry pattern.** Our Postgres+RLS architecture is correct for our market. Don't fork.

**7. Defer, on the refactor mutation tools.** The pattern is interesting (preview → ID → apply) but the code-mutation surface is out of scope for company-brain v1. Revisit when we have the "actionable suggestions" feature in roadmap.

The TL;DR for the team: **this is a layered integration, not a replacement on either side**. CRG's structural layer + company-brain's semantic layer + CRG-style MCP/skills/hints on top is a strictly better system than either has alone.
