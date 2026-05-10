"""Unit tests for ADR-0050 M1 batch planner."""
import pytest
from companybrain.pipeline.batch_planner import pack_into_batches


class _Chunk:
    """Minimal MethodChunk stand-in for tests."""
    def __init__(self, body: str = "", header_context: str = "", import_context: str = ""):
        self.body = body
        self.header_context = header_context
        self.import_context = import_context


def _chunks(n: int, body_size: int = 50) -> list[_Chunk]:
    return [_Chunk(body="x" * body_size) for _ in range(n)]


def test_empty_returns_empty():
    assert pack_into_batches([]) == []


def test_single_chunk_is_one_batch():
    result = pack_into_batches(_chunks(1))
    assert len(result) == 1
    assert len(result[0]) == 1


def test_small_chunks_fit_together():
    # 7 tiny chunks (50 chars each) should pack into a single batch
    # within default 4000 token budget.
    result = pack_into_batches(_chunks(7, body_size=50))
    assert len(result) == 1
    assert len(result[0]) == 7


def test_hard_max_per_batch_splits():
    # With hard_max=4, 8 chunks should split into 2 batches of 4.
    result = pack_into_batches(_chunks(8, body_size=50), hard_max_per_batch=4)
    assert len(result) == 2
    assert all(len(b) <= 4 for b in result)


def test_large_chunks_split_into_singletons():
    # body_size 4000 chars → input estimate is large, but output estimate
    # is what triggers splits.  Use max_output_tokens=1 to force singletons.
    result = pack_into_batches(_chunks(3, body_size=100), max_output_tokens=1)
    # Each chunk must be in its own batch (budget of 1 means even 1 chunk
    # doesn't "fit", but the code puts singletons in their own batch anyway).
    assert len(result) == 3
    assert all(len(b) == 1 for b in result)


def test_total_chunks_preserved():
    chunks = _chunks(10, body_size=50)
    batches = pack_into_batches(chunks, max_output_tokens=4_000, hard_max_per_batch=4)
    total = sum(len(b) for b in batches)
    assert total == 10


def test_order_preserved():
    chunks = [_Chunk(body=f"body_{i}") for i in range(5)]
    batches = pack_into_batches(chunks, hard_max_per_batch=2)
    flat = [c for b in batches for c in b]
    for orig, got in zip(chunks, flat):
        assert orig is got
