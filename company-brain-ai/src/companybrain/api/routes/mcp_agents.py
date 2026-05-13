"""
MCP Agent Telemetry — ADR-0072 item A3.

GET /mcp/agents?workspace_id={id}
    Returns a list of MCP agent sessions with computed live/idle/gone status.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional
from uuid import UUID

import structlog
from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import text

from companybrain.db import get_session

log = structlog.get_logger(__name__)
router = APIRouter()

# Status thresholds (seconds)
_LIVE_THRESHOLD = 60
_IDLE_THRESHOLD = 300


class AgentSession(BaseModel):
    id: UUID
    agent_name: str
    client_id: str
    connected_at: datetime
    last_ping_at: datetime
    query_count: int
    qpm: float
    status: str  # "live" | "idle" | "gone"


def _compute_status(last_ping_at: datetime, disconnected_at: Optional[datetime]) -> str:
    """Compute live/idle/gone status from DB row timestamps."""
    if disconnected_at is not None:
        return "gone"
    now = datetime.now(timezone.utc)
    # Ensure last_ping_at is tz-aware
    if last_ping_at.tzinfo is None:
        last_ping_at = last_ping_at.replace(tzinfo=timezone.utc)
    age_secs = (now - last_ping_at).total_seconds()
    if age_secs <= _LIVE_THRESHOLD:
        return "live"
    if age_secs <= _IDLE_THRESHOLD:
        return "idle"
    return "gone"


@router.get("/agents", response_model=List[AgentSession])
async def list_mcp_agents(
    workspace_id: UUID = Query(..., description="Workspace UUID"),
) -> List[AgentSession]:
    """
    Return all MCP agent sessions for a workspace, newest first.
    Status is computed server-side:
      live — last_ping_at within 60 s, not disconnected
      idle — last_ping_at within 300 s, not disconnected
      gone — disconnected or no ping in 300 s
    qpm is approximated as total query_count (no per-minute history yet).
    """
    sql = text("""
        SELECT id, agent_name, client_id, connected_at, last_ping_at,
               query_count, disconnected_at
        FROM mcp_agent_sessions
        WHERE workspace_id = :workspace_id
        ORDER BY connected_at DESC
    """)

    async with get_session() as session:
        result = await session.execute(sql, {"workspace_id": str(workspace_id)})
        rows = result.mappings().all()

    sessions: List[AgentSession] = []
    for row in rows:
        status = _compute_status(row["last_ping_at"], row["disconnected_at"])
        sessions.append(AgentSession(
            id=row["id"],
            agent_name=row["agent_name"],
            client_id=row["client_id"],
            connected_at=row["connected_at"],
            last_ping_at=row["last_ping_at"],
            query_count=row["query_count"],
            qpm=float(row["query_count"]),  # rough proxy — no per-minute history yet
            status=status,
        ))

    log.info("Listed MCP agent sessions", workspace_id=str(workspace_id), count=len(sessions))
    return sessions
