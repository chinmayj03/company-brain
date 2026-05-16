# ADR-0080 — Estimation & Velocity Model

**Status:** Proposed
**Date:** 2026-05-13
**Builds on:** ADR-0050 (call/dependency graph), ADR-0059 (domain inference), ADR-0066 (experiential memory), ADR-0067 (brain evolution), ADR-0070 (PRD ingestion), ADR-0073 (event-stream memory)
**Pairs with:** ADR-0079 (persona templates — surfaces this model)
**Strategic goal:** PM persona's flagship question is "can we ship this in N days?" Today the brain has all the data needed but no model to answer it. This ADR builds the model.

---

## Context

PM persona's most important question is the estimate question. The brain already has:
- Every PRD timestamp (ADR-0070)
- Every PR open/merge/close timestamp (ADR-0073 M1)
- Every deploy timestamp (ADR-0073 M1)
- Domain entity inference per code module (ADR-0059)
- Call/dependency graph (ADR-0050 P3)

What's missing is the model that turns those events into `estimate(feature, team, deadline) → P(ship_on_time)`.

The naive answer ("average PRD-to-deploy time was 12 days last quarter") is useless because it ignores:
- Feature complexity varies wildly
- Team velocity varies wildly by domain area (the Payer-integration team ships differently from the UI team)
- Current load matters (a team already shipping 3 features will not ship a 4th in 4 days)
- Novelty matters (the first time a team touches an entity area, it takes 3× longer)
- Confidence band matters more than point estimate (P(ship_on_time)=0.45 means something different from a single number)

This ADR designs a model that combines historical velocity, complexity, current load, and novelty into a probability distribution. It is **not** a magic-AI-estimator; it's a transparent statistical model with visible inputs that PMs can challenge.

---

## Decision

Five mechanisms.

---

### M1 — Historical Velocity Index

**Problem solved**: brain needs to know how long similar features took historically, per entity area, per team.

**Mechanism**: a materialized view derived from ADR-0073 event stream:

```python
@dataclass
class VelocityRecord:
    feature_urn: str
    entity_areas_touched: list[str]      # e.g., ["Payer", "ClaimSubmission"]
    primary_team: str                    # inferred from PR authors
    contributing_teams: list[str]
    started_at: datetime                 # earliest of: PRD created, first PR opened
    deployed_at: Optional[datetime]      # first deploy that includes all relevant PRs
    canceled_at: Optional[datetime]      # if feature was abandoned
    duration_days: Optional[float]       # business days
    complexity_score: float              # from M2
    novelty_score: float                 # was this team's first time touching these areas?
    actual_vs_initial_estimate_ratio: Optional[float]
```

Index is rebuilt incrementally on every deploy event. Queryable by:
- `entity_area` (returns distribution of duration_days for features touching that area)
- `team` (returns distribution for that team)
- `(entity_area, team)` (joint distribution — usually small sample size; fall back to wider distributions)
- `complexity_bucket` (filter by complexity_score range)

Sparse-data fallback: when a specific `(entity_area, team)` has < 5 historical features, widen to entity_area-only, then team-only, then org-wide. Each fallback step recorded so the answer can say "based on 12 features in the Payer area, of which only 2 were by this team."

---

### M2 — Feature Complexity Scorer

**Problem solved**: not all features are equal. Need a complexity score that's predictive of duration.

**Mechanism**: compute a score from observable signals **at PRD time** (not retrospectively).

```python
def complexity_score(feature_prd: PRDEntity) -> ComplexityBreakdown:
    affected_entities = infer_affected_entities(feature_prd)
    return ComplexityBreakdown(
        entity_count = len(affected_entities),
        cross_entity_edge_count = count_edges_between(affected_entities),
        test_surface = sum(test_files_touching_entity(e) for e in affected_entities),
        external_integration_count = count_external_integrations(affected_entities),
        novelty = team_novelty_in_entity_areas(team, affected_entities),
        prd_token_count = len(feature_prd.text.split()),     # weak signal
        ambiguity_score = llm_ambiguity_judge(feature_prd),  # 0-1; PRD clarity
        composite = weighted_sum_calibrated_to_history()
    )
```

The composite score is calibrated by regression against historical `duration_days` (M1 data). Weights re-fit weekly (ADR-0067 background process).

Score is **explainable**: PM sees `composite = 7.4 (high)` AND the breakdown (entity_count: 4, novelty: 0.8, etc.). PM can challenge any line.

---

### M3 — Estimation Query

**Problem solved**: given a proposed feature + team + deadline, return P(ship_on_time) with a confidence band.

**Mechanism**: query interface:

```python
def estimate(
    feature_description: str | PRDEntity,
    team: str,
    deadline_days: int,
    today: datetime = now(),
) -> EstimateResult:
    affected = infer_affected_entities(feature_description)
    complexity = complexity_score(feature_description)
    similar = velocity_index.lookup(
        entity_areas=affected,
        team=team,
        complexity_bucket=complexity.bucket,
    )
    current_load = team_load_index.lookup(team, today)

    duration_distribution = combine(
        historical=similar.duration_distribution,
        complexity_adjustment=complexity.composite,
        load_adjustment=current_load.factor,
        novelty_adjustment=complexity.novelty,
    )

    return EstimateResult(
        p_ship_on_time=duration_distribution.cdf(deadline_days),
        p10_days=duration_distribution.quantile(0.10),
        p50_days=duration_distribution.quantile(0.50),
        p90_days=duration_distribution.quantile(0.90),
        similar_past_features=similar.top_n(5),
        complexity_breakdown=complexity,
        load_breakdown=current_load,
        confidence_label=confidence_band(similar.sample_size),
        risks=identify_risks(affected, team, current_load),
    )
```

Output is consumed by `pm.feature_blast_radius_for_estimate` shape (ADR-0079 M6).

---

### M4 — Confidence Bands & Sparse-Data Fallbacks

**Problem solved**: a model with no historical data should not return a confident number.

**Mechanism**: every estimate carries a confidence label:

| Label | Threshold | What it means |
|---|---|---|
| `high_confidence` | ≥ 15 similar (entity_area, team) features | "Based on 23 similar features by this team in this area" |
| `medium_confidence` | 5-14 similar features (after entity_area widening) | "Based on 8 features in the area; this team has done 2 of them" |
| `low_confidence` | 1-4 features | "Based on only 3 features; estimate is highly uncertain" |
| `cold_start` | 0 features after all fallbacks | "No historical data; using org-wide median. Estimate is essentially a guess." |

The shape's answer format (ADR-0079 M4) renders the confidence label prominently — never as fine print. A PM should see `cold_start` and know not to commit on the model alone.

---

### M5 — Calibration Loop

**Problem solved**: model drifts. Without calibration, today's good estimates become tomorrow's bad ones.

**Mechanism**: every shipped feature is a labeled training example. ADR-0067 background process:

1. Compares actual `duration_days` to the original estimate (if one was recorded at PRD time)
2. Updates the regression weights for M2 complexity scoring
3. Flags estimates that were >2× off (over- or under-estimate) for review
4. Recomputes calibration metrics weekly: mean absolute error, P50 coverage, P90 coverage

If P90 coverage drops below 80% (i.e., features are blowing past P90 more than 20% of the time), surface a model-quality alert.

The calibration loop also writes back into ADR-0066 experiential memory as `EstimationTrajectory` records.

---

## Consequences

**Positive**:
- PM persona gets a concrete answer to its flagship question, with explainable breakdown
- Confidence bands prevent over-trust in cold-start cases
- Calibration loop ensures the model doesn't rot
- Same data also powers VP `velocity_per_team` and `estimate_vs_actual` shapes
- Generalizes beyond code: once cross-source connectors land (ADR-0070), can estimate cross-functional initiatives (PRD → marketing collateral → sales enablement → launch) not just code shipping

**Negative / risks**:
- Cold-start is a real problem for new customers. First quarter of usage will have low-confidence estimates everywhere.
- Team-attribution from PR authors is imperfect (some companies don't tag teams; some engineers float)
- Feature-attribution from PR-to-PRD is fragile (depends on consistent PR labeling or LLM-inferred matching)
- Underlying assumption that "past velocity predicts future velocity" breaks when team composition changes drastically (reorgs)

**Cost estimate**:
- M1 velocity index: 0.5 week (mostly schema + materialization from existing events)
- M2 complexity scorer: 1 week (the regression-fit loop + the LLM ambiguity judge)
- M3 estimation query: 0.5 week
- M4 confidence labeling: 0.5 week
- M5 calibration: 0.5 week

**Total: 2.5-3 weeks**. Single engineer; parallel-safe with ADR-0079/0082/0083/0084.

---

## Phasing

**Phase 1 (seed → Series A)**: M1, M2, M3 with rule-based complexity (no LLM ambiguity judge yet), M4 confidence labels, M5 manual calibration weekly.

**Phase 2 (Series A)**: LLM ambiguity judge, automated calibration, P&L visualization of estimate-vs-actual for VP/CFO consumption.

**Phase 3 (post Series A)**: cross-functional estimate (not just code — include design, GTM, sales enablement time), per-engineer-skill velocity bands (research-grade, sensitive — opt-in only).

---

## Open questions

1. **Team identity**: how to resolve "team" when companies don't tag PRs? Default: infer from PR-author clusters via graph community detection on co-authorship. Allow workspace config to override.
2. **What counts as "feature"?** A PRD entity (ADR-0070) by default. For workspaces without PRDs, fall back to PR-cluster heuristic (PRs sharing a feature-branch prefix or label).
3. **How do reorgs invalidate the model?** Detect via team-composition diff over time; downweight historical data older than the last major composition change. Surface to PMs as "reorg recently; historical velocity may not apply."
4. **External dependencies?** Many features are blocked on external integrations or vendor work. M3's `risks` should flag features whose affected entities have known external dependencies (e.g., "this touches Aetna integration; last 3 Aetna features had vendor-side delays").
5. **Estimate publication**: should the model output be auto-posted to the PRD / Linear ticket? Default no; opt-in per workspace. Auto-posting creates social pressure on the model output that distorts calibration.

---

## What this unlocks

- `pm.feature_blast_radius_for_estimate` shape in ADR-0079 becomes concrete
- VP `velocity_per_team` and `estimate_vs_actual` shapes get populated
- Feeds into ADR-0082 (drift): velocity drift is one signal of architectural drift
- Feeds into CFO eventually (per-feature cost = team-cost-per-day × estimated duration × confidence interval)

This ADR is the **PM persona's anchor**. Without estimation, PM gets summaries; with it, PM gets decisions.
