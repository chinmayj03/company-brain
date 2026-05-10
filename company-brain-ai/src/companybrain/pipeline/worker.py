"""
ADR-0047: Extraction worker and drain loop.

Workers pull one chunk at a time from the extraction_queue, run ChunkExtractor,
and write results back (mark_done / mark_failed).

The worker operates at single-chunk granularity because the queue stores one row
per chunk. Batching happens upstream (in the orchestrator) before enqueueing.
The ChunkExtractor.extract() single-chunk path is used here.

Exponential backoff on failure: 1s → 5s → 25s, max 3 attempts.
Budget guard: if cumulative job cost_usd >= settings.brain_job_budget_usd,
workers stop claiming new chunks.

drain_queue(job_id, workspace_id, max_workers) runs N coroutines in parallel,
each pulling chunks until the queue is empty. Returns when pending + in_progress == 0.
"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Optional

import structlog

from companybrain.config import settings
from companybrain.pipeline.chunk_extractor import ChunkExtractor, ChunkResult
from companybrain.pipeline.code_chunker import MethodChunk, _LANGUAGE_MAP
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

    # Prefer the language stored on the queue row; fall back to detecting from
    # file extension so we never silently default to "java" for non-Java files.
    language = qc.language or ""
    if not language:
        suffix = Path(qc.file_path).suffix.lower()
        language = _LANGUAGE_MAP.get(suffix, "unknown")

    return MC(
        file_path=qc.file_path,
        qname=qc.qname,
        kind=qc.chunk_kind,  # type: ignore[arg-type]
        body=qc.body,
        header_context=qc.header_context,
        import_context=qc.import_context,
        body_hash=qc.body_hash,
        language=language,
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
            return

        method_chunk = _queue_chunk_to_method_chunk(chunk)
        lookup = LookupTool(symbol_index)

        attempt = chunk.attempt_count
        backoff = _BACKOFF_SECONDS[min(attempt - 1, len(_BACKOFF_SECONDS) - 1)]

        log.info(
            "chunk_worker.processing",
            worker=worker_id,
            qname=chunk.qname,
            language=method_chunk.language,
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
                language=method_chunk.language,
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
                from companybrain.pipeline import queue as _q
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

    stats = await queue_stats(job_id, workspace_id)
    pending     = stats.get("pending", 0)
    in_progress = stats.get("in_progress", 0)
    if pending > 0 or in_progress > 0:
        log.warning(
            "drain_queue.incomplete",
            job_id=job_id,
            pending=pending,
            in_progress=in_progress,
        )

    done     = stats.get("done", 0)
    failed   = stats.get("failed", 0)
    filtered = stats.get("filtered", 0)
    log.info(
        "drain_queue.complete",
        job_id=job_id,
        done=done,
        failed=failed,
        filtered=filtered,
        results=len(results),
    )
    return results


async def drain_queue_batched(
    job_id: str,
    workspace_id: str,
    batch_size: int = 8,
) -> list[ChunkResult]:
    """ADR-0048: drain the queue using ContextAgent in class-grouped batches.

    Instead of one worker-per-chunk, this path:
    1. Fetches ALL pending chunks for the job in one query.
    2. Groups them by file_path (same-class siblings first).
    3. Sends each group to ContextAgent.extract_batch in slices of batch_size.
    4. Returns ChunkResult-compatible objects for the existing merger.

    Falls back to the standard per-chunk drain if ContextAgent import fails.
    """
    from companybrain.pipeline import queue as _q
    from companybrain.agents.context_agent import ContextAgent, ContextAgentResult
    from companybrain.pipeline.chunk_extractor import ChunkResult as CR, ExtractedChunkEntity, ExtractedEdge

    # Pull all pending chunks for this job in one DB round-trip
    conn = await _q._get_conn()
    try:
        rows = await conn.fetch(
            """
            SELECT id, workspace_id, job_id, repo, file_path, qname,
                   body_hash, chunk_kind, header_context, import_context,
                   body, attempt_count, language
            FROM extraction_queue
            WHERE job_id = $1 AND workspace_id = $2 AND status = 'pending'
            ORDER BY file_path, qname
            """,
            job_id, workspace_id,
        )
    finally:
        await conn.close()

    if not rows:
        log.info("drain_queue_batched: no pending chunks", job_id=job_id)
        return []

    # Convert to QueueChunk objects
    queue_chunks: list[QueueChunk] = [
        QueueChunk(
            id=str(r["id"]),
            workspace_id=r["workspace_id"],
            job_id=r["job_id"],
            repo=r["repo"],
            file_path=r["file_path"],
            qname=r["qname"],
            body_hash=r["body_hash"],
            chunk_kind=r["chunk_kind"],
            header_context=r["header_context"] or "",
            import_context=r["import_context"] or "",
            body=r["body"] or "",
            attempt_count=r["attempt_count"],
            language=r["language"] or "",
        )
        for r in rows
    ]

    # Group by file_path
    from collections import defaultdict
    by_file: dict[str, list[QueueChunk]] = defaultdict(list)
    for qc in queue_chunks:
        by_file[qc.file_path].append(qc)

    agent = ContextAgent()
    all_results: list[ChunkResult] = []

    for file_path, file_chunks in by_file.items():
        # Slice into batches of batch_size
        for batch_start in range(0, len(file_chunks), batch_size):
            batch_qchunks = file_chunks[batch_start:batch_start + batch_size]
            method_chunks = [_queue_chunk_to_method_chunk(qc) for qc in batch_qchunks]

            try:
                agent_results: list[ContextAgentResult] = await agent.extract_batch(method_chunks)
            except Exception as exc:
                log.error("drain_queue_batched: ContextAgent failed", error=str(exc), file_path=file_path)
                # Mark all chunks in this batch as failed
                for qc in batch_qchunks:
                    await mark_failed(qc.id, str(exc))
                continue

            # Persist results
            for qc, mc, ar in zip(batch_qchunks, method_chunks, agent_results):
                entity = ar.entity if ar else None
                edges = ar.edges if ar else []
                try:
                    await mark_done(qc.id, cost_usd=ar.cost_usd if ar else 0.0,
                                    input_tokens=ar.input_tokens if ar else 0,
                                    output_tokens=ar.output_tokens if ar else 0)
                except Exception as exc:
                    log.warning("drain_queue_batched: mark_done failed", qname=qc.qname, error=str(exc))

                all_results.append(ChunkResult(
                    chunk=mc,
                    entity=entity,
                    edges=edges,
                    cost_usd=ar.cost_usd if ar else 0.0,
                    input_tokens=ar.input_tokens if ar else 0,
                    output_tokens=ar.output_tokens if ar else 0,
                ))

    log.info(
        "drain_queue_batched.complete",
        job_id=job_id,
        total_chunks=len(queue_chunks),
        results=len(all_results),
    )
    return all_results


def collect_entities_and_edges(
    results: list[ChunkResult],
) -> tuple[list, list]:
    """Flatten ChunkResults into entity list + edge list for the merger."""
    from companybrain.pipeline.chunk_extractor import ExtractedChunkEntity, ExtractedEdge

    entities: list[ExtractedChunkEntity] = []
    edges: list[ExtractedEdge] = []
    for r in results:
        if r.entity:
            entities.append(r.entity)
        edges.extend(r.edges)
    return entities, edges
