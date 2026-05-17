"""
ADR-0082 M3 — Per-domain drift scoring.

Severity weights: critical=8, high=4, medium=2, low=1
Age factor: 1 + max(0, (age_days - 90) / 90)  — items older than 90 days compound.

Waived items are excluded from scoring (they are suppressed intentionally).
"""
from __future__ import annotations

from typing import Iterable

from companybrain.drift.models import DomainDriftScore, DriftItem, Severity


# ── Weight / factor tables ────────────────────────────────────────────────────

_SEVERITY_WEIGHTS: dict[Severity, float] = {
    "critical": 8.0,
    "high":     4.0,
    "medium":   2.0,
    "low":      1.0,
}

_AGE_INFLECTION_DAYS: float = 90.0


def severity_weight(severity: Severity) -> float:
    """Return the numeric weight for a severity level."""
    return _SEVERITY_WEIGHTS.get(severity, 1.0)


def age_factor(age_days: float) -> float:
    """
    Items start at a factor of 1.0 and compound once they are older than
    90 days. At 180 days the factor is 2.0; at 270 days it is 3.0, etc.
    """
    return 1.0 + max(0.0, (age_days - _AGE_INFLECTION_DAYS) / _AGE_INFLECTION_DAYS)


def item_score(item: DriftItem) -> float:
    """Weighted score for a single DriftItem."""
    return severity_weight(item.severity) * age_factor(item.age_days)


# ── Per-domain aggregation ────────────────────────────────────────────────────

def domain_drift_score(
    domain: str,
    items: Iterable[DriftItem],
    *,
    exclude_waived: bool = True,
) -> DomainDriftScore:
    """
    Compute the weighted drift score for a single domain.

    ``items`` should be all active DriftItems (e.g. from DriftStore).
    Items with ``state == "waived"`` are excluded by default.
    """
    domain_items = [
        i for i in items
        if domain in i.domain_areas
        and (not exclude_waived or i.state != "waived")
    ]

    weighted = sum(item_score(i) for i in domain_items)
    critical = sum(1 for i in domain_items if i.severity == "critical")
    oldest = max((i.age_days for i in domain_items), default=0.0)
    in_flight_days = sum(
        i.estimated_remediation_days or 0.0
        for i in domain_items
        if i.state == "in_flight"
    )

    return DomainDriftScore(
        domain=domain,
        item_count=len(domain_items),
        weighted_score=weighted,
        critical_count=critical,
        oldest_item_age_days=oldest,
        in_flight_remediation_days=in_flight_days,
    )


def all_domain_scores(
    items: Iterable[DriftItem],
    *,
    exclude_waived: bool = True,
) -> list[DomainDriftScore]:
    """
    Compute per-domain scores for every domain that appears in the item set.

    Returns list sorted by weighted_score descending (highest drift first).
    Items with no domain_areas are attributed to "unattributed".
    """
    item_list = list(items)

    # Collect every domain mentioned, mapping un-attributed items to sentinel.
    domains: set[str] = set()
    for it in item_list:
        if it.domain_areas:
            domains.update(it.domain_areas)
        else:
            domains.add("unattributed")

    # Normalise: items without domain_areas appear under "unattributed"
    normalised: list[DriftItem] = []
    for it in item_list:
        if not it.domain_areas:
            from dataclasses import replace
            it = replace(it, domain_areas=["unattributed"])
        normalised.append(it)

    scores = [
        domain_drift_score(d, normalised, exclude_waived=exclude_waived)
        for d in domains
    ]
    return sorted(scores, key=lambda s: s.weighted_score, reverse=True)
