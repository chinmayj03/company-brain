"""
ADR-0090 M1 — EventStore: append-only event stream backed by Postgres.

Public API:
    EventStore.append(event)            — write one event; idempotent on PK.
    EventStore.replay(urn, since, until) — read events ordered by occurred_at.

Design notes:
  - Uses SQLAlchemy raw text() queries so no ORM model is needed.
  - Detects SQLite in the URL and omits RETURNING / JSONB syntax.
  - append() is always O(1); replay() is O(log N) via the urn+time index.
  - The caller is responsible for obtaining a session from companybrain.db.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from companybrain.events.models import BrainEvent

log = logging.getLogger(__name__)


class EventStore:
    """
    Thin append-only wrapper over the brain_events table.

    Usage::

        store = EventStore(session)
        await store.append(event)
        events = await store.replay(urn="urn:cb:…", since=yesterday)
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._is_sqlite: bool = "sqlite" in str(session.bind.url) if session.bind else False

    # ── Write path ────────────────────────────────────────────────────────────

    async def append(self, event: BrainEvent) -> None:
        """Insert one event.  Silently ignores duplicate IDs (ON CONFLICT DO NOTHING)."""
        payload_json = json.dumps(event.payload)
        causal_parents = list(event.causal_parents)
        actors = list(event.actors)

        if self._is_sqlite:
            await self._append_sqlite(event, payload_json, causal_parents, actors)
        else:
            await self._append_postgres(event, payload_json, causal_parents, actors)

    async def _append_postgres(
        self,
        event: BrainEvent,
        payload_json: str,
        causal_parents: list[str],
        actors: list[str],
    ) -> None:
        stmt = text("""
            INSERT INTO brain_events
                (id, workspace_id, repo, branch, event_type,
                 payload, occurred_at, recorded_at,
                 causal_parents, actors, urn_affected)
            VALUES
                (:id, :workspace_id, :repo, :branch, :event_type,
                 :payload::jsonb, :occurred_at, :recorded_at,
                 :causal_parents, :actors, :urn_affected)
            ON CONFLICT DO NOTHING
        """)
        await self._session.execute(stmt, {
            "id": event.id,
            "workspace_id": event.workspace_id,
            "repo": event.repo,
            "branch": event.branch,
            "event_type": event.event_type,
            "payload": payload_json,
            "occurred_at": _ensure_aware(event.occurred_at),
            "recorded_at": _ensure_aware(event.recorded_at),
            "causal_parents": causal_parents,
            "actors": actors,
            "urn_affected": event.urn_affected,
        })

    async def _append_sqlite(
        self,
        event: BrainEvent,
        payload_json: str,
        causal_parents: list[str],
        actors: list[str],
    ) -> None:
        # SQLite has no native ARRAY or JSONB; store as JSON text.
        stmt = text("""
            INSERT OR IGNORE INTO brain_events
                (id, workspace_id, repo, branch, event_type,
                 payload, occurred_at, recorded_at,
                 causal_parents, actors, urn_affected)
            VALUES
                (:id, :workspace_id, :repo, :branch, :event_type,
                 :payload, :occurred_at, :recorded_at,
                 :causal_parents, :actors, :urn_affected)
        """)
        await self._session.execute(stmt, {
            "id": event.id,
            "workspace_id": event.workspace_id,
            "repo": event.repo,
            "branch": event.branch,
            "event_type": event.event_type,
            "payload": payload_json,
            "occurred_at": _ensure_aware(event.occurred_at).isoformat(),
            "recorded_at": _ensure_aware(event.recorded_at).isoformat(),
            "causal_parents": json.dumps(causal_parents),
            "actors": json.dumps(actors),
            "urn_affected": event.urn_affected,
        })

    # ── Read path ─────────────────────────────────────────────────────────────

    async def replay(
        self,
        urn: str,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
    ) -> list[BrainEvent]:
        """
        Return all BrainEvents touching *urn*, ordered by occurred_at ascending.

        Filters:
            since — exclude events before this timestamp (inclusive)
            until — exclude events after this timestamp (inclusive)
        """
        conditions = ["urn_affected = :urn"]
        params: dict = {"urn": urn}

        if since is not None:
            conditions.append("occurred_at >= :since")
            params["since"] = _ensure_aware(since)
        if until is not None:
            conditions.append("occurred_at <= :until")
            params["until"] = _ensure_aware(until)

        where_clause = " AND ".join(conditions)
        stmt = text(f"""
            SELECT id, workspace_id, repo, branch, event_type,
                   payload, occurred_at, recorded_at,
                   causal_parents, actors, urn_affected
            FROM brain_events
            WHERE {where_clause}
            ORDER BY occurred_at ASC
        """)
        result = await self._session.execute(stmt, params)
        rows = result.fetchall()
        return [_row_to_event(row, is_sqlite=self._is_sqlite) for row in rows]

    async def replay_many(
        self,
        urns: list[str],
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
    ) -> list[BrainEvent]:
        """Return events touching any of the given URNs, ordered by occurred_at."""
        if not urns:
            return []

        conditions = ["urn_affected = ANY(:urns)"]
        params: dict = {"urns": urns}

        if since is not None:
            conditions.append("occurred_at >= :since")
            params["since"] = _ensure_aware(since)
        if until is not None:
            conditions.append("occurred_at <= :until")
            params["until"] = _ensure_aware(until)

        where_clause = " AND ".join(conditions)

        if self._is_sqlite:
            # SQLite doesn't have ANY(); use a parameterised IN clause instead.
            placeholders = ", ".join(f":urn{i}" for i in range(len(urns)))
            for i, u in enumerate(urns):
                params[f"urn{i}"] = u
            del params["urns"]
            where_clause = where_clause.replace(
                "urn_affected = ANY(:urns)",
                f"urn_affected IN ({placeholders})",
            )

        stmt = text(f"""
            SELECT id, workspace_id, repo, branch, event_type,
                   payload, occurred_at, recorded_at,
                   causal_parents, actors, urn_affected
            FROM brain_events
            WHERE {where_clause}
            ORDER BY occurred_at ASC
        """)
        result = await self._session.execute(stmt, params)
        rows = result.fetchall()
        return [_row_to_event(row, is_sqlite=self._is_sqlite) for row in rows]

    async def latest_event(self, urn: str) -> Optional[BrainEvent]:
        """Return the most recent event touching *urn*, or None."""
        stmt = text("""
            SELECT id, workspace_id, repo, branch, event_type,
                   payload, occurred_at, recorded_at,
                   causal_parents, actors, urn_affected
            FROM brain_events
            WHERE urn_affected = :urn
            ORDER BY occurred_at DESC
            LIMIT 1
        """)
        result = await self._session.execute(stmt, {"urn": urn})
        row = result.fetchone()
        if row is None:
            return None
        return _row_to_event(row, is_sqlite=self._is_sqlite)

    async def get_by_id(self, event_id: str) -> Optional[BrainEvent]:
        """Fetch a single event by its UUID."""
        stmt = text("""
            SELECT id, workspace_id, repo, branch, event_type,
                   payload, occurred_at, recorded_at,
                   causal_parents, actors, urn_affected
            FROM brain_events
            WHERE id = :id
        """)
        result = await self._session.execute(stmt, {"id": event_id})
        row = result.fetchone()
        if row is None:
            return None
        return _row_to_event(row, is_sqlite=self._is_sqlite)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _ensure_aware(dt: datetime) -> datetime:
    """Return a timezone-aware datetime; assume UTC if naive."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _row_to_event(row, *, is_sqlite: bool) -> BrainEvent:
    """Convert a DB row to a BrainEvent."""
    if is_sqlite:
        payload = json.loads(row.payload or "{}")
        causal_parents = tuple(json.loads(row.causal_parents or "[]"))
        actors = tuple(json.loads(row.actors or "[]"))
    else:
        payload = row.payload if isinstance(row.payload, dict) else json.loads(row.payload or "{}")
        causal_parents = tuple(row.causal_parents or [])
        actors = tuple(row.actors or [])

    def _parse_dt(val) -> datetime:
        if isinstance(val, datetime):
            return _ensure_aware(val)
        if isinstance(val, str):
            dt = datetime.fromisoformat(val)
            return _ensure_aware(dt)
        return datetime.now(timezone.utc)

    return BrainEvent(
        id=row.id,
        workspace_id=row.workspace_id or "",
        repo=row.repo or "",
        branch=row.branch or "",
        event_type=row.event_type,
        payload=payload,
        occurred_at=_parse_dt(row.occurred_at),
        recorded_at=_parse_dt(row.recorded_at),
        causal_parents=causal_parents,
        actors=actors,
        urn_affected=row.urn_affected,
    )
