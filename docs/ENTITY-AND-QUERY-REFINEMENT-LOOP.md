# Entity Catalog & Query Pattern Refinement Loop

**The user's correction**: "entities or query patterns are not exactly defined but we can refine them."

Right. The earlier persona-extraction doc spoke as if we had a fixed Payer/Provider/Plan/Customer ontology and six fixed persona query templates. We don't — and we shouldn't try to. **No company-brain product ships with a complete ontology**; the ones that work ship a seed + a refinement loop.

This doc defines that loop.

---

## TL;DR

Three ideas:

1. **Don't enumerate entities universally — seed per vertical, discover per workspace, promote on signal.** Healthcare-RCM workspace seeds with `{Payer, Provider, Plan, Claim, Member}`; brain auto-discovers more during ingestion (ADR-0059 already does this); high-confidence discoveries get promoted to first-class with human review.

2. **Don't enumerate query patterns universally — define question SHAPES per persona, fill them per vertical, refine from usage.** The PM "can we ship in N days" question has a fixed shape; what `entity_area` means depends on whether the workspace is RCM software or a SaaS.

3. **The refinement loop runs continuously**: ingest → infer → query → score → refine. ADR-0066 (experiential memory) and ADR-0067 (brain evolution background process) are the substrate for this. The catalog and templates are themselves entities that evolve in the brain.

This is also how Glean, Palantir Foundry, and DataHub work in practice. **Nobody ships a finished ontology.**

---

## Layer 1 — Entity catalog: seed → discover → promote

### Seed (what ships in the box)

Per industry vertical, ship a small opinionated seed catalog of "spine" entities — the ones every company in that vertical has.

| Vertical | Seed entities |
|---|---|
| Healthcare RCM | Payer, Provider, Plan, Claim, Member, Service, Encounter |
| SaaS B2B | Customer, Subscription, Feature, User, Incident, Contract |
| Fintech (lending) | Borrower, Loan, Application, Decision, Disbursement, Repayment |
| Marketplaces | Buyer, Seller, Listing, Order, Transaction, Dispute |
| Generic (no vertical declared) | Customer, Feature, Incident, User, Engineer, Team |

**Seed catalog properties:**
- Each entity has a name + a short description + 3-5 example aliases the inference layer should map to
- Each entity declares expected edges (Payer → Plan, Plan → Member, etc.)
- Each entity declares expected source coverage (which connectors should mention it)
- Catalog is a YAML file in the workspace, version-controlled, human-editable

```yaml
# workspace/entities.yaml (seeded for healthcare-rcm vertical)
entities:
  - name: Payer
    description: Insurance company that pays claims (e.g., Aetna, BCBS, UHC)
    aliases: [insurance, insurer, carrier, plan_admin]
    expected_edges: [issues→Plan, contracts_with→Provider]
    expected_sources: [code, prd, salesforce, intercom]
    confidence: 1.0   # seeded; human-confirmed
    source: seed

  - name: Provider
    description: Healthcare provider (doctor, hospital, clinic) submitting claims
    aliases: [doctor, hospital, clinic, facility, billing_provider]
    expected_edges: [submits→Claim, contracts_with→Payer]
    confidence: 1.0
    source: seed
```

### Discover (what the brain finds during ingestion)

ADR-0059 (domain entity inference) already extracts candidate entities from code, PRDs, calls, etc. Most of what's discovered won't be a real "spine" entity — it'll be a noun phrase, a class name, a Slack channel name. The discovery layer keeps everything but tags by confidence.

Discovery sources:
- Code: class names, table names, repeated noun phrases in comments
- PRDs: capitalized noun phrases, glossary sections
- Confluence/Notion: page titles, tag taxonomy
- Calls: speaker-noun phrases that recur across multiple meetings
- Salesforce/Hubspot: object/field names

A discovered entity carries:
```yaml
- name: PriorAuth   # discovered, not seeded
  description: "Pre-approval workflow for certain procedures"  # auto-generated from corpus
  aliases: [prior_authorization, preauth, pa, authorization]
  confidence: 0.62
  source: discovered
  evidence_count: 47   # times mentioned across sources
  first_seen: 2026-04-12
  source_breakdown: {code: 12, prd: 3, slack: 24, calls: 8}
```

### Promote (what becomes first-class)

A discovery is **promoted** to first-class when:
- evidence_count crosses a threshold (configurable; default 20)
- it appears in 2+ source types (not just code OR just Slack)
- it gets queried by a user (strong signal)
- a human approves it via curation UI

Once promoted, it becomes part of the live entity graph, gets edges resolved, and feeds into the persona templates.

**Demote / merge** happens too:
- Two entities with high alias overlap → merge candidate (human reviews)
- An entity with no recent mentions → demoted (still searchable, lower salience)
- An entity that humans repeatedly mark "not really an entity" → archived

This is the **stability/plasticity** pattern from the memory literature: stable spine + plastic edges. Seed entities are stability; discovery + promotion is plasticity.

---

## Layer 2 — Query patterns: SHAPE → FILL → REFINE

### Shape (persona-level, vertical-agnostic)

Per persona, define a small set of question SHAPES — the structural template, independent of what entities exist.

```yaml
# persona-templates/pm.yaml
shapes:
  - id: feature_progress
    intent: "what's the status of feature {F}"
    required_signals: [feature_entity, prd_event, pr_events, deploy_event]
    retrieval: timeline_view(feature=F, range=last_90d)
    answer_format:
      sections: [status_summary, milestones_hit, milestones_missed, blocking_items]
      citations_required: true

  - id: feature_blast_radius_for_estimate
    intent: "can we ship feature {F} in {N} days"
    required_signals: [feature_entity, affected_entity_count, historical_velocity, current_team_load]
    retrieval: estimation_query(feature=F, deadline=N)
    answer_format:
      sections: [confidence_distribution, comparable_past_features, risks]

  - id: customer_promise_lookup
    intent: "what did we promise customer {C} about feature {F}"
    required_signals: [customer_entity, feature_entity, call_transcripts, emails]
    retrieval: cross_source_search(customer=C, feature=F, source_types=[call, email, doc])
```

The shape is fixed per persona. The entities that fill `{F}`, `{C}`, `{N}` come from the workspace's evolving entity catalog.

### Fill (per-vertical concrete bindings)

Per vertical, the SHAPES get concrete bindings to the seed entities.

```yaml
# vertical-bindings/healthcare-rcm.yaml
pm.feature_progress:
  feature_entity_examples: [PriorAuth, EligibilityCheck, ClaimSubmission]
  domain_callouts:
    - "Payer-specific behavior matters; feature may differ per Payer"
    - "Highlight Payer integration count and per-Payer test status"

cs.incident_root_cause:
  customer_entity_examples: [Provider, Member]
  trace_back_through: [deploy_event, pr_event, prd_event]
  domain_callouts:
    - "If incident affects claim submission, surface Payer-specific impact"
    - "Default to last 30 days of deploys touching the affected entity"
```

### Refine (from usage signals)

ADR-0066 (experiential memory) was designed for exactly this: capture every query, every answer, every thumbs-up/down/correction. Three streams:

1. **Query trajectories**: which retrieval paths led to good answers; which were dead ends
2. **Verifier corrections**: when a human marks an answer wrong and provides the right one
3. **Pattern utility**: which templates get used repeatedly; which get bypassed

Every week (ADR-0067 brain evolution background process), the refinement step:
- Promotes templates that score high on usefulness
- Demotes templates that get bypassed
- Suggests new template variants based on questions that didn't match any existing shape
- Updates the entity catalog (promotions/demotions/merges)
- Surfaces a curation queue to a human admin

---

## The loop, end to end

```
┌────────────────────────────────────────────────────────────────────────┐
│                       INGESTION (continuous)                           │
│  Code, PRDs, Confluence, Slack, Calls, Email, Salesforce, etc.         │
└─────────────────────────┬──────────────────────────────────────────────┘
                          │
                          ▼
┌────────────────────────────────────────────────────────────────────────┐
│  ENTITY INFERENCE (ADR-0059, 0070, 0076)                               │
│  Map mentions → candidate entities; resolve cross-source duplicates    │
└─────────────────────────┬──────────────────────────────────────────────┘
                          │
                          ▼
┌────────────────────────────────────────────────────────────────────────┐
│  CATALOG STATE                                                         │
│  Seeded entities (high conf) + Discovered entities (variable conf)     │
│  + Promoted entities (graduated) + Demoted/merged history              │
└─────────────────────────┬──────────────────────────────────────────────┘
                          │
                          ▼
┌────────────────────────────────────────────────────────────────────────┐
│  PERSONA QUERY (PM, Dev, CS, VP, CFO, CEO)                             │
│  Match query → shape (per persona) → fill from catalog → retrieve      │
│  → answer with citations                                                │
└────────────────┬───────────────────────────────────┬──────────────────┘
                 │                                    │
                 ▼                                    ▼
┌──────────────────────────────────┐  ┌──────────────────────────────────┐
│  GOOD ANSWER                      │  │  BAD ANSWER / NO ANSWER          │
│  → record trajectory (positive)   │  │  → record trajectory (negative)  │
│  → boost template salience        │  │  → log query as unmatched shape  │
│  → boost matched entities         │  │  → flag for template extension   │
└──────────────┬────────────────────┘  └──────────────┬───────────────────┘
               │                                      │
               └──────────────────┬───────────────────┘
                                  │
                                  ▼
┌────────────────────────────────────────────────────────────────────────┐
│  WEEKLY EVOLUTION (ADR-0067 background process)                        │
│  Promote / demote / merge entities                                     │
│  Promote / demote / new-variant query templates                        │
│  Update vertical bindings                                              │
│  Surface curation queue to human admin                                 │
└─────────────────────────┬──────────────────────────────────────────────┘
                          │
                          └────── feeds back into catalog state ──────────┐
                                                                          │
                                                                          ▼
                                                                    next cycle
```

This is the same loop Glean runs, the same loop Palantir Foundry runs, the same loop DataHub/Amundsen run. None of them ship with a finished ontology. None of them require one.

---

## What this means for the ADRs

The persona doc proposed three new ADRs. With the refinement-loop framing, those ADRs need to be written **as a system, not as fixed schemas**.

### ADR-0079 (Persona Templates) — revised scope

NOT: "here are the 30 query templates we ship."
INSTEAD: "here is the **template framework** — shape definition format, fill resolution rules, refinement signal pipeline, and a seed of 6-10 templates per persona to bootstrap."

### ADR-0080 (Velocity Model) — revised scope

NOT: "model that predicts ship time given a fixed feature taxonomy."
INSTEAD: "model that predicts ship time given **whatever entity area the brain has discovered**, with falls-back-to-org-wide-average when entity-specific data is sparse."

### ADR-0082 (Drift as Entity) — revised scope

NOT: "drift detection over a fixed set of architectural decisions."
INSTEAD: "drift detection that **adapts to whatever ADRs/specs are present in the workspace**, with novelty detection for emerging architectural patterns the brain didn't seed with."

### One new ADR worth adding

**ADR-0083 — Catalog Evolution & Curation**

Defines:
- Entity promotion/demotion/merge thresholds
- Curation UI for human admin
- Catalog snapshot + rollback (so a bad auto-promotion doesn't poison the brain)
- API for users to manually add/remove entities
- Vertical-pack format (for shipping new verticals — healthcare-RCM, fintech-lending, etc.)

Cost: 2 weeks. Should be designed alongside ADR-0079 because they share the catalog state.

---

## Why this is actually the stronger pitch

The earlier "we have a brain that knows your entities and answers your queries" framing has a problem investors will spot in 30 seconds: **how do you know which entities? How do you know which queries?** If the answer is "we hardcode them per customer," that's services, not product.

The refinement-loop framing flips it: **we ship a seed + a learning system. Every week the brain knows your company better than the week before. Catalog evolves; templates evolve; the more you use it, the more it fits.**

That's the same pitch that took DataHub from open-source to the LinkedIn-spinout/$200M-ARR Acryl, the same pitch that powers Glean's per-customer "Knowledge Graph" ($5B valuation), the same pitch behind Palantir Foundry's ontology layer ($90B+ market cap). It's a proven category narrative.

Demo line for the seed pitch:
> "Day 1: brain ships with healthcare-RCM seed entities and 30 query templates. Day 30: brain has discovered 80 more entities specific to YOUR company, promoted 12 to first-class via your team's queries, refined 8 query templates because your PMs ask in a slightly different way. Day 90: brain matches the way YOUR company actually thinks. That's the moat — every week of usage makes it harder to switch."

---

## TL;DR for the founder

1. **You're right that we don't have entities or query patterns nailed down — and we shouldn't try to.** No successful company-brain product ships with a finished ontology.

2. **Ship seed + refinement loop.** Per vertical: 6-8 spine entities, 6-10 query templates per persona. Brain discovers and promotes the rest from real usage.

3. **The refinement loop is already designed in pieces** (ADR-0059 inference, ADR-0066 experiential memory, ADR-0067 brain evolution). Need ADR-0079 (template framework, NOT fixed templates) and ADR-0083 (catalog evolution & curation) to tie them together.

4. **This is also a stronger pitch than "we have your entities."** The "every week of usage makes the brain fit you better" narrative is what worked for DataHub, Glean, Palantir Foundry. It's the moat investors recognize.

5. **What to write next**: redraft ADR-0079 as a *framework* (seed format + refinement signals + curation), and write ADR-0083 (catalog evolution). Both designable in the seed window. I can draft on signal.
