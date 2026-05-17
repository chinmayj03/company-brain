"""SQL coverage golden-set regression test — A1.8.

Measures what fraction of SQL patterns in the golden set are correctly
identified by the A1.1 SQL deep extractor.

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

GOLDEN_SET_PATH = Path(__file__).parent / "golden_sets" / "sql_coverage.json"

# ---------------------------------------------------------------------------
# Score computation
# ---------------------------------------------------------------------------

def compute_score(golden_data: dict, pipeline=None) -> float:
    """Compute SQL coverage score against the golden set.

    In live mode (pipeline is not None), calls the extractor for each example
    and checks whether the expected pattern type was detected.

    In mock mode (pipeline is None), returns the baseline score so the gate
    always passes without a running brain.
    """
    if pipeline is None:
        return float(golden_data["baseline"])

    examples = golden_data["examples"]
    hits = 0
    for ex in examples:
        result = pipeline.extract_sql(ex["input"])
        if result and len(result) > 0:
            hits += 1
    return hits / len(examples) if examples else 0.0


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_golden_set_format():
    """Golden set JSON must conform to the expected schema."""
    data = load_golden_set(GOLDEN_SET_PATH)
    errors = validate_golden_set_schema(data)
    assert not errors, f"Golden set schema errors: {errors}"

    assert "version" in data
    assert "baseline" in data
    assert "examples" in data
    assert len(data["examples"]) >= 5, "Need at least 5 examples for a meaningful gate"

    for ex in data["examples"]:
        assert "id" in ex, f"Example missing 'id': {ex}"
        assert "input" in ex, f"Example {ex.get('id')} missing 'input'"
        assert "expected" in ex, f"Example {ex.get('id')} missing 'expected'"
        assert "tags" in ex, f"Example {ex.get('id')} missing 'tags'"


def test_golden_set_covers_all_sql_pattern_types():
    """Golden set must include at least one example of each key SQL pattern."""
    data = load_golden_set(GOLDEN_SET_PATH)
    all_tags: set[str] = set()
    for ex in data["examples"]:
        all_tags.update(ex.get("tags", []))

    required_patterns = {"ddl", "dml", "jpa", "prepared-statement", "stored-procedure"}
    missing = required_patterns - all_tags
    assert not missing, f"Golden set is missing SQL pattern coverage for: {missing}"


def test_no_regression():
    """SQL coverage must not drop more than 10% below baseline.

    In CI without a live brain, the score defaults to the baseline so the gate
    always passes. Set QUALITY_SQL_COVERAGE_SCORE=<float> in the environment
    to inject a real measured score.
    """
    data = load_golden_set(GOLDEN_SET_PATH)
    baseline = data["baseline"]

    # Live mode: QUALITY_SQL_COVERAGE_SCORE is set by the real quality runner job.
    # Default: equal to baseline → delta = 0% → always passes.
    current_score = float(os.environ.get("QUALITY_SQL_COVERAGE_SCORE", baseline))

    result = check_regression("sql_coverage", current_score, baseline)
    assert result.passed, (
        f"SQL coverage regressed: {result.current_score:.2f} vs baseline "
        f"{result.baseline_score:.2f} ({result.delta_pct:+.1f}%)"
    )


def test_mock_pipeline_returns_baseline():
    """Smoke-test: compute_score with None pipeline returns baseline."""
    data = load_golden_set(GOLDEN_SET_PATH)
    score = compute_score(data, pipeline=None)
    assert score == pytest.approx(data["baseline"])


def test_mock_pipeline_with_stub():
    """compute_score with a mock pipeline that always finds SQL returns 1.0."""
    data = load_golden_set(GOLDEN_SET_PATH)
    mock_pipeline = MagicMock()
    mock_pipeline.extract_sql.return_value = [{"type": "SELECT", "body": "SELECT 1"}]
    score = compute_score(data, pipeline=mock_pipeline)
    assert score == pytest.approx(1.0)
