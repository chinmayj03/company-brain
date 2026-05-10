"""Session management — list / resume / transcript (ADR-0051 P4).

A *session* is one HarnessLoop run, identified by a session_id (= job_id when
the run came from a pipeline job, or a UUID when triggered ad-hoc from the
CLI). Sessions hold:

  * a :class:`TodoList` for streaming progress to clients,
  * the message transcript for post-mortem inspection,
  * a :class:`CostTracker` for the run-level cost summary,
  * lifecycle metadata (status, timestamps, endpoint info).

Persistence is in-memory by default. Sessions can be saved to and loaded
from a JSON file (one session per file) so the CLI commands ``brain session
resume <id>`` and ``brain session transcript <id>`` work across processes.

Public surface
--------------

* :class:`Session` — the dataclass.
* :func:`create`, :func:`get_session`, :func:`list_sessions` — registry.
* :func:`save`, :func:`load` — JSON round-trip.
* :func:`session_dir` — default on-disk location, ``$BRAIN_HOME/sessions``.
"""
from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog

from companybrain.harness.cost import CostTracker
from companybrain.harness.progress import TodoList

log = structlog.get_logger(__name__)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass
class Session:
    """One harness run.

    The dataclass intentionally captures simple Python types in `transcript`
    and `metadata` so save/load is a straightforward ``json.dumps`` /
    ``json.loads``. Live runtime objects (``todo``, ``cost``) are recreated
    from the JSON snapshot on load — anyone resuming gets the *state*, not
    the listeners or threading primitives.
    """

    id: str
    created_at: str = field(default_factory=_now)
    repo_path:  str = ""
    endpoint:   str = ""
    method:     str = ""
    workspace_id: str = ""
    status: str = "active"   # active | completed | failed | timeout
    todo: TodoList = field(default_factory=TodoList)
    transcript: list[dict[str, Any]] = field(default_factory=list)
    cost: CostTracker = field(default_factory=CostTracker)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """JSON-serialisable view. Drops listeners; preserves tree state."""
        return {
            "id":           self.id,
            "created_at":   self.created_at,
            "repo_path":    self.repo_path,
            "endpoint":     self.endpoint,
            "method":       self.method,
            "workspace_id": self.workspace_id,
            "status":       self.status,
            "todo":         self.todo.snapshot(),
            "transcript":   list(self.transcript),
            "cost":         self.cost.summary(),
            "metadata":     dict(self.metadata),
        }


# In-process registry. The HTTP layer reads from here to attach SSE listeners.
_SESSIONS: dict[str, Session] = {}


def new_id() -> str:
    """Mint a fresh session id when the caller doesn't have a job_id."""
    return f"sess-{uuid.uuid4().hex[:12]}"


def create(
    id: str | None = None,
    *,
    repo_path: str = "",
    endpoint:  str = "",
    method:    str = "",
    workspace_id: str = "",
    metadata: dict[str, Any] | None = None,
) -> Session:
    """Register a new session; return it.

    Calling :func:`create` twice with the same id replaces the prior entry.
    That matches typical job-restart semantics — re-creating ``job-abc`` is
    "this run is the new authority."
    """
    sess = Session(
        id=id or new_id(),
        repo_path=repo_path,
        endpoint=endpoint,
        method=method,
        workspace_id=workspace_id,
        metadata=dict(metadata or {}),
    )
    _SESSIONS[sess.id] = sess
    log.debug("session.create", id=sess.id, endpoint=endpoint, method=method)
    return sess


def get_session(id: str) -> Session:
    """Fetch a session by id; raises :class:`KeyError` if missing."""
    return _SESSIONS[id]


def get_session_or_none(id: str) -> Session | None:
    """Fetch by id, or None — used by the SSE handler so missing ids are 404."""
    return _SESSIONS.get(id)


def list_sessions() -> list[dict[str, Any]]:
    """One-line summaries for ``brain session list``."""
    return [
        {
            "id":         s.id,
            "created_at": s.created_at,
            "status":     s.status,
            "endpoint":   f"{s.method} {s.endpoint}".strip(),
            "cost_usd":   round(s.cost.total_cost_usd, 4),
        }
        for s in _SESSIONS.values()
    ]


def remove(id: str) -> bool:
    """Drop a session from the registry (e.g. after archive). Returns True if removed."""
    return _SESSIONS.pop(id, None) is not None


# ── on-disk persistence ────────────────────────────────────────────────────


def session_dir() -> Path:
    """Default directory for save/load. Created on first use.

    Resolution: ``$BRAIN_HOME/sessions`` if set, else ``~/.brain/sessions``.
    """
    home = os.environ.get("BRAIN_HOME")
    if home:
        return Path(home) / "sessions"
    return Path.home() / ".brain" / "sessions"


def save(session: Session, path: Path | str | None = None) -> Path:
    """Write a session snapshot to disk; return the file path.

    Default path is ``session_dir() / "<id>.json"``. Listeners and event
    loops are not persisted — only the resumable state.
    """
    target = Path(path) if path is not None else session_dir() / f"{session.id}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(session.to_dict(), indent=2, default=str))
    return target


def load(path: Path | str) -> Session:
    """Reconstruct a :class:`Session` from a JSON snapshot.

    Restores TodoList items as :class:`TodoItem` nodes (without listeners) and
    CostTracker as a fresh tracker seeded with per-tool totals so cost-by-tool
    reads match the snapshot.
    """
    from companybrain.harness.progress import TodoItem, TodoStatus

    raw = json.loads(Path(path).read_text())

    todo = TodoList()
    def _hydrate(items: list[dict[str, Any]]) -> list[TodoItem]:
        out: list[TodoItem] = []
        for it in items:
            node = TodoItem(
                id=it["id"],
                title=it["title"],
                status=TodoStatus(it.get("status", "pending")),
                started_at=it.get("started_at"),
                completed_at=it.get("completed_at"),
                metadata=dict(it.get("metadata", {})),
                children=_hydrate(it.get("children", [])),
            )
            out.append(node)
        return out
    todo.root = _hydrate(raw.get("todo", []))

    cost = CostTracker()
    cost_summary = raw.get("cost", {})
    cost.total_cost_usd = float(cost_summary.get("total_cost_usd", 0.0))
    cost.total_calls    = int(cost_summary.get("total_calls", 0))
    for tool, slot in cost_summary.get("by_tool", {}).items():
        cost.by_tool[tool] = {
            "calls":         int(slot.get("calls", 0)),
            "input_tokens":  int(slot.get("input_tokens", 0)),
            "output_tokens": int(slot.get("output_tokens", 0)),
            "cost_usd":      float(slot.get("cost_usd", 0.0)),
        }

    sess = Session(
        id=raw["id"],
        created_at=raw.get("created_at", _now()),
        repo_path=raw.get("repo_path", ""),
        endpoint=raw.get("endpoint", ""),
        method=raw.get("method", ""),
        workspace_id=raw.get("workspace_id", ""),
        status=raw.get("status", "active"),
        todo=todo,
        transcript=list(raw.get("transcript", [])),
        cost=cost,
        metadata=dict(raw.get("metadata", {})),
    )
    return sess


__all__ = [
    "Session",
    "new_id",
    "create",
    "get_session",
    "get_session_or_none",
    "list_sessions",
    "remove",
    "save",
    "load",
    "session_dir",
]
