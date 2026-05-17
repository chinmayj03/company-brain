"""
Feedback routes — Task #28: Human Annotation Feedback Loop.

POST /feedback/resynthesise
    Called by Java (async, fire-and-forget) after a user annotation is saved.
    Accepts the annotation payload, immediately returns 202, then re-synthesises
    the entity's BusinessContext in the background and POSTs the result back to
    Java via the callback URL.

POST /feedback/resynthesise-sync  [dev/test only]
    Same as above but waits for synthesis to complete and returns the result.
    Use for integration tests and local development — not production.

POST /feedback/thumbs                                         (A1.7 few-shot bank)
    Record a thumbs-up or thumbs-down signal for a completed Q&A pair.
    thumbs="up"  → record as few-shot exemplar (quality boosted to >= 0.9)
    thumbs="down" -> suppress / do not record

POST /feedback/edit                                           (A1.7 few-shot bank)
    Record an implicit positive signal when the user edits an answer.
    Edited answers are treated as thumbs-up with a quality floor of 0.85.

GET /feedback/stats                                           (A1.7 few-shot bank)
    Return per-persona example counts and recent-addition stats for a workspace.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, Query
from pydantic import BaseModel

import structlog

from companybrain.pipeline.feedback_resynthesizer import (
    FeedbackResynthesizer, AnnotationFeedback
)

log = structlog.get_logger(__name__)
router = APIRouter()


class ResynthesiseRequest(BaseModel):
    """Payload sent by Java's ResynthesisService after saving an annotation."""
    workspace_id:    str
    node_id:         str          # Java UUID for the node
    external_id:     str          # entity external_id (matches knowledge-graph nodes)
    entity_name:     str
    entity_type:     str = "Function"
    entity_file:     str = ""
    annotation_type: str          # business_context | invariant | risk_flag | deprecation_note
    annotation_text: str
    author:          str = ""
    callback_url:    str = ""     # Java's /v1/internal/pipeline-result
    callback_key:    str = ""     # X-Internal-Key header value


class ResynthesiseResponse(BaseModel):
    status: str
    entity_name: str
    message: str


# ── Async (production) ────────────────────────────────────────────────────────

@router.post("/resynthesise", response_model=ResynthesiseResponse, status_code=202)
async def resynthesise_async(
    request: ResynthesiseRequest,
    background_tasks: BackgroundTasks,
):
    """
    Accept an annotation feedback payload and immediately return 202.
    Re-synthesis runs in the background and pushes results back to Java.

    Java fires this endpoint without waiting for a response (connection timeout ~2s).
    """
    feedback = _to_feedback(request)
    background_tasks.add_task(_run_resynthesis, feedback)

    log.info(
        "Annotation feedback queued for re-synthesis",
        entity=request.entity_name,
        annotation_type=request.annotation_type,
        workspace=request.workspace_id,
    )
    return ResynthesiseResponse(
        status="accepted",
        entity_name=request.entity_name,
        message="Re-synthesis queued. Results will be pushed via callback_url.",
    )


# ── Sync (dev/test) ───────────────────────────────────────────────────────────

@router.post("/resynthesise-sync")
async def resynthesise_sync(request: ResynthesiseRequest):
    """
    Synchronous variant — waits for synthesis and returns result inline.
    Use for integration tests only; not recommended for production.
    """
    feedback = _to_feedback(request)
    result = await FeedbackResynthesizer().resynthesise_one(feedback)
    return {
        "status": "ok" if result.success else "error",
        "entity_name": result.entity_name,
        "external_id": result.external_id,
        "change_risk": result.business_context.change_risk,
        "purpose": result.business_context.purpose,
        "t0": result.t0,
        "t1": result.t1,
        "error": result.error or None,
    }


# ── Internal ──────────────────────────────────────────────────────────────────

async def _run_resynthesis(feedback: AnnotationFeedback) -> None:
    """Background task: run re-synthesis and push result to Java."""
    try:
        await FeedbackResynthesizer().resynthesise_one(feedback)
    except Exception as e:
        log.error(
            "Background re-synthesis crashed",
            entity=feedback.entity_name,
            error=str(e),
        )


def _to_feedback(request: ResynthesiseRequest) -> AnnotationFeedback:
    return AnnotationFeedback(
        workspace_id=request.workspace_id,
        node_id=request.node_id,
        external_id=request.external_id,
        entity_name=request.entity_name,
        entity_type=request.entity_type,
        entity_file=request.entity_file,
        annotation_type=request.annotation_type,
        annotation_text=request.annotation_text,
        author=request.author,
        callback_url=request.callback_url,
        callback_key=request.callback_key,
    )


# ── A1.7: Few-shot bank endpoints ─────────────────────────────────────────────

def _get_capture():
    """Lazy-initialise the FewShotCapture (bank + retriever) using config."""
    from pathlib import Path
    from companybrain.config import settings
    from companybrain.workspace.few_shot.bank import FewShotBank
    from companybrain.workspace.few_shot.retriever import FewShotRetriever
    from companybrain.workspace.few_shot.capture import FewShotCapture

    bank = FewShotBank(
        storage_path=Path(settings.few_shot_bank_path),
        max_per_bucket=settings.few_shot_max_per_bucket,
    )
    retriever = FewShotRetriever(bank)
    return FewShotCapture(bank, retriever)


def _get_bank():
    """Return a FewShotBank pointed at the configured storage path."""
    from pathlib import Path
    from companybrain.config import settings
    from companybrain.workspace.few_shot.bank import FewShotBank

    return FewShotBank(
        storage_path=Path(settings.few_shot_bank_path),
        max_per_bucket=settings.few_shot_max_per_bucket,
    )


class ThumbsRequest(BaseModel):
    workspace_id: str
    question: str
    answer: str
    persona: str = "generic"
    citations: List[str] = []
    confidence_score: float = 0.5
    thumbs: str  # "up" | "down"


class ThumbsResponse(BaseModel):
    recorded: bool
    message: str


class EditRequest(BaseModel):
    workspace_id: str
    question: str
    original_answer: str
    edited_answer: str
    persona: str = "generic"
    citations: List[str] = []
    confidence_score: float = 0.75  # implicit positive signal floor


class EditResponse(BaseModel):
    recorded: bool
    message: str


class StatsResponse(BaseModel):
    workspace_id: str
    total_examples: Dict[str, int]
    recent_additions: int


@router.post("/thumbs", response_model=ThumbsResponse)
async def record_thumbs(request: ThumbsRequest):
    """
    Record a thumbs-up or thumbs-down signal for a completed Q&A pair.

    thumbs="up"  -> qualifies the pair as a few-shot exemplar
    thumbs="down" -> explicitly blocks the pair from being recorded
    """
    if request.thumbs not in ("up", "down"):
        return ThumbsResponse(
            recorded=False,
            message=f"Invalid thumbs value '{request.thumbs}'; must be 'up' or 'down'.",
        )

    try:
        capture = _get_capture()
        recorded = await capture.record_if_successful(
            workspace_id=request.workspace_id,
            persona=request.persona,
            question=request.question,
            answer=request.answer,
            citations=request.citations,
            confidence_score=request.confidence_score,
            thumbs_feedback=request.thumbs,
        )
        msg = "Example recorded." if recorded else "Example not recorded (below quality threshold)."
        log.info(
            "feedback.thumbs",
            workspace_id=request.workspace_id,
            persona=request.persona,
            thumbs=request.thumbs,
            recorded=recorded,
        )
        return ThumbsResponse(recorded=recorded, message=msg)
    except Exception as exc:
        log.error("feedback.thumbs.error", error=str(exc))
        return ThumbsResponse(recorded=False, message=f"Error: {exc}")


@router.post("/edit", response_model=EditResponse)
async def record_edit(request: EditRequest):
    """
    Record an implicit positive signal when a user edits an answer.

    An edited answer is treated as implicit thumbs-up with quality floor 0.85.
    The *edited* answer is stored (not the original) so future queries get the
    human-refined version as the exemplar.
    """
    try:
        # Use edited_answer as the stored answer; boost confidence floor
        effective_confidence = max(request.confidence_score, 0.85)

        capture = _get_capture()
        recorded = await capture.record_if_successful(
            workspace_id=request.workspace_id,
            persona=request.persona,
            question=request.question,
            answer=request.edited_answer,
            citations=request.citations,
            confidence_score=effective_confidence,
            thumbs_feedback="up",   # edit is implicit positive signal
        )
        msg = "Edited example recorded." if recorded else "Example not recorded."
        log.info(
            "feedback.edit",
            workspace_id=request.workspace_id,
            persona=request.persona,
            recorded=recorded,
        )
        return EditResponse(recorded=recorded, message=msg)
    except Exception as exc:
        log.error("feedback.edit.error", error=str(exc))
        return EditResponse(recorded=False, message=f"Error: {exc}")


_KNOWN_PERSONAS = ("developer", "pm", "vp_eng", "generic")


@router.get("/stats", response_model=StatsResponse)
async def get_stats(workspace_id: str = Query(..., description="Workspace UUID")):
    """
    Return per-persona example counts and recent-addition stats.
    """
    from datetime import datetime, timezone, timedelta

    try:
        bank = _get_bank()
        total_examples: Dict[str, int] = {}
        recent_additions = 0
        cutoff = datetime.now(tz=timezone.utc) - timedelta(hours=24)

        for persona in _KNOWN_PERSONAS:
            examples = bank.get_all(workspace_id, persona)
            total_examples[persona] = len(examples)
            recent_additions += sum(
                1 for ex in examples if ex.created_at >= cutoff
            )

        return StatsResponse(
            workspace_id=workspace_id,
            total_examples=total_examples,
            recent_additions=recent_additions,
        )
    except Exception as exc:
        log.error("feedback.stats.error", error=str(exc))
        return StatsResponse(
            workspace_id=workspace_id,
            total_examples={},
            recent_additions=0,
        )
