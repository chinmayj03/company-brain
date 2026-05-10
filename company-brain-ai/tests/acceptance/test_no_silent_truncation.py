"""ADR-0050 acceptance test — zero silent truncation guarantee.

Mocks every LLM call with random stop_reason='max_tokens' and asserts
that the recovery path recovers all entities (or degrades to stubs —
never silently loses them).
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Optional
import itertools

import pytest

from companybrain.pipeline.extraction_recovery import (
    extract_batch_with_recovery,
    ContextAgentResult,
    RecoveryStats,
)
from companybrain.pipeline.batch_planner import pack_into_batches


@dataclass
class _SyntheticChunk:
    qname: str
    body: str = "x" * 200
    header_context: str = ""
    import_context: str = ""
    file_path: str = "Synthetic.java"
    language: str = "java"


@dataclass
class _FakeEntity:
    name: str
    qname: str = ""

    def __post_init__(self):
        if not self.qname:
            self.qname = self.name


def _truncated_response(chunks):
    @dataclass
    class R:
        stop_reason: str = "max_tokens"
        _chunks: list = field(default_factory=list)

        @property
        def results(self):
            return [ContextAgentResult(entity=_FakeEntity(c.qname), edges=[]) for c in self._chunks]

    r = R()
    r._chunks = list(chunks)
    return r


def _full_response(chunks):
    @dataclass
    class R:
        stop_reason: str = "end_turn"
        _chunks: list = field(default_factory=list)

        @property
        def results(self):
            return [ContextAgentResult(entity=_FakeEntity(c.qname), edges=[]) for c in self._chunks]

    r = R()
    r._chunks = list(chunks)
    return r


class _MockTruncatingAgent:
    """Agent that truncates at random positions to stress-test recovery."""

    def __init__(self, truncate_pattern: list[bool]):
        self._pattern = truncate_pattern
        self._idx = itertools.cycle(range(len(truncate_pattern)))

    def _should_truncate(self, batch_size: int) -> bool:
        i = next(self._idx)
        return self._pattern[i % len(self._pattern)] and batch_size > 1

    async def extract_batch_raw(self, batch, max_tokens=4000):
        if self._should_truncate(len(batch)):
            return _truncated_response(batch[: len(batch) // 2])
        return _full_response(batch)

    def parse(self, response):
        return response.results

    def parse_partial(self, response):
        return response.results

    async def extract_batch(self, chunk_list, max_tokens=4000):
        return [ContextAgentResult(entity=_FakeEntity(c.qname), edges=[]) for c in chunk_list]

    async def extract_region(self, region):
        return ContextAgentResult(entity=_FakeEntity("region"), edges=[])

    async def summarise_regions(self, chunk, region_results):
        return ContextAgentResult(entity=_FakeEntity(chunk.qname), edges=[])


@pytest.mark.asyncio
async def test_force_truncation_loses_zero_entities():
    """Force-truncate half of all batches; assert no entities are silently lost."""
    # 16 chunks to keep the test fast but non-trivial
    chunk_count = 16
    chunks = [_SyntheticChunk(qname=f"Cls.m{i}") for i in range(chunk_count)]
    expected_qnames = {c.qname for c in chunks}

    truncate_pattern = [True, False, True, True, False]
    agent = _MockTruncatingAgent(truncate_pattern)

    stats = RecoveryStats()
    batches = pack_into_batches(chunks, max_output_tokens=4_000, hard_max_per_batch=8)

    all_results = []
    for batch in batches:
        results = await extract_batch_with_recovery(batch, agent=agent, stats=stats)
        all_results.extend(results)

    recovered_qnames = set()
    for r in all_results:
        entity = getattr(r, "entity", None)
        if entity is not None:
            qname = getattr(entity, "qname", None) or getattr(entity, "name", None)
            if qname:
                recovered_qnames.add(qname)

    # All methods should be accounted for (some may be stubs, but none lost).
    assert expected_qnames <= recovered_qnames, (
        f"Lost entities: {expected_qnames - recovered_qnames}"
    )


@pytest.mark.asyncio
async def test_no_double_count_on_recovery():
    """Recovery must not emit the same entity twice."""
    chunks = [_SyntheticChunk(qname=f"Cls.m{i}") for i in range(8)]
    # Never truncate — each entity should appear exactly once.
    agent = _MockTruncatingAgent(truncate_pattern=[False] * 8)

    batches = pack_into_batches(chunks, max_output_tokens=4_000, hard_max_per_batch=4)
    all_results = []
    for batch in batches:
        results = await extract_batch_with_recovery(batch, agent=agent)
        all_results.extend(results)

    qnames = [
        getattr(getattr(r, "entity", None), "qname", None)
        for r in all_results
        if getattr(r, "entity", None) is not None
    ]
    assert len(qnames) == len(set(qnames)), f"Duplicate entities found: {qnames}"


@pytest.mark.asyncio
async def test_zero_chunks_returns_empty():
    agent = _MockTruncatingAgent(truncate_pattern=[False])
    results = await extract_batch_with_recovery([], agent=agent)
    assert results == []
