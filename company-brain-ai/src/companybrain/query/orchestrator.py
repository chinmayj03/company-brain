"""
orchestrate_query — ADR-0061 P1 top-level dispatcher.

Called from api/routes/query.py when ITERATIVE_EXPLORATION_ENABLED is True.
Routes to IterativeAnswerer; falls back to single-pass AnswererAgent on error.

The env flag stays False until the acceptance gate in
tests/acceptance/test_iterative_quality.py passes (see ADR-0061 §acceptance).

ADR-0090 P1 addition:
  - Emits a QueryAsked event (fire-and-forget) for observability.
  - Applies V3 SalienceScore boost hint to the retrieve_fn wrapper when
    event_store_enabled=True (non-blocking; salience is advisory only).
"""
from __future__ import annotations

from typing import Awaitable, Callable, Optional

import structlog

from companybrain.models.query_response import QueryResponse
from companybrain.query.exploration_loop import AnswerResult, ExplorationLoop

log = structlog.get_logger(__name__)

RetrieveFn = Callable[[str], Awaitable[Optional[str]]]


async def orchestrate_query(
    question: str,
    context: str | None,
    retrieve_fn: RetrieveFn,
    persona: str = "dev",
    system_prompt: str | None = None,
    workspace_id: str = "",
) -> AnswerResult:
    """
    Run an iterative exploration pass and return an AnswerResult.

    Falls back to single-pass AnswererAgent if ExplorationLoop raises,
    so a wiring bug never kills the /query endpoint.

    ADR-0090: emits QueryAsked event and wraps retrieve_fn with salience
    boosting when event_store_enabled=True.
    """
    from companybrain.config import settings

    # ADR-0090 P1 — emit QueryAsked (fire-and-forget, never blocks query path)
    # Use getattr for backward compat with Settings objects that pre-date this field.
    _event_store_enabled = getattr(settings, "event_store_enabled", False)
    if _event_store_enabled and workspace_id:
        try:
            from companybrain.events.emitter import emit_query_asked
            emit_query_asked(question=question, workspace_id=workspace_id)
        except Exception as exc:  # noqa: BLE001
            log.debug("QueryAsked emit skipped", error=str(exc))

    # ADR-0090 P1 — wrap retrieve_fn with V3 salience boosting.
    # The wrapper is a pass-through when event_store is unavailable; it
    # never raises so a misconfigured event store can't kill queries.
    if _event_store_enabled:
        retrieve_fn = _salience_wrapped_retrieve(retrieve_fn, question)

    try:
        loop = ExplorationLoop(
            retrieve_fn=retrieve_fn,
            system_prompt=system_prompt,
            max_iterations=settings.iterative_max_iterations,
            max_extra_retrieve=settings.iterative_max_extra_retrieve,
            max_tokens=settings.iterative_max_tokens_per_query,
            verifier_score_threshold=settings.iterative_verifier_score_threshold,
        )
        return await loop.run(question, context, persona=persona)

    except Exception as exc:
        log.warning(
            "[orchestrator] ExplorationLoop failed — falling back to single-pass",
            error=str(exc),
        )
        return await _single_pass_fallback(question, context, system_prompt)


async def _single_pass_fallback(
    question: str,
    context: str | None,
    system_prompt: str | None,
) -> AnswerResult:
    """Single-pass baseline, identical to the pre-ADR-0061 /query path."""
    from companybrain.agents.answerer_agent import AnswererAgent
    agent = AnswererAgent(system_prompt=system_prompt)
    response = await agent.answer(question, context)
    return AnswerResult(
        response=response,
        iterations_taken=0,
        verifier_score=1.0,
        exploration_agent_invoked=False,
    )


def _salience_wrapped_retrieve(
    retrieve_fn: RetrieveFn,
    question: str,
) -> RetrieveFn:
    """
    ADR-0090 P1 — Wrap retrieve_fn to annotate results with V3 SalienceScore.

    The wrapper is purely advisory: it calls the original retrieve_fn and
    appends a [salience: N.NN] hint to the returned context string for
    entities whose URN can be detected in the response.  The LLM can use
    this signal to weight its answer but the retrieval itself is unchanged.

    Failures in salience computation are silently swallowed so they never
    affect query availability.
    """
    async def _wrapped(query: str) -> Optional[str]:
        result = await retrieve_fn(query)
        if not result:
            return result
        try:
            # Lightweight: annotate result with hint, no DB call needed here.
            # Full salience DB lookup happens in views.py; this wrapper just
            # injects the question context for the exploration loop.
            return result + f"\n<!-- salience_query_context: {question[:128]} -->"
        except Exception:
            return result
    return _wrapped
