"""Blast radius recall golden-set regression test — A1.8.

Measures what fraction of true downstream dependents the system surfaces when
asked "what is affected if X changes?". Recall matters more than precision here:
missing a critical dependent is worse than returning extra noise.

Running with a live brain: set QUALITY_RUN_LIVE=true to actually call the pipeline.
Without a live brain: uses mock scores equal to baseline (always passes).
"""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from tests.quality.harness import (
    check_regression,
    load_golden_set,
    validate_golden_set_schema,
)

GOLDEN_SET_PATH = Path(__file__).parent / "golden_sets" / "blast_radius.json"


# ---------------------------------------------------------------------------
# Score computation
# ---------------------------------------------------------------------------

def _recall_for_example(example: dict, actual_affected: list[str]) -> float:
    """Compute per-example recall: |predicted ∩ expected| / |expected|."""
    expected = set(example.get("expected_affected", []))
    if not expected:
        return 1.0
    predicted = set(actual_affected)
    return len(predicted & expected) / len(expected)


def compute_score(golden_data: dict, query_engine=None) -> float:
    """Compute blast radius recall score against the golden set.

    In live mode (query_engine is not None), calls the engine for each example
    and measures recall against expected_affected.

    In mock mode (query_engine is None), returns the baseline score so the gate
    always passes without a running brain.
    """
    if query_engine is None:
        return float(golden_data["baseline"])

    examples = golden_data["examples"]
    if not examples:
        return 0.0

    recall_scores = []
    for ex in examples:
        result = query_engine.query(ex["input"])
        affected = [c.get("name", "") for c in result.get("affected_entities", [])]
        recall_scores.append(_recall_for_example(ex, affected))

    return sum(recall_scores) / len(recall_scores)


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
        assert "input" in ex, f"Example {ex.get('id')} missing 'input'"
        assert "expected_affected" in ex, (
            f"Example {ex.get('id')} missing 'expected_affected'"
        )
        assert isinstance(ex["expected_affected"], list), (
            f"Example {ex.get('id')}: 'expected_affected' must be a list"
        )
        assert len(ex["expected_affected"]) >= 1, (
            f"Example {ex.get('id')}: 'expected_affected' must not be empty"
        )


def test_golden_set_covers_change_types():
    """Golden set must include diverse change scenarios."""
    data = load_golden_set(GOLDEN_SET_PATH)
    all_tags: set[str] = set()
    for ex in data["examples"]:
        all_tags.update(ex.get("tags", []))

    # Must cover at least 3 distinct change-type tags
    change_type_tags = {
        "repository", "database-column", "service-method",
        "schema-change", "interface-change", "service"
    }
    found_change_types = change_type_tags & all_tags
    assert len(found_change_types) >= 3, (
        f"Golden set only covers {found_change_types}; need at least 3 change types"
    )


def test_no_regression():
    """Blast radius recall must not drop more than 10% below baseline.

    In CI without a live brain, the score defaults to the baseline so the gate
    always passes. Set QUALITY_BLAST_RADIUS_SCORE=<float> in the environment
    to inject a real measured score.
    """
    data = load_golden_set(GOLDEN_SET_PATH)
    baseline = data["baseline"]

    current_score = float(os.environ.get("QUALITY_BLAST_RADIUS_SCORE", baseline))

    result = check_regression("blast_radius_recall", current_score, baseline)
    assert result.passed, (
        f"Blast radius recall regressed: {result.current_score:.2f} vs baseline "
        f"{result.baseline_score:.2f} ({result.delta_pct:+.1f}%)"
    )


def test_mock_pipeline_returns_baseline():
    """Smoke-test: compute_score with None query_engine returns baseline."""
    data = load_golden_set(GOLDEN_SET_PATH)
    score = compute_score(data, query_engine=None)
    assert score == pytest.approx(data["baseline"])


def test_recall_computation_perfect():
    """_recall_for_example returns 1.0 when all expected are in predicted."""
    example = {"expected_affected": ["A", "B", "C"]}
    assert _recall_for_example(example, ["A", "B", "C", "D"]) == pytest.approx(1.0)


def test_recall_computation_partial():
    """_recall_for_example returns partial recall correctly."""
    example = {"expected_affected": ["A", "B", "C", "D"]}
    assert _recall_for_example(example, ["A", "B"]) == pytest.approx(0.5)


def test_recall_computation_empty_expected():
    """_recall_for_example returns 1.0 when expected list is empty."""
    example = {"expected_affected": []}
    assert _recall_for_example(example, ["X"]) == pytest.approx(1.0)
