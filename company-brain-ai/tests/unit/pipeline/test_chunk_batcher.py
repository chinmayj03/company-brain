"""
ADR-0047: ChunkBatcher unit tests.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from companybrain.pipeline.chunk_batcher import ChunkBatcher, ChunkBatch, SMALL_BODY_CHARS, MAX_BATCH_SIZE
from companybrain.pipeline.code_chunker import MethodChunk, _sha256


def _make_chunk(
    qname: str,
    body: str = "x" * 100,
    kind: str = "method",
) -> MethodChunk:
    return MethodChunk(
        file_path="Foo.java",
        qname=qname,
        kind=kind,  # type: ignore[arg-type]
        body=body,
        header_context="",
        import_context="",
        body_hash=_sha256(body),
        language="java",
    )


def _small(qname: str) -> MethodChunk:
    return _make_chunk(qname, body="x" * (SMALL_BODY_CHARS - 1))


def _large(qname: str) -> MethodChunk:
    return _make_chunk(qname, body="x" * SMALL_BODY_CHARS)


class TestChunkBatcher:

    def test_empty_input(self):
        assert ChunkBatcher().batch([]) == []

    def test_single_small_chunk_is_solo_batch(self):
        batches = ChunkBatcher().batch([_small("Foo.bar")])
        assert len(batches) == 1
        assert not batches[0].is_batched
        assert batches[0].rationale == "single chunk"

    def test_single_large_chunk_is_solo_batch(self):
        batches = ChunkBatcher().batch([_large("Foo.bar")])
        assert len(batches) == 1
        assert not batches[0].is_batched
        assert "large" in batches[0].rationale

    def test_two_small_same_class_grouped(self):
        chunks = [_small("Foo.a"), _small("Foo.b")]
        batches = ChunkBatcher().batch(chunks)
        assert len(batches) == 1
        assert batches[0].is_batched
        assert len(batches[0].chunks) == 2
        assert "grouped" in batches[0].rationale

    def test_class_boundary_flushes_group(self):
        chunks = [_small("Foo.a"), _small("Foo.b"), _small("Bar.c")]
        batches = ChunkBatcher().batch(chunks)
        assert len(batches) == 2
        assert len(batches[0].chunks) == 2   # Foo.a + Foo.b
        assert len(batches[1].chunks) == 1   # Bar.c

    def test_large_chunk_flushes_pending_then_goes_solo(self):
        chunks = [_small("Foo.a"), _large("Foo.b"), _small("Foo.c")]
        batches = ChunkBatcher().batch(chunks)
        # Foo.a → solo (flushed when large arrives), Foo.b → solo (large), Foo.c → solo
        assert len(batches) == 3
        assert "large" in batches[1].rationale

    def test_max_batch_size_respected(self):
        chunks = [_small(f"Foo.m{i}") for i in range(MAX_BATCH_SIZE + 2)]
        batches = ChunkBatcher().batch(chunks)
        for b in batches:
            assert len(b.chunks) <= MAX_BATCH_SIZE

    def test_all_chunks_covered(self):
        chunks = (
            [_small("A.x"), _small("A.y")]
            + [_large("B.big")]
            + [_small("C.p"), _small("C.q"), _small("C.r")]
        )
        batches = ChunkBatcher().batch(chunks)
        flat = [c for b in batches for c in b.chunks]
        assert flat == chunks

    def test_rationale_includes_count_for_grouped(self):
        chunks = [_small("Foo.a"), _small("Foo.b"), _small("Foo.c")]
        batches = ChunkBatcher().batch(chunks)
        assert len(batches) == 1
        assert "3" in batches[0].rationale
