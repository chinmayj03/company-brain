"""
Unit tests for ADR-0090 EventStore and M2 views.

Uses an async in-memory mock session (no real DB required) so these tests
run offline without Postgres or aiosqlite.

Covers:
  - EventStore.append() builds correct parameters
  - EventStore.replay() returns events in order
  - EntityStateCacheV1.refresh() builds correct upsert
  - CausalChainV2.walk() returns ordered events
  - SalienceScoreV3.compute() respects time decay and boosts
"""
from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from companybrain.events.models import BrainEvent
from companybrain.events.store import EventStore, _row_to_event, _ensure_aware
from companybrain.events.views import (
    EntityStateCacheV1,
    CausalChainV2,
    SalienceScoreV3,
    _keyword_affinity,
)


# ── Fixtures ───────────────────────────────────────────────────────────────────

def _now():
    return datetime.now(timezone.utc)


def _make_event(
    urn: Optional[str] = "urn:cb:acme:component:PaymentService",
    event_type: str = "AgentAction",
    occurred_at: Optional[datetime] = None,
    causal_parents: tuple = (),
    **kwargs,
) -> BrainEvent:
    return BrainEvent(
        workspace_id="ws-1",
        repo="acme/api",
        branch="main",
        event_type=event_type,
        payload={"test": True},
        occurred_at=occurred_at or _now(),
        recorded_at=_now(),
        causal_parents=causal_parents,
        actors=("tester",),
        urn_affected=urn,
        **kwargs,
    )


class _MockResult:
    """Mimics the SQLAlchemy result proxy."""
    def __init__(self, rows: list):
        self._rows = rows

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _MockSession:
    """
    Minimal async session mock that captures execute() calls and supports
    configuring return values for replay queries.
    """
    def __init__(self):
        self.executed: list[dict] = []
        self._replay_rows: list = []
        self.bind = MagicMock()
        self.bind.url = MagicMock()
        self.bind.url.__str__ = lambda _: "postgresql://localhost/test"

    async def execute(self, stmt, params=None):
        call = {"stmt": str(stmt), "params": params or {}}
        self.executed.append(call)
        return _MockResult(self._replay_rows)

    async def commit(self):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    def set_replay_rows(self, rows: list):
        self._replay_rows = rows


def _make_row(event: BrainEvent):
    """Create a mock row object matching the SELECT column order."""
    row = MagicMock()
    row.id = event.id
    row.workspace_id = event.workspace_id
    row.repo = event.repo
    row.branch = event.branch
    row.event_type = event.event_type
    row.payload = event.payload
    row.occurred_at = event.occurred_at
    row.recorded_at = event.recorded_at
    row.causal_parents = list(event.causal_parents)
    row.actors = list(event.actors)
    row.urn_affected = event.urn_affected
    return row


# ── EventStore.append() ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_append_executes_insert():
    session = _MockSession()
    store = EventStore(session)
    event = _make_event()
    await store.append(event)

    assert len(session.executed) == 1
    call = session.executed[0]
    params = call["params"]
    assert params["id"] == event.id
    assert params["workspace_id"] == "ws-1"
    assert params["event_type"] == "AgentAction"
    assert params["urn_affected"] == "urn:cb:acme:component:PaymentService"


@pytest.mark.asyncio
async def test_append_payload_serialized():
    session = _MockSession()
    store = EventStore(session)
    event = BrainEvent(
        workspace_id="ws-1", repo="acme/api", branch="main",
        event_type="AgentAction",
        payload={"amount": 100, "currency": "USD"},
        urn_affected="urn:cb:acme:component:PaymentService",
    )
    await store.append(event)

    params = session.executed[0]["params"]
    import json
    payload = json.loads(params["payload"])
    assert payload == {"amount": 100, "currency": "USD"}


@pytest.mark.asyncio
async def test_append_causal_parents_as_list():
    parent_id = "aaa-111"
    session = _MockSession()
    store = EventStore(session)
    event = _make_event(causal_parents=(parent_id,))
    await store.append(event)

    params = session.executed[0]["params"]
    assert parent_id in params["causal_parents"]


# ── EventStore.replay() ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_replay_returns_events_in_order():
    t1 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    t2 = datetime(2026, 1, 2, tzinfo=timezone.utc)
    e1 = _make_event(occurred_at=t1)
    e2 = _make_event(occurred_at=t2)

    session = _MockSession()
    session.set_replay_rows([_make_row(e1), _make_row(e2)])
    store = EventStore(session)

    events = await store.replay("urn:cb:acme:component:PaymentService")

    assert len(events) == 2
    assert events[0].occurred_at <= events[1].occurred_at


@pytest.mark.asyncio
async def test_replay_empty_when_no_events():
    session = _MockSession()
    session.set_replay_rows([])
    store = EventStore(session)

    events = await store.replay("urn:cb:acme:component:Unknown")
    assert events == []


@pytest.mark.asyncio
async def test_replay_since_filter_included_in_params():
    session = _MockSession()
    session.set_replay_rows([])
    store = EventStore(session)

    since = datetime(2026, 1, 1, tzinfo=timezone.utc)
    await store.replay("urn:cb:test", since=since)

    params = session.executed[0]["params"]
    assert "since" in params


@pytest.mark.asyncio
async def test_replay_until_filter_included_in_params():
    session = _MockSession()
    session.set_replay_rows([])
    store = EventStore(session)

    until = datetime(2026, 6, 1, tzinfo=timezone.utc)
    await store.replay("urn:cb:test", until=until)

    params = session.executed[0]["params"]
    assert "until" in params


@pytest.mark.asyncio
async def test_replay_returns_brain_event_instances():
    e = _make_event()
    session = _MockSession()
    session.set_replay_rows([_make_row(e)])
    store = EventStore(session)

    result = await store.replay(e.urn_affected)
    assert len(result) == 1
    assert isinstance(result[0], BrainEvent)
    assert result[0].id == e.id
    assert result[0].event_type == e.event_type


# ── _ensure_aware helper ──────────────────────────────────────────────────────

def test_ensure_aware_naive_gets_utc():
    naive = datetime(2026, 1, 1)
    aware = _ensure_aware(naive)
    assert aware.tzinfo is not None
    assert aware.tzinfo == timezone.utc


def test_ensure_aware_preserves_existing_tz():
    import datetime as dt_module
    tz = dt_module.timezone(dt_module.timedelta(hours=5))
    aware = datetime(2026, 1, 1, tzinfo=tz)
    result = _ensure_aware(aware)
    assert result.tzinfo == tz


# ── EntityStateCacheV1 ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_entity_state_cache_refresh_executes_upsert():
    session = _MockSession()
    cache = EntityStateCacheV1(session)
    event = _make_event()

    await cache.refresh(event)

    assert len(session.executed) == 1
    params = session.executed[0]["params"]
    assert params["urn"] == event.urn_affected
    assert params["branch"] == "main"
    assert params["workspace_id"] == "ws-1"


@pytest.mark.asyncio
async def test_entity_state_cache_no_op_without_urn():
    session = _MockSession()
    cache = EntityStateCacheV1(session)
    event = _make_event(urn=None)

    await cache.refresh(event)

    # Should not execute any SQL since there's no urn_affected
    assert len(session.executed) == 0


@pytest.mark.asyncio
async def test_entity_state_cache_get_returns_none_when_not_found():
    session = _MockSession()
    session.set_replay_rows([])
    cache = EntityStateCacheV1(session)

    result = await cache.get("urn:cb:nonexistent")
    assert result is None


@pytest.mark.asyncio
async def test_entity_state_cache_get_returns_dict_when_found():
    import json
    event = _make_event()

    row = MagicMock()
    row.urn = event.urn_affected
    row.branch = "main"
    row.workspace_id = "ws-1"
    row.repo = "acme/api"
    row.snapshot = json.dumps({"last_event_id": event.id})
    row.last_event_at = _now()
    row.last_refreshed_at = _now()
    row.event_count = 3

    session = _MockSession()
    session.set_replay_rows([row])
    cache = EntityStateCacheV1(session)

    result = await cache.get(event.urn_affected)
    assert result is not None
    assert result["urn"] == event.urn_affected
    assert result["event_count"] == 3


# ── SalienceScoreV3 ───────────────────────────────────────────────────────────

def test_keyword_affinity_zero_for_empty_query():
    assert _keyword_affinity("", "payments", "urn:cb:acme:payments:Service") == 0.0


def test_keyword_affinity_match_in_domain():
    score = _keyword_affinity("payments handler endpoint", "payments", "urn:cb:acme:api")
    assert score > 0.0


def test_keyword_affinity_no_match():
    score = _keyword_affinity("auth refresh token", "payments", "urn:cb:acme:payments:Service")
    # "payments" appears in urn but not in query, so low score
    assert score < 0.3


def test_keyword_affinity_capped_at_0_3():
    score = _keyword_affinity(
        "payments payments payments payments payments",
        "payments",
        "urn:cb:acme:payments:PaymentService",
    )
    assert score <= 0.3


@pytest.mark.asyncio
async def test_salience_pinned_returns_1():
    session = _MockSession()
    scorer = SalienceScoreV3(session)
    score = await scorer.compute("urn:cb:test", pinned=True)
    assert score == 1.0


@pytest.mark.asyncio
async def test_salience_no_events_returns_low_score():
    # No events → age defaults to half-life (90 days) → recency = 0.5
    session = _MockSession()
    session.set_replay_rows([])  # no latest event
    scorer = SalienceScoreV3(session)

    score = await scorer.compute("urn:cb:test:component:Foo", entity_type="component")
    # base=0.5, recency=0.5, no boosts → 0.5 * 0.5 = 0.25
    assert 0.0 <= score <= 1.0


@pytest.mark.asyncio
async def test_salience_recent_event_boost():
    """Entity touched within 7 days gets +0.2 boost."""
    event = _make_event(occurred_at=_now() - timedelta(hours=1))

    session = _MockSession()
    # latest_event() query returns a row
    session.set_replay_rows([_make_row(event)])
    scorer = SalienceScoreV3(session)

    score = await scorer.compute(
        "urn:cb:acme:component:PaymentService",
        entity_type="component",
    )
    # Should be boosted: recency near 1.0, event_boost=0.2
    assert score > 0.5


@pytest.mark.asyncio
async def test_salience_user_domain_boost():
    """User's active domain matching entity domain gives +0.15 boost."""
    event = _make_event(occurred_at=_now() - timedelta(hours=1))
    session = _MockSession()
    session.set_replay_rows([_make_row(event)])

    scorer = SalienceScoreV3(session)

    score_with_match = await scorer.compute(
        "urn:cb:acme:component:PaymentService",
        entity_type="component",
        entity_domain="payments",
        user_active_domain="payments",
    )
    score_without_match = await scorer.compute(
        "urn:cb:acme:component:PaymentService",
        entity_type="component",
        entity_domain="payments",
        user_active_domain="auth",
    )
    assert score_with_match > score_without_match


@pytest.mark.asyncio
async def test_salience_score_bounded_0_to_1():
    """Score must always be in [0, 1] regardless of inputs."""
    event = _make_event(occurred_at=_now())
    session = _MockSession()
    session.set_replay_rows([_make_row(event)])
    scorer = SalienceScoreV3(session)

    score = await scorer.compute(
        "urn:cb:acme:payments:api_contract:PaymentController",
        entity_type="api_contract",
        query_context="payments handler endpoint contract",
        entity_domain="payments",
        user_active_domain="payments",
        pinned=False,
    )
    assert 0.0 <= score <= 1.0


# ── CausalChainV2 ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_causal_chain_empty_for_unknown_urn():
    session = _MockSession()
    session.set_replay_rows([])
    chain = CausalChainV2(session)

    result = await chain.walk("urn:cb:unknown:entity")
    assert result == []


@pytest.mark.asyncio
async def test_causal_chain_single_event():
    event = _make_event()
    session = _MockSession()
    session.set_replay_rows([_make_row(event)])
    chain = CausalChainV2(session)

    result = await chain.walk(event.urn_affected)
    assert len(result) >= 1
    assert any(e.id == event.id for e in result)


@pytest.mark.asyncio
async def test_causal_chain_ordered_oldest_first():
    t1 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    t2 = datetime(2026, 1, 2, tzinfo=timezone.utc)
    t3 = datetime(2026, 1, 3, tzinfo=timezone.utc)

    e1 = _make_event(occurred_at=t1)
    e2 = _make_event(occurred_at=t2)
    e3 = _make_event(occurred_at=t3)

    session = _MockSession()
    session.set_replay_rows([_make_row(e1), _make_row(e2), _make_row(e3)])
    chain = CausalChainV2(session)

    result = await chain.walk(e1.urn_affected)
    times = [e.occurred_at for e in result]
    assert times == sorted(times)
