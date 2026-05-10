"""Bisection-on-truncation wrapper for ContextAgent (ADR-0050 M2).

When a batch returns stop_reason='max_tokens', salvage what completed,
recursively split the rest. Bounded recursion via max_split_depth.

ADR-0048 dependency note: ContextAgent lands in ADR-0048. Until that ADR
merges, this module falls through to the legacy chunk_extractor path
(which already has char-by-char JSON recovery as a degraded fallback).
Either path produces the same interface: a list of ChunkResult objects.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Optional

import structlog

log = structlog.get_logger(__name__)

# ── M1 recovery telemetry counters (per-call; reset by caller) ────────────────
@dataclass
class RecoveryStats:
    recovery_invocations: int = 0
    bisection_depth_max: int = 0
    region_splits: int = 0
    oversized_methods: int = 0


# ── ContextAgentResult stub (used until ADR-0048 lands) ──────────────────────
@dataclass
class ContextAgentResult:
    """Minimal stub matching the interface extraction_recovery expects."""
    entity: Any
    edges: list = field(default_factory=list)
    business_context: dict = field(default_factory=dict)
    stop_reason: str = "end_turn"   # "max_tokens" triggers recovery


def _try_import_context_agent():
    """Return ContextAgent class if ADR-0048 has landed, else None."""
    try:
        from companybrain.agents.context_agent import ContextAgent  # noqa: F401
        return ContextAgent
    except ImportError:
        return None


async def extract_batch_with_recovery(
    chunks: list,
    agent: Any | None = None,
    max_tokens: int = 4_000,
    depth: int = 0,
    stats: Optional[RecoveryStats] = None,
) -> list:
    """Bisect-on-truncation wrapper.

    Tries to extract `chunks` via ContextAgent (ADR-0048). Falls through
    to the legacy chunk_extractor when ContextAgent isn't available yet.

    Returns a list of ChunkResult (legacy path) or ContextAgentResult
    (ADR-0048 path). Callers should duck-type on `.entity` and `.edges`.
    """
    if stats is None:
        stats = RecoveryStats()

    # If a concrete agent was provided, use it directly regardless of whether
    # the ContextAgent class (ADR-0048) has landed yet.
    if agent is None:
        ContextAgent = _try_import_context_agent()
        if ContextAgent is None:
            # ADR-0048 not merged — fall through to legacy chunk_extractor.
            return await _legacy_extract(chunks, stats=stats)
        # No agent provided and ContextAgent is importable — nothing to do
        # (caller should pass an agent instance; legacy is the only fallback).
        return await _legacy_extract(chunks, stats=stats)

    from companybrain.config import settings
    max_depth = getattr(settings, "max_split_depth", 6)

    if depth > max_depth:
        log.error("extraction_recovery.depth_exceeded", chunks=len(chunks))
        stats.oversized_methods += len(chunks)
        return await _extract_each_solo(chunks, agent, stats=stats)

    try:
        response = await agent.extract_batch_raw(chunks, max_tokens=max_tokens)
    except Exception as exc:
        log.warning("extraction_recovery.extract_failed", error=str(exc), chunks=len(chunks))
        return await _extract_each_solo(chunks, agent, stats=stats)

    if getattr(response, "stop_reason", "end_turn") != "max_tokens":
        return agent.parse(response)   # happy path

    # Truncation detected.
    stats.recovery_invocations += 1
    stats.bisection_depth_max = max(stats.bisection_depth_max, depth)

    completed = agent.parse_partial(response)
    completed_qnames = {_qname_of(r) for r in completed}
    remaining = [c for c in chunks if _chunk_qname(c) not in completed_qnames]

    log.warning(
        "extraction_recovery.truncated",
        depth=depth, completed=len(completed), remaining=len(remaining),
    )

    if not remaining:
        return completed
    if len(remaining) == 1:
        return completed + [await _extract_solo(remaining[0], agent, stats=stats)]

    mid = len(remaining) // 2
    left, right = await asyncio.gather(
        extract_batch_with_recovery(remaining[:mid], agent, max_tokens, depth + 1, stats),
        extract_batch_with_recovery(remaining[mid:], agent, max_tokens, depth + 1, stats),
    )
    return completed + left + right


async def _extract_solo(chunk, agent, stats: RecoveryStats) -> Any:
    """M3a: single-method retry with bumped max_tokens.
    M3b: if still too big, fall through to region split.
    M3c: if region split also truncates, map-reduce summary."""
    stats.oversized_methods += 1
    try:
        results = await agent.extract_batch([chunk], max_tokens=4_000)
        if results:
            return results[0]
    except Exception as exc:
        log.warning("solo_extract_failed", qname=_chunk_qname(chunk), error=str(exc))

    # M3b
    try:
        from companybrain.pipeline.region_splitter import split_method_into_regions
        regions = split_method_into_regions(chunk)
        if regions:
            stats.region_splits += 1
            region_results = await asyncio.gather(*[
                agent.extract_region(r) for r in regions
            ])
            return await agent.summarise_regions(chunk, list(region_results))
    except Exception as exc:
        log.warning("region_split_failed", qname=_chunk_qname(chunk), error=str(exc))

    # M3c last resort: stub entity — we never lose the method's existence.
    return _stub_result(chunk)


async def _extract_each_solo(chunks, agent, stats: RecoveryStats) -> list:
    return [await _extract_solo(c, agent, stats=stats) for c in chunks]


async def _legacy_extract(chunks: list, stats: RecoveryStats) -> list:
    """Fall-through to legacy ChunkExtractor when ContextAgent isn't available."""
    try:
        from companybrain.pipeline.chunk_extractor import ChunkExtractor
        from companybrain.pipeline.chunk_batcher import ChunkBatch
        extractor = ChunkExtractor()
        batch = ChunkBatch(chunks=list(chunks), rationale="recovery_fallback")
        result = await extractor.extract_batch(batch)
        return result.results
    except Exception as exc:
        log.error("legacy_extract_fallback_failed", error=str(exc), chunks=len(chunks))
        return [_stub_result(c) for c in chunks]


def _stub_result(chunk):
    """When all recovery fails, emit a stub so we don't lose the method's existence."""
    try:
        from companybrain.pipeline.chunk_extractor import ChunkResult, ExtractedChunkEntity
        return ChunkResult(
            chunk=chunk,
            entity=ExtractedChunkEntity(
                entity_type="Method",
                name=_chunk_qname(chunk),
                qname=_chunk_qname(chunk),
                file_path=getattr(chunk, "file_path", ""),
                confidence=0.3,
            ),
            edges=[],
            error="extraction_recovery_stub",
        )
    except Exception:
        return ContextAgentResult(
            entity=None,
            edges=[],
            business_context={"purpose": "extraction_recovery_stub"},
        )


def _chunk_qname(chunk) -> str:
    return getattr(chunk, "qname", "") or getattr(chunk, "name", "") or ""


def _qname_of(result) -> str:
    entity = getattr(result, "entity", None)
    if entity is None:
        return ""
    return getattr(entity, "qname", "") or getattr(entity, "name", "") or ""
