"""
ADR-0082 P1 — Drift REST API routes.

Endpoints:
    GET  /drift/snapshot/latest         → most recent DriftSnapshot
    GET  /drift/snapshot/{id}           → specific snapshot
    GET  /drift/items                   → list with filters (state, severity, domain)
    GET  /drift/items/{id}              → item detail
    POST /drift/items/{id}/ack          → acknowledge (open → acknowledged)
    POST /drift/items/{id}/waive        → waive with justification
    GET  /drift/scores/by-domain        → per-domain weighted scores
    POST /drift/snapshot/run            → on-demand snapshot trigger
"""
from __future__ import annotations

import asyncio
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from companybrain.drift.models import DriftItem
from companybrain.drift.scorer import all_domain_scores
from companybrain.drift.state_machine import DriftStateMachine, InvalidTransition
from companybrain.drift.store import DriftStore

router = APIRouter()

# ── Pydantic request/response models ─────────────────────────────────────────


class WaiveRequest(BaseModel):
    justification: str
    duration_days: int = 90


class AckRequest(BaseModel):
    actor: Optional[str] = None


class SnapshotRunResponse(BaseModel):
    snapshot_id: str
    snapshot_at: str
    items_open: int
    items_acknowledged: int
    items_in_flight: int
    new_items: int
    resolved_items: int
    elapsed_seconds: float


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_store() -> DriftStore:
    return DriftStore()


def _item_or_404(store: DriftStore, item_id: str) -> DriftItem:
    item = store.load_item(item_id)
    if item is None:
        raise HTTPException(status_code=404, detail=f"DriftItem {item_id!r} not found")
    return item


# ── Snapshot endpoints ────────────────────────────────────────────────────────


@router.get("/snapshot/latest")
async def get_latest_snapshot() -> dict[str, Any]:
    """Return the most recent DriftSnapshot."""
    store = _get_store()
    snapshot = await asyncio.get_event_loop().run_in_executor(
        None, store.load_latest_snapshot
    )
    if snapshot is None:
        raise HTTPException(
            status_code=404,
            detail="No snapshots found. Run 'POST /drift/snapshot/run' to create the first one.",
        )
    return snapshot.to_dict()


@router.get("/snapshot/{snapshot_id}")
async def get_snapshot(snapshot_id: str) -> dict[str, Any]:
    """Return a specific DriftSnapshot by ID."""
    store = _get_store()
    snapshot = await asyncio.get_event_loop().run_in_executor(
        None, store.load_snapshot, snapshot_id
    )
    if snapshot is None:
        raise HTTPException(status_code=404, detail=f"Snapshot {snapshot_id!r} not found")
    return snapshot.to_dict()


@router.post("/snapshot/run", response_model=SnapshotRunResponse)
async def run_snapshot_on_demand() -> dict[str, Any]:
    """
    Trigger an on-demand snapshot. Runs the full drift check pipeline
    synchronously and returns a summary. Completes in < 30s for typical repos.
    """
    from companybrain.drift.scheduler import run_snapshot_now

    result = await asyncio.get_event_loop().run_in_executor(None, run_snapshot_now)
    return result.to_dict()


# ── Item endpoints ────────────────────────────────────────────────────────────


@router.get("/items")
async def list_drift_items(
    state: Optional[str] = Query(default=None, description="Filter by state: open|acknowledged|in_flight|resolved|waived"),
    severity: Optional[str] = Query(default=None, description="Filter by severity: low|medium|high|critical"),
    domain: Optional[str] = Query(default=None, description="Filter by domain area (partial match not supported)"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    """List DriftItems with optional filters."""
    store = _get_store()

    def _query():
        return store.query_items(state=state, severity=severity, domain=domain)

    items = await asyncio.get_event_loop().run_in_executor(None, _query)
    total = len(items)
    start = (page - 1) * page_size
    page_items = items[start : start + page_size]

    return {
        "items": [i.to_dict() for i in page_items],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/items/{item_id}")
async def get_drift_item(item_id: str) -> dict[str, Any]:
    """Return a DriftItem by its stable ID."""
    store = _get_store()

    def _load():
        return _item_or_404(store, item_id)

    item = await asyncio.get_event_loop().run_in_executor(None, _load)
    return item.to_dict()


@router.post("/items/{item_id}/ack")
async def acknowledge_item(item_id: str, body: AckRequest = AckRequest()) -> dict[str, Any]:
    """
    Acknowledge a drift item (open → acknowledged).
    Records that a human is aware of the violation.
    """
    store = _get_store()

    def _ack():
        item = _item_or_404(store, item_id)
        sm = DriftStateMachine(item)
        try:
            sm.acknowledge(actor=body.actor)
        except InvalidTransition as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        store.upsert_item(item)
        return item

    item = await asyncio.get_event_loop().run_in_executor(None, _ack)
    return {"ok": True, "item": item.to_dict()}


@router.post("/items/{item_id}/waive")
async def waive_item(item_id: str, body: WaiveRequest) -> dict[str, Any]:
    """
    Waive a drift item with a required justification.
    The item will auto-reactivate after ``duration_days`` days (default 90).
    """
    if not body.justification or not body.justification.strip():
        raise HTTPException(status_code=422, detail="justification is required to waive an item")

    store = _get_store()

    def _waive():
        item = _item_or_404(store, item_id)
        sm = DriftStateMachine(item)
        try:
            sm.waive(
                justification=body.justification,
                duration_days=body.duration_days,
            )
        except InvalidTransition as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        store.upsert_item(item)
        return item

    item = await asyncio.get_event_loop().run_in_executor(None, _waive)
    return {"ok": True, "item": item.to_dict()}


# ── Scoring endpoints ─────────────────────────────────────────────────────────


@router.get("/scores/by-domain")
async def scores_by_domain() -> dict[str, Any]:
    """
    Return per-domain weighted drift scores, sorted by weighted_score descending.
    Waived items are excluded from scoring.
    """
    store = _get_store()

    def _score():
        items = store.load_all_items()
        return all_domain_scores(items, exclude_waived=True)

    scores = await asyncio.get_event_loop().run_in_executor(None, _score)
    return {
        "scores": [s.to_dict() for s in scores],
        "total_domains": len(scores),
    }
