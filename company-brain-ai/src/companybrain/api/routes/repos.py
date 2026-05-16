import json
import os
import subprocess
from pathlib import Path

import structlog
from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import text

from companybrain.db import get_session

log = structlog.get_logger(__name__)
router = APIRouter()


class Repo(BaseModel):
    id: str
    display_name: str
    repo_path: str
    default_branch: str
    current_branch: str
    last_synced_at: str | None = None
    entity_count: int
    sync_status: str  # 'ok' | 'syncing' | 'error' | 'pending'


class BranchList(BaseModel):
    current: str
    branches: list[str]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _current_branch(repo_path: str) -> str:
    try:
        return subprocess.check_output(
            ["git", "-C", repo_path, "rev-parse", "--abbrev-ref", "HEAD"],
            text=True, timeout=2, stderr=subprocess.DEVNULL,
        ).strip() or "main"
    except Exception:
        return "main"


def _all_branches(repo_path: str) -> list[str]:
    try:
        output = subprocess.check_output(
            ["git", "-C", repo_path, "for-each-ref", "--format=%(refname:short)", "refs/heads/"],
            text=True, timeout=3, stderr=subprocess.DEVNULL,
        ).strip()
        branches = [b for b in output.splitlines() if b]
        return branches or ["main"]
    except Exception:
        return ["main"]


def _repo_from_path(repo_id: str, repo_path: str, last_synced_at: str | None = None) -> Repo:
    path_obj = Path(repo_path)
    exists = path_obj.exists()
    current = _current_branch(repo_path) if exists else "main"
    return Repo(
        id=repo_id,
        display_name=path_obj.name,
        repo_path=repo_path,
        default_branch=current,
        current_branch=current,
        last_synced_at=last_synced_at,
        entity_count=0,
        sync_status="ok" if exists else "error",
    )


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/{workspace_id}/repos", response_model=list[Repo])
async def list_repos(workspace_id: str) -> list[Repo]:
    # 1. Try Postgres
    try:
        async with get_session() as session:
            rows = (await session.execute(
                text("""
                    SELECT id, display_name, repo_path, default_branch,
                           last_synced_at, entity_count, sync_status
                    FROM repos WHERE workspace_id = :wid
                """),
                {"wid": workspace_id},
            )).fetchall()
            if rows:
                result = []
                for r in rows:
                    current = _current_branch(r.repo_path) if Path(r.repo_path).exists() else r.default_branch
                    status = r.sync_status if Path(r.repo_path).exists() else "error"
                    result.append(Repo(
                        id=str(r.id),
                        display_name=r.display_name,
                        repo_path=r.repo_path,
                        default_branch=r.default_branch or "main",
                        current_branch=current,
                        last_synced_at=r.last_synced_at.isoformat() if r.last_synced_at else None,
                        entity_count=r.entity_count or 0,
                        sync_status=status,
                    ))
                return result
    except Exception:
        log.warning("repos_db_lookup_failed", workspace_id=workspace_id)

    # 2. CB_REPO_PATH env fallback
    env_path = os.getenv("CB_REPO_PATH")
    if env_path:
        return [_repo_from_path("env-repo", env_path)]

    # 3. Manifest JSON fallback
    manifest_path = Path.home() / ".company-brain" / workspace_id / "manifest.json"
    if manifest_path.exists():
        try:
            data = json.loads(manifest_path.read_text())
            repos = data.get("repos", [])
            return [
                _repo_from_path(
                    r.get("id", f"repo-{i}"),
                    r["repo_path"],
                    r.get("last_synced_at"),
                )
                for i, r in enumerate(repos)
                if "repo_path" in r
            ]
        except Exception:
            log.warning("manifest_parse_failed", path=str(manifest_path))

    return []


@router.get("/{workspace_id}/repos/{repo_id}/branches", response_model=BranchList)
async def list_branches(workspace_id: str, repo_id: str) -> BranchList:
    # Resolve repo_path for this repo_id
    repo_path: str | None = None

    try:
        async with get_session() as session:
            row = (await session.execute(
                text("SELECT repo_path FROM repos WHERE id = :id AND workspace_id = :wid"),
                {"id": repo_id, "wid": workspace_id},
            )).fetchone()
            if row:
                repo_path = row.repo_path
    except Exception:
        log.warning("branches_db_lookup_failed", repo_id=repo_id)

    if not repo_path:
        env_path = os.getenv("CB_REPO_PATH")
        if env_path and (repo_id == "env-repo" or repo_id.startswith("repo-")):
            repo_path = env_path

    if not repo_path:
        return BranchList(current="main", branches=["main"])

    current = _current_branch(repo_path)
    branches = _all_branches(repo_path)
    return BranchList(current=current, branches=branches)
