# ADR-0082 P1 — Task Checklist

## Phase 1: Models
- [x] `drift/models.py` — DriftItem, DriftSnapshot, DomainDriftScore, ResolutionRecord dataclasses
- [x] `tests/unit/test_drift_models.py` — serialization, stable ID, field validation

## Phase 2: Persistence
- [x] `drift/store.py` — DriftStore: find_or_create, upsert, list, load snapshot
- [x] Tests for store (file I/O, no duplicates)

## Phase 3: State Machine
- [x] `drift/state_machine.py` — DriftStateMachine, valid transitions, InvalidTransition
- [x] `tests/unit/test_drift_state_machine.py` — all valid + invalid transitions

## Phase 4: Scorer
- [x] `drift/scorer.py` — domain_drift_score(), severity_weight(), age_factor(), all_domain_scores()
- [x] `tests/unit/test_drift_scorer.py` — weights, age factor, per-domain aggregation

## Phase 5: Scheduler
- [x] `drift/scheduler.py` — run_snapshot_now(), nightly cron setup (APScheduler)
- [x] Wire into checks/drift_check.py (existing violations → DriftItems)

## Phase 6: REST API
- [x] `api/routes/drift.py` — 7 endpoints
- [x] Register in `api/main.py`
- [x] Config tunables in `config.py`

## Phase 7: Acceptance Test
- [x] `tests/acceptance/test_drift_e2e.py` — full lifecycle: create item → ack → fix → resolved

## Phase 8: Evidence
- [ ] `specs/03-evidence/snapshot-example.json` — real snapshot output
- [ ] `specs/03-evidence/curl-output.txt` — API endpoint responses
- [ ] `specs/03-evidence/test-run.txt` — pytest output

## Acceptance Criteria
- [ ] Snapshot completes in < 30s
- [ ] DriftItems have stable IDs (no duplicates across runs)
- [ ] All state machine transitions work via API
- [ ] Per-domain scoring returns sensible numbers
- [ ] REST endpoints return correct JSON
- [ ] All existing tests pass
