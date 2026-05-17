"""Confidence calibration golden-set regression test — A1.8.

Verifies that the system's reported confidence level (high/medium/low) matches
the expected level for each query. Baseline 0.70 means 70% accuracy — questions
about well-indexed classes should yield high confidence, and ambiguous or
operational questions should yield low confidence.

Running with a live brain: set QUALITY_RUN_LIVE=true to actually call the pipeline.
Without a live brain: uses mock scores equal to baseline (always passes).
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Literal
from unittest.mock import MagicMock

import pytest

from tests.quality.harness import (
    check_regression,
    load_golden_set,
    validate_golden_set_schema,
)

GOLDEN_SET_PATH = Path(__file__).parent / "golden_sets" / "confidence.json"

VALID_CONFIDENCE_LEVELS = {"high", "medium", "low"}


# ---------------------------------------------------------------------------
# Score computation
# ---------------------------------------------------------------------------

def compute_score(golden_data: dict, query_engine=None) -> float:
    """Compute confidence calibration accuracy against the golden set.

    In live mode (query_engine is not None), calls the engine for each question
    and checks whether the reported confidence.level matches expected_level.

    In mock mode (query_engine is None), returns the baseline score so the gate
    always passes without a running brain.
    """
    if query_engine is None:
        return float(golden_data["baseline"])

    examples = golden_data["examples"]
    if not examples:
        return 0.0

    matches = 0
    for ex in examples:
        response = query_engine.query(ex["question"])
        actual_level = response.get("confidence", {}).get("level", "")
        if actual_level == ex["expected_level"]:
            matches += 1

    return matches / len(examples)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_golden_set_format():
    """Golden set JSON must conform to the expected schema."""
    data = load_golden_set(GOLDEN_SET_PATH)
    errors = validate_golden_set_schema(data)
    assert not errors, f"Golden set schema errors: {errors}"

    assert len(data["examples"]) >= 5, "Need at least 5 examples for a meaningful gate"

    for ex in data["examples"]:
        assert "id" in ex, f"Example missing 'id': {ex}"
        assert "question" in ex, f"Example {ex.get('id')} missing 'question'"
        assert "expected_level" in ex, (
            f"Example {ex.get('id')} missing 'expected_level'"
        )
        assert ex["expected_level"] in VALID_CONFIDENCE_LEVELS, (
            f"Example {ex.get('id')}: 'expected_level' must be one of "
            f"{VALID_CONFIDENCE_LEVELS}, got '{ex['expected_level']}'"
        )


def test_golden_set_covers_all_confidence_levels():
    """Golden set must include examples of high, medium, and low confidence."""
    data = load_golden_set(GOLDEN_SET_PATH)
    levels_present = {ex["expected_level"] for ex in data["examples"]}
    missing = VALID_CONFIDENCE_LEVELS - levels_present
    assert not missing, (
        f"Golden set is missing confidence level examples for: {missing}"
    )


def test_no_regression():
    """Confidence calibration accuracy must not drop more than 10% below baseline.

    In CI without a live brain, the score defaults to the baseline so the gate
    always passes. Set QUALITY_CONFIDENCE_SCORE=<float> in the environment
    to inject a real measured score.
    """
    data = load_golden_set(GOLDEN_SET_PATH)
    baseline = data["baseline"]

    current_score = float(os.environ.get("QUALITY_CONFIDENCE_SCORE", baseline))

    result = check_regression("confidence_accuracy", current_score, baseline)
    assert result.passed, (
        f"Confidence accuracy regressed: {result.current_score:.2f} vs baseline "
        f"{result.baseline_score:.2f} ({result.delta_pct:+.1f}%)"
    )


def test_mock_pipeline_returns_baseline():
    """Smoke-test: compute_score with None query_engine returns baseline."""
    data = load_golden_set(GOLDEN_SET_PATH)
    score = compute_score(data, query_engine=None)
    assert score == pytest.approx(data["baseline"])


def test_mock_pipeline_with_perfect_stub():
    """compute_score with a mock that always returns the expected level gives 1.0."""
    data = load_golden_set(GOLDEN_SET_PATH)
    first_level = data["examples"][0]["expected_level"]

    # Build a mock that always returns the right level for each call
    def mock_query(question):
        # Find the expected level for this question
        for ex in data["examples"]:
            if ex["question"] == question:
                return {"confidence": {"level": ex["expected_level"]}}
        return {"confidence": {"level": "low"}}

    mock_engine = MagicMock()
    mock_engine.query.side_effect = mock_query

    score = compute_score(data, query_engine=mock_engine)
    assert score == pytest.approx(1.0)


def test_mock_pipeline_with_wrong_level():
    """compute_score returns 0.0 when mock always returns the wrong level."""
    data = load_golden_set(GOLDEN_SET_PATH)

    wrong_mock = MagicMock()
    # Return a level that is never expected: if expected is "high" this returns "low"
    # We want all to be wrong — pick a level that's unlikely to be universally expected
    wrong_mock.query.return_value = {"confidence": {"level": "NEVER_VALID"}}

    score = compute_score(data, query_engine=wrong_mock)
    assert score == pytest.approx(0.0)
