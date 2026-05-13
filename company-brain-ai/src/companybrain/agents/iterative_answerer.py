"""
IterativeAnswerer — ADR-0061 P1 / M1–M4.

High-level entry point that wires together:
  - ExplorationLoop (M1/M2: iterative retrieval + answer)
  - SelfVerifier    (M3: verify claims against citations)
  - FollowupGenerator (M4: surface gaps as user questions)

Called from query/orchestrator.py; not called directly by the route.

Usage::

    answerer = IterativeAnswerer(retrieve_fn=my_retrieve)
    result: AnswerResult = await answerer.answer(question, context)
    response = result.response  # QueryResponse, same shape as before

Replaces the single-pass path when ITERATIVE_EXPLORATION_ENABLED=true.
AnswererAgent (single-pass) is retained as fallback in orchestrator.py.
"""
from __future__ import annotations

from typing import Awaitable, Callable, Optional

import structlog

from companybrain.query.exploration_loop import AnswerResult, ExplorationLoop

log = structlog.get_logger(__name__)

RetrieveFn = Callable[[str], Awaitable[Optional[str]]]


class IterativeAnswerer:
    """
    Iterative answerer: exploration loop + self-verification + followup generation.

    Parameters
    ----------
    retrieve_fn
        Async callable ``(query_text) → str | None`` used during exploration
        iterations to pull additional evidence.
    system_prompt
        Override the query system prompt (mostly useful in tests).
    persona
        User persona for followup question framing: "pm" | "dev" (default).
    """

    def __init__(
        self,
        retrieve_fn: RetrieveFn,
        system_prompt: str | None = None,
        persona: str = "dev",
    ) -> None:
        self._retrieve = retrieve_fn
        self._system = system_prompt
        self._persona = persona

    async def answer(self, question: str, context: str | None) -> AnswerResult:
        """
        Run the full iterative explore-then-answer cycle.

        Returns an AnswerResult whose .response field is a QueryResponse
        compatible with the existing /query contract.
        """
        from companybrain.config import settings

        loop = ExplorationLoop(
            retrieve_fn=self._retrieve,
            system_prompt=self._system,
            max_iterations=settings.iterative_max_iterations,
            max_extra_retrieve=settings.iterative_max_extra_retrieve,
            max_tokens=settings.iterative_max_tokens_per_query,
            verifier_score_threshold=settings.iterative_verifier_score_threshold,
        )

        result = await loop.run(question, context, persona=self._persona)

        log.info(
            "[iterative_answerer] done",
            question_preview=question[:60],
            iterations=result.iterations_taken,
            verifier_score=result.verifier_score,
            exploration_invoked=result.exploration_agent_invoked,
            citations=len(result.response.affected_entities),
            unverified_claims=len(result.unverified_claims),
        )
        return result
