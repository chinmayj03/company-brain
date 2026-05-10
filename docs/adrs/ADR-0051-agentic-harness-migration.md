# ADR-0051 — Agentic Harness Migration (Claude-Code-style architecture for the extraction pipeline)

**Status:** Proposed
**Date:** 2026-05-10
**Deciders:** Chinmay (product), pipeline-team
**Builds on:** ADR-0048 (two-agent extraction), ADR-0049 (caching), ADR-0050 (big-repo recovery)
**Supersedes (partial):** the linear stage-machine in `pipeline/orchestrator.py`

---

## Context

The current pipeline is a **fixed linear stage machine**: `Code Tracer →
Navigator → Chunker → Extractor → Merger → Synthesizer → Storage`. Each
stage is hand-wired in `orchestrator.py` (~2000 LOC of conditionals,
checkpoint logic, fallbacks). Every new feature — chunked extraction
(ADR-0044), reachability filter (ADR-0043), structural pre-pass (ADR-0011),
cache layers (ADR-0049), big-repo recovery (ADR-0050) — gets bolted into
that orchestrator with another `if/else` branch.

The pattern is hitting its ceiling:

1. **Adding a new extraction strategy** (Python FastAPI, Go, Rust, ML
   notebooks) means another conditional in `_trace_backend` and another
   set of regex tracers in `_trace_python` / `_trace_typescript`. Each
   addition compounds the spaghetti.

2. **No parallelism within a job.** Files extract serially through the
   queue; even with `max_workers=4`, the orchestrator itself is
   single-threaded. A repo with 100 entry points extracts 100× sequentially.

3. **No introspection / replanning mid-run.** When the chunker discovers
   the entry handler delegates to a class not in the manifest, the
   pipeline can't dynamically expand the manifest — it just drops the
   call chain.

4. **No skills system.** Spring Boot expertise lives in
   `_trace_java`. NestJS expertise lives in `_trace_typescript`.
   FastAPI expertise lives in `_trace_python`. Each is a copy-pasted
   regex tracer with no shared abstraction. New framework support is a
   2-week project.

5. **No persistent agent memory.** Re-extracting the same repo on the
   next run, the agent has no notion of "I learned last time that
   `JsonKeyMapping` is irrelevant — skip it." It re-discovers the same
   things every time.

User asked: *"can we move to a very complex and amazing harness like
claude code or antigravity? explain their architecture and differences
with us — we want to copy what works as much as possible."*

This ADR (a) explains those architectures, (b) maps each design pattern
to a problem in our current pipeline, (c) proposes a migration plan
that does it incrementally instead of as a big bang.

---

## How Claude Code is structured (the parts worth copying)

Claude Code is a coding agent CLI. Its architecture in one sentence:
**a model-driven loop where the assistant turn produces tool-use blocks,
the harness executes them, and the results are appended to the
conversation as the next user turn**. Repeat until the model emits a
text-only response (the "answer"). Specifics that matter for us:

### 1. Strongly-typed tool registry, not hard-coded stages

The agent has a flat catalogue of tools (`Read`, `Edit`, `Grep`, `Bash`,
`Glob`, `Task`, `TodoWrite`, etc.) each with a JSON-schema-validated
input. The loop is:

```
SYSTEM PROMPT (policies, behaviours, file-handling rules)
  +
USER MESSAGE (the task)
  ↓
ASSISTANT TURN: text + tool_use blocks (zero or more)
  ↓
HARNESS: validates inputs, executes tools, appends tool_result blocks
  ↓
ASSISTANT TURN: text + tool_use blocks (zero or more)
  ↓
... until assistant emits text-only ...
```

There is no hard-coded "stage 1, stage 2". The model decides what to do
next based on current conversation state. Adding a new capability is
"register one more tool" — no orchestrator surgery.

### 2. Sub-agents via the Task tool — fresh context windows for parallel work

When the main agent encounters a chunk of work that doesn't need the
parent's full context (research, fan-out file edits, code review of an
isolated module), it spawns a **sub-agent** via the `Task` tool. The
sub-agent gets:

- Its own context window (no inherited history bloat)
- Its own tool access (configurable; e.g. read-only research agents)
- A focused prompt the parent crafted ("here's the question, here's the
  context you need, return a 200-word summary")

The parent only sees the sub-agent's final result, NOT its tool-call
trajectory. This is how Claude Code research questions don't blow up
the parent's context.

For us this maps directly to: **per-file or per-class extraction
sub-agents**. Each file gets a fresh sub-agent with the file's content,
extracts entities + edges, returns structured results. The parent
orchestrator never sees the full file contents — only the results.
Parallelism becomes trivial (Task tool calls fire in parallel inside a
single assistant turn).

### 3. Skills — domain expertise loaded on demand

Skills are markdown files (`SKILL.md`) with a `description` field that
the agent matches against the user's intent. When a user asks "create a
PowerPoint about X", the agent invokes the `pptx` skill, which expands
into ~3000 tokens of detailed instructions on how to build pptx files.
The skill stays out of context until needed.

For us: **per-framework skills** — `spring-boot/SKILL.md`,
`fastapi/SKILL.md`, `nestjs/SKILL.md`, each describing the framework's
extraction patterns (annotation conventions, common idioms, where to
look for SQL, common DTO patterns to skip). The orchestrator picks the
skill based on the repo's primary language + framework signals.

### 4. Hooks — event-driven extension points

Claude Code fires hooks at well-defined points: `UserPromptSubmit`,
`PreToolUse`, `PostToolUse`, `Stop`, etc. Hooks can be shell scripts
that the harness invokes; their output can modify the conversation
(injecting context, blocking tools, transforming inputs).

For us: pipeline hooks like `PreExtraction`, `PostExtraction`,
`PreStorage`, `OnTruncation`. Today these are hard-coded callbacks in
the orchestrator; making them hookable lets us add custom telemetry,
gating, and per-org policies without touching core code.

### 5. Permission model — three-tier auto/ask/deny per tool

Claude Code groups tools into permission categories:
- **Auto-allow**: read-only (Read, Glob, Grep, WebSearch)
- **Ask user**: writes to files, network calls to unfamiliar hosts
- **Deny**: explicitly prohibited (e.g. credential exfiltration paths)

In our pipeline, the equivalent: read-only extraction is auto;
DB inserts in production go through a confirmation step in
interactive mode; secret-looking strings auto-deny.

### 6. Memory files — `CLAUDE.md` per repo, persistent across sessions

A repo can ship a `CLAUDE.md` (or `.claude/CLAUDE.md`) that's
auto-loaded into context when the agent enters the repo. It carries
project-specific conventions, gotchas, and anti-patterns that survive
across sessions.

For us: `.brain/BRAIN.md` (next to the existing `.brain/index.json`)
that the extractor reads on every run. Carries: "in this repo,
`JsonKeyMapping` is a constants table — skip it" / "all SQL is jOOQ
DSL, look for `.from(` chains" / "the LOB column was renamed in
2024-Q3 from `lobName` to `lob`". Curated by humans + auto-updated by
the pipeline when it discovers patterns.

### 7. TodoWrite — internal task tracker visible to the user

The agent maintains a structured todo list that updates in real-time
in the user's terminal. This isn't just UX — it forces the model to
plan explicitly and lets the user see/redirect progress.

For us: per-job todo list surfaced in `/pipeline/jobs/{id}` showing
"extracting CompetitivenessController.java (3/15 files)" — the
SpecialistAgent (ADR-0048) emits this, the harness updates it as
sub-agents complete.

### 8. Streaming + tool-use parallelism

The model can emit multiple `tool_use` blocks in a single assistant
turn; the harness fires them in parallel. Streaming means the UI shows
text + tool calls as they're generated, not after the whole turn
completes.

For us: when the SpecialistAgent emits its plan, the orchestrator can
fire all per-file sub-agents in parallel from one turn, not 8 batches
waiting in a worker queue.

---

## How Antigravity is structured (the parts worth copying)

Antigravity is Google's IDE-integrated coding agent. Less is publicly
documented than Claude Code, but the patterns that have been disclosed
or are visible in product behaviour:

### A. Multi-pane "rooms" — the agent navigates between live views

Antigravity exposes named workspaces (browser, terminal, code, docs)
the agent can switch between. Each room has a typed surface (DOM tree,
file tree, terminal buffer) and the agent issues actions targeted at
that surface.

For us: rooms map to subsystems we already have — `code:` (source
files), `db:` (Postgres / Neo4j / Qdrant), `git:` (commit history),
`api:` (running service inspection). The harness exposes each as a
typed surface; the agent picks which to query.

### B. Persistent project context across IDE sessions

Antigravity remembers what files you were last working on, what tasks
were pending, what the agent had already completed. Closing and
reopening continues from the same state.

For us: the existing `.brain/manifest.json` is a primitive version of
this. Promote it to a richer "session resume" — last endpoint, last
diff hash, pending sub-agent tasks. The orchestrator can resume mid-
extraction if killed.

### C. Live "explanation" of agent reasoning

Antigravity surfaces what the agent is about to do BEFORE doing it,
giving the user a chance to redirect. This is a UX pattern that
encourages trust on long-running operations.

For us: surface the SpecialistAgent's plan in the UI as soon as it's
generated, not after extraction completes. User can kill the run if
the plan is wrong (saves $0.04 of wasted ContextAgent calls).

### D. Capability flags per repo / per workspace

Antigravity gates capabilities (run shell commands, install packages,
modify config files) per workspace. A "trusted" workspace runs more
freely; an "exploratory" one is read-only by default.

For us: per-workspace capability flags in `BRAIN_WORKSPACE_*` env vars
already exist as primitives. Promote them to a typed model: each
extraction tool declares its required capabilities; the workspace
declares granted capabilities; the harness enforces the intersection.

---

## Side-by-side: current vs. target architecture

| Concern | Current pipeline | Claude-Code-style harness | Migration cost |
|---|---|---|---|
| Control flow | Hardcoded `orchestrator.py` linear stages | Tool-using agent loop driven by SYSTEM prompt | High (1-2 weeks) |
| Parallelism | `chunk_queue_max_workers=4` worker pool | Multiple `tool_use` blocks per assistant turn, executed in parallel | Medium (3-5 days) |
| Adding a framework | New `_trace_X` regex method + new orchestrator branch | New `frameworks/X/SKILL.md` file | Low (1 day per framework after migration) |
| Reusing prior runs | `.brain/.l2-cache/main.json` per-file hash | `BRAIN.md` memory + L2 cache (both) | Low (additive) |
| Sub-task isolation | None (everything shares orchestrator state) | `Task` tool spawns sub-agent with fresh context | Medium (3-5 days) |
| Replanning mid-run | None — static plan from SpecialistAgent | Agent re-evaluates after each tool result | Low if harness is in place |
| Hooks / extensibility | Edit `orchestrator.py` | `~/.brain/hooks/<event>.sh` shell scripts | Low (1-2 days) |
| Permission model | Boolean env flags | Per-tool capability declarations × workspace grants | Low (1 day) |
| Per-job progress | Polling `/pipeline/jobs/{id}` | Streamed TodoWrite updates | Medium (needs SSE backend) |
| Memory across runs | Brain JSON files | Brain + curated `BRAIN.md` | Low (additive) |
| Live UI surfaces | None — JSON polling | Multi-pane rooms (code/db/git/api) | High (UI work) |

---

## Decision

Migrate the orchestrator to a Claude-Code-style harness in **four
sequenced phases**, each independently shippable. We do NOT rewrite
the pipeline in one shot — that would block all current work for
weeks. Instead, each phase replaces one orchestrator concern with
its harness equivalent and the legacy path stays available behind a
flag until the new path is proven.

### Phase 1 (P1) — Tool-use harness around the existing stages

Build a thin `HarnessLoop` that:
- Owns the conversation history.
- Has a `ToolRegistry` with the 8 tools below (each wrapping an
  existing pipeline call).
- Drives the loop until the model emits a text-only response.

Initial tool catalogue (each tool wraps existing code):

```
- discover_routes(repo_path) → list[Route]              # existing discover_routes()
- find_entry_handler(endpoint, method, repo_path) → Handler
- list_candidate_files(endpoint, repo_path) → list[CandidateFile]
- read_file(path) → str                                 # FileCache-backed
- extract_methods_from_class(file, methods) → BatchResult  # ContextAgent
- spawn_extractor(file, methods) → SubAgentHandle        # Task-style sub-agent
- write_to_brain(entities, edges) → WriteResult
- finalize_brain(workspace_id) → ManifestResult
```

The agent's system prompt encodes today's pipeline as guidance:
"first discover the entry handler, then list candidates, then plan
which files to extract, fan out via spawn_extractor, then
write_to_brain, then finalize."

**Net effect**: same stages, same costs, but the orchestrator is now
a *prompt-controlled agent*. New tools can be added without code
edits to the orchestration logic.

### Phase 2 (P2) — Sub-agents and parallel fan-out

Replace `worker.drain_queue` with `spawn_extractor` sub-agents. Each
file → one Task sub-agent with its own context window. The parent
agent fires N `spawn_extractor` calls in a single assistant turn;
the harness runs them in parallel (bounded by `max_subagents=8`).

Sub-agent context size drops from the parent's full conversation
(currently ~50 KB after navigator + plan + per-file results) to just
the file's content + extraction instructions (~10 KB). This alone
cuts per-file LLM input cost by 5×.

### Phase 3 (P3) — Skills + memory

- `frameworks/spring-boot/SKILL.md`, `frameworks/fastapi/SKILL.md`,
  `frameworks/nestjs/SKILL.md`, `frameworks/django/SKILL.md`. Each
  has a `description` (e.g. "Java + Spring Boot codebases — recognise
  @Controller, @Service, @Repository, JPA, jOOQ DSL chains"). The
  harness loads at most one framework skill per run, picked by a
  cheap detector (file-extension count + framework-marker scan).

- Per-repo `.brain/BRAIN.md` loaded into the system prompt on every
  run. Curated section + auto-appended section (the pipeline appends
  observations like "skip JsonKeyMapping" when its dropped-by-type
  count exceeds threshold).

### Phase 4 (P4) — Hooks, permission model, streaming progress

- `.brain/hooks/pre_extraction.sh`, `post_extraction.sh`,
  `on_truncation.sh`. Shell scripts the harness invokes with JSON
  on stdin; output JSON modifies behaviour.

- Per-tool capability declaration + per-workspace grant table.
  Replaces the scattered `BRAIN_*` env flags.

- TodoWrite-style streaming progress over SSE. UI subscribes,
  shows per-file status in real-time. Replaces the current 2-second
  polling loop.

The four phases are sequential because P2 needs P1's harness to spawn
sub-agents into; P3 needs P2's sub-agent shape so skills can target
sub-agents; P4 needs P3's skill identity so per-skill hooks make sense.

---

## Options Considered

### Option A — Big-bang rewrite to harness in one PR

Rewrite `orchestrator.py` from scratch as a HarnessLoop, port all
existing tools at once, ship under a feature flag.

| Dimension | Assessment |
|---|---|
| Complexity | Very High |
| Risk | Very High — orchestrator is 2000 LOC and load-bearing |
| Time-to-first-value | 3-4 weeks of nothing-shipping |

Rejected. Too much regression risk for an already-working pipeline.

### Option B — Phased migration (this ADR)

Land four sequential PRs, each shipping incremental value with the
legacy path available as fallback.

| Dimension | Assessment |
|---|---|
| Complexity | Medium per phase; high cumulative |
| Risk | Low per phase (each is additive + flag-gated) |
| Time-to-first-value | P1 ships in ~5 days, useful immediately |

The chosen design.

### Option C — Stay linear, just keep adding ADRs

Continue the current pattern: each new requirement = one more ADR
that adds another conditional to `orchestrator.py`.

| Dimension | Assessment |
|---|---|
| Complexity | Low per change |
| Long-term maintainability | Worsens monotonically |
| Adding new framework | 2 weeks per framework |

Rejected. We're already at the ceiling of this approach (the user's
"can we move to a complex amazing harness" is the symptom).

### Option D — Adopt an existing agentic framework (LangGraph, CrewAI, AutoGen)

Pull in a third-party agent framework instead of building our own.

| Dimension | Assessment |
|---|---|
| Complexity | Medium (framework's abstractions to learn) |
| Lock-in | High — these frameworks evolve fast and break APIs |
| Fit | Poor — none of them model "extraction pipeline with
typed tool catalog" cleanly |

Rejected. The harness pattern itself is small (~500 LOC); copying it
specifically for our domain is cheaper than vendoring a generic
framework.

---

## Trade-off Analysis

The fundamental trade-off is **expressiveness vs. predictability**.
A linear stage machine is predictable: every run executes the same
stages in the same order. An agent-driven harness is expressive: it
can adapt mid-run, replan when it sees unexpected results, fan out
arbitrary work — but its execution path is determined by a model
output, which is non-deterministic.

We mitigate the determinism risk three ways:

1. **System prompt encodes the canonical flow** — the agent is
   strongly nudged to follow today's pipeline order. Deviations
   require explicit textual reasoning the agent has to articulate.

2. **All tools are deterministic** — the model decides which to call,
   but each tool's output is purely a function of its inputs.
   Reproducibility = same input → same plan → same tool calls (modulo
   model temperature, which we set to 0 for the harness loop).

3. **Telemetry is per-tool-call, not per-stage** — replaces the
   current per-stage progress with a tool-call trace. Easier to
   debug "agent went down a wrong path" than "stage 2 failed".

The expressiveness win is what justifies the trade. Today, adding
"FastAPI extraction" means a 1500-line `_trace_python` rewrite. Post-
P3, it means dropping a `frameworks/fastapi/SKILL.md` file.

---

## Consequences

**What becomes easier**

- Adding a new framework: one SKILL.md file.
- Per-organisation customisation: `~/.brain/hooks/*.sh` overrides
  without forking.
- Debugging: tool-call trace per job shows exactly what the agent did.
- Parallelism: P2's sub-agents fan out trivially.
- Cost telemetry: each tool call is independently priced.

**What becomes harder**

- Reading `orchestrator.py` to understand the pipeline — by P3, the
  pipeline IS the system prompt + tool registry, not a code file.
  Mitigation: ship a `docs/HARNESS.md` that's the canonical
  "how the pipeline runs" doc, kept in sync with the prompt.
- Test surface: agent loops are harder to unit-test than linear
  pipelines. Mitigation: each TOOL is unit-tested in isolation; the
  harness has scenario tests that mock the LLM and assert tool-call
  sequences.
- Rolling back a bad agent decision mid-run: harder than rolling back
  a stage. Mitigation: the harness runs in a transactional mode for
  write tools (write_to_brain wraps a Postgres transaction; rollback
  on agent error).

**What we'll need to revisit**

- Model temperature: 0 for the harness loop is mandatory; revisit if
  we ever want exploratory runs.
- Sub-agent isolation: does the parent need to see sub-agent costs?
  Probably yes, surface in TodoWrite.
- Skill discovery: today the harness loads the framework skill by
  detector; what happens for polyglot repos? Revisit when first
  multi-language customer hits this.

---

## Action Items

### P1 — Tool-use harness (~5 days)

1. [ ] `src/companybrain/harness/loop.py` — `HarnessLoop` class with
       conversation state + tool dispatch.
2. [ ] `src/companybrain/harness/tools/` — one file per tool wrapping
       an existing pipeline function. JSON-schema-validated inputs.
3. [ ] `src/companybrain/harness/system_prompt.py` — generated from
       a template that includes the tool catalog descriptions.
4. [ ] `BRAIN_USE_HARNESS=true` env flag — when on, the orchestrator's
       `run_pipeline` delegates to `HarnessLoop`. Default false until
       P1 acceptance test passes.
5. [ ] Acceptance test `tests/acceptance/test_harness_p1.py` — runs
       the lob endpoint through the harness, asserts same brain
       output as the legacy path within ±5% on entity count.

### P2 — Sub-agents and parallel fan-out (~5 days)

6. [ ] `harness/tools/spawn_extractor.py` — spawns a sub-agent
       (separate context window) per file or per class.
7. [ ] `harness/subagent.py` — minimal sub-agent runner with
       configurable tool subset (e.g. read-only research vs.
       full-extraction).
8. [ ] `worker.drain_queue` deprecated; legacy ContextAgent still
       callable as a tool from sub-agents.
9. [ ] Acceptance test: 60-method endpoint completes in
       `< 30 seconds wall time` (vs current 5+ minutes).

### P3 — Skills + memory (~5 days)

10. [ ] `frameworks/spring-boot/SKILL.md`,
        `frameworks/fastapi/SKILL.md`, `frameworks/nestjs/SKILL.md`,
        `frameworks/django/SKILL.md`. Each describes its framework's
        extraction patterns in ~2000-token Markdown.
11. [ ] `harness/skills.py` — detects primary framework and loads
        at most one SKILL.md into the system prompt.
12. [ ] `.brain/BRAIN.md` per-repo memory file. Loaded on every run.
        Auto-append section managed by `BrainMemoryWriter` when
        recurring patterns are detected.
13. [ ] Acceptance test: same lob endpoint extracted on a Spring
        Boot repo AND a FastAPI repo with no orchestrator code
        change — only SKILL.md selection differs.

### P4 — Hooks, permission model, streaming (~5 days)

14. [ ] `harness/hooks.py` — invokes `.brain/hooks/*.sh` at
        defined events (`pre_extraction`, `post_extraction`,
        `on_truncation`, `pre_storage`).
15. [ ] `harness/permissions.py` — per-tool capability declarations
        + per-workspace grants table. Replaces scattered
        `BRAIN_*` flag checks.
16. [ ] `harness/progress.py` — TodoList model + SSE endpoint.
        UI subscribes to `/pipeline/jobs/{id}/stream`.
17. [ ] Documentation: `docs/HARNESS.md` — the canonical "how the
        pipeline runs" doc. Auto-rebuilt from the system prompt
        + tool registry on every release.
18. [ ] Acceptance test: end-to-end streaming run with one custom
        hook (e.g. `pre_extraction.sh` that drops files matching
        a pattern). UI shows per-file completion in real-time.

### Sequencing & merge plan

P1 → P2 → P3 → P4, each as its own PR. Each phase's PR includes its
own acceptance test. Legacy path (`BRAIN_USE_HARNESS=false`) stays
green throughout the migration; only after P4 lands AND the
acceptance suite is green for two weeks do we delete the legacy
orchestrator path.

This ADR is intentionally sequenced AFTER ADR-0048/49/50 so those
land first and the harness inherits all their benefits (caching,
two-agent extraction, big-repo recovery) as building-block tools
rather than re-implementing them.

---

## Open questions for the team

1. **Model choice for the harness loop**: Sonnet for the orchestrator
   agent (better instruction-following), Haiku for sub-agents (cheap
   per-file extraction)? Or all-Haiku to control cost?
2. **Sub-agent context isolation**: do sub-agents share the parent's
   FileCache + AstCache? Probably yes for performance; verify no
   contamination of plan-level decisions.
3. **Hook security**: shell-script hooks can do anything. Sandbox via
   bubblewrap? Or document as "only install hooks you trust"?
4. **`BRAIN.md` curation**: human-edited only? Or auto-PR-bot?
   Probably both: bot proposes, human reviews.
