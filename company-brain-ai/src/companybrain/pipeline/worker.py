"""
ADR-0044 PR-0044-4: Extraction worker and drain loop.

Workers pull one chunk at a time from the extraction_queue, run ChunkExtractor,
and write results back (mark_done / mark_failed).

Exponential backoff on failure: 1s → 5s → 25s, max 3 attempts.
Budget guard: if cumulative job cost_usd >= settings.brain_job_budget_usd,
workers stop claiming new chunks.

drain_queue(job_id, workspace_id, max_workers) runs N coroutines in parallel,
each pulling chunks until the queue is empty. Returns when pending + in_progress == 0.
"""
from __future__ import annotations

import asyncio
import time
from typing import Optional

import structlog

from companybrain.config import settings
from companybrain.pipeline.chunk_extractor import ChunkExtractor, ChunkResult
from companybrain.pipeline.code_chunker import MethodChunk
from companybrain.pipeline.lookup_tool import LookupTool, get_symbol_index
from companybrain.pipeline.queue import (
    QueueChunk,
    ChunkInput,
    claim_next,
    mark_done,
    mark_failed,
    queue_stats,
    job_cost_usd,
)

log = structlog.get_logger(__name__)

_BACKOFF_SECONDS = [1, 5, 25]
_MAX_ATTEMPTS = 3


def _queue_chunk_to_method_chunk(qc: QueueChunk) -> MethodChunk:
    from companybrain.pipeline.code_chunker import MethodChunk as MC
    return MC(
        file_path=qc.file_path,
        qname=qc.qname,
        kind=qc.chunk_kind,  # type: ignore[arg-type]
        body=qc.body,
        header_context=qc.header_context,
        import_context=qc.import_context,
        body_hash=qc.body_hash,
        language="java",  # stored in queue — default fallback; real lang in body_hash context
    )


async def _run_one_worker(
    worker_id: str,
    workspace_id: str,
    job_id: str,
    extractor: ChunkExtractor,
    results: list[ChunkResult],
) -> None:
    """
    One worker loop: claim → extract → mark done/failed → repeat.
    Stops when:
    - No more pending chunks (returns None from claim_next)
    - Budget exhausted
    """
    symbol_index = get_symbol_index()

    while True:
        # Budget guard: check cumulative cost before claiming another chunk
        current_cost = await job_cost_usd(job_id, workspace_id)
        if current_cost >= settings.brain_job_budget_usd:
            log.warning(
                "chunk_worker.budget_exhausted",
                worker=worker_id,
                cost_usd=current_cost,
                limit_usd=settings.brain_job_budget_usd,
            )
            return

        chunk = await claim_next(worker_id, workspace_id, job_id)
        if chunk is None:
            return  # queue empty for this worker

        method_chunk = _queue_chunk_to_method_chunk(chunk)
        lookup = LookupTool(symbol_index)

        attempt = chunk.attempt_count
        backoff = _BACKOFF_SECONDS[min(attempt - 1, len(_BACKOFF_SECONDS) - 1)]

        log.info(
            "chunk_worker.processing",
            worker=worker_id,
            qname=chunk.qname,
            attempt=attempt,
        )

        try:
            result = await extractor.extract(method_chunk, lookup_tool=lookup)
            if result.error:
                raise RuntimeError(result.error)

            await mark_done(
                chunk.id,
                cost_usd=result.cost_usd,
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
            )
            results.append(result)
            log.info(
                "chunk_worker.done",
                worker=worker_id,
                qname=chunk.qname,
                edges=len(result.edges),
            )

        except Exception as exc:
            log.error(
                "chunk_worker.failed",
                worker=worker_id,
                qname=chunk.qname,
                attempt=attempt,
                error=str(exc),
            )
            if attempt >= _MAX_ATTEMPTS:
                await mark_failed(chunk.id, str(exc))
            else:
                # Re-enqueue by resetting to pending for the next worker to pick up
                from companybrain.pipeline import queue as _q
                import asyncpg as _asyncpg
                conn = await _q._get_conn()
                try:
                    await conn.execute(
                        "UPDATE extraction_queue SET status = 'pending' WHERE id = $1",
                        chunk.id,
                    )
                finally:
                    await conn.close()
                await asyncio.sleep(backoff)


async def drain_queue(
    job_id: str,
    workspace_id: str,
    max_workers: int = 4,
) -> list[ChunkResult]:
    """
    Drain all pending chunks for this job using up to max_workers concurrent workers.
    Returns when pending + in_progress == 0.
    """
    extractor = ChunkExtractor()
    results: list[ChunkResult] = []

    workers = [
        _run_one_worker(
            worker_id=f"worker-{i}",
            workspace_id=workspace_id,
            job_id=job_id,
            extractor=extractor,
            results=results,
        )
        for i in range(max_workers)
    ]
    await asyncio.gather(*workers)

    # Verify completion
    stats = await queue_stats(job_id, workspace_id)
    pending = stats.get("pending", 0)
    in_progress = stats.get("in_progress", 0)
    if pending > 0 or in_progress > 0:
        log.warning(
            "drain_queue.incomplete",
            job_id=job_id,
            pending=pending,
            in_progress=in_progress,
        )

    done = stats.get("done", 0)
    failed = stats.get("failed", 0)
    log.info(
        "drain_queue.complete",
        job_id=job_id,
        done=done,
        failed=failed,
        results=len(results),
    )
    return results


def collect_entities_and_edges(
    results: list[ChunkResult],
) -> tuple[list, list]:
    """
    Flatten ChunkResults into entity list + edge list for the merger.
    """
    from companybrain.pipeline.chunk_extractor import ExtractedChunkEntity, ExtractedEdge

    entities: list[ExtractedChunkEntity] = []
    edges: list[ExtractedEdge] = []
    for r in results:
        if r.entity:
            entities.append(r.entity)
        edges.extend(r.edges)
    return entities, edges
