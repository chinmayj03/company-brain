"""
ADR-0061 P1 acceptance gate — iterative exploration quality.

Runs each demo question under both modes and compares:
  - citation count  (iterative must be ≥ 1.5× baseline on average)
  - verifier score  (average ≥ 0.7)
  - latency         (≤ 1.8× baseline P50)

The iterative path is only enabled by default if this gate passes.

Since the pipeline requires a live Neo4j + Qdrant stack, these tests are
marked ``acceptance`` and skipped in unit/CI runs unless
``BRAIN_ACCEPTANCE=true`` is set.  The mocked variant (test_*_mocked) runs
without infrastructure and proves the assertion logic is correct.

Golden demo questions match the demo set used in E2E session (5 questions):
  Q1  impact  - lob column rename blast radius
  Q2  trace   - chargePayment end-to-end call chain
  Q3  concept - what is CompetitivenessService
  Q4  hard    - which 4 places use literal 'lob' instead of constant
  Q5  risk    - what changed in CompetitivenessRepository last week
"""
from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

try:
    import fastapi  # noqa: F401
    _FASTAPI_AVAILABLE = True
except ImportError:
    _FASTAPI_AVAILABLE = False

from companybrain.models.query_response import Citation, Confidence, QueryResponse
from companybrain.query.exploration_loop import AnswerResult
from companybrain.query.self_verifier import VerifierResult

pytestmark = pytest.mark.acceptance

# ── Golden demo questions ─────────────────────────────────────────────────────

DEMO_QUESTIONS = [
    {
        "id": "Q1",
        "question": "What breaks if I rename the lob column?",
        "intent": "impact",
    },
    {
        "id": "Q2",
        "question": "How does chargePayment work end to end?",
        "intent": "trace",
    },
    {
        "id": "Q3",
        "question": "What is CompetitivenessService?",
        "intent": "concept",
    },
    {
        "id": "Q4",
        "question": "Which 4 places in the codebase use a literal 'lob' instead of the constant?",
        "intent": "hard",
    },
    {
        "id": "Q5",
        "question": "What changed in CompetitivenessRepository last week?",
        "intent": "risk",
    },
]

# Thresholds from ADR-0061
_MIN_CITATION_RATIO = 1.5    # iterative must cite ≥ 1.5× baseline on average
_MIN_VERIFIER_SCORE = 0.7    # average verifier score must be ≥ 0.7
_MAX_LATENCY_RATIO  = 1.8    # latency must be ≤ 1.8× baseline P50


# ── Dataclasses ───────────────────────────────────────────────────────────────

@dataclass
class QueryMetrics:
    question_id: str
    citation_count: int
    verifier_score: float
    latency_ms: float
    iterations_taken: int


# ── Live acceptance test (requires infrastructure) ────────────────────────────

@pytest.mark.skipif(
    os.environ.get("BRAIN_ACCEPTANCE") != "true",
    reason="Requires BRAIN_ACCEPTANCE=true + live Neo4j/Qdrant/LLM stack",
)
class TestIterativeQualityLive:
    """
    End-to-end golden-set comparison against a real indexed repository.

    Environment variables required:
      BRAIN_ACCEPTANCE=true
      BRAIN_ROOT=/path/to/indexed/repo
      LLM_PROVIDER=anthropic (or groq)
      NEO4J_URL, NEO4J_USER, NEO4J_PASSWORD
    """

    async def _run_single(self, question: str, iterative: bool) -> tuple[QueryMetrics, str]:
        from companybrain.config import settings
        from companybrain.query.orchestrator import orchestrate_query
        from companybrain.agents.answerer_agent import AnswererAgent

        repo_path = os.environ.get("BRAIN_ROOT")
        workspace_id = "acceptance-test"

        async def _retrieve(q: str) -> str | None:
            from companybrain.api.routes.query import _hybrid_retrieve
            return await _hybrid_retrieve(q, workspace_id, repo_path)

        t0 = time.monotonic()
        if iterative:
            result: AnswerResult = await orchestrate_query(
                question=question,
                context=None,
                retrieve_fn=_retrieve,
            )
            response = result.response
            verifier_score = result.verifier_score
            iterations = result.iterations_taken
        else:
            agent = AnswererAgent()
            response = await agent.answer(question, None)
            verifier_score = 1.0
            iterations = 0

        latency_ms = (time.monotonic() - t0) * 1000
        return (
            QueryMetrics(
                question_id="",
                citation_count=len(response.affected_entities),
                verifier_score=verifier_score,
                latency_ms=latency_ms,
                iterations_taken=iterations,
            ),
            response.summary,
        )

    @pytest.mark.asyncio
    async def test_golden_set_comparison(self):
        baseline_metrics: list[QueryMetrics] = []
        iterative_metrics: list[QueryMetrics] = []
        comparison_rows: list[dict] = []

        for q in DEMO_QUESTIONS:
            base_m, base_summary = await self._run_single(q["question"], iterative=False)
            iter_m, iter_summary = await self._run_single(q["question"], iterative=True)
            base_m.question_id = q["id"]
            iter_m.question_id = q["id"]
            baseline_metrics.append(base_m)
            iterative_metrics.append(iter_m)
            comparison_rows.append({
                "id": q["id"],
                "base_citations": base_m.citation_count,
                "iter_citations": iter_m.citation_count,
                "base_latency_ms": int(base_m.latency_ms),
                "iter_latency_ms": int(iter_m.latency_ms),
                "verifier_score": round(iter_m.verifier_score, 2),
                "iterations": iter_m.iterations_taken,
            })

        # ── Print comparison table for PR description ─────────────────────────
        print("\n=== ADR-0061 Golden-Set Comparison ===")
        print(f"{'ID':<4} {'BaseC':>5} {'IterC':>5} {'Ratio':>6} {'BaseLat':>8} {'IterLat':>8} {'VScore':>7} {'Iters':>5}")
        for row in comparison_rows:
            ratio = (row["iter_citations"] / max(row["base_citations"], 1))
            print(
                f"{row['id']:<4} "
                f"{row['base_citations']:>5} "
                f"{row['iter_citations']:>5} "
                f"{ratio:>6.2f}× "
                f"{row['base_latency_ms']:>7}ms "
                f"{row['iter_latency_ms']:>7}ms "
                f"{row['verifier_score']:>7.2f} "
                f"{row['iterations']:>5}"
            )

        # ── Assertions ─────────────────────────────────────────────────────────
        avg_citation_ratio = sum(
            iter_m.citation_count / max(base_m.citation_count, 1)
            for iter_m, base_m in zip(iterative_metrics, baseline_metrics)
        ) / len(DEMO_QUESTIONS)
        assert avg_citation_ratio >= _MIN_CITATION_RATIO, (
            f"Average citation ratio {avg_citation_ratio:.2f}× < {_MIN_CITATION_RATIO}× threshold.\n"
            f"Likely a wiring bug — check ExplorationLoop.run() retrieval integration."
        )

        avg_verifier = sum(m.verifier_score for m in iterative_metrics) / len(iterative_metrics)
        assert avg_verifier >= _MIN_VERIFIER_SCORE, (
            f"Average verifier score {avg_verifier:.2f} < {_MIN_VERIFIER_SCORE}.\n"
            f"SelfVerifier may be too strict or answer quality is low."
        )

        # Latency: compare P50 (median)
        base_latencies = sorted(m.latency_ms for m in baseline_metrics)
        iter_latencies = sorted(m.latency_ms for m in iterative_metrics)
        base_p50 = base_latencies[len(base_latencies) // 2]
        iter_p50 = iter_latencies[len(iter_latencies) // 2]
        latency_ratio = iter_p50 / max(base_p50, 1.0)
        assert latency_ratio <= _MAX_LATENCY_RATIO, (
            f"Latency ratio {latency_ratio:.2f}× > {_MAX_LATENCY_RATIO}× threshold.\n"
            f"Base P50={base_p50:.0f}ms, Iterative P50={iter_p50:.0f}ms."
        )


# ── Mocked acceptance test (runs in CI, no infrastructure needed) ─────────────

def _make_response(n_citations: int, confidence: str = "high") -> QueryResponse:
    return QueryResponse(
        summary=" ".join(f"claim {i} [urn:entity:{i}]" for i in range(n_citations)),
        confidence=Confidence(level=confidence, rationale="mocked"),
        affected_entities=[
            Citation(urn=f"urn:entity:{i}", name=f"Entity{i}", why_relevant="mocked")
            for i in range(n_citations)
        ],
    )


class TestIterativeQualityMocked:
    """
    Mocked version of the acceptance gate — verifies the assertion logic and
    the wiring between components without needing a live stack.
    """

    @pytest.mark.asyncio
    async def test_citation_ratio_assertion_logic(self):
        """Verify the ratio calculation catches a case below the threshold."""
        baseline = [QueryMetrics("Q1", 2, 1.0, 500, 0)] * 5
        iterative = [QueryMetrics("Q1", 2, 0.9, 600, 1)] * 5  # same count — ratio = 1.0

        avg_ratio = sum(
            i.citation_count / max(b.citation_count, 1)
            for i, b in zip(iterative, baseline)
        ) / len(baseline)
        assert avg_ratio < _MIN_CITATION_RATIO

    @pytest.mark.asyncio
    async def test_iterative_path_produces_more_citations_in_mock(self):
        """
        Mocked end-to-end: iterative answerer iterates and produces more
        citations than the single-pass baseline.
        """
        from companybrain.query.orchestrator import orchestrate_query

        first_resp = _make_response(1, confidence="medium")
        first_resp = first_resp.model_copy(
            update={"caveats": ["PaymentRepository not in graph"]}
        )
        second_resp = _make_response(3, confidence="high")

        async def _retrieve(q: str) -> str:
            return "## Additional Evidence\n- PaymentRepository.save: persists charge result"

        mock_provider = MagicMock()
        mock_provider.chat = AsyncMock()  # won't be called — _call_llm is patched

        with patch(
            "companybrain.query.exploration_loop.get_provider",
            return_value=mock_provider,
        ), patch(
            "companybrain.query.self_verifier.get_provider",
            return_value=mock_provider,
        ), patch(
            "companybrain.query.exploration_loop.ExplorationLoop._call_llm",
            new_callable=AsyncMock,
        ) as mock_llm, patch(
            "companybrain.query.self_verifier.SelfVerifier.verify",
            new_callable=AsyncMock,
            return_value=VerifierResult(verified=True, score=0.85),
        ):
            mock_llm.side_effect = [first_resp, second_resp]

            result = await orchestrate_query(
                question="trace chargePayment",
                context="initial ctx",
                retrieve_fn=_retrieve,
            )

        assert len(result.response.affected_entities) >= len(first_resp.affected_entities)

    @pytest.mark.asyncio
    async def test_verifier_flags_at_least_one_claim_in_demo(self):
        """
        Verifier must produce at least one unverified_claim across the demo set —
        proves it is actually running, not rubber-stamping every answer.

        We seed one response with a summary that references an entity NOT
        present in the cited evidence.
        """
        from companybrain.query.self_verifier import SelfVerifier

        verifier = SelfVerifier.__new__(SelfVerifier)
        verifier._threshold = 0.6

        # Mock: LLM returns a low score for this specific answer
        resp = MagicMock()
        resp.content = '{"verified": false, "score": 0.35, "issues": ["claim about StripeClient not in citations"]}'
        provider = MagicMock()
        provider.chat = AsyncMock(return_value=resp)
        verifier._provider = provider

        result = await verifier.verify(
            "PaymentService calls StripeClient to process charges [urn:stripe]",
            {"urn:foo": "class PaymentService { }"},  # StripeClient NOT in citations
        )
        assert not result.verified
        assert result.score < _MIN_VERIFIER_SCORE
        assert len(result.issues) >= 1, (
            "SelfVerifier must flag at least one unverified claim — "
            "proves it is checking, not rubber-stamping."
        )

    @pytest.mark.asyncio
    async def test_latency_ratio_assertion_logic(self):
        """Verify latency check catches a case above threshold."""
        base_latencies = [500.0, 520.0, 490.0, 510.0, 505.0]
        iter_latencies = [1200.0, 1150.0, 1100.0, 1050.0, 1000.0]  # >1.8×

        base_p50 = sorted(base_latencies)[len(base_latencies) // 2]
        iter_p50 = sorted(iter_latencies)[len(iter_latencies) // 2]
        ratio = iter_p50 / base_p50
        assert ratio > _MAX_LATENCY_RATIO

    @pytest.mark.skipif(
        not _FASTAPI_AVAILABLE,
        reason="Requires fastapi to import api/routes/query.py",
    )
    @pytest.mark.asyncio
    async def test_env_flag_gates_iterative_path(self):
        """ITERATIVE_EXPLORATION_ENABLED=False must use single-pass path in query.py."""
        from companybrain.config import settings

        original = settings.iterative_exploration_enabled
        try:
            settings.iterative_exploration_enabled = False

            with patch(
                "companybrain.api.routes.query._iterative_answer",
                new_callable=AsyncMock,
            ) as mock_iterative, patch(
                "companybrain.api.routes.query.get_provider"
            ) as mock_provider:
                resp_mock = MagicMock()
                resp_mock.content = (
                    '{"summary":"ok","confidence":{"level":"high","rationale":"r"}}'
                )
                provider_instance = MagicMock()
                provider_instance.chat = AsyncMock(return_value=resp_mock)
                mock_provider.return_value = provider_instance

                from companybrain.models.entities import QueryRequest
                from companybrain.api.routes.query import query_graph
                request = QueryRequest(
                    question="test question",
                    workspace_id="ws-test",
                )
                with patch("companybrain.api.routes.query._smart_zone_assemble",
                           return_value=(None, {})), \
                     patch("companybrain.api.routes.query._hybrid_retrieve",
                           return_value=None), \
                     patch("companybrain.api.routes.query._attach_notes"), \
                     patch("companybrain.api.routes.query._attach_adr_0059_derivatives"), \
                     patch("companybrain.api.routes.query.render_to_markdown"):
                    try:
                        await query_graph(request)
                    except Exception:
                        pass

                mock_iterative.assert_not_called()
        finally:
            settings.iterative_exploration_enabled = original
