"""
ADR-0059 Pass T1 follow-on — RiskAlert heuristics.

Reads each entity's ``TemporalOwnership`` (populated by ``temporal_pass.py``)
and emits ``RiskAlert`` rows for the three product-defined risk surfaces:

  - ``bus_factor_one``   single-point-of-failure
  - ``high_churn``       instability / active redesign
  - ``stale_owner_left`` knowledge-departure risk

Thresholds come from settings with ADR-spec defaults; callers can tune them
per workspace via ``settings.risk_alert_*`` overrides.

The detector is a pure function — no LLM calls, no I/O beyond an optional
``author_last_seen_lookup`` for the stale-owner check (passed by the caller
so the unit tests can stub it).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Callable, Iterable, Optional

import structlog

from companybrain.models.entities import (
    EDGE_AFFECTS,
    ExtractedEntity,
    ExtractedRelationship,
    RiskAlert,
)

log = structlog.get_logger(__name__)

# Defaults per ADR-0059 §Pass T1 heuristics.
_DEFAULT_BUS_FACTOR_PRIMARY_SHARE = 0.70   # primary author > 70% of lines
_DEFAULT_BUS_FACTOR_RUNNER_SHARE  = 0.10   # runner-up has < 10%
_DEFAULT_CHURN_30D_THRESHOLD      = 5      # churn_30d > 5 commits
_DEFAULT_STALE_OWNER_DAYS         = 90     # last_touched_by silent >= 90d


AuthorLastSeenLookup = Callable[[str], Optional[datetime]]
"""``lookup(author_email_or_name) -> datetime | None``. Returns the timestamp
of the author's most recent commit anywhere in the repo, or ``None`` if the
author has no recent commits."""


def detect_risk_alerts(
    entities: Iterable[ExtractedEntity],
    *,
    author_last_seen_lookup: Optional[AuthorLastSeenLookup] = None,
    now: Optional[datetime] = None,
    bus_factor_primary_share: float = _DEFAULT_BUS_FACTOR_PRIMARY_SHARE,
    bus_factor_runner_share:  float = _DEFAULT_BUS_FACTOR_RUNNER_SHARE,
    churn_30d_threshold:      int   = _DEFAULT_CHURN_30D_THRESHOLD,
    stale_owner_days:         int   = _DEFAULT_STALE_OWNER_DAYS,
) -> tuple[list[RiskAlert], list[ExtractedRelationship]]:
    """Walk ``entities`` and emit a RiskAlert for each entity that trips one
    of the three heuristics. Returns ``(alerts, edges)``: edges are AFFECTS
    relationships from the alert back to the entity it warns about, so the
    graph layer can wire them as first-class connections.
    """
    now = now or datetime.now(tz=timezone.utc)
    stale_cutoff = now - timedelta(days=stale_owner_days)

    alerts: list[RiskAlert] = []
    edges: list[ExtractedRelationship] = []

    for entity in entities:
        t = entity.temporal
        if t is None:
            continue

        # ── bus_factor_one ────────────────────────────────────────────────
        total_lines = sum(c for _, c in t.co_authors) or 0
        if total_lines > 0 and len(t.co_authors) >= 1:
            primary_share = t.co_authors[0][1] / total_lines
            runner_share = (
                t.co_authors[1][1] / total_lines if len(t.co_authors) > 1 else 0.0
            )
            if (primary_share > bus_factor_primary_share
                    and runner_share < bus_factor_runner_share):
                pct = int(round(primary_share * 100))
                msg = (
                    f"{t.primary_author} owns {pct}% of this; "
                    f"no one else has more than "
                    f"{int(round(runner_share * 100))}%."
                )
                stale_owner_dt: Optional[datetime] = None
                if author_last_seen_lookup is not None:
                    try:
                        stale_owner_dt = author_last_seen_lookup(t.primary_author)
                    except Exception as exc:
                        log.debug(
                            "risk_alert_detector.author_lookup failed",
                            author=t.primary_author, error=str(exc),
                        )
                if stale_owner_dt is not None and stale_owner_dt < stale_cutoff:
                    days = (now - stale_owner_dt).days
                    msg = (
                        f"{t.primary_author} owns {pct}% of this; "
                        f"{t.primary_author} hasn't committed in {days} days."
                    )
                    severity: str = "HIGH"
                elif primary_share >= 0.85:
                    severity = "HIGH"
                else:
                    severity = "MED"
                alert = RiskAlert(
                    kind="bus_factor_one",
                    affected_entity_urn=entity.external_id,
                    severity=severity,  # type: ignore[arg-type]
                    message=msg,
                )
                alerts.append(alert)
                edges.append(_edge(alert, entity))

        # ── high_churn ────────────────────────────────────────────────────
        if t.churn_30d > churn_30d_threshold:
            severity = "HIGH" if t.churn_30d >= churn_30d_threshold * 2 else "MED"
            alert = RiskAlert(
                kind="high_churn",
                affected_entity_urn=entity.external_id,
                severity=severity,  # type: ignore[arg-type]
                message=(
                    f"{entity.name} has churned {t.churn_30d} times in the "
                    f"last 30 days — likely unstable or actively being redesigned."
                ),
            )
            alerts.append(alert)
            edges.append(_edge(alert, entity))

        # ── stale_owner_left ──────────────────────────────────────────────
        if t.last_touched_by and author_last_seen_lookup is not None:
            try:
                last_seen = author_last_seen_lookup(t.last_touched_by)
            except Exception as exc:
                log.debug(
                    "risk_alert_detector.last_touched_lookup failed",
                    author=t.last_touched_by, error=str(exc),
                )
                last_seen = None
            if last_seen is not None and last_seen < stale_cutoff:
                days = (now - last_seen).days
                alert = RiskAlert(
                    kind="stale_owner_left",
                    affected_entity_urn=entity.external_id,
                    severity="MED",
                    message=(
                        f"Last toucher {t.last_touched_by} hasn't committed "
                        f"anywhere in the repo in {days} days — knowledge "
                        f"departure risk."
                    ),
                )
                alerts.append(alert)
                edges.append(_edge(alert, entity))

    log.info(
        "risk_alert_detector.detect_risk_alerts complete",
        alerts=len(alerts),
        by_kind={
            "bus_factor_one":   sum(1 for a in alerts if a.kind == "bus_factor_one"),
            "high_churn":       sum(1 for a in alerts if a.kind == "high_churn"),
            "stale_owner_left": sum(1 for a in alerts if a.kind == "stale_owner_left"),
        },
    )
    return alerts, edges


def project_risk_alerts(alerts: Iterable[RiskAlert]) -> list[ExtractedEntity]:
    """Project RiskAlert rows into ExtractedEntity projections, so Stage 5's
    ``_to_brain_entity`` writer treats them like every other entity. Mirrors
    ``project_cross_file_entities`` in cross_file_pass.py."""
    out: list[ExtractedEntity] = []
    for a in alerts:
        out.append(ExtractedEntity(
            entity_type="RiskAlert",
            name=f"{a.kind}::{a.affected_entity_urn}",
            file=f"_alerts/{a.kind}/{a.affected_entity_urn}",
            repo="_alerts",
            signature=a.message[:200],
            last_modified_commit="",
            confidence=1.0,
            code_snippet=a.message,
        ))
    return out


def _edge(alert: RiskAlert, entity: ExtractedEntity) -> ExtractedRelationship:
    return ExtractedRelationship(
        from_entity=alert.external_id,
        from_type="RiskAlert",
        edge_type=EDGE_AFFECTS,
        to_entity=entity.external_id,
        to_type=entity.entity_type,
        confidence=1.0,
        evidence=alert.message,
    )
