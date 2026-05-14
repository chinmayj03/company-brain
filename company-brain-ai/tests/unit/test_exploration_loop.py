"""Unit tests for ExplorationLoop (ADR-0061 P1 / M1 + M2)."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from companybrain.models.query_response import Citation, Confidence, QueryResponse
from companybrain.query.exploration_loop import (
    AnswerResult,
    ExplorationLoop,
    _plain_user_message,
)
from companybrain.query.self_verifier import VerifierResult


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_response(
    summary: str = "FooService calls BarRepo [urn:foo]",
    confidence_level: str = "high",
    caveats: list[str] | None = None,
    affected: list[Citation] | None = None,
) -> QueryResponse:
    return QueryResponse(
        summary=summary,
        confidence=Confidence(level=confidence_level, rationale="test"),
        caveats=caveats or [],
        affected_entities=affected or [Citation(urn="urn:foo", name="Foo", why_relevant="entry")],
        follow_up_questions=[],
    )


def _make_loop(
    retrieve_returns: str | None = "extra context",
    response: QueryResponse | None = None,
    verifier_result: VerifierResult | None = None,
) -> ExplorationLoop:
    """Build an ExplorationLoop with mocked LLM + retrieval."""
    retrieve_fn = AsyncMock(return_value=retrieve_returns)
    resp = response or _make_response()

    loop = ExplorationLoop.__new__(ExplorationLoop)
    loop._retrieve = retrieve_fn
    loop._max_iter = 3
    loop._max_retrieve = 5
    loop._max_tokens = 4096
    loop._threshold = 0.6
    loop._provider = MagicMock()
    loop._verifier = MagicMock()
    loop._verifier.verify = AsyncMock(
        return_value=verifier_result or VerifierResult(verified=True, score=0.9)
    )
    # _call_llm is the integration point — patch it directly
    loop._call_llm = AsyncMock(return_value=resp)

    from companybrain.api.prompts.query_system import QUERY_SYSTEM_PROMPT
    loop._system = QUERY_SYSTEM_PROMPT

    return loop


# ── _derive_search_query ──────────────────────────────────────────────────────

class TestDeriveSearchQuery:
    def test_extracts_quoted_identifier(self):
        q = ExplorationLoop._derive_search_query("rename lob", "the `lob` column is not in the graph")
        assert "lob" in q

    def test_extracts_camelcase(self):
        q = ExplorationLoop._derive_search_query("what does FooService do", "FooService implementation not found")
        assert "FooService" in q

    def test_falls_back_to_uncertainty_text(self):
        q = ExplorationLoop._derive_search_query("q", "simple plain text here")
        assert len(q) > 0

    def test_result_is_under_100_chars(self):
        uncertainty = "x" * 200
        q = ExplorationLoop._derive_search_query("q", uncertainty)
        assert len(q) <= 100


# ── _merge_context ────────────────────────────────────────────────────────────

class TestMergeContext:
    def test_empty_existing_returns_extra(self):
        out = ExplorationLoop._merge_context("", "new stuff")
        assert out == "new stuff"

    def test_appends_with_separator(self):
        out = ExplorationLoop._merge_context("first", "second")
        assert "first" in out
        assert "second" in out
        assert "Additional Evidence" in out

    def test_no_duplicate_append(self):
        ctx = "already there"
        out = ExplorationLoop._merge_context(ctx, "already there")
        assert out.count("already there") == 1


# ── _extract_uncertainties ────────────────────────────────────────────────────

class TestExtractUncertainties:
    def test_high_confidence_no_caveats_returns_empty(self):
        response = _make_response(confidence_level="high", caveats=[])
        loop = _make_loop()
        assert loop._extract_uncertainties(response) == []

    def test_caveats_are_returned(self):
        response = _make_response(confidence_level="medium", caveats=["migration file missing"])
        loop = _make_loop()
        items = loop._extract_uncertainties(response)
        assert "migration file missing" in items

    def test_low_confidence_rationale_included(self):
        response = _make_response(confidence_level="low", caveats=[])
        response = response.model_copy(
            update={"confidence": Confidence(level="low", rationale="many gaps")}
        )
        loop = _make_loop()
        items = loop._extract_uncertainties(response)
        assert "many gaps" in items


# ── run() end-to-end ──────────────────────────────────────────────────────────

class TestExplorationLoopRun:
    @pytest.mark.asyncio
    async def test_high_confidence_skips_iteration(self):
        loop = _make_loop(response=_make_response(confidence_level="high"))
        result = await loop.run("what is FooService?", "initial context")
        assert result.iterations_taken == 0
        assert result.exploration_agent_invoked is False
        loop._call_llm.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_medium_confidence_triggers_iteration(self):
        low_resp = _make_response(
            confidence_level="medium",
            caveats=["PaymentRepository.save not in graph"],
        )
        high_resp = _make_response(confidence_level="high")
        loop = _make_loop()
        loop._call_llm = AsyncMock(side_effect=[low_resp, high_resp])

        result = await loop.run("trace chargePayment", "ctx")
        assert result.iterations_taken >= 1
        assert result.exploration_agent_invoked is True

    @pytest.mark.asyncio
    async def test_iteration_cap_respected(self):
        always_medium = _make_response(
            confidence_level="medium",
            caveats=["gap 1"],
        )
        loop = _make_loop(response=always_medium)
        loop._call_llm = AsyncMock(return_value=always_medium)

        result = await loop.run("q", "ctx")
        assert result.iterations_taken <= loop._max_iter

    @pytest.mark.asyncio
    async def test_verifier_failure_populates_unverified_claims(self):
        bad_verifier = VerifierResult(
            verified=False, score=0.4, issues=["claim X not in citations"]
        )
        loop = _make_loop(verifier_result=bad_verifier)
        # Two verifier calls: initial + after revision
        loop._verifier.verify = AsyncMock(return_value=bad_verifier)
        # followup generator mock
        with patch(
            "companybrain.query.exploration_loop.FollowupGenerator"
        ) as MockGen:
            mock_gen_instance = MagicMock()
            mock_gen_instance.generate = AsyncMock(return_value=["What is X?"])
            MockGen.return_value = mock_gen_instance

            result = await loop.run("q", "ctx")

        assert len(result.unverified_claims) >= 1
        assert any("[unverified]" in c for c in result.response.caveats)

    @pytest.mark.asyncio
    async def test_suggested_followups_merged_into_response(self):
        bad_verifier = VerifierResult(
            verified=False, score=0.3, issues=["gap"]
        )
        loop = _make_loop(verifier_result=bad_verifier)
        loop._verifier.verify = AsyncMock(return_value=bad_verifier)

        with patch(
            "companybrain.query.exploration_loop.FollowupGenerator"
        ) as MockGen:
            instance = MagicMock()
            instance.generate = AsyncMock(return_value=["Specific followup?"])
            MockGen.return_value = instance

            result = await loop.run("q", "ctx")

        assert "Specific followup?" in result.response.follow_up_questions
        assert "Specific followup?" in result.suggested_followups

    @pytest.mark.asyncio
    async def test_no_context_still_answers(self):
        loop = _make_loop()
        result = await loop.run("what is this?", None)
        assert isinstance(result, AnswerResult)
        assert result.response.summary


# ── _plain_user_message ───────────────────────────────────────────────────────

def test_plain_user_message_includes_context():
    msg = _plain_user_message("the question", "the context")
    assert "the question" in msg
    assert "the context" in msg


def test_plain_user_message_no_context_warns():
    msg = _plain_user_message("the question", None)
    assert "No brain context" in msg
