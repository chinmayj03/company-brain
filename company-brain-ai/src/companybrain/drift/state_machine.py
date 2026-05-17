"""
ADR-0082 M6 — DriftItem lifecycle state machine.

Valid transitions:
    open → acknowledged
    open → in_flight
    open → waived
    acknowledged → in_flight
    acknowledged → resolved
    acknowledged → waived
    in_flight → resolved
    in_flight → open        (PR closed without merge — re-opens)
    resolved → open         (regression — rule violated again)
    waived → open           (waive expiry or re-activation)

Terminal states: resolved (can regress), waived (can expire).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from companybrain.drift.models import DriftItem, DriftState, ResolutionRecord


# ── Valid transition table ─────────────────────────────────────────────────────

_VALID_TRANSITIONS: dict[DriftState, set[DriftState]] = {
    "open":         {"acknowledged", "in_flight", "waived", "resolved"},
    "acknowledged": {"in_flight", "resolved", "waived"},
    "in_flight":    {"resolved", "open"},
    "resolved":     {"open"},               # regression
    "waived":       {"open"},               # expiry or manual re-activate
}

# Default waiver duration before auto-reactivation.
_DEFAULT_WAIVE_DAYS: int = 90


class InvalidTransition(ValueError):
    """Raised when an illegal state transition is attempted."""


class DriftStateMachine:
    """
    Enforces valid lifecycle transitions for a DriftItem.

    Usage::

        sm = DriftStateMachine(item)
        sm.acknowledge(actor="alice@company.com")
        sm.waive(justification="Intentional — will revisit Q3", actor="bob@company.com")
    """

    def __init__(self, item: DriftItem):
        self.item = item

    # ── Public transition methods ─────────────────────────────────────────────

    def acknowledge(self, actor: Optional[str] = None) -> DriftItem:
        """Human marks the item as known. open → acknowledged."""
        self._transition("acknowledged")
        return self.item

    def start_flight(self, pr_url: Optional[str] = None, actor: Optional[str] = None) -> DriftItem:
        """A PR is open to address this item. * → in_flight."""
        self._transition("in_flight")
        if pr_url and pr_url not in self.item.related_prs:
            self.item.related_prs.append(pr_url)
        return self.item

    def resolve(
        self,
        actor: Optional[str] = None,
        pr_url: Optional[str] = None,
        auto: bool = False,
    ) -> DriftItem:
        """Rule no longer violated. * → resolved."""
        self._transition("resolved")
        self.item.resolution = ResolutionRecord(
            resolved_at=datetime.now(timezone.utc),
            resolved_by=actor or ("auto" if auto else None),
            auto_resolved=auto,
            pr_url=pr_url,
        )
        return self.item

    def waive(
        self,
        justification: str,
        actor: Optional[str] = None,
        duration_days: int = _DEFAULT_WAIVE_DAYS,
    ) -> DriftItem:
        """
        Human suppresses the item with a required justification.
        Auto-reactivates after ``duration_days`` days.
        """
        if not justification or not justification.strip():
            raise ValueError("justification is required to waive a drift item")
        self._transition("waived")
        now = datetime.now(timezone.utc)
        self.item.resolution = ResolutionRecord(
            resolved_at=now,
            resolved_by=actor,
            auto_resolved=False,
            justification=justification,
        )
        self.item.waive_expires_at = now + timedelta(days=duration_days)
        return self.item

    def reopen(self, reason: Optional[str] = None) -> DriftItem:
        """Regression or waive expiry — returns item to open. resolved/waived → open."""
        self._transition("open")
        self.item.resolution = None
        self.item.waive_expires_at = None
        return self.item

    # ── Internal ──────────────────────────────────────────────────────────────

    def _transition(self, target: DriftState) -> None:
        current = self.item.state
        allowed = _VALID_TRANSITIONS.get(current, set())
        if target not in allowed:
            raise InvalidTransition(
                f"Cannot transition DriftItem {self.item.id!r} "
                f"from {current!r} to {target!r}. "
                f"Allowed targets from {current!r}: {sorted(allowed)}"
            )
        self.item.state = target

    # ── Class-level helpers ───────────────────────────────────────────────────

    @classmethod
    def can_transition(cls, from_state: DriftState, to_state: DriftState) -> bool:
        """Return True if the transition is valid without raising."""
        return to_state in _VALID_TRANSITIONS.get(from_state, set())

    @classmethod
    def valid_transitions_from(cls, state: DriftState) -> list[DriftState]:
        """Return all reachable states from ``state``."""
        return sorted(_VALID_TRANSITIONS.get(state, set()))
