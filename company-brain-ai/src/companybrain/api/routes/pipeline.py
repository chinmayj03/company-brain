"""
Pipeline AI routes — LLM inference only.

POST /pipeline/run  — called by the Java backend (PipelineService.dispatchToAi).
                      Receives job context, runs all LLM passes, then POSTs
                      results back to Java via callback_url.

The AI service owns zero persistence. Job state is tracked in Java's
pipeline_jobs table. The frontend polls Java's GET /v1/pipeline/jobs/{id}.
"""
import json
from datetime import datetime
from typing import Optional

import httpx
from fastapi import APIRouter, BackgroundTasks
from pydantic import BaseModel

from companybrain.models.entities import PipelineStartRequest, RepoConfig, RepoType
from companybrain.pipeline.orchestrator import run_pipeline, PipelineResult

router = APIRouter()


class AiRunRequest(BaseModel):
    """Payload sent by the Java backend when it delegates to the AI service."""
    job_id: str
    workspace_id: str
    endpoint_path: str
    http_method: str = "GET"
    branch: str = "main"
    repos: list[dict] = []
    callback_url: Optional[str] = None   # Java's /v1/internal/pipeline-result
    callback_key: Optional[str] = None   # X-Internal-Key for callback auth


@router.post("/run")
async def run_from_java(request: AiRunRequest, background_tasks: BackgroundTasks):
    """
    Called by the Java backend (PipelineService.dispatchToAi).
    Returns 202 immediately; runs the full LLM pipeline in the background,
    then POSTs results back to Java via callback_url.
    """
    background_tasks.add_task(_run_and_callback, request)
    return {"accepted": True, "job_id": request.job_id}


async def _run_and_callback(request: AiRunRequest):
    log_entries: list[dict] = []

    async def on_progress(stage: str, emoji: str, message: str, data: dict):
        entry = {
            "stage": stage, "emoji": emoji, "message": message,
            "ts": datetime.utcnow().isoformat(),
            **{k: v for k, v in data.items() if isinstance(v, (str, int, float, bool, list, type(None)))},
        }
        log_entries.append(entry)
        if request.callback_url:
            progress_url = request.callback_url.replace(
                "/pipeline-result", "/pipeline-progress"
            )
            try:
                async with httpx.AsyncClient(timeout=5) as client:
                    await client.post(
                        progress_url,
                        json={"jobId": request.job_id, "logs": [entry]},
                        headers={"X-Internal-Key": request.callback_key or ""},
                    )
            except Exception:
                pass

    repos = [
        RepoConfig(
            local_path=r.get("local_path"),
            url=r.get("url"),
            type=RepoType(r.get("type", "backend")),
            branch=r.get("branch", request.branch),
        )
        for r in request.repos
    ]

    pipeline_request = PipelineStartRequest(
        endpoint_path=request.endpoint_path,
        http_method=request.http_method,
        branch=request.branch,
        repos=repos,
        workspace_id=request.workspace_id,
    )
    pipeline_request.__dict__["job_id"] = request.job_id

    started_at = datetime.utcnow().isoformat()
    result: Optional[PipelineResult] = None
    error: Optional[str] = None

    try:
        result = await run_pipeline(
            pipeline_request,
            on_progress=on_progress,
            java_callback_url=request.callback_url,
            java_callback_key=request.callback_key,
        )
    except Exception as exc:
        error = str(exc)

    if request.callback_url:
        payload = {
            "job_id":    request.job_id,
            "workspace_id": request.workspace_id,
            "status":    result.status if result else "failed",
            "error":     error or (result.error if result else None),
            "started_at": started_at,
            "completed_at": datetime.utcnow().isoformat(),
            "progress_logs": log_entries,
            "entity_count":      result.entity_count      if result else 0,
            "edge_count":        result.edge_count         if result else 0,
            "gap_count":         result.gap_count          if result else 0,
            "code_units_found":  result.code_units_found   if result else 0,
            "git_commits_found": result.git_commits_found  if result else 0,
            "files_traced":      (result.files_traced[:20] if result else []),
            "stages_summary":    (result.stages_summary    if result else []),
        }
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                await client.post(
                    request.callback_url,
                    json=payload,
                    headers={"X-Internal-Key": request.callback_key or ""},
                )
        except Exception:
            pass
