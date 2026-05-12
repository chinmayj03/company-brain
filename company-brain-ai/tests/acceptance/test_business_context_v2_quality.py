"""
ADR-0060 — acceptance tests for the v2 BusinessContext quality lift.

These tests require:
  • the network-iq fixture repo under fixtures/network-iq-snapshot
  • a working `run_pipeline_harness` + `brain_get_context` test helper
  • a benchmark question runner

Where any of those are missing on the running host (e.g. local dev box
without the fixture, CI without the LLM), the tests skip cleanly. The
v2-fields machinery itself is covered exhaustively by the unit + regression
suites; this file is what you run when you want to claim the ADR's quality
lift is real.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

pytestmark = pytest.mark.acceptance


FIXTURE_ROOT = Path("fixtures/network-iq-snapshot")


def _skip_if_no_fixture() -> None:
    if not FIXTURE_ROOT.exists():
        pytest.skip(f"fixture missing: {FIXTURE_ROOT}")


def _try_import(name: str):
    """Return the module if importable, else None. Used to skip when a
    harness helper isn't wired up in this checkout yet."""
    try:
        return importlib.import_module(name)
    except ImportError:
        return None


def _harness():
    """The test harness lives in companybrain.harness.pipeline_test (or
    similar) on hosts that have it. Skip if absent."""
    mod = _try_import("companybrain.harness.pipeline_test")
    if mod is None or not all(
        hasattr(mod, fn)
        for fn in ("run_pipeline_harness", "brain_get_context", "run_benchmark_question")
    ):
        pytest.skip("pipeline test harness not available in this checkout")
    return mod


async def test_idempotent_field_populated():
    """SELECT-only methods should have is_idempotent=true."""
    _skip_if_no_fixture()
    harness = _harness()
    await harness.run_pipeline_harness(repo=str(FIXTURE_ROOT))
    bc = await harness.brain_get_context(
        "CompetitivenessPlanRepository.getPayerCompetitors"
    )
    assert bc.schema_version == 2
    assert bc.is_idempotent is True


async def test_null_handling_per_param():
    """getPayerCompetitors null-checks basePayerName but not request."""
    _skip_if_no_fixture()
    harness = _harness()
    bc = await harness.brain_get_context(
        "CompetitivenessPlanRepository.getPayerCompetitors"
    )
    assert bc.null_handling.get("basePayerName") == "throws"
    assert bc.null_handling.get("request") in ("unchecked", "tolerates")


async def test_anti_pattern_literal_lob():
    """CompetitivenessReportRequestDTO uses literal 'lob' — anti-pattern."""
    _skip_if_no_fixture()
    harness = _harness()
    bc = await harness.brain_get_context("CompetitivenessReportRequestDTO")
    assert any("literal" in p.lower() for p in bc.anti_patterns)


async def test_quality_lift_on_benchmark_questions():
    """A19 (idempotent), A20 (null handling), A5 (anti-pattern),
    B14 (transaction mode) all PASS after this ADR."""
    _skip_if_no_fixture()
    harness = _harness()
    await harness.run_pipeline_harness(repo=str(FIXTURE_ROOT))
    for qid in ("A19", "A20", "A5", "B14"):
        result = await harness.run_benchmark_question(qid)
        assert result.status == "PASS", f"{qid} expected PASS, got {result.status}"
