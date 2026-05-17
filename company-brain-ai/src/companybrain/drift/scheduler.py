"""
ADR-0082 M2 — DriftSnapshot scheduler.

Two modes:
  1. Nightly cron via APScheduler (started by the FastAPI lifespan hook).
  2. On-demand CLI: `companybrain drift snapshot --now` (calls run_snapshot_now directly).

The snapshot algorithm:
  1. Run all active drift checks (checks/drift_check.py)
  2. For each violation, find-or-create the DriftItem (stable ID)
  3. Bump last_seen_at for items still violating
  4. Mark resolved (auto) for items no longer violating
  5. Check waived items for expiry → reopen
  6. Compute DriftSnapshot aggregate
  7. Persist snapshot
  8. Return SnapshotResult summary
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import structlog

from companybrain.drift.models import DriftItem, DriftSnapshot
from companybrain.drift.scorer import all_domain_scores, item_score
from companybrain.drift.state_machine import DriftStateMachine
from companybrain.drift.store import DriftStore

log = structlog.get_logger(__name__)

# Sentinel — replaced by tests / CLI to inject a different store.
_store_factory = DriftStore


# ── Public API ────────────────────────────────────────────────────────────────

class SnapshotResult:
    """Summary returned by run_snapshot_now()."""

    def __init__(
        self,
        snapshot: DriftSnapshot,
        created_count: int,
        updated_count: int,
        resolved_count: int,
        elapsed_seconds: float,
    ):
        self.snapshot = snapshot
        self.created_count = created_count
        self.updated_count = updated_count
        self.resolved_count = resolved_count
        self.elapsed_seconds = elapsed_seconds

    def __repr__(self) -> str:
        return (
            f"<SnapshotResult id={self.snapshot.snapshot_id!r} "
            f"open={self.snapshot.items_open} "
            f"new={self.created_count} resolved={self.resolved_count} "
            f"elapsed={self.elapsed_seconds:.1f}s>"
        )

    def to_dict(self) -> dict:
        return {
            "snapshot_id": self.snapshot.snapshot_id,
            "snapshot_at": self.snapshot.snapshot_at.isoformat(),
            "items_open": self.snapshot.items_open,
            "items_acknowledged": self.snapshot.items_acknowledged,
            "items_in_flight": self.snapshot.items_in_flight,
            "new_items": self.created_count,
            "resolved_items": self.resolved_count,
            "elapsed_seconds": round(self.elapsed_seconds, 2),
        }


def run_snapshot_now(
    brain_root: Optional[Path] = None,
    workspace: str = "default",
) -> SnapshotResult:
    """
    Run a full drift snapshot synchronously and return a SnapshotResult.

    Safe to call from an asyncio executor, CLI, or test code.
    """
    import time
    t0 = time.monotonic()
    now = datetime.now(timezone.utc)

    store = _store_factory(brain_root)

    # 1. Run all drift checks.
    from companybrain.checks.drift_check import run_drift_checks, get_active_violation_ids
    violations = run_drift_checks(brain_root)
    active_ids = {
        DriftItem.make_id(v["rule_id"], v["scope_urn"], v["kind"])
        for v in violations
    }

    # 2. Load previous snapshot for delta computation.
    prev_snapshot = store.load_latest_snapshot()
    prev_open_ids: set[str] = set(prev_snapshot.all_item_ids) if prev_snapshot else set()

    # 3. Find-or-create DriftItems for current violations.
    created_ids: list[str] = []
    updated_ids: list[str] = []

    for v in violations:
        rule_id   = v["rule_id"]
        scope_urn = v["scope_urn"]
        kind      = v["kind"]

        item, created = store.find_or_create_item(
            rule_id=rule_id,
            scope_urn=scope_urn,
            kind=kind,
            defaults={
                "rule_source": v.get("rule_source", "adr"),
                "domain_areas": v.get("domain_areas", []),
                "severity": v.get("severity", "medium"),
                "detected_at": now,
                "last_seen_at": now,
                "age_days": 0.0,
                "state": "open",
                "description": v.get("description", ""),
            },
        )

        if created:
            created_ids.append(item.id)
        else:
            # Bump last_seen_at for still-violating items.
            item.last_seen_at = now
            item.refresh_age(now)

            # Auto-promote to in_flight if related_prs is non-empty and state is open/ack.
            # (PR detection is best-effort in P1; actual PR linking deferred to P2.)
            if item.state in ("open", "acknowledged") and item.related_prs:
                sm = DriftStateMachine(item)
                if DriftStateMachine.can_transition(item.state, "in_flight"):
                    sm.start_flight()

            store.upsert_item(item)
            updated_ids.append(item.id)

    # 4. Auto-resolve items that are no longer violating.
    resolved_ids: list[str] = []
    all_items = store.load_all_items()
    for item in all_items:
        if item.id not in active_ids and item.state in ("open", "acknowledged", "in_flight"):
            sm = DriftStateMachine(item)
            sm.resolve(auto=True)
            store.upsert_item(item)
            resolved_ids.append(item.id)

    # 5. Check waived items for expiry.
    for item in all_items:
        if item.state == "waived" and item.waive_expires_at:
            if item.waive_expires_at <= now:
                # Only re-open if still violating.
                if item.id in active_ids:
                    sm = DriftStateMachine(item)
                    sm.reopen(reason="Waive period expired")
                    store.upsert_item(item)
                    log.info("drift.scheduler.waive_expired", item_id=item.id)

    # 6. Compute snapshot aggregate.
    all_items = store.load_all_items()  # reload after mutations
    active_items = [i for i in all_items if i.state not in ("resolved", "waived")]
    all_item_ids = [i.id for i in all_items]

    by_domain: dict[str, int] = {}
    for it in active_items:
        for domain in (it.domain_areas or ["unattributed"]):
            by_domain[domain] = by_domain.get(domain, 0) + 1

    by_severity: dict[str, int] = {}
    for it in active_items:
        by_severity[it.severity] = by_severity.get(it.severity, 0) + 1

    items_open         = sum(1 for i in active_items if i.state == "open")
    items_acknowledged = sum(1 for i in active_items if i.state == "acknowledged")
    items_in_flight    = sum(1 for i in active_items if i.state == "in_flight")

    # Delta score vs previous snapshot.
    current_score = sum(item_score(i) for i in active_items)
    if prev_snapshot:
        prev_active = [i for i in all_items if i.id in prev_open_ids]
        prev_score = sum(item_score(i) for i in prev_active)
    else:
        prev_score = 0.0
    delta_score = current_score - prev_score

    snapshot_id = str(uuid.uuid4())
    snapshot = DriftSnapshot(
        snapshot_id=snapshot_id,
        snapshot_at=now,
        workspace=workspace,
        items_open=items_open,
        items_acknowledged=items_acknowledged,
        items_in_flight=items_in_flight,
        items_by_domain=by_domain,
        items_by_severity=by_severity,
        new_since_last=created_ids,
        resolved_since_last=resolved_ids,
        delta_score=round(delta_score, 3),
        all_item_ids=all_item_ids,
    )

    # 7. Persist.
    store.save_snapshot(snapshot)

    elapsed = time.monotonic() - t0
    result = SnapshotResult(
        snapshot=snapshot,
        created_count=len(created_ids),
        updated_count=len(updated_ids),
        resolved_count=len(resolved_ids),
        elapsed_seconds=elapsed,
    )
    log.info(
        "drift.scheduler.snapshot_complete",
        snapshot_id=snapshot_id,
        elapsed_s=round(elapsed, 2),
        new=len(created_ids),
        resolved=len(resolved_ids),
        open=items_open,
    )
    return result


# ── APScheduler integration ───────────────────────────────────────────────────

_scheduler_started = False


def start_nightly_scheduler(
    cron_expression: str = "0 2 * * *",
    brain_root: Optional[Path] = None,
    workspace: str = "default",
) -> None:
    """
    Register a nightly cron job with APScheduler.

    Safe to call multiple times — idempotent (checks ``_scheduler_started``).
    The cron format is standard five-field (minute hour dom month dow).
    Default: 02:00 UTC daily (``"0 2 * * *"``).

    If APScheduler is not installed, logs a warning and returns — the service
    still works via on-demand ``run_snapshot_now()``.
    """
    global _scheduler_started
    if _scheduler_started:
        return

    try:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler  # type: ignore[import-not-found]
        from apscheduler.triggers.cron import CronTrigger  # type: ignore[import-not-found]
    except ImportError:
        log.warning(
            "drift.scheduler.apscheduler_missing",
            msg="APScheduler not installed; nightly drift snapshot disabled. "
                "Use 'companybrain drift snapshot --now' for on-demand runs.",
        )
        return

    # Parse cron expression (5 fields: min hour dom month dow)
    parts = cron_expression.split()
    if len(parts) != 5:
        log.error("drift.scheduler.invalid_cron", expr=cron_expression)
        return

    minute, hour, day, month, day_of_week = parts
    trigger = CronTrigger(
        minute=minute,
        hour=hour,
        day=day,
        month=month,
        day_of_week=day_of_week,
        timezone="UTC",
    )

    sched = AsyncIOScheduler()
    sched.add_job(
        _nightly_job,
        trigger=trigger,
        id="drift_nightly_snapshot",
        replace_existing=True,
        kwargs={"brain_root": brain_root, "workspace": workspace},
    )
    sched.start()
    _scheduler_started = True
    log.info("drift.scheduler.started", cron=cron_expression)


async def _nightly_job(brain_root: Optional[Path], workspace: str) -> None:
    """Async wrapper for the nightly APScheduler job."""
    import asyncio
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None, lambda: run_snapshot_now(brain_root=brain_root, workspace=workspace)
    )
    log.info(
        "drift.scheduler.nightly_complete",
        snapshot_id=result.snapshot.snapshot_id,
        elapsed_s=result.elapsed_seconds,
    )
