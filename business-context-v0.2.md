# Business Context — Refined Schema (v0.2 extension to schema-v0.1.md)

**Scope:** v0.1 captured the obvious business artifacts (PRDs, tickets, ADRs, glossary, OKRs). This document adds the *operating* business layer that almost every codebase needs but almost no graph captures: pricing & entitlements, customer model, strategic narrative, decision precedent, brand voice, business processes, customer evidence, and the geographic/regulatory variation that turns one-size answers wrong. Without this layer, code is contextually homeless — you can find what it does, but not why the business needs it that way.

**Lossless principle for business context:** Source artifacts are often non-code (Notion pages, Figma frames, contract PDFs, support tickets, interview recordings). Lossless means:
1. The source is stored/snapshotted at extraction time (`raw_payload` + `source_checksum`),
2. Parsed structure references the verbatim source via `source_range` or section anchors,
3. Versioned snapshots for things that change publicly (pricing pages, public roadmap) — we never overwrite a past public commitment.

---

## 1. Three Concentric Rings of Business Context

```
   ┌────────────────────────────────────────────┐
   │                STRATEGY                    │  who we are, what we believe,
   │  (bets, north star, principles, non-goals) │  what we won't do
   │  ┌──────────────────────────────────────┐  │
   │  │         CUSTOMER & PRODUCT           │  │  who we serve, what we offer,
   │  │  (segments, journeys, plans, promises)│  │  what we charge, what we promise
   │  │  ┌────────────────────────────────┐  │  │
   │  │  │           OPERATIONS           │  │  │  how we deliver, escalate,
   │  │  │   (processes, SLAs, support,    │  │  │  refund, comply, run incidents
   │  │  │    refunds, escalations, comp.) │  │  │
   │  │  └────────────────────────────────┘  │  │
   │  └──────────────────────────────────────┘  │
   └────────────────────────────────────────────┘
                      ↑
                 CODE links to
                 each ring via
                explicit edges
```

Most companies document only the inner two and lose the outer rings as tribal knowledge. v0.2 makes all three first-class.

---

## 2. Strategy Layer

### Nodes

| Node | Lossless attributes |
|------|--------------------|
| `StrategicBet` | id, narrative (verbatim), time_horizon, success_criteria[], owner_id, status (proposed/active/won/lost/sunset), evidence_for[], evidence_against[] |
| `NorthStarMetric` | name, definition (formula or sql), unit, current, target, owner_team_id, dashboard_id |
| `MetricTree` | root_metric_id, contributors[] (each `{metric_id, weight, relationship: 'input'|'leading'|'lagging'}`) |
| `Principle` | id, statement (verbatim), rationale, examples_following[], examples_violating[], owner_id, immutability (soft/hard) |
| `NonNegotiable` | id, statement, scope, enforcement (manual_review/process/code_assertion), owner |
| `Bet` | hypothesis, predicted_outcome, success_metric_id, decided_at, decided_by, status |
| `Sunset` | what_we_stopped_doing, when, rationale, replaced_by? |
| `Pivot` | from_strategy_id, to_strategy_id, reason, decided_at, lost_value[] |
| `StrategicNarrative` | title, body (verbatim), version, audience, supersedes_id? |

### Edges

| Edge | Source → Target | Notes |
|------|-----------------|-------|
| `enacts_bet` | Feature/Epic/Initiative → StrategicBet | |
| `contributes_to_north_star` | Feature/Endpoint/Component → NorthStarMetric | with `expected_lift` |
| `contributor_to` | BusinessMetric → MetricTree | |
| `governed_by_principle` | Decision/Feature/Code → Principle | |
| `violates_principle` | Decision/Feature → Principle | rare; carries `justification`, `approved_by` |
| `respects_nongoal` | Decision/Feature → NonGoal | explicit "we considered and didn't" |
| `supersedes_strategy` | Pivot → StrategicBet | |

### Why it matters
"Should we add a free tier?" — without strategy nodes the agent answers from generic best practice. With them, it traverses `Principle("we monetize teams, not individuals") ─governed_by_principle← past Decision(rejected_freemium_2024)` and answers in *this company's* voice.

---

## 3. Customer Model (beyond Persona)

### Nodes

| Node | Lossless attributes |
|------|--------------------|
| `CustomerSegment` | name, criteria (firmographic+behavioral, expression), size_estimate, ARR_share, examples[] |
| `Account` (B2B) | external_id, name, segment_id, plan_id, MRR, ARR, status (lead/trial/active/expansion/at_risk/churned), CSM_id, contract_id, signed_at, renews_at |
| `Workspace` (B2B) | account_id, name, member_count, settings_overrides, region, created_at |
| `EndUser` | workspace_id?, role_in_workspace, plan_seat_id, first_seen, last_seen |
| `LifecycleStage` | name (lead/trial/activated/expanded/at_risk/churned), criteria (expression), transition_rules[] |
| `JourneyStage` | name, expected_actions[], success_criteria, drop_off_metric_id, time_budget |
| `ActivationCriterion` | id, statement (e.g., "first 5 messages within 7 days"), measured_by_event_ids[] |
| `EngagementLoop` | name, trigger, action, reward, investment, instrumented_by_event_ids[] |
| `Cohort` | id, definition (signup_window/segment/behavior), tracked_metrics[] |
| `CustomerHealthScore` | account_id, score, components{}, computed_at |
| `Contract` | id, account_id, term_start, term_end, value, special_terms[], custom_clauses[] |
| `RenewalRisk` | account_id, score, signals[], owner |

### Edges

| Edge | Source → Target | Notes |
|------|-----------------|-------|
| `belongs_to_segment` | Account/Workspace → CustomerSegment | |
| `at_lifecycle_stage` | Account → LifecycleStage | with `valid_from`, `valid_to` |
| `journey_step_implemented_by` | JourneyStage → Screen/Endpoint/Workflow | |
| `activates_via` | ActivationCriterion → AnalyticsEvent | |
| `engagement_loop_for` | EngagementLoop → Feature/Component | |
| `health_signal_from` | CustomerHealthScore → MetricDefinition | |

### Why it matters
"What endpoints are critical to activation?" → traverse `ActivationCriterion ─activates_via→ AnalyticsEvent ←tracks_event← Component ─uses→ Endpoint`. These endpoints get extra deploy gates.

---

## 4. Pricing, Plans, Entitlements (the most underrated layer)

This is where business directly meets code. Every feature flag, paywall, quota, and rate limit ultimately maps to a pricing decision.

### Nodes

| Node | Lossless attributes |
|------|--------------------|
| `PricingPlan` | id, name (Free/Pro/Team/Enterprise), tier_index, status (active/grandfathered/sunset), available_to_segments[], available_in_geos[], landing_url |
| `PlanVersion` | plan_id, version_label, effective_from, effective_to, snapshot{}, public_url_at_publish |
| `PricingTier` | plan_id, threshold (e.g., seats 1-10), price_per_unit, currency, billing_period |
| `Entitlement` | id, key (e.g., "advanced_analytics"), kind (boolean/quota/rate/feature), description, owner_team_id |
| `EntitlementGrant` | plan_version_id → entitlement_id; props { granted (true/false), limit, soft_cap, hard_cap, overage_unit_price } |
| `QuotaDefinition` | id, name, unit (api_calls/seats/storage_gb/messages), reset_window (none/daily/monthly/billing_cycle) |
| `BusinessRateLimit` | id, entitlement_id, limit, window, scope (account/workspace/user), strategy (block/throttle/queue) |
| `Addon` | name, price, plan_compatibility[], entitlements_added[] |
| `Discount` | id, name, percent_off | flat_off, conditions, eligible_segments[], eligible_plans[], expiry, code |
| `Promotion` | id, name, eligible_segments[], offer_id, redemption_rules, valid_window |
| `BillingEvent` | name, schema_id, kind (charge/refund/credit/proration/usage), revenue_recognition_rule_id |
| `RevenueRule` | id, name, recognition_method (cash/accrual/deferred/subscription), formula, applies_to_skus[] |
| `Trial` | id, plan_id, length_days, conversion_rules, restrictions[] |
| `Sku` | id, name, plan_link, accounting_code |

### Edges (the bridge to code)

| Edge | Source → Target | Notes |
|------|-----------------|-------|
| `gated_by_entitlement` | FeatureFlag/Endpoint/Component/Function → Entitlement | the single most useful business→code edge |
| `consumes_quota` | Endpoint/Function → QuotaDefinition | with `units_per_call` |
| `meters_event` | AnalyticsEvent → BillingEvent | usage-based billing |
| `priced_by` | Plan → PricingTier | |
| `granted_by` | Entitlement → PlanVersion | |
| `revenue_recognized_by` | BillingEvent → RevenueRule | |
| `paywall_triggered_by` | Component → Entitlement | UI paywalls |
| `upgrade_path_to` | PricingPlan → PricingPlan | |
| `grandfathered_from` | PlanVersion → PlanVersion | |

### Why it matters (queries this enables)
- "If we change endpoint X's response shape, which pricing plans does that affect?" — `Endpoint ─gated_by_entitlement→ Entitlement ←granted_by← PlanVersion`.
- "Show every feature available to Trial users." — `Trial ─plan_id→ PlanVersion ─granted→ Entitlement ─gated_by_entitlement← Feature/Endpoint`.
- "Are there entitlements with no code enforcement?" — Entitlements with no incoming `gated_by_entitlement` from any Endpoint/FeatureFlag → leak risk.
- "Which endpoints are unpriced (no quota, no entitlement gate)?" — Endpoint without `consumes_quota` or `gated_by_entitlement` → revenue leakage candidate.

---

## 5. Decision Precedent, Voice & Brand

Most companies have implicit decision style and brand voice. Make them explicit so agents (and humans) follow them.

### Nodes

| Node | Lossless attributes |
|------|--------------------|
| `DecisionPrecedent` | pattern (e.g., "we always favor explicit consent"), examples_supporting[], examples_diverging[], status (active/superseded), owner |
| `BrandVoiceGuideline` | id, rule (verbatim), examples_good[], examples_bad[], scope (errors/empty_states/onboarding/all), severity |
| `MicrocopyStandard` | context (button/error/empty/onboarding/confirmation), pattern, examples, tone_attributes[] |
| `MessagingFramework` | audience, value_props[], proof_points[], objections_handled[] |
| `LegalConstraint` | id, jurisdiction, statement, source_law_or_contract_id, applies_to_features[] |
| `RegulatoryRequirement` | framework (GDPR/CCPA/HIPAA/PCI-DSS/SOC2/...), control_id, statement, applies_to_segments[], applies_to_geos[] |
| `EthicsGuideline` | principle, applies_to[] |
| `LocalizationRule` | locale, treatment_kind (currency/date/number/honorifics/units/sort_order), rule |
| `AccessibilityStandard` | wcag_level, applicable_components[], required_attributes[] |

### Edges

| Edge | Source → Target | Notes |
|------|-----------------|-------|
| `governed_by_precedent` | Decision/Feature → DecisionPrecedent | |
| `voice_compliant` | TranslationKey/HelpArticle/Component → BrandVoiceGuideline | |
| `voice_violates` | * → BrandVoiceGuideline | with `severity`, `auto_detected` |
| `subject_to_legal` | Feature/Endpoint/DataClassification → LegalConstraint | |
| `localized_by_rule` | TranslationKey/Component → LocalizationRule | |
| `meets_accessibility` | Component → AccessibilityStandard | |
| `messaging_aligned_with` | MarketingClaim/HelpArticle → MessagingFramework | |

### Why it matters
"Generate an error message for the rate-limit case" — agent traverses `MicrocopyStandard(context=error)` + `BrandVoiceGuideline("calm, brief, no blame")` and produces text in *this brand's* voice rather than generic.

---

## 6. Business Processes (cross-team workflows)

These live in someone's wiki page that's stale, or in tribal knowledge. Make them queryable.

### Nodes

| Node | Lossless attributes |
|------|--------------------|
| `BusinessProcess` | name (e.g., "Customer onboarding", "Refund approval", "Incident escalation", "Sales handoff"), trigger_event, owner_team_id, sla, status |
| `ProcessStep` | process_id, ordering, actor_kind (role/system/customer), actor_ref, action, input_definition, output_definition, decision_branches[] |
| `ApprovalWorkflow` | name, levels[] (each: required_roles[], threshold, conditions), escalation_after |
| `EscalationPath` | trigger, levels[] (role + time_to_next + channel), terminal_action |
| `Handoff` | from_role, to_role, payload_definition, sla, channel |
| `RACIAssignment` | process_step_id, role, assignment (responsible/accountable/consulted/informed) |
| `ServiceCatalogItem` | name, owner_team_id, request_form_id, fulfillment_sla |

### Edges

| Edge | Source → Target | Notes |
|------|-----------------|-------|
| `process_step_implemented_by` | ProcessStep → Endpoint/Workflow/Job/Form | the bridge to code |
| `requires_approval` | ProcessStep → ApprovalWorkflow | |
| `escalates_via` | Incident/Ticket → EscalationPath | |
| `handed_off_via` | ProcessStep → Handoff | |
| `step_responsible` | RACIAssignment → ProcessStep | |
| `triggered_by_event` | BusinessProcess → AnalyticsEvent/SystemEvent | |

### Why it matters
"Customer asks for a refund" → traverse `BusinessProcess(refund_request) ─step→ ProcessStep ─requires_approval→ ApprovalWorkflow ─process_step_implemented_by→ Endpoint(POST /admin/refunds)`. Anyone (or any agent) can answer "who approves what" without asking on Slack.

---

## 7. Customer Evidence & Commitments

Where does the truth about what customers actually want live, with what weight?

### Nodes

| Node | Lossless attributes |
|------|--------------------|
| `CustomerInterview` | id, interviewee_role, segment_id, date, transcript_id, themes[], consent_to_quote |
| `CustomerQuote` | text (verbatim), source_interview_id, segment, sentiment, mentioned_features[] |
| `SupportTicketTheme` | id, name, ticket_count_30d, examples_ticket_ids[], affected_segments[], severity_distribution{} |
| `NPSResponse` | score, verbatim, segment, persona, date, account_id |
| `WinReason` | text, count, segment, source (sales call/survey) |
| `LossReason` | text, count, segment, competitor_id?, source |
| `FeatureRequest` | id, title, evidence_ids[], requesters_count, weighted_arr, segment_breakdown{}, status |
| `Commitment` | kind (contract/marketing/public_roadmap/sales_promise), audience (account_id or segment_id), promise (verbatim), due, owner_id, status (pending/met/missed/withdrawn), source_id |
| `MarketingClaim` | text (verbatim), source_url, snapshot_at_publish, asset_id, applies_to_features[] |
| `RoadmapPublicSnapshot` | published_at, items[], source_url, snapshot_hash |
| `WinLossInterview` | account_id, outcome, key_factors[], date |
| `CustomerAdvisoryBoard` | members[], current_topics[] |

### Edges

| Edge | Source → Target | Notes |
|------|-----------------|-------|
| `evidence_for` | Quote/Interview/Ticket/NPS → FeatureRequest/Decision/Hypothesis | with `weight` |
| `customer_committed_to` | Account/Segment → Feature | with `contract_id`, `due` |
| `marketed_as` | Feature → MarketingClaim | |
| `quoted_in` | Quote → BlogPost/SalesDeck/HelpArticle | |
| `support_volume_for` | SupportTicketTheme → Component/Endpoint | with `ticket_count_30d` |
| `requested_by_segment` | FeatureRequest → CustomerSegment | with `weighted_arr` |
| `promised_in` | Feature → Commitment | |

### Why it matters
"Should we deprioritize feature X?" — agent traverses `Feature ←promised_in← Commitment` (any contractual? any public roadmap?) and `Feature ←requested_by_segment← FeatureRequest` (which segments? what ARR?). Decisions become evidence-weighted, not gut-weighted.

---

## 8. Revenue / Churn / Health Tagging on Code

Wire business outcomes back to code so the consequence of changes is visible.

### Edges (added to existing code nodes)

| Edge | Source → Target | Notes |
|------|-----------------|-------|
| `revenue_attributed_to` | Endpoint/Feature → CustomerSegment | with `arr_attributed`, `calculation_method` |
| `churn_signal_from` | AnalyticsEvent → ChurnPredictor | |
| `retention_critical` | Component/Endpoint/Workflow → JourneyStage | "this is the activation moment" |
| `monetization_hook` | Component → BillingEvent | upgrade prompt, paywall trigger |
| `support_volume` | Component/Endpoint → SupportTicketTheme | with `ticket_count_30d` |
| `npsa_correlated` | Feature → NPSResponse cohort | |
| `expansion_driver` | Feature → ExpansionMetric | |

### Why it matters
"Show endpoints with high support volume but no recent test changes" — operational triage prioritization.
"Show features that drive expansion ARR but are gated for the largest paying tier" — pricing/packaging review.
"Find retention-critical components without owners" — on-call coverage gaps.

---

## 9. Operational Business Rules

The "small print" that lives in support runbooks and contract addenda — and is repeatedly violated when not encoded.

### Nodes

| Node | Lossless attributes |
|------|--------------------|
| `RefundPolicy` | applies_to_segments[], window_days, conditions, approval_required, max_self_serve_amount |
| `EscalationCriterion` | trigger, channel, target_role, sla, ladder[] |
| `SLATier` | name (Standard/Premium/Enterprise), response_time, resolution_time, applies_to_plans[], breach_consequence |
| `DataPortabilityRight` | formats, scope, geo_applicability, sla_days |
| `RightToErasure` | scope, retention_overrides{}, geo_applicability, sla_days |
| `ContentModerationRule` | applies_to_field_id, severity, action (flag/block/escalate), reviewer_role |
| `FraudRule` | pattern (expression), action, false_positive_handling, owner |
| `KYCRequirement` | jurisdiction, applies_to_segments[], required_evidence[] |
| `SupportSeverityClassification` | level, criteria, response_sla, escalation_path_id |

### Edges

| Edge | Source → Target | Notes |
|------|-----------------|-------|
| `enforces_policy` | Function/Endpoint/Workflow → RefundPolicy/FraudRule/ContentModerationRule/KYCRequirement | |
| `governed_by_sla_tier` | Service/Endpoint → SLATier | with `breach_consequence` |
| `subject_to_data_right` | DataClassification/Column → DataPortabilityRight/RightToErasure | |

---

## 10. Geographic & Regulatory Variation

The single most common source of "it works for everyone except customers in EU/Brazil/India".

### Nodes

| Node | Lossless attributes |
|------|--------------------|
| `Geography` | id, name, regulatory_zones[] (EEA/CCPA/PIPL/...), data_residency_requirements |
| `JurisdictionalRule` | geography_id, rule (verbatim), applies_to_features[], source_law |
| `TaxRule` | jurisdiction, rate, applies_to_skus[], effective_from |
| `LocalPaymentMethod` | name, geography_id, integration_id |
| `ContentRestriction` | jurisdiction, restriction, applies_to[] |

### Edges

| Edge | Source → Target | Notes |
|------|-----------------|-------|
| `feature_available_in` | Feature → Geography | |
| `feature_unavailable_in` | Feature → Geography | with `reason`, `source_law_id?` |
| `data_must_reside_in` | DataClassification → Geography | |
| `payment_routed_via` | Endpoint(checkout) → LocalPaymentMethod | |

---

## 11. The Purpose Chain (Motivation Backbone) — the central traversal

Every non-trivial code element should be reachable backwards through:
```
Function/Component/Endpoint
  ←implemented_by─ ProcessStep   (operations layer)
  ←tracked_by─ Ticket
  ←implements_story─ UserStory
  ←belongs_to_epic─ Epic
  ←delivers─ Feature
  ←enacts_bet─ StrategicBet
  ←contributes_to_north_star─ NorthStarMetric
  
And separately:
Function/Endpoint
  ─gated_by_entitlement─→ Entitlement ←granted_by─ PlanVersion ←belongs_to─ PricingPlan
  ─enforces_policy─→ Policy
  ─subject_to_legal─→ LegalConstraint
  ─marketed_as─→ MarketingClaim
  ─customer_committed_to─→ Commitment
```

Code that breaks this chain → `flagged_orphan` (defined in code-context-v0.2.md §9). Surfaces in "purposeless code" report. The chain is the *forcing function* that keeps the business layer alive.

---

## 12. The Reverse Chain (Impact Analysis)

Same edges traversed forward let you ask:
```
StrategicBet
  →delivered_by→ Feature
    →broken_into→ Epic/Story
      →implemented_by→ Endpoint/Component/Job
        →validated_by→ TestCase
        →measured_by→ MetricDefinition/AnalyticsEvent
        →surfaced_in→ Component/HelpArticle
        →committed_to_via→ Commitment
```

Answers "If we deprioritize this bet, what's at stake?" deterministically.

---

## 13. Worked Trace — Pricing & Entitlements Scenario

**Scenario:** A customer on the Pro plan during a trial extension attempts to use Advanced Analytics in the EU.

```
EndUser ─belongs_to→ Workspace ─belongs_to→ Account
Account ─plan_id→ PricingPlan(Pro)
Account ─at_lifecycle_stage→ LifecycleStage(trial_extension)
Account ─region→ Geography(EU)

PricingPlan(Pro) ─version→ PlanVersion(2024.05)
PlanVersion(2024.05) ─granted_by→ Entitlement(advanced_analytics) {
  granted: true, limit: 10000_events_per_day, hard_cap: true, overage_unit_price: null
}

# Trial extension restriction
Trial(pro_extended) ─restrictions→ EntitlementRestriction {
  entitlement: advanced_analytics, granted: false, reason: "Trial restriction policy v3"
}

# Geographic gate
Feature(advanced_analytics) ─feature_unavailable_in→ Geography(EU) {
  reason: "Pending DPA review", source_law_id: "GDPR-art-28"
}

# Now follow the request through code:
HTTPEndpoint(GET /analytics/advanced) 
  ─gated_by_entitlement→ Entitlement(advanced_analytics)
  ─enforces_check→ AuthCheck(authenticated)
  ─consumes_quota→ QuotaDefinition(analytics_events_daily) [units_per_call: 1]

Handler(getAdvancedAnalytics) ─has_effect_profile→ EffectProfile {...}
Handler ─reads_from→ Table(analytics_facts) ─partitioned_by→ Column(region)

# Gating logic (in middleware) traverses the same graph at runtime:
Middleware(entitlementGate) ─enforces→ EntitlementCheck(advanced_analytics)
  decision tree:
    1. Is feature available in user's geography? 
       Geography(EU) for Feature(advanced_analytics) → unavailable → 451 with reason
    2. Does plan grant entitlement? 
       PlanVersion(Pro 2024.05) → granted: true
    3. Is account in trial extension? 
       Trial(pro_extended) restriction → granted: false → 402 with upgrade prompt
    4. Quota?
       (not reached)
```

The graph encodes every input the runtime decision needs, AND the rationale for each branch (`reason`, `source_law_id`), AND the link to the feature flag/contract/policy. An agent answering "why did this user get 451?" or "why is this paywalled in trial?" gives an answer with citations, not guesses.

---

## 14. Bridge: Business → Code Edges (consolidated)

The most important edges in the entire schema, repeated here for emphasis. These are what make company-brain a *moat*, because no one has them:

| Edge | Source (business) → Target (code) | What it answers |
|------|----------------------------------|-----------------|
| `gated_by_entitlement` | Entitlement → FeatureFlag/Endpoint/Component | "What does this plan unlock?" |
| `consumes_quota` | QuotaDefinition → Endpoint/Function | "Where do we meter usage?" |
| `process_step_implemented_by` | ProcessStep → Endpoint/Workflow | "Who implements this business process?" |
| `journey_step_implemented_by` | JourneyStage → Screen/Endpoint | "Where does the user touch the product at each stage?" |
| `customer_committed_to` | Account → Feature | "What did we promise this customer?" |
| `marketed_as` | Feature → MarketingClaim | "What did we publicly say this does?" |
| `governed_by_principle` | Decision → Principle | "Why was it built this way?" |
| `subject_to_legal` | Endpoint/Feature → LegalConstraint | "What law constrains this code?" |
| `feature_available_in` / `unavailable_in` | Feature → Geography | "Where can/can't this run?" |
| `revenue_attributed_to` | Endpoint → CustomerSegment | "What revenue does this protect?" |
| `enforces_policy` | Function → RefundPolicy/FraudRule/etc | "What business rule does this enforce?" |
| `enacts_bet` | Feature → StrategicBet | "What strategy does this serve?" |
| `contributes_to_north_star` | Feature → NorthStarMetric | "How does this move the metric that matters?" |

---

## 15. Lossless Concerns Specific to Business Context

| Source | How we keep it lossless | Watch out for |
|--------|------------------------|---------------|
| Customer interviews | Verbatim transcript stored; quotes pulled with timestamp offsets | Recording rights; PII redaction on extraction |
| Pricing pages | HTML snapshot per `PlanVersion` + parsed structure | Frequent A/B tests — multiple valid versions concurrently; record each |
| Contracts | PDF stored; clauses extracted with page+coords; custom amendments as separate `ContractClause` nodes | Custom one-offs are common at enterprise tier — don't average them away |
| Support tickets | Source-system raw payload retained for sample; aggregate into `SupportTicketTheme` | Volume — store all metadata, sample full bodies |
| Marketing claims | URL + asset snapshot at publish time | Drift from current product reality is itself a `DriftSignal` |
| Public roadmap | Versioned snapshots; never overwrite past public versions | Once public, it's a `Commitment` |
| Pricing plan changes | New `PlanVersion` per change; never modify past versions; grandfathering edges link versions | Customers on grandfathered plans need historical truth |
| Legal/regulatory text | Verbatim quoted with citation to source law/section | Translation differences across jurisdictions — store per-locale |

---

## 16. Open Questions for v0.3

1. **Source of truth for entitlements.** Configs, DB tables, and a SaaS like Stigg/Orb often disagree. Define a sync model with `entitlement_source_of_truth` per entitlement and reconciliation jobs.
2. **Implicit commitments.** Sales agent promised feature X to customer Y on a call. Capture as `Commitment(kind=sales_promise)` requires a workflow step in the sales process — instrument or accept the gap?
3. **Cohort primitives.** First-class `Cohort` nodes vs. computed at query time. If first-class, how are membership changes versioned?
4. **Granularity of segment-to-code.** Per-endpoint? Per-feature? Per-workflow? Probably feature-level by default with optional drill-down to endpoint.
5. **Plan version migrations.** Grandfathering rules can be arbitrarily complex. Need a `PlanMigrationRule` with `applies_when` predicates.
6. **Auto-extraction vs. human curation.** Strategy/principles/voice are usually human-authored. Operations/processes may be partially extractable from runbooks + workflow tools. Pricing/entitlements are extractable from billing systems. Define per-domain extraction strategy.
7. **Privacy boundaries.** Some business context (Account MRR, churn risk) is sensitive. Reuse the §4.14 security model from v0.1 with explicit `viewable_by` edges on business nodes.
8. **External commitments expiring.** Public roadmap items have implicit "we'll do this someday" weight even after dates pass. How to model the half-life of a commitment?
