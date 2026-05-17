"""
ADR-0090 M1 — Fire-and-forget event emission helpers.

emit() wraps asyncio.create_task() so callers on the hot mutation path
never block.  Failed emissions are logged as warnings; they do NOT raise.

Usage in mutation paths::

    from companybrain.events.emitter import emit_entity_written

    # In FanoutBrainStore.write():
    emit_entity_written(entity, run_id=run_id, workspace_id=workspace_id)

    # In relationship creation:
    emit_edge_created(from_urn, to_urn, edge_type, run_id, workspace_id)
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

from companybrain.events.models import BrainEvent

log = logging.getLogger(__name__)


def emit(event: BrainEvent) -> None:
    """
    Schedule event persistence as a background asyncio task.

    Safe to call from sync or async contexts — wraps the coroutine in
    create_task() so it runs in the current event loop without blocking.
    Falls back to a synchronous direct-write if no event loop is running
    (e.g. in test teardown or CLI contexts).

    The task is fire-and-forget: if it fails, a warning is logged but the
    caller is not affected.
    """
    from companybrain.config import settings
    if not settings.event_store_enabled:
        return

    coro = _persist_event(event)

    try:
        loop = asyncio.get_running_loop()
        task = loop.create_task(coro)
        task.add_done_callback(_log_task_failure)
    except RuntimeError:
        # No event loop running — best-effort sync write via asyncio.run().
        try:
            asyncio.run(coro)
        except Exception as exc:
            log.warning("emit fallback failed", event_id=event.id, error=str(exc))


def emit_entity_written(
    entity_id: str,
    entity_type: str,
    repo: str,
    workspace_id: str,
    run_id: str,
    branch: str = "main",
    actors: tuple[str, ...] = ("harness/extractor",),
    causal_parents: tuple[str, ...] = (),
) -> None:
    """
    Emit a HumanFactWritten event when a brain entity is created or updated.

    Called from FanoutBrainStore.write() as a fire-and-forget side-effect.
    """
    event = BrainEvent(
        workspace_id=workspace_id,
        repo=repo,
        branch=branch,
        event_type="HumanFactWritten",
        payload={
            "run_id": run_id,
            "entity_type": entity_type,
            "action": "upsert",
        },
        occurred_at=datetime.now(timezone.utc),
        recorded_at=datetime.now(timezone.utc),
        causal_parents=causal_parents,
        actors=actors,
        urn_affected=entity_id,
    )
    emit(event)


def emit_edge_created(
    from_urn: str,
    to_urn: str,
    edge_type: str,
    repo: str,
    workspace_id: str,
    run_id: str,
    branch: str = "main",
    actors: tuple[str, ...] = ("harness/extractor",),
    causal_parents: tuple[str, ...] = (),
) -> None:
    """
    Emit an AgentAction event when a brain relationship edge is created.

    Called from relationship write paths.  Uses AgentAction as event_type
    because edge creation is an agent-driven structural action.
    """
    event = BrainEvent(
        workspace_id=workspace_id,
        repo=repo,
        branch=branch,
        event_type="AgentAction",
        payload={
            "run_id": run_id,
            "action": "edge_created",
            "from_urn": from_urn,
            "to_urn": to_urn,
            "edge_type": edge_type,
        },
        occurred_at=datetime.now(timezone.utc),
        recorded_at=datetime.now(timezone.utc),
        causal_parents=causal_parents,
        actors=actors,
        urn_affected=from_urn,
    )
    emit(event)


def emit_query_asked(
    question: str,
    workspace_id: str,
    repo: str = "",
    branch: str = "",
    actors: tuple[str, ...] = ("user",),
) -> None:
    """Emit a QueryAsked event for observability and salience scoring."""
    event = BrainEvent(
        workspace_id=workspace_id,
        repo=repo,
        branch=branch,
        event_type="QueryAsked",
        payload={"question": question[:512]},  # truncate very long queries
        occurred_at=datetime.now(timezone.utc),
        recorded_at=datetime.now(timezone.utc),
        actors=actors,
        urn_affected=None,
    )
    emit(event)


def emit_run_completed(
    run_id: str,
    workspace_id: str,
    repo: str,
    entity_count: int,
    branch: str = "main",
) -> None:
    """Emit an AgentAction event when an extraction run completes."""
    event = BrainEvent(
        workspace_id=workspace_id,
        repo=repo,
        branch=branch,
        event_type="AgentAction",
        payload={
            "run_id": run_id,
            "action": "run_completed",
            "entity_count": entity_count,
        },
        occurred_at=datetime.now(timezone.utc),
        recorded_at=datetime.now(timezone.utc),
        actors=("harness/pipeline",),
        urn_affected=None,
    )
    emit(event)


# ── Internal helpers ──────────────────────────────────────────────────────────

async def _persist_event(event: BrainEvent) -> None:
    """Async coroutine: open a DB session, append the event, commit."""
    try:
        from companybrain.db import get_session
        from companybrain.events.store import EventStore
        from companybrain.events.views import EntityStateCacheV1

        async with get_session() as session:
            store = EventStore(session)
            await store.append(event)
            # Refresh V1 cache if there is an entity linked.
            if event.urn_affected:
                cache = EntityStateCacheV1(session)
                await cache.refresh(event)
            await session.commit()
    except Exception as exc:
        log.warning(
            "event emission failed",
            event_id=event.id,
            event_type=event.event_type,
            urn=event.urn_affected,
            error=str(exc),
        )


def _log_task_failure(task: "asyncio.Task[None]") -> None:
    """Callback attached to every emit task to surface failures as warnings."""
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        log.warning("emit task raised", error=str(exc), exc_info=exc)
