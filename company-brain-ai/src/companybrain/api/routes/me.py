import os
import subprocess

import structlog
from fastapi import APIRouter
from pydantic import BaseModel

log = structlog.get_logger(__name__)
router = APIRouter()


class MeResponse(BaseModel):
    id: str
    display_name: str
    email: str
    workspace_id: str
    workspace_name: str


@router.get("/me", response_model=MeResponse)
async def get_me() -> MeResponse:
    name = os.getenv("CB_USER_NAME") or _git_config("user.name") or "You"
    email = os.getenv("CB_USER_EMAIL") or _git_config("user.email") or ""
    ws_id = os.getenv("CB_WORKSPACE_ID", "00000000-0000-0000-0000-000000000001")
    ws_name = os.getenv("CB_WORKSPACE_NAME") or _cwd_name() or "workspace"
    return MeResponse(
        id="local",
        display_name=name,
        email=email,
        workspace_id=ws_id,
        workspace_name=ws_name,
    )


def _git_config(key: str) -> str | None:
    try:
        value = subprocess.check_output(["git", "config", key], text=True, timeout=2).strip()
        return value or None
    except Exception:
        return None


def _cwd_name() -> str:
    return os.path.basename(os.getcwd())
