"""
ADR-0074 — Source Registry routes.

Endpoints:
  GET    /workspaces/{workspace_id}/sources
  POST   /workspaces/{workspace_id}/sources
  DELETE /workspaces/{workspace_id}/sources/{source_id}
"""

import asyncio
import uuid
from typing import Optional

import structlog
from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy import text

from companybrain.db import get_session

log = structlog.get_logger(__name__)
router = APIRouter()


# ── Pydantic models ────────────────────────────────────────────────────────────

class WorkspaceSource(BaseModel):
    id: str
    workspace_id: str
    kind: str
    display_name: str
    url: Optional[str] = None
    last_synced_at: Optional[str] = None
    sync_status: str  # 'ok' | 'syncing' | 'error' | 'pending'
    error_message: Optional[str] = None
    config: dict = {}
    entity_count: int = 0


class RegisterSourceRequest(BaseModel):
    kind: str
    display_name: str
    config: dict = {}
    auto_index: bool = True


class RegisterSourceResponse(BaseModel):
    source: WorkspaceSource
    job_id: Optional[str] = None


# ── Helpers ────────────────────────────────────────────────────────────────────

def _row_to_source(row) -> WorkspaceSource:
    return WorkspaceSource(
        id=str(row.id),
        workspace_id=str(row.workspace_id),
        kind=row.kind,
        display_name=row.display_name,
        url=row.url if hasattr(row, "url") else None,
        last_synced_at=row.last_synced_at.isoformat() if row.last_synced_at else None,
        sync_status=row.sync_status or "pending",
        error_message=getattr(row, "error_message", None),
        config=row.config if row.config else {},
        entity_count=row.entity_count if row.entity_count is not None else 0,
    )


async def _fire_background_index(source_id: str, workspace_id: str, kind: str, config: dict, job_id: str) -> None:
    """Dispatch background indexing — best-effort, does not raise on failure."""
    try:
        from companybrain.pipeline.orchestrator import Orchestrator  # type: ignore
        await Orchestrator().run_source(
            source_id=source_id,
            workspace_id=workspace_id,
            kind=kind,
            config=config,
            job_id=job_id,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "background_index_fire_failed",
            source_id=source_id,
            job_id=job_id,
            error=str(exc),
        )


# ── Routes ─────────────────────────────────────────────────────────────────────

@router.get("/{workspace_id}/sources", response_model=list[WorkspaceSource])
async def list_sources(workspace_id: str) -> list[WorkspaceSource]:
    """Return all sources registered for a workspace."""
    try:
        async with get_session() as session:
            rows = (await session.execute(
                text("""
                    SELECT id, workspace_id, kind, display_name,
                           url, last_synced_at, sync_status, error_message,
                           config, entity_count
                    FROM workspace_sources
                    WHERE workspace_id = :wid
                    ORDER BY created_at DESC NULLS LAST, id
                """),
                {"wid": workspace_id},
            )).fetchall()
            return [_row_to_source(r) for r in rows]
    except Exception as exc:
        log.warning("list_sources_db_failed", workspace_id=workspace_id, error=str(exc))
        return []


@router.post("/{workspace_id}/sources", response_model=RegisterSourceResponse, status_code=201)
async def register_source(workspace_id: str, body: RegisterSourceRequest) -> RegisterSourceResponse:
    """Register a new source and optionally fire background indexing."""
    source_id = str(uuid.uuid4())
    job_id: Optional[str] = None

    try:
        async with get_session() as session:
            await session.execute(
                text("""
                    INSERT INTO workspace_sources
                        (id, workspace_id, kind, display_name, config, sync_status, entity_count)
                    VALUES
                        (:id, :wid, :kind, :display_name, cast(:config as jsonb), 'pending', 0)
                """),
                {
                    "id": source_id,
                    "wid": workspace_id,
                    "kind": body.kind,
                    "display_name": body.display_name,
                    "config": __import__("json").dumps(body.config),
                },
            )
            await session.commit()
    except Exception as exc:
        log.error("register_source_db_failed", error=str(exc))
        raise HTTPException(status_code=500, detail=f"Failed to register source: {exc}") from exc

    # Construct the response object (no second DB round-trip needed)
    source = WorkspaceSource(
        id=source_id,
        workspace_id=workspace_id,
        kind=body.kind,
        display_name=body.display_name,
        url=None,
        last_synced_at=None,
        sync_status="pending",
        config=body.config,
        entity_count=0,
    )

    if body.auto_index:
        job_id = str(uuid.uuid4())
        asyncio.create_task(
            _fire_background_index(source_id, workspace_id, body.kind, body.config, job_id)
        )
        log.info("background_index_dispatched", source_id=source_id, job_id=job_id)

    return RegisterSourceResponse(source=source, job_id=job_id)


@router.delete("/{workspace_id}/sources/{source_id}", status_code=204)
async def delete_source(workspace_id: str, source_id: str) -> Response:
    """Remove a source from the workspace."""
    try:
        async with get_session() as session:
            await session.execute(
                text("""
                    DELETE FROM workspace_sources
                    WHERE id = :sid AND workspace_id = :wid
                """),
                {"sid": source_id, "wid": workspace_id},
            )
            await session.commit()
    except Exception as exc:
        log.error("delete_source_db_failed", error=str(exc))
        raise HTTPException(status_code=500, detail=f"Failed to delete source: {exc}") from exc

    return Response(status_code=204)
