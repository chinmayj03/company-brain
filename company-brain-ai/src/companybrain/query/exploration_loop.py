"""
ExplorationLoop — ADR-0061 P1 / M1 + M2.

Wraps a single /query question in an iterative explore-then-answer cycle:

  1. Initial retrieval (caller-supplied context)
  2. Call answerer LLM — get a QueryResponse + uncertainty signals
     (confidence level, caveats, empty call_chain)
  3. For each uncertainty (max MAX_ITERATIONS):
       a. Derive a focused search query from the caveat/uncertainty text
       b. Call the injected retrieval function to pull additional evidence
       c. Re-call the answerer LLM with augmented context
  4. Run SelfVerifier on the final answer
  5. If verifier score < threshold AND revision allowed:
       - Feed issues back to the answerer for one revision pass
  6. If still < threshold: set unverified_claims in result
  7. Return AnswerResult

Hard caps (all workspace-configurable via Settings):
  MAX_ITERATIONS     = 3    (additional retrieval calls)
  MAX_EXTRA_RETRIEVE = 5    (total extra retrieval round-trips)
  MAX_TOKENS_PER_QUERY = 12_000

Cost profile: ~2-4 QUERY-role calls + 1 FAST-role verifier call per hard query.
"""
from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional

import structlog

from companybrain.llm import TaskRole, get_provider
from companybrain.llm.base import ChatMessage
from companybrain.models.query_response import Confidence, QueryResponse
from companybrain.query.followup_generator import FollowupGenerator
from companybrain.query.self_verifier import SelfVerifier, VerifierResult

log = structlog.get_logger(__name__)

# Injected at import time; callers may pass their own via constructor args.
RetrieveFn = Callable[[str], Awaitable[Optional[str]]]


@dataclass
class AnswerResult:
    """Enriched answer returned by ExplorationLoop and IterativeAnswerer."""
    response: QueryResponse
    iterations_taken: int = 0
    verifier_score: float = 1.0
    suggested_followups: list[str] = field(default_factory=list)
    unverified_claims: list[str] = field(default_factory=list)
    exploration_agent_invoked: bool = False


class ExplorationLoop:
    """
    Iterative explore-then-answer loop for a single query.

    Parameters
    ----------
    retrieve_fn
        Async callable ``(query_text) → context_str | None`` used to pull
        additional evidence during iteration. Typically wraps
        ``_hybrid_retrieve`` or ``_smart_zone_assemble`` from the route layer.
    system_prompt
        The LLM system prompt used for answering.  Defaults to the standard
        query system prompt.
    max_iterations
        Maximum additional retrieval+answer cycles (default 3).
    max_extra_retrieve
        Hard cap on total extra retrieval calls across all iterations.
    max_tokens
        Per-LLM-call token budget.
    verifier_score_threshold
        Minimum verifier score to consider the answer verified.
    """

    def __init__(
        self,
        retrieve_fn: RetrieveFn,
        system_prompt: str | None = None,
        max_iterations: int = 3,
        max_extra_retrieve: int = 5,
        max_tokens: int = 4_096,
        verifier_score_threshold: float = 0.6,
    ) -> None:
        self._retrieve = retrieve_fn
        self._max_iter = max_iterations
        self._max_retrieve = max_extra_retrieve
        self._max_tokens = max_tokens
        self._threshold = verifier_score_threshold
        self._provider = get_provider()
        self._verifier = SelfVerifier(score_threshold=verifier_score_threshold)

        if system_prompt is None:
            from companybrain.api.prompts.query_system import QUERY_SYSTEM_PROMPT
            system_prompt = QUERY_SYSTEM_PROMPT
        self._system = system_prompt

    # ── Public entry ──────────────────────────────────────────────────────────

    async def run(
        self,
        question: str,
        initial_context: str | None,
        persona: str = "dev",
    ) -> AnswerResult:
        """
        Run the full explore-then-answer cycle and return an AnswerResult.
        """
        t0 = time.monotonic()
        context = initial_context or ""
        retrieve_budget = self._max_retrieve
        exploration_invoked = False

        # ── Pass 1: initial answer ─────────────────────────────────────────
        response = await self._call_llm(question, context)
        log.info(
            "[exploration_loop] initial answer",
            confidence=response.confidence.level,
            citations=len(response.affected_entities),
            caveats=len(response.caveats),
        )

        # ── Iteration: resolve uncertainties ──────────────────────────────
        iterations = 0
        uncertainties = self._extract_uncertainties(response)

        while (
            uncertainties
            and iterations < self._max_iter
            and retrieve_budget > 0
            and response.confidence.level != "high"
        ):
            exploration_invoked = True
            iterations += 1
            uncertainty = uncertainties[0]  # tackle one per round
            search_query = self._derive_search_query(question, uncertainty)

            log.debug(
                "[exploration_loop] iteration",
                iter=iterations,
                uncertainty=uncertainty[:80],
                search_query=search_query[:80],
            )

            extra = await self._retrieve(search_query)
            retrieve_budget -= 1

            if extra:
                context = self._merge_context(context, extra)
                response = await self._call_llm(question, context)
                uncertainties = self._extract_uncertainties(response)
                log.debug(
                    "[exploration_loop] after iteration",
                    confidence=response.confidence.level,
                    new_citations=len(response.affected_entities),
                )
            else:
                # retrieval returned nothing — skip this uncertainty
                uncertainties = uncertainties[1:]

        # ── Self-verification ──────────────────────────────────────────────
        entity_contents = self._build_entity_contents(response)
        verifier_result = await self._verifier.verify(response.summary, entity_contents)

        # ── Revision pass if score < threshold ────────────────────────────
        if not verifier_result.verified and verifier_result.issues:
            log.info(
                "[exploration_loop] verifier triggered revision",
                score=verifier_result.score,
                issues=verifier_result.issues,
            )
            response = await self._call_llm(
                question, context,
                revision_hint=self._format_revision_hint(verifier_result),
            )
            entity_contents = self._build_entity_contents(response)
            verifier_result = await self._verifier.verify(response.summary, entity_contents)

        # ── Followup questions when still low confidence ───────────────────
        suggested_followups: list[str] = []
        unverified_claims: list[str] = []

        if not verifier_result.verified and verifier_result.issues:
            gen = FollowupGenerator()
            suggested_followups = await gen.generate(
                question, verifier_result.issues, persona=persona
            )
            unverified_claims = verifier_result.issues
            # Surface gaps in the response caveats
            extra_caveats = [f"[unverified] {i}" for i in verifier_result.issues]
            response = response.model_copy(
                update={"caveats": response.caveats + extra_caveats}
            )

        # Merge generated followups into the response field so it reaches the UI
        if suggested_followups:
            merged_followups = list(dict.fromkeys(
                response.follow_up_questions + suggested_followups
            ))[:5]
            response = response.model_copy(update={"follow_up_questions": merged_followups})

        dur_ms = int((time.monotonic() - t0) * 1000)
        log.info(
            "[exploration_loop] done",
            iterations=iterations,
            verifier_score=verifier_result.score,
            total_ms=dur_ms,
            exploration_invoked=exploration_invoked,
        )

        return AnswerResult(
            response=response,
            iterations_taken=iterations,
            verifier_score=verifier_result.score,
            suggested_followups=suggested_followups,
            unverified_claims=unverified_claims,
            exploration_agent_invoked=exploration_invoked,
        )

    # ── Private helpers ───────────────────────────────────────────────────────

    async def _call_llm(
        self,
        question: str,
        context: str,
        revision_hint: str | None = None,
    ) -> QueryResponse:
        """One LLM call → QueryResponse.  Revision hint appended to user message."""
        try:
            from companybrain.api.prompts.user_message import build_user_message
            user_content = build_user_message(question, intent="concept", context=context or None)
        except Exception:
            user_content = _plain_user_message(question, context or None)

        if revision_hint:
            user_content = user_content + f"\n\n{revision_hint}"

        resp = await self._provider.chat(
            messages=[
                ChatMessage(role="system", content=self._system),
                ChatMessage(role="user", content=user_content),
            ],
            role=TaskRole.QUERY,
            max_tokens=self._max_tokens,
        )
        from companybrain.agents.answerer_agent import _parse_response
        return _parse_response(resp.content, context)

    def _extract_uncertainties(self, response: QueryResponse) -> list[str]:
        """Extract uncertainty signals from a QueryResponse."""
        items: list[str] = []
        # Caveats are the clearest uncertainty signal
        items.extend(response.caveats)
        # Low/medium confidence with a rationale also signals uncertainty
        if response.confidence.level in ("low", "medium"):
            items.append(response.confidence.rationale)
        return items

    @staticmethod
    def _derive_search_query(original_question: str, uncertainty: str) -> str:
        """
        Derive a focused retrieval query from an uncertainty string.

        Heuristic: extract quoted identifiers, CamelCase names, or
        known keywords; fall back to the first 80 chars of the uncertainty.
        """
        # Extract quoted strings
        quoted = re.findall(r'["`\']([\w.]+)["`\']', uncertainty)
        if quoted:
            return " ".join(quoted[:3])[:80]
        # Extract CamelCase / snake_case identifiers (≥4 chars, ≤40 chars each)
        identifiers = re.findall(r'\b[A-Z][a-zA-Z0-9]{3,39}\b|\b[a-z][a-z0-9_]{3,39}\b', uncertainty)
        if identifiers:
            return " ".join(identifiers[:4])[:80]
        # Fallback: use most specific clause of the original question
        return uncertainty[:80].strip() or original_question[:80]

    @staticmethod
    def _merge_context(existing: str, extra: str) -> str:
        """Append extra context with a clear separator."""
        if not existing:
            return extra
        separator = "\n\n## Additional Evidence\n\n"
        # Avoid appending duplicate blocks
        if extra in existing:
            return existing
        return existing + separator + extra

    @staticmethod
    def _build_entity_contents(response: QueryResponse) -> dict[str, str]:
        """
        Build a urn→content map from the response's affected_entities.

        Since we don't have disk access here, we use the `why_relevant` field
        as a proxy for the entity's content — it's filled by the LLM from the
        actual snippet in the knowledge base context.
        """
        return {
            c.urn: f"{c.name}: {c.why_relevant}"
            for c in response.affected_entities
            if c.urn
        }

    @staticmethod
    def _format_revision_hint(result: VerifierResult) -> str:
        issues_text = "\n".join(f"  - {i}" for i in result.issues)
        return (
            "SELF-VERIFICATION FAILED — please revise your answer.\n"
            f"Score: {result.score:.2f}\n"
            f"Issues found:\n{issues_text}\n\n"
            "Fix only the flagged claims. Keep all correctly cited claims unchanged."
        )


def _plain_user_message(question: str, context: str | None) -> str:
    if context:
        return f"KNOWLEDGE BASE:\n\n{context}\n\n---\n\nQUESTION: {question}"
    return (
        f"QUESTION: {question}\n\n"
        "Note: No brain context available. Run the extraction pipeline first."
    )
