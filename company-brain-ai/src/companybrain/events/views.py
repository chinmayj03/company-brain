"""
ADR-0090 M2 — Materialized views derived from the event stream.

Views are computed from BrainEvents; they do NOT replace the existing entity
graph — they augment it with temporal, causal, and salience information.

V1 — EntityStateCacheV1
    Maintains entity_state_current table: one row per (urn, branch).
    Refreshed on every new BrainEvent that touches the entity.

V2 — CausalChainV2
    Walks causal_parents backward from the latest event for a URN to produce
    an ordered causal trail.

V3 — SalienceScoreV3
    Computes per-query, per-time salience score for an entity.
    No materialization — computed at query time.
"""
from __future__ import annotations

import json
import logging
import math
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from companybrain.events.models import BrainEvent

log = logging.getLogger(__name__)


# ── V1 — EntityState cache ────────────────────────────────────────────────────

class EntityStateCacheV1:
    """
    Maintains the entity_state_current table.

    Each row stores the most recent event snapshot for a (urn, branch) pair.
    Use refresh() on every new event; use get() at query time.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._is_sqlite = "sqlite" in str(session.bind.url) if session.bind else False

    async def refresh(self, event: BrainEvent) -> None:
        """Upsert the cache row for the entity touched by this event."""
        if not event.urn_affected:
            return

        snapshot_json = json.dumps({
            "last_event_id": event.id,
            "last_event_type": event.event_type,
            "payload": event.payload,
            "actors": list(event.actors),
        })
        now = datetime.now(timezone.utc)

        if self._is_sqlite:
            stmt = text("""
                INSERT INTO entity_state_current
                    (urn, branch, workspace_id, repo, snapshot,
                     last_event_at, last_refreshed_at, event_count)
                VALUES
                    (:urn, :branch, :workspace_id, :repo, :snapshot,
                     :last_event_at, :last_refreshed_at, 1)
                ON CONFLICT (urn, branch) DO UPDATE SET
                    snapshot         = :snapshot,
                    last_event_at    = :last_event_at,
                    last_refreshed_at = :last_refreshed_at,
                    event_count      = entity_state_current.event_count + 1
            """)
        else:
            stmt = text("""
                INSERT INTO entity_state_current
                    (urn, branch, workspace_id, repo, snapshot,
                     last_event_at, last_refreshed_at, event_count)
                VALUES
                    (:urn, :branch, :workspace_id, :repo, :snapshot::jsonb,
                     :last_event_at, :last_refreshed_at, 1)
                ON CONFLICT (urn, branch) DO UPDATE SET
                    snapshot          = EXCLUDED.snapshot,
                    last_event_at     = EXCLUDED.last_event_at,
                    last_refreshed_at = EXCLUDED.last_refreshed_at,
                    event_count       = entity_state_current.event_count + 1
            """)

        await self._session.execute(stmt, {
            "urn": event.urn_affected,
            "branch": event.branch or "main",
            "workspace_id": event.workspace_id,
            "repo": event.repo,
            "snapshot": snapshot_json,
            "last_event_at": event.occurred_at.isoformat() if self._is_sqlite else event.occurred_at,
            "last_refreshed_at": now.isoformat() if self._is_sqlite else now,
        })

    async def get(
        self,
        urn: str,
        branch: str = "main",
    ) -> Optional[dict]:
        """Return the cached entity state dict or None if not found."""
        stmt = text("""
            SELECT urn, branch, workspace_id, repo, snapshot,
                   last_event_at, last_refreshed_at, event_count
            FROM entity_state_current
            WHERE urn = :urn AND branch = :branch
        """)
        result = await self._session.execute(stmt, {"urn": urn, "branch": branch})
        row = result.fetchone()
        if row is None:
            return None

        snapshot = row.snapshot
        if isinstance(snapshot, str):
            snapshot = json.loads(snapshot)

        return {
            "urn": row.urn,
            "branch": row.branch,
            "workspace_id": row.workspace_id,
            "repo": row.repo,
            "snapshot": snapshot,
            "last_event_at": row.last_event_at,
            "last_refreshed_at": row.last_refreshed_at,
            "event_count": row.event_count,
        }

    async def is_stale(
        self,
        urn: str,
        branch: str = "main",
        freshness_seconds: int = 60,
    ) -> bool:
        """Return True if the cache entry is older than freshness_seconds."""
        state = await self.get(urn, branch)
        if state is None:
            return True
        last_refreshed = state["last_refreshed_at"]
        if isinstance(last_refreshed, str):
            last_refreshed = datetime.fromisoformat(last_refreshed)
        if last_refreshed.tzinfo is None:
            last_refreshed = last_refreshed.replace(tzinfo=timezone.utc)
        age = datetime.now(timezone.utc) - last_refreshed
        return age.total_seconds() > freshness_seconds


# ── V2 — CausalChain ─────────────────────────────────────────────────────────

class CausalChainV2:
    """
    Walk causal_parents backward from the latest event for a URN.

    Returns an ordered list of BrainEvents forming the causal trail,
    oldest-first.  Performs a BFS/DFS over causal_parents stored in each
    event row; depth is capped at MAX_WALK_DEPTH to prevent runaway queries.
    """

    MAX_WALK_DEPTH = 20

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        from companybrain.events.store import EventStore
        self._store = EventStore(session)

    async def walk(self, urn: str) -> list[BrainEvent]:
        """
        Return the causal trail for *urn*, ordered oldest → newest.

        Algorithm:
          1. Get the latest event for the URN.
          2. Walk causal_parents (BFS) up to MAX_WALK_DEPTH, fetching each
             parent event by ID.
          3. Return the chain sorted by occurred_at ascending.
        """
        latest = await self._store.latest_event(urn)
        if latest is None:
            return []

        # BFS over causal parents
        visited: dict[str, BrainEvent] = {}
        queue: list[str] = [latest.id]
        # Also include the latest event itself
        visited[latest.id] = latest

        depth = 0
        while queue and depth < self.MAX_WALK_DEPTH:
            current_id = queue.pop(0)
            current = visited.get(current_id) or await self._store.get_by_id(current_id)
            if current is None:
                continue
            visited[current.id] = current

            for parent_id in current.causal_parents:
                if parent_id not in visited:
                    parent = await self._store.get_by_id(parent_id)
                    if parent is not None:
                        visited[parent_id] = parent
                        queue.append(parent_id)
            depth += 1

        # Also include direct events for this URN (they may not all be in
        # the causal parent chain but are part of the entity's history).
        direct_events = await self._store.replay(urn)
        for ev in direct_events:
            visited.setdefault(ev.id, ev)

        # Sort by occurred_at ascending
        chain = sorted(visited.values(), key=lambda e: e.occurred_at)
        return chain


# ── V3 — SalienceScore ────────────────────────────────────────────────────────

# Per-entity-type base salience baselines (from ADR-0073 M7).
_TYPE_BASELINES: dict[str, float] = {
    "api_contract":      0.7,
    "data_model":        0.6,
    "component":         0.5,
    "business_context":  0.6,
    "function_node":     0.4,
    "assumption":        0.3,
    "screen":            0.5,
}
_DEFAULT_BASELINE = 0.3


class SalienceScoreV3:
    """
    Per-query, per-time salience scoring for brain entities.

    Computed at query time; no materialization.  Combines:
      - Time-decay (exponential with 90-day half-life)
      - Recent-event boost (+0.2 if entity touched within 7 days)
      - Query-context affinity (0–0.3 cosine-like overlap)
      - User-domain boost (+0.15 if user's active domain matches entity domain)

    Usage::

        scorer = SalienceScoreV3(session)
        score = await scorer.compute(
            urn="urn:cb:...",
            entity_type="api_contract",
            query_context="payments handler",
            entity_domain="payments",
        )
    """

    RECENT_DAYS = 7
    RECENT_BOOST = 0.2
    USER_DOMAIN_BOOST = 0.15
    DECAY_HALFLIFE_DAYS = 90

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        from companybrain.events.store import EventStore
        self._store = EventStore(session)

    async def compute(
        self,
        urn: str,
        entity_type: str = "component",
        query_context: str = "",
        entity_domain: str = "",
        user_active_domain: str = "",
        pinned: bool = False,
        current_time: Optional[datetime] = None,
    ) -> float:
        """
        Return a salience score in [0, 1].

        If *pinned* is True, returns 1.0 immediately (pinned memories never
        decay, per ADR-0073 M7).
        """
        if pinned:
            return 1.0

        now = current_time or datetime.now(timezone.utc)

        # ── Base salience by entity type ──────────────────────────────────────
        base = _TYPE_BASELINES.get(entity_type, _DEFAULT_BASELINE)

        # ── Time-decay component ──────────────────────────────────────────────
        last_event_at = await self._last_event_time(urn)
        if last_event_at is not None:
            age_days = max(0.0, (now - last_event_at).total_seconds() / 86400)
        else:
            age_days = self.DECAY_HALFLIFE_DAYS  # treat unknown as half-life old
        # Exponential decay: exp(-lambda * t), lambda = ln2 / halflife
        lam = math.log(2) / self.DECAY_HALFLIFE_DAYS
        recency = math.exp(-lam * age_days)

        # ── Recent-event boost ────────────────────────────────────────────────
        has_recent = (
            last_event_at is not None
            and (now - last_event_at) <= timedelta(days=self.RECENT_DAYS)
        )
        event_boost = self.RECENT_BOOST if has_recent else 0.0

        # ── Query-context affinity ────────────────────────────────────────────
        affinity = _keyword_affinity(query_context, entity_domain, urn)

        # ── User-domain boost ─────────────────────────────────────────────────
        domain_boost = (
            self.USER_DOMAIN_BOOST
            if (user_active_domain and entity_domain and
                user_active_domain.lower() == entity_domain.lower())
            else 0.0
        )

        # ── Combine ───────────────────────────────────────────────────────────
        score = base * recency + 0.3 * affinity + event_boost + domain_boost
        return min(1.0, max(0.0, score))

    async def _last_event_time(self, urn: str) -> Optional[datetime]:
        """Return the occurred_at of the most recent event for this URN."""
        latest = await self._store.latest_event(urn)
        if latest is None:
            return None
        t = latest.occurred_at
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        return t


def _keyword_affinity(query_context: str, entity_domain: str, urn: str) -> float:
    """
    Lightweight keyword-overlap proxy for cosine affinity.

    Returns a float in [0, 0.3] based on how many domain/urn keywords
    appear in the query context.  This is an intentionally cheap heuristic
    that can be replaced with embedding cosine when voyager is available.
    """
    if not query_context:
        return 0.0

    query_lower = query_context.lower()

    # Collect keywords from entity domain and URN segments
    keywords: set[str] = set()
    if entity_domain:
        keywords.update(entity_domain.lower().split("/"))
    # URN segments like urn:cb:repo:entity_type:qualified.Name
    for seg in urn.lower().replace("urn:cb:", "").split(":"):
        for part in seg.replace(".", "/").split("/"):
            if len(part) >= 4:
                keywords.add(part)

    if not keywords:
        return 0.0

    hits = sum(1 for kw in keywords if kw in query_lower)
    return min(0.3, hits * 0.1)
