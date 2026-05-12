# ADR-0059 — Temporal Ownership + Domain Inference Passes

**Status:** Proposed
**Date:** 2026-05-11
**Builds on:** ADR-0055 (cross-file pass — uses domain entities), existing GitCollector
**Sequenced with:** ADR-0055/56/57/58/60 — six-ADR set, parallel-shippable.

---

## Context

Two persistent gaps the benchmark exposed:

**Temporal blindness.** The brain captures `last_modified_commit` per entity but never builds a richer ownership / age / churn model. Questions the brain CAN'T answer:
- Who owns this code? (C13 in benchmark)
- What was added in the last 30 days? (the "what changed" question every code review needs)
- Which entities have churned more than 5× in the last quarter? (instability detector)
- What's the bus factor for the payments path? (Product 2 in PRODUCT-VISION — Risk layer)

**Code-vs-domain confusion.** The brain extracts Java classes (`CompetitivenessPayerSummaryDTO`) but doesn't know "Payer" is a business concept those classes represent. Questions C2/C3/C13/C19 in the benchmark all hit this. Without domain abstraction, queries like "explain this codebase to a new hire" return code-jargon answers.

Both gaps are solved by post-extraction passes that derive new structure from the existing graph + git history.

---

## Decision

Two new derived passes that run AFTER Stage 3 (BusinessContext synthesis) and BEFORE storage:

### Pass T1 — Temporal Ownership Pass (deterministic)

For each `Method` / `Class` / `ApiEndpoint` entity, read the file's git blame and aggregate:

```python
@dataclass
class TemporalOwnership:
    primary_author: str         # most lines
    co_authors: list[tuple[str, int]]  # (author, line_count) sorted desc
    bus_factor: int             # count of authors with >= 10% of lines
    age_days: int               # since first commit touching this range
    last_touched_at: datetime
    last_touched_by: str
    churn_30d: int              # commits in last 30 days
    churn_90d: int              # commits in last 90 days
```

Attach to entity as `entity.temporal: TemporalOwnership`.

Plus, derived ALERT entities (the Product 2 hook):

```python
@dataclass
class RiskAlert:
    entity_type: str = "RiskAlert"
    kind: Literal["bus_factor_one", "high_churn", "stale_owner_left"]
    affected_entity_urn: str
    severity: Literal["LOW", "MED", "HIGH"]
    message: str                # "Sarah owns 35% of this; Sarah hasn't committed in 90 days"
```

Heuristics:
- `bus_factor_one` if primary_author has > 70% of lines AND co_authors[1].lines < 10% — single-point-of-failure.
- `high_churn` if `churn_30d > 5` — instability or active redesign.
- `stale_owner_left` if `last_touched_by` hasn't committed anywhere in the repo in 90 days — knowledge departure risk.

These RiskAlerts power the Risk Dashboard (Product 2).

### Pass T2 — Domain Inference Pass (LLM, one-shot per repo)

After all extraction completes, ONE LLM call sees:

```
<input>
  <classes>
    {Class entities — name + role + 1-line purpose}
  </classes>
  <packages>
    {top-level package tree with file counts per package}
  </packages>
  <database_tables>
    {DatabaseTable entities from ADR-0058 if available}
  </database_tables>
  <api_endpoints>
    {ApiEndpoint URLs grouped by controller}
  </api_endpoints>
</input>

<task>
  Identify 5-15 distinct BUSINESS DOMAIN entities. For each, list the
  Java classes that represent it. A domain entity is a noun the business
  cares about (Customer, Payment, Provider) — not a technical artifact
  (Controller, Repository, Service).

  For each domain entity, also list:
  - aliases: other names used (DB column names, API field names, code shortcuts)
  - description: 2-sentence business meaning
  - cross_concept_relationships: e.g. "Plan belongs to Payer (one-to-many)"
</task>
```

Output: `DomainEntity` entities + `REPRESENTS` edges from each anchor Class to its DomainEntity. (Same shape as defined in ADR-0055, refined here.)

**Cost**: ~$0.01 per repo. Runs once per `brain index`.

Plus, two follow-on inferences that fall out of this:

**T2a — Module ownership rollup**: aggregate the TemporalOwnership data UP from methods → classes → modules → domains. Surface "the Competitiveness domain is 80% Sarah's code" as a higher-level RiskAlert.

**T2b — Onboarding curriculum**: derived from DomainEntity + temporal data. Rank DomainEntities by code volume; for each, pick 3 anchor classes (one Controller, one Service, one Repository) as the "read these to understand X" set. Stored as a `OnboardingPath` entity. Used to answer C4 ("what should a new hire read first") instantly.

---

## File ownership for THIS PR (parallel-safe)

```
company-brain-ai/src/companybrain/pipeline/temporal_pass.py            # NEW — Pass T1
company-brain-ai/src/companybrain/pipeline/git_blame_aggregator.py     # NEW — git blame helper
company-brain-ai/src/companybrain/pipeline/risk_alert_detector.py      # NEW — RiskAlert heuristics
company-brain-ai/src/companybrain/pipeline/domain_inference_pass.py    # NEW — Pass T2
company-brain-ai/src/companybrain/pipeline/onboarding_path_builder.py  # NEW — Pass T2b
tests/unit/test_temporal_pass.py                                         # NEW
tests/acceptance/test_risk_alerts_and_domain_inference.py                # NEW
```

Append-only edits to:

```
company-brain-ai/src/companybrain/models/entities.py    # add TemporalOwnership, RiskAlert, DomainEntity (if not added by 0055), OnboardingPath
company-brain-ai/src/companybrain/pipeline/orchestrator.py  # invoke temporal_pass + domain_inference_pass after stage 3
company-brain-ai/src/companybrain/api/routes/query.py   # surface RiskAlerts + OnboardingPaths in response payload
pyproject.toml                                            # pygit2 (for fast git blame)
```

Does NOT touch the chunker, ContextAgent, verifier, or universal extractors. Coordinates with ADR-0055 ONLY on the DomainEntity dataclass — recommend ADR-0055 ships first; ADR-0059 imports the same class.

---

## Acceptance test

```python
async def test_bus_factor_alert_for_lob_path():
    """If git history shows Sarah wrote 80% of CompetitivenessPlanRepository,
    a bus_factor_one RiskAlert must be emitted."""
    await run_pipeline_harness(repo="fixtures/network-iq-snapshot")
    alerts = await brain_query("RiskAlert where kind = 'bus_factor_one'")
    assert any(
        "CompetitivenessPlanRepository" in a.affected_entity_urn for a in alerts
    )


async def test_domain_entity_payer_inferred():
    """ADR-0055 had this same test; here it's the LLM-driven version."""
    await run_pipeline_harness(repo="fixtures/network-iq-snapshot")
    domains = await brain_query("list DomainEntity")
    payer = next(d for d in domains if d.name.lower() == "payer")
    assert len(payer.anchor_class_urns) >= 5
    assert "payer_id" in [a.lower() for a in payer.aliases]


async def test_onboarding_path_for_new_hire():
    """C4 in benchmark — must now PASS."""
    answer = await brain_query("I'm new to this team. What 5 files should I read first to understand the competitive analysis features?")
    # Should reference an OnboardingPath, not generic top-of-mind suggestion
    assert "CompetitivenessController" in answer
    assert "CompetitivenessPlanRepository" in answer
    assert any(re.search(r"port[/\.]in", answer))   # the service interface
```

---

## Effort estimate

3 days. Git blame aggregation is well-trodden territory (`pygit2`). Domain inference is one prompt + one parser. Onboarding path builder is heuristic-based.

---

## Action items

1. [ ] `pipeline/git_blame_aggregator.py` — `pygit2`-based blame reader; cache per-file results.
2. [ ] `pipeline/temporal_pass.py` — invoke aggregator for each entity; populate `TemporalOwnership`.
3. [ ] `pipeline/risk_alert_detector.py` — three heuristics emit RiskAlerts.
4. [ ] `pipeline/domain_inference_pass.py` — one-shot LLM call; parse output to DomainEntity entities.
5. [ ] `pipeline/onboarding_path_builder.py` — derive OnboardingPath per DomainEntity.
6. [ ] Wire into orchestrator AFTER stage 3.
7. [ ] Surface in `/query` response: `risk_alerts: [...]`, `domain_entities: [...]`, `onboarding_paths: [...]`.
8. [ ] Acceptance: bus_factor alert + Payer domain + onboarding path C4.
