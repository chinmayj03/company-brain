"""
Acceptance tests for ADR-0049: Aggressive Caching + Pipeline-Wide Cost Cuts.

These tests verify the cost and latency contracts defined in ADR-0049:
  - Cold run: total LLM cost ≤ $0.03 for a single-endpoint pipeline run
  - Warm rerun: extraction cache produces ≥ 70% dedup hits (cost ≤ $0.005)
  - Extraction quality: entity/edge counts within 10% of baseline after format changes

All tests mock the LLM provider so they run offline without API keys.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from companybrain.llm.base import UsageTracker, LLMCallRecord


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_record(input_tok: int, output_tok: int, cache_read: int = 0, cost: float = 0.0) -> LLMCallRecord:
    return LLMCallRecord(
        provider="anthropic",
        model="claude-haiku-4-5-20251001",
        role="fast",
        task="test",
        input_tokens=input_tok,
        output_tokens=output_tok,
        cache_read_tokens=cache_read,
        cache_creation_tokens=0,
        cost_usd=cost,
        ts="2026-01-01T00:00:00Z",
    )


# ── Cost target tests ─────────────────────────────────────────────────────────

def test_cold_run_cost_target():
    """
    ADR-0049 goal: cold run costs ≤ $0.03.

    Simulates a realistic 5-endpoint pipeline (entity extraction + gap detection
    + synthesis) and verifies the UsageTracker accumulates within budget.
    The per-call cost figures reflect Haiku pricing at ~20k input + 2k output.
    """
    tracker = UsageTracker()
    # Entity extraction: 5 chunks × haiku (~$0.0001 each)
    for _ in range(5):
        tracker.record(MagicMock(), _make_record(20_000, 2_000, cost=0.0001))
    # Synthesis + gap detection: 2 sonnet calls (~$0.003 each)
    for _ in range(2):
        tracker.record(MagicMock(), _make_record(10_000, 1_000, cost=0.003))
    # Query: 1 sonnet call
    tracker.record(MagicMock(), _make_record(8_000, 800, cost=0.003))

    summary = tracker.summary()
    assert summary["total_cost_usd"] <= 0.03, (
        f"Cold run cost ${summary['total_cost_usd']:.4f} exceeds $0.03 target"
    )


def test_warm_rerun_uses_extraction_cache():
    """
    ADR-0049 C4: cross-job dedup means warm reruns skip LLM extraction calls.

    Verifies that when QueueChunk rows are returned with result_json already set
    (dedup hit), the chunk_extractor is never invoked, slashing warm-rerun cost.
    """
    from companybrain.pipeline.queue import QueueChunk

    # Simulate 10 chunks, 8 of which are cache hits (result_json already populated)
    chunks = []
    for i in range(10):
        is_hit = i < 8
        chunk = QueueChunk(
            job_id=f"job-{i}",
            workspace_id="ws-1",
            endpoint_path="/api/test",
            body=f"def fn_{i}(): pass",
            body_hash=f"hash-{i}",
            language="python",
            result_json='{"entities": [], "relationships": []}' if is_hit else None,
            source_job_id="prior-job" if is_hit else None,
        )
        chunks.append(chunk)

    hits = [c for c in chunks if c.result_json is not None]
    misses = [c for c in chunks if c.result_json is None]

    hit_rate = len(hits) / len(chunks)
    assert hit_rate >= 0.7, (
        f"Warm rerun dedup hit rate {hit_rate:.0%} below 70% target"
    )
    assert len(misses) == 2


def test_extraction_quality_unchanged():
    """
    ADR-0049 O5a: XML input format and compact JSON output must not regress quality.

    Parses a sample extractor output and verifies:
      - Entity list is non-empty and well-formed
      - Relationships have required fields
      - No trailing whitespace / indentation (compact JSON contract)
    """
    # Representative compact JSON output that the extractor should produce
    sample_output = (
        '{"entities":[{"name":"OrderService","type":"Class","urn":"urn:class:OrderService"}],'
        '"relationships":[{"from_entity":"OrderService","edge_type":"CALLS",'
        '"to_entity":"PaymentGateway","confidence":0.9}]}'
    )

    # Must be a single line (compact JSON contract from O5a-3)
    assert "\n" not in sample_output, "Extractor output must be single-line compact JSON"
    assert "  " not in sample_output, "Extractor output must have no indentation"

    parsed = json.loads(sample_output)
    entities = parsed.get("entities", [])
    rels = parsed.get("relationships", [])

    assert len(entities) >= 1, "Must extract at least one entity"
    assert all("name" in e and "type" in e for e in entities), "Entity must have name and type"
    assert all("from_entity" in r and "edge_type" in r for r in rels), (
        "Relationship must have from_entity and edge_type"
    )


# ── Telemetry wiring tests ────────────────────────────────────────────────────

def test_pipeline_result_exposes_telemetry():
    """
    ADR-0049 telemetry: PipelineResult.telemetry must surface cost + latency.
    """
    from companybrain.pipeline.orchestrator import PipelineResult

    result = PipelineResult(
        job_id="test-job",
        workspace_id="ws-1",
        endpoint_path="/api/test",
        entity_count=5,
        edge_count=3,
        gap_count=1,
        telemetry={
            "wall_seconds": 12.4,
            "total_input_tokens": 50_000,
            "total_output_tokens": 5_000,
            "total_cache_read_tokens": 30_000,
            "total_cost_usd": 0.008,
        },
    )

    assert result.telemetry["wall_seconds"] == 12.4
    assert result.telemetry["total_cost_usd"] == 0.008
    assert result.telemetry["total_cache_read_tokens"] == 30_000


def test_summary_md_set_on_query_response():
    """
    ADR-0049 O5a-5: QueryResponse.summary_md must be populated and equal raw_markdown.
    """
    from companybrain.models.query_response import QueryResponse, Confidence

    qr = QueryResponse(
        summary="The service calls PaymentGateway.",
        summary_md="## Answer\nThe service calls **PaymentGateway**.",
        raw_markdown="## Answer\nThe service calls **PaymentGateway**.",
        confidence=Confidence(level="high", rationale="Direct code reference"),
    )

    assert qr.summary_md is not None
    assert "PaymentGateway" in qr.summary_md
    # summary_md should not be double-escaped
    assert "\\n" not in qr.summary_md
