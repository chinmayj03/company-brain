"""Quality regression harness. Runs golden-set tests and compares against baselines."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

QUALITY_THRESHOLD = 0.10  # > 10% drop fails the gate


@dataclass
class QualityResult:
    metric: str
    current_score: float
    baseline_score: float
    passed: bool
    delta_pct: float  # positive = improvement, negative = regression


def check_regression(metric: str, current: float, baseline: float) -> QualityResult:
    """Compare current score against baseline and return a QualityResult.

    A regression is detected when the current score drops more than
    QUALITY_THRESHOLD (10%) below the baseline.
    """
    delta = (current - baseline) / baseline if baseline > 0 else 0.0
    return QualityResult(
        metric=metric,
        current_score=current,
        baseline_score=baseline,
        passed=delta >= -QUALITY_THRESHOLD,
        delta_pct=delta * 100,
    )


def load_golden_set(path: Path) -> dict[str, Any]:
    """Load a golden set JSON file and return its contents."""
    with path.open() as f:
        return json.load(f)


def validate_golden_set_schema(data: dict[str, Any]) -> list[str]:
    """Validate the structure of a golden set dict.

    Returns a list of error strings; empty list means valid.
    """
    errors: list[str] = []

    if "version" not in data:
        errors.append("Missing required field: 'version'")
    if "baseline" not in data:
        errors.append("Missing required field: 'baseline'")
    elif not isinstance(data["baseline"], (int, float)):
        errors.append("Field 'baseline' must be a number")
    if "description" not in data:
        errors.append("Missing required field: 'description'")
    if "examples" not in data:
        errors.append("Missing required field: 'examples'")
    elif not isinstance(data["examples"], list):
        errors.append("Field 'examples' must be a list")
    elif len(data["examples"]) == 0:
        errors.append("Field 'examples' must not be empty")

    return errors
