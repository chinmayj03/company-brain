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
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, BackgroundTasks
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
