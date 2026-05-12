"""
ADR-0056 — VerifierLoop orchestrator.

Inserted between Stage 2.5 (cross-file / reachability) and Stage 3
(BusinessContext synthesis). Walks every entity through:

    Mode A (deterministic, $0)
        ↓ if fuzzy / hallucinated
    Mode B (Haiku sub-agent, ~$0.001 each)
        ↓ if NO + original_confidence ≥ 0.8 + high-stakes
    Mode C (re-extraction with retry context, ~$0.005 each)

Writes ``verified`` / ``verifier_mode`` / ``verifier_notes`` on each entity
and returns aggregate telemetry: confirmed/fuzzy/hallucinated/conflicting
counts plus how many Mode-C cycles fired.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import structlog

from companybrain.agents.verifier_agent import VerifierAgent
from companybrain.models.entities import ExtractedEntity
from companybrain.pipeline.self_correction import (
    SelfCorrector,
    should_self_correct,
)
from companybrain.pipeline.verifier_deterministic import (
    _read_source,
    verify_entity,
)

log = structlog.get_logger(__name__)


# Cap on concurrent Mode-B calls — keeps the verifier from saturating the
# provider's rate limits when a run produces hundreds of fuzzy entities.
_MAX_SUBAGENT_CONCURRENCY = 8


@dataclass
class VerifierStats:
    """Per-run summary returned to the orchestrator for telemetry/UI."""

    total: int = 0
    confirmed: int = 0
    fuzzy: int = 0
    hallucinated: int = 0
    conflicting: int = 0
    skipped: int = 0
    subagent_calls: int = 0
    self_correction_fires: int = 0
    self_correction_accepted: int = 0
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "total":                    self.total,
            "confirmed":                self.confirmed,
            "fuzzy":                    self.fuzzy,
            "hallucinated":             self.hallucinated,
            "conflicting":              self.conflicting,
            "skipped":                  self.skipped,
            "subagent_calls":           self.subagent_calls,
            "self_correction_fires":    self.self_correction_fires,
            "self_correction_accepted": self.self_correction_accepted,
            "errors":                   list(self.errors),
        }


class VerifierLoop:
    """Stateful orchestrator for one extraction run's verifier pass.

    Construct once per pipeline invocation. ``run()`` mutates the entities in
    place (writing ``verified`` / ``verifier_mode`` / ``verifier_notes``) and
    returns the same list along with the aggregate stats.
    """

    def __init__(
        self,
        agent: Optional[VerifierAgent] = None,
        corrector: Optional[SelfCorrector] = None,
        enable_subagent: bool = True,
        enable_self_correction: bool = True,
    ) -> None:
        self._agent = agent or VerifierAgent()
        self._corrector = corrector or SelfCorrector(verifier=self._agent)
        self._enable_subagent = enable_subagent
        self._enable_self_correction = enable_self_correction

    async def run(
        self,
        entities: list[ExtractedEntity],
        source_roots: list[Path],
    ) -> tuple[list[ExtractedEntity], VerifierStats]:
        stats = VerifierStats(total=len(entities))
        if not entities:
            return entities, stats

        # ── Mode A: deterministic pass on every entity ───────────────────
        # Synchronous and CPU-bound; just loop. Records the initial status
        # plus a list of (entity, claim_text) tuples that need Mode B.
        needs_subagent: list[tuple[ExtractedEntity, str]] = []
        for entity in entities:
            det = verify_entity(entity, source_roots)
            entity.verified = det.status              # type: ignore[assignment]
            entity.verifier_mode = "deterministic"
            entity.verifier_notes = det.notes

            if det.status == "confirmed":
                stats.confirmed += 1
            elif det.status == "skipped":
                stats.skipped += 1
            elif det.status in ("fuzzy", "hallucinated"):
                claim = entity.query_text or entity.code_snippet or ""
                needs_subagent.append((entity, claim))

        # ── Mode B: Haiku sub-agent on Mode-A-flagged entities ──────────
        if self._enable_subagent and needs_subagent:
            await self._run_subagent(needs_subagent, source_roots, stats)
        else:
            # No sub-agent run — finalise Mode-A flags into stats.
            for entity, _ in needs_subagent:
                if entity.verified == "fuzzy":
                    stats.fuzzy += 1
                elif entity.verified == "hallucinated":
                    stats.hallucinated += 1

        log.info(
            "[verifier] run complete",
            **stats.as_dict(),
        )
        return entities, stats

    # ── Mode B helpers ──────────────────────────────────────────────────

    async def _run_subagent(
        self,
        candidates: list[tuple[ExtractedEntity, str]],
        source_roots: list[Path],
        stats: VerifierStats,
    ) -> None:
        sem = asyncio.Semaphore(_MAX_SUBAGENT_CONCURRENCY)

        async def _one(entity: ExtractedEntity, claim: str) -> None:
            async with sem:
                await self._verify_one(entity, claim, source_roots, stats)

        await asyncio.gather(*[_one(e, c) for e, c in candidates])

    async def _verify_one(
        self,
        entity: ExtractedEntity,
        claim: str,
        source_roots: list[Path],
        stats: VerifierStats,
    ) -> None:
        source = _read_source(entity.file, source_roots) or ""
        if not source:
            # Mode-A already marked the entity skipped or hallucinated — leave
            # the existing flag and stats accounting alone.
            if entity.verified == "fuzzy":
                stats.fuzzy += 1
            elif entity.verified == "hallucinated":
                stats.hallucinated += 1
            return

        try:
            verdict = await self._agent.verify(
                claim=claim, source_excerpt=source, file_path=entity.file,
            )
            stats.subagent_calls += 1
        except Exception as exc:
            stats.errors.append(f"subagent: {exc}")
            log.warning("[verifier] subagent failed", entity=entity.name, error=str(exc))
            # Fall back to Mode-A verdict in stats.
            if entity.verified == "fuzzy":
                stats.fuzzy += 1
            elif entity.verified == "hallucinated":
                stats.hallucinated += 1
            return

        if verdict.result == "YES":
            entity.verified = "confirmed"
            entity.verifier_mode = "subagent"
            entity.verifier_notes = f"subagent YES: {verdict.reason}"
            stats.confirmed += 1
            return

        if verdict.result == "PARTIAL":
            entity.verified = "fuzzy"
            entity.verifier_mode = "subagent"
            entity.verifier_notes = f"subagent PARTIAL: {verdict.reason}"
            stats.fuzzy += 1
            return

        # verdict.result == "NO"
        entity.verified = "hallucinated"
        entity.verifier_mode = "subagent"
        entity.verifier_notes = f"subagent NO: {verdict.reason}"

        if (
            self._enable_self_correction
            and should_self_correct(entity, verifier_said_no=True)
        ):
            stats.self_correction_fires += 1
            await self._maybe_self_correct(entity, verdict.reason, source_roots, stats)
        else:
            stats.hallucinated += 1

    # ── Mode C ──────────────────────────────────────────────────────────

    async def _maybe_self_correct(
        self,
        entity: ExtractedEntity,
        verifier_reason: str,
        source_roots: list[Path],
        stats: VerifierStats,
    ) -> None:
        try:
            result = await self._corrector.recorrect(
                entity=entity,
                verifier_reason=verifier_reason,
                source_roots=source_roots,
            )
        except Exception as exc:
            stats.errors.append(f"self_correction: {exc}")
            log.warning("[verifier] self_correction failed",
                        entity=entity.name, error=str(exc))
            stats.hallucinated += 1
            return

        if result.accepted:
            entity.query_text = result.new_query_text or entity.query_text
            entity.code_snippet = result.new_code_snippet or entity.code_snippet
            entity.confidence = result.new_confidence or entity.confidence
            entity.verified = "confirmed"
            entity.verifier_mode = "self_correction"
            entity.verifier_notes = f"self-corrected: {result.notes}"
            stats.confirmed += 1
            stats.self_correction_accepted += 1
            return

        # Rewrite was rejected — either the retry was unparseable, the
        # verifier disputed it again, or source was unreadable. Mark
        # conflicting so /query still excludes it but the row is preserved
        # for telemetry / human review.
        entity.verified = "conflicting"
        entity.verifier_mode = "self_correction"
        entity.verifier_notes = (
            f"self-correction did not resolve: {result.notes}"
        )
        stats.conflicting += 1
