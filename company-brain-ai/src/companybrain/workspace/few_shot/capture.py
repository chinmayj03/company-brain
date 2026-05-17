"""
FewShotCapture — write path that decides whether a completed Q&A pair
qualifies as a few-shot exemplar and, if so, records it into the bank.

Quality criteria
----------------
1. confidence_score >= FEW_SHOT_MIN_CONFIDENCE  AND  len(citations) >= 1
       → quality_score = confidence_score
2. thumbs_feedback == "up"
       → quality_score = max(quality_score, 0.9)  (positive boost)
3. thumbs_feedback == "down"
       → skip entirely (never record a disliked answer)
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import List, Optional

import structlog

from companybrain.workspace.few_shot.bank import FewShotBank, FewShotExample
from companybrain.workspace.few_shot.retriever import FewShotRetriever, _embed

log = structlog.get_logger(__name__)


class FewShotCapture:
    """Called after a query completes to optionally record it as a few-shot example."""

    def __init__(self, bank: FewShotBank, retriever: FewShotRetriever) -> None:
        self._bank      = bank
        self._retriever = retriever

    async def record_if_successful(
        self,
        workspace_id: str,
        persona: str,
        question: str,
        answer: str,
        citations: List[str],
        confidence_score: float,
        thumbs_feedback: Optional[str] = None,  # "up" | "down" | None
    ) -> bool:
        """
        Record a Q&A pair if quality threshold is met.

        Returns True if the example was recorded.
        """
        from companybrain.config import settings

        # Guard: feature flag
        if not settings.few_shot_enabled:
            return False

        # Guard: explicit dislike — never record
        if thumbs_feedback == "down":
            log.debug(
                "few_shot.capture.skipped_thumbs_down",
                workspace_id=workspace_id,
                persona=persona,
            )
            return False

        # Guard: minimum quality threshold
        min_confidence = settings.few_shot_min_confidence
        if confidence_score < min_confidence or len(citations) < 1:
            log.debug(
                "few_shot.capture.below_threshold",
                workspace_id=workspace_id,
                persona=persona,
                confidence=confidence_score,
                n_citations=len(citations),
                min_confidence=min_confidence,
            )
            return False

        # Compute quality score
        quality_score = confidence_score
        if thumbs_feedback == "up":
            quality_score = max(quality_score, 0.9)

        # Compute embedding for future similarity search
        embedding = _embed(question)

        now = datetime.now(tz=timezone.utc)
        example = FewShotExample(
            id=str(uuid.uuid4()),
            workspace_id=workspace_id,
            persona=persona,
            question=question,
            answer=answer,
            citations=list(citations),
            quality_score=quality_score,
            embedding=embedding,
            created_at=now,
            last_used_at=now,
            use_count=0,
        )

        self._bank.add(example)

        log.info(
            "few_shot.capture.recorded",
            workspace_id=workspace_id,
            persona=persona,
            example_id=example.id,
            quality_score=quality_score,
            n_citations=len(citations),
        )
        return True
