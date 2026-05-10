"""
ADR-0044 PR-0044-4: Worker tests — resume, parallel, budget guard.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from companybrain.pipeline.code_chunker import MethodChunk, _sha256
from companybrain.pipeline.chunk_extractor import ChunkResult, ExtractedChunkEntity, ExtractedEdge
from companybrain.pipeline.worker import collect_entities_and_edges


def _make_method_chunk(qname: str = "Foo.bar") -> MethodChunk:
    body = f"void {qname.split('.')[-1]}() {{}}"
    return MethodChunk(
        file_path="Foo.java",
        qname=qname,
        kind="method",
        body=body,
        header_context="class Foo {",
        import_context="",
        body_hash=_sha256(body),
        language="java",
    )


def _make_queue_chunk(qname: str, attempt: int = 1) -> SimpleNamespace:
    # Mirrors the QueueChunk dataclass shape (companybrain.pipeline.queue).
    # `language` is required by _queue_chunk_to_method_chunk — leaving it off
    # broke these tests when ADR-0047's per-chunk language column landed.
    return SimpleNamespace(
        id=f"id-{qname}",
        workspace_id="ws",
        job_id="job",
        repo="repo",
        file_path="Foo.java",
        qname=qname,
        body_hash=_sha256(qname),
        chunk_kind="method",
        header_context="class Foo {",
        import_context="",
        body=f"void {qname}() {{}}",
        attempt_count=attempt,
        language="java",
    )


def _make_chunk_result(qname: str, error: str | None = None) -> ChunkResult:
    mc = _make_method_chunk(qname)
    entity = None if error else ExtractedChunkEntity(
        entity_type="Function", name=qname, qname=qname,
        file_path="Foo.java", confidence=0.9,
    )
    return ChunkResult(
        chunk=mc, entity=entity, edges=[], error=error,
        cost_usd=0.0001 if not error else 0.0,
    )


# ── collect_entities_and_edges ────────────────────────────────────────────────

def test_collect_entities_skips_failed_chunks():
    results = [
        _make_chunk_result("Foo.a"),
        _make_chunk_result("Foo.b", error="timeout"),
        _make_chunk_result("Foo.c"),
    ]
    entities, edges = collect_entities_and_edges(results)
    assert len(entities) == 2
    qnames = {e.qname for e in entities}
    assert "Foo.a" in qnames
    assert "Foo.c" in qnames
    assert "Foo.b" not in qnames


def test_collect_edges_from_all_chunks():
    mc = _make_method_chunk("Foo.a")
    edge = ExtractedEdge(edge_type="CALLS", target="Bar.baz", confidence=0.9)
    result = ChunkResult(chunk=mc, entity=_make_chunk_result("Foo.a").entity, edges=[edge])
    entities, edges = collect_entities_and_edges([result])
    assert len(edges) == 1
    assert edges[0].edge_type == "CALLS"


# ── Worker drain ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_drain_queue_processes_all_chunks():
    """drain_queue with 4 chunks and 4 workers processes all."""
    queue_chunks = [_make_queue_chunk(f"Foo.method{i}") for i in range(4)]
    call_count = [0]

    claim_returns = list(queue_chunks) + [None, None, None, None]  # one None per worker
    claim_iter = iter(claim_returns)

    async def fake_claim(worker_id, ws, job):
        try:
            return next(claim_iter)
        except StopIteration:
            return None

    async def fake_mark_done(chunk_id, **kwargs):
        call_count[0] += 1

    async def fake_mark_failed(chunk_id, error):
        pass

    async def fake_job_cost(job_id, workspace_id):
        return 0.0

    async def fake_stats(job_id, workspace_id):
        return {"done": 4, "pending": 0, "in_progress": 0}

    mock_extractor = AsyncMock()
    mock_extractor.extract = AsyncMock(side_effect=lambda chunk, **kw: _make_chunk_result(chunk.qname))

    with (
        patch("companybrain.pipeline.worker.claim_next", side_effect=fake_claim),
        patch("companybrain.pipeline.worker.mark_done", side_effect=fake_mark_done),
        patch("companybrain.pipeline.worker.mark_failed", side_effect=fake_mark_failed),
        patch("companybrain.pipeline.worker.job_cost_usd", side_effect=fake_job_cost),
        patch("companybrain.pipeline.worker.queue_stats", side_effect=fake_stats),
        patch("companybrain.pipeline.worker.ChunkExtractor", return_value=mock_extractor),
        patch("companybrain.pipeline.worker.get_symbol_index", return_value=MagicMock()),
    ):
        from companybrain.pipeline.worker import drain_queue
        results = await drain_queue("job", "ws", max_workers=4)

    assert len(results) == 4


@pytest.mark.asyncio
async def test_budget_guard_stops_workers():
    """When job cost >= budget, workers stop claiming."""
    claimed = []

    async def fake_claim(worker_id, ws, job):
        claimed.append(worker_id)
        return _make_queue_chunk("Foo.method")

    async def fake_job_cost(job_id, workspace_id):
        return 999.0  # way over budget

    async def fake_stats(job_id, workspace_id):
        return {"done": 0, "pending": 1, "in_progress": 0}

    with (
        patch("companybrain.pipeline.worker.claim_next", side_effect=fake_claim),
        patch("companybrain.pipeline.worker.job_cost_usd", side_effect=fake_job_cost),
        patch("companybrain.pipeline.worker.queue_stats", side_effect=fake_stats),
        patch("companybrain.pipeline.worker.ChunkExtractor", return_value=AsyncMock()),
        patch("companybrain.pipeline.worker.get_symbol_index", return_value=MagicMock()),
    ):
        from companybrain.pipeline.worker import drain_queue
        results = await drain_queue("job", "ws", max_workers=2)

    assert len(results) == 0  # no chunks processed due to budget
    assert len(claimed) == 0  # claim never called


@pytest.mark.asyncio
async def test_failed_chunk_marks_failed_after_max_attempts():
    """A chunk that always errors is marked failed after _MAX_ATTEMPTS."""
    exhausted_chunk = _make_queue_chunk("Foo.bad", attempt=3)
    failed_ids: list[str] = []
    claim_calls = [0]

    async def fake_claim(worker_id, ws, job):
        if claim_calls[0] == 0:
            claim_calls[0] += 1
            return exhausted_chunk
        return None

    async def fake_mark_failed(chunk_id, error):
        failed_ids.append(chunk_id)

    async def fake_job_cost(job_id, workspace_id):
        return 0.0

    async def fake_stats(job_id, workspace_id):
        return {"done": 0, "failed": 1}

    bad_result = ChunkResult(chunk=_make_method_chunk("Foo.bad"), entity=None, edges=[], error="boom")
    mock_extractor = AsyncMock()
    mock_extractor.extract = AsyncMock(return_value=bad_result)

    with (
        patch("companybrain.pipeline.worker.claim_next", side_effect=fake_claim),
        patch("companybrain.pipeline.worker.mark_done", AsyncMock()),
        patch("companybrain.pipeline.worker.mark_failed", side_effect=fake_mark_failed),
        patch("companybrain.pipeline.worker.job_cost_usd", side_effect=fake_job_cost),
        patch("companybrain.pipeline.worker.queue_stats", side_effect=fake_stats),
        patch("companybrain.pipeline.worker.ChunkExtractor", return_value=mock_extractor),
        patch("companybrain.pipeline.worker.get_symbol_index", return_value=MagicMock()),
    ):
        from companybrain.pipeline.worker import drain_queue
        await drain_queue("job", "ws", max_workers=1)

    assert "id-Foo.bad" in failed_ids
