"""
Unit tests for ADR-0082 DriftItem lifecycle state machine.

Tests cover:
- All valid transitions succeed
- All invalid transitions raise InvalidTransition
- Waive requires justification
- resolve() sets ResolutionRecord
- waive() sets waive_expires_at
- reopen() clears resolution
- can_transition() and valid_transitions_from() helpers
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from companybrain.drift.models import DriftItem, DriftState
from companybrain.drift.state_machine import DriftStateMachine, InvalidTransition


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _item(state: DriftState = "open", item_id: str = "deadbeef01234567") -> DriftItem:
    now = datetime.now(timezone.utc)
    return DriftItem(
        id=item_id,
        rule_id="adr-0007-test",
        rule_source="adr",
        kind="schema",
        scope_urn="urn:cb:api:TestController",
        domain_areas=["Payer"],
        severity="high",
        detected_at=now - timedelta(days=5),
        last_seen_at=now,
        age_days=5.0,
        state=state,
    )


# ── Valid transitions ─────────────────────────────────────────────────────────

class TestValidTransitions:
    def test_open_to_acknowledged(self):
        item = _item("open")
        DriftStateMachine(item).acknowledge()
        assert item.state == "acknowledged"

    def test_open_to_in_flight(self):
        item = _item("open")
        DriftStateMachine(item).start_flight(pr_url="https://github.com/org/repo/pull/1")
        assert item.state == "in_flight"
        assert "https://github.com/org/repo/pull/1" in item.related_prs

    def test_open_to_waived(self):
        item = _item("open")
        DriftStateMachine(item).waive(justification="Intentional for Q3")
        assert item.state == "waived"

    def test_acknowledged_to_in_flight(self):
        item = _item("acknowledged")
        DriftStateMachine(item).start_flight()
        assert item.state == "in_flight"

    def test_acknowledged_to_resolved(self):
        item = _item("acknowledged")
        DriftStateMachine(item).resolve(actor="alice")
        assert item.state == "resolved"

    def test_acknowledged_to_waived(self):
        item = _item("acknowledged")
        DriftStateMachine(item).waive(justification="Accepted risk")
        assert item.state == "waived"

    def test_in_flight_to_resolved(self):
        item = _item("in_flight")
        DriftStateMachine(item).resolve(pr_url="https://github.com/org/repo/pull/99")
        assert item.state == "resolved"
        assert item.resolution is not None
        assert item.resolution.pr_url == "https://github.com/org/repo/pull/99"

    def test_in_flight_to_open(self):
        """PR closed without merge → regression back to open."""
        item = _item("in_flight")
        DriftStateMachine(item).reopen()
        assert item.state == "open"

    def test_resolved_to_open(self):
        """Regression — rule violated again."""
        item = _item("resolved")
        DriftStateMachine(item).reopen()
        assert item.state == "open"
        assert item.resolution is None

    def test_waived_to_open(self):
        """Waive expiry — item reactivated."""
        item = _item("waived")
        DriftStateMachine(item).reopen()
        assert item.state == "open"


# ── Invalid transitions ───────────────────────────────────────────────────────

class TestInvalidTransitions:
    @pytest.mark.parametrize("from_state,to_method", [
        ("in_flight",    "acknowledge"),
        ("resolved",     "acknowledge"),
        ("resolved",     "start_flight"),
        ("waived",       "acknowledge"),
        ("waived",       "start_flight"),
    ])
    def test_invalid_transitions_raise(self, from_state, to_method):
        item = _item(from_state)
        sm = DriftStateMachine(item)
        with pytest.raises(InvalidTransition):
            method = getattr(sm, to_method)
            if to_method == "waive":
                method(justification="x")
            else:
                method()

    def test_cannot_acknowledge_from_in_flight(self):
        item = _item("in_flight")
        with pytest.raises(InvalidTransition):
            DriftStateMachine(item).acknowledge()


# ── Waive semantics ───────────────────────────────────────────────────────────

class TestWaiveSemantics:
    def test_waive_requires_justification(self):
        item = _item("open")
        with pytest.raises(ValueError):
            DriftStateMachine(item).waive(justification="")

    def test_waive_sets_resolution_record(self):
        item = _item("open")
        DriftStateMachine(item).waive(justification="Known issue, deferred to Q4")
        assert item.resolution is not None
        assert item.resolution.justification == "Known issue, deferred to Q4"
        assert item.resolution.auto_resolved is False

    def test_waive_sets_expiry(self):
        item = _item("open")
        DriftStateMachine(item).waive(justification="ok", duration_days=30)
        assert item.waive_expires_at is not None
        now = datetime.now(timezone.utc)
        diff = item.waive_expires_at - now
        assert 29 <= diff.days <= 30

    def test_waive_default_duration_is_90_days(self):
        item = _item("open")
        DriftStateMachine(item).waive(justification="ok")
        now = datetime.now(timezone.utc)
        diff = item.waive_expires_at - now
        # Allow ±1 day for sub-second timing variance.
        assert 89 <= diff.days <= 90


# ── Resolve semantics ─────────────────────────────────────────────────────────

class TestResolveSemantics:
    def test_auto_resolve_sets_flag(self):
        item = _item("in_flight")
        DriftStateMachine(item).resolve(auto=True)
        assert item.resolution.auto_resolved is True
        assert item.resolution.resolved_by == "auto"

    def test_manual_resolve_sets_actor(self):
        item = _item("acknowledged")
        DriftStateMachine(item).resolve(actor="bob@company.com")
        assert item.resolution.resolved_by == "bob@company.com"
        assert item.resolution.auto_resolved is False

    def test_resolve_sets_resolved_at(self):
        item = _item("in_flight")
        before = datetime.now(timezone.utc)
        DriftStateMachine(item).resolve()
        after = datetime.now(timezone.utc)
        assert before <= item.resolution.resolved_at <= after


# ── Reopen semantics ──────────────────────────────────────────────────────────

class TestReopenSemantics:
    def test_reopen_clears_resolution(self):
        item = _item("resolved")
        item.resolution = object()  # type: ignore[assignment]
        DriftStateMachine(item).reopen()
        assert item.resolution is None

    def test_reopen_clears_waive_expiry(self):
        item = _item("waived")
        item.waive_expires_at = datetime.now(timezone.utc) + timedelta(days=10)
        DriftStateMachine(item).reopen()
        assert item.waive_expires_at is None


# ── Helpers ───────────────────────────────────────────────────────────────────

class TestHelpers:
    def test_can_transition_valid(self):
        assert DriftStateMachine.can_transition("open", "acknowledged") is True
        assert DriftStateMachine.can_transition("in_flight", "resolved") is True

    def test_can_transition_invalid(self):
        # in_flight cannot go back to acknowledged
        assert DriftStateMachine.can_transition("in_flight", "acknowledged") is False
        assert DriftStateMachine.can_transition("resolved", "waived") is False

    def test_valid_transitions_from_open(self):
        targets = DriftStateMachine.valid_transitions_from("open")
        assert set(targets) == {"acknowledged", "in_flight", "waived", "resolved"}

    def test_valid_transitions_from_resolved(self):
        targets = DriftStateMachine.valid_transitions_from("resolved")
        assert targets == ["open"]

    def test_valid_transitions_from_unknown_state(self):
        # Should not raise; returns empty list.
        targets = DriftStateMachine.valid_transitions_from("bogus")  # type: ignore[arg-type]
        assert targets == []
