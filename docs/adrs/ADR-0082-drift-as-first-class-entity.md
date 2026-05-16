# ADR-0082 — Drift as a First-Class Entity

**Status:** Proposed
**Date:** 2026-05-13
**Builds on:** ADR-0007 (drift-detection-v1), ADR-0050 (call/dependency graph), ADR-0059 (domain inference), ADR-0067 (brain evolution), ADR-0072 (memory primitives), ADR-0073 (event-stream)
**Pairs with:** ADR-0079 (persona templates — VP and CEO surfaces this)
**Strategic goal:** VP Eng's flagship question is "where is reality drifting from architectural intent?" Today drift is a check that runs on demand. Promote it to a persistent, time-series, queryable entity so VP can see trends, allocate capacity to remediation, and tie drift to incidents.

---

## Context

ADR-0007 introduced drift detection as a check: given an ADR specifying "module X should not depend on module Y", verify by running over the current code state and emit a violation if it exists. The check is on-demand and ephemeral.

For VP persona, this is insufficient. VP wants:
- **Trend**: is drift getting worse or better over time?
- **Per-domain attribution**: which domain areas are accumulating drift fastest?
- **Connection to outcomes**: do drift hotspots correlate with incidents / customer escalations / on-call burden?
- **Remediation tracking**: when did we acknowledge a drift; when did we resolve it; what was the fix?
- **Forecasting**: at current accumulation rate, when will drift become unmanageable?

None of those are answerable from a check that runs once and prints violations. They require drift to be a **first-class persistent entity** with snapshots, history, edges to incidents/PRs/decisions, and a lifecycle (detected → acknowledged → in-flight → resolved).

This ADR promotes drift from a check to an entity. It also extends drift beyond ADR-violations to cover schema drift, convention drift, and dependency drift. Cheap to ship (most data exists); high VP-persona unlock.

---

## Decision

Six mechanisms.

---

### M1 — DriftSnapshot Entity Schema

**Problem solved**: drift needs a stable typed shape so it can be stored, queried, edged, and timeline'd.

**Mechanism**:

```python
@dataclass
class DriftItem:
    id: str                              # stable hash of (rule_id, scope_urn, kind)
    rule_id: str                         # links to the ADR / spec / convention being violated
    rule_source: Literal["adr", "spec", "convention", "dependency_policy", "schema_pact"]
    kind: Literal[
        "structural",            # call/dependency edge violates rule
        "schema",                # schema diverges from spec
        "convention",            # naming/style violates inferred convention
        "dependency_policy",     # disallowed import / outdated version
        "data_flow",             # data crosses boundary it shouldn't
        "ownership",             # entity has no clear owner / orphaned
    ]
    scope_urn: str                       # the offending entity URN
    domain_areas: list[str]              # affected domain entities (Payer, etc.)
    severity: Literal["low", "medium", "high", "critical"]
    detected_at: datetime
    last_seen_at: datetime               # bumped each snapshot it persists in
    first_violated_commit: Optional[str] # first commit where the rule started failing
    age_days: float                      # last_seen_at - detected_at
    state: Literal[
        "open",                  # actively in violation
        "acknowledged",          # a human marked "we know"
        "in_flight",             # PR open to fix
        "resolved",              # rule no longer violated
        "waived",                # human said "intentional, suppress"
    ]
    resolution: Optional[ResolutionRecord]
    related_incidents: list[str]         # incident URNs causally linked
    related_prs: list[str]               # PRs that introduced or addressed
    estimated_remediation_days: Optional[float]   # for VP capacity planning

@dataclass
class DriftSnapshot:
    snapshot_id: str
    snapshot_at: datetime
    workspace: str
    items_open: int
    items_acknowledged: int
    items_in_flight: int
    items_by_domain: dict[str, int]     # {Payer: 12, ClaimSubmission: 7}
    items_by_severity: dict[str, int]
    new_since_last: list[str]           # newly-detected drift item IDs
    resolved_since_last: list[str]
    delta_score: float                  # +/- vs previous snapshot
```

A DriftItem is the unit; a DriftSnapshot is the periodic aggregate. Both stored in the brain's entity graph, edged to ADRs, PRs, incidents, and domain entities.

---

### M2 — Snapshot Scheduler

**Problem solved**: drift trend requires regular snapshots, not just on-demand checks.

**Mechanism**: nightly job (configurable cadence per workspace) that:

1. Runs all active drift rules over current code/spec state
2. For each violation, finds-or-creates the corresponding DriftItem
3. Bumps `last_seen_at` for items still violating
4. Marks `resolved` for items no longer violating (with confidence — see Open Questions)
5. Computes the snapshot aggregate
6. Persists to the brain
7. Emits a `DriftSnapshotComputed` event into the ADR-0073 event stream

Cadence is workspace-configurable. Default nightly. High-velocity workspaces may run on every deploy (event-driven via ADR-0073 webhooks).

---

### M3 — Per-Domain Drift Scoring

**Problem solved**: a flat count of drift items is meaningless. VP needs per-domain attribution to allocate capacity.

**Mechanism**: drift items inherit `domain_areas` from their `scope_urn` via ADR-0059 inference. Per-domain score:

```python
def domain_drift_score(domain: str, snapshot: DriftSnapshot) -> DomainDriftScore:
    items = [i for i in snapshot.all_items() if domain in i.domain_areas]
    return DomainDriftScore(
        domain=domain,
        item_count=len(items),
        weighted_score=sum(severity_weight(i.severity) * age_factor(i.age_days) for i in items),
        oldest_item_age_days=max((i.age_days for i in items), default=0),
        critical_count=sum(1 for i in items if i.severity == "critical"),
        in_flight_remediation_days=sum(i.estimated_remediation_days or 0
                                       for i in items if i.state == "in_flight"),
        velocity=resolved_per_week - introduced_per_week,
    )
```

`severity_weight`: critical=8, high=4, medium=2, low=1.
`age_factor`: items older than 90 days get a multiplier — old drift compounds (technical debt interest).

Per-domain scores are the primary VP-facing surface. CEO sees the top-3 by weighted score in the strategic summary.

---

### M4 — Drift Trend Computation

**Problem solved**: VP needs to know "are we getting better or worse?"

**Mechanism**: snapshots are inherently a time series. Compute:

- `drift_velocity`: items introduced per week minus items resolved per week (per domain, org-wide)
- `compound_score_trend`: weighted_score over time, smoothed via 4-week moving average
- `aging_distribution`: histogram of age_days per snapshot — surfaces "we have 30 items >90 days old"
- `domain_velocity_ranking`: which domains are improving vs deteriorating

Trends rendered as small charts in the VP `drift_trend` shape (ADR-0079). Each datapoint clickable → drilldown to the items that contributed.

---

### M5 — Drift-to-Outcome Edges

**Problem solved**: not all drift matters. VP needs to know which drift correlates with operational pain.

**Mechanism**: at incident close (or on-call event close), the brain runs a correlation pass:

1. Identifies the entity area(s) involved in the incident
2. Looks up open DriftItems in those areas at the time of the incident
3. Proposes causal edges: `Incident I-2026-04-15 may be caused by DriftItem D-payer-circular-dep` with confidence based on:
   - Time proximity (drift was open when incident happened)
   - Same scope_urn (drift was in the affected file/module)
   - Engineer hypothesis (postmortem text mentions the drift)

Confidence-scored, never auto-asserted. Surfaced in the VP shape as "drift items linked to N incidents this quarter."

This is one of the highest-value views in the entire brain: **drift tied to dollar pain** (incidents → MTTR → on-call hours → eventually $).

---

### M6 — Resolution Lifecycle Tracking

**Problem solved**: VP needs to see remediation progress, not just open count.

**Mechanism**: every DriftItem has a state machine:

```
open → acknowledged → in_flight → resolved
                  ↓
                waived (terminal, with required justification text)
```

Transitions:
- `acknowledged`: human action (UI button or Slack `/drift ack <id>`)
- `in_flight`: a PR is open touching the scope_urn — auto-detected
- `resolved`: snapshot detects rule no longer violated
- `waived`: human action with required justification recorded

Per-snapshot deltas: `new_since_last`, `resolved_since_last`, `acknowledged_since_last`. These power the VP weekly digest.

Waived items still count in audit but not in active scoring. Re-emerge as active if conditions change (e.g., a waived "ok we'll allow this dependency for 90 days" auto-reactivates after 90 days).

---

## Consequences

**Positive**:
- VP gets a real dashboard, not on-demand reports
- CEO gets a single-number org-health metric (composite drift score with trend)
- Drift-to-incident edges create a $-attribution path (toward CFO eventually)
- Lifecycle tracking creates accountability without blame (state machine, not engineer-shaming)
- Cheap to ship — most data exists

**Negative / risks**:
- Drift detection only as good as the rules. Garbage rules → garbage scores → VP loses trust
- Auto-resolution detection is fragile (a rule can pass for the wrong reason — refactor that hides the violation behind indirection)
- Severity assignment is subjective; need workspace-configurable defaults
- Score becomes a metric → people game the metric (Goodhart). Need to not gamify; surface as informational, not punitive

**Cost estimate**:
- M1 schema + M2 scheduler: 0.5 week
- M3 per-domain scoring: 0.5 week
- M4 trend computation + viz integration: 0.5 week
- M5 drift-to-incident correlation: 0.5 week (depends on incident ingestion from ADR-0070)
- M6 lifecycle: 0.5 week (UI + state machine)

**Total: 1.5-2.5 weeks**. Single engineer; parallel-safe with all other ADRs.

---

## Phasing

**Phase 1 (seed)**: M1, M2, M3, M6 — basic drift entity + nightly snapshots + per-domain scoring + acknowledge/waive. M4 trend after 4 weeks of snapshots.

**Phase 2 (seed → Series A)**: M5 drift-to-incident correlation (requires incident ingestion live). VP weekly digest auto-generated.

**Phase 3 (Series A and after)**: predictive drift forecasting ("at current velocity, Payer area drift becomes unmanageable in 8 weeks"); drift cost estimation in $ (via ADR-0081 cost ingestion); cross-org drift benchmarking (anonymized).

---

## Open questions

1. **What counts as "rule"?** Default sources: ADR allow/deny lists (ADR-0007 format), API contracts (ADR-0008), schema pacts (ADR-0058), inferred conventions (ADR-0063). Workspace can add custom rules via YAML in repo.
2. **Auto-resolution false positives.** How to verify a rule "passing" isn't because someone hid the violation? Mitigation: when snapshot marks resolved, scan recent commits in scope; if a commit added an import suppression / type ignore / structural indirection, downgrade resolution confidence and require human confirmation.
3. **Severity assignment**: who decides? Default: rule declares its own severity. Workspace can override per-rule. CSV-import support for bulk severity adjustment.
4. **Domain attribution for cross-cutting drift.** A drift item may span multiple domains (e.g., a security policy violation affects all entities). Allow `domain_areas: list` (already in schema); show in all relevant per-domain scores but with `cross_cutting=True` flag to avoid double-counting in totals.
5. **What about drift in non-code artifacts?** ADRs themselves can drift from each other (ADR-A says X, ADR-B says not-X). Phase 2 extends drift detection to ADR self-consistency. Useful for compliance/audit personas.
6. **How to ship without the metric becoming a stick?** Default presentation is informational with trend. No leaderboards, no cross-team comparison surfaced by default. Workspace admin can opt into team-level views; off by default.

---

## What this unlocks

- VP `drift_trend`, `debt_hotspots`, `area_health_summary` shapes in ADR-0079 become concrete
- CEO `strategic_risks` shape gets the top-3 drift areas
- CFO eventually gets `debt_dollarized_exposure` (drift × estimated_remediation_days × loaded_engineer_rate)
- Compliance personas (post-Series-A) get audit-grade drift tracking with full lifecycle history

This ADR is the **VP persona's anchor**. Without persistent drift, VP gets snapshots; with it, VP gets trajectory.
