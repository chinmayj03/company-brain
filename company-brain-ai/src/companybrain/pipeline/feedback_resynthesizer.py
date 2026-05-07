"""
FeedbackResynthesizer — Task #28: Human Annotation Feedback Loop.

When a user submits an annotation (business_context | invariant | risk_flag |
deprecation_note) for a graph node, Java calls this module via the
POST /feedback/resynthesise endpoint.  We re-run Stage 3 (ContextSynthesizer)
and Stage 3.5 (MemoryTokenizer) for ONLY that entity, then push the updated
BusinessContext back to Java — no full pipeline re-run required.

Flow:
    User annotates node in VS Code / dashboard
          ↓
    POST /v1/nodes/{nodeId}/annotations  (Java)
          ↓ async, fire-and-forget
    POST /feedback/resynthesise  (Python AI service)
          ↓
    FeedbackResynthesizer.resynthesise_one()
      1. Fetch existing node context from Java (git commits, PRs, existing annotations)
      2. Build a synthetic CommitCluster from the new annotation (highest signal)
      3. Run ContextSynthesizer._synthesise_entity() on the single entity
      4. Run MemoryTokenizer._tokenize_one() for T0/T1 tokens
      5. POST updated BusinessContext + tokens back to Java pipeline-result endpoint

This keeps annotation latency low: one LLM call (Stage 3 synthesis) instead of
the full 5-stage pipeline.

Why not just run the full pipeline?
  - The entity's code hasn't changed — entity extraction (Stage 1) would be a no-op.
  - Relationship extraction (Stage 2) is unchanged.
  - Gap detection (Stage 4) can wait for the next scheduled run.
  - Only the business context (Stage 3) incorporates annotations, so only that
    stage needs to re-run.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Optional

import httpx
import structlog
from tenacity import retry, stop_after_attempt, wait_exponential

from companybrain.config import settings
from companybrain.llm import get_provider, TaskRole, ChatMessage
from companybrain.models.entities import (
    ExtractedEntity, BusinessContext, CommitCluster, CommitEvent
)
from companybrain.models.entities import RepoType
from companybrain.pipeline.context_synthesizer import ContextSynthesizer, CONTEXT_SYNTHESIS_PROMPT
from companybrain.pipeline.memory_tokenizer import MemoryTokenizer, memory_tokens_to_metadata

log = structlog.get_logger(__name__)


# ── Request / Result types ────────────────────────────────────────────────────

@dataclass
class AnnotationFeedback:
    """What Java sends us when an annotation is submitted."""
    workspace_id:    str
    node_id:         str         # Java UUID
    external_id:     str         # entity external_id (matches graph nodes)
    entity_name:     str
    entity_type:     str
    entity_file:     str
    annotation_type: str         # business_context | invariant | risk_flag | deprecation_note
    annotation_text: str
    author:          str = ""
    callback_url:    str = ""    # Java's /v1/internal/pipeline-result
    callback_key:    str = ""    # X-Internal-Key


@dataclass
class ResynthesisResult:
    external_id:    str
    entity_name:    str
    business_context: BusinessContext
    t0: str
    t1: str
    success: bool = True
    error: str = ""


# ── Main class ────────────────────────────────────────────────────────────────

class FeedbackResynthesizer:
    """
    Re-synthesises business context for a single entity after a user annotation.

    Designed to be called from the FastAPI background task handler so it runs
    asynchronously and does not block the HTTP response to Java.
    """

    def __init__(self):
        self._synthesizer = ContextSynthesizer(max_concurrency=1)
        self._tokenizer   = MemoryTokenizer()
        log.info("FeedbackResynthesizer ready")

    async def resynthesise_one(self, feedback: AnnotationFeedback) -> ResynthesisResult:
        """
        Core method: rebuild BusinessContext for a single entity.

        Steps:
          1. Fetch existing node context from Java (previous annotations + git history)
          2. Inject the new annotation as a high-confidence commit cluster entry
          3. Run Stage 3 (synthesis) for just this entity
          4. Run Stage 3.5 (tokenization) for T0/T1
          5. Push result back to Java
        """
        log.info(
            "Feedback re-synthesis starting",
            entity=feedback.entity_name,
            annotation_type=feedback.annotation_type,
            workspace=feedback.workspace_id,
        )

        try:
            # Step 1 — build a minimal ExtractedEntity from the feedback payload
            entity = _build_entity(feedback)

            # Step 2 — fetch prior context from Java (best-effort)
            existing_context = await _fetch_existing_context(feedback)

            # Step 3 — inject the new annotation as a synthetic high-signal cluster
            clusters = _annotation_to_cluster(feedback, existing_context)

            # Step 4 — re-synthesise (single LLM call)
            annotations_list = _build_annotations_list(feedback, existing_context)
            business_ctx = await self._synthesizer._synthesise_entity(
                entity=entity,
                clusters=clusters,
                annotations=annotations_list,
                related_contexts={},
            )

            if not business_ctx:
                # LLM returned nothing usable — construct a minimal fallback
                business_ctx = _fallback_context(feedback)

            # Step 5 — generate T0/T1 tokens
            token = self._tokenizer._tokenize_one(entity, business_ctx)

            result = ResynthesisResult(
                external_id=feedback.external_id,
                entity_name=feedback.entity_name,
                business_context=business_ctx,
                t0=token.t0,
                t1=token.t1,
            )

            # Step 6 — push back to Java (fire-and-forget, timeout 15s)
            if feedback.callback_url:
                await _push_to_java(result, feedback)

            log.info(
                "Feedback re-synthesis complete",
                entity=feedback.entity_name,
                change_risk=business_ctx.change_risk,
            )
            return result

        except Exception as e:
            log.error(
                "Feedback re-synthesis failed",
                entity=feedback.entity_name,
                error=str(e),
            )
            return ResynthesisResult(
                external_id=feedback.external_id,
                entity_name=feedback.entity_name,
                business_context=_fallback_context(feedback),
                t0=f"{feedback.entity_name} ({feedback.entity_type}) — UNKNOWN risk — {feedback.entity_file.split('/')[-1]}",
                t1=f"{feedback.entity_type} `{feedback.entity_name}`\n(Re-synthesis failed: {e})",
                success=False,
                error=str(e),
            )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _build_entity(feedback: AnnotationFeedback) -> ExtractedEntity:
    """Reconstruct a minimal ExtractedEntity from the feedback payload."""
    return ExtractedEntity(
        entity_type=feedback.entity_type or "Function",
        name=feedback.entity_name,
        file=feedback.entity_file,
        repo="",
        signature="",
        last_modified_commit="",
        confidence=1.0,
        external_id=feedback.external_id,
    )


def _annotation_to_cluster(
    feedback: AnnotationFeedback,
    existing_context: list[dict],
) -> list[CommitCluster]:
    """
    Build a CommitCluster list that puts the new annotation front-and-centre.

    The new annotation is injected as a synthetic CommitEvent whose pr_body
    carries the annotation text — ContextSynthesizer treats pr_body as the
    highest-signal input alongside user annotations.
    """
    import datetime as _dt

    now = _dt.datetime.now()

    # New annotation as synthetic commit (highest signal)
    annotation_commit = CommitEvent(
        commit_hash="human-annotation-" + feedback.node_id[:8],
        timestamp=now,
        author=feedback.author or "human",
        message=f"[Human annotation: {feedback.annotation_type}] {feedback.annotation_text[:200]}",
        repo=feedback.entity_file.split("/")[0] if "/" in feedback.entity_file else "unknown",
        repo_type=RepoType.BACKEND,
        file_path=feedback.entity_file,
        pr_title=f"Annotation ({feedback.annotation_type}): {feedback.entity_name}",
        pr_body=feedback.annotation_text,
    )

    # Existing git commits from prior context (background)
    prior_commits = []
    for ctx_entry in existing_context:
        if ctx_entry.get("context_type") in ("git_commit", "pull_request"):
            try:
                prior_commits.append(CommitEvent(
                    commit_hash=(ctx_entry.get("source_id") or "prior")[:40],
                    timestamp=now,
                    author=ctx_entry.get("author", ""),
                    message=ctx_entry.get("title", ""),
                    repo="",
                    repo_type=RepoType.BACKEND,
                    file_path=feedback.entity_file,
                    pr_title=ctx_entry.get("title", ""),
                    pr_body=(ctx_entry.get("body") or "")[:500],
                ))
            except Exception:
                pass

    cluster = CommitCluster(
        cluster_id=annotation_commit.commit_hash,
        approximate_date=now,
        commits=[annotation_commit] + prior_commits[:5],
        cluster_reason="user_annotation",
    )
    return [cluster]


def _build_annotations_list(
    feedback: AnnotationFeedback,
    existing_context: list[dict],
) -> list[dict]:
    """
    Build the annotations list that ContextSynthesizer.synthesise_all receives.

    The new annotation goes first (highest signal). Prior user_annotations follow.
    """
    new_annotation = {
        "annotation_type": feedback.annotation_type,
        "entity_name": feedback.entity_name,
        "text": feedback.annotation_text,
        "author": feedback.author,
    }
    prior_annotations = [
        {
            "annotation_type": e.get("annotation_type", "note"),
            "entity_name": feedback.entity_name,
            "text": (e.get("body") or "")[:300],
        }
        for e in existing_context
        if e.get("context_type") == "user_annotation"
    ]
    return [new_annotation] + prior_annotations


async def _fetch_existing_context(feedback: AnnotationFeedback) -> list[dict]:
    """
    Best-effort fetch of prior NodeContext entries from Java.
    Returns empty list on any error — we still re-synthesise with just the new annotation.
    """
    if not feedback.callback_url:
        return []

    # Derive base URL from callback_url (strip path)
    base = feedback.callback_url.split("/v1/")[0] if "/v1/" in feedback.callback_url else ""
    if not base:
        return []

    url = f"{base}/v1/nodes/{feedback.node_id}/context"
    headers = {"X-Internal-Key": feedback.callback_key} if feedback.callback_key else {}

    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code == 200:
                data = resp.json()
                return data.get("items", [])
    except Exception as e:
        log.debug("Could not fetch prior context", entity=feedback.entity_name, error=str(e))
    return []


async def _push_to_java(result: ResynthesisResult, feedback: AnnotationFeedback) -> None:
    """
    Push the re-synthesised BusinessContext back to Java as a slim pipeline result.

    We POST to the same /v1/internal/pipeline-result endpoint used by the full
    pipeline but with only one entity and one context — Java merges it into the
    existing node using its standard upsert logic.
    """
    ctx = result.business_context
    payload = {
        "jobId": f"feedback-{feedback.node_id[:8]}",
        "workspaceId": feedback.workspace_id,
        "entities": [{
            "externalId": result.external_id,
            "nodeType": feedback.entity_type,
            "name": result.entity_name,
            "file": feedback.entity_file,
            "repo": "",
            "signature": "",
            "confidence": 1.0,
            "metadata": {
                "t0": result.t0,
                "t1": result.t1,
                "annotation_resynthesis": True,
                "annotation_type": feedback.annotation_type,
            },
        }],
        "relationships": [],
        "businessContexts": [{
            "externalId": result.external_id,
            "purpose": ctx.purpose,
            "historySummary": ctx.history_summary,
            "invariants": ctx.invariants,
            "changeRisk": ctx.change_risk,
            "changeRiskReason": ctx.change_risk_reason,
            "ownerTeam": ctx.owner_team,
            "externalDependencies": ctx.external_dependencies,
            "sourceConfidence": ctx.source_confidence,
            "gaps": ctx.gaps,
        }],
        "gaps": [],
        "stages": [{"stage": "3-feedback", "label": "Annotation Re-synthesis", "entities": 1}],
        "intentContexts": {},
        "memoryTokens": {result.external_id: {"t0": result.t0, "t1": result.t1}},
    }

    headers = {"X-Internal-Key": feedback.callback_key, "Content-Type": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(feedback.callback_url, json=payload, headers=headers)
            if resp.status_code in (200, 204):
                log.info("Pushed re-synthesis result to Java", entity=result.entity_name)
            else:
                log.warning(
                    "Java rejected re-synthesis push",
                    status=resp.status_code,
                    entity=result.entity_name,
                )
    except Exception as e:
        log.error("Failed to push re-synthesis result", entity=result.entity_name, error=str(e))


def _fallback_context(feedback: AnnotationFeedback) -> BusinessContext:
    """Minimal BusinessContext when LLM synthesis fails."""
    return BusinessContext(
        purpose=f"[Annotation: {feedback.annotation_type}] {feedback.annotation_text[:300]}",
        history_summary="Re-synthesis from human annotation.",
        invariants=[],
        change_risk="MEDIUM",
        change_risk_reason="Unable to synthesise — annotation applied directly.",
        owner_team=None,
        external_dependencies=[],
        source_confidence="high",   # annotation = high confidence
        gaps=[],
    )
