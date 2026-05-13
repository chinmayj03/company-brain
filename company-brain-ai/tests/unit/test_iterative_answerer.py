"""Unit tests for IterativeAnswerer and AnswererAgent (ADR-0061 P1)."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from companybrain.agents.answerer_agent import AnswererAgent
from companybrain.agents.iterative_answerer import IterativeAnswerer
from companybrain.models.query_response import Citation, Confidence, QueryResponse
from companybrain.query.exploration_loop import AnswerResult
from companybrain.query.self_verifier import VerifierResult


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_response(confidence: str = "high") -> QueryResponse:
    return QueryResponse(
        summary="FooService handles requests [urn:foo]",
        confidence=Confidence(level=confidence, rationale="test"),
        affected_entities=[Citation(urn="urn:foo", name="Foo", why_relevant="entry")],
    )


# ── AnswererAgent ─────────────────────────────────────────────────────────────

class TestAnswererAgent:
    @pytest.mark.asyncio
    async def test_returns_query_response(self):
        # AnswererAgent is now fastapi-free; provide valid JSON so _parse_response
        # can construct a real QueryResponse without any patches.
        valid_json = (
            '{"summary":"FooService handles requests [urn:foo]",'
            '"confidence":{"level":"high","rationale":"r"},'
            '"affected_entities":[{"urn":"urn:foo","name":"Foo",'
            '"why_relevant":"entry","confidence":0.9}]}'
        )
        resp_mock = MagicMock()
        resp_mock.content = valid_json
        provider = MagicMock()
        provider.chat = AsyncMock(return_value=resp_mock)

        agent = AnswererAgent.__new__(AnswererAgent)
        agent._provider = provider
        agent._system = "sys"
        result = await agent.answer("what is Foo?", "some context")

        assert isinstance(result, QueryResponse)
        assert result.confidence.level == "high"

    @pytest.mark.asyncio
    async def test_handles_missing_context(self):
        # Free-form response when context is None → low confidence envelope
        resp_mock = MagicMock()
        resp_mock.content = "I don't know"
        provider = MagicMock()
        provider.chat = AsyncMock(return_value=resp_mock)

        agent = AnswererAgent.__new__(AnswererAgent)
        agent._provider = provider
        agent._system = "sys"
        result = await agent.answer("q", None)

        assert isinstance(result, QueryResponse)
        assert result.confidence.level == "low"

    def test_parse_response_valid_json(self):
        from companybrain.agents.answerer_agent import _parse_response
        raw = '{"summary":"s","confidence":{"level":"high","rationale":"r"}}'
        result = _parse_response(raw, None)
        assert result.summary == "s"

    def test_parse_response_freeform_wraps(self):
        from companybrain.agents.answerer_agent import _parse_response
        result = _parse_response("plain text answer", "ctx")
        assert result.summary == "plain text answer"
        assert result.confidence.level == "medium"


# ── IterativeAnswerer ─────────────────────────────────────────────────────────

class TestIterativeAnswerer:
    @pytest.mark.asyncio
    async def test_delegates_to_exploration_loop(self):
        retrieve_fn = AsyncMock(return_value=None)
        expected = AnswerResult(
            response=_make_response(),
            iterations_taken=2,
            verifier_score=0.88,
            exploration_agent_invoked=True,
        )

        with patch(
            "companybrain.agents.iterative_answerer.ExplorationLoop"
        ) as MockLoop:
            instance = MagicMock()
            instance.run = AsyncMock(return_value=expected)
            MockLoop.return_value = instance

            answerer = IterativeAnswerer(retrieve_fn=retrieve_fn)
            result = await answerer.answer("trace chargePayment", "ctx")

        assert isinstance(result, AnswerResult)
        assert result.iterations_taken == 2
        assert result.verifier_score == pytest.approx(0.88)
        assert result.exploration_agent_invoked is True

    @pytest.mark.asyncio
    async def test_persona_passed_to_loop(self):
        retrieve_fn = AsyncMock(return_value=None)
        expected = AnswerResult(response=_make_response(), verifier_score=0.9)

        with patch(
            "companybrain.agents.iterative_answerer.ExplorationLoop"
        ) as MockLoop:
            instance = MagicMock()
            instance.run = AsyncMock(return_value=expected)
            MockLoop.return_value = instance

            answerer = IterativeAnswerer(retrieve_fn=retrieve_fn, persona="pm")
            await answerer.answer("q", "ctx")

            # verify run was called with persona="pm"
            instance.run.assert_awaited_once_with("q", "ctx", persona="pm")

    @pytest.mark.asyncio
    async def test_result_includes_response_fields(self):
        retrieve_fn = AsyncMock(return_value=None)
        resp = _make_response()
        expected = AnswerResult(
            response=resp,
            unverified_claims=["claim A not cited"],
            suggested_followups=["What does FooService call?"],
        )

        with patch(
            "companybrain.agents.iterative_answerer.ExplorationLoop"
        ) as MockLoop:
            instance = MagicMock()
            instance.run = AsyncMock(return_value=expected)
            MockLoop.return_value = instance

            answerer = IterativeAnswerer(retrieve_fn=retrieve_fn)
            result = await answerer.answer("q", "ctx")

        assert result.unverified_claims == ["claim A not cited"]
        assert "What does FooService call?" in result.suggested_followups


# ── orchestrate_query ─────────────────────────────────────────────────────────

class TestOrchestrateQuery:
    @pytest.mark.asyncio
    async def test_delegates_to_exploration_loop(self):
        from companybrain.query.orchestrator import orchestrate_query

        expected_resp = _make_response()
        expected = AnswerResult(response=expected_resp, verifier_score=0.95)

        # ExplorationLoop is a top-level import in orchestrator
        with patch(
            "companybrain.query.orchestrator.ExplorationLoop"
        ) as MockLoop:
            instance = MagicMock()
            instance.run = AsyncMock(return_value=expected)
            MockLoop.return_value = instance

            retrieve_fn = AsyncMock(return_value=None)
            result = await orchestrate_query("q", "ctx", retrieve_fn=retrieve_fn)

        assert result.response is expected_resp

    @pytest.mark.asyncio
    async def test_falls_back_on_loop_exception(self):
        from companybrain.query.orchestrator import orchestrate_query

        fallback_resp = _make_response()
        fallback_result = AnswerResult(response=fallback_resp, iterations_taken=0)

        with patch(
            "companybrain.query.orchestrator.ExplorationLoop",
            side_effect=RuntimeError("loop crashed"),
        ), patch(
            "companybrain.query.orchestrator._single_pass_fallback",
            new_callable=AsyncMock,
            return_value=fallback_result,
        ):
            retrieve_fn = AsyncMock(return_value=None)
            result = await orchestrate_query("q", "ctx", retrieve_fn=retrieve_fn)

        assert result.response is fallback_resp
        assert result.iterations_taken == 0
