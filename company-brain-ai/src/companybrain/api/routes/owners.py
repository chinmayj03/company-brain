import subprocess
from collections import Counter
from datetime import datetime, timezone
from urllib.parse import unquote

import structlog
from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import text

from companybrain.db import get_session

log = structlog.get_logger(__name__)
router = APIRouter()


class EntityOwner(BaseModel):
    email: str
    name: str
    commit_count: int
    last_commit_at: str
    pct: float


class OwnersResponse(BaseModel):
    urn: str
    owners: list[EntityOwner]
    bus_factor: int


@router.get("/{urn}/owners", response_model=OwnersResponse)
async def get_entity_owners(urn: str) -> OwnersResponse:
    decoded_urn = unquote(urn)
    empty = OwnersResponse(urn=decoded_urn, owners=[], bus_factor=0)

    # Resolve file_path + line range from brain store
    file_path: str | None = None
    line_start: int | None = None
    line_end: int | None = None
    repo_path: str | None = None

    try:
        async with get_session() as session:
            row = (await session.execute(
                text("""
                    SELECT e.file_path, e.line_start, e.line_end, r.repo_path
                    FROM entities e
                    LEFT JOIN repos r ON r.id = e.repo_id
                    WHERE e.urn = :urn
                    LIMIT 1
                """),
                {"urn": decoded_urn},
            )).fetchone()
            if row:
                file_path = row.file_path
                line_start = row.line_start
                line_end = row.line_end
                repo_path = row.repo_path
    except Exception:
        log.warning("owners_db_lookup_failed", urn=decoded_urn)

    if not file_path or not repo_path:
        return empty

    # Git log over the relevant line range
    try:
        cmd = [
            "git", "-C", repo_path, "log",
            "--follow", "--no-merges",
            "--since=90 days ago",
            "--format=%ae|%an|%ai",
        ]
        if line_start and line_end:
            cmd += [f"-L{line_start},{line_end}:{file_path}"]
        else:
            cmd += ["--", file_path]

        output = subprocess.check_output(
            cmd, text=True, timeout=5, stderr=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired:
        log.warning("git_blame_timeout", urn=decoded_urn)
        return empty
    except Exception as exc:
        log.warning("git_blame_failed", urn=decoded_urn, error=str(exc))
        return empty

    # Parse output: "email|name|date"
    commits_by_email: Counter[str] = Counter()
    name_by_email: dict[str, str] = {}
    last_by_email: dict[str, str] = {}

    for line in output.splitlines():
        line = line.strip()
        if not line or "|" not in line:
            continue
        parts = line.split("|", 2)
        if len(parts) < 2:
            continue
        email, name = parts[0].strip(), parts[1].strip()
        date_str = parts[2].strip() if len(parts) > 2 else ""
        if not email:
            continue
        commits_by_email[email] += 1
        name_by_email.setdefault(email, name)
        if date_str and (email not in last_by_email or date_str > last_by_email[email]):
            last_by_email[email] = date_str

    total = sum(commits_by_email.values())
    if total == 0:
        return empty

    top = commits_by_email.most_common(3)
    now_iso = datetime.now(tz=timezone.utc).isoformat()
    owners = [
        EntityOwner(
            email=email,
            name=name_by_email.get(email, email.split("@")[0]),
            commit_count=count,
            last_commit_at=last_by_email.get(email, now_iso),
            pct=round(count / total * 100, 1),
        )
        for email, count in top
    ]
    bus_factor = sum(1 for o in owners if o.pct >= 20)

    return OwnersResponse(urn=decoded_urn, owners=owners, bus_factor=max(bus_factor, 1))
