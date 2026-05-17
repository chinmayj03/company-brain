"""
ADR-0007 / ADR-0082 — Drift check runner.

This module is the bridge between the ADR-0007 static drift detection
(DriftSignal nodes in the brain graph) and the ADR-0082 persistent
DriftItem entities.

``run_drift_checks(brain_root)`` is called by the snapshot scheduler
(drift/scheduler.py) on each nightly tick. It reads DriftSignal nodes
from the .brain/ JSON store, converts them to DriftItem violation dicts,
and returns them for the scheduler to upsert into the DriftStore.

The check itself is deterministic (pure .brain/ read + structural compare).
It does NOT call any LLM — it is a post-extraction static pass.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import structlog

log = structlog.get_logger(__name__)


def _brain_root_path(brain_root: Path | None = None) -> Path:
    if brain_root is not None:
        return Path(brain_root)
    configured = (
        os.environ.get("BRAIN_DEMO_REPO_PATH")
        or os.environ.get("TARGET_REPO")
        or os.environ.get("BRAIN_REPO_PATH")
        or "/tmp/brain-drift-default"
    )
    return Path(configured) / ".brain"


def _load_drift_signals(brain_root: Path) -> list[dict[str, Any]]:
    """
    Read all DriftSignal entities from the .brain/ JSON store.

    DriftSignal files live under .brain/drift_signal/ (written by the
    ADR-0007 extraction pass). Each file is a JSON object with at minimum:
        id, severity, description, implementation_urn, contract_urn, detected_fields
    """
    signals_dir = brain_root / "drift_signal"
    if not signals_dir.exists():
        log.debug("drift_check.no_signals_dir", path=str(signals_dir))
        return []

    signals: list[dict[str, Any]] = []
    for path in sorted(signals_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text())
            if data.get("id"):
                signals.append(data)
        except Exception as exc:
            log.warning("drift_check.signal_parse_error", path=str(path), error=str(exc))
    log.debug("drift_check.loaded_signals", count=len(signals))
    return signals


def _severity_from_signal(signal_severity: str) -> str:
    """Map ADR-0007 signal severity → ADR-0082 DriftItem severity."""
    mapping = {
        "breaking": "critical",
        "warning":  "high",
        "info":     "low",
    }
    return mapping.get(signal_severity, "medium")


def _infer_domain_areas(scope_urn: str, brain_root: Path) -> list[str]:
    """
    Attempt to infer domain areas from a scope URN via the .brain/ index.

    Looks for the entity in the brain index and extracts any domain_areas
    or lob (line of business) attributes. Falls back to empty list.
    """
    index_path = brain_root / "index.json"
    if not index_path.exists():
        return []
    try:
        index = json.loads(index_path.read_text())
        rel_path = index.get(scope_urn)
        if rel_path:
            entity_path = brain_root / rel_path
            if entity_path.exists():
                entity = json.loads(entity_path.read_text())
                meta = entity.get("metadata") or {}
                # Try several known field names for domain attribution
                areas = (
                    entity.get("domain_areas")
                    or meta.get("domain_areas")
                    or meta.get("lob_context")
                    or []
                )
                if isinstance(areas, str):
                    areas = [areas]
                if areas:
                    return areas
    except Exception as exc:
        log.debug("drift_check.domain_infer_failed", urn=scope_urn, error=str(exc))
    return []


def run_drift_checks(brain_root: Path | None = None) -> list[dict[str, Any]]:
    """
    Run all drift checks against the current .brain/ state.

    Returns a list of violation dicts with the fields expected by the
    snapshot scheduler's find-or-create logic:

        rule_id, rule_source, kind, scope_urn, domain_areas,
        severity, description

    Each dict corresponds to one DriftItem that is currently violating.
    The scheduler handles stable IDs, timestamps, and state transitions.
    """
    root = _brain_root_path(brain_root)
    signals = _load_drift_signals(root)

    violations: list[dict[str, Any]] = []
    for signal in signals:
        raw_severity = signal.get("severity", "warning")
        scope_urn = (
            signal.get("implementation_urn")
            or signal.get("scope_urn")
            or signal.get("id", "unknown")
        )
        violation = {
            "rule_id": signal.get("id", "adr-0007-unknown"),
            "rule_source": "adr",
            "kind": "schema",           # ADR-0007 detects schema/contract drift
            "scope_urn": scope_urn,
            "domain_areas": _infer_domain_areas(scope_urn, root),
            "severity": _severity_from_signal(raw_severity),
            "description": signal.get("description", ""),
            "detected_fields": signal.get("detected_fields", []),
        }
        violations.append(violation)

    log.info("drift_check.run_complete", violations_found=len(violations))
    return violations


def get_active_violation_ids(brain_root: Path | None = None) -> set[str]:
    """
    Return the set of stable DriftItem IDs that are currently in violation.

    Used by the snapshot scheduler to determine which existing items have
    been resolved (they were previously violating but are no longer in this set).
    """
    from companybrain.drift.models import DriftItem

    root = _brain_root_path(brain_root)
    violations = run_drift_checks(root)
    return {
        DriftItem.make_id(v["rule_id"], v["scope_urn"], v["kind"])
        for v in violations
    }
