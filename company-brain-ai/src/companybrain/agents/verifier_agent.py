"""
ADR-0056 Mode B — sub-agent verifier (Haiku-class LLM).

Used only when the deterministic check (Mode A) returns ``fuzzy`` or
``hallucinated``. Asks a fast model whether the claim is genuinely present in
the supplied source excerpt and returns one of YES / NO / PARTIAL.

The prompt is deliberately tiny so each call lands at roughly $0.001. Around
10% of entities reach this mode in steady state.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Literal

import structlog

from companybrain.llm import ChatMessage, TaskRole, get_provider

log = structlog.get_logger(__name__)


_SYSTEM_PROMPT = """\
You are a code-claim verifier. Given a CLAIM about a piece of code and an
EXCERPT of the actual source file, judge whether the claim is genuinely
present in the source.

Reply with a single compact JSON object — no prose, no markdown:
{"result": "YES" | "NO" | "PARTIAL", "reason": "one-line reason"}

- YES: the claim text appears in the source (verbatim or modulo whitespace/case).
- PARTIAL: the claim's *meaning* matches but the exact tokens differ — for
  example, semantically equivalent SQL or a paraphrased code line.
- NO: the claim is not in the source. Do NOT mark YES just because the claim
  looks like the kind of code the file *could* contain — only if it is actually
  there.

Output the JSON object and nothing else.
"""


@dataclass
class SubagentVerdict:
    """Result of a single Mode-B verifier call."""

    result: Literal["YES", "NO", "PARTIAL"]
    reason: str


class VerifierAgent:
    """Wraps a fast LLM behind the YES/NO/PARTIAL prompt.

    The underlying provider is resolved via ``get_provider()`` so callers
    inherit whichever Anthropic / OpenAI / Ollama backend is configured for
    the run.
    """

    def __init__(self) -> None:
        # Resolve lazily — constructing the loop must not require the LLM
        # provider to be configured (so CI and unit tests can run dry).
        self._provider = None

    async def verify(
        self,
        claim: str,
        source_excerpt: str,
        file_path: str = "",
    ) -> SubagentVerdict:
        user = (
            f"FILE: {file_path}\n"
            f"<source>\n{source_excerpt}\n</source>\n"
            f"<claim>\n{claim}\n</claim>"
        )
        if self._provider is None:
            self._provider = get_provider()
        try:
            raw = await self._provider.chat_json(
                messages=[
                    ChatMessage(role="system", content=_SYSTEM_PROMPT),
                    ChatMessage(role="user", content=user),
                ],
                role=TaskRole.FAST,
                max_tokens=120,
            )
        except Exception as exc:
            log.warning("[verifier_agent] LLM call failed", error=str(exc))
            return SubagentVerdict("PARTIAL", f"verifier llm failed: {exc}")

        return parse_verdict(raw)


def parse_verdict(raw: str) -> SubagentVerdict:
    """Parse the LLM response into a SubagentVerdict. Tolerant of stray
    markdown fences and of models that ignore the JSON instruction."""
    cleaned = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.MULTILINE)
    cleaned = re.sub(r"```\s*$", "", cleaned, flags=re.MULTILINE).strip()
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        upper = cleaned.upper()
        for verdict in ("PARTIAL", "YES", "NO"):
            if verdict in upper:
                return SubagentVerdict(verdict, "parsed from non-JSON response")
        return SubagentVerdict("PARTIAL", "could not parse response")

    if not isinstance(data, dict):
        return SubagentVerdict("PARTIAL", "non-object JSON response")
    result = str(data.get("result", "")).upper()
    if result not in ("YES", "NO", "PARTIAL"):
        return SubagentVerdict("PARTIAL", f"unknown result: {result!r}")
    return SubagentVerdict(result, str(data.get("reason", "")))
