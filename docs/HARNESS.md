# HARNESS — the agentic extraction loop

**Status:** Phase 1 (tool-use harness around the existing stages).
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

## What's next

* **P2** — sub-agents and parallel fan-out via a `spawn_extractor` tool.
* **P3** — per-framework `SKILL.md` + per-repo `BRAIN.md` memory.
* **P4** — hooks, capability declarations, streaming TodoWrite progress.

The legacy linear path stays the default until the P4 acceptance suite is
green for two weeks (per ADR-0051).
