"""
ADR-0046 D2: Adaptive chunk-sizing strategy router.

Chooses the extraction strategy for a file based on its total size and
per-method sizes.  Workers dispatch to one of three LLM prompts:

  WHOLE_FILE      — one call for the entire file (<4 000 chars)
  BATCHED_METHODS — one call per batch of small methods (4 000-15 000 chars)
  PER_METHOD      — one call per method (>15 000 chars or large methods)

Size thresholds are configurable via environment variables so we can tune
based on observed cost/quality telemetry without code changes.
"""
from __future__ import annotations

import os
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from companybrain.pipeline.code_chunker import MethodChunk

# ── Thresholds (chars, NOT tokens) ────────────────────────────────────────────
# Override via env: BRAIN_CHUNK_WHOLE_FILE_THRESHOLD, etc.
WHOLE_FILE_THRESHOLD     = int(os.getenv("BRAIN_CHUNK_WHOLE_FILE_THRESHOLD",     "4000"))
BATCHED_METHODS_THRESHOLD = int(os.getenv("BRAIN_CHUNK_BATCHED_THRESHOLD",       "15000"))
SMALL_METHOD_THRESHOLD   = int(os.getenv("BRAIN_CHUNK_SMALL_METHOD_THRESHOLD",   "500"))

# Max methods per batch call.  Keep ≤10 so the LLM output stays within
# MAX_TOKENS_PER_CALL and never gets truncated.
MAX_METHODS_PER_BATCH = int(os.getenv("BRAIN_CHUNK_MAX_METHODS_PER_BATCH", "8"))


class ChunkStrategy(str, Enum):
    WHOLE_FILE      = "whole_file"
    BATCHED_METHODS = "batched_methods"
    PER_METHOD      = "per_method"


def choose_strategy(file_size_chars: int) -> ChunkStrategy:
    """Return the extraction strategy for a file of the given character count."""
    if file_size_chars < WHOLE_FILE_THRESHOLD:
        return ChunkStrategy.WHOLE_FILE
    if file_size_chars < BATCHED_METHODS_THRESHOLD:
        return ChunkStrategy.BATCHED_METHODS
    return ChunkStrategy.PER_METHOD


def group_into_batches(
    chunks: list["MethodChunk"],
) -> list[list["MethodChunk"]]:
    """
    Group per-method chunks into batches for BATCHED_METHODS strategy.

    Rules:
    - Methods with body ≥ SMALL_METHOD_THRESHOLD go in their own batch (size 1).
    - Smaller methods are collected into groups of up to MAX_METHODS_PER_BATCH.
    """
    batches: list[list["MethodChunk"]] = []
    current: list["MethodChunk"] = []

    for chunk in chunks:
        if len(chunk.body) >= SMALL_METHOD_THRESHOLD:
            # Flush any accumulated small methods first, then emit this one solo.
            if current:
                batches.append(current)
                current = []
            batches.append([chunk])
        else:
            current.append(chunk)
            if len(current) >= MAX_METHODS_PER_BATCH:
                batches.append(current)
                current = []

    if current:
        batches.append(current)

    return batches
