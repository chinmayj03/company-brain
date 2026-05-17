"""
Source Registry — ADR-0074.

GET    /workspaces/{workspace_id}/sources                    — list workspace sources
POST   /workspaces/{workspace_id}/sources                    — register a source (+ optional auto-index)
DELETE /workspaces/{workspace_id}/sources/{source_id}        — remove a source
POST   /workspaces/{workspace_id}/sources/{source_id}/sync   — trigger sync (202 Accepted)
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import List, Optional
from uuid import UUID

import structlog
from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy import text

from companybrain.db import get_session

log = structlog.get_logger(__name__)
router = APIRouter()


class WorkspaceSource(BaseModel):
    id: UUID
    workspace_id: UUID
    kind: str
    display_name: str
    url: Optional[str]
    last_synced_at: Optional[datetime]
    sync_status: str
    error_message: Optional[str]
    meta: Optional[dict]
    config: dict = {}
    entity_count: int = 0


class RegisterSourceRequest(BaseModel):
    kind: str
    display_name: str
    config: dict
    auto_index: bool = True


class RegisterSourceResponse(BaseModel):
    source: WorkspaceSource
    job_id: Optional[str] = None


@router.get("/workspaces/{workspace_id}/sources", response_model=List[WorkspaceSource])
async def list_sources(workspace_id: UUID) -> List[WorkspaceSource]:
    """Return all sources registered for a workspace, ordered by display_name."""
    sql = text("""
        SELECT id, workspace_id, kind, display_name, url,
               last_synced_at, sync_status, error_message, meta,
               COALESCE(config, '{}') AS config,
               COALESCE(entity_count, 0) AS entity_count
        FROM workspace_sources
        WHERE workspace_id = :workspace_id
        ORDER BY display_name
    """)

    async with get_session() as session:
        result = await session.execute(sql, {"workspace_id": str(workspace_id)})
        rows = result.mappings().all()

    sources = [
        WorkspaceSource(
            id=row["id"],
            workspace_id=row["workspace_id"],
            kind=row["kind"],
            display_name=row["display_name"],
            url=row["url"],
            last_synced_at=row["last_synced_at"],
            sync_status=row["sync_status"],
            error_message=row["error_message"],
            meta=row["meta"],
            config=row["config"] if isinstance(row["config"], dict) else {},
            entity_count=row["entity_count"] or 0,
        )
        for row in rows
    ]
    log.info("Listed workspace sources", workspace_id=str(workspace_id), count=len(sources))
    return sources


@router.post("/workspaces/{workspace_id}/sources", response_model=RegisterSourceResponse, status_code=201)
async def register_source(workspace_id: UUID, body: RegisterSourceRequest) -> RegisterSourceResponse:
    """
    Register a new source and optionally trigger indexing.
    The config blob is stored as-is; validation is the connector's responsibility.
    """
    import uuid as _uuid
    source_id = str(_uuid.uuid4())

    sql_insert = text("""
        INSERT INTO workspace_sources
          (id, workspace_id, kind, display_name, url, sync_status, config)
        VALUES
          (:id, :workspace_id, :kind, :display_name, :url, 'pending', :config::jsonb)
        RETURNING id, workspace_id, kind, display_name, url, last_synced_at,
                  sync_status, error_message, meta,
                  COALESCE(config, '{}') AS config,
                  COALESCE(entity_count, 0) AS entity_count
    """)

    async with get_session() as session:
        result = await session.execute(sql_insert, {
            "id": source_id,
            "workspace_id": str(workspace_id),
            "kind": body.kind,
            "display_name": body.display_name,
            "url": body.config.get("spec_path_or_url") or body.config.get("clone_url") or body.config.get("repo_path"),
            "config": json.dumps(body.config),
        })
        await session.commit()
        row = result.mappings().fetchone()

    source = WorkspaceSource(
        id=row["id"],
        workspace_id=row["workspace_id"],
        kind=row["kind"],
        display_name=row["display_name"],
        url=row["url"],
        last_synced_at=row["last_synced_at"],
        sync_status=row["sync_status"],
        error_message=row["error_message"],
        meta=row["meta"],
        config=row["config"] if isinstance(row["config"], dict) else {},
        entity_count=row["entity_count"] or 0,
    )
    job_id = None

    if body.auto_index and body.kind in ("git_local", "git_remote"):
        import uuid as _uuid2
        import asyncio
        job_id = str(_uuid2.uuid4())
        asyncio.create_task(_index_source(source_id, str(workspace_id), body))

    log.info("Source registered", source_id=source_id, kind=body.kind, auto_index=body.auto_index)
    return RegisterSourceResponse(source=source, job_id=job_id)


async def _index_source(source_id: str, workspace_id: str, body: RegisterSourceRequest) -> None:
    """Background task: run pipeline and update source status."""
    try:
        from companybrain.db import get_session
        from sqlalchemy import text

        async with get_session() as session:
            await session.execute(text(
                "UPDATE workspace_sources SET sync_status='syncing' WHERE id=:id"
            ), {"id": source_id})
            await session.commit()

        from companybrain.pipeline.orchestrator import run_pipeline
        from companybrain.api.routes.pipeline import PipelineStartRequest
        from companybrain.models.entities import RepoConfig, RepoType

        repo_path = body.config.get("repo_path") or body.config.get("clone_url", "")
        branch = body.config.get("branch", "main")

        pipeline_req = PipelineStartRequest(
            endpoint_path=repo_path,
            http_method="SCAN",
            branch=branch,
            repos=[RepoConfig(
                local_path=repo_path,
                type=RepoType.backend,
                branch=branch,
            )],
            workspace_id=workspace_id,
        )
        result = await run_pipeline(pipeline_req)

        async with get_session() as session:
            await session.execute(text("""
                UPDATE workspace_sources
                SET sync_status='ok', last_synced_at=NOW(), entity_count=:count
                WHERE id=:id
            """), {"id": source_id, "count": getattr(result, "entity_count", 0)})
            await session.commit()

    except Exception as exc:
        try:
            from companybrain.db import get_session
            from sqlalchemy import text
            async with get_session() as session:
                await session.execute(text("""
                    UPDATE workspace_sources
                    SET sync_status='error', error_message=:msg
                    WHERE id=:id
                """), {"id": source_id, "msg": str(exc)[:500]})
                await session.commit()
        except Exception:
            pass
        log.error("Source indexing failed", source_id=source_id, error=str(exc))


@router.delete("/workspaces/{workspace_id}/sources/{source_id}", status_code=204)
async def delete_source(workspace_id: UUID, source_id: UUID) -> None:
    sql = text("""
        DELETE FROM workspace_sources
        WHERE id = :source_id AND workspace_id = :workspace_id
    """)
    async with get_session() as session:
        await session.execute(sql, {
            "source_id": str(source_id),
            "workspace_id": str(workspace_id),
        })
        await session.commit()
    log.info("Source deleted", source_id=str(source_id))


@router.post(
    "/workspaces/{workspace_id}/sources/{source_id}/sync",
    status_code=202,
)
async def trigger_sync(workspace_id: UUID, source_id: UUID) -> dict:
    """
    Mark a source as syncing and return 202 Accepted.
    The actual sync is handled by a background worker; this endpoint
    only sets the sync_status flag so the UI can show a spinner.
    """
    sql_check = text("""
        SELECT id FROM workspace_sources
        WHERE id = :source_id AND workspace_id = :workspace_id
    """)
    sql_update = text("""
        UPDATE workspace_sources
        SET sync_status = 'syncing'
        WHERE id = :source_id AND workspace_id = :workspace_id
    """)

    async with get_session() as session:
        result = await session.execute(
            sql_check,
            {"source_id": str(source_id), "workspace_id": str(workspace_id)},
        )
        if result.fetchone() is None:
            raise HTTPException(status_code=404, detail="Source not found")

        await session.execute(
            sql_update,
            {"source_id": str(source_id), "workspace_id": str(workspace_id)},
        )
        await session.commit()

    log.info(
        "Source sync triggered",
        workspace_id=str(workspace_id),
        source_id=str(source_id),
    )
    return {"status": "accepted", "source_id": str(source_id)}
