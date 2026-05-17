"""
ADR-0090 P1 — Event-stream REST endpoints.

Routes:
    GET /events/causal-chain/{urn}
        Returns the causal event trail for the given entity URN.
        Response: { urn, events: [...], count }

    GET /events/entity/{urn}
        Returns raw events for an entity (replay), with optional since/until.
        Response: { urn, events: [...], count }
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

import structlog
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from companybrain.config import settings
from companybrain.db import get_session

log = structlog.get_logger(__name__)

router = APIRouter()


# ── Response schemas ──────────────────────────────────────────────────────────

class EventOut(BaseModel):
    id: str
    workspace_id: str
    repo: str
    branch: str
    event_type: str
    payload: dict
    occurred_at: str
    recorded_at: str
    causal_parents: list[str]
    actors: list[str]
    urn_affected: Optional[str] = None


class CausalChainResponse(BaseModel):
    urn: str
    events: list[EventOut]
    count: int


class ReplayResponse(BaseModel):
    urn: str
    events: list[EventOut]
    count: int
    since: Optional[str] = None
    until: Optional[str] = None


# ── Helpers ───────────────────────────────────────────────────────────────────

def _event_to_out(event) -> EventOut:
    return EventOut(
        id=event.id,
        workspace_id=event.workspace_id,
        repo=event.repo,
        branch=event.branch,
        event_type=event.event_type,
        payload=event.payload,
        occurred_at=event.occurred_at.isoformat(),
        recorded_at=event.recorded_at.isoformat(),
        causal_parents=list(event.causal_parents),
        actors=list(event.actors),
        urn_affected=event.urn_affected,
    )


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get(
    "/causal-chain/{urn:path}",
    response_model=CausalChainResponse,
    summary="Get causal chain for an entity",
    description=(
        "Returns the ordered causal trail of BrainEvents that led to the "
        "current state of the entity identified by *urn*.  Events are "
        "ordered oldest → newest.  Requires EVENT_STORE_ENABLED=true."
    ),
)
async def get_causal_chain(urn: str) -> CausalChainResponse:
    if not settings.event_store_enabled:
        raise HTTPException(
            status_code=503,
            detail="Event store is disabled (EVENT_STORE_ENABLED=false).",
        )

    log.info("events.causal_chain", urn=urn)
    try:
        async with get_session() as session:
            from companybrain.events.views import CausalChainV2
            chain = CausalChainV2(session)
            events = await chain.walk(urn)
    except Exception as exc:
        log.error("events.causal_chain.error", urn=urn, error=str(exc))
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return CausalChainResponse(
        urn=urn,
        events=[_event_to_out(e) for e in events],
        count=len(events),
    )


@router.get(
    "/entity/{urn:path}",
    response_model=ReplayResponse,
    summary="Replay events for an entity",
    description=(
        "Returns raw BrainEvents touching *urn*, ordered oldest → newest. "
        "Optional *since* and *until* filters accept ISO-8601 timestamps."
    ),
)
async def replay_entity(
    urn: str,
    since: Optional[str] = Query(default=None, description="ISO-8601 start timestamp"),
    until: Optional[str] = Query(default=None, description="ISO-8601 end timestamp"),
) -> ReplayResponse:
    if not settings.event_store_enabled:
        raise HTTPException(
            status_code=503,
            detail="Event store is disabled (EVENT_STORE_ENABLED=false).",
        )

    since_dt: Optional[datetime] = None
    until_dt: Optional[datetime] = None
    if since:
        try:
            since_dt = datetime.fromisoformat(since)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=f"Invalid since: {since}") from exc
    if until:
        try:
            until_dt = datetime.fromisoformat(until)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=f"Invalid until: {until}") from exc

    log.info("events.replay", urn=urn, since=since, until=until)
    try:
        async with get_session() as session:
            from companybrain.events.store import EventStore
            store = EventStore(session)
            events = await store.replay(urn, since=since_dt, until=until_dt)
    except Exception as exc:
        log.error("events.replay.error", urn=urn, error=str(exc))
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return ReplayResponse(
        urn=urn,
        events=[_event_to_out(e) for e in events],
        count=len(events),
        since=since,
        until=until,
    )
