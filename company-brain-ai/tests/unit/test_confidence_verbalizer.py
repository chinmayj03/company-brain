"""
Unit tests for Verbalizer — A1.4 Verbalized Confidence.

Tests verify:
  - Scalar thresholds map to correct labels
  - Rationale string is non-empty and references actual signal values
  - Boundary values (exactly 0.80, 0.55) behave correctly
  - Freshness stub (0.5) is not mentioned in rationale
  - Freshness non-stub is mentioned
  - Entity counts appear in rationale
"""
from __future__ import annotations

import pytest

from companybrain.confidence.signals import ConfidenceSignals
from companybrain.confidence.verbalizer import (
    Verbalizer,
    HIGH_THRESHOLD,
    MEDIUM_THRESHOLD,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_signals(**kwargs) -> ConfidenceSignals:
    defaults = dict(
        retrieval_score=0.5,
        entity_match_count=2,
        source_diversity=0.5,
        verifier_agreement=0.5,
        chain_length=0,
        freshness_score=0.5,
    )
    defaults.update(kwargs)
    return ConfidenceSignals(**defaults)


# ── Label threshold tests ─────────────────────────────────────────────────────

class TestLabelThresholds:
    @pytest.mark.parametrize("scalar,expected", [
        (0.80, "high"),
        (0.85, "high"),
        (1.00, "high"),
        (0.799, "medium"),
        (0.55, "medium"),
        (0.60, "medium"),
        (0.549, "low"),
        (0.30, "low"),
        (0.00, "low"),
    ])
    def test_scalar_to_label(self, scalar, expected):
        v = Verbalizer()
        signals = make_signals()
        result = v.verbalize(scalar, signals)
        assert result.label == expected, (
            f"scalar={scalar} expected={expected!r} got={result.label!r}"
        )

    def test_exactly_high_threshold(self):
        v = Verbalizer()
        assert v.verbalize(HIGH_THRESHOLD, make_signals()).label == "high"

    def test_just_below_high_threshold(self):
        v = Verbalizer()
        assert v.verbalize(HIGH_THRESHOLD - 0.001, make_signals()).label == "medium"

    def test_exactly_medium_threshold(self):
        v = Verbalizer()
        assert v.verbalize(MEDIUM_THRESHOLD, make_signals()).label == "medium"

    def test_just_below_medium_threshold(self):
        v = Verbalizer()
        assert v.verbalize(MEDIUM_THRESHOLD - 0.001, make_signals()).label == "low"


# ── Rationale content tests ───────────────────────────────────────────────────

class TestRationaleContent:
    def test_rationale_is_non_empty(self):
        v = Verbalizer()
        r = v.verbalize(0.7, make_signals())
        assert r.rationale and len(r.rationale) > 20

    def test_rationale_includes_scalar_percent(self):
        v = Verbalizer()
        r = v.verbalize(0.73, make_signals())
        assert "73%" in r.rationale

    def test_rationale_includes_entity_count(self):
        v = Verbalizer()
        r = v.verbalize(0.7, make_signals(entity_match_count=3))
        assert "3" in r.rationale

    def test_rationale_includes_label(self):
        v = Verbalizer()
        r = v.verbalize(0.82, make_signals())
        assert "high" in r.rationale

    def test_rationale_verifier_run(self):
        v = Verbalizer()
        r = v.verbalize(0.9, make_signals(verifier_agreement=1.0))
        assert "verifier" in r.rationale.lower()
        assert "confirmed" in r.rationale.lower()

    def test_rationale_verifier_not_run(self):
        v = Verbalizer()
        r = v.verbalize(0.6, make_signals(verifier_agreement=0.5))
        assert "verifier" in r.rationale.lower()
        assert "not run" in r.rationale.lower()

    def test_rationale_verifier_issues(self):
        v = Verbalizer()
        r = v.verbalize(0.3, make_signals(verifier_agreement=0.0))
        assert "verifier" in r.rationale.lower()
        assert "issues" in r.rationale.lower()

    def test_rationale_chain_length_mentioned_when_nonzero(self):
        v = Verbalizer()
        r = v.verbalize(0.7, make_signals(chain_length=4))
        assert "4" in r.rationale
        assert "hop" in r.rationale.lower()

    def test_rationale_chain_length_not_mentioned_when_zero(self):
        v = Verbalizer()
        r = v.verbalize(0.5, make_signals(chain_length=0))
        assert "hop" not in r.rationale.lower()

    def test_rationale_freshness_stub_not_mentioned(self):
        """freshness_score=0.5 is the stub value and should be omitted."""
        v = Verbalizer()
        r = v.verbalize(0.5, make_signals(freshness_score=0.5))
        # The word "stale" or "extracted" should not appear when using stub
        assert "stale" not in r.rationale.lower()
        assert "extracted" not in r.rationale.lower()

    def test_rationale_freshness_non_stub_mentioned(self):
        """freshness_score != 0.5 should appear in rationale."""
        v = Verbalizer()
        r = v.verbalize(0.5, make_signals(freshness_score=0.9))
        # Should mention "recently"
        assert "recent" in r.rationale.lower()

    def test_rationale_freshness_stale_mentioned(self):
        v = Verbalizer()
        r = v.verbalize(0.4, make_signals(freshness_score=0.1))
        assert "stale" in r.rationale.lower() or "days" in r.rationale.lower()

    def test_rationale_retrieval_score_strong(self):
        v = Verbalizer()
        r = v.verbalize(0.85, make_signals(retrieval_score=0.9))
        assert "strong" in r.rationale.lower() or "0.9" in r.rationale

    def test_rationale_retrieval_score_weak(self):
        v = Verbalizer()
        r = v.verbalize(0.3, make_signals(retrieval_score=0.2))
        assert "weak" in r.rationale.lower() or "0.2" in r.rationale

    def test_rationale_no_entities(self):
        v = Verbalizer()
        r = v.verbalize(0.2, make_signals(entity_match_count=0))
        assert "no entities" in r.rationale.lower()

    def test_rationale_many_entities(self):
        v = Verbalizer()
        r = v.verbalize(0.9, make_signals(entity_match_count=7))
        assert "7" in r.rationale
        assert "strong" in r.rationale.lower() or "coverage" in r.rationale.lower()


# ── VerbalizedConfidence fields ───────────────────────────────────────────────

class TestVerbalizedConfidenceFields:
    def test_returns_dataclass(self):
        from companybrain.confidence.verbalizer import VerbalizedConfidence
        v = Verbalizer()
        r = v.verbalize(0.7, make_signals())
        assert isinstance(r, VerbalizedConfidence)
        assert hasattr(r, "label")
        assert hasattr(r, "rationale")
