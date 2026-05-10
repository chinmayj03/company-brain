"""
ADR-0044 PR-0044-1: Extraction queue — Postgres-backed work queue for per-method chunks.

Workers pull rows via SELECT ... FOR UPDATE SKIP LOCKED so multiple coroutines
can drain the queue in parallel without double-processing any chunk.

The UNIQUE constraint on (workspace_id, job_id, file_path, qname, body_hash) makes
enqueue() idempotent: calling it twice with the same chunk is a silent no-op.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import asyncpg
import structlog

from companybrain.config import settings

log = structlog.get_logger(__name__)


@dataclass
class QueueChunk:
    """One row from extraction_queue, as returned by claim_next()."""
    id: str
    workspace_id: str
    job_id: str
    repo: str
    file_path: str
    qname: str
    body_hash: str
    chunk_kind: str
    header_context: str
    import_context: str
    body: str
    attempt_count: int
    language: str = ""
    # ADR-0049 C4: non-None when this row was deduped from a prior run.
    # Workers should skip the LLM call and use this result directly.
    result_json: Optional[str] = None
    source_job_id: Optional[str] = None


@dataclass
class ChunkInput:
    """Caller-supplied data for enqueue()."""
    workspace_id: str
    job_id: str
    repo: str
    file_path: str
    qname: str
    body_hash: str
    chunk_kind: str
    header_context: str
    import_context: str
    body: str
    language: str = ""
    filter_reason: str = ""  # non-empty → row is pre-filtered, status='filtered'


async def _get_conn() -> asyncpg.Connection:
    """Open a raw asyncpg connection from the DATABASE_URL."""
    dsn = settings.database_url.replace(
        "postgresql+asyncpg://", "postgresql://"
    )
    return await asyncpg.connect(dsn)


async def enqueue(chunks: list[ChunkInput]) -> int:
    """
    Insert chunks into the queue; skip duplicates (ON CONFLICT DO NOTHING).
    Chunks with a non-empty filter_reason are inserted with status='filtered'.

    ADR-0049 C4: cross-job dedup — if a chunk with the same body_hash was
    already processed successfully in ANY prior run for this workspace, copy
    its result_json and mark the new row 'done' immediately (zero LLM cost).

    Returns the number of rows actually inserted (filtered + pending + deduped).
    """
    if not chunks:
        return 0

    conn = await _get_conn()
    try:
        inserted = 0
        filtered_count = 0
        deduped_count = 0
        for c in chunks:
            if c.filter_reason:
                result = await conn.execute(
                    """
                    INSERT INTO extraction_queue
                        (workspace_id, job_id, repo, file_path, qname, body_hash,
                         chunk_kind, header_context, import_context, body,
                         language, filter_reason, status)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, 'filtered')
                    ON CONFLICT (workspace_id, job_id, file_path, qname, body_hash)
                    DO NOTHING
                    """,
                    c.workspace_id, c.job_id, c.repo, c.file_path,
                    c.qname, c.body_hash, c.chunk_kind,
                    c.header_context, c.import_context, c.body,
                    c.language or "", c.filter_reason or "",
                )
                n = int(result.split()[-1])
                inserted += n
                if n:
                    filtered_count += 1
                continue

            # ADR-0049 C4: look for a previously-done row with same body_hash.
            existing = await conn.fetchrow(
                """
                SELECT result_json FROM extraction_queue
                WHERE workspace_id = $1 AND body_hash = $2
                  AND status = 'done' AND result_json IS NOT NULL
                ORDER BY processed_at DESC NULLS LAST, finished_at DESC NULLS LAST
                LIMIT 1
                """,
                c.workspace_id, c.body_hash,
            )
            if existing and existing["result_json"]:
                # Reuse: insert as already-done with source_job_id for lineage.
                result = await conn.execute(
                    """
                    INSERT INTO extraction_queue
                        (workspace_id, job_id, repo, file_path, qname, body_hash,
                         chunk_kind, header_context, import_context, body,
                         language, filter_reason, status, result_json, source_job_id,
                         finished_at)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, '', 'done',
                            $12, $13, now())
                    ON CONFLICT (workspace_id, job_id, file_path, qname, body_hash)
                    DO NOTHING
                    """,
                    c.workspace_id, c.job_id, c.repo, c.file_path,
                    c.qname, c.body_hash, c.chunk_kind,
                    c.header_context, c.import_context, c.body,
                    c.language or "",
                    existing["result_json"], c.job_id,
                )
                n = int(result.split()[-1])
                inserted += n
                if n:
                    deduped_count += 1
                continue

            # Normal insert as 'pending'.
            result = await conn.execute(
                """
                INSERT INTO extraction_queue
                    (workspace_id, job_id, repo, file_path, qname, body_hash,
                     chunk_kind, header_context, import_context, body,
                     language, filter_reason, status)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, '', 'pending')
                ON CONFLICT (workspace_id, job_id, file_path, qname, body_hash)
                DO NOTHING
                """,
                c.workspace_id, c.job_id, c.repo, c.file_path,
                c.qname, c.body_hash, c.chunk_kind,
                c.header_context, c.import_context, c.body,
                c.language or "",
            )
            n = int(result.split()[-1])
            inserted += n

        log.info("extraction_queue.enqueue",
                 total=len(chunks), inserted=inserted,
                 filtered=filtered_count,
                 deduped=deduped_count,
                 skipped=len(chunks) - inserted)
        return inserted
    finally:
        await conn.close()


async def claim_next(worker_id: str, workspace_id: str, job_id: str) -> Optional[QueueChunk]:
    """
    Claim one pending row for this worker.
    Uses SELECT ... FOR UPDATE SKIP LOCKED so parallel workers never double-claim.
    Returns None when the queue is empty for this job.
    Filtered rows (status='filtered') are never claimed.
    """
    conn = await _get_conn()
    try:
        async with conn.transaction():
            row = await conn.fetchrow(
                """
                SELECT id, workspace_id, job_id, repo, file_path, qname,
                       body_hash, chunk_kind, header_context, import_context,
                       body, attempt_count, language, result_json, source_job_id
                FROM extraction_queue
                WHERE workspace_id = $1
                  AND job_id = $2
                  AND status = 'pending'
                ORDER BY created_at
                LIMIT 1
                FOR UPDATE SKIP LOCKED
                """,
                workspace_id, job_id,
            )
            if row is None:
                return None

            await conn.execute(
                """
                UPDATE extraction_queue
                SET status = 'in_progress',
                    started_at = now(),
                    attempt_count = attempt_count + 1
                WHERE id = $1
                """,
                row["id"],
            )
            return QueueChunk(
                id=str(row["id"]),
                workspace_id=str(row["workspace_id"]),
                job_id=str(row["job_id"]),
                repo=row["repo"],
                file_path=row["file_path"],
                qname=row["qname"],
                body_hash=row["body_hash"],
                chunk_kind=row["chunk_kind"],
                header_context=row["header_context"],
                import_context=row["import_context"],
                body=row["body"],
                attempt_count=row["attempt_count"] + 1,
                language=row["language"] or "",
                result_json=row["result_json"],
                source_job_id=str(row["source_job_id"]) if row["source_job_id"] else None,
            )
    finally:
        await conn.close()


async def mark_filtered(chunk_id: str, reason: str) -> None:
    """Mark a pre-filtered chunk — it was never sent to the LLM."""
    conn = await _get_conn()
    try:
        await conn.execute(
            """
            UPDATE extraction_queue
            SET status = 'filtered',
                finished_at = now(),
                filter_reason = $2
            WHERE id = $1
            """,
            chunk_id, reason[:500],
        )
    finally:
        await conn.close()


async def mark_done(
    chunk_id: str,
    cost_usd: float = 0.0,
    input_tokens: int = 0,
    output_tokens: int = 0,
    result_json: Optional[str] = None,
) -> None:
    """Mark a chunk as successfully processed.

    ADR-0049 C4: result_json is stored so future runs can reuse it without
    calling the LLM (cross-job dedup in enqueue()).
    finished_at doubles as processed_at for the dedup ORDER BY.
    """
    conn = await _get_conn()
    try:
        await conn.execute(
            """
            UPDATE extraction_queue
            SET status = 'done',
                finished_at = now(),
                cost_usd = $2,
                input_tokens = $3,
                output_tokens = $4,
                result_json = $5
            WHERE id = $1
            """,
            chunk_id, cost_usd, input_tokens, output_tokens, result_json,
        )
    finally:
        await conn.close()


async def mark_failed(chunk_id: str, error: str) -> None:
    """Mark a chunk as failed; caller decides whether to retry."""
    conn = await _get_conn()
    try:
        await conn.execute(
            """
            UPDATE extraction_queue
            SET status = 'failed',
                finished_at = now(),
                last_error = $2
            WHERE id = $1
            """,
            chunk_id, error[:2000],
        )
    finally:
        await conn.close()


async def retry_failed(job_id: str, workspace_id: str) -> int:
    """
    Reset all failed rows for this job back to 'pending' so workers can retry.
    Returns the number of rows reset.
    """
    conn = await _get_conn()
    try:
        result = await conn.execute(
            """
            UPDATE extraction_queue
            SET status = 'pending',
                started_at = NULL,
                finished_at = NULL,
                last_error = NULL
            WHERE job_id = $1
              AND workspace_id = $2
              AND status = 'failed'
            """,
            job_id, workspace_id,
        )
        count = int(result.split()[-1])
        log.info("extraction_queue.retry_failed", job_id=job_id, reset=count)
        return count
    finally:
        await conn.close()


async def queue_stats(job_id: str, workspace_id: str) -> dict:
    """Return counts by status for this job — used by drain_queue to detect completion."""
    conn = await _get_conn()
    try:
        rows = await conn.fetch(
            """
            SELECT status, count(*) AS n
            FROM extraction_queue
            WHERE job_id = $1 AND workspace_id = $2
            GROUP BY status
            """,
            job_id, workspace_id,
        )
        return {row["status"]: row["n"] for row in rows}
    finally:
        await conn.close()


async def job_cost_usd(job_id: str, workspace_id: str) -> float:
    """Sum of cost_usd for all done rows in this job — used for budget enforcement."""
    conn = await _get_conn()
    try:
        row = await conn.fetchrow(
            """
            SELECT COALESCE(SUM(cost_usd), 0) AS total
            FROM extraction_queue
            WHERE job_id = $1 AND workspace_id = $2
            """,
            job_id, workspace_id,
        )
        return float(row["total"])
    finally:
        await conn.close()


# ── CLI entry point ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import asyncio, sys

    async def _demo():
        sample = ChunkInput(
            workspace_id="00000000-0000-0000-0000-000000000001",
            job_id="00000000-0000-0000-0000-000000000002",
            repo="demo-repo",
            file_path="src/Demo.java",
            qname="Demo.hello",
            body_hash="abc123",
            chunk_kind="method",
            header_context="class Demo {",
            import_context="import java.util.*;",
            body="void hello() { System.out.println(\"hi\"); }",
        )
        n = await enqueue([sample])
        print(f"Inserted {n} rows")
        chunk = await claim_next("worker-0", sample.workspace_id, sample.job_id)
        print(f"Claimed: {chunk}")
        if chunk:
            await mark_done(chunk.id, cost_usd=0.0001, input_tokens=100, output_tokens=50)

    asyncio.run(_demo())
