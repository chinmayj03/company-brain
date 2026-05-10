# Implementation Prompt — ADR-0051 Phase 1 (HarnessLoop + tool registry)

**This is a single-PR Claude Code session. Estimated effort: 5 days. You're landing the foundational HarnessLoop that replaces `pipeline/orchestrator.py`'s linear stage machine with a prompt-controlled tool-use loop.**

---

## Pre-flight

1. Read `docs/adrs/ADR-0051-agentic-harness-migration.md` §"Decision" + §"Phase 1".
2. Verify prereqs are merged on `main`:
   ```bash
   for adr in 0048 0049 0050; do
     git log --oneline main | head -100 | grep -q "ADR-${adr}" \
       && echo "✓ ADR-${adr} merged" || { echo "✗ ADR-${adr} MISSING — block on it"; exit 1; }
   done
   ```
3. `git checkout -b feature/adr-0051-p1-harness-loop` from `main`.

---

## File ownership for THIS PR (do not touch anything else)

CREATE / MODIFY exclusively:

```
src/companybrain/harness/__init__.py
src/companybrain/harness/loop.py
src/companybrain/harness/system_prompt.py
src/companybrain/harness/tools/__init__.py
src/companybrain/harness/tools/discover_routes.py
src/companybrain/harness/tools/find_entry_handler.py
src/companybrain/harness/tools/list_candidate_files.py
src/companybrain/harness/tools/read_file.py
src/companybrain/harness/tools/glob_files.py
src/companybrain/harness/tools/grep_code.py
src/companybrain/harness/tools/extract_methods_from_class.py
src/companybrain/harness/tools/write_to_brain.py
src/companybrain/harness/tools/finalize_brain.py
docs/HARNESS.md
tests/unit/test_harness_loop.py
tests/acceptance/test_harness_p1.py
```

APPEND-ONLY to:

```
src/companybrain/pipeline/orchestrator.py    # add BRAIN_USE_HARNESS=true delegate
src/companybrain/config.py                   # add use_harness flag
```

Do NOT touch any other file.

---

## Implementation steps

### 1. `harness/loop.py`

```python
"""HarnessLoop — prompt-controlled tool dispatch.

Owns the conversation history. Each iteration:
  1. Send messages to the LLM
  2. If response is text-only → return as final answer
  3. If response has tool_use blocks → validate, dispatch, append tool_results
  4. Repeat
"""
from dataclasses import dataclass, field
from typing import Any
import structlog

from companybrain.providers import get_provider, ChatMessage, TaskRole
from companybrain.harness.system_prompt import build_system_prompt
from companybrain.harness.tools import TOOL_REGISTRY

log = structlog.get_logger(__name__)


@dataclass
class HarnessResult:
    final_text: str
    tool_calls: list[dict] = field(default_factory=list)
    iterations: int = 0
    telemetry: dict = field(default_factory=dict)


class HarnessLoop:
    def __init__(self, *, max_iterations: int = 50, role: TaskRole = TaskRole.BALANCED):
        self._provider = get_provider()
        self._max_iter = max_iterations
        self._role = role

    async def run(self, user_message: str, *, context: dict) -> HarnessResult:
        messages = [
            ChatMessage(role="system", content=build_system_prompt(context)),
            ChatMessage(role="user",   content=user_message),
        ]
        result = HarnessResult(final_text="")

        for i in range(self._max_iter):
            result.iterations = i + 1
            resp = await self._provider.chat_with_tools(
                messages=messages, role=self._role,
                tools=[t.schema for t in TOOL_REGISTRY.values()],
                max_tokens=4_000,
            )
            if not resp.tool_uses:
                result.final_text = resp.text
                return result

            # Execute tool_use blocks (potentially in parallel)
            tool_results = []
            for tu in resp.tool_uses:
                tool = TOOL_REGISTRY.get(tu.name)
                if tool is None:
                    tool_results.append({"id": tu.id, "error": f"Unknown tool: {tu.name}"})
                    continue
                try:
                    out = await tool.invoke(tu.input, context=context)
                    tool_results.append({"id": tu.id, "content": out})
                    result.tool_calls.append({"name": tu.name, "input": tu.input})
                except Exception as e:
                    tool_results.append({"id": tu.id, "error": str(e)})
                    log.exception("harness.tool_error", tool=tu.name)

            messages.append(ChatMessage(role="assistant", content=resp.raw))
            messages.append(ChatMessage(role="user", content=tool_results))

        log.warning("harness.max_iterations_reached", iterations=self._max_iter)
        return result
```

### 2. `harness/tools/__init__.py` — registry pattern

```python
"""Tool registry. Each tool registers itself via @register_tool."""
from typing import Callable, Awaitable

TOOL_REGISTRY: dict[str, "Tool"] = {}


class Tool:
    def __init__(self, name: str, description: str, schema: dict,
                 handler: Callable[..., Awaitable[Any]]):
        self.name = name
        self.description = description
        self.schema = {"name": name, "description": description, "input_schema": schema}
        self._handler = handler

    async def invoke(self, args: dict, *, context: dict):
        return await self._handler(args, context)


def register_tool(name: str, description: str, input_schema: dict):
    def deco(fn):
        TOOL_REGISTRY[name] = Tool(name, description, input_schema, fn)
        return fn
    return deco


# Import all tool modules so their @register_tool decorators fire
from . import (
    discover_routes, find_entry_handler, list_candidate_files,
    read_file, glob_files, grep_code, extract_methods_from_class,
    write_to_brain, finalize_brain,
)
```

### 3. Each tool wraps an existing pipeline function

Example, `harness/tools/discover_routes.py`:

```python
from companybrain.collectors.code_tracer import discover_routes as _discover
from . import register_tool


@register_tool(
    name="discover_routes",
    description="List all controller routes in a repo. Returns [(METHOD, path, file)].",
    input_schema={
        "type": "object",
        "properties": {"repo_path": {"type": "string"}},
        "required": ["repo_path"],
    },
)
async def handler(args, context):
    from pathlib import Path
    routes = _discover(Path(args["repo_path"]))
    return [{"method": m, "path": p, "file": f} for m, p, f in routes]
```

Repeat the pattern for `find_entry_handler`, `list_candidate_files` (wraps ADR-0050's `manifest_filter`), `read_file` (FileCache from ADR-0049), `extract_methods_from_class` (ContextAgent from ADR-0048), `write_to_brain`, `finalize_brain`. `glob_files` and `grep_code` are new — implement via stdlib `Path.rglob` and the existing `grep_code` shell tool respectively.

### 4. `harness/system_prompt.py`

```python
def build_system_prompt(context: dict) -> str:
    from companybrain.harness.tools import TOOL_REGISTRY
    tool_list = "\n".join(
        f"- {t.name}: {t.description}" for t in TOOL_REGISTRY.values()
    )
    return f"""You are the company-brain extraction agent.

Goal: extract entities, relationships, and business context for an
endpoint's call chain into the persistent brain store.

Canonical pipeline (you may deviate if you see a better path, but
explain why):
  1. discover_routes(repo_path) — confirm the endpoint exists.
  2. find_entry_handler(endpoint, method, repo_path) — identify the controller.
  3. list_candidate_files(endpoint, repo_path) — get filtered manifest.
  4. For each candidate: extract_methods_from_class(file, methods).
  5. write_to_brain(entities, edges).
  6. finalize_brain(workspace_id).

Available tools:
{tool_list}

Guidelines:
- Prefer batched tool calls in one assistant turn when actions are independent.
- Stop calling tools when extraction is complete; emit a summary as text.
- If a tool fails, explain why and either retry with different args or stop.
"""
```

### 5. Wire into orchestrator (append-only)

In `pipeline/orchestrator.py::run_pipeline`, near the top:

```python
from companybrain.config import settings
if settings.use_harness:
    from companybrain.harness.loop import HarnessLoop
    return await HarnessLoop().run(
        user_message=f"Extract {request.http_method} {request.endpoint_path} from {request.repos[0].local_path}",
        context={"request": request, "workspace_id": request.workspace_id, ...},
    )
# else: fall through to existing legacy pipeline
```

In `config.py`:

```python
use_harness: bool = False    # BRAIN_USE_HARNESS — flip to True after P4 acceptance suite passes
```

### 6. `docs/HARNESS.md` (first version)

Document the loop architecture, the tool registry, and how to add a new tool. Keep ≤ 200 lines.

---

## Acceptance test

`tests/acceptance/test_harness_p1.py`:

```python
import pytest

@pytest.mark.asyncio
async def test_harness_extracts_lob_endpoint_matching_legacy(monkeypatch):
    """Run lob endpoint through harness; brain output must match legacy ±5%."""
    monkeypatch.setenv("BRAIN_USE_HARNESS", "false")
    legacy = await run_pipeline(endpoint="/competitiveness/summary/competitors/payer", method="POST")

    monkeypatch.setenv("BRAIN_USE_HARNESS", "true")
    harness = await run_pipeline(endpoint="/competitiveness/summary/competitors/payer", method="POST")

    legacy_count, harness_count = legacy.entity_count, harness.entity_count
    assert abs(legacy_count - harness_count) / legacy_count < 0.05
    plan_repo = harness.brain.read("CompetitivenessPlanRepository.getPayerCompetitors")
    assert "lob" in (plan_repo.metadata.get("query_text") or "")
```

---

## Verification

```bash
.venv/bin/mypy src/companybrain/harness
.venv/bin/ruff check src/companybrain/harness
.venv/bin/pytest tests/unit/test_harness_loop.py tests/acceptance/test_harness_p1.py -v
```

---

## PR description

```
feat(harness): tool-use loop foundation (ADR-0051 P1)

Replaces pipeline/orchestrator.py's linear stage machine with a
prompt-controlled HarnessLoop. 9 tools wrap existing pipeline calls;
the model decides which to invoke based on conversation state.

Behind BRAIN_USE_HARNESS=true. Legacy path remains default until P4
acceptance suite is green for two weeks.

Acceptance: lob endpoint extracted via harness matches legacy output
±5% on entity count, with extraction quality preserved (lob query
still finds .lob(r.value4())).
```
