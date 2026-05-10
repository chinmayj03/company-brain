"""spawn_research — fan out read-only research sub-agents (ADR-0051 P2).

The parent agent calls this when it needs a focused answer to a question
that doesn't justify polluting its own context with the supporting reads
("which Spring annotations does this controller use?", "is there a SQL
migration for the lob column rename?"). Each question goes to a sub-agent
with read-only tools; the parent only sees the answer text.

This tool is the on-ramp for P5's WebFetch tool: when WebFetch is added it
joins this allowlist and lets research sub-agents pull framework docs, but
extraction sub-agents keep using the narrower _EXTRACTOR_TOOLS set.

Why a separate spawn_* tool instead of just using spawn_verifier?
  • Verifier emits a structured VERDICT/EVIDENCE block; research is open-
    ended prose. Different output shape → different parent-side handling.
  • Different default `max_iterations` (research often needs more reads).
"""
from __future__ import annotations

import asyncio
from typing import Any

import structlog

from companybrain.config import settings
from companybrain.harness.permissions import Capability
from companybrain.harness.subagent import Subagent, run_with_timeout
from companybrain.harness.tools import register_tool
from companybrain.llm.base import TaskRole, ToolParameter

log = structlog.get_logger(__name__)


# Read-only tools. WebFetch lands in P5; until then research stays in-repo.
_RESEARCH_TOOLS = [
    "read_file",
    "glob_files",
    "grep_code",
]


_RESEARCH_SYSTEM_PROMPT = """\
You are a research sub-agent for company-brain.

Scope: ONE focused question about the repository (or the framework code it
relies on). Your job is to answer it concisely using only read-only tools.

How to do it
------------
1. Plan the search — which files or symbols would prove or disprove the
   answer? Use grep_code first; only read_file when grep gives a hit.
2. Stop as soon as you have enough evidence for a 2-4 sentence answer.

Output
------
Emit a 2-4 sentence text answer. Cite file paths inline when relevant.
Do NOT echo the question, do NOT add headings, do NOT list every file you
read. The parent agent merges your answer into its plan, so brevity wins.

Operating rules
---------------
- You are read-only. spawn_*, write_to_brain, finalize_brain, and
  extract_methods_from_class are not in your allowlist.
- Do not exceed the iteration cap. If two read/grep calls fail to surface
  evidence, return "Inconclusive — see <best path>" and stop.
"""


@register_tool(
    name="spawn_research",
    description=(
        "Fan out read-only research sub-agents to answer focused questions "
        "about the repository (e.g. 'which Spring annotations does this "
        "controller use?'). Each sub-agent gets a fresh context and a "
        "read-only tool subset. Returns one prose answer per question."
    ),
    parameters=[
        ToolParameter(
            "questions", "array",
            "Questions to research. Each item: {question: <text>, "
            "repo_path?: <path>, hints?: [<file or symbol>]}.",
        ),
        ToolParameter(
            "max_iterations", "integer",
            "Per-sub-agent assistant-turn cap (default 8).",
            required=False,
        ),
    ],
    requires=(Capability.READ_REPO, Capability.LLM_CALL),
)
async def handler(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    questions = list(args.get("questions") or [])
    if not questions:
        return {"subagents": 0, "total_cost_usd": 0.0, "results": []}

    max_iter = int(args.get("max_iterations") or 8)
    sem = asyncio.Semaphore(max(1, int(settings.max_subagents)))
    timeout_s = float(settings.subagent_timeout_s)
    default_repo = str(context.get("repo_path") or "")

    async def _one(q_spec: dict[str, Any]) -> dict[str, Any]:
        question = str(q_spec.get("question") or "").strip()
        if not question:
            return {
                "question": "",
                "answer": "",
                "iterations": 0,
                "cost_usd": 0.0,
                "tool_calls": 0,
                "error": "spawn_research: missing 'question' text",
            }
        repo = str(q_spec.get("repo_path") or default_repo)
        hints = list(q_spec.get("hints") or [])

        prompt = (
            f"<question>{question}</question>\n"
            f"<repo_path>{repo}</repo_path>\n"
            f"<hints>{hints}</hints>\n\n"
            "Answer in 2-4 sentences using only read-only tools."
        )

        async with sem:
            agent = Subagent(
                name=f"research:{question[:60]}",
                allowed_tools=_RESEARCH_TOOLS,
                system_prompt=_RESEARCH_SYSTEM_PROMPT,
                role=TaskRole.FAST,
                max_iterations=max_iter,
            )
            res = await run_with_timeout(
                agent, prompt, context=context, timeout_seconds=timeout_s,
            )

        return {
            "question":      question,
            "answer":        res.final_text,
            "iterations":    res.iterations,
            "tool_calls":    res.tool_call_count,
            "cost_usd":      round(res.cost_usd, 6),
            "input_tokens":  res.input_tokens,
            "output_tokens": res.output_tokens,
            "wall_time_seconds": res.wall_time_seconds,
            "timed_out":     res.timed_out,
            "error":         res.error,
        }

    results = await asyncio.gather(*[_one(q) for q in questions])

    payload = {
        "subagents":      len(results),
        "total_cost_usd": round(sum(r["cost_usd"] for r in results), 6),
        "timed_out":      [r["question"] for r in results if r["timed_out"]],
        "results":        results,
    }
    log.info(
        "spawn_research.done",
        questions=payload["subagents"],
        cost_usd=payload["total_cost_usd"],
        timed_out=len(payload["timed_out"]),
    )
    return payload
