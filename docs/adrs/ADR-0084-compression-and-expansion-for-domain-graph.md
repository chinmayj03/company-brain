# ADR-0084 — Compression & Expansion for the Domain Knowledge Graph

**Status:** Proposed
**Date:** 2026-05-13
**Builds on:** ADR-0059 (domain inference), ADR-0064 (privacy/audit), ADR-0065 (RRF fusion), ADR-0070 (cross-source ingestion), ADR-0072 (memory primitives — contradiction, salience, decay), ADR-0073 (event-stream memory)
**Pairs with:** ADR-0079 (templates consume cards), ADR-0083 (curation governs the lifecycle)
**Strategic goal:** make the domain knowledge graph hold up at multi-source scale. Code edges are explicit; prose edges are implicit. Without compression + expansion, the graph either drowns in raw evidence or starves on lossy summaries. This ADR resolves the tension.

---

## Context

The pivot from code-only to company-wide brain breaks code-graph assumptions. The COMPRESSION-EXPANSION-DOMAIN-GRAPH design doc laid out why:

| | Code | Prose (Slack/PRD/calls/email) |
|---|---|---|
| Edges | Explicit (imports, calls) | Implicit (buried in sentences) |
| Cardinality | Bounded (~20 callees per function) | Unbounded (50K Slack mentions per Payer) |
| Coreference | Unambiguous | "the Payer" / "Aetna" / "they" all coreferent |
| Update granularity | Atomic commit | Granular noisy messages |

Today's brain treats every entity uniformly: extract, store, retrieve. That works for code; it fails at multi-source scale. A Payer entity in a network-iq workspace will accumulate 50K-500K Slack mentions over a year. Loading them is impossible; ignoring them loses signal; summarizing into a single blob loses fidelity.

The fix: **two-state representation per entity** (compressed canonical card + raw evidence pool) with three pipelines (compactor, edge inferer, context expander) and adaptive storage tiering.

This ADR is the **data-side counterpart to ADR-0083** (which is governance/admin-side). They share state (the catalog, the entity records); they should be designed and shipped together.

---

## Decision

Seven mechanisms.

---

### M1 — Canonical Card Schema

**Problem solved**: every persona prompt loads multiple entity references. Loading raw evidence is infeasible; loading short summaries loses critical signal. Need a structured intermediate that's compressed enough to fit dozens per prompt and rich enough to be useful.

**Mechanism**: typed schema for the always-loaded representation:

```python
@dataclass
class CanonicalCard:
    entity_urn: str
    schema_version: str
    generated_at: datetime
    generated_from_evidence_count: int
    storage_tier: Literal["hot", "warm", "cold", "archived"]

    # Identity
    canonical_name: str
    aliases: list[str]
    type: str                           # Payer, Customer, Feature, etc.
    confidence: float                   # 0-1; identity confidence
    sources_seen_in: dict[str, int]     # {slack: 127, code: 5, ...}

    # Role summary (LLM-generated; refreshed on regen)
    role_summary: str                   # ~300-500 tokens; what is this entity, what does it do
    summary_generated_at: datetime
    summary_evidence_refs: list[str]    # which evidence chunks informed the summary

    # Edges (with confidence)
    key_edges: list[CardEdge]           # top-N highest-confidence edges; full edges in graph
    edges_truncated_count: int

    # State
    recent_state: RecentStateBlock      # last_30_days_mentions, trend, open_issues
    salience: float                     # 0-1; from ADR-0072 salience scorer
    last_useful_at: datetime
    first_seen_at: datetime

    # Citations (top-N for the summary; expander can pull more)
    top_citations: list[CitationRef]

    # Provenance for the card itself
    pipeline_run_id: str                # which compactor run generated this
    stale_marker: Optional[StaleMarker] # set when underlying evidence has changed since regen

@dataclass
class CardEdge:
    target_urn: str
    edge_type: str                      # impacts, depends_on, causes, owned_by, etc.
    confidence: float
    evidence_count: int
    last_corroborated_at: datetime
    decay_factor: float                 # current decay; 1.0 = fresh, 0.0 = stale

@dataclass
class StaleMarker:
    underlying_evidence_changed_at: datetime
    new_evidence_count_since_regen: int
    severity: Literal["minor", "moderate", "severe"]
```

Cards are **derived artifacts**, never source-of-truth. Source-of-truth lives in the event store (ADR-0073). Cards are regenerated from events.

Cards are size-bounded: target ~500-2000 tokens per card (configurable per workspace; tradeoff between context budget and summary richness).

---

### M2 — Card Generation Pipeline

**Problem solved**: cards must be generated cheaply, refreshed on appropriate cadence, and not regenerate unnecessarily.

**Mechanism**: a generation pipeline with three triggers:

**Trigger A — Initial generation**: when entity transitions from `candidate` to `promoted` (per ADR-0083 M1). Generates first card from accumulated evidence.

**Trigger B — Periodic regeneration**: weekly for `hot` tier, monthly for `warm`, quarterly for `cold`. Never auto for `archived` (regen on demand only).

**Trigger C — Event-driven regeneration**: when `new_evidence_count_since_last_regen` crosses a threshold (default 50 mentions, configurable). Or when a high-salience event (incident, deploy, contract signed) mentions the entity.

Generation algorithm:

```
1. Pull top-K most-salient evidence for entity (K depends on tier; hot=200, warm=100, cold=50)
2. Cluster evidence by topic (small embedding-clustering pass)
3. For each cluster, summarize via LLM (small model — Haiku-class — cost optimization)
4. Aggregate cluster summaries → role_summary
5. Refresh recent_state from last 30/60/90 days of events
6. Recompute key_edges (top-N by confidence × decay_factor)
7. Pull top citations (highest-salience evidence per cluster)
8. Persist new card; mark old card replaced (kept for audit per ADR-0083)
```

Cost target: < $0.05 per card regeneration on average. Hot-tier weekly + warm/cold rarely → ~$10-50 per workspace per month at typical entity counts.

---

### M3 — Implicit Edge Inferer

**Problem solved**: prose mentions co-occurring entities without declaring the relationship. Need to extract `(A, edge_type, B, confidence)` from raw text.

**Mechanism**: at ingestion time, every prose chunk goes through:

```
1. Identify entity mentions in chunk (ADR-0059 inference)
2. If 2+ entities present, run edge classifier (small LLM)
3. Output: list of (entity_A, edge_type, entity_B, confidence, supporting_span)
4. Persist as EdgeMention events in ADR-0073 stream
5. Confidence accumulator (M4) folds mentions into the live edge index
```

**Edge types** (small fixed set; expansion requires ADR amendment):

| Edge type | Semantics | Example |
|---|---|---|
| `impacts` | A's behavior affects B's behavior | "the Aetna change broke claim submission" |
| `depends_on` | A requires B to function | "PriorAuth requires the eligibility service" |
| `causes` | A directly produced B | "the PA latency spike caused the Aetna escalation" |
| `discussed_in` | Co-occurrence; weaker than topical relation | (any same-chunk mention) |
| `decision_about` | A formally decides about B (PRD, ADR) | PRD entity → its subject feature |
| `owned_by` | B has responsibility for A | "Sarah owns the Aetna integration" |
| `serves` | A delivers value to B | "we onboarded HealthcareSystemX as an Aetna customer" |

**Cost**: edge classification on every prose chunk is the dominant ingestion cost. Mitigations:
- Small classifier model (Haiku-class)
- Skip chunks with < 2 entity mentions
- Batch inference (10-50 chunks per call)
- Skip duplicate-pattern chunks (cache by chunk-hash)
- Workspace-configurable cost cap (degrade gracefully: skip low-salience chunks first)

Target: < $0.001 per chunk; < $50/month for typical workspace; configurable cost cap with auto-degrade.

---

### M4 — Confidence Accumulation

**Problem solved**: a single mention is noisy. The same edge inferred from many independent mentions across sources is reliable. Need a model that aggregates evidence into a calibrated confidence.

**Mechanism**: each edge has an accumulated confidence updated on every new EdgeMention event:

```python
def update_edge_confidence(
    current_confidence: float,
    new_mention: EdgeMention,
    existing_mentions: list[EdgeMention],
) -> float:
    base_weight = source_authority_weight(new_mention.source_type)
    # code structure > PRD > ADR > Confluence > Slack > call > email
    novelty_weight = 1.0 - similarity_to_existing(new_mention, existing_mentions)
    # corroboration in different words counts more than identical re-postings
    chunk_classifier_confidence = new_mention.classifier_confidence
    boost = base_weight * novelty_weight * chunk_classifier_confidence * 0.15
    return min(1.0, current_confidence + boost)
```

**Decay**: per ADR-0072 M2 salience decay. An edge with no new corroboration over 90 days decays. Decay is per-edge-type-tunable (a `causes` edge from an incident postmortem decays slowly; a `discussed_in` edge from one Slack message decays fast).

**Contradiction handling**: per ADR-0072 M1. If a new EdgeMention contradicts an existing high-confidence edge (e.g., "Aetna does NOT integrate via X12"), surface as Contradiction entity rather than silently lowering confidence.

---

### M5 — Context Expander

**Problem solved**: the canonical card is a starting point; some queries need raw evidence. The expander decides what raw evidence to pull, how much, and re-ranks for query relevance.

**Mechanism**: invoked by ADR-0079 query templates. Template declares `evidence_budget_tokens`; expander operates within budget.

```python
def expand_context(
    query: ResolvedQuery,
    cards_loaded: list[CanonicalCard],
    budget_tokens: int,
) -> ExpandedContext:
    gaps = identify_gaps(query, cards_loaded)
    # gap types: edge_unverified, recent_evidence_needed, raw_quote_required,
    # entity_relationship_unclear, contradiction_present

    candidates = []
    for gap in gaps:
        candidates.extend(retrieve_evidence_for_gap(gap, max_per_gap=20))

    # rank candidates with RRF (ADR-0065) across:
    # salience, recency, source_authority, query-specific relevance
    ranked = rrf_rank(candidates, signals=["salience", "recency", "authority", "relevance"])

    # re-rank top 3x budget with small LLM for query-specific relevance
    top = ranked[:3 * budget_tokens // avg_chunk_tokens]
    re_ranked = llm_rerank(query, top)

    selected = trim_to_budget(re_ranked, budget_tokens)

    return ExpandedContext(
        chunks=selected,
        gap_coverage_score=score_gap_coverage(selected, gaps),
        budget_utilization=sum(c.tokens for c in selected) / budget_tokens,
        unfilled_gaps=[g for g in gaps if not covered(g, selected)],
    )
```

**Budget management**: per-persona defaults (PM=5K, Dev=10K, CFO=20K — from ADR-0079 M1). Workspace-overridable. Hard cap to prevent runaway cost.

**Saliency-fragility mitigation** (per NeurIPS 2025 finding: 90% misattribution at 10K+ tokens): for budgets > 8K, expander chunks evidence into named "evidence sections" that the answer formatter can cite separately. Reduces interleaving distraction.

---

### M6 — Storage Tiering

**Problem solved**: hot entities (queried daily, mentioned weekly) deserve rich cards + fast retrieval; cold entities (touched once a year) shouldn't burn the same resources. Adaptive tiering keeps costs bounded.

**Mechanism**: every entity is in one of four tiers based on recency × query frequency × salience:

| Tier | Card refresh | Card token target | Evidence index | Expander cost |
|---|---|---|---|---|
| **hot** | weekly | 1500-2000 | full indexed | full budget |
| **warm** | monthly | 800-1200 | full indexed | budget × 0.7 |
| **cold** | quarterly | 400-600 | sparse-indexed (lazy) | budget × 0.4 |
| **archived** | on-demand only | minimal | offline; rehydrate on access | budget × 0.2; user-warned |

Tier transitions:
- Promotions on signal (high query rate, recent salience boost)
- Demotions on inactivity (per ADR-0083 M1 schedule)
- Manual override available (admin pins entity to a tier)

Storage backend tiering aligns: hot in fast vector index, warm in standard tier, cold in cheap object store, archived in cold object store with rehydration latency budget.

---

### M7 — Card-Stale Detector

**Problem solved**: cards drift from underlying evidence between regenerations. Answers cited from stale cards may be wrong. Need to detect and surface staleness.

**Mechanism**: lightweight watcher process:

```
For each card:
  count new evidence events since card.generated_at
  if count > stale_minor_threshold:
    set stale_marker = "minor"
  if count > stale_moderate_threshold OR contains_high_salience_event:
    set stale_marker = "moderate"
    schedule out-of-band regeneration
  if contains_contradiction OR contains_state_change_event:
    set stale_marker = "severe"
    block use of card; force regeneration before next query
```

`severe` staleness blocks: queries that would load this card pause, trigger immediate regen, then proceed. Adds latency on rare critical events; prevents stale-card-driven hallucination.

`minor` and `moderate` staleness annotates the answer: "Note: card last regenerated 14 days ago; 23 new mentions since; recent details may not reflect."

---

## Consequences

**Positive**:
- Domain graph holds up at multi-source scale (months of Slack ingestion don't kill the brain)
- Compressed cards make persona prompts feasible (load 10-20 entities per query within context budget)
- Expander preserves fidelity for high-stakes queries (CS root-cause, audit-grade compliance)
- Adaptive tiering keeps cost bounded as workspace grows
- Card-stale detector + ADR-0072 contradiction handling prevent stale-driven hallucination
- Edge inferer + confidence accumulation means the brain reasons about *probabilistic* relationships, not pretending everything is fact

**Negative / risks**:
- Card generation is the dominant LLM cost; needs aggressive caching + batching + small-model use
- Edge inference at ingest is real ongoing cost; cost-cap and graceful degradation are mandatory
- Expander re-ranking adds query latency (target < 200ms additional)
- Two-state architecture means consistency bugs (cards drift from evidence) are the most likely failure mode → M7 is critical
- Edge confidence model needs calibration; uncalibrated confidences mislead more than they help
- Saliency-fragility threshold (8K tokens) is a real cliff; architectural choice to chunk evidence is a partial mitigation, not a full fix

**Cost estimate**:
- M1 schema: 0.5 week
- M2 generation pipeline: 1.5-2 weeks (orchestration + cost optimization)
- M3 edge inferer: 1.5 weeks (classifier + integration with ingest)
- M4 confidence accumulation: 1 week
- M5 context expander: 1.5 weeks (RRF integration + LLM re-rank)
- M6 storage tiering: 1 week (mostly schema + backend integration)
- M7 stale detector: 0.5 week

**Total: 7-8 weeks**. 1-2 engineers; parallel-safe in pieces but M1-M2-M3 form a critical path (must be designed coherently).

---

## Phasing

**Phase 1 (seed window — cheap wins)**: M1 schema, M2 with simple summarization (no clustering yet), M6 with two tiers only (hot/cold), M7 basic stale flag (no auto-regen). Edge inference (M3, M4) deferred — live with explicit code edges + co-occurrence as edge-weak signal.

**Phase 2 (seed → Series A — real value)**: M3 implicit edge inference, M4 confidence accumulation, M5 context expander, M6 four-tier system, M7 auto-regen on severe stale.

**Phase 3 (Series A and after — research-grade)**: adaptive per-entity compression ratios, query-time edge inference (run classifier ad-hoc when card edges are insufficient), multi-hop evidence retrieval (follow causal chains), per-persona compression style (CFO cards emphasize cost; CEO cards emphasize narrative), evidence-pool sharding for very-high-cardinality entities.

---

## Open questions

1. **Card vs raw — when does a query MUST go raw?** Per-template `evidence_budget_tokens > 0` is the signal. CS forensic shapes default high; CEO synthesis defaults low. Workspace-overridable.
2. **Card-vs-evidence consistency on rollback** (ADR-0083 M3)?  Rollback only restores catalog state, not cards. Cards regenerate from evidence pool (which is append-only and unaffected by rollback). New cards consistent with new catalog state.
3. **Edge type extension**: when do we add a new edge type? Avoid expanding lightly (each new type increases classifier cost and confuses retrieval). Process: open ADR amendment; require evidence that an existing type can't capture the relationship; require classifier accuracy benchmarks.
4. **Confidence calibration**: how do we know our confidences are actually calibrated? Reliability-diagram benchmark on a held-out set of edges with human-labeled truth. Quarterly evaluation; recalibration via ADR-0067 background process.
5. **Multi-tenant isolation of evidence**: if Workspace A and Workspace B both mention "Aetna", their evidence pools must be strictly isolated even if entity inference is shared. Tier separation per workspace by default; cross-workspace inference only on explicit federation contract.
6. **PII / sensitive-content handling**: cards should not surface PII unless explicit. ADR-0064 PII detection runs at evidence ingest; redacted variants stored alongside; card generation uses redacted variants; expander surfaces full only with appropriate role.
7. **Compression quality eval**: how do we know cards are good enough? Hold-out queries answered from cards-only vs cards+raw; measure citation quality, factual accuracy, completeness. Run as part of ADR-0066 calibration loop.

---

## What this unlocks

- ADR-0079 templates can declare `evidence_budget_tokens` and trust the expander
- ADR-0083 can govern card lifecycle alongside entity lifecycle
- ADR-0072 contradiction detection feeds into edge confidence reduction (graceful contradiction handling)
- Cross-source persona answers become tractable (CFO loading 30 entity cards in one query within context window)
- Series-A pitch becomes provable: "we maintain a compressed knowledge graph with adaptive expansion, not per-document RAG. Reasoning, not retrieval."

This ADR is the **scale plane**. Without it, the company-wide brain melts at month 6 of multi-source ingestion. With it, the brain holds up to enterprise data volumes — and the compressed-graph architecture becomes the moat.
