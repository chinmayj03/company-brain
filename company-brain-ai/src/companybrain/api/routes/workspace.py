import os

import structlog
from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import text

from companybrain.db import get_session

log = structlog.get_logger(__name__)
router = APIRouter()


class WorkspaceMeta(BaseModel):
    id: str
    name: str
    slug: str
    repo_count: int
    source_count: int


@router.get("/{workspace_id}", response_model=WorkspaceMeta)
async def get_workspace(workspace_id: str) -> WorkspaceMeta:
    try:
        async with get_session() as session:
            row = (await session.execute(
                text("SELECT id, name, slug FROM workspaces WHERE id = :id"),
                {"id": workspace_id},
            )).fetchone()
            if row:
                repo_count = (await session.execute(
                    text("SELECT COUNT(*) FROM repos WHERE workspace_id = :id"),
                    {"id": workspace_id},
                )).scalar() or 0
                source_count = (await session.execute(
                    text("SELECT COUNT(*) FROM workspace_sources WHERE workspace_id = :id"),
                    {"id": workspace_id},
                )).scalar() or 0
                return WorkspaceMeta(
                    id=str(row.id),
                    name=row.name,
                    slug=row.slug,
                    repo_count=int(repo_count),
                    source_count=int(source_count),
                )
    except Exception:
        log.warning("workspace_db_lookup_failed", workspace_id=workspace_id)

    # Env / default fallback
    name = os.getenv("CB_WORKSPACE_NAME") or os.path.basename(os.getcwd()) or "workspace"
    slug = name.lower().replace(" ", "-")
    return WorkspaceMeta(
        id=workspace_id,
        name=name,
        slug=slug,
        repo_count=0,
        source_count=0,
    )
