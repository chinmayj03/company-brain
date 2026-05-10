"""spawn_extractor — fan out per-file extraction sub-agents (ADR-0051 P2).

The parent agent calls this with a list of files; the tool spawns one
sub-agent per file (bounded by `settings.max_subagents`) and waits for all
of them to finish before returning a merged summary. Each sub-agent has its
own context window, so the parent's input tokens stay flat regardless of
how many files are in the batch — the whole point of P2.

Why this is a tool rather than a method on HarnessLoop:
  • It composes — the parent agent decides when to fan out vs. extract one
    file inline via extract_methods_from_class.
  • Output is a flat JSON payload the parent can reason about; sub-agent
    tool-call trajectories never enter the parent's context.
  • Concurrency cap and timeout live in `companybrain.config.settings`, so
    operators can tune them per environment without code changes.
"""
from __future__ import annotations

import asyncio
from typing import Any

import structlog

from companybrain.config import settings
from companybrain.harness.subagent import Subagent, run_with_timeout
from companybrain.harness.tools import register_tool
from companybrain.llm.base import TaskRole, ToolParameter

log = structlog.get_logger(__name__)


# Tools the extraction sub-agent is allowed to call. Deliberately narrow:
# the sub-agent's job is "read the file, extract its methods" — not browse
# the whole repo, write to the brain, or finalize anything.
_EXTRACTOR_TOOLS = [
    "read_file",
    "glob_files",
    "grep_code",
    "extract_methods_from_class",
]


_EXTRACTOR_SYSTEM_PROMPT = """\
You are an extraction sub-agent for company-brain.

Scope: ONE source file. Your job is to extract every method-level entity
and edge it contains, then emit a short text summary the parent agent can
merge into the brain.

How to do it
------------
1. Call `extract_methods_from_class(file=<path>, methods=<list>)` exactly
   once per file. Pass the qualified method names you were given.
2. If the method list is empty or unknown, call `read_file` first, identify
   methods worth extracting, then run extract_methods_from_class with that
   list.
3. Use `grep_code` and `glob_files` only for orientation when the file
   references a symbol you cannot resolve from its own contents.

Stop conditions
---------------
- After one successful extract_methods_from_class call, write a one-sentence
  text summary of what was extracted ("entities=N, edges=M, kinds=...") and
  stop. The parent agent persists results via write_to_brain — sub-agents
  do NOT call write_to_brain or finalize_brain.

Operating rules
---------------
- Do NOT request files or methods outside the scope you were given.
- Do NOT loop on the same failing tool call. If extract_methods_from_class
  returns errors for every requested method, stop and emit a short text
  describing the failure.
"""


@register_tool(
    name="spawn_extractor",
    description=(
        "Fan out per-file extraction sub-agents in parallel. Each entry in "
        "`files` produces ONE sub-agent with its own context window; the "
        "parent only sees the merged summary list, never the sub-agents' "
        "tool-call trajectories. Concurrency is bounded by max_subagents."
    ),
    parameters=[
        ToolParameter(
            "files", "array",
            "Files to extract. Each item: "
            "{path: <abs path>, methods?: [<qname>, ...], role?: <hint>}.",
        ),
        ToolParameter(
            "max_iterations", "integer",
            "Per-sub-agent assistant-turn cap (default 10). Lower for cheap "
            "fan-outs, higher when files need orientation.",
            required=False,
        ),
    ],
)
async def handler(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    files = list(args.get("files") or [])
    if not files:
        return {"subagents": 0, "total_cost_usd": 0.0, "results": []}

    max_iter = int(args.get("max_iterations") or 10)
    sem = asyncio.Semaphore(max(1, int(settings.max_subagents)))
    timeout_s = float(settings.subagent_timeout_s)

    async def _one(file_spec: dict[str, Any]) -> dict[str, Any]:
        path = str(file_spec.get("path") or "")
        if not path:
            return {
                "file": "",
                "summary": "",
                "error": "spawn_extractor: missing 'path' in file_spec",
                "iterations": 0,
                "cost_usd": 0.0,
                "input_tokens": 0,
                "output_tokens": 0,
                "tool_calls": 0,
            }
        methods = list(file_spec.get("methods") or [])
        role_hint = file_spec.get("role") or ""

        prompt = (
            f"<file>{path}</file>\n"
            f"<methods>{methods}</methods>\n"
            f"<role>{role_hint}</role>\n\n"
            "Extract every entity and edge in this file using "
            "extract_methods_from_class, then emit a one-sentence summary."
        )

        async with sem:
            agent = Subagent(
                name=f"extractor:{path}",
                allowed_tools=_EXTRACTOR_TOOLS,
                system_prompt=_EXTRACTOR_SYSTEM_PROMPT,
                role=TaskRole.FAST,
                max_iterations=max_iter,
            )
            res = await run_with_timeout(
                agent, prompt, context=context, timeout_seconds=timeout_s,
            )

        return {
            "file":          path,
            "summary":       res.final_text,
            "iterations":    res.iterations,
            "tool_calls":    res.tool_call_count,
            "cost_usd":      round(res.cost_usd, 6),
            "input_tokens":  res.input_tokens,
            "output_tokens": res.output_tokens,
            "wall_time_seconds": res.wall_time_seconds,
            "timed_out":     res.timed_out,
            "error":         res.error,
        }

    started = asyncio.get_event_loop().time()
    results = await asyncio.gather(*[_one(f) for f in files])
    wall = round(asyncio.get_event_loop().time() - started, 3)

    payload = {
        "subagents":          len(results),
        "total_cost_usd":     round(sum(r["cost_usd"] for r in results), 6),
        "total_input_tokens": sum(r["input_tokens"] for r in results),
        "total_output_tokens": sum(r["output_tokens"] for r in results),
        "wall_time_seconds":  wall,
        "max_concurrency":    int(settings.max_subagents),
        "timed_out":          [r["file"] for r in results if r["timed_out"]],
        "results":            results,
    }
    log.info(
        "spawn_extractor.done",
        subagents=payload["subagents"],
        cost_usd=payload["total_cost_usd"],
        wall_time_seconds=wall,
        timed_out=len(payload["timed_out"]),
    )
    return payload
