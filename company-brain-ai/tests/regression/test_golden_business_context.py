"""
ADR-0060 — regression check on the golden BusinessContext few-shot fixtures.

The 30 golden files under tests/regression/golden_business_context/ are the
human-curated source of truth for the few-shot library shipped in
companybrain.pipeline.few_shot_library. If the in-memory library and the
on-disk snapshot diverge, this test fails — forcing the author to consciously
regenerate the snapshot rather than letting drift sneak in.
"""

from __future__ import annotations

import json
from pathlib import Path

from companybrain.pipeline.few_shot_library import EXAMPLES


FIXTURE_DIR = Path(__file__).parent / "golden_business_context"


def test_thirty_golden_fixtures_on_disk():
    fixtures = sorted(FIXTURE_DIR.glob("example_*.json"))
    assert len(fixtures) == 30, "expected 30 golden BusinessContext fixtures"


def test_each_golden_fixture_matches_in_memory_library():
    """One-to-one alignment between in-memory EXAMPLES[i] and the
    example_(i+1:02).json snapshot. Drift fails the build."""
    fixtures = sorted(FIXTURE_DIR.glob("example_*.json"))
    assert len(fixtures) == len(EXAMPLES)
    for snapshot_path, in_mem in zip(fixtures, EXAMPLES):
        snapshot = json.loads(snapshot_path.read_text())
        assert snapshot == in_mem, (
            f"{snapshot_path.name} drift — regenerate the snapshot from "
            f"few_shot_library.EXAMPLES if the library was intentionally edited"
        )
