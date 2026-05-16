# ADR-0079 — Persona-Aware Query Template Framework

**Status:** Proposed
**Date:** 2026-05-13
**Builds on:** ADR-0048 (citations), ADR-0059 (domain inference), ADR-0065 (RRF fusion), ADR-0066 (experiential memory), ADR-0067 (brain evolution), ADR-0070 (cross-source ingestion), ADR-0073 (event-stream)
**Pairs with:** ADR-0080 (velocity), ADR-0082 (drift), ADR-0083 (catalog evolution), ADR-0084 (compression/expansion)
**Strategic goal:** turn the brain from a single-surface developer-tool into a six-persona product (PM, Developer, CS, VP Eng, CFO, CEO). Same entity graph, six lenses.

---

## Context

The brain today returns a single answer shape regardless of who's asking. A PM asking "can we ship feature F in 4 days" gets the same retrieval recipe as a developer asking "what breaks if I change F", which is the same recipe as a CFO asking "what does F cost to maintain". They're different questions; they need different signals, different ranking, different answer formats.

PERSONA-DRIVEN-EXTRACTION-CAPABILITY.md audited the architecture and concluded 70% of the data is already extracted; what's missing is **persona-specific composition**. ENTITY-AND-QUERY-REFINEMENT-LOOP.md added: don't try to enumerate the templates universally — ship a framework with seed templates per vertical, then refine from usage.

This ADR formalizes that framework. It is explicitly **not** a list of 30 fixed templates. It's the schema, the routing, the answer-format scaffolding, and a starter set per persona that evolves via ADR-0066 experiential signal.

This is also the **highest-leverage ADR for the seed window**. Without persona templates, the brain is a backend with no product surface; with them, the same brain becomes six distinct products, demoable to six distinct buyers. Promoted from "Series A" (per pivot doc) to "ship now."

---

## Decision

Six mechanisms. The framework is shippable in 1.5-2 weeks; the seed templates take another 1-2 weeks of iteration; refinement runs continuously thereafter.

---

### M1 — Question Shape Schema

**Problem solved**: persona templates need a stable structural format so the catalog (ADR-0083) can evolve them, the router (M3) can match them, and the answer formatter (M4) can render them.

**Mechanism**: every persona template is a typed Shape:

```python
@dataclass
class QuestionShape:
    id: str                              # "pm.feature_progress"
    persona: Literal["pm", "dev", "cs", "vp_eng", "cfo", "ceo"]
    intent: str                          # natural-language intent statement
    intent_examples: list[str]           # 3-10 examples for router training
    required_signals: list[SignalSpec]   # what data must be retrievable
    optional_signals: list[SignalSpec]
    retrieval_recipe: RetrievalRecipe    # which views/edges to traverse
    answer_format: AnswerFormat          # sections, charts, citation rules
    evidence_budget_tokens: int          # how much raw evidence the expander may pull
    fallback_behavior: FallbackPolicy    # what to do if signals are sparse
    refinement_metadata: RefinementMeta  # promoted_at, usage_count, score
```

```python
@dataclass
class SignalSpec:
    name: str                            # "feature_entity"
    source_views: list[str]              # ["EntityState", "TimelineWindow"]
    required_confidence: float           # min 0.6 to satisfy
    sparse_fallback: SparseFallback      # what to use when missing
```

Shapes are vertical-agnostic. A `pm.feature_progress` shape works the same way in healthcare-RCM, fintech, and SaaS — only the entity bindings (M2) change.

**Why this matters**: if shapes are first-class typed objects, then the refinement loop (M5) and the curation UI (ADR-0083) can promote/demote/version them like any other entity. Shapes-as-code-only (e.g., Python functions) wouldn't be evolvable.

---

### M2 — Vertical Bindings

**Problem solved**: a shape says "look up `feature_entity`"; the binding says what counts as a feature in *this* workspace.

**Mechanism**: per-vertical YAML file mapping shape variables to seed entities + retrieval hints.

```yaml
# workspace/bindings/healthcare-rcm.yaml
pm.feature_progress:
  feature_entity_seeds: [PriorAuth, EligibilityCheck, ClaimSubmission]
  feature_entity_inference_hint: "Anything tagged as 'feature' in PRDs or Linear, or
    any code module under src/features/, or any noun-phrase in a Confluence page
    titled 'Feature: ...'"
  domain_callouts:
    - "Payer-specific behavior matters; flag if feature has per-Payer divergence"
    - "Surface integration test status per Payer separately"

cs.incident_root_cause:
  customer_entity_seeds: [Provider, Member]
  trace_back_through: [deploy_event, pr_event, prd_event]
  domain_callouts:
    - "If incident affects claim submission, prioritize Payer integration evidence"
    - "Default to last 30 days of deploys touching the affected entity area"
```

```yaml
# workspace/bindings/saas-b2b.yaml
pm.feature_progress:
  feature_entity_seeds: [Feature, Capability]
  feature_entity_inference_hint: "Anything tagged with type=Feature in Linear/Jira;
    any PRD with #feature label; any Notion page under /Product/Features/"
  domain_callouts:
    - "Surface customer-tier impact (free / pro / enterprise) separately"
```

Bindings ship as part of vertical packs (ADR-0083 M4). Customers can override per-workspace.

---

### M3 — Persona Router

**Problem solved**: a query like "how is the prior auth feature doing" has to map to (a) the right persona inferred from caller identity / context, (b) the right shape (`pm.feature_progress`), (c) the right entity bindings (`feature_entity = PriorAuth`).

**Mechanism**: three-stage routing.

**Stage 1 — Persona inference**: from caller metadata (auth/role mapping in workspace config) OR explicit selector in the query (`@pm`, `@dev`, `@cs`, etc.) OR LLM-classifier fallback over the query text. Default precedence: explicit > metadata > inference.

**Stage 2 — Shape match**: small classifier (seeded with `intent_examples` from each shape; refined via ADR-0066) chooses top-K candidate shapes within the matched persona. K=3 default.

**Stage 3 — Binding resolution**: extract entity references from the query (re-using ADR-0059 inference) and resolve against the workspace binding for the matched vertical. Substitute into the shape's retrieval recipe.

If shape match confidence < 0.5 → fall through to a generic retrieval (current behavior); also log as **unmatched query** for ADR-0083 curation queue (someone may need to add a new shape).

---

### M4 — Answer Format Templates

**Problem solved**: a CFO answer should not look like a developer answer. Same source data; different rendering.

**Mechanism**: per-persona answer formatters that consume retrieval output and emit structured response blocks.

Built-in formatters:

| Persona | Default sections | Default chart types | Default citation density |
|---|---|---|---|
| PM | status_summary, milestones_hit, milestones_missed, blocking_items, estimate_assessment | timeline, burndown | 2-4 per claim |
| Developer | blast_radius, similar_implementations, risk_overlay, citations_to_lines | call-graph snippet, dependency arrows | 1-3 per claim, line-anchored |
| CS | symptom, root_cause_chain, customer_history, prior_communications, recommended_next_step | causal chain | 2-5 per claim, source-stamped |
| VP Eng | drift_summary, debt_hotspots, capacity_load, estimate_vs_actual | trend lines, drift gauges | aggregate, drill-down on demand |
| CFO | cost_breakdown, debt_dollarized, infra_spend, headcount_allocation | cost waterfall, P&L style | numeric, source-stamped |
| CEO | strategic_summary, risks, wins, recommended_decisions | exec scorecard | 5-sentence narrative + drill-down links |

Each formatter is a typed function `(retrieval_output, shape, bindings) -> AnswerBlocks`. Pluggable; per-workspace overridable.

Citations are mandatory at all personas (per ADR-0048). Density and format vary; presence does not.

---

### M5 — Refinement Signal Capture

**Problem solved**: shapes need to evolve from real usage. ADR-0066 (experiential memory) provides the substrate; this mechanism wires templates into it.

**Mechanism**: every query → answer cycle records:

```python
@dataclass
class TemplateUsageEvent:
    timestamp: datetime
    persona: str
    matched_shape_id: Optional[str]      # None if no match
    match_confidence: float
    bindings_resolved: dict[str, str]    # which entities filled which slots
    retrieval_signal_coverage: float     # 0.0-1.0; how many required_signals were found
    answer_blocks_emitted: list[str]
    user_feedback: Optional[FeedbackSignal]   # thumbs up/down/edit
    follow_up_query: Optional[str]       # was a follow-up needed?
    elapsed_ms: int
    tokens_used: int
```

ADR-0067 background process aggregates these weekly:
- Shapes with low match confidence + high follow-up rate → flag for refinement
- Shapes never matched → flag for archival
- Recurring unmatched intents → flag for new-shape proposal (LLM-drafted; human-approved)
- Bindings with low signal coverage → flag binding gap (catalog work)

---

### M6 — Seed Template Catalog

**Problem solved**: brain has to ship with *some* templates or it does nothing on day 1.

**Mechanism**: ship 6-10 templates per persona (60 total) covering the high-frequency questions identified in PERSONA-DRIVEN-EXTRACTION-CAPABILITY.md. These are starter templates; refinement is expected.

Seed list (concrete, not exhaustive):

**PM (6)**: feature_progress · feature_blast_radius_for_estimate · customer_promise_lookup · feature_dependency_map · roadmap_status · open_decisions_for_feature

**Developer (8)**: blast_radius · similar_implementations_with_risk · domain_meaning_of_entity · architecture_pattern_for_use_case · why_was_this_decided · who_owns_this_area · recent_changes_to_area · pre_pr_overlay (using ADR-0073 M4)

**CS (6)**: incident_root_cause · customer_history_with_issue · why_we_built_it_this_way · explanation_for_customer · escalation_recommendation · similar_past_incidents

**VP Eng (8)**: drift_trend · debt_hotspots · capacity_load · estimate_vs_actual · bus_factor_per_area · velocity_per_team · risk_areas_for_quarter · area_health_summary

**CFO (5)**: feature_build_cost · feature_maintenance_cost · infra_spend_by_product_line · debt_dollarized_exposure · cut_impact_analysis (5 only because most CFO templates depend on ADR-0081 cost ingestion which is post-Series-A)

**CEO (6)**: quarterly_shipment_summary · open_escalations · strategic_risks · wins_and_proof_points · capital_allocation_signals · competitive_position (last one stub-quality until competitive intel connector)

Total: 39 seed templates. Each fits in ~30-80 lines of YAML. ~3 days of focused authoring + per-vertical binding files.

---

## Consequences

**Positive**:
- Same brain backend serves six personas → revenue expansion within same customer (engineering buys → expand to PM, CS → expand to VP, CFO at Series A)
- Templates as data (not code) means non-engineering customer-success can author + refine bindings
- Refinement loop turns every query into a learning signal; brain fits the customer better over time
- Unmatched queries surface gaps explicitly → product roadmap signal
- Pitch-ready: "every persona gets answers in their format from the same brain" is demoable in week 2

**Negative / risks**:
- Template authoring is real work; quality of seed templates is the demo
- Shape match classifier needs training data; cold-start may be poor
- Per-vertical bindings means more YAML to maintain; need vertical pack tooling (ADR-0083 M4)
- Distinct answer formats per persona means more LLM token spend per query (each format renders separately)

**Cost estimate**:
- Framework (M1-M5): 1.5-2 weeks
- Seed templates (M6): 1-2 weeks
- Per-vertical bindings (first vertical, healthcare-RCM): 0.5 week
- Refinement loop integration with ADR-0066: 0.5 week (assumes ADR-0066 is shipped)

**Total: 3.5-5 weeks of engineering** for full framework + seed templates + one vertical. Single engineer; parallel-safe with ADR-0080/0082/0083/0084.

---

## Phasing

**Phase 1 (seed window)**: M1 schema, M3 router (rule-based, no LLM classifier yet), M4 with built-in formatters per persona, M6 seed templates for healthcare-RCM (since network-iq-backend-java is the demo repo).

**Phase 2 (seed → Series A)**: M2 multi-vertical bindings (add SaaS-B2B + fintech-lending packs), M3 LLM classifier trained on M5 data, M5 refinement loop wired to ADR-0066.

**Phase 3 (Series A and after)**: per-customer template variants (workspace overrides), advanced formatters (interactive answers, drill-downs), template marketplace (customers share templates).

---

## Open questions

1. **Where do shapes live physically?** YAML in repo + curation API in admin UI. YAML for source-of-truth + version control; UI for fast iteration. Sync via filesystem watcher.
2. **What happens when a shape requires a signal that doesn't exist in the workspace?** Sparse-fallback declared per signal (e.g., "if no PRD ingested, fall back to commit-message-derived feature description"). If no fallback → refuse with a "missing connector: PRD ingestion" message + setup link.
3. **Multi-shape composition?** A query like "how is feature F doing AND can we ship in 4 days" maps to two shapes. Phase 1: pick the higher-confidence shape and add a follow-up suggestion. Phase 3: support shape composition via declarative joins.
4. **How do we evaluate template quality?** Same as ADR-0066: thumbs-up rate, follow-up rate, time-to-answer, citation-coverage. Aggregated weekly per shape; flagged below threshold.
5. **Vertical detection**: do we ask the customer or infer? Default: ask during onboarding (one question, dropdown of supported verticals). Auto-suggest based on connector content for self-service tier.

---

## What this unlocks

- ADR-0080 (velocity model) plugs into `pm.feature_blast_radius_for_estimate`
- ADR-0082 (drift entity) plugs into `vp_eng.drift_trend`
- ADR-0083 (catalog evolution) governs the templates as catalog entries
- ADR-0084 (compression/expansion) drives the evidence-budget mechanic in M1's `evidence_budget_tokens`

This ADR is the **product surface** for the brain. Without it, every other ADR is plumbing nobody sees.
