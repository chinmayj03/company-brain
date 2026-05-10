"""TodoList — structured task tree streamed to the UI (ADR-0051 P4).

A pipeline run is a tree of tasks: the top-level extraction job, its
per-file sub-agents, the storage commit. Each task moves through
``pending → in_progress → completed | failed``. Every transition fires
listeners — the SSE endpoint subscribes and forwards the events to clients
so the UI can render real-time progress instead of polling
``/pipeline/jobs/{id}`` every two seconds.

Public surface
--------------

* :class:`TodoStatus` — closed enum of statuses (matches Claude Code's TodoWrite).
* :class:`TodoItem` — one node in the tree.
* :class:`TodoList` — the tree container, with subscribe/notify and
  serialisation for SSE.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

import structlog

log = structlog.get_logger(__name__)


class TodoStatus(str, Enum):
    PENDING     = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED   = "completed"
    FAILED      = "failed"


# Listener signature: (action, item_dict) -> None. We pass dicts (not
# TodoItem instances) so listeners can JSON-serialise without extra work.
Listener = Callable[[str, dict[str, Any]], None]


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass
class TodoItem:
    """One node in the todo tree.

    `metadata` is open-ended for callers — sub-agents stuff their
    ``cost_usd``, ``input_tokens``, ``output_tokens`` here so the UI can
    render per-task cost without extra wiring.
    """

    id: str
    title: str
    status: TodoStatus = TodoStatus.PENDING
    children: list[TodoItem] = field(default_factory=list)
    started_at:   str | None = None
    completed_at: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Render to a JSON-serialisable dict (children rendered recursively)."""
        return {
            "id":           self.id,
            "title":        self.title,
            "status":       self.status.value,
            "children":     [c.to_dict() for c in self.children],
            "started_at":   self.started_at,
            "completed_at": self.completed_at,
            "metadata":     dict(self.metadata),
        }


class TodoList:
    """Mutable tree of :class:`TodoItem` with pub/sub for streaming consumers.

    Thread-safety: this object is intended to be touched from a single asyncio
    event loop. The harness runs on one loop; sub-agents run on the same loop
    via ``asyncio.gather``. We don't lock — adding sync primitives would force
    every callback through them and slow the hot path.
    """

    def __init__(self) -> None:
        self.root: list[TodoItem] = []
        self._listeners: list[Listener] = []

    # ── mutations ──────────────────────────────────────────────────────────

    def add(self, item: TodoItem, parent_id: str | None = None) -> TodoItem:
        """Insert ``item`` under ``parent_id`` (or at root if None).

        Raises :class:`KeyError` if ``parent_id`` is not None and not found —
        callers usually pass a freshly minted parent and wouldn't typo it,
        but this surface is preferable to silently dropping the item.
        """
        if parent_id is None:
            self.root.append(item)
        else:
            parent = self._find(parent_id)
            if parent is None:
                raise KeyError(f"TodoList: parent {parent_id!r} not found")
            parent.children.append(item)
        self._notify("add", item)
        return item

    def update(
        self,
        id: str,
        *,
        status: TodoStatus | str | None = None,
        title: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> TodoItem:
        """Mutate one item; fire ``update`` to listeners.

        Status transitions auto-stamp ``started_at`` (on first move to
        ``in_progress``) and ``completed_at`` (on move to ``completed`` or
        ``failed``). Idempotent re-updates are tolerated (the timestamp is
        only set if currently None).
        """
        item = self._find(id)
        if item is None:
            raise KeyError(f"TodoList: id {id!r} not found")

        if status is not None:
            new_status = status if isinstance(status, TodoStatus) else TodoStatus(status)
            item.status = new_status
            if new_status == TodoStatus.IN_PROGRESS and item.started_at is None:
                item.started_at = _now()
            if new_status in (TodoStatus.COMPLETED, TodoStatus.FAILED):
                item.completed_at = _now()
        if title is not None:
            item.title = title
        if metadata:
            item.metadata.update(metadata)

        self._notify("update", item)
        return item

    # ── pub/sub ─────────────────────────────────────────────────────────────

    def subscribe(self, callback: Listener) -> Callable[[], None]:
        """Register a listener; return an unsubscriber."""
        self._listeners.append(callback)

        def _unsubscribe() -> None:
            try:
                self._listeners.remove(callback)
            except ValueError:
                pass

        return _unsubscribe

    def snapshot(self) -> list[dict[str, Any]]:
        """Full tree as a list of dicts. Used for SSE 'replay' on connect."""
        return [item.to_dict() for item in self.root]

    # ── internals ───────────────────────────────────────────────────────────

    def _notify(self, action: str, item: TodoItem) -> None:
        payload = item.to_dict()
        for cb in list(self._listeners):
            try:
                cb(action, payload)
            except Exception:  # noqa: BLE001 — one bad listener must not break the rest
                log.exception("todo.listener_error", action=action, item_id=item.id)

    def _find(self, id: str) -> TodoItem | None:
        """DFS over the tree; first match wins."""
        def walk(items: list[TodoItem]) -> TodoItem | None:
            for it in items:
                if it.id == id:
                    return it
                hit = walk(it.children)
                if hit is not None:
                    return hit
            return None
        return walk(self.root)


__all__ = ["TodoStatus", "TodoItem", "TodoList", "Listener"]
