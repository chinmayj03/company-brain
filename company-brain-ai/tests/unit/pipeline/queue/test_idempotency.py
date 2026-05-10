"""
ADR-0044 PR-0044-1: Queue idempotency tests.

These tests use an in-memory fake to verify the UNIQUE constraint logic
without requiring a live Postgres instance. Integration tests that hit real
Postgres live in tests/integration/.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from companybrain.pipeline.queue import ChunkInput


def _make_chunk(**kwargs) -> ChunkInput:
    defaults = dict(
        workspace_id="ws-1",
        job_id="job-1",
        repo="repo",
        file_path="Foo.java",
        qname="Foo.bar",
        body_hash="hash-abc",
        chunk_kind="method",
        header_context="class Foo {",
        import_context="",
        body="void bar() {}",
    )
    defaults.update(kwargs)
    return ChunkInput(**defaults)


class _FakeQueue:
    """
    In-memory queue that enforces the UNIQUE (workspace_id, job_id, file_path,
    qname, body_hash) constraint, mirroring Postgres behaviour.
    """

    def __init__(self):
        self._rows: dict[tuple, dict] = {}
        self._statuses: dict[str, str] = {}
        import uuid
        self._uuid = uuid

    def _key(self, c: ChunkInput) -> tuple:
        return (c.workspace_id, c.job_id, c.file_path, c.qname, c.body_hash)

    def insert(self, c: ChunkInput) -> int:
        k = self._key(c)
        if k in self._rows:
            return 0  # ON CONFLICT DO NOTHING
        row_id = str(self._uuid.uuid4())
        self._rows[k] = {"id": row_id, "status": "pending", **c.__dict__}
        self._statuses[row_id] = "pending"
        return 1

    def all_statuses(self) -> list[str]:
        return [r["status"] for r in self._rows.values()]


def test_duplicate_enqueue_is_noop():
    """Enqueuing the same chunk twice inserts only one row."""
    q = _FakeQueue()
    chunk = _make_chunk()

    n1 = q.insert(chunk)
    n2 = q.insert(chunk)

    assert n1 == 1
    assert n2 == 0
    assert len(q._rows) == 1


def test_different_body_hash_is_new_row():
    """Same file+qname but different body → two distinct rows."""
    q = _FakeQueue()
    c1 = _make_chunk(body_hash="hash-v1")
    c2 = _make_chunk(body_hash="hash-v2")

    n1 = q.insert(c1)
    n2 = q.insert(c2)

    assert n1 == 1
    assert n2 == 1
    assert len(q._rows) == 2


def test_different_qname_is_new_row():
    """Same file, different method → two rows."""
    q = _FakeQueue()
    c1 = _make_chunk(qname="Foo.a", body_hash="h1")
    c2 = _make_chunk(qname="Foo.b", body_hash="h2")

    assert q.insert(c1) == 1
    assert q.insert(c2) == 1
    assert len(q._rows) == 2


def test_enqueue_many_deduplicates():
    """Bulk enqueue with duplicates returns only unique rows."""
    q = _FakeQueue()
    chunks = [
        _make_chunk(qname="Foo.a", body_hash="h1"),
        _make_chunk(qname="Foo.b", body_hash="h2"),
        _make_chunk(qname="Foo.a", body_hash="h1"),  # duplicate
    ]
    total = sum(q.insert(c) for c in chunks)
    assert total == 2
    assert len(q._rows) == 2


@pytest.mark.asyncio
async def test_enqueue_function_calls_db_correctly():
    """enqueue() calls INSERT for each chunk and counts inserted rows."""
    from companybrain.pipeline.queue import enqueue

    mock_conn = AsyncMock()
    mock_conn.execute = AsyncMock(side_effect=["INSERT 0 1", "INSERT 0 0"])

    with patch("companybrain.pipeline.queue._get_conn", return_value=mock_conn):
        chunks = [
            _make_chunk(qname="Foo.a", body_hash="h1"),
            _make_chunk(qname="Foo.a", body_hash="h1"),
        ]
        inserted = await enqueue(chunks)

    assert inserted == 1  # second was DO NOTHING → 0
    assert mock_conn.execute.call_count == 2
    mock_conn.close.assert_called_once()
