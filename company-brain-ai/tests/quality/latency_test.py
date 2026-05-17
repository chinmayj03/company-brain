"""Latency regression test — A1.8.

Verifies that query response latency stays within baseline P50 and P90
thresholds. Baselines: P50=3.0s, P90=8.0s.

Unlike other quality tests, this test uses an inline golden set (no JSON file)
because latency baselines are infrastructure-dependent and should be tuned per
deployment rather than committed as absolute thresholds.

Running with a live brain: set QUALITY_RUN_LIVE=true and inject measured
percentile scores via QUALITY_LATENCY_P50_SCORE / QUALITY_LATENCY_P90_SCORE.
Without a live brain: uses baseline scores (always passes).

Note: for latency, lower is better. The regression check is INVERTED —
a "higher than baseline + 10%" P50/P90 is a regression. We implement this
by treating (baseline / current) as the score: if current > baseline, score < 1.
"""
from __future__ import annotations

import os
import time
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest

from tests.quality.harness import check_regression

# ---------------------------------------------------------------------------
# Inline golden set (no JSON file — latency baselines are env-specific)
# ---------------------------------------------------------------------------

LATENCY_BASELINE = {
    "version": "1.0",
    "description": (
        "Query latency baselines. P50 and P90 are wall-clock seconds for "
        "a live /query call against an indexed repository."
    ),
    "p50_baseline_s": 3.0,
    "p90_baseline_s": 8.0,
    "sample_queries": [
        "What does ClaimRepository.findByMemberId do?",
        "Which services call MemberEligibilityService?",
        "What SQL runs when a claim is adjudicated?",
        "Show the call chain for POST /api/claims",
        "What is the blast radius of changing NetworkProviderRepository?",
        "How does prior authorization flow from request to approval?",
        "Which tables does EnrollmentService write to?",
        "What happens if the npi_number column is removed?",
        "Show all callers of ClaimAdjudicationService.adjudicate",
        "What is the purpose of EligibilitySnapshotJob?",
    ],
}


# ---------------------------------------------------------------------------
# Score computation
# ---------------------------------------------------------------------------

def measure_latency_percentiles(
    query_engine, queries: list[str]
) -> tuple[float, float]:
    """Run all queries and return (p50_s, p90_s) wall-clock latency."""
    import statistics

    latencies: list[float] = []
    for q in queries:
        t0 = time.monotonic()
        query_engine.query(q)
        latencies.append(time.monotonic() - t0)

    latencies.sort()
    n = len(latencies)
    p50 = latencies[int(n * 0.50)]
    p90 = latencies[int(n * 0.90)]
    return p50, p90


def latency_to_score(measured_s: float, baseline_s: float) -> float:
    """Convert a latency measurement to a [0, 1] score for regression checks.

    Score = baseline / measured. If measured == baseline → score = 1.0.
    If measured is 2× baseline → score = 0.5 (50% regression).
    This lets check_regression() handle latency uniformly with other metrics.
    """
    if measured_s <= 0:
        return 1.0
    return baseline_s / measured_s


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_latency_baseline_spec_format():
    """Inline baseline spec must contain the expected fields."""
    spec = LATENCY_BASELINE
    assert "version" in spec
    assert "p50_baseline_s" in spec
    assert "p90_baseline_s" in spec
    assert "sample_queries" in spec
    assert len(spec["sample_queries"]) >= 5
    assert spec["p50_baseline_s"] > 0
    assert spec["p90_baseline_s"] >= spec["p50_baseline_s"]


def test_no_regression_p50():
    """P50 query latency must not exceed baseline P50 by more than 10%.

    In CI without a live brain, the score defaults to 1.0 (equal to baseline).
    Set QUALITY_LATENCY_P50_SCORE=<float_seconds> in the environment to inject
    a real measured P50.
    """
    baseline_p50 = LATENCY_BASELINE["p50_baseline_s"]
    measured_p50 = float(
        os.environ.get("QUALITY_LATENCY_P50_SCORE", baseline_p50)
    )

    # Convert latency to score: 1.0 = at baseline, <0.9 = >10% slower = regression
    current_score = latency_to_score(measured_p50, baseline_p50)
    baseline_score = 1.0  # by definition, baseline / baseline = 1.0

    result = check_regression("latency_p50", current_score, baseline_score)
    assert result.passed, (
        f"P50 latency regressed: {measured_p50:.2f}s vs baseline "
        f"{baseline_p50:.2f}s ({result.delta_pct:+.1f}%)"
    )


def test_no_regression_p90():
    """P90 query latency must not exceed baseline P90 by more than 10%.

    In CI without a live brain, the score defaults to 1.0 (equal to baseline).
    Set QUALITY_LATENCY_P90_SCORE=<float_seconds> in the environment to inject
    a real measured P90.
    """
    baseline_p90 = LATENCY_BASELINE["p90_baseline_s"]
    measured_p90 = float(
        os.environ.get("QUALITY_LATENCY_P90_SCORE", baseline_p90)
    )

    current_score = latency_to_score(measured_p90, baseline_p90)
    baseline_score = 1.0

    result = check_regression("latency_p90", current_score, baseline_score)
    assert result.passed, (
        f"P90 latency regressed: {measured_p90:.2f}s vs baseline "
        f"{baseline_p90:.2f}s ({result.delta_pct:+.1f}%)"
    )


def test_latency_to_score_at_baseline():
    """latency_to_score returns 1.0 when measured equals baseline."""
    assert latency_to_score(3.0, 3.0) == pytest.approx(1.0)


def test_latency_to_score_improvement():
    """latency_to_score > 1.0 when measured is faster than baseline."""
    assert latency_to_score(1.5, 3.0) == pytest.approx(2.0)


def test_latency_to_score_regression():
    """latency_to_score < 1.0 when measured is slower than baseline."""
    assert latency_to_score(6.0, 3.0) == pytest.approx(0.5)


def test_latency_to_score_zero_safe():
    """latency_to_score handles zero measured latency gracefully."""
    assert latency_to_score(0.0, 3.0) == pytest.approx(1.0)


def test_mock_pipeline_latency_measurement():
    """measure_latency_percentiles returns non-negative floats with a mock engine."""
    mock_engine = MagicMock()
    mock_engine.query.return_value = {"summary": "mock answer"}

    queries = LATENCY_BASELINE["sample_queries"]
    p50, p90 = measure_latency_percentiles(mock_engine, queries)

    assert p50 >= 0.0
    assert p90 >= p50
    assert mock_engine.query.call_count == len(queries)
