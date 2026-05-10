"""spawn_verifier — fan out claim-verification sub-agents (ADR-0051 P2).

The parent agent calls this with a list of claims (e.g. "entity X has a
CALLS edge to Y", "method Z reads column `lob`") and the verifier sub-agents
re-derive each claim from primary sources (grep, file reads) without
inheriting the parent's reasoning. The parent only sees a per-claim
{verdict, evidence} payload — useful for cheap consistency checks before
the brain is finalized.

Verifier sub-agents are READ-ONLY: they cannot extract entities, write to
the brain, or finalize. The allowlist enforces this; misuse surfaces as a
tool-blocked error to the model so it can re-plan.
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


# Read-only tool subset — verifiers may inspect, never mutate.
_VERIFIER_TOOLS = [
    "read_file",
    "glob_files",
    "grep_code",
]


_VERIFIER_SYSTEM_PROMPT = """\
You are a verification sub-agent for company-brain.

Scope: ONE claim about an extracted entity or edge. Your job is to decide
whether the claim is supported by primary sources in the repository.

How to do it
------------
1. Read the claim carefully. It will state something like
   "entity X.method has a CALLS edge to Y.method" or
   "method Z reads database column `lob`".
2. Use `grep_code` and `read_file` to find evidence in the source code.
3. Decide: supported / contradicted / inconclusive.

Output
------
Emit a single short text response in this exact format:

  VERDICT: <supported|contradicted|inconclusive>
  EVIDENCE: <one sentence quoting or paraphrasing the proof>

Operating rules
---------------
- You are read-only. Do NOT call extract_methods_from_class, write_to_brain,
  spawn_*, or finalize_brain — they are not in your allowlist and will
  return errors.
- Do not loop more than a few grep/read calls. If two grep calls find no
  evidence either way, emit "inconclusive" and stop.
"""


@register_tool(
    name="spawn_verifier",
    description=(
        "Fan out read-only sub-agents to verify a list of claims about "
        "extracted entities or edges. Each sub-agent gets a fresh context "
        "and read-only tools (grep_code, read_file, glob_files). Returns "
        "a {claim, verdict, evidence} payload per item."
    ),
    parameters=[
        ToolParameter(
            "claims", "array",
            "Claims to verify. Each item: {claim: <text>, repo_path?: <path>}. "
            "If repo_path is omitted the verifier uses context['repo_path'].",
        ),
        ToolParameter(
            "max_iterations", "integer",
            "Per-sub-agent assistant-turn cap (default 6).",
            required=False,
        ),
    ],
)
async def handler(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    claims = list(args.get("claims") or [])
    if not claims:
        return {"subagents": 0, "total_cost_usd": 0.0, "results": []}

    max_iter = int(args.get("max_iterations") or 6)
    sem = asyncio.Semaphore(max(1, int(settings.max_subagents)))
    timeout_s = float(settings.subagent_timeout_s)
    default_repo = str(context.get("repo_path") or "")

    async def _one(claim_spec: dict[str, Any]) -> dict[str, Any]:
        claim_text = str(claim_spec.get("claim") or "").strip()
        if not claim_text:
            return {
                "claim": "",
                "verdict": "inconclusive",
                "evidence": "spawn_verifier: empty claim",
                "summary": "",
                "iterations": 0,
                "cost_usd": 0.0,
                "tool_calls": 0,
                "error": "missing 'claim' text",
            }
        repo = str(claim_spec.get("repo_path") or default_repo)

        prompt = (
            f"<claim>{claim_text}</claim>\n"
            f"<repo_path>{repo}</repo_path>\n\n"
            "Verify the claim using only read-only tools. End with the "
            "VERDICT/EVIDENCE block described in your system prompt."
        )

        async with sem:
            agent = Subagent(
                name=f"verifier:{claim_text[:60]}",
                allowed_tools=_VERIFIER_TOOLS,
                system_prompt=_VERIFIER_SYSTEM_PROMPT,
                role=TaskRole.FAST,
                max_iterations=max_iter,
            )
            res = await run_with_timeout(
                agent, prompt, context=context, timeout_seconds=timeout_s,
            )

        verdict, evidence = _parse_verdict(res.final_text)
        return {
            "claim":         claim_text,
            "verdict":       verdict,
            "evidence":      evidence,
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

    results = await asyncio.gather(*[_one(c) for c in claims])

    payload = {
        "subagents":          len(results),
        "total_cost_usd":     round(sum(r["cost_usd"] for r in results), 6),
        "supported":          sum(1 for r in results if r["verdict"] == "supported"),
        "contradicted":       sum(1 for r in results if r["verdict"] == "contradicted"),
        "inconclusive":       sum(1 for r in results if r["verdict"] == "inconclusive"),
        "timed_out":          [r["claim"] for r in results if r["timed_out"]],
        "results":            results,
    }
    log.info(
        "spawn_verifier.done",
        claims=payload["subagents"],
        supported=payload["supported"],
        contradicted=payload["contradicted"],
        inconclusive=payload["inconclusive"],
    )
    return payload


def _parse_verdict(text: str) -> tuple[str, str]:
    """Pull (verdict, evidence) out of the sub-agent's final text.

    The system prompt asks for a 'VERDICT: ...' / 'EVIDENCE: ...' block;
    if the model deviates we fall back to 'inconclusive' so callers always
    get a usable verdict field.
    """
    verdict = "inconclusive"
    evidence = ""
    for raw in (text or "").splitlines():
        line = raw.strip()
        lower = line.lower()
        if lower.startswith("verdict:"):
            value = line.split(":", 1)[1].strip().lower()
            if value in ("supported", "contradicted", "inconclusive"):
                verdict = value
        elif lower.startswith("evidence:"):
            evidence = line.split(":", 1)[1].strip()
    return verdict, evidence
