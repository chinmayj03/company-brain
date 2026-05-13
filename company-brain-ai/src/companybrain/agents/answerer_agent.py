"""
AnswererAgent — ADR-0061 P1 single-pass baseline.

Extracts the pre-ADR-0061 single-pass LLM call into a reusable class so
the iterative path can fall back to it and tests can unit-test it in isolation.

Intentionally has NO dependency on companybrain.api.routes.query so it can be
imported in test environments where fastapi is not installed.

This class is KEPT as the fallback; do NOT delete it when the iterative
path becomes default.
"""
from __future__ import annotations

import json
from typing import Any

import structlog

from companybrain.llm import TaskRole, get_provider
from companybrain.llm.base import ChatMessage
from companybrain.models.query_response import Confidence, QueryResponse

log = structlog.get_logger(__name__)


class AnswererAgent:
    """Single-pass LLM answerer — the pre-ADR-0061 baseline."""

    def __init__(self, system_prompt: str | None = None) -> None:
        self._provider = get_provider()
        if system_prompt is None:
            from companybrain.api.prompts.query_system import QUERY_SYSTEM_PROMPT
            system_prompt = QUERY_SYSTEM_PROMPT
        self._system = system_prompt

    async def answer(self, question: str, context: str | None) -> QueryResponse:
        """Call the LLM once and return a typed QueryResponse."""
        user_content = self._build_user_content(question, context)

        resp = await self._provider.chat(
            messages=[
                ChatMessage(role="system", content=self._system),
                ChatMessage(role="user", content=user_content),
            ],
            role=TaskRole.QUERY,
            max_tokens=4_096,
        )
        return _parse_response(resp.content, context)

    def _build_user_content(self, question: str, context: str | None) -> str:
        try:
            from companybrain.api.prompts.user_message import build_user_message
            return build_user_message(question, intent="concept", context=context)
        except Exception:
            return _plain_user_message(question, context)


def _parse_response(raw: str, context: str | None) -> QueryResponse:
    """Parse LLM output to QueryResponse; wraps free-form text in a minimal envelope."""
    lines = raw.strip().splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    cleaned = "\n".join(lines).strip()

    try:
        data: dict[str, Any] = json.loads(cleaned)
        return QueryResponse(**data)
    except Exception as exc:
        log.warning("[answerer_agent] LLM output not valid JSON — wrapping",
                    error=str(exc), preview=raw[:200])
        return QueryResponse(
            summary=raw,
            confidence=Confidence(
                level="medium" if context else "low",
                rationale="LLM returned free-form text",
            ),
        )


def _plain_user_message(question: str, context: str | None) -> str:
    if context:
        return f"KNOWLEDGE BASE:\n\n{context}\n\n---\n\nQUESTION: {question}"
    return (
        f"QUESTION: {question}\n\n"
        "Note: No brain context available. Run the extraction pipeline first."
    )
