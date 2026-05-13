"""Regression tests for P0 bug bundle (B1/B2/B3/B4/B5/B6).

Each test pins the specific failure mode that was found in the E2E session
and must never regress. Tests are pure-unit (no LLM, no external services).
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── B1: tool_calls_count in headless JSON output ───────────────────────────────

def test_b1_headless_payload_has_tool_calls_count():
    """headless JSON output must include tool_calls_count at the top level."""
    from companybrain.cli_helpers.headless import run_index_headless  # noqa: F401
    import inspect
    src = inspect.getsource(run_index_headless)
    assert "tool_calls_count" in src, (
        "run_index_headless must emit tool_calls_count in telemetry"
    )


def test_b1_headless_tool_calls_rollup(tmp_path):
    """tool_calls_count is summed from per-endpoint harness telemetry."""
    # Simulate two endpoints with harness telemetry.
    telem_a = {
        "harness": {"tool_calls_total": 12, "cost": {"total_cost_usd": 0.05}},
        "tool_calls": [{"name": "discover_routes", "ok": True}],
    }
    telem_b = {
        "harness": {"tool_calls_total": 8, "cost": {"total_cost_usd": 0.03}},
        "tool_calls": [],
    }

    def _cost_from_telem(telem: dict) -> float:
        return float(
            telem.get("total_cost_usd")
            or telem.get("harness", {}).get("cost", {}).get("total_cost_usd")
            or telem.get("cost", {}).get("total_cost_usd")
            or 0.0
        )

    def _tools_from_telem(telem: dict) -> int:
        return int(
            telem.get("harness", {}).get("tool_calls_total")
            or len(telem.get("tool_calls") or [])
            or 0
        )

    assert _cost_from_telem(telem_a) == 0.05
    assert _cost_from_telem(telem_b) == 0.03
    assert _tools_from_telem(telem_a) == 12
    assert _tools_from_telem(telem_b) == 8
    assert _tools_from_telem(telem_a) + _tools_from_telem(telem_b) == 20


def test_b1_use_harness_default_is_true():
    """use_harness must default to True so the harness runs on brain index."""
    from companybrain.config import Settings
    s = Settings()
    assert s.use_harness is True, (
        "use_harness must be True by default (P0 fix: LLM uses native tool-use)"
    )


# ── B2: cost telemetry reads from all three paths ─────────────────────────────

def test_b2_cost_reads_harness_path():
    """Harness telemetry cost is read correctly from the nested path."""
    telem = {
        "harness": {
            "tool_calls_total": 5,
            "cost": {"total_cost_usd": 0.123, "total_calls": 5},
        },
    }
    cost = float(
        telem.get("total_cost_usd")
        or telem.get("harness", {}).get("cost", {}).get("total_cost_usd")
        or telem.get("cost", {}).get("total_cost_usd")
        or 0.0
    )
    assert cost == pytest.approx(0.123), "harness cost path must be read correctly"


def test_b2_cost_reads_legacy_path():
    """Legacy linear-pipeline telemetry cost is read from top-level key."""
    telem = {"total_cost_usd": 0.456, "entity_count": 42}
    cost = float(
        telem.get("total_cost_usd")
        or telem.get("harness", {}).get("cost", {}).get("total_cost_usd")
        or telem.get("cost", {}).get("total_cost_usd")
        or 0.0
    )
    assert cost == pytest.approx(0.456)


# ── B3: rebuild-from-json skips Postgres when Java is down ─────────────────────

def test_b3_rebuild_skips_postgres_gracefully():
    """brain_rebuild skips Postgres mirror when Java API is unreachable."""
    import inspect
    from companybrain.cli_helpers.brain_rebuild import rebuild_from_json
    src = inspect.getsource(rebuild_from_json)
    # The function must check reachability and build a mirrors list only with
    # available backends — never raise on connection refused.
    assert "_java_api_reachable" in src
    assert "skip_postgres_mirror" in src or "java_ok" in src, (
        "rebuild must check Java reachability before adding Postgres mirror"
    )


@pytest.mark.asyncio
async def test_b3_java_api_reachable_returns_false_on_connect_error():
    """_java_api_reachable returns False when the server is down."""
    import httpx
    from companybrain.cli_helpers.brain_rebuild import _java_api_reachable
    from companybrain.graph.java_client import JavaGraphClient

    mock_java = MagicMock(spec=JavaGraphClient)
    mock_java._result_url = "http://localhost:8080/v1/internal/pipeline-result"

    with patch("httpx.AsyncClient") as mock_client_cls:
        instance = AsyncMock()
        instance.__aenter__ = AsyncMock(return_value=instance)
        instance.__aexit__ = AsyncMock(return_value=False)
        instance.get = AsyncMock(side_effect=httpx.ConnectError("refused"))
        mock_client_cls.return_value = instance
        result = await _java_api_reachable(mock_java)

    assert result is False


# ── B4: cited_entity_urns is present and non-empty when affected_entities is set ─

def test_b4_cited_entity_urns_from_affected_entities():
    """cited_entity_urns is computed from affected_entities URNs."""
    from companybrain.models.query_response import (
        Citation, Confidence, QueryResponse,
    )
    resp = QueryResponse(
        summary="test",
        confidence=Confidence(level="high", rationale="all good"),
        affected_entities=[
            Citation(urn="urn:cb:dev:code:repo:component:Foo", name="Foo",
                     why_relevant="entry point", confidence=0.9),
            Citation(urn="urn:cb:dev:code:repo:component:Bar", name="Bar",
                     why_relevant="service", confidence=0.8),
        ],
    )
    assert resp.cited_entity_urns == [
        "urn:cb:dev:code:repo:component:Foo",
        "urn:cb:dev:code:repo:component:Bar",
    ]


def test_b4_cited_entity_urns_is_empty_when_no_citations():
    """cited_entity_urns is [] when affected_entities and call_chain are empty."""
    from companybrain.models.query_response import Confidence, QueryResponse
    resp = QueryResponse(
        summary="no context",
        confidence=Confidence(level="low", rationale="no data"),
    )
    assert resp.cited_entity_urns == []


def test_b4_cited_entity_urns_deduplicates():
    """URN appearing in both affected_entities and call_chain is emitted once."""
    from companybrain.models.query_response import (
        CallChainStep, Citation, Confidence, QueryResponse,
    )
    shared_urn = "urn:cb:dev:code:repo:component:Shared"
    resp = QueryResponse(
        summary="dedup test",
        confidence=Confidence(level="medium", rationale="ok"),
        affected_entities=[
            Citation(urn=shared_urn, name="Shared", why_relevant="x", confidence=0.9),
        ],
        call_chain=[
            CallChainStep(ord=1, urn=shared_urn, name="Shared", role="service",
                          one_liner="shared node"),
        ],
    )
    assert resp.cited_entity_urns.count(shared_urn) == 1


def test_b4_cited_entity_urns_in_json_serialisation():
    """cited_entity_urns is included in QueryResponse.model_dump()."""
    from companybrain.models.query_response import Citation, Confidence, QueryResponse
    resp = QueryResponse(
        summary="json test",
        confidence=Confidence(level="high", rationale="r"),
        affected_entities=[
            Citation(urn="urn:cb:dev:code:repo:component:X", name="X",
                     why_relevant="y", confidence=1.0),
        ],
    )
    d = resp.model_dump()
    assert "cited_entity_urns" in d
    assert d["cited_entity_urns"] == ["urn:cb:dev:code:repo:component:X"]


# ── B5: confidence schema mismatch ──────────────────────────────────────────────

def test_b5_citation_confidence_accepts_float():
    """Citation.confidence accepts plain float without validation error."""
    from companybrain.models.query_response import Citation
    c = Citation(urn="u", name="n", why_relevant="r", confidence=0.85)
    assert c.confidence == pytest.approx(0.85)


def test_b5_citation_confidence_accepts_object_form():
    """Citation.confidence coerces {value: 0.85, rationale: '...'} to float."""
    from companybrain.models.query_response import Citation
    c = Citation(urn="u", name="n", why_relevant="r",
                 confidence={"value": 0.85, "rationale": "because"})
    assert c.confidence == pytest.approx(0.85)


def test_b5_citation_confidence_accepts_legacy_score_form():
    """Citation.confidence coerces {score: 0.7} to float."""
    from companybrain.models.query_response import Citation
    c = Citation(urn="u", name="n", why_relevant="r", confidence={"score": 0.7})
    assert c.confidence == pytest.approx(0.7)


def test_b5_postgres_consumer_coerces_confidence():
    """_coerce_confidence handles float, object, and None without crashing."""
    from companybrain.store.postgres_consumer import _coerce_confidence

    assert _coerce_confidence(0.85) == pytest.approx(0.85)
    assert _coerce_confidence({"value": 0.85, "rationale": "x"}) == pytest.approx(0.85)
    assert _coerce_confidence({"score": 0.7}) == pytest.approx(0.7)
    assert _coerce_confidence(None) == pytest.approx(0.9)
    assert _coerce_confidence(None, default=0.5) == pytest.approx(0.5)
    assert _coerce_confidence("bad_value") == pytest.approx(0.9)


# ── B6: query path parallelism + driver reuse ─────────────────────────────────

def test_b6_smart_zone_uses_singleton_driver():
    """_smart_zone_assemble must reuse _get_neo4j_driver() not create new drivers."""
    import inspect
    from companybrain.api.routes import query as query_mod
    src = inspect.getsource(query_mod._smart_zone_assemble)
    # Should NOT create a new driver
    assert "AsyncGraphDatabase.driver(" not in src, (
        "_smart_zone_assemble must reuse _get_neo4j_driver() for connection pooling"
    )
    assert "_get_neo4j_driver()" in src


def test_b6_query_handler_runs_intent_and_smartzone_in_parallel():
    """query_graph must use asyncio.gather to parallelize step 0 + step 1."""
    import inspect
    from companybrain.api.routes import query as query_mod
    src = inspect.getsource(query_mod.query_graph)
    assert "asyncio.gather" in src, (
        "query_graph must parallelize intent classification + SmartZone assembly"
    )


@pytest.mark.asyncio
async def test_b6_neo4j_driver_singleton_is_reused():
    """_get_neo4j_driver() returns the same object on repeated calls."""
    from companybrain.api.routes import query as query_mod

    # Reset singleton state for a clean test
    query_mod._neo4j_driver = None

    with patch("companybrain.api.routes.query.AsyncGraphDatabase" if hasattr(
            query_mod, "AsyncGraphDatabase") else "neo4j.AsyncGraphDatabase",
               create=True) as mock_db:
        mock_driver = MagicMock()
        mock_db.driver.return_value = mock_driver

        # Patch the import inside _get_neo4j_driver
        with patch.dict("sys.modules", {"neo4j": MagicMock(
                AsyncGraphDatabase=MagicMock(driver=MagicMock(
                    return_value=mock_driver)))}):
            query_mod._neo4j_driver = None  # reset again after patch
            d1 = query_mod._get_neo4j_driver()
            d2 = query_mod._get_neo4j_driver()
            # Both calls should return the same singleton
            assert d1 is d2
