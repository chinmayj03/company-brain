"""Unit tests for ADR-0046 chunk_strategy router."""
from __future__ import annotations

import pytest

from companybrain.pipeline.chunk_strategy import (
    BATCHED_METHODS_THRESHOLD,
    MAX_METHODS_PER_BATCH,
    SMALL_METHOD_THRESHOLD,
    WHOLE_FILE_THRESHOLD,
    ChunkStrategy,
    choose_strategy,
    group_into_batches,
)
from companybrain.pipeline.code_chunker import MethodChunk


def _make_chunk(qname: str = "Foo.bar", body: str = "x") -> MethodChunk:
    return MethodChunk(
        file_path="Foo.java",
        qname=qname,
        kind="method",
        body=body,
        header_context="",
        import_context="",
        body_hash="abc",
        language="java",
    )


class TestChooseStrategy:
    def test_small_file_is_whole_file(self):
        assert choose_strategy(WHOLE_FILE_THRESHOLD - 1) == ChunkStrategy.WHOLE_FILE

    def test_boundary_whole_file(self):
        # exactly at threshold → switches to BATCHED
        assert choose_strategy(WHOLE_FILE_THRESHOLD) == ChunkStrategy.BATCHED_METHODS

    def test_mid_size_is_batched(self):
        mid = (WHOLE_FILE_THRESHOLD + BATCHED_METHODS_THRESHOLD) // 2
        assert choose_strategy(mid) == ChunkStrategy.BATCHED_METHODS

    def test_large_file_is_per_method(self):
        assert choose_strategy(BATCHED_METHODS_THRESHOLD) == ChunkStrategy.PER_METHOD

    def test_very_large_file(self):
        assert choose_strategy(100_000) == ChunkStrategy.PER_METHOD


class TestGroupIntoBatches:
    def _chunks(self, sizes: list[int]) -> list[MethodChunk]:
        return [_make_chunk(f"Foo.m{i}", "x" * s) for i, s in enumerate(sizes)]

    def test_all_small_fits_in_one_batch(self):
        chunks = self._chunks([10] * 5)
        batches = group_into_batches(chunks)
        assert len(batches) == 1
        assert len(batches[0]) == 5

    def test_overflow_creates_new_batch(self):
        chunks = self._chunks([10] * (MAX_METHODS_PER_BATCH + 1))
        batches = group_into_batches(chunks)
        assert len(batches) == 2

    def test_large_method_goes_solo(self):
        chunks = self._chunks([10, SMALL_METHOD_THRESHOLD, 10])
        batches = group_into_batches(chunks)
        # small, large (solo), small — expect 3 batches
        assert len(batches) == 3
        assert len(batches[1]) == 1  # the large one

    def test_empty_input(self):
        assert group_into_batches([]) == []

    def test_single_large_method(self):
        chunks = self._chunks([SMALL_METHOD_THRESHOLD])
        batches = group_into_batches(chunks)
        assert len(batches) == 1
        assert len(batches[0]) == 1

    def test_multiple_large_methods(self):
        chunks = self._chunks([SMALL_METHOD_THRESHOLD, SMALL_METHOD_THRESHOLD])
        batches = group_into_batches(chunks)
        assert len(batches) == 2

    def test_small_flushed_before_large(self):
        # small × 3, then large — the 3 smalls should be in their own batch
        chunks = self._chunks([10, 10, 10, SMALL_METHOD_THRESHOLD])
        batches = group_into_batches(chunks)
        assert len(batches) == 2
        assert len(batches[0]) == 3
        assert len(batches[1]) == 1
