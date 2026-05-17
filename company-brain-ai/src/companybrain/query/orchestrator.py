"""
orchestrate_query — ADR-0061 P1 top-level dispatcher.

Called from api/routes/query.py when ITERATIVE_EXPLORATION_ENABLED is True.
Routes to IterativeAnswerer; falls back to single-pass AnswererAgent on error.

The env flag stays False until the acceptance gate in
tests/acceptance/test_iterative_quality.py passes (see ADR-0061 §acceptance).

A1.4: After the answer is produced the multi-signal aggregator computes a
deterministic confidence score that replaces the LLM-hallucinated stub.
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
    # A1.4: optional retrieval metadata for the confidence aggregator
    retrieval_score: float = 0.0,
    source_paths: list[str] | None = None,
) -> AnswerResult:
    """
    Run an iterative exploration pass and return an AnswerResult.

    Falls back to single-pass AnswererAgent if ExplorationLoop raises,
    so a wiring bug never kills the /query endpoint.

    A1.4: ``retrieval_score`` and ``source_paths`` are forwarded to the
    multi-signal confidence aggregator after the answer is produced.
    """
    from companybrain.config import settings

    try:
        loop = ExplorationLoop(
            retrieve_fn=retrieve_fn,
            system_prompt=system_prompt,
            max_iterations=settings.iterative_max_iterations,
            max_extra_retrieve=settings.iterative_max_extra_retrieve,
            max_tokens=settings.iterative_max_tokens_per_query,
            verifier_score_threshold=settings.iterative_verifier_score_threshold,
        )
        result = await loop.run(question, context, persona=persona)
    except Exception as exc:
        log.warning(
            "[orchestrator] ExplorationLoop failed — falling back to single-pass",
            error=str(exc),
        )
        result = await _single_pass_fallback(question, context, system_prompt)

    # ── A1.4: attach deterministic multi-signal confidence ────────────────
    result = _apply_aggregated_confidence(
        result,
        retrieval_score=retrieval_score,
        source_paths=source_paths,
    )
    return result


def _apply_aggregated_confidence(
    result: AnswerResult,
    *,
    retrieval_score: float,
    source_paths: list[str] | None,
) -> AnswerResult:
    """
    Replace the LLM-produced confidence stub with a deterministic score.

    Non-fatal: if the confidence module is unavailable the original result
    is returned unchanged so /query always has a response.
    """
    try:
        from companybrain.confidence.helpers import build_confidence_from_query_result
        new_confidence = build_confidence_from_query_result(
            result.response,
            retrieval_score=retrieval_score,
            source_paths=source_paths,
            verifier_score=result.verifier_score,
        )
        result.response = result.response.model_copy(
            update={"confidence": new_confidence}
        )
        log.debug(
            "[orchestrator] confidence aggregated",
            value=new_confidence.value,
            label=new_confidence.level,
        )
    except Exception as exc:
        log.warning(
            "[orchestrator] confidence aggregation failed (non-fatal)",
            error=str(exc),
        )
    return result


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
