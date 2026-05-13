"""
Source Registry — ADR-0072 item A4.

GET  /workspaces/{workspace_id}/sources           — list workspace sources
POST /workspaces/{workspace_id}/sources/{source_id}/sync — trigger sync (202 Accepted)
"""
from __future__ import annotations

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


@router.get("/workspaces/{workspace_id}/sources", response_model=List[WorkspaceSource])
async def list_sources(workspace_id: UUID) -> List[WorkspaceSource]:
    """Return all sources registered for a workspace, ordered by display_name."""
    sql = text("""
        SELECT id, workspace_id, kind, display_name, url,
               last_synced_at, sync_status, error_message, meta
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
        )
        for row in rows
    ]
    log.info("Listed workspace sources", workspace_id=str(workspace_id), count=len(sources))
    return sources


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
