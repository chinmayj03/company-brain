"""
FollowupGenerator — ADR-0061 P1 / M4.

When the self-verifier finds gaps that survive the revision pass,
generate 2-3 user-facing follow-up questions that would help resolve them.

Persona-aware:
  "pm"  → product/business language ("Which teams own…", "What is the SLA…")
  "dev" → technical language  ("Which method throws…", "Where is X called from…")
  default → generic phrasing

Uses the FAST LLM role (Haiku-class) — called at most once per query.
"""
from __future__ import annotations

import json

import structlog

from companybrain.llm import TaskRole, get_provider
from companybrain.llm.base import ChatMessage

log = structlog.get_logger(__name__)

_SYSTEM = """\
You generate focused follow-up questions that a user could ask to fill gaps
in a codebase answer.

Rules:
  - Produce exactly 2-3 questions as a JSON array of strings.
  - Each question must be self-contained (the user could ask it cold).
  - Tailor language to the persona: "pm" means product/business vocabulary;
    "dev" means technical/engineering vocabulary.
  - Do NOT repeat the original question.
  - Do NOT produce generic hedges ("you might want to check…") — make each
    question specific to the gap described.

Respond with a JSON array only.
"""

_FALLBACK: list[str] = [
    "Can you show the call chain for this feature?",
    "Which tests cover this code path?",
]


class FollowupGenerator:
    """Generate targeted follow-up questions when verification finds gaps."""

    def __init__(self) -> None:
        self._provider = get_provider()

    async def generate(
        self,
        original_question: str,
        verifier_issues: list[str],
        persona: str = "dev",
    ) -> list[str]:
        """
        Return 2-3 follow-up questions shaped around the verifier's issues.

        Falls back to generic questions on LLM error.
        """
        if not verifier_issues:
            return []

        issues_text = "\n".join(f"- {i}" for i in verifier_issues)
        user_msg = (
            f"ORIGINAL QUESTION: {original_question}\n\n"
            f"GAPS / ISSUES FOUND:\n{issues_text}\n\n"
            f"PERSONA: {persona}\n\n"
            "Generate 2-3 follow-up questions to resolve these gaps. Respond with a JSON array."
        )

        try:
            resp = await self._provider.chat(
                messages=[
                    ChatMessage(role="system", content=_SYSTEM),
                    ChatMessage(role="user", content=user_msg),
                ],
                role=TaskRole.FAST,
                max_tokens=256,
            )
            return self._parse(resp.content)
        except Exception as exc:
            log.warning("[followup_gen] LLM call failed — using fallback", error=str(exc))
            return _FALLBACK[:]

    def _parse(self, raw: str) -> list[str]:
        text = raw.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
        try:
            data = json.loads(text.strip())
            if isinstance(data, list):
                return [str(q) for q in data[:3]]
        except Exception:
            import re
            m = re.search(r'\[[\s\S]*\]', text)
            if m:
                try:
                    data = json.loads(m.group(0))
                    if isinstance(data, list):
                        return [str(q) for q in data[:3]]
                except Exception:
                    pass
        return _FALLBACK[:]
