# Persona-Driven Extraction — Capability Audit

**The user's framing**: company-brain should serve six different humans asking six different questions about the same underlying state:

| Persona | Their question | Decision they're trying to make |
|---|---|---|
| **PM** | What's the progress on feature X? Which domain entities are affected? Is the architecture good enough to land it in 4 days? | Scope, sequence, and commit |
| **Developer** | What's the blast radius of this change? What domain knowledge do I need? What's the implementation pattern? | How to build it without breaking things |
| **Customer Success** | What went wrong with this customer/incident? Why did we do it that way? | What to tell the customer; what to escalate |
| **VP Eng** | Where is reality drifting from intent? Where are we accumulating debt? | Where to invest engineering capacity |
| **CFO** | What's the cost of this feature — to build, to maintain, to host? Where's the tech debt $ exposure? | Where to fund / cut |
| **CEO** | What's the strategic state of the org? What's at risk? What's working? | Hiring, capital allocation, board narrative |

**Same brain. Six lenses.** This doc audits whether company-brain's current architecture (ADRs 0001-0073) supports those lenses, and identifies the specific capability gaps.

---

## TL;DR

**Architecture is capable; product surface is missing.**

The brain already extracts and structures most of the underlying signals: domain entities (ADR-0059), causal chains (ADR-0073 M2), temporal events (ADR-0073 M1), drift between ADRs and code (ADR-0050 P5), blast radius (ADR-0050 P3), provenance (ADR-0048), and cross-source connectors planned (ADR-0070). What's missing is **persona-specific composition** — query templates, answer formats, and four data extensions:

1. **Estimation/Velocity Model** (PM) — historical PRD-to-merge time per entity-area, complexity scoring
2. **Cost & Spend Ingestion** (CFO) — cloud bill, infra-tag-to-feature mapping, on-call hours
3. **Drift Scoring as a first-class entity** (VP) — currently a derived check; needs to be a persistent, time-series entity
4. **Persona-Aware Query Templates** (all) — already named ADR-0079 in the pivot doc; this is now urgent, not "post-Series A"

**Net**: 70% of the data is there. 30% needs four new ADRs (0079, 0080, 0081, 0082). All are designable now and shippable in the seed-to-Series-A window.

---

## Per-persona capability map

For each persona below, I list (a) the questions they ask, (b) the architectural primitives that already cover it, (c) what's missing, and (d) the new capability needed.

---

### 1. Product Manager

#### Questions
- "What's the status of feature F?"
- "Which domain entities (Payer, Provider, Plan, etc.) does feature F touch?"
- "Can the team realistically ship F in 4 days given current architecture and team velocity?"
- "What other features are blocked by/blocking F?"
- "What did we promise the customer about F in last week's call?"

#### What we already have
| Question | Covered by | Status |
|---|---|---|
| Feature → entities affected | ADR-0059 (domain entity inference) + ADR-0070 (PRD ingestion linking PRDs to entities) | DESIGNED, partially shipped |
| Feature progress (PRD → PR → deploy) | ADR-0073 M1 (event-sourced storage) + M2 (TimelineWindow view) | DESIGNED |
| Architecture fit ("is the codebase ready") | ADR-0048 (architecture-aware retrieval) + ADR-0063 (convention inference) | SHIPPED |
| Blocking dependencies | ADR-0050 P3 (call/dependency graph) | SHIPPED |
| Customer promises about F | ADR-0070 (call/email connector) + ADR-0076 (cross-source entity resolution, in pivot doc) | DESIGNED |

#### What's missing
- **Velocity model**: brain knows what shipped, but doesn't yet know **how long** similar features took historically. "Can we ship in 4 days?" requires `lookup(feature_complexity, team_velocity_for_this_entity_area, current_load) → probability_distribution`.
- **Complexity scoring**: needs feature → (entity count × edge count × test surface × avg historical PRD-to-merge time). Derivable from existing data, not yet computed.

#### New capability needed
**ADR-0080 — Estimation & Velocity Model** (proposed below).

---

### 2. Developer

#### Questions
- "If I change function F or entity E, what breaks?" (blast radius)
- "What does Provider mean in *this* codebase, not Provider in general?" (domain knowledge)
- "What's the existing pattern for adding a new claim type?" (implementation guidance)
- "Why was X built this way? What's the constraint?" (architecture rationale)

#### What we already have
| Question | Covered by | Status |
|---|---|---|
| Blast radius | ADR-0050 P3 (call graph) + ADR-0050 P4 (data-flow) + ADR-0073 M2 (CausalChain) | SHIPPED |
| Domain meaning of an entity | ADR-0059 (domain inference) + ADR-0070 (PRD/Confluence linkage) | DESIGNED |
| Implementation pattern (similar code) | ADR-0050 P2 (semantic retrieval) + ADR-0063 (convention inference) | SHIPPED |
| Architecture rationale | ADR ingestion (in-repo) + ADR-0070 (Confluence/Notion) + ADR-0048 (citations) | SHIPPED for in-repo; DESIGNED for cross-source |
| Why a decision was made | ADR-0073 M2 CausalChain (PRD → ADR → PR → deploy) | DESIGNED |

#### What's missing
- **"Implementation pattern WITH risk overlay"**: brain returns similar code but doesn't yet attach "this pattern was rolled back twice" or "this pattern caused incident I-2024-12-03". Combining patterns + incident outcomes is M2/M6 of ADR-0073, but the surface for it isn't built.
- **Multi-repo blast radius**: covered by ADR-0073 M5 (federated multi-repo) but only conceptually — the federation index isn't shipped.

#### New capability needed
Mostly **ship what's already designed** (ADR-0073 federation, M6 causal edges). One new piece: **risk-weighted retrieval** — when returning a code pattern, attach its operational track record (incident rate, rollback rate, MTTR contribution). This is a small extension to ADR-0065 (RRF fusion); list as a sub-feature, not a new ADR.

---

### 3. Customer Success

#### Questions
- "Customer C reported error X — what's the root cause?"
- "Why did we ship feature F the way we did? What's the constraint?"
- "Has this customer hit this before? What did we tell them last time?"
- "Which engineer wrote this code, and is the explanation in the PR?"

#### What we already have
| Question | Covered by | Status |
|---|---|---|
| Root cause of incident | ADR-0073 M2 (CausalChain view) + M6 (causal edges first-class) | DESIGNED |
| Why a decision was made | ADR-0073 M2 + ADR provenance | DESIGNED + SHIPPED for ADRs in-repo |
| Customer history with this issue | ADR-0070 (email/Intercom/Salesforce connectors) + ADR-0076 (entity resolution) | DESIGNED |
| Author + PR explanation | ADR-0050 P1 (git provenance) + ADR-0048 (citations) | SHIPPED |

#### What's missing
- **Customer entity is missing from the brain.** Today's brain has Payer, Provider, Plan (network-iq domain) but not "Customer" as a first-class entity that links incidents → calls → contracts → support tickets. ADR-0076 (cross-source entity resolution, in pivot doc) introduces this conceptually; needs to be designed in detail.
- **Incident → root-cause chain isn't end-to-end**: brain has the events but the "follow the chain backward from a customer-reported error to the offending PR to the offending PRD-decision" workflow isn't packaged.

#### New capability needed
- ADR-0076 (Cross-Source Entity Resolution — Customer as a first-class entity)
- **Incident-Trace** query template (lives in ADR-0079 Persona-Aware Templates)

---

### 4. VP of Engineering

#### Questions
- "Where is the codebase drifting from the architectural intent (the ADRs)?"
- "Where are we accumulating tech debt fastest?"
- "Which teams are over/under capacity?"
- "Which features are taking 3× their estimate?"
- "What's the bus factor on each domain area?"

#### What we already have
| Question | Covered by | Status |
|---|---|---|
| Drift between ADRs and code | ADR-0050 P5 (drift detection) | SHIPPED (basic) |
| Tech debt hotspots | engineering:tech-debt skill + ADR-0050 (semantic search over TODO/FIXME/anti-patterns) | SHIPPED |
| Team capacity | ADR-0073 M1 (event stream) + git author signals | PARTIAL (signals exist, not aggregated) |
| Estimate vs actual | ADR-0073 M1 (PRD timestamp vs deploy timestamp) | DESIGNED, not surfaced |
| Bus factor | ADR-0050 P1 (git provenance per entity area) | SHIPPED (data; not aggregated) |

#### What's missing
- **Drift as a first-class, time-series entity.** Today drift is a check that runs on demand. VP wants a dashboard view showing drift trend over time per domain — that requires drift to be a persistent entity with snapshots.
- **Capacity / load model.** Brain has commits, PRs, on-call rotations (if PagerDuty connected); not yet aggregated into "team X is at 130% load."
- **Estimate-vs-actual is on the data side but no surfacing.** PRD has target date; deploy has actual date. Diff is one query away but isn't packaged.

#### New capability needed
- **ADR-0082 — Drift as a first-class entity** with snapshots, trends, and per-domain scoring (proposed below)
- **Capacity rollup** — feature in ADR-0079 (persona templates) for VP

---

### 5. CFO

#### Questions
- "What did this feature cost to build? To run? To maintain?"
- "What's our infrastructure spend per product line?"
- "Where's our tech-debt exposure in dollar terms?"
- "If we cut team X by 20%, which products/customers are at risk?"

#### What we already have
| Question | Covered by | Status |
|---|---|---|
| Feature build cost (engineering hours) | ADR-0073 M1 events + git author hours | DERIVABLE, not packaged |
| Infrastructure spend per feature | NONE — we don't ingest cloud bills | MISSING |
| Maintenance cost (incidents, on-call) | engineering:incident-response data + on-call connector | DESIGNED |
| Tech-debt $ exposure | engineering:tech-debt skill (qualitative) | SHIPPED qualitative; no $ model |
| Team-cut impact analysis | bus factor + entity ownership + customer linkage | PARTIAL |

#### What's missing
- **Cost ingestion is entirely absent.** No connector to AWS/GCP/Azure cost-explorer, no payroll/HRIS connector, no headcount-cost-per-feature model.
- **No dollar-translation layer.** Even when we have engineering hours and headcount, we don't translate to $ (which requires payroll connector + cost-allocation model).
- **No infra-tag-to-feature mapping.** Even with cloud bill, we'd need to map cloud resources (tagged EC2 instances, S3 buckets, RDS clusters) to features/products. This requires either tag conventions or learned mapping.

#### New capability needed
**ADR-0081 — Cost & Spend Connectors and Mapping** (proposed below). Big ADR — covers cloud bills, payroll, on-call hours, infra-tag-to-entity mapping.

CFO persona is the **most data-poor** of the six. Most of what they need requires connectors we haven't built or designed. This is also the highest-value persona for enterprise expansion (CFO writes the check).

---

### 6. CEO

#### Questions
- "What did we ship this quarter? What's the lag on commitments?"
- "What customer escalations are open? What's the financial exposure?"
- "What strategic risks are emerging in the data?"
- "Where are we beating/losing to the competition?"

#### What we already have
The CEO view is mostly a **rollup of the other personas' data, framed strategically**. Architecture supports it; product surface needs designing.

| Question | Covered by | Status |
|---|---|---|
| Quarterly shipments | ADR-0073 M1 events + grouping | SHIPPED data; need PRD-to-launch entity |
| Open escalations | CS persona data | DEPENDS on CS coverage |
| Strategic risks | drift + tech debt + customer-promise gaps + incident trends | PARTIAL |
| Competitive position | NONE in current architecture | MISSING |

#### What's missing
- **Competitive intelligence ingestion.** Could be a connector (Crayon, Klue, sales:competitive-intelligence skill) but isn't part of brain today. Probably a separate connector / not first-priority for code-memory pivot.
- **The "strategic narrative" surface.** CEO doesn't want a dashboard with 50 metrics; they want 5 sentences with sources. This is mostly a query-template + LLM-synthesis layer (ADR-0079).

#### New capability needed
- ADR-0079 (Persona-Aware Query Templates) covers the synthesis surface
- Competitive-intel connector is a Series-A-or-later add

---

## Capability matrix at a glance

Legend: ✅ shipped · ✏️ designed (ADR exists) · ❌ missing

| Capability | PM | Dev | CS | VP | CFO | CEO |
|---|---|---|---|---|---|---|
| Domain entity extraction | ✅ | ✅ | ✏️ | ✅ | ✏️ | ✏️ |
| Cross-source entity resolution | ✏️ | ✏️ | ✏️ | ✏️ | ✏️ | ✏️ |
| Causal chain (decision → code → outcome) | ✏️ | ✏️ | ✏️ | ✏️ | ✏️ | ✏️ |
| Temporal/event timeline | ✏️ | ✏️ | ✏️ | ✏️ | ✏️ | ✏️ |
| Blast radius | n/a | ✅ | ✅ | ✅ | n/a | n/a |
| Drift detection (basic) | ✅ | ✅ | n/a | ✅ | ✏️ | ✏️ |
| Drift as time-series entity | ✏️ ❌ | n/a | n/a | ❌ | ❌ | ❌ |
| Velocity / estimation model | ❌ | n/a | n/a | ❌ | ❌ | ❌ |
| Cost ingestion (cloud, payroll) | n/a | n/a | n/a | ✏️ | ❌ | ❌ |
| Risk-weighted retrieval | n/a | ✏️ | ✏️ | n/a | n/a | n/a |
| Persona query templates | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |

The single biggest leverage point: **persona query templates**. Cheap to build (it's prompt + format work, not new data), unlocks usable answers for all six personas immediately, doesn't require Series-A capital.

The single biggest gap: **cost ingestion** — required for CFO answers, basically nothing in the brain today.

---

## What needs to be designed

### Now (during seed cycle)

**ADR-0079 — Persona-Aware Query Templates** (was post-Series-A in pivot doc; **promote to seed window**)

Without persona templates, the brain returns generic blobs. With templates, the same brain becomes 6 distinct products. This is the highest-leverage thing we can ship in the seed cycle.

Structure:
- Six template families (PM, Dev, CS, VP, CFO, CEO)
- Per-family: query intent → required signals → retrieval recipe (which views/edges to traverse) → answer template (sections, citations, charts)
- Plug into ADR-0065 (RRF fusion) as intent signal
- Plug into ADR-0048 (citations) for source attribution

Cost estimate: 1.5 weeks engineering + ongoing template iteration.

---

### Soon (seed-to-Series-A, prove the architecture extends)

**ADR-0080 — Estimation & Velocity Model**

Required for PM persona's "can we ship in 4 days" question. Learn from event stream (PRD timestamp → deploy timestamp) per domain area, surface confidence intervals, expose as a query.

Components:
- `historical_velocity(entity_area, team) → distribution`
- `complexity_score(feature) = f(entity_count, edge_count, test_surface, novelty)`
- `estimate(feature, team, deadline) → P(ship_on_time)`

Cost estimate: 2-3 weeks.

---

**ADR-0082 — Drift as First-Class Entity**

Today drift is a check; promote to a persistent entity with snapshots so VP can see trends. Required for VP persona "where is reality diverging from intent over time."

Components:
- DriftSnapshot entity (per domain, per ADR, per file area)
- Snapshot schedule (nightly)
- Trend computation
- Per-domain drift scoring

Cost estimate: 1 week (mostly packaging existing data).

---

### Series-A and after (the big-ticket items)

**ADR-0081 — Cost & Spend Connectors and Mapping**

CFO persona's data gap. Big effort because requires new connectors (AWS Cost Explorer, GCP Billing, Azure Cost Management, Workday/Gusto/Rippling, PagerDuty for on-call hours) AND a mapping layer (cloud-resource-tag → feature-entity).

Components:
- Cost connectors (cloud + payroll + on-call)
- Tag-to-entity learned mapping (with manual override)
- $ allocation model (engineer-hours × loaded-rate; infra-tag-spend × allocation %)
- Tech-debt $ exposure model (debt items × estimated remediation cost × interest rate)

Cost estimate: 2-3 months. Series-A pitch artifact ("CFO is now your buyer, deal sizes 5-10×").

---

**ADR-0076 — Cross-Source Entity Resolution** (already proposed in pivot doc)

Required for CS, CEO. Customer becomes a first-class entity that unifies Salesforce account + Intercom contact + Stripe customer + call transcripts + support tickets + the code that serves them.

Cost estimate: 2-3 months. Hardest of the post-seed ADRs; entity resolution at scale is research-grade.

---

## How this changes the pivot doc

The earlier `STRATEGIC-PIVOT-COMPANY-WIDE-BRAIN.md` placed ADR-0079 (persona templates) in the post-Series-A bucket. **This audit reverses that.** Persona templates aren't an enterprise-expansion feature — they're the difference between "another RAG over your repo" and "the brain that gives the right person the right answer." They should ship in the seed window.

Updated post-seed roadmap (priority order):

1. **ADR-0079 Persona Templates** — seed window, 1.5 weeks → unlocks all six personas with current data
2. **ADR-0073 M5 Federation + M6 Causal Edges** — already designed, ship by end of seed → unlocks Dev + CS
3. **ADR-0082 Drift as Entity** — seed-to-Series-A, 1 week → unlocks VP dashboard
4. **ADR-0080 Velocity Model** — seed-to-Series-A, 2-3 weeks → unlocks PM estimation
5. **ADR-0076 Cross-Source Entity Resolution + Customer entity** — Series-A, 2-3 months → unlocks CS + CEO at depth
6. **ADR-0081 Cost & Spend Ingestion** — Series-A, 2-3 months → unlocks CFO

This is also the natural **deal-expansion sequence**: VP Eng buys the seed product (Dev + VP personas), expands to PM and CS (one quarter later), then CFO + CEO (Series A surface).

---

## What this means for the pitch

**Today's pitch (single persona)**: "We make engineers faster by giving them perfect codebase context."

**Pitch with personas (post-templates)**: "We're the brain that answers six different humans' six different questions about the same company state. PM gets feature-progress with estimates. Developer gets blast-radius with risk overlays. CS gets root-cause chains with customer history. VP gets drift trends and capacity. CFO gets cost-per-feature and tech-debt $ exposure. CEO gets the strategic narrative. Same brain. Six lenses. Buyer expands as we ship more lenses; renewal is sticky because every persona using it makes the brain richer for the others."

This is the **flywheel argument** — the more personas use it, the more signal lands in the brain, the better the answers get. It's the Series-A pitch you can demo with one customer, three personas, and real data — IF the persona templates ship in the seed window.

---

## TL;DR for the founder

1. **Architecture supports all six personas already.** 70% of the data is in or designed.

2. **The product surface — persona-aware templates — is the missing piece.** Promote ADR-0079 from "Series A" to "ship now." 1.5 weeks of work; transforms the brain from a backend into six products.

3. **Three more ADRs needed (0080 velocity, 0081 cost, 0082 drift)** to fully cover all six personas. Spread across seed-to-Series-A. 0082 is cheap (1 week); 0080 is medium (2-3 weeks); 0081 is the expensive one (2-3 months) and is also the CFO unlock.

4. **CS and CEO depend on cross-source entity resolution (ADR-0076)** — Customer must become a first-class entity. Hard problem, Series-A bucket.

5. **The flywheel pitch becomes**: same brain, six lenses, every additional persona makes the brain better for all the others. This is the Series-A flywheel slide.

6. **Recommend writing now**: ADR-0079, ADR-0080, ADR-0082. Three ADRs unlock all six personas with one extension after the seed close. I can draft each on signal.
