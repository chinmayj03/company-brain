"""
Unit tests for ADR-0082 DriftItem and DriftSnapshot models.

Tests cover:
- Stable ID generation
- Serialization round-trip (to_dict / from_dict)
- age_days refresh
- ResolutionRecord serialization
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from companybrain.drift.models import (
    DriftItem,
    DriftSnapshot,
    DomainDriftScore,
    ResolutionRecord,
    _parse_dt,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_item(
    rule_id: str = "adr-0007-schema",
    scope_urn: str = "urn:cb:api:PaymentController",
    kind: str = "schema",
    severity: str = "high",
    state: str = "open",
    age_days: float = 5.0,
) -> DriftItem:
    now = datetime.now(timezone.utc)
    return DriftItem(
        id=DriftItem.make_id(rule_id, scope_urn, kind),
        rule_id=rule_id,
        rule_source="adr",
        kind=kind,
        scope_urn=scope_urn,
        domain_areas=["Payer", "ClaimSubmission"],
        severity=severity,
        detected_at=now - timedelta(days=age_days),
        last_seen_at=now,
        age_days=age_days,
        state=state,
    )


# ── Stable ID ─────────────────────────────────────────────────────────────────

class TestStableId:
    def test_same_inputs_produce_same_id(self):
        id1 = DriftItem.make_id("r1", "urn:x", "schema")
        id2 = DriftItem.make_id("r1", "urn:x", "schema")
        assert id1 == id2

    def test_different_rule_id_produces_different_id(self):
        id1 = DriftItem.make_id("r1", "urn:x", "schema")
        id2 = DriftItem.make_id("r2", "urn:x", "schema")
        assert id1 != id2

    def test_different_scope_urn_produces_different_id(self):
        id1 = DriftItem.make_id("r1", "urn:x", "schema")
        id2 = DriftItem.make_id("r1", "urn:y", "schema")
        assert id1 != id2

    def test_id_is_16_chars(self):
        item_id = DriftItem.make_id("r1", "urn:x", "schema")
        assert len(item_id) == 16
        assert all(c in "0123456789abcdef" for c in item_id)


# ── DriftItem serialization ───────────────────────────────────────────────────

class TestDriftItemSerialization:
    def test_round_trip(self):
        item = _make_item()
        d = item.to_dict()
        restored = DriftItem.from_dict(d)
        assert restored.id == item.id
        assert restored.rule_id == item.rule_id
        assert restored.severity == item.severity
        assert restored.state == item.state
        assert restored.domain_areas == item.domain_areas

    def test_all_fields_present(self):
        item = _make_item()
        d = item.to_dict()
        required_keys = {
            "id", "rule_id", "rule_source", "kind", "scope_urn",
            "domain_areas", "severity", "detected_at", "last_seen_at",
            "age_days", "state", "resolution", "related_prs",
            "estimated_remediation_days", "description",
        }
        assert required_keys.issubset(d.keys())

    def test_resolution_round_trip(self):
        item = _make_item(state="resolved")
        item.resolution = ResolutionRecord(
            resolved_at=datetime.now(timezone.utc),
            resolved_by="alice",
            auto_resolved=False,
            justification="Fixed in PR #42",
            pr_url="https://github.com/org/repo/pull/42",
        )
        d = item.to_dict()
        restored = DriftItem.from_dict(d)
        assert restored.resolution is not None
        assert restored.resolution.resolved_by == "alice"
        assert restored.resolution.pr_url == "https://github.com/org/repo/pull/42"

    def test_no_resolution_serializes_as_null(self):
        item = _make_item(state="open")
        d = item.to_dict()
        assert d["resolution"] is None

    def test_related_prs_default_empty(self):
        item = _make_item()
        assert item.related_prs == []
        d = item.to_dict()
        assert d["related_prs"] == []

    def test_waive_expires_at_round_trip(self):
        item = _make_item(state="waived")
        item.waive_expires_at = datetime(2026, 8, 15, 2, 0, 0, tzinfo=timezone.utc)
        d = item.to_dict()
        restored = DriftItem.from_dict(d)
        assert restored.waive_expires_at is not None
        assert restored.waive_expires_at.year == 2026


# ── age_days refresh ──────────────────────────────────────────────────────────

class TestAgeRefresh:
    def test_refresh_age_computes_from_detected_at(self):
        now = datetime.now(timezone.utc)
        item = _make_item(age_days=0.0)
        item.detected_at = now - timedelta(days=10)
        item.refresh_age(now)
        assert abs(item.age_days - 10.0) < 0.1

    def test_refresh_age_naive_datetime_is_treated_as_utc(self):
        """Naive detected_at should not raise; treated as UTC."""
        item = _make_item()
        # Force naive detected_at.
        item.detected_at = datetime.utcnow() - timedelta(days=30)
        item.refresh_age()
        assert item.age_days >= 29.0


# ── DriftSnapshot serialization ───────────────────────────────────────────────

class TestDriftSnapshotSerialization:
    def _make_snapshot(self) -> DriftSnapshot:
        return DriftSnapshot(
            snapshot_id="snap-001",
            snapshot_at=datetime.now(timezone.utc),
            workspace="test-ws",
            items_open=3,
            items_acknowledged=1,
            items_in_flight=0,
            items_by_domain={"Payer": 2, "ClaimSubmission": 1},
            items_by_severity={"high": 2, "medium": 1},
            new_since_last=["abc123"],
            resolved_since_last=[],
            delta_score=8.5,
            all_item_ids=["abc123", "def456"],
        )

    def test_round_trip(self):
        snap = self._make_snapshot()
        d = snap.to_dict()
        restored = DriftSnapshot.from_dict(d)
        assert restored.snapshot_id == snap.snapshot_id
        assert restored.items_open == 3
        assert restored.items_by_domain == {"Payer": 2, "ClaimSubmission": 1}
        assert restored.delta_score == 8.5
        assert restored.all_item_ids == ["abc123", "def456"]

    def test_all_fields_present(self):
        snap = self._make_snapshot()
        d = snap.to_dict()
        required = {
            "snapshot_id", "snapshot_at", "workspace", "items_open",
            "items_acknowledged", "items_in_flight", "items_by_domain",
            "items_by_severity", "new_since_last", "resolved_since_last",
            "delta_score", "all_item_ids",
        }
        assert required.issubset(d.keys())


# ── DomainDriftScore ──────────────────────────────────────────────────────────

class TestDomainDriftScore:
    def test_to_dict_rounds_floats(self):
        score = DomainDriftScore(
            domain="Payer",
            item_count=3,
            weighted_score=12.3456789,
            critical_count=1,
            oldest_item_age_days=45.678,
            in_flight_remediation_days=5.0,
        )
        d = score.to_dict()
        assert d["weighted_score"] == 12.346
        assert d["oldest_item_age_days"] == 45.7


# ── _parse_dt helper ──────────────────────────────────────────────────────────

class TestParseDt:
    def test_parses_iso_with_z(self):
        dt = _parse_dt("2026-05-17T02:00:00Z")
        assert dt.tzinfo is not None
        assert dt.year == 2026

    def test_parses_iso_with_offset(self):
        dt = _parse_dt("2026-05-17T02:00:00+00:00")
        assert dt.tzinfo is not None

    def test_parses_naive_iso(self):
        dt = _parse_dt("2026-05-17T02:00:00")
        # Should default to UTC.
        assert dt.tzinfo is not None
