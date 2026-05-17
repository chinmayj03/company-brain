"""
ADR-0082 P1 — Drift as a First-Class Entity.

Public surface:

    from companybrain.drift import DriftItem, DriftSnapshot, DriftStore
    from companybrain.drift import run_snapshot_now, DriftStateMachine
    from companybrain.drift import domain_drift_score, all_domain_scores
"""
from companybrain.drift.models import (
    DriftItem,
    DriftSnapshot,
    DomainDriftScore,
    ResolutionRecord,
)
from companybrain.drift.store import DriftStore
from companybrain.drift.state_machine import DriftStateMachine, InvalidTransition
from companybrain.drift.scorer import (
    domain_drift_score,
    all_domain_scores,
    severity_weight,
    age_factor,
    item_score,
)
from companybrain.drift.scheduler import run_snapshot_now, start_nightly_scheduler

__all__ = [
    "DriftItem",
    "DriftSnapshot",
    "DomainDriftScore",
    "ResolutionRecord",
    "DriftStore",
    "DriftStateMachine",
    "InvalidTransition",
    "domain_drift_score",
    "all_domain_scores",
    "severity_weight",
    "age_factor",
    "item_score",
    "run_snapshot_now",
    "start_nightly_scheduler",
]
