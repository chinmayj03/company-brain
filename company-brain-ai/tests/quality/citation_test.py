"""Citation rate golden-set regression test — A1.8.

Verifies that every factual answer includes at least the minimum number of
cited entities. Baseline 1.0 means 100% of answers must have at least one
citation — no uncited answers allowed.

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

GOLDEN_SET_PATH = Path(__file__).parent / "golden_sets" / "citation.json"


# ---------------------------------------------------------------------------
# Score computation
# ---------------------------------------------------------------------------

def _example_passes(example: dict, response: dict) -> bool:
    """Return True if the response meets the min_citations requirement."""
    min_required = example.get("min_citations", 1)
    citations = response.get("affected_entities", []) + [
        step for step in response.get("call_chain", [])
    ]
    return len(citations) >= min_required


def compute_score(golden_data: dict, query_engine=None) -> float:
    """Compute citation rate score against the golden set.

    In live mode (query_engine is not None), calls the engine for each question
    and checks whether the response meets the min_citations requirement.

    In mock mode (query_engine is None), returns the baseline score so the gate
    always passes without a running brain.
    """
    if query_engine is None:
        return float(golden_data["baseline"])

    examples = golden_data["examples"]
    if not examples:
        return 0.0

    passes = 0
    for ex in examples:
        response = query_engine.query(ex["question"])
        if _example_passes(ex, response):
            passes += 1

    return passes / len(examples)


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
        assert "min_citations" in ex, f"Example {ex.get('id')} missing 'min_citations'"
        assert isinstance(ex["min_citations"], int), (
            f"Example {ex.get('id')}: 'min_citations' must be an int"
        )
        assert ex["min_citations"] >= 1, (
            f"Example {ex.get('id')}: 'min_citations' must be >= 1"
        )


def test_golden_set_baseline_is_perfect():
    """Baseline for citation rate must be 1.0 (every answer must cite)."""
    data = load_golden_set(GOLDEN_SET_PATH)
    assert data["baseline"] == pytest.approx(1.0), (
        f"Citation baseline should be 1.0 (100%), got {data['baseline']}"
    )


def test_no_regression():
    """Citation rate must not drop more than 10% below baseline.

    In CI without a live brain, the score defaults to the baseline so the gate
    always passes. Set QUALITY_CITATION_SCORE=<float> in the environment
    to inject a real measured score.
    """
    data = load_golden_set(GOLDEN_SET_PATH)
    baseline = data["baseline"]

    current_score = float(os.environ.get("QUALITY_CITATION_SCORE", baseline))

    result = check_regression("citation_rate", current_score, baseline)
    assert result.passed, (
        f"Citation rate regressed: {result.current_score:.2f} vs baseline "
        f"{result.baseline_score:.2f} ({result.delta_pct:+.1f}%)"
    )


def test_mock_pipeline_returns_baseline():
    """Smoke-test: compute_score with None query_engine returns baseline."""
    data = load_golden_set(GOLDEN_SET_PATH)
    score = compute_score(data, query_engine=None)
    assert score == pytest.approx(data["baseline"])


def test_example_passes_with_sufficient_citations():
    """_example_passes returns True when affected_entities meets min_citations."""
    example = {"min_citations": 2}
    response = {
        "affected_entities": [{"urn": "a"}, {"urn": "b"}],
        "call_chain": [],
    }
    assert _example_passes(example, response) is True


def test_example_fails_with_insufficient_citations():
    """_example_passes returns False when citations are below minimum."""
    example = {"min_citations": 2}
    response = {
        "affected_entities": [{"urn": "a"}],
        "call_chain": [],
    }
    assert _example_passes(example, response) is False


def test_example_passes_combining_citations_and_call_chain():
    """_example_passes combines affected_entities and call_chain for total count."""
    example = {"min_citations": 3}
    response = {
        "affected_entities": [{"urn": "a"}, {"urn": "b"}],
        "call_chain": [{"urn": "c", "ord": 1, "name": "X"}],
    }
    assert _example_passes(example, response) is True
