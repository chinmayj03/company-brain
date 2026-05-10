# ADR-0052 — Comprehensive Feature Adoption (every Claude Code + Antigravity pattern we can leverage)

**Status:** Proposed
**Date:** 2026-05-10
**Deciders:** Chinmay (product), pipeline-team
**Builds on:** ADR-0051 (agentic harness migration — the 4 foundational phases)
**Scope:** Exhaustive feature catalog + per-feature adoption decision + use-case mapping

---

## Context

ADR-0051 proposed the four foundational phases (HarnessLoop, sub-agents,
skills, hooks). The user's response: *"we want all features of both
which we can leverage"*. This ADR is the exhaustive list — every
Claude-Code and Antigravity feature, mapped to a concrete use case in
our extraction pipeline, with an adopt / adapt / skip decision and the
phase it lands in.

The goal is feature parity with the best-in-class agentic harnesses,
**adapted to the extraction-pipeline domain**, so future asks ("add a
new framework", "let users customise extraction", "make queries
faster") are answered by a feature already in the harness rather than
another point patch.

---

## Full feature catalog

### From Claude Code (35 features)

| # | Feature | Adopt? | Use case in extraction pipeline | Phase |
|---|---|---|---|---|
| 1 | Tool-use loop (assistant → tool_use blocks → tool_result → repeat) | ✅ Adopt | Replace `orchestrator.py` linear stages with prompt-controlled tool dispatch | P1 (ADR-0051) |
| 2 | Sub-agents via `Task` tool (fresh context windows for parallel work) | ✅ Adopt | One sub-agent per file/class for extraction; parent only sees results | P2 (ADR-0051) |
| 3 | Skills (markdown files loaded on demand by description match) | ✅ Adopt | One SKILL per framework: `spring-boot`, `fastapi`, `nestjs`, `django`, `rails`, `nextjs` | P3 (ADR-0051) |
| 4 | Memory files (`CLAUDE.md` per repo, auto-loaded) | ✅ Adopt | `.brain/BRAIN.md` carrying repo-specific gotchas + auto-appended observations | P3 (ADR-0051) |
| 5 | Hooks (`PreToolUse`, `PostToolUse`, `Stop`, `SessionStart`, etc.) | ✅ Adopt | Pipeline events: `pre_extraction`, `post_extraction`, `on_truncation`, `pre_storage`, `on_query` | P4 (ADR-0051) |
| 6 | Permission model (auto / ask / deny per tool) | ✅ Adopt | Read tools auto; DB writes ask in interactive mode; secret-scanning auto-deny | P4 (ADR-0051) |
| 7 | TodoWrite (structured task list, surfaced to user) | ✅ Adopt | Per-job todo tracker streamed via SSE: "extracting CompetitivenessController.java (3/15 files)" | P4 (ADR-0051) |
| 8 | Streaming (model output + tool calls stream incrementally) | ✅ Adopt | Stream extraction progress + partial results to UI; SSE on `/pipeline/jobs/{id}/stream` | P4 (ADR-0051) |
| 9 | Plan mode (preview what the agent will do without doing it) | ✅ Adopt | `--plan` flag on extraction CLI: SpecialistAgent runs, plan rendered, user approves before paying for ContextAgent | P4 |
| 10 | Slash commands (user-defined shortcuts) | ✅ Adopt | `/extract`, `/query`, `/verify`, `/diff <commit>`, `/cost`, `/explain <method>`, `/wipe` | P5 (NEW — see below) |
| 11 | MCP servers (external tool integration via standard protocol) | ✅ Adopt | Expose company-brain itself AS an MCP server so other tools (IDEs, ChatGPT, etc.) can query it | P5 |
| 12 | Plugins / marketplaces (installable bundles of skills + MCPs + commands) | ✅ Adopt | "company-brain plugin marketplace" — orgs publish their custom framework skills + extraction strategies | P6 (NEW) |
| 13 | Bash tool with sandbox | ✅ Adopt | Sandboxed `run_repo_command` tool: `mvn test`, `npm test`, `git diff` — agent uses repo's own tooling | P5 |
| 14 | File-state tracking (must Read before Edit) | ⚠ Adapt | We don't write to user files, but adopt the pattern for `BRAIN.md`: must read current state before auto-appending | P3 |
| 15 | `Glob` / `Grep` tools (built-in code search) | ✅ Adopt | Tool catalog: `glob_files(pattern)`, `grep_code(regex)`. Used by sub-agents during extraction | P1 |
| 16 | `WebFetch` / `WebSearch` tools | ✅ Adopt | Sub-agent tool for "what does this Spring annotation do?" — fetch official docs to enrich BusinessContext | P5 |
| 17 | Compaction (auto-compress long conversations when context fills) | ✅ Adopt | When extracting a 1000-method repo, parent harness conversation will fill — auto-compact preserving the plan + key decisions | P4 |
| 18 | Settings hierarchy (user / project / enterprise) | ✅ Adopt | `~/.brain/settings.json` (user), `.brain/settings.json` (repo), org-level via central config service | P5 |
| 19 | IDE integrations (VS Code, JetBrains) | ✅ Adopt | "Right-click on a method → Ask brain" — calls `/query` with the method qname pre-filled. Sidebar shows brain context for current file | P7 (NEW) |
| 20 | Output styles (concise / default / verbose) | ⚠ Adapt | Per-query verbosity: `summary_only` / `with_evidence` / `full_trace` modes on `/query` | P5 |
| 21 | Status line (customisable bottom-of-terminal info) | ⚠ Adapt | CLI status line during `make demo run-cli`: `[STAGE: ContextAgent] [COST: $0.012] [FILES: 3/15] [ETA: 12s]` | P4 |
| 22 | Session management (resume, list, transcript) | ✅ Adopt | Resume mid-extraction (already exists primitively via checkpoints; promote to first-class). `brain session list / resume <id> / transcript <id>` | P4 |
| 23 | Image support (multimodal: read screenshots / diagrams) | ✅ Adopt | When repo has architecture diagrams in `docs/`, vision-extract them as Artifact + relate to entities | P6 |
| 24 | Worktrees (isolated git workspaces for parallel agent work) | ✅ Adopt | Each extraction job gets a worktree at the target commit — concurrent extractions don't fight over `git checkout` state | P5 |
| 25 | Cost tracking (per-session, per-model) | ✅ Adopt | Already partially via job summary; promote to per-tool-call cost so we can see "ContextAgent batch #3: $0.004" | P4 |
| 26 | Model selection per role (fast / balanced / synthesis / reasoning / query) | ✅ Already done | `.env` already configures these; no change needed | — |
| 27 | Headless mode (`-p` flag for CI / automation) | ✅ Adopt | `brain extract --headless --json` for CI integration: extract on every PR merge, exit non-zero on regression | P5 |
| 28 | Scheduled tasks (cron-like background runs) | ✅ Adopt | `brain schedule daily-rebuild --repo X --endpoint Y` — keeps brain fresh without manual triggers | P6 |
| 29 | Notebook support (Jupyter `.ipynb`) | ✅ Adopt | New language `notebook` in chunker; extract cell entities; useful for ML repos | P6 |
| 30 | Sub-agent orchestration with typed agent variants | ✅ Adopt | Specialised sub-agents: `ExtractorAgent` (per file), `VerifierAgent` (cross-checks claims), `ResearchAgent` (looks up framework docs) | P2 |
| 31 | Per-tool allow/deny lists | ✅ Adopt | Workspace policy: "this workspace cannot use `web_fetch`" / "this workspace cannot write to neo4j" | P4 |
| 32 | CLI + SDK (both interfaces) | ✅ Adopt | Existing CLI stays; add `companybrain` Python SDK for programmatic use (currently just FastAPI HTTP) | P5 |
| 33 | Resume from checkpoint (long-running tasks) | ✅ Already done | `_checkpoint_save` already exists; promote to per-tool-call granularity in P1 harness | P1 |
| 34 | Edit confirmation (interactive vs auto-accept) | ⚠ Adapt | "Confirm DB write" prompt in interactive mode; `--yes` flag for non-interactive | P4 |
| 35 | Output JSON formats for SDK use | ✅ Adopt | `brain extract --output json` returns structured run summary; useful for piping into other tools | P5 |

### From Antigravity (15 features)

| # | Feature | Adopt? | Use case in extraction pipeline | Phase |
|---|---|---|---|---|
| A1 | Multi-pane "rooms" (browser, terminal, code, docs) | ✅ Adopt | Typed surfaces: `code:`, `db:`, `git:`, `api:` (running service), `docs:` (markdown / ADRs), `metrics:` (telemetry) | P5 |
| A2 | Browser room (real Chromium under agent control) | ⚠ Adapt | Sub-agent can launch a headless browser to verify a frontend's API call against the brain (parity check) | P6 |
| A3 | Terminal room (real shell) | ✅ Adopt | `terminal_exec(command, repo_path)` tool — sandboxed; for `mvn test`, `git log`, etc. | P5 |
| A4 | Code room (file tree + editor) | ⚠ Adapt | UI feature, not pipeline; expose via IDE integration in P7 | P7 |
| A5 | Persistent project context across IDE sessions | ✅ Already partially | `.brain/manifest.json` exists; promote to richer "session resume" state in P4 | P4 |
| A6 | Live reasoning (shown before acting) | ✅ Adopt | Specialist's plan rendered to UI before ContextAgent fires; user can kill the run if plan is wrong | P4 (overlaps with #9) |
| A7 | Capability flags per workspace | ✅ Adopt | Each workspace declares granted capabilities; harness enforces intersection with tool requirements | P4 (overlaps with #6) |
| A8 | Workspace concept (entire project state inc. git, env, files) | ✅ Adopt | `Workspace` data class consolidating today's scattered `WORKSPACE_ID`, `WORKSPACE_SLUG`, repo path, branch, capabilities | P5 |
| A9 | Multi-step plans visible to user | ✅ Adopt | TodoList renders as a kanban-style tree in UI: file → methods → entities → edges, all live | P4 |
| A10 | Artifact pinning (work-in-progress for review) | ✅ Adopt | "Pin this entity" in UI — entities flagged for human review before promoting from `proposed` to `accepted` status | P6 |
| A11 | Diffing UI (file-level diff before commit) | ⚠ Adapt | Brain diff before storage: shows new/changed/deleted entities since last run; user approves before write | P4 |
| A12 | Git integration (branch management, commit prep) | ✅ Adopt | `git_branch_diff(branch_a, branch_b)` tool — extract only the entities affected by a branch's changes | P5 |
| A13 | Docs / notes integration (sticky notes) | ✅ Adopt | Per-entity user notes stored alongside auto-extracted context: "Adam said this method is being deprecated 2026-Q4" | P6 |
| A14 | Multi-model support (Gemini, GPT, etc.) | ✅ Already done | Provider abstraction exists; just keep it healthy | — |
| A15 | Tool registry exposed to user | ✅ Adopt | `brain tools list` CLI command; per-job tool-call telemetry surfaces which tools fired | P4 |

---

## Decision

Adopt **47 of the 50 features** above. The three skipped/adapted-only:
- A2 (browser room) — full browser MCP is overkill; we adapt to "headless verifier sub-agent" only when needed.
- #20 (output styles) — we adapt as `verbosity` modes on `/query` only, not on every response.
- A4 (code-room editor) — pure UI; we expose via IDE integration (P7) instead of building our own editor.

The 47 features land across **seven phases** (P1–P4 from ADR-0051,
plus three new phases P5–P7 for slash commands / IDE / marketplace).
Each phase is independently shippable; total estimated effort is
**~6 weeks of one person's time** to get full Claude-Code-equivalent
parity.

---

## Phase plan (extends ADR-0051's 4 phases with 3 more)

### Phase 1 — Tool-use harness (~5 days) — ADR-0051 P1 unchanged

Features in this phase: 1, 15, 33

Outputs the `HarnessLoop`, the `ToolRegistry`, and the basic tool catalogue
(`discover_routes`, `find_entry_handler`, `read_file`, `extract_methods_from_class`,
`write_to_brain`, `glob_files`, `grep_code`, `finalize_brain`). The pipeline now
runs as `assistant → tool_use → tool_result → repeat` instead of as a hardcoded
stage machine.

### Phase 2 — Sub-agents and parallel fan-out (~5 days) — ADR-0051 P2 unchanged

Features: 2, 30

Output: `spawn_extractor`, `spawn_verifier`, `spawn_research` Task-style
sub-agent tools. Each sub-agent has its own context window and configurable
tool subset.

### Phase 3 — Skills + memory (~5 days) — ADR-0051 P3 unchanged

Features: 3, 4, 14

Output: `frameworks/{spring-boot,fastapi,nestjs,django,rails,nextjs}/SKILL.md`,
the skill-detector, and `.brain/BRAIN.md` per-repo memory file with the
file-state-tracking pattern for safe auto-append.

### Phase 4 — Hooks + permissions + streaming + introspection (~7 days)

Features: 5, 6, 7, 8, 17, 21, 22, 25, 31, 34, A5, A6, A7, A9, A11, A15

This phase is bigger because the user-visible surface lives here:

- **Hooks** at `pre_extraction`, `post_extraction`, `on_truncation`,
  `pre_storage`, `post_storage`, `pre_query`, `post_query`,
  `session_start`, `session_end`. Shell scripts in `.brain/hooks/*.sh`.
- **Permission model**: per-tool capability declarations × per-workspace
  grants. Replaces the scattered `BRAIN_*` env flags and auto/ask/deny
  for any tool that touches DB or network.
- **TodoWrite** equivalent: structured per-job task tree (file → methods
  → entities → edges) updated by sub-agents. Streamed via SSE.
- **Streaming**: `/pipeline/jobs/{id}/stream` SSE endpoint. UI subscribes;
  no more 2-second polling.
- **Compaction**: when the parent harness conversation grows beyond 80%
  of the context window, auto-compact (preserve the plan, drop the
  per-tool-result transcripts that fed completed sub-agents).
- **Status line**: CLI `make -f Makefile.demo run-cli` prints a live
  bottom-line `[STAGE: ContextAgent] [COST: $0.012] [FILES: 3/15] [ETA: 12s]`.
- **Session management**: `brain session list / resume / transcript` for
  long-running or resumed jobs. Builds on existing checkpoint code.
- **Cost tracking**: per-tool-call cost in the job summary, not just
  per-stage.
- **Brain diff before storage**: list of new / changed / deleted entities
  since previous run; interactive `--yes` flag for headless mode.
- **Tool registry exposed**: `brain tools list` CLI + per-job
  tool-call telemetry.

### Phase 5 — Slash commands + MCP server + workspace + headless + browser+terminal rooms (~7 days)

Features: 9, 10, 11, 13, 16, 18, 24, 27, 32, 35, A1, A3, A8, A12

The "agent meets the world" phase:

- **Slash commands** (`.brain/commands/*.md`): `/extract`,
  `/query`, `/verify`, `/diff <commit>`, `/cost`, `/explain <method>`,
  `/wipe`, `/stats`, `/init` (bootstrap a new repo's BRAIN.md), `/skills list`.
- **MCP server**: company-brain itself becomes an MCP server. IDEs,
  ChatGPT, Claude Code, etc. can connect and query the brain.
  Implements `tools/list`, `tools/call`, `resources/list` per the MCP spec.
- **Workspace** dataclass: consolidates `WORKSPACE_ID`, `WORKSPACE_SLUG`,
  repo path, branch, commit SHA, capabilities, env vars. Replaces the
  10+ scattered config sources today.
- **Bash tool with sandbox**: `run_repo_command(cmd, cwd)` —
  bubble-wrap-isolated, time-limited, output-truncated. Used by sub-agents
  for `mvn test`, `git log`, `npm run build`, `psql -c '\d table'`.
- **WebFetch / WebSearch tools** for sub-agents to enrich BusinessContext
  with framework documentation.
- **Settings hierarchy**: `~/.brain/settings.json` (user), `.brain/settings.json`
  (repo), `BRAIN_ENTERPRISE_CONFIG_URL` (org). Resolved with deep merge.
- **Worktrees**: every extraction job creates a `git worktree add` for
  the target commit so concurrent jobs don't fight over `HEAD`. Cleaned
  up on job complete.
- **Headless mode**: `brain extract --headless --json` for CI. Exit code
  reflects extraction success + drift detection. JSON output is
  pipe-able.
- **CLI + SDK**: `companybrain` Python SDK. `brain = CompanyBrain(repo);
  result = await brain.extract(endpoint, method)`. CLI is the SDK's
  thin wrapper.
- **Output JSON format**: every CLI command supports `--output json` for
  machine consumption.
- **Multi-pane rooms**: typed surfaces — `code:`, `db:`, `git:`, `api:`
  (running service), `docs:`, `metrics:`. Sub-agents pick which to query.
- **Terminal room**: actual shell access wrapped as the bash tool above.
- **Workspace dataclass** replaces scattered config.
- **`git_branch_diff(branch_a, branch_b)` tool**: extract only entities
  affected by a branch's changes. Massive cost win on large repos
  (extract a 5-file PR for $0.001 instead of $0.05).

### Phase 6 — Marketplace + scheduled + notebook + image + browser-verifier + artifact pinning + notes (~5 days)

Features: 12, 23, 28, 29, A2, A10, A13

The "ecosystem" phase:

- **Plugin marketplace**: orgs publish their framework skills + extraction
  strategies as installable bundles. `brain plugin install company-acme`
  fetches `frameworks/acme/SKILL.md` + custom hooks + custom commands.
- **Scheduled tasks**: `brain schedule daily-rebuild --repo X --endpoint Y --cron "0 2 * * *"`. Cron-style background runs that keep the brain fresh.
  Implemented via APScheduler + Postgres job table.
- **Notebook support**: `.ipynb` in chunker — extract per-cell entities;
  useful for ML repos with notebook prototypes.
- **Image support**: vision-extract architecture diagrams from `docs/*.png`
  and relate to extracted entities (e.g. a "Service Mesh" diagram becomes
  an `Artifact` linked to all the services it depicts).
- **Browser-verifier sub-agent** (adapted A2): when a frontend repo is
  also in the workspace, headless-browser the running app and verify the
  frontend's network calls match the backend's `ApiEndpoint` entities.
  Surfaces drift.
- **Artifact pinning**: UI / CLI flag to mark an entity as `pinned`
  (excluded from auto-overwrites) or `proposed` (needs human approval
  before promotion).
- **Per-entity notes**: `brain note add <urn> "Adam said this is being
  deprecated"` — sticky notes alongside auto-extracted context.

### Phase 7 — IDE integration (~5 days)

Features: 19, A4 (adapted)

The "agent in the editor" phase:

- VS Code extension. Right-click on a method → "Ask brain" runs `/query`
  with the qname pre-filled, response renders inline.
- Sidebar pane shows the brain's current context for the open file:
  related entities, edges, BusinessContext.
- Hover-tooltip enrichment: hovering a Spring `@Autowired` field shows
  the brain's view of that bean's risk + invariants.
- JetBrains version (same backend, different client) tracked separately.

This phase ships LAST because it's the most expensive UI work and
benefits least from the rest of the harness. P1–P6 deliver value
without it.

---

## Adoption matrix summary

```
Total Claude Code features cataloged:    35
Total Antigravity features cataloged:    15
Adopted (✅):                              45
Adapted (⚠ — partial / different shape): 2  (#14, #20)
Already done (no change):                 3  (#26, #33, A14)
Skipped:                                  0  (everything maps to a use case)
```

The 47 we're adopting/adapting break down by phase:

| Phase | Days | Features delivered |
|---|---|---|
| P1 — Harness loop | 5 | 1, 15, 33 |
| P2 — Sub-agents | 5 | 2, 30 |
| P3 — Skills + memory | 5 | 3, 4, 14 |
| P4 — Hooks + permissions + streaming | 7 | 5, 6, 7, 8, 17, 21, 22, 25, 31, 34, A5, A6, A7, A9, A11, A15 |
| P5 — Slash + MCP + workspace + headless | 7 | 9, 10, 11, 13, 16, 18, 24, 27, 32, 35, A1, A3, A8, A12 |
| P6 — Marketplace + scheduled + notebook + image + verifier + notes | 5 | 12, 23, 28, 29, A2, A10, A13 |
| P7 — IDE integration | 5 | 19, A4 |
| **Total** | **39 days (~6 weeks)** | **47 features** |

---

## Options Considered

### Option A — Adopt all 47 features over 4 phases (this ADR)

| Dimension | Assessment |
|---|---|
| Complexity | High |
| Time-to-Claude-Code-parity | 6 weeks |
| Risk | Bounded — each phase is flag-gated, legacy stays available |

The chosen design.

### Option B — Adopt only the top 10 features (the highest-impact ones)

| Dimension | Assessment |
|---|---|
| Complexity | Medium |
| Time | 2 weeks |
| Result | 80% of the value of Option A but locks us into another bolt-on cycle for the rest |

Rejected per user's "we want all features" — partial adoption defeats
the goal of a coherent harness.

### Option C — Vendor an existing harness (LangChain, LangGraph, AutoGen, Pydantic-AI) and adapt to extraction

| Dimension | Assessment |
|---|---|
| Complexity | Initially low, eventually high (fighting the framework) |
| Lock-in | Severe — abandoning is a rewrite |
| Domain fit | Poor — none of them target "long-running pipeline with structured outputs and persistent storage" |

Rejected. The harness pattern is ~500 LOC of glue around the LLM SDK;
the value is in the tool catalog and skill registry, both of which we
have to write either way.

---

## Trade-off Analysis

The core trade-off is **6 weeks of investment for a multiplicatively
extensible system** vs. **another N weeks of bolt-ons that each cost
1-2 days but don't compound**.

The bet is that future features ("add NestJS support", "add a custom
hook for Acme Corp", "schedule nightly rebuilds", "let users query
from VS Code") become 1-day tasks instead of 1-2-week projects once
the harness is in place. Even if we only add 10 such features in the
next year, the break-even is well inside that year.

The risk is over-engineering relative to actual demand. Mitigation:
each phase is independently shippable and behind a feature flag. If
demand for marketplace plugins (P6) never materialises, we ship P5
and stop.

---

## Consequences

**What becomes easier**

- Adding a framework: drop a SKILL.md (P3).
- Customising for an org: install a plugin (P6).
- Long-running ops: native session/resume/transcript (P4).
- IDE-driven workflows: brain queries inline (P7).
- CI/automation: headless mode + JSON output + scheduled jobs (P5/P6).
- Verifying extraction quality: VerifierAgent sub-agents (P2).
- Discovering brain capabilities: `brain tools list` (P4).

**What becomes harder**

- Onboarding a new contributor: the system has many surfaces. Mitigate
  via `docs/HARNESS.md` and `docs/FEATURE-INDEX.md` cross-referencing
  every feature to its phase + ADR.
- Predicting cost: agent-driven flows are less deterministic than
  hardcoded stages. Mitigate via plan mode (`#9`) and per-tool cost
  telemetry (`#25`).

**What we'll need to revisit**

- Plugin / marketplace governance (P6): publishing process, review,
  trust model. Defer until first external customer asks.
- IDE extension distribution (P7): VSIX publishing, JetBrains plugin
  marketplace submission. Defer until P5/P6 are battle-tested.

---

## Action Items

(Each phase has its own implementation prompt; the ADR-0048/49/50
prompts already shipped will be joined by ADR-0051-P{1..4} prompts
already planned, plus three new ones for P5/P6/P7.)

1. [ ] Land ADR-0049 → 0048 → 0050 (currently in three open Claude Code
       sessions). These are prerequisites — they make the building
       blocks (caching, two-agent extraction, big-repo recovery) that
       the harness uses as tools.
2. [ ] Phase 1 implementation prompt: `SONNET-IMPLEMENTATION-PROMPT-ADR-0051-P1.md`.
3. [ ] Phase 2 implementation prompt: `SONNET-IMPLEMENTATION-PROMPT-ADR-0051-P2.md`.
4. [ ] Phase 3 implementation prompt: `SONNET-IMPLEMENTATION-PROMPT-ADR-0051-P3.md`.
5. [ ] Phase 4 implementation prompt: `SONNET-IMPLEMENTATION-PROMPT-ADR-0052-P4.md`.
6. [ ] Phase 5 implementation prompt: `SONNET-IMPLEMENTATION-PROMPT-ADR-0052-P5.md`.
7. [ ] Phase 6 implementation prompt: `SONNET-IMPLEMENTATION-PROMPT-ADR-0052-P6.md`.
8. [ ] Phase 7 implementation prompt: `SONNET-IMPLEMENTATION-PROMPT-ADR-0052-P7.md`.
9. [ ] `docs/HARNESS.md` — canonical "how the brain pipeline runs" doc.
       Auto-rebuilt from system prompt + tool registry on every release.
10. [ ] `docs/FEATURE-INDEX.md` — cross-reference every adopted feature
        to its phase, ADR, code location, and acceptance test.

---

## Open questions for the team

1. **Plugin trust model** (P6): orgs install third-party skills + hooks.
   How do we sandbox? Bubblewrap? Read-only by default?
2. **MCP server scope** (P5): expose only read tools? Or full read+write?
   Probably read-only public, write-only with auth.
3. **IDE extension priority** (P7): which IDE first? VS Code has bigger
   reach; JetBrains has stickier users in the Java/Kotlin world that's
   our primary domain.
4. **Marketplace governance** (P6): self-host? Or use an existing
   registry (e.g. as a GitHub Topic)?
5. **Multi-tenant for the MCP server** (P5): how do we authenticate
   different organisations querying the same brain instance?

---

## Appendix: feature → use-case index (alphabetical)

For navigating back from "I want feature X" to its phase + use case:

- **Bash tool** → P5; `run_repo_command` for `mvn test`/`git log`
- **Compaction** → P4; auto-compact parent transcript at 80% context
- **CLI + SDK** → P5; `companybrain` Python SDK; CLI is its wrapper
- **Cost tracking** → P4; per-tool-call cost in job summary
- **Diffing UI** → P4 (A11); brain-diff preview before storage
- **Edit confirmation** → P4 (#34); `--yes` flag for non-interactive
- **Git integration** → P5 (A12); `git_branch_diff` tool
- **Glob / Grep** → P1 (#15); native code search tools
- **Headless mode** → P5 (#27); `brain extract --headless --json`
- **Hooks** → P4 (#5); `.brain/hooks/*.sh` at defined events
- **IDE integration** → P7 (#19); VS Code + JetBrains plugins
- **Image support** → P6 (#23); vision-extract `docs/*.png` diagrams
- **MCP server** → P5 (#11); brain-as-MCP for external consumers
- **Memory file** → P3 (#4); `.brain/BRAIN.md` per repo
- **Multi-pane rooms** → P5 (A1); typed surfaces `code:`, `db:`, `git:`, etc.
- **Notebook support** → P6 (#29); `.ipynb` in the chunker
- **Notes (per-entity)** → P6 (A13); sticky notes alongside extraction
- **Output styles** → P5 (#20); `verbosity` modes on `/query`
- **Permission model** → P4 (#6); per-tool capability × workspace grant
- **Pinning artifacts** → P6 (A10); `pinned` / `proposed` entity status
- **Plan mode** → P4 (#9); `--plan` flag previews extraction
- **Plugins / marketplace** → P6 (#12); `brain plugin install`
- **Resume / sessions** → P4 (#22, A5); `brain session list/resume`
- **Scheduled tasks** → P6 (#28); `brain schedule` cron-like jobs
- **Skills** → P3 (#3); `frameworks/{spring-boot,fastapi,...}/SKILL.md`
- **Slash commands** → P5 (#10); `.brain/commands/*.md`
- **Status line** → P4 (#21); CLI live status
- **Streaming** → P4 (#8); SSE on `/pipeline/jobs/{id}/stream`
- **Sub-agents** → P2 (#2, #30); `Task`-style spawn_extractor / verifier / research
- **Terminal room** → P5 (A3); same as bash tool
- **Tool-use loop** → P1 (#1); core HarnessLoop
- **Tool registry exposed** → P4 (#15, A15); `brain tools list`
- **Verifier sub-agent (browser)** → P6 (A2); frontend↔brain parity check
- **WebFetch / WebSearch** → P5 (#16); doc-fetching tool for sub-agents
- **Workspace dataclass** → P5 (A8); consolidates scattered config
- **Worktrees** → P5 (#24); per-job `git worktree add`
