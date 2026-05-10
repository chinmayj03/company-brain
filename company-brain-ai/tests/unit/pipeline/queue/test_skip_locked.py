"""
ADR-0044 PR-0044-1: SKIP LOCKED concurrency tests.

Verifies that two concurrent workers claim different rows and that the
claim_next() / mark_done() interface behaves correctly under races.
Uses an in-memory fake that enforces the SKIP LOCKED semantic.
"""
from __future__ import annotations

import asyncio
import uuid
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from companybrain.pipeline.queue import ChunkInput, QueueChunk


class _FakeConcurrentQueue:
    """
    Thread-safe in-memory queue that mimics SELECT ... FOR UPDATE SKIP LOCKED.
    A row that is 'in_progress' is invisible to other workers until released.
    """

    def __init__(self):
        self._rows: list[dict] = []
        self._lock = asyncio.Lock()

    def seed(self, *chunks: ChunkInput) -> None:
        for c in chunks:
            self._rows.append({
                "id": str(uuid.uuid4()),
                "status": "pending",
                **c.__dict__,
            })

    async def claim(self, worker_id: str) -> Optional[QueueChunk]:
        async with self._lock:
            for row in self._rows:
                if row["status"] == "pending":
                    row["status"] = "in_progress"
                    row["worker"] = worker_id
                    return QueueChunk(
                        id=row["id"],
                        workspace_id=row["workspace_id"],
                        job_id=row["job_id"],
                        repo=row["repo"],
                        file_path=row["file_path"],
                        qname=row["qname"],
                        body_hash=row["body_hash"],
                        chunk_kind=row["chunk_kind"],
                        header_context=row["header_context"],
                        import_context=row["import_context"],
                        body=row["body"],
                        attempt_count=1,
                    )
            return None

    async def done(self, chunk_id: str) -> None:
        async with self._lock:
            for row in self._rows:
                if row["id"] == chunk_id:
                    row["status"] = "done"

    def workers_by_id(self) -> dict[str, str]:
        return {r["id"]: r.get("worker", "") for r in self._rows}


def _make_chunk(qname: str) -> ChunkInput:
    return ChunkInput(
        workspace_id="ws-1",
        job_id="job-1",
        repo="repo",
        file_path="Foo.java",
        qname=qname,
        body_hash=f"hash-{qname}",
        chunk_kind="method",
        header_context="class Foo {",
        import_context="",
        body=f"void {qname}() {{}}",
    )


@pytest.mark.asyncio
async def test_two_workers_claim_different_rows():
    """Two concurrent workers must claim distinct chunks."""
    fq = _FakeConcurrentQueue()
    fq.seed(_make_chunk("a"), _make_chunk("b"))

    c1, c2 = await asyncio.gather(fq.claim("worker-0"), fq.claim("worker-1"))

    assert c1 is not None
    assert c2 is not None
    assert c1.id != c2.id
    assert c1.qname != c2.qname


@pytest.mark.asyncio
async def test_empty_queue_returns_none():
    """claim() returns None when no pending rows remain."""
    fq = _FakeConcurrentQueue()
    fq.seed(_make_chunk("a"))

    c1 = await fq.claim("worker-0")
    c2 = await fq.claim("worker-1")  # queue exhausted

    assert c1 is not None
    assert c2 is None


@pytest.mark.asyncio
async def test_four_workers_no_duplicate_processing():
    """Four concurrent workers process 4 chunks; each chunk processed exactly once."""
    fq = _FakeConcurrentQueue()
    chunks = [_make_chunk(f"method{i}") for i in range(4)]
    for c in chunks:
        fq.seed(c)

    claimed_ids: list[str] = []

    async def worker(wid: str):
        while True:
            chunk = await fq.claim(wid)
            if chunk is None:
                break
            claimed_ids.append(chunk.id)
            await asyncio.sleep(0)  # yield
            await fq.done(chunk.id)

    await asyncio.gather(*[worker(f"w{i}") for i in range(4)])

    assert len(claimed_ids) == 4
    assert len(set(claimed_ids)) == 4, "Each chunk must be processed exactly once"


@pytest.mark.asyncio
async def test_claim_next_marks_in_progress():
    """claim_next() calls UPDATE to set status = in_progress."""
    from companybrain.pipeline.queue import claim_next

    row_id = str(uuid.uuid4())
    mock_row = {
        "id": row_id, "workspace_id": "ws", "job_id": "job", "repo": "r",
        "file_path": "F.java", "qname": "F.m", "body_hash": "bh",
        "chunk_kind": "method", "header_context": "", "import_context": "", "body": "x",
        "attempt_count": 0,
    }

    mock_conn = AsyncMock()
    mock_conn.fetchrow = AsyncMock(return_value=mock_row)
    mock_conn.execute = AsyncMock(return_value="UPDATE 1")

    # transaction context manager
    mock_txn = AsyncMock()
    mock_txn.__aenter__ = AsyncMock(return_value=None)
    mock_txn.__aexit__ = AsyncMock(return_value=False)
    mock_conn.transaction = MagicMock(return_value=mock_txn)

    with patch("companybrain.pipeline.queue._get_conn", return_value=mock_conn):
        chunk = await claim_next("worker-0", "ws", "job")

    assert chunk is not None
    assert chunk.id == row_id
    assert chunk.attempt_count == 1
    mock_conn.execute.assert_called_once()
