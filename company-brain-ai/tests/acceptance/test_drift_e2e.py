"""
ADR-0082 P1 — End-to-end acceptance test for drift entity lifecycle.

Scenario:
  1. Create a workspace with intentional drift signals (written directly to .brain/)
  2. Run snapshot → verify DriftItem created with correct attributes
  3. Run snapshot again → verify last_seen_at bumped, no duplicate item
  4. Acknowledge item via state machine → verify state transition
  5. Fix the drift (remove signal file) → run snapshot → verify resolved
  6. Verify per-domain score computed correctly
  7. Verify snapshot completes in < 30s
"""
from __future__ import annotations

import json
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from companybrain.drift.models import DriftItem
from companybrain.drift.scheduler import run_snapshot_now
from companybrain.drift.scorer import all_domain_scores, domain_drift_score
from companybrain.drift.state_machine import DriftStateMachine, InvalidTransition
from companybrain.drift.store import DriftStore


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def tmp_brain(tmp_path: Path) -> Path:
    """Return a temporary .brain/ directory ready for drift tests."""
    brain = tmp_path / ".brain"
    brain.mkdir(parents=True)
    return brain


@pytest.fixture
def store(tmp_brain: Path) -> DriftStore:
    return DriftStore(brain_root=tmp_brain)


def _write_drift_signal(brain_root: Path, signal_id: str, severity: str, urn: str, description: str = "") -> None:
    """Write a fake ADR-0007 DriftSignal file under .brain/drift_signal/."""
    signals_dir = brain_root / "drift_signal"
    signals_dir.mkdir(parents=True, exist_ok=True)
    signal = {
        "id": signal_id,
        "severity": severity,
        "description": description or f"Drift detected in {urn}",
        "implementation_urn": urn,
        "contract_urn": f"urn:cb:contract:{urn}",
        "detected_fields": ["amount", "currency"],
    }
    (signals_dir / f"{signal_id}.json").write_text(json.dumps(signal, indent=2))


def _remove_drift_signal(brain_root: Path, signal_id: str) -> None:
    """Remove a drift signal file — simulates fixing the violation."""
    path = brain_root / "drift_signal" / f"{signal_id}.json"
    if path.exists():
        path.unlink()


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestDriftItemCreation:
    def test_snapshot_creates_drift_item_from_signal(self, tmp_brain: Path, store: DriftStore):
        """A DriftSignal file → snapshot → DriftItem with correct attributes."""
        _write_drift_signal(
            tmp_brain,
            signal_id="sig-payer-001",
            severity="breaking",   # maps to "critical"
            urn="urn:cb:api:PaymentController",
            description="Response shape missing 'amount' field",
        )

        result = run_snapshot_now(brain_root=tmp_brain, workspace="test")

        assert result.created_count == 1
        items = store.load_all_items()
        assert len(items) == 1

        item = items[0]
        assert item.rule_id == "sig-payer-001"
        assert item.severity == "critical"
        assert item.scope_urn == "urn:cb:api:PaymentController"
        assert item.state == "open"
        assert item.kind == "schema"
        assert item.rule_source == "adr"
        assert item.description == "Response shape missing 'amount' field"
        assert item.age_days >= 0.0

    def test_snapshot_id_is_stable(self, tmp_brain: Path, store: DriftStore):
        """Same violation always produces the same item ID (no duplicates)."""
        _write_drift_signal(tmp_brain, "sig-001", "warning", "urn:cb:api:Foo")

        run_snapshot_now(brain_root=tmp_brain)
        items_after_first = store.load_all_items()
        first_id = items_after_first[0].id

        run_snapshot_now(brain_root=tmp_brain)
        items_after_second = store.load_all_items()
        # Still exactly one item.
        assert len(items_after_second) == 1
        assert items_after_second[0].id == first_id

    def test_last_seen_at_bumped_on_second_run(self, tmp_brain: Path, store: DriftStore):
        """Repeated snapshots bump last_seen_at but don't duplicate the item."""
        _write_drift_signal(tmp_brain, "sig-001", "warning", "urn:cb:api:Foo")

        run_snapshot_now(brain_root=tmp_brain)
        first_item = store.load_all_items()[0]
        first_seen = first_item.last_seen_at

        # Small sleep to ensure time advances.
        time.sleep(0.05)
        run_snapshot_now(brain_root=tmp_brain)
        second_item = store.load_all_items()[0]

        assert len(store.load_all_items()) == 1
        assert second_item.last_seen_at >= first_seen


class TestStateTransitions:
    def test_acknowledge_via_state_machine(self, tmp_brain: Path, store: DriftStore):
        """open → acknowledged via DriftStateMachine."""
        _write_drift_signal(tmp_brain, "sig-001", "breaking", "urn:cb:api:Claim")
        run_snapshot_now(brain_root=tmp_brain)

        item = store.load_all_items()[0]
        assert item.state == "open"

        sm = DriftStateMachine(item)
        sm.acknowledge(actor="alice@company.com")
        store.upsert_item(item)

        reloaded = store.load_item(item.id)
        assert reloaded.state == "acknowledged"

    def test_waive_with_justification(self, tmp_brain: Path, store: DriftStore):
        """open → waived requires justification."""
        _write_drift_signal(tmp_brain, "sig-002", "warning", "urn:cb:api:Auth")
        run_snapshot_now(brain_root=tmp_brain)

        item = store.load_all_items()[0]
        sm = DriftStateMachine(item)
        sm.waive(justification="Accepted for Q3 sprint, reviewed with architect")
        store.upsert_item(item)

        reloaded = store.load_item(item.id)
        assert reloaded.state == "waived"
        assert reloaded.resolution.justification == "Accepted for Q3 sprint, reviewed with architect"
        assert reloaded.waive_expires_at is not None

    def test_waive_without_justification_raises(self, tmp_brain: Path, store: DriftStore):
        _write_drift_signal(tmp_brain, "sig-003", "info", "urn:cb:api:Baz")
        run_snapshot_now(brain_root=tmp_brain)
        item = store.load_all_items()[0]
        with pytest.raises(ValueError):
            DriftStateMachine(item).waive(justification="")

    def test_invalid_transition_raises(self, tmp_brain: Path, store: DriftStore):
        """Cannot go from in_flight back to acknowledged."""
        _write_drift_signal(tmp_brain, "sig-004", "warning", "urn:cb:api:X")
        run_snapshot_now(brain_root=tmp_brain)
        item = store.load_all_items()[0]
        # Move to in_flight first.
        DriftStateMachine(item).start_flight()
        assert item.state == "in_flight"
        with pytest.raises(InvalidTransition):
            DriftStateMachine(item).acknowledge()


class TestAutoResolution:
    def test_fixed_drift_becomes_resolved(self, tmp_brain: Path, store: DriftStore):
        """Remove the signal file → next snapshot auto-resolves the item."""
        _write_drift_signal(tmp_brain, "sig-fix", "breaking", "urn:cb:api:Payment")
        result1 = run_snapshot_now(brain_root=tmp_brain)
        assert result1.created_count == 1

        item_id = store.load_all_items()[0].id

        # "Fix" the drift — remove the signal.
        _remove_drift_signal(tmp_brain, "sig-fix")

        result2 = run_snapshot_now(brain_root=tmp_brain)
        assert result2.resolved_count == 1

        item = store.load_item(item_id)
        assert item.state == "resolved"
        assert item.resolution is not None
        assert item.resolution.auto_resolved is True

    def test_new_items_appear_in_new_since_last(self, tmp_brain: Path, store: DriftStore):
        """Second snapshot with a new signal should report it in new_since_last."""
        _write_drift_signal(tmp_brain, "sig-old", "info", "urn:cb:api:A")
        run_snapshot_now(brain_root=tmp_brain)

        _write_drift_signal(tmp_brain, "sig-new", "warning", "urn:cb:api:B")
        result2 = run_snapshot_now(brain_root=tmp_brain)

        snap2 = store.load_snapshot(result2.snapshot.snapshot_id)
        new_ids = snap2.new_since_last
        assert len(new_ids) == 1  # only sig-new is brand new

    def test_resolved_appear_in_resolved_since_last(self, tmp_brain: Path, store: DriftStore):
        _write_drift_signal(tmp_brain, "sig-r", "breaking", "urn:cb:api:C")
        run_snapshot_now(brain_root=tmp_brain)
        _remove_drift_signal(tmp_brain, "sig-r")
        result2 = run_snapshot_now(brain_root=tmp_brain)

        snap2 = store.load_snapshot(result2.snapshot.snapshot_id)
        assert len(snap2.resolved_since_last) == 1


class TestPerDomainScoring:
    def test_score_positive_with_violations(self, tmp_brain: Path, store: DriftStore):
        """Items with domain attribution produce a positive weighted score."""
        # Write a signal for a URN that has a brain entity with domain_areas.
        # Without a brain entity, domain_areas defaults to [] → "unattributed".
        _write_drift_signal(tmp_brain, "sig-s1", "breaking", "urn:cb:api:Payer", "payer drift")
        _write_drift_signal(tmp_brain, "sig-s2", "warning",  "urn:cb:api:Claim", "claim drift")
        run_snapshot_now(brain_root=tmp_brain)

        items = store.load_all_items()
        # Without domain inference, all items land in "unattributed".
        scores = all_domain_scores(items)
        assert len(scores) > 0
        total_weighted = sum(s.weighted_score for s in scores)
        assert total_weighted > 0

    def test_waived_items_excluded(self, tmp_brain: Path, store: DriftStore):
        _write_drift_signal(tmp_brain, "sig-w", "critical", "urn:cb:api:X")
        run_snapshot_now(brain_root=tmp_brain)

        item = store.load_all_items()[0]
        DriftStateMachine(item).waive(justification="Temporary exception")
        store.upsert_item(item)

        items = store.load_all_items()
        scores = all_domain_scores(items, exclude_waived=True)
        total = sum(s.item_count for s in scores)
        assert total == 0

    def test_domain_drift_score_direct(self, tmp_brain: Path, store: DriftStore):
        """domain_drift_score with explicit domain_areas on items."""
        from datetime import timedelta
        now = datetime.now(timezone.utc)
        item = DriftItem(
            id=DriftItem.make_id("r-direct", "urn:x", "schema"),
            rule_id="r-direct",
            rule_source="adr",
            kind="schema",
            scope_urn="urn:x",
            domain_areas=["Payer"],
            severity="critical",
            detected_at=now - timedelta(days=100),
            last_seen_at=now,
            age_days=100.0,
            state="open",
        )
        store.upsert_item(item)

        items = store.load_all_items()
        score = domain_drift_score("Payer", items)
        # weight=8 × age_factor(100)=1+(100-90)/90≈1.111 → ~8.89
        assert score.item_count == 1
        assert score.critical_count == 1
        assert score.weighted_score == pytest.approx(8.0 * (1.0 + 10.0 / 90.0), rel=1e-3)


class TestSnapshotPerformance:
    def test_snapshot_completes_under_30s(self, tmp_brain: Path):
        """Snapshot must finish in < 30s (acceptance criterion)."""
        # Write 50 drift signals to simulate a non-trivial codebase.
        for i in range(50):
            _write_drift_signal(
                tmp_brain,
                f"sig-perf-{i:03d}",
                ["breaking", "warning", "info"][i % 3],
                f"urn:cb:api:Controller{i}",
            )

        t0 = time.monotonic()
        run_snapshot_now(brain_root=tmp_brain)
        elapsed = time.monotonic() - t0

        assert elapsed < 30.0, f"Snapshot took {elapsed:.1f}s (limit: 30s)"


class TestSnapshotAggregate:
    def test_items_open_count(self, tmp_brain: Path, store: DriftStore):
        _write_drift_signal(tmp_brain, "s1", "warning", "urn:a")
        _write_drift_signal(tmp_brain, "s2", "breaking", "urn:b")
        result = run_snapshot_now(brain_root=tmp_brain)
        assert result.snapshot.items_open == 2

    def test_delta_score_positive_on_first_run(self, tmp_brain: Path):
        _write_drift_signal(tmp_brain, "s1", "breaking", "urn:a")
        result = run_snapshot_now(brain_root=tmp_brain)
        assert result.snapshot.delta_score > 0

    def test_delta_score_negative_on_resolution(self, tmp_brain: Path):
        _write_drift_signal(tmp_brain, "s1", "breaking", "urn:a")
        run_snapshot_now(brain_root=tmp_brain)
        _remove_drift_signal(tmp_brain, "s1")
        result2 = run_snapshot_now(brain_root=tmp_brain)
        assert result2.snapshot.delta_score < 0

    def test_items_by_severity_populated(self, tmp_brain: Path, store: DriftStore):
        _write_drift_signal(tmp_brain, "s1", "breaking", "urn:a")   # → critical
        _write_drift_signal(tmp_brain, "s2", "warning",  "urn:b")   # → high
        result = run_snapshot_now(brain_root=tmp_brain)
        snap = store.load_snapshot(result.snapshot.snapshot_id)
        assert snap.items_by_severity.get("critical", 0) >= 1
        assert snap.items_by_severity.get("high", 0) >= 1
