"""Unit tests for SelfVerifier (ADR-0061 P1 / M3)."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from companybrain.query.self_verifier import SelfVerifier, VerifierResult


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_verifier(score: float = 0.9, issues: list[str] | None = None) -> SelfVerifier:
    v = SelfVerifier.__new__(SelfVerifier)
    v._threshold = 0.6
    v._provider = MagicMock()
    return v


def _mock_provider_response(content: str) -> MagicMock:
    resp = MagicMock()
    resp.content = content
    provider = MagicMock()
    provider.chat = AsyncMock(return_value=resp)
    return provider


# ── _parse ────────────────────────────────────────────────────────────────────

class TestSelfVerifierParse:
    def test_parse_clean_json(self):
        v = _make_verifier()
        result = v._parse('{"verified": true, "score": 0.95, "issues": []}')
        assert result.verified is True
        assert result.score == pytest.approx(0.95)
        assert result.issues == []

    def test_parse_markdown_fence(self):
        v = _make_verifier()
        raw = '```json\n{"verified": false, "score": 0.4, "issues": ["claim X not cited"]}\n```'
        result = v._parse(raw)
        assert result.verified is False
        assert result.score == pytest.approx(0.4)
        assert "claim X not cited" in result.issues

    def test_parse_score_only_fallback(self):
        v = _make_verifier()
        result = v._parse('some text "score": 0.55 more text')
        assert result.score == pytest.approx(0.55)
        assert result.verified is False   # 0.55 < 0.7

    def test_parse_garbage_returns_safe_default(self):
        v = _make_verifier()
        result = v._parse("I cannot verify this answer.")
        assert 0.0 <= result.score <= 1.0
        assert isinstance(result.verified, bool)

    def test_issues_are_strings(self):
        v = _make_verifier()
        result = v._parse('{"verified": false, "score": 0.5, "issues": [1, 2]}')
        assert all(isinstance(i, str) for i in result.issues)


# ── _format_citations ─────────────────────────────────────────────────────────

class TestFormatCitations:
    def test_empty_returns_fallback_string(self):
        v = _make_verifier()
        out = v._format_citations({})
        assert "no citation" in out.lower()

    def test_long_content_is_truncated(self):
        v = _make_verifier()
        out = v._format_citations({"urn:x": "x" * 1000})
        assert len(out) < 600   # 400-char excerpt + urn prefix

    def test_urn_appears_in_output(self):
        v = _make_verifier()
        out = v._format_citations({"urn:cb:dev:code:Foo": "class Foo {}"})
        assert "urn:cb:dev:code:Foo" in out


# ── verify() integration ──────────────────────────────────────────────────────

class TestSelfVerifierVerify:
    @pytest.mark.asyncio
    async def test_empty_answer_is_verified(self):
        v = SelfVerifier.__new__(SelfVerifier)
        v._threshold = 0.6
        v._provider = MagicMock()
        result = await v.verify("", {})
        assert result.verified is True
        assert result.score == 1.0

    @pytest.mark.asyncio
    async def test_llm_called_with_expected_content(self):
        provider = _mock_provider_response(
            '{"verified": true, "score": 0.9, "issues": []}'
        )
        v = SelfVerifier.__new__(SelfVerifier)
        v._threshold = 0.6
        v._provider = provider

        result = await v.verify(
            "PaymentService calls Stripe [urn:x]",
            {"urn:x": "class PaymentService { void charge() { stripe.call(); } }"},
        )
        assert result.verified is True
        assert result.score >= 0.7
        provider.chat.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_provider_error_returns_safe_default(self):
        provider = MagicMock()
        provider.chat = AsyncMock(side_effect=RuntimeError("network error"))
        v = SelfVerifier.__new__(SelfVerifier)
        v._threshold = 0.6
        v._provider = provider

        result = await v.verify("some answer", {"urn:x": "content"})
        assert result.verified is True   # fail-safe: don't block response
        assert result.score == pytest.approx(0.8)

    @pytest.mark.asyncio
    async def test_low_score_sets_verified_false(self):
        provider = _mock_provider_response(
            '{"verified": false, "score": 0.3, "issues": ["claim not in citations"]}'
        )
        v = SelfVerifier.__new__(SelfVerifier)
        v._threshold = 0.6
        v._provider = provider

        result = await v.verify("Foo calls Bar", {"urn:x": "completely unrelated content"})
        assert result.verified is False
        assert result.score < 0.6
        assert len(result.issues) >= 1
