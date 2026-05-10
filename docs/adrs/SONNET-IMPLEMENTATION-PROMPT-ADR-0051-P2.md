# Implementation Prompt — ADR-0051 Phase 2 (sub-agents + parallel fan-out)

**Single-PR Claude Code session. ~5 days. You're adding Task-style sub-agents to the HarnessLoop from P1, enabling per-file parallel extraction with isolated context windows.**

---

## Pre-flight

1. Read `docs/adrs/ADR-0051-agentic-harness-migration.md` §"Phase 2".
2. Verify P1 is on `main`:
   ```bash
   git log --oneline main | head -50 | grep -q "ADR-0051 P1" \
     || { echo "✗ P1 missing — block on it"; exit 1; }
   ```
3. `git checkout -b feature/adr-0051-p2-subagents` from `main`.

---

## File ownership for THIS PR

CREATE / MODIFY exclusively:

```
src/companybrain/harness/subagent.py
src/companybrain/harness/tools/spawn_extractor.py
src/companybrain/harness/tools/spawn_verifier.py
src/companybrain/harness/tools/spawn_research.py
tests/unit/test_subagent.py
tests/acceptance/test_harness_p2_subagents.py
```

APPEND-ONLY to:

```
src/companybrain/harness/tools/__init__.py    # register new spawn_* tools
src/companybrain/config.py                    # add max_subagents, subagent timeout
docs/HARNESS.md                                # add "Sub-agents" section
```

Do NOT modify `harness/loop.py` or any other foundation file.

---

## Implementation steps

### 1. `harness/subagent.py`

```python
"""Subagent — minimal isolated agent runner.

Distinct from HarnessLoop in two ways:
  1. Fresh context window — does not inherit parent's conversation.
  2. Restricted tool subset — caller specifies which tools the
     sub-agent may use (e.g. read-only research vs. full-extraction).
"""
from dataclasses import dataclass, field
import structlog

from companybrain.providers import get_provider, ChatMessage, TaskRole
from companybrain.harness.tools import TOOL_REGISTRY

log = structlog.get_logger(__name__)


@dataclass
class SubagentResult:
    final_text: str
    tool_calls: list[dict] = field(default_factory=list)
    iterations: int = 0
    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0


class Subagent:
    def __init__(
        self,
        *,
        name: str,
        allowed_tools: list[str],
        system_prompt: str,
        role: TaskRole = TaskRole.BALANCED,
        max_iterations: int = 20,
    ):
        self._name = name
        self._allowed = set(allowed_tools)
        self._system = system_prompt
        self._role = role
        self._max_iter = max_iterations

    async def run(self, prompt: str, *, context: dict) -> SubagentResult:
        provider = get_provider()
        messages = [
            ChatMessage(role="system", content=self._system),
            ChatMessage(role="user",   content=prompt),
        ]
        result = SubagentResult(final_text="")

        for i in range(self._max_iter):
            result.iterations = i + 1
            tools = [TOOL_REGISTRY[n].schema for n in self._allowed if n in TOOL_REGISTRY]
            resp = await provider.chat_with_tools(
                messages=messages, role=self._role, tools=tools, max_tokens=4_000,
            )
            result.input_tokens  += resp.usage.input_tokens
            result.output_tokens += resp.usage.output_tokens
            result.cost_usd      += resp.usage.cost_usd

            if not resp.tool_uses:
                result.final_text = resp.text
                return result

            tool_results = []
            for tu in resp.tool_uses:
                if tu.name not in self._allowed:
                    tool_results.append({"id": tu.id, "error": f"Tool '{tu.name}' not in subagent allowlist"})
                    continue
                tool = TOOL_REGISTRY.get(tu.name)
                try:
                    out = await tool.invoke(tu.input, context=context)
                    tool_results.append({"id": tu.id, "content": out})
                    result.tool_calls.append({"name": tu.name, "input": tu.input})
                except Exception as e:
                    tool_results.append({"id": tu.id, "error": str(e)})

            messages.append(ChatMessage(role="assistant", content=resp.raw))
            messages.append(ChatMessage(role="user",      content=tool_results))

        log.warning("subagent.max_iterations", name=self._name, iterations=self._max_iter)
        return result
```

### 2. `harness/tools/spawn_extractor.py`

```python
"""spawn_extractor — fan out per-file extraction sub-agents.

Parent agent calls this with a list of files; we spawn N sub-agents
in parallel (bounded by max_subagents). Parent only sees the merged
result list, not each sub-agent's tool-call trajectory.
"""
import asyncio
from companybrain.harness.subagent import Subagent
from companybrain.config import settings
from . import register_tool


_SUBAGENT_SYSTEM = """You are an extraction sub-agent.

Your scope: ONE file. Extract all entities (Method, Class, etc.) and
edges (CALLS, READS_COLUMN, ...) using extract_methods_from_class.
Return a structured JSON list.

Tools you may use:
- read_file
- glob_files (within this file's directory only)
- grep_code
- extract_methods_from_class

Stop calling tools when the file is fully extracted.
"""


@register_tool(
    name="spawn_extractor",
    description="Spawn sub-agents to extract a list of files in parallel. "
                "Each sub-agent gets a fresh context window.",
    input_schema={
        "type": "object",
        "properties": {
            "files": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "methods": {"type": "array", "items": {"type": "string"}},
                        "role": {"type": "string"},
                    },
                    "required": ["path"],
                },
            },
        },
        "required": ["files"],
    },
)
async def handler(args, context):
    files = args["files"]
    sem = asyncio.Semaphore(settings.max_subagents)

    async def _one(file_spec):
        async with sem:
            agent = Subagent(
                name=f"extractor:{file_spec['path']}",
                allowed_tools=["read_file", "glob_files", "grep_code", "extract_methods_from_class"],
                system_prompt=_SUBAGENT_SYSTEM,
            )
            prompt = (
                f"<file>{file_spec['path']}</file>\n"
                f"<methods>{file_spec.get('methods', [])}</methods>\n"
                f"<role>{file_spec.get('role','')}</role>\n"
                "Extract all entities and edges for this file."
            )
            return await agent.run(prompt, context=context)

    results = await asyncio.gather(*[_one(f) for f in files])
    return {
        "subagents": len(results),
        "total_cost_usd": sum(r.cost_usd for r in results),
        "results": [
            {"file": files[i]["path"], "summary": r.final_text,
             "iterations": r.iterations, "cost_usd": r.cost_usd}
            for i, r in enumerate(results)
        ],
    }
```

### 3. `spawn_verifier` and `spawn_research`

Analogous patterns. `spawn_verifier` cross-checks claims (e.g. "this entity says it READS_COLUMN `lob` — verify by grep"). `spawn_research` has WebFetch tool for framework-doc lookups (WebFetch tool is registered in P5 — for now `spawn_research` uses only built-in tools).

### 4. Config

```python
# config.py
max_subagents:      int = 8     # max concurrent sub-agents
subagent_timeout_s: int = 120   # per-sub-agent wall-time cap
```

---

## Acceptance test

```python
@pytest.mark.asyncio
async def test_60_method_endpoint_under_30_seconds():
    """End-to-end harness run on lob endpoint completes < 30s wall time."""
    import time
    monkeypatch.setenv("BRAIN_USE_HARNESS", "true")
    start = time.perf_counter()
    result = await run_pipeline(endpoint="/competitiveness/summary/competitors/payer", method="POST")
    assert (time.perf_counter() - start) < 30
    assert result.telemetry["subagent_count"] >= 5


@pytest.mark.asyncio
async def test_subagent_context_isolation(tracing):
    """Sub-agents must not see the parent's full conversation."""
    spans = await run_pipeline_harness_with_tracing(...)
    parent = next(s for s in spans if s.name == "harness.parent")
    for sa in (s for s in spans if s.name.startswith("harness.subagent.")):
        assert sa.input_tokens < parent.input_tokens / 2
```

---

## PR description

```
feat(harness): sub-agents and parallel fan-out (ADR-0051 P2)

Adds spawn_extractor / spawn_verifier / spawn_research Task-style tools.
Each sub-agent has its own context window (~5× smaller input than the
parent's accumulated state) and a configurable tool subset.

Acceptance: 60-method endpoint completes < 30s wall time; sub-agent input
< 50% of parent input (proves context isolation).
```
