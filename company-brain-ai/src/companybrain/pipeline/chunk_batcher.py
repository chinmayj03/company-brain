"""
ADR-0047: ChunkBatcher — group small adjacent same-class method chunks into batches.

Strategy (language-agnostic):
  - A chunk is "small" when its body is < SMALL_BODY_CHARS characters.
  - Group up to MAX_BATCH_SIZE small chunks that share the same parent class
    (extracted from qname prefix) and are adjacent in the chunk list.
  - Large chunks (body >= SMALL_BODY_CHARS) always get their own single-chunk batch.
  - Never mix chunks from different classes in one batch (prompt clarity).

Each ChunkBatch carries a rationale string for telemetry. The extractor emits
one LLM call per batch and returns exactly len(batch.chunks) entities.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from companybrain.pipeline.code_chunker import MethodChunk

SMALL_BODY_CHARS = 800   # body < this → eligible for grouping
MAX_BATCH_SIZE   = 8     # max chunks per grouped batch


@dataclass
class ChunkBatch:
    chunks: list[MethodChunk]
    rationale: str = ""    # "single chunk" | "grouped N small siblings"

    @property
    def is_batched(self) -> bool:
        return len(self.chunks) > 1


class ChunkBatcher:
    """
    Converts a flat list of MethodChunks into ChunkBatch objects.

    Usage:
        batches = ChunkBatcher().batch(chunks)
    """

    def batch(self, chunks: list[MethodChunk]) -> list[ChunkBatch]:
        """Group chunks into batches according to ADR-0047 grouping rules."""
        if not chunks:
            return []

        batches: list[ChunkBatch] = []
        pending: list[MethodChunk] = []  # small same-class chunks accumulating

        def _flush(reason: str = "") -> None:
            if not pending:
                return
            if len(pending) == 1:
                batches.append(ChunkBatch(chunks=list(pending), rationale="single chunk"))
            else:
                n = len(pending)
                batches.append(ChunkBatch(
                    chunks=list(pending),
                    rationale=reason or f"grouped {n} small siblings",
                ))
            pending.clear()

        pending_class: str = ""

        for chunk in chunks:
            chunk_class = _class_of(chunk)
            is_small = len(chunk.body) < SMALL_BODY_CHARS

            if not is_small:
                # Large chunk: flush pending, emit single batch
                _flush()
                pending_class = ""
                batches.append(ChunkBatch(chunks=[chunk], rationale="single chunk (large)"))
                continue

            # Small chunk — try to join pending group
            if pending and (chunk_class != pending_class or len(pending) >= MAX_BATCH_SIZE):
                _flush()
                pending_class = ""

            if not pending:
                pending_class = chunk_class

            pending.append(chunk)

        _flush()
        return batches


def _class_of(chunk: MethodChunk) -> str:
    """Extract the class portion from 'ClassName.methodName' qname."""
    qname = chunk.qname or ""
    dot = qname.rfind(".")
    return qname[:dot] if dot != -1 else qname
