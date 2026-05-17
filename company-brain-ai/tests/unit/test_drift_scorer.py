"""
Unit tests for ADR-0082 per-domain drift scorer.

Tests cover:
- severity_weight() returns correct values
- age_factor() behaviour: 1.0 below 90 days, compounds above
- item_score() = weight × factor
- domain_drift_score() aggregates correctly
- all_domain_scores() returns sorted list, handles unattributed items
- Waived items excluded from scoring by default
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from companybrain.drift.models import DriftItem
from companybrain.drift.scorer import (
    all_domain_scores,
    age_factor,
    domain_drift_score,
    item_score,
    severity_weight,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _item(
    rule_id: str,
    scope_urn: str,
    severity: str,
    age_days: float,
    domain_areas: list[str],
    state: str = "open",
) -> DriftItem:
    now = datetime.now(timezone.utc)
    return DriftItem(
        id=DriftItem.make_id(rule_id, scope_urn, "schema"),
        rule_id=rule_id,
        rule_source="adr",
        kind="schema",
        scope_urn=scope_urn,
        domain_areas=domain_areas,
        severity=severity,  # type: ignore[arg-type]
        detected_at=now - timedelta(days=age_days),
        last_seen_at=now,
        age_days=age_days,
        state=state,  # type: ignore[arg-type]
    )


# ── severity_weight ───────────────────────────────────────────────────────────

class TestSeverityWeight:
    def test_critical(self):
        assert severity_weight("critical") == 8.0

    def test_high(self):
        assert severity_weight("high") == 4.0

    def test_medium(self):
        assert severity_weight("medium") == 2.0

    def test_low(self):
        assert severity_weight("low") == 1.0

    def test_unknown_defaults_to_1(self):
        assert severity_weight("bogus") == 1.0  # type: ignore[arg-type]


# ── age_factor ────────────────────────────────────────────────────────────────

class TestAgeFactor:
    def test_zero_days_is_1(self):
        assert age_factor(0.0) == 1.0

    def test_30_days_is_1(self):
        assert age_factor(30.0) == 1.0

    def test_90_days_is_1(self):
        assert age_factor(90.0) == 1.0

    def test_180_days_compounds_to_2(self):
        assert age_factor(180.0) == pytest.approx(2.0)

    def test_270_days_compounds_to_3(self):
        assert age_factor(270.0) == pytest.approx(3.0)

    def test_negative_days_clamped_to_1(self):
        assert age_factor(-10.0) == 1.0

    def test_91_days_just_above_inflection(self):
        # 1 + (91 - 90) / 90 ≈ 1.011
        assert age_factor(91.0) == pytest.approx(1.0 + 1.0 / 90.0, rel=1e-3)


# ── item_score ────────────────────────────────────────────────────────────────

class TestItemScore:
    def test_critical_young_item(self):
        item = _item("r1", "urn:x", "critical", age_days=10, domain_areas=["Payer"])
        assert item_score(item) == pytest.approx(8.0)

    def test_high_old_item(self):
        # high weight=4, age_factor(180)=2 → score=8
        item = _item("r2", "urn:y", "high", age_days=180, domain_areas=["Payer"])
        assert item_score(item) == pytest.approx(8.0)

    def test_low_young_item(self):
        item = _item("r3", "urn:z", "low", age_days=0, domain_areas=["Payer"])
        assert item_score(item) == pytest.approx(1.0)


# ── domain_drift_score ────────────────────────────────────────────────────────

class TestDomainDriftScore:
    def _items(self):
        return [
            _item("r1", "urn:a", "critical", 10,  ["Payer"]),
            _item("r2", "urn:b", "high",     20,  ["Payer"]),
            _item("r3", "urn:c", "medium",   5,   ["ClaimSubmission"]),
            _item("r4", "urn:d", "low",      200, ["Payer", "ClaimSubmission"]),
        ]

    def test_payer_item_count(self):
        score = domain_drift_score("Payer", self._items())
        # r1, r2, r4 are in Payer
        assert score.item_count == 3

    def test_payer_critical_count(self):
        score = domain_drift_score("Payer", self._items())
        assert score.critical_count == 1

    def test_payer_weighted_score_non_zero(self):
        score = domain_drift_score("Payer", self._items())
        assert score.weighted_score > 0

    def test_claim_submission_item_count(self):
        score = domain_drift_score("ClaimSubmission", self._items())
        # r3, r4 are in ClaimSubmission
        assert score.item_count == 2

    def test_empty_domain_returns_zeros(self):
        score = domain_drift_score("NonExistent", self._items())
        assert score.item_count == 0
        assert score.weighted_score == 0.0
        assert score.critical_count == 0
        assert score.oldest_item_age_days == 0.0

    def test_oldest_item_age_days(self):
        score = domain_drift_score("Payer", self._items())
        assert score.oldest_item_age_days == pytest.approx(200.0, rel=0.01)

    def test_waived_excluded_by_default(self):
        items = self._items()
        # Waive one Payer item.
        items[0] = _item("r1", "urn:a", "critical", 10, ["Payer"], state="waived")
        score = domain_drift_score("Payer", items)
        assert score.item_count == 2  # r2, r4
        assert score.critical_count == 0

    def test_waived_included_when_flag_false(self):
        items = self._items()
        items[0] = _item("r1", "urn:a", "critical", 10, ["Payer"], state="waived")
        score = domain_drift_score("Payer", items, exclude_waived=False)
        assert score.item_count == 3
        assert score.critical_count == 1

    def test_in_flight_remediation_days(self):
        items = [
            _item("r5", "urn:e", "high", 5, ["Payer"], state="in_flight"),
        ]
        items[0].estimated_remediation_days = 5.0
        score = domain_drift_score("Payer", items)
        assert score.in_flight_remediation_days == pytest.approx(5.0)


# ── all_domain_scores ─────────────────────────────────────────────────────────

class TestAllDomainScores:
    def test_sorted_by_weighted_score_descending(self):
        items = [
            _item("r1", "urn:a", "critical", 10, ["Payer"]),          # score = 8
            _item("r2", "urn:b", "low",      5,  ["ClaimSubmission"]),  # score = 1
            _item("r3", "urn:c", "high",     20, ["Auth"]),             # score = 4
        ]
        scores = all_domain_scores(items)
        names = [s.domain for s in scores]
        assert names[0] == "Payer"
        assert names[-1] == "ClaimSubmission"

    def test_items_without_domain_go_to_unattributed(self):
        items = [
            _item("r1", "urn:a", "high", 10, []),   # no domain
        ]
        scores = all_domain_scores(items)
        assert any(s.domain == "unattributed" for s in scores)

    def test_all_domains_covered(self):
        items = [
            _item("r1", "urn:a", "critical", 10, ["Payer"]),
            _item("r2", "urn:b", "high",     20, ["ClaimSubmission"]),
            _item("r3", "urn:c", "medium",   5,  ["Payer", "Auth"]),
        ]
        scores = all_domain_scores(items)
        domains = {s.domain for s in scores}
        assert "Payer" in domains
        assert "ClaimSubmission" in domains
        assert "Auth" in domains

    def test_empty_items_returns_empty(self):
        assert all_domain_scores([]) == []
