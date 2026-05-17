"""
Unit tests for MultiSignalAggregator — A1.4 Verbalized Confidence.

Tests verify:
  - Default weights produce correct scalar
  - Custom weights override defaults
  - Partial weight override is re-normalised correctly
  - All-zero signals → 0.0 scalar
  - All-one signals → 1.0 scalar
  - Scalar is clamped to [0, 1]
  - AggregatedConfidence carries all expected fields
"""
from __future__ import annotations

import pytest

from companybrain.confidence.signals import ConfidenceSignals
from companybrain.confidence.aggregator import MultiSignalAggregator, _DEFAULT_WEIGHTS


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_signals(
    retrieval_score=0.0,
    entity_match_count=0,
    source_diversity=0.5,
    verifier_agreement=0.5,
    chain_length=0,
    freshness_score=0.5,
) -> ConfidenceSignals:
    return ConfidenceSignals(
        retrieval_score=retrieval_score,
        entity_match_count=entity_match_count,
        source_diversity=source_diversity,
        verifier_agreement=verifier_agreement,
        chain_length=chain_length,
        freshness_score=freshness_score,
    )


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestDefaultWeights:
    def test_weights_sum_to_one(self):
        total = sum(_DEFAULT_WEIGHTS.values())
        assert abs(total - 1.0) < 1e-9

    def test_all_zero_signals_give_zero(self):
        agg = MultiSignalAggregator()
        signals = make_signals(
            retrieval_score=0.0,
            entity_match_count=0,
            source_diversity=0.0,
            verifier_agreement=0.0,
            chain_length=0,
            freshness_score=0.0,
        )
        result = agg.aggregate(signals)
        assert result.value == 0.0

    def test_all_one_signals_give_one(self):
        agg = MultiSignalAggregator()
        signals = make_signals(
            retrieval_score=1.0,
            entity_match_count=5,     # normalised → 1.0
            source_diversity=1.0,
            verifier_agreement=1.0,
            chain_length=5,           # normalised → 1.0
            freshness_score=1.0,
        )
        result = agg.aggregate(signals)
        assert abs(result.value - 1.0) < 1e-4

    def test_mid_signals_give_mid_scalar(self):
        """Uniform 0.5 across all signals should yield 0.5."""
        agg = MultiSignalAggregator()
        # entity_match_count=2 or 3 gives ~0.4–0.6 normalised;
        # use raw normalised values instead for a predictable test.
        signals = ConfidenceSignals(
            retrieval_score=0.5,
            entity_match_count=3,      # normalised → 0.6 (close to mid)
            source_diversity=0.5,
            verifier_agreement=0.5,
            chain_length=2,            # normalised → 0.4 (close to mid)
            freshness_score=0.5,
        )
        result = agg.aggregate(signals)
        # Expected: 0.5*0.30 + 0.6*0.20 + 0.5*0.15 + 0.5*0.20 + 0.4*0.10 + 0.5*0.05
        # = 0.150 + 0.120 + 0.075 + 0.100 + 0.040 + 0.025 = 0.510
        assert abs(result.value - 0.510) < 0.001

    def test_known_weighted_calculation(self):
        """Explicit calculation matches the weighted sum formula."""
        agg = MultiSignalAggregator()
        signals = make_signals(
            retrieval_score=0.8,
            entity_match_count=3,   # → 0.6
            source_diversity=0.7,
            verifier_agreement=1.0,
            chain_length=5,         # → 1.0
            freshness_score=0.9,
        )
        # Expected (default weights):
        # 0.8*0.30 + 0.6*0.20 + 0.7*0.15 + 1.0*0.20 + 1.0*0.10 + 0.9*0.05
        # = 0.240 + 0.120 + 0.105 + 0.200 + 0.100 + 0.045 = 0.810
        result = agg.aggregate(signals)
        assert abs(result.value - 0.810) < 0.001

    def test_high_confidence_label(self):
        agg = MultiSignalAggregator()
        signals = make_signals(
            retrieval_score=1.0, entity_match_count=5,
            source_diversity=1.0, verifier_agreement=1.0,
            chain_length=5, freshness_score=1.0,
        )
        result = agg.aggregate(signals)
        assert result.label == "high"

    def test_medium_confidence_label(self):
        agg = MultiSignalAggregator()
        # Scalar ≈ 0.65 → medium
        signals = make_signals(
            retrieval_score=0.7, entity_match_count=3,
            source_diversity=0.6, verifier_agreement=0.5,
            chain_length=2, freshness_score=0.5,
        )
        result = agg.aggregate(signals)
        assert result.label == "medium"

    def test_low_confidence_label(self):
        agg = MultiSignalAggregator()
        signals = make_signals(
            retrieval_score=0.1, entity_match_count=0,
            source_diversity=0.1, verifier_agreement=0.0,
            chain_length=0, freshness_score=0.2,
        )
        result = agg.aggregate(signals)
        assert result.label == "low"


class TestCustomWeights:
    def test_custom_weights_override_defaults(self):
        """When retrieval weight is 1.0 (all others 0), scalar == retrieval_score."""
        agg = MultiSignalAggregator(weights={
            "retrieval": 1.0,
            "entity_match": 0.0,
            "source_diversity": 0.0,
            "verifier": 0.0,
            "chain": 0.0,
            "freshness": 0.0,
        })
        signals = make_signals(retrieval_score=0.73)
        result = agg.aggregate(signals)
        assert abs(result.value - 0.73) < 0.001

    def test_partial_weight_override_renormalises(self):
        """Partial override (only one key) is re-normalised; result still in [0,1]."""
        agg = MultiSignalAggregator(weights={"retrieval": 1.0})
        signals = make_signals(
            retrieval_score=0.9, entity_match_count=5,
            source_diversity=1.0, verifier_agreement=1.0,
            chain_length=5, freshness_score=1.0,
        )
        result = agg.aggregate(signals)
        assert 0.0 <= result.value <= 1.0

    def test_weights_attribute_reflects_normalised_values(self):
        agg = MultiSignalAggregator(weights={
            "retrieval": 2.0, "entity_match": 2.0, "source_diversity": 2.0,
            "verifier": 2.0, "chain": 2.0, "freshness": 2.0,
        })
        total = sum(agg._weights.values())
        assert abs(total - 1.0) < 1e-9


class TestOutputFields:
    def test_result_has_all_fields(self):
        agg = MultiSignalAggregator()
        signals = make_signals(retrieval_score=0.8, entity_match_count=3)
        result = agg.aggregate(signals)
        assert isinstance(result.value, float)
        assert result.label in ("high", "medium", "low")
        assert isinstance(result.rationale, str)
        assert isinstance(result.signals, dict)
        assert isinstance(result.weights, dict)

    def test_signals_dict_has_all_keys(self):
        agg = MultiSignalAggregator()
        signals = make_signals(entity_match_count=2)
        result = agg.aggregate(signals)
        expected_keys = {
            "retrieval_score", "entity_match_count", "source_diversity",
            "verifier_agreement", "chain_length", "freshness_score",
        }
        assert set(result.signals.keys()) == expected_keys

    def test_rationale_is_non_empty_string(self):
        agg = MultiSignalAggregator()
        signals = make_signals()
        result = agg.aggregate(signals)
        assert len(result.rationale) > 10


class TestEntityNormalisation:
    @pytest.mark.parametrize("count,expected_norm", [
        (0, 0.0),
        (1, 0.2),
        (5, 1.0),
        (10, 1.0),  # capped at 1.0
    ])
    def test_entity_normalisation(self, count, expected_norm):
        s = ConfidenceSignals(entity_match_count=count)
        assert abs(s.normalised_entity_match - expected_norm) < 0.001

    @pytest.mark.parametrize("length,expected_norm", [
        (0, 0.0),
        (1, 0.2),
        (5, 1.0),
        (20, 1.0),  # capped
    ])
    def test_chain_normalisation(self, length, expected_norm):
        s = ConfidenceSignals(chain_length=length)
        assert abs(s.normalised_chain_length - expected_norm) < 0.001
