"""
ADR-0090 M1 — BrainEvent dataclass.

This is the canonical event-stream record for the event-sourced memory
substrate. It is DISTINCT from companybrain.store.base.StoreEvent (formerly
BrainEvent), which is a simple upsert/invalidate envelope used internally by
the store layer.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal, Optional

# All valid event_type values (ADR-0073 M1).
EVENT_TYPES = Literal[
    # Code lifecycle
    "GitCommit",
    "PROpened",
    "PRMerged",
    "PRClosed",
    "BranchCreated",
    "BranchDeleted",
    # Deployment lifecycle
    "Deploy",
    "Rollback",
    "ConfigChange",
    "SchemaMigration",
    # Incident lifecycle
    "IncidentDeclared",
    "IncidentMitigated",
    "IncidentResolved",
    "PostmortemPublished",
    # Human / agent actions
    "HumanFactWritten",
    "AgentAction",
    "VerifierCorrection",
    "QueryAsked",
    "FeedbackGiven",
    # External events
    "ExternalDocChange",
]

# Plain tuple of all valid event type strings — useful for validation.
ALL_EVENT_TYPES: tuple[str, ...] = (
    "GitCommit",
    "PROpened",
    "PRMerged",
    "PRClosed",
    "BranchCreated",
    "BranchDeleted",
    "Deploy",
    "Rollback",
    "ConfigChange",
    "SchemaMigration",
    "IncidentDeclared",
    "IncidentMitigated",
    "IncidentResolved",
    "PostmortemPublished",
    "HumanFactWritten",
    "AgentAction",
    "VerifierCorrection",
    "QueryAsked",
    "FeedbackGiven",
    "ExternalDocChange",
)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _new_uuid() -> str:
    return str(uuid.uuid4())


@dataclass(frozen=True)
class BrainEvent:
    """
    Immutable event-stream record.

    Fields match the ADR-0073 M1 schema exactly.  All fields are required
    except `urn_affected` (Optional) and the list fields which default to
    empty lists so callers can omit them when not applicable.

    The dataclass is *frozen* — events are append-only; mutation is never
    allowed.  Build new events with BrainEvent(...) or the factory helpers in
    companybrain.events.emitter.
    """

    # ── Identity ──────────────────────────────────────────────────────────────
    id: str = field(default_factory=_new_uuid)

    # ── Scope ─────────────────────────────────────────────────────────────────
    workspace_id: str = ""
    repo: str = ""
    branch: str = ""          # empty string = cross-branch / repo-level event

    # ── Type + payload ────────────────────────────────────────────────────────
    event_type: str = "AgentAction"   # one of ALL_EVENT_TYPES
    payload: dict = field(default_factory=dict)

    # ── Timestamps ────────────────────────────────────────────────────────────
    occurred_at: datetime = field(default_factory=_now_utc)
    recorded_at: datetime = field(default_factory=_now_utc)

    # ── Causality + actors ────────────────────────────────────────────────────
    causal_parents: tuple[str, ...] = field(default_factory=tuple)
    actors: tuple[str, ...] = field(default_factory=tuple)

    # ── Entity linkage (indexed) ──────────────────────────────────────────────
    urn_affected: Optional[str] = None   # which entity this touches (for index)

    # ── Post-init validation ─────────────────────────────────────────────────
    def __post_init__(self) -> None:
        if self.event_type not in ALL_EVENT_TYPES:
            raise ValueError(
                f"Unknown event_type {self.event_type!r}. "
                f"Must be one of: {ALL_EVENT_TYPES}"
            )

    def to_dict(self) -> dict:
        """Return a JSON-serialisable dict (timestamps as ISO strings)."""
        return {
            "id": self.id,
            "workspace_id": self.workspace_id,
            "repo": self.repo,
            "branch": self.branch,
            "event_type": self.event_type,
            "payload": self.payload,
            "occurred_at": self.occurred_at.isoformat(),
            "recorded_at": self.recorded_at.isoformat(),
            "causal_parents": list(self.causal_parents),
            "actors": list(self.actors),
            "urn_affected": self.urn_affected,
        }
