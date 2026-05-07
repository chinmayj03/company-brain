"""
Pipeline AI routes — LLM inference only.

POST /pipeline/run    — called by Java backend to run LLM extraction.
                        Receives job context, runs all 4 LLM passes,
                        then posts results back to Java via callback URL.

POST /pipeline/start  — legacy direct-from-frontend entry point.
                        Kept for backward compatibility during transition.
                        Stores job state in Redis; Java backend is preferred.

GET  /pipeline/jobs/{id} — legacy job polling via Redis.

Architecture note:
  The Java backend is the authoritative orchestrator in the new design.
  The frontend calls Java → Java calls this /pipeline/run endpoint →
  AI service POSTs results back to Java's /v1/internal/pipeline-result.
  The AI service owns ZERO persistence — it is a pure LLM inference service.
"""
import json
import uuid
from datetime import datetime
from typing import Optional

import redis.asyncio as aioredis
from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel

from companybrain.config import settings
from companybrain.models.entities import PipelineStartRequest, PipelineJobResponse, RepoConfig, RepoType
from companybrain.pipeline.orchestrator import run_pipeline, PipelineResult

router = APIRouter()
JOB_TTL = 7200


def _redis():
    return aioredis.from_url(settings.redis_url, decode_responses=True)


# ── New: Java-initiated run ───────────────────────────────────────────────────

class AiRunRequest(BaseModel):
    """Payload sent by the Java backend when it delegates to the AI service."""
    job_id: str
    workspace_id: str
    endpoint_path: str
    http_method: str = "GET"
    branch: str = "main"
    repos: list[dict] = []
    callback_url: Optional[str] = None    # Java's /v1/internal/pipeline-result
    callback_key: Optional[str] = None    # X-Internal-Key for callback auth


@router.post("/run")
async def run_from_java(request: AiRunRequest, background_tasks: BackgroundTasks):
    """
    Called by the Java backend (PipelineService.dispatchToAi).
    Returns 202 immediately; runs the full LLM pipeline in the background,
    then POSTs results back to Java via callback_url.
    """
    background_tasks.add_task(
        _run_and_callback, request
    )
    return {"accepted": True, "job_id": request.job_id}


async def _run_and_callback(request: AiRunRequest):
    """
    Runs the pipeline and posts results back to Java.
    Also pushes live progress to Redis so the frontend can poll status.
    """
    log_entries: list[dict] = []
    r = _redis()

    # Seed the Redis key so the frontend can start polling immediately
    try:
        await r.setex(f"job:{request.job_id}", JOB_TTL, json.dumps({
            "status": "running", "job_id": request.job_id,
            "started_at": datetime.utcnow().isoformat(),
            "progress": {"logs": [], "current_stage": "starting"},
        }))
    except Exception:
        pass

    async def on_progress(stage: str, emoji: str, message: str, data: dict):
        entry = {
            "stage": stage, "emoji": emoji, "message": message,
            "ts": datetime.utcnow().isoformat(),
            **{k: v for k, v in data.items() if isinstance(v, (str, int, float, bool, list, type(None)))},
        }
        log_entries.append(entry)
        # Push live to Redis so frontend polling sees current stage immediately
        try:
            await r.setex(f"job:{request.job_id}", JOB_TTL, json.dumps({
                "status": "running", "job_id": request.job_id,
                "progress": {"logs": log_entries, "current_stage": stage},
            }))
        except Exception:
            pass

    # Build PipelineStartRequest from the Java payload
    repos = []
    for r in request.repos:
        repos.append(RepoConfig(
            local_path=r.get("local_path"),
            url=r.get("url"),
            type=RepoType(r.get("type", "backend")),
            branch=r.get("branch", "main"),
        ))

    pipeline_request = PipelineStartRequest(
        endpoint_path=request.endpoint_path,
        http_method=request.http_method,
        branch=request.branch,
        repos=repos,
        workspace_id=request.workspace_id,
    )
    # Attach job_id so orchestrator can pass it to JavaGraphClient
    pipeline_request.__dict__["job_id"] = request.job_id

    await run_pipeline(
        pipeline_request,
        on_progress=on_progress,
        java_callback_url=request.callback_url,
        java_callback_key=request.callback_key,
    )


# ── Legacy: direct-from-frontend (Redis-backed) ──────────────────────────────

@router.post("/start", response_model=PipelineJobResponse)
async def start_pipeline(request: PipelineStartRequest, background_tasks: BackgroundTasks):
    """Legacy direct-call entry point. Prefer routing through the Java backend."""
    job_id = str(uuid.uuid4())
    r = _redis()
    await r.setex(
        f"job:{job_id}", JOB_TTL,
        json.dumps({
            "status": "running", "job_id": job_id,
            "started_at": datetime.utcnow().isoformat(),
            "progress": {"logs": [], "current_stage": "starting"},
        }),
    )
    background_tasks.add_task(_run_and_store_redis, job_id, request, r)
    return PipelineJobResponse(job_id=job_id, status="running")


async def _run_and_store_redis(job_id: str, request: PipelineStartRequest, r):
    log_entries: list[dict] = []

    async def on_progress(stage: str, emoji: str, message: str, data: dict):
        entry = {
            "stage": stage, "emoji": emoji, "message": message,
            "ts": datetime.utcnow().isoformat(),
            **{k: v for k, v in data.items() if isinstance(v, (str, int, float, bool, list, type(None)))},
        }
        log_entries.append(entry)
        try:
            raw = await r.get(f"job:{job_id}")
            state = json.loads(raw) if raw else {}
            state["progress"] = {"logs": log_entries, "current_stage": stage}
            await r.setex(f"job:{job_id}", JOB_TTL, json.dumps(state))
        except Exception:
            pass

    result: PipelineResult = await run_pipeline(request, on_progress=on_progress)

    await r.setex(f"job:{job_id}", JOB_TTL, json.dumps({
        "status": result.status,
        "job_id": job_id,
        "started_at": log_entries[0]["ts"] if log_entries else None,
        "completed_at": datetime.utcnow().isoformat(),
        "result": {
            "entity_count": result.entity_count,
            "edge_count":   result.edge_count,
            "gap_count":    result.gap_count,
            "code_units_found":  result.code_units_found,
            "git_commits_found": result.git_commits_found,
            "files_traced":      result.files_traced[:20],
            "stages_summary":    result.stages_summary,
        },
        "error": result.error,
        "progress": {"logs": log_entries, "current_stage": "done" if result.status == "completed" else "error"},
    }))


@router.get("/jobs/{job_id}", response_model=PipelineJobResponse)
async def get_job(job_id: str):
    r = _redis()
    raw = await r.get(f"job:{job_id}")
    if not raw:
        raise HTTPException(status_code=404, detail="Job not found")
    return PipelineJobResponse(**json.loads(raw))
