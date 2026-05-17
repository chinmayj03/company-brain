# ADR-0082 P1 — Drift as a First-Class Entity: Implementation Plan

**Branch:** feature/adr-0082-p1-drift-entity  
**Target PR:** release/v2-seed-window  
**Budget:** $20 / 6-8 hours  
**Date:** 2026-05-17

---

## Goal

Promote drift from an on-demand ADR-0007 check to a **persistent typed entity** with:
- `DriftItem` + `DriftSnapshot` schemas (M1)
- Nightly snapshot scheduler with CLI on-demand trigger (M2)
- Per-domain drift scoring with severity×age weighting (M3)
- Lifecycle state machine: open → acknowledged → in_flight → resolved/waived (M6)
- REST surface (7 endpoints) consumed by the Drift Dashboard

## Architecture

```
companybrain/drift/
  __init__.py         — public surface (DriftItem, DriftSnapshot, DriftStore, run_snapshot)
  models.py           — dataclasses (DriftItem, DriftSnapshot, DomainDriftScore, ResolutionRecord)
  scorer.py           — domain_drift_score(), severity_weight(), age_factor()
  state_machine.py    — DriftStateMachine.transition(), valid transitions
  store.py            — DriftStore: JSON file persistence in .brain/drift/
  scheduler.py        — APScheduler nightly job + run_snapshot_now()

companybrain/api/routes/drift.py   — FastAPI router, 7 endpoints

Append-only:
  companybrain/api/main.py         — include drift router
  companybrain/config.py           — DRIFT_SNAPSHOT_CRON, severity thresholds
  companybrain/checks/drift_check.py — called by snapshot scheduler
```

## Persistence Strategy

Snapshots: append-only JSON files in `<brain_root>/drift/snapshots/<snapshot_id>.json`  
Items: upsert-by-stable-id in `<brain_root>/drift/items/<item_id>.json`  
No DB migration required — file-based like the rest of `.brain/`.

## ID Stability

`DriftItem.id = sha256(rule_id + ":" + scope_urn + ":" + kind)[:16]`  
Same violation always produces the same ID across runs.

## Severity Weights & Age Factor

- critical=8, high=4, medium=2, low=1  
- age_factor(d) = 1 + max(0, (d - 90) / 90)  — items >90 days compound

## Key Design Decisions

1. File-based persistence (consistent with .brain/ convention; no migration needed)
2. `DriftItem` is find-or-create: first snapshot creates it, subsequent ones bump `last_seen_at`
3. Items resolved if rule no longer violating — `ResolutionRecord.auto_resolved=True`
4. Waived items excluded from active scoring but preserved for audit
5. State machine enforces valid transitions (raises `InvalidTransition` for illegal moves)
6. Existing `/brain/drift/snapshot/latest` mock endpoint is superseded by the new real endpoint
