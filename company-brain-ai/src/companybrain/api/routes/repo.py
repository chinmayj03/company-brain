"""
Repo utility routes — used by the frontend to inspect local git repos.

GET /repo/branches?local_path=/path/to/repo
  Returns available branches + the active (checked-out) branch.
  Called when the user pastes a local path into the repo input
  so the UI can show a branch dropdown populated from real git data.
"""

from fastapi import APIRouter, Query
from pathlib import Path
import structlog

log = structlog.get_logger(__name__)
router = APIRouter()


@router.get("/branches")
async def get_repo_branches(local_path: str = Query(..., description="Absolute path to a local git repo")):
    """
    Return all branches in a local git repository.
    The active branch is returned separately so the UI can pre-select it.
    """
    try:
        from git import Repo, InvalidGitRepositoryError, NoSuchPathError
    except ImportError:
        return {"branches": [], "active": "main", "error": "gitpython not installed"}

    path = Path(local_path.strip())

    if not path.exists():
        return {"branches": [], "active": "main", "error": f"Path does not exist: {local_path}"}

    try:
        repo = Repo(str(path), search_parent_directories=True)
    except Exception as e:
        return {"branches": [], "active": "main", "error": f"Not a git repo: {e}"}

    try:
        # Local branches
        local_branches = [h.name for h in repo.heads]

        # Remote-tracking branches (strip "origin/" prefix, deduplicate)
        remote_branches = []
        for ref in repo.remotes[0].refs if repo.remotes else []:
            name = ref.name.split("/", 1)[-1]  # drop "origin/"
            if name not in ("HEAD",) and name not in local_branches:
                remote_branches.append(name)

        all_branches = local_branches + remote_branches

        # Active branch (may be detached HEAD)
        try:
            active = repo.active_branch.name
        except TypeError:
            active = repo.head.commit.hexsha[:7]  # detached HEAD

        log.info(
            "Repo branches fetched",
            path=str(path),
            local=len(local_branches),
            remote=len(remote_branches),
            active=active,
        )

        return {
            "branches": all_branches,
            "active": active,
            "repo_root": str(repo.working_dir),
        }

    except Exception as e:
        log.warning("Failed to list branches", path=str(path), error=str(e))
        return {"branches": ["main", "master", "develop"], "active": "main", "error": str(e)}
