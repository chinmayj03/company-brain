# HARNESS — the agentic extraction loop

**Status:** Phase 3 (sub-agent fan-out + per-framework skills + per-repo memory).
**Source:** `company-brain-ai/src/companybrain/harness/`.
**Driving ADR:** [`ADR-0051`](adrs/ADR-0051-agentic-harness-migration.md).

---

## What it is

A prompt-controlled tool-dispatch loop that replaces the linear stage machine
in `pipeline/orchestrator.py`. The model picks which tool to call next based
on the conversation state. Adding a new capability is "register one more tool"
— no orchestrator surgery.

```
┌─────────────────────────────────────────────────────────────────┐
│  build_system_prompt(context)                                   │
│   • Canonical pipeline description                              │
│   • Live tool catalog (one bullet per registered tool)          │
│   • Workspace / repo / endpoint context                         │
└─────────────────────────────────────────────────────────────────┘
                               ↓
┌─────────────────────────────────────────────────────────────────┐
│  HarnessLoop.run(user_message, context)                         │
│                                                                 │
│   ┌──────────────────────────────────────────────────────────┐  │
│   │ for i in range(max_iterations):                          │  │
│   │   resp = provider.chat_with_tools(messages, tool_defs)   │  │
│   │   if not resp.tool_calls:                                │  │
│   │       return final_text                                  │  │
│   │   results = await gather(dispatch(tc) for tc in ...)     │  │
│   │   messages.extend(assistant_turn + tool_result_turns)    │  │
│   └──────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

Phase 1 deliberately does not introduce sub-agents (P2), skills (P3), or
hooks (P4). Tools wrap existing pipeline functions verbatim, so output is
identical to the legacy path on the same input.

---

## Enabling the harness

The legacy linear pipeline is the default. Flip the harness on per-run with:

```bash
BRAIN_USE_HARNESS=true
```

Or in code:

```python
from companybrain.config import settings
settings.use_harness = True
```

`pipeline/orchestrator.run_pipeline` checks the flag at the top of every run
and delegates to `_run_via_harness`. Everything else in `orchestrator.py` is
the legacy path, untouched.

---

## Tool catalog (Phase 1)

| Tool                          | Wraps                                              | Use it when                                     |
|-------------------------------|----------------------------------------------------|-------------------------------------------------|
| `discover_routes`             | `collectors.code_tracer.discover_routes`           | Confirming the target endpoint exists.          |
| `find_entry_handler`          | `agents.tools.code_tools.find_entry_handler`       | Locating the controller method for a route.     |
| `list_candidate_files`        | `collectors.manifest_filter.build_filtered_manifest` (ADR-0050) | Building a bounded extraction manifest. |
| `read_file`                   | `util.file_cache.FileCache` (ADR-0049) → stdlib    | Orienting in an unfamiliar file.                |
| `glob_files`                  | `Path.glob`                                        | Listing files by pattern (e.g. `**/*Service.java`). |
| `grep_code`                   | `Path.rglob` + `re`                                | Finding where a symbol is referenced.           |
| `extract_methods_from_class`  | `agents.context_agent.ContextAgent` (ADR-0048)     | Batched extraction of methods in one file.      |
| `write_to_brain`              | `store.json_store.JsonFileBrainStore`              | Persisting entities + edges.                    |
| `finalize_brain`              | `store.json_store.JsonFileBrainStore.commit_run`   | Closing the run; call exactly once at the end.  |

Tools are pure async functions registered with the `@register_tool` decorator.
They receive `(args: dict, context: dict)` and return any JSON-serialisable
value; the loop converts non-string returns via `json.dumps`.

---

## Adding a new tool

1. Create `harness/tools/<my_tool>.py`.
2. Register it:

   ```python
   from companybrain.llm.base import ToolParameter
   from companybrain.harness.tools import register_tool

   @register_tool(
       name="my_tool",
       description="One sentence the model will read to decide if this is the right tool.",
       parameters=[
           ToolParameter("foo", "string", "What foo is for."),
           ToolParameter("limit", "integer", "Max results.", required=False),
       ],
   )
   async def handler(args: dict, context: dict):
       ...
   ```

3. Import it in `harness/tools/__init__.py` so the decorator runs at package
   load (otherwise the registry never sees it).
4. Tests that exercise the harness will pick the tool up automatically — the
   system prompt is generated from `TOOL_REGISTRY` on every run.

There is no separate orchestrator branch to update.

---

## Context dict — what to pass

The `context` dict is opaque to the loop and forwarded verbatim to every tool
handler. Phase 1 conventions:

| Key            | Set by                | Used by                                                  |
|----------------|-----------------------|----------------------------------------------------------|
| `workspace_id` | orchestrator delegate | `write_to_brain`, `finalize_brain`                        |
| `repo_path`    | orchestrator delegate | brain store location, system prompt                       |
| `endpoint_path`| orchestrator delegate | system prompt                                             |
| `http_method`  | orchestrator delegate | system prompt                                             |
| `job_id` / `run_id` | orchestrator delegate | brain store `run_id`                                  |
| `file_cache`   | orchestrator delegate | `read_file`, `extract_methods_from_class` (ADR-0049 cache) |
| `brain_store`  | populated lazily      | `write_to_brain`, `finalize_brain` (reuses one store)     |

Tools should treat unknown keys as opaque and validate any key they read.

---

## Failure handling

* Unknown tool name → returns `{"error": "..."}` to the model so it can pick
  another tool. The loop continues.
* Tool raises an exception → caught, logged via `structlog` as
  `harness.tool_error`, surfaced to the model as `{"error": "..."}`.
* Tool times out (`tool_timeout_seconds`, default 60s) → logged as
  `harness.tool_timeout`, surfaced as an error to the model.
* `max_iterations` reached → loop returns the last assistant text it has so
  callers always get a result. Logged as `harness.max_iterations_reached`.

The loop never raises out to its caller; failures are always observable in
`HarnessResult.tool_calls` and `HarnessResult.telemetry`.

---

## Observability

Every iteration emits a `harness.turn` debug log with iteration number,
tools requested, and content length. The orchestrator delegate emits a
`harness.done` info log on completion with iteration count, tool-call count,
and wall time.

`HarnessResult.telemetry` carries:

```
{
  "iterations":          int,
  "tool_calls_total":    int,
  "tool_calls_ok":       int,
  "wall_time_seconds":   float,
  "provider":            str,
  "model":               str,
}
```

This bubbles up into `PipelineResult.telemetry["harness"]` so the existing
`/pipeline/jobs/{id}` route surfaces it without any change.

---

## Sub-agents (Phase 2)

P2 adds the Task-tool primitive from Claude Code: **isolated sub-agents
with fresh context windows**. The parent agent invokes a `spawn_*` tool
with a list of work items; the tool spawns one sub-agent per item, runs
them in parallel (bounded by `settings.max_subagents`, default 8), and
returns a flat result list. The parent never sees the sub-agents'
tool-call trajectories — only the final summaries.

```
Parent agent (HarnessLoop)
  ├─ spawn_extractor({files: [F1, F2, F3, ...]})
  │     ├─ Subagent("extractor:F1") ─ fresh ctx, tools=extractor subset
  │     ├─ Subagent("extractor:F2") ─ fresh ctx, tools=extractor subset
  │     └─ Subagent("extractor:F3") ─ fresh ctx, tools=extractor subset
  │     → {results: [{file, summary, cost_usd, ...}, ...]}
  │
  └─ Continues with the merged result; per-sub-agent reads/greps stay
     out of the parent's context.
```

### Why isolation matters

Sub-agent input tokens stay flat at "system prompt + one focused user
prompt" (typically 1-3 KB) regardless of how much the parent has already
extracted. Without isolation, every fan-out would carry the parent's full
accumulated state — the per-file context grows linearly with the run.
For a 60-method endpoint, this is the difference between ~50 KB × 60 =
3 MB of duplicated input vs. ~3 KB × 60 = 180 KB. The acceptance test
asserts sub-agent input < 50% of parent input; in practice it's < 10%.

### The three spawn_* tools

| Tool             | Tools the sub-agent may call                                              | Output shape                              | Use it for                                                      |
|------------------|---------------------------------------------------------------------------|-------------------------------------------|------------------------------------------------------------------|
| `spawn_extractor`| `read_file`, `glob_files`, `grep_code`, `extract_methods_from_class`      | `{file, summary, iterations, cost_usd}`   | Per-file extraction fan-out (replaces `worker.drain_queue`).    |
| `spawn_verifier` | `read_file`, `glob_files`, `grep_code` (read-only)                        | `{claim, verdict, evidence}` per item     | Cross-checking extracted edges against primary sources.          |
| `spawn_research` | `read_file`, `glob_files`, `grep_code` (read-only; +`WebFetch` in P5)     | `{question, answer}` per item             | Focused questions whose reads should not pollute parent context. |

Each spawn_* tool returns aggregated cost and timeout flags so the
parent can decide whether to retry, downgrade, or proceed.

### Config knobs

```python
# companybrain.config.settings
max_subagents:      int = 8     # fan-out width per spawn_* call
subagent_timeout_s: int = 120   # per-sub-agent wall-clock cap
```

Both are pydantic-settings fields; override via the corresponding
uppercase environment variable (`MAX_SUBAGENTS`, `SUBAGENT_TIMEOUT_S`).

### Tool-allowlist enforcement

Each `Subagent` is constructed with `allowed_tools=[...]`. The runner:

1. Sends only the allowlisted tool schemas to the model — others are
   invisible to it.
2. Refuses any tool call whose name isn't in the allowlist, returning a
   `{"error": "Tool not in allowlist"}` payload so the model can re-plan.

This is what makes "research sub-agent (read-only)" vs. "extractor
sub-agent (read + extract)" a hard guarantee, not a guideline.

### Failure handling

* Sub-agent provider error → captured on `SubagentResult.error`, the
  spawn_* tool keeps going for the rest of the batch.
* Sub-agent exceeds `subagent_timeout_s` → result has `timed_out=True`
  and an empty `final_text`; the parent sees which items timed out.
* Sub-agent hits its iteration cap → last assistant text is salvaged as
  `final_text`, mirroring HarnessLoop's max-iterations behaviour.

The parent's HarnessLoop never sees a sub-agent crash propagate — fan-out
failures are always observable in the spawn_* tool's return value.

---

## Skills (Phase 3)

P3 attaches a per-framework `SKILL.md` to the system prompt when the repo
matches one of the supported frameworks. The skill is a focused ~2 KB
markdown file teaching the agent the conventions, annotations, and false
positives of one framework — enough that the same `extract_methods_from_class`
loop produces sensible output on Spring Boot, FastAPI, NestJS, Django,
Rails, and Next.js without any per-repo orchestrator change.

```
companybrain.harness.system_prompt.build_system_prompt(context)
   ├─ base prompt (canonical pipeline + tool catalog)
   ├─ skills.detect_framework(repo_path)
   │     • cheap deterministic scan of file markers
   │     • Counter of hits per framework, max wins
   │     • caps to 50 files per pattern, skips node_modules / target / .venv
   ├─ skills.load_skill(framework) → SKILL.md text
   │     • appended under "# Framework Skill: <name>"
   │     • context["skill_loaded"] = framework  (echoed in telemetry)
   └─ memory.load(repo_path) → BRAIN.md text
         • appended under "# Repo memory (BRAIN.md)"
         • context["brain_md_loaded"] = bool
```

### Supported frameworks

| Framework     | Marker patterns                                                     | Where the SKILL.md lives                  |
|---------------|---------------------------------------------------------------------|-------------------------------------------|
| `spring-boot` | `@SpringBootApplication` in `*.java`, `spring-boot-starter` in pom / gradle | `frameworks/spring-boot/SKILL.md` |
| `fastapi`     | `from fastapi import` in `*.py`, `fastapi` in pyproject / requirements | `frameworks/fastapi/SKILL.md`          |
| `nestjs`      | `@nestjs/core` / `@nestjs/common` in `*.ts`, `@nestjs/` in `package.json` | `frameworks/nestjs/SKILL.md`         |
| `django`      | `from django` / `import django`, `manage.py`                        | `frameworks/django/SKILL.md`              |
| `rails`       | `rails` in `Gemfile`, `Rails.application.routes` in `routes.rb`     | `frameworks/rails/SKILL.md`               |
| `nextjs`      | `"next":` in `package.json`, `next.config.{js,mjs}`                 | `frameworks/nextjs/SKILL.md`              |

The detector returns `None` for repos that match nothing; the harness then
runs with the base prompt only.

### Adding a new framework

1. Add an entry to `_FRAMEWORK_MARKERS` in `harness/skills.py`. Each entry
   is `(glob_pattern, predicate)` pairs the scan will tally.
2. Create `frameworks/<name>/SKILL.md` with the conventions, common false
   positives, and DTOs to skip for that framework.
3. The detector picks the new framework up on the next run — no other code
   change is required.

The skill text is read fresh on every prompt build, so editing a SKILL.md
does not require a process restart.

### Per-repo memory — BRAIN.md (Phase 3)

`<repo>/.brain/BRAIN.md` carries repo-specific gotchas the agent could not
infer from the code alone (column renames, no-op DTOs, legacy passthroughs,
recurring drops). It has two sections:

```
## Curated notes (human-edited)
   The team writes the gotchas they want every run to see.

<!-- AUTO-APPENDED — managed by company-brain. Do not edit by hand. -->
   The pipeline appends timestamped observations here via memory.auto_append.
```

* `memory.load(repo_path)` → returns the file contents or `""` if missing.
  Called by `build_system_prompt` and stitched in under
  `# Repo memory (BRAIN.md)`.
* `memory.auto_append(repo_path, observation)` → adds a timestamped bullet
  to the auto section. Skips when the same observation already appears in
  the trailing 4 KB (kills repetitive spam) and seeds the file from the
  `.brain-template/BRAIN.md` template on first call.

Telemetry echoes both attachments:

```
HarnessResult.telemetry["skill_loaded"]    -> "spring-boot" | "fastapi" | ... | None
HarnessResult.telemetry["brain_md_loaded"] -> True | False
```

These bubble through `PipelineResult.telemetry["harness"]` so existing job
endpoints surface "which skill ran" without an API change.

---

## What's next

* **P4** — hooks, capability declarations, streaming TodoWrite progress.

The legacy linear path stays the default until the P4 acceptance suite is
green for two weeks (per ADR-0051).
