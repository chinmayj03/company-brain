# Strategic Pivot — Company-Wide Brain (not just code)

**The user's framing**: company-brain shouldn't be a developer tool with code as the product. It should be **the entire company's brain** — emails, Slack, PRDs, Confluence, architectures, client calls, payroll. Code becomes one tier among many. The **core domain entities** the company is built on (Payer, Provider in network-iq; Customer/Subscription in SaaS; Client/Engagement in services) are the universe; everything else is commentary.

This is a **major strategic pivot**. Not "add a Notion connector to the existing product" — a fundamentally different category, buyer, and competitive landscape. Worth thinking through carefully before deciding when (and if) to make it.

---

## TL;DR

**The pivot is RIGHT directionally.** The brain's architecture (entity-graph + edges + provenance + temporal + causal) generalizes naturally beyond code. The user's insight — "core entities stay the same; everything is commentary about them" — is exactly the right framing. Most of our ADRs (especially 0070 PRD ingestion + 0073 event-stream + 0072 general primitives) already point this direction.

**But the timing is dangerous.** The pivot expands TAM 10× ($5B dev tools → $50B+ enterprise data fabric) but also puts us against $5B-valuation incumbents (Glean, Microsoft Copilot, Notion AI) who have distribution, brand, and 50+ shipped connectors.

**Recommended sequencing**: ship the seed pitch on the CODE wedge (we're best-in-class there, defensible against Sourcegraph), then EXPAND to company-wide as the Series-A growth story. Don't pitch "company brain" at seed — investors will ask "how is this not Glean?" and we don't have a 5-minute answer yet. Pitch "code brain that EXPANDS to company brain" — investors love expansion stories.

---

## The strategic shift in one diagram

```
Today's positioning (code memory):
  ┌────────────────────────┐
  │ Company-Brain          │
  │ (extracts code →       │  Buyer: VP Engineering
  │  answers code Qs)      │  Competitors: Sourcegraph, Cody, Cursor
  └────────────────────────┘  TAM: ~$5B (dev tools)


Pivoted positioning (company brain):
                 ┌──────────────────────────────────────────┐
                 │      DOMAIN ENTITIES (the universe)      │
                 │  Payer · Provider · Plan · Customer ·    │
                 │  Subscription · Feature · Incident ·     │
                 │  Engagement · Client · Employee · Bug    │
                 └──────────────────┬───────────────────────┘
                                    │ (every source extracts entity references)
       ┌────────┬────────┬────────┬─┴──────┬────────┬────────┬────────┐
       ▼        ▼        ▼        ▼        ▼        ▼        ▼        ▼
   ┌───────┐┌───────┐┌────────┐┌───────┐┌──────┐┌──────┐┌────────┐┌──────┐
   │ Code  ││ Notion││Confluen││ Slack ││Email ││ PRDs ││Client  ││Payroll│
   │       ││       ││ce      ││       ││      ││      ││Calls   ││/HR    │
   └───────┘└───────┘└────────┘└───────┘└──────┘└──────┘└────────┘└──────┘

 Buyer: CEO / COO / Chief of Staff
 Competitors: Glean, Notion AI, Microsoft Copilot for M365, ChatGPT Enterprise
 TAM: ~$50B (enterprise search + knowledge mgmt + workflow)
```

The pivot inverts the hierarchy: **code stops being THE product and becomes ONE source of evidence about the entities the company actually cares about.**

---

## What changes (honestly)

### 1. Buyer changes from VP Eng → Executives

**VP Eng buyer** (today's pitch): *"Onboard engineers faster, prevent bus-factor incidents, document SOC2 from code."* Budget: $50-200K/year. Cycle: 2-3 months.

**Executive buyer** (post-pivot): *"Your company has 1,000 employees, 500K Slack messages/month, 300 Notion docs, 200 hours of client calls. Nobody can answer 'what did we promise client X about feature Y in our last 5 calls?' Company-brain answers it in 3 seconds with citations."* Budget: $200K-2M/year (because it touches everyone). Cycle: 6-12 months.

The exec sale is harder, slower, more political — but the deal sizes are 5-10× and renewals are stickier.

### 2. Competition changes drastically

**Today** (code memory): Sourcegraph (SOTA, just announced 7.0), Cody, Cursor's indexing, GitHub Copilot Enterprise. Manageable competitive set; we have specific 12-feature differentiation.

**Post-pivot** (company memory):

| Competitor | Strengths | Weaknesses we exploit |
|---|---|---|
| **Glean** ($5B valuation) | 50+ connectors, big enterprise customers, "enterprise search" brand | Per-doc search; no entity graph; no temporal/causal; no contradictions |
| **Microsoft Copilot for M365** | Owns the data (Outlook, Teams, OneDrive, SharePoint) | Locked to Microsoft tenant; weak on non-MS data; not extensible |
| **Notion AI** | Great in-Notion experience | Doesn't extend beyond Notion; not for code/Slack/email |
| **ChatGPT Enterprise** | Brand; integrations growing | Pure RAG; no structured entity extraction; no causal/temporal model |
| **Causal / Coda AI** | Verticals (finance, knowledge work) | Niche; not full company memory |
| **Gong / Chorus** | Call intelligence (subset) | Calls only; doesn't unify with other data |
| **Mem.ai / Reflect / Tana** | Personal knowledge | Personal scale; not enterprise |
| **Palantir Foundry** | Best-in-class entity ontology + data fabric | Six-figure setup; consulting-heavy; enterprise-only |

**The brutal truth**: Glean is closest to where we'd land. They have a 4-year head start, $200M raised, ~600 customers. Going head-on against Glean is a Series-B fight, not a seed fight.

### 3. Architecture is mostly already right

The good news: **the brain's architecture generalizes naturally.** Most ADRs already point this direction:

- ADR-0057 (universal file extraction): generalizes — extends to any extractable file
- ADR-0070 (PRD/Notion/Confluence/Slack ingestion): explicitly proposed for this pivot
- ADR-0058 (schema awareness): generalizes — DDL becomes one schema source; OpenAPI is another; Salesforce schema is another
- ADR-0059 (DomainEntity inference): EXACTLY this — derives Payer/Provider/Customer concepts from code; trivially extends to derive them from documents too
- ADR-0073 (event-stream architecture): generalizes — ANY change in ANY source becomes an event
- ADR-0072 (memory primitives — contradictions, salience, forgetting): generalizes — these are domain-agnostic
- ADR-0066 (ExperientialMemory): generalizes — query trajectories work for any query type

**What needs to be net-new for the pivot:**

- **N1 — Multi-source connector framework** (extends ADR-0070 from Notion-only to 20+ sources)
- **N2 — Cross-source entity resolution** (the Payer mentioned in code = the Payer mentioned in Notion PRD = the Payer mentioned in client call transcript = ONE entity)
- **N3 — Source-attribution provenance** (every fact carries which source it came from + when)
- **N4 — Source-specific permissions** (HR data ≠ engineering data ≠ board-deck data; per-source RBAC)
- **N5 — Per-buyer-persona query templates** (CEO asks differently than VP Eng asks differently than account executive)
- **N6 — Compliance for non-code sources** (HIPAA for medical, SOX for financial, attorney-client privilege for legal)

### 4. The demo changes

**Today's demo**: lob column rename. Code-specific. Resonates with VP Eng.

**Post-pivot demo**: *"Last quarter we promised 3 customers something about feature X. What was it, who agreed to it, and is it on the roadmap?"* → brain queries last 90 days of Slack + Notion + Salesforce + meeting transcripts + PRDs + code commits → returns 7-bullet summary with citations to specific Slack threads, Salesforce notes, the PRD that documents the commitment, and the GitHub PR that started implementation.

**The exec sees this and wires the check.** It's a 100× more visceral demo than "show me what tables this method reads."

---

## Why the pivot is RIGHT (the long version)

### Argument 1: Code is one source; the company has 20+ sources of memory

Most institutional knowledge isn't in the codebase. It's in Slack threads from 2 years ago, board decks that explain WHY we built X, customer interviews that drove the product, contract negotiations that constrain what we can change, PRDs that capture intent before code, post-mortems that explain why production broke. **Building a brain that ONLY reads code answers ~20% of the questions a company asks.**

### Argument 2: The entity graph IS the company

Every company has a small set of core entities the entire org revolves around:
- Network-IQ: Payer, Provider, Plan, Network, Service Area
- Stripe: Customer, Charge, Subscription, Invoice, Payout
- Snowflake: Customer, Warehouse, Database, Role, Cost
- A consulting firm: Client, Engagement, Deliverable, Consultant, Practice Area
- Anthropic: Customer, Model, Prompt, Conversation, Capability

Once you have the entity graph, EVERY source of data is a stream of references to those entities + commentary about them. The brain becomes the unified view of all that commentary, indexed by entity.

This is what Palantir Foundry does (and why it's worth $80B). But Palantir is consulting-heavy, six-figure setup, white-glove only. **There is no AI-native, self-serve, fast-deploy version of this.** That's the opportunity.

### Argument 3: AI agents need company-wide context, not just code context

The user's earlier question about Sourcegraph 7.0 missed something important. Sourcegraph is positioning as the "intelligence layer for AI coding agents" — code-context only. But agents will increasingly need NON-CODE context: "what did we promise this customer", "who's the decision-maker for this account", "what's our deployment pipeline that we need to respect", "what compliance constraints affect this code path".

The agent of 2027 needs a brain that knows the company, not just the codebase. **First-mover wins this category.**

### Argument 4: Vertical specialisation is a Series-A risk

If we stay code-only and hit Series A with great metrics in code memory, the next investor question will be: *"How big can this get? Who else uses it besides VPs of Engineering?"* If the answer is "just VPs of Eng", our ceiling is bounded at ~$300-500M ARR (the dev tools ceiling). If the answer is "code is wedge 1 of 10", we have a $1B+ ARR story.

**Investors fund the bigger story; the bigger story is company-wide brain.**

### Argument 5: The architectural primitives we're already building TRANSFER

Cross-file pattern detection (ADR-0055) → cross-source pattern detection. Schema-aware extraction (ADR-0058) → entity-aware extraction across any structured source. Verifier loop (ADR-0056) → claim verification across any text source. Branch-aware versioning (ADR-0073) → workspace-aware versioning across any document workspace. Domain entity inference (ADR-0059) → THE FOUNDATION of company-wide brain.

We're 60-70% of the way there architecturally. The pivot doesn't require throwing anything away.

---

## Why the pivot is DANGEROUS (honest counterarguments)

### Counter 1: Glean ate this market 4 years ago

Glean has shipped 50+ connectors, has $5B valuation, has 600+ customers including Pinterest, Sony, Confluent. They sell to executives. They have the brand. **Going head-on against Glean as a seed-stage startup is a fight you lose.** They'll outspend you on sales, they'll close enterprise deals you can't get into, they'll out-integrate you on day one.

### Counter 2: Microsoft Copilot is the default

Every Microsoft 365 customer (most enterprises) gets Copilot bundled. It's "free" (in the bundle); it has access to Outlook + Teams + SharePoint + OneDrive natively; it's growing fast. Competing with the bundle is hard. **A meaningful chunk of the "company brain" market is being eaten before alternatives arrive.**

### Counter 3: Connector breadth is everything; we have ZERO connectors

Glean's moat is largely connectors (Slack, Gmail, Outlook, Notion, Confluence, Jira, Salesforce, GitHub, GitLab, Workday, Box, Dropbox, Drive, Zoom, Asana, ClickUp, Linear, Monday, etc.). Each connector is 2-4 weeks of engineering + ongoing maintenance + auth flows + enterprise security review. **Catching up to Glean's connector footprint is 18-24 engineer-months. We don't have that runway.**

### Counter 4: Compliance complexity explodes 5-10×

Code memory has bounded compliance scope (it's source code). Company-wide memory means HR data (Workday) → SOC2 + GDPR + state-level employment law. Financial data (Salesforce) → SOX. Customer call transcripts → CCPA + per-state recording laws. Healthcare (any client like Network-IQ) → HIPAA. **Each adds months of legal + security work. Each is a deal-blocker if missing.**

### Counter 5: Deeper means harder to demo

The lob query demo is concrete (rename a column; see the impact). The "company brain" demo is harder to scope: *"What did we promise customer X in last quarter's calls?"* — fragile. Requires real customer fixtures with realistic Slack/Notion/Salesforce data. **A bad demo on this kills the pitch.**

### Counter 6: Buyer cycle is 2-3× longer

Exec sales = 6-12 month cycles vs 2-3 months for VP Eng. As a seed-stage startup, slower cycles mean slower revenue, slower learning, slower iteration. **The pivot trades wedge speed for ceiling height.**

---

## The right framing — DON'T pivot at seed; SET UP the pivot for Series A

The honest synthesis:

- **The pivot direction is right.** Company-wide brain is the bigger product. Code is one source of many.
- **The pivot timing is wrong for the seed pitch.** We can't credibly compete with Glean today; we don't have the connectors, the compliance posture, the brand, or the proof points.
- **The right move is sequencing.** Pitch code memory at seed (defensible, demo-ready, clear differentiation vs Sourcegraph). Build the architectural primitives that GENERALIZE during the seed-to-Series-A period. Pitch company-wide brain at Series A as the expansion story (with demonstrated ARR from code memory + early multi-source proof points).

This is exactly the playbook Snowflake used: started as "data warehouse"; positioned as "data cloud" once they had product-market fit; expanded into Snowpark + apps + governance at Series E+. **Wedge first; expand from strength.**

### Concrete sequencing recommendation

| Phase | When | What we sell | What we build (background) |
|---|---|---|---|
| **Seed** | Now → Q3 2026 | "Code memory + AI agent context substrate" | All current ADRs + connectors for Notion, Confluence, Slack (3 connectors, not 20) |
| **Post-seed → Series A** | Q4 2026 → Q2 2027 | Same pitch + 3 design-partner customers using non-code sources | DomainEntity inference at scale; cross-source entity resolution; 7 more connectors |
| **Series A** | Q2-Q3 2027 | "Company brain — code is one source; we extend across your entire stack" | Connector library to 20+; compliance for HR/financial/healthcare; exec dashboards |
| **Series B** | 2028 | "The Palantir Foundry of AI-native institutional memory" | Industry verticals; partner ecosystem; on-prem + air-gapped deployment |

### What "set up the pivot" means concretely

These are things to do NOW that PRESERVE the option to pivot later:

1. **Don't lock the data model to code-only.** Entity types should be EXTENSIBLE. (Already true — entities are dataclasses; adding `MeetingTranscript` is a new type, not a schema migration.)
2. **Build the connector framework now (ADR-0070), even if only Notion ships first.** The framework is reusable for all sources.
3. **DomainEntity inference (ADR-0059) is the cornerstone.** Make sure it generalises to non-code entities. (Currently extracts from code class structure; should also extract from frequency-of-mention in any text.)
4. **Cross-source entity resolution** is the ONE genuinely-new architectural concept. Start the design now (ADR-0074 below); ship by Series A.
5. **Pricing model architecturally permits per-source upsells.** Start with code; add Notion connector for $X/month; add Slack for $Y/month. Price expansion built in.
6. **Hire one connector engineer** at Series A close (not before); they own the connector library expansion.

### What NOT to do at seed

- Don't pitch "we're company brain" at seed. Investors will compare to Glean and we lose.
- Don't try to ship 20 connectors before seed close. Ship 1 (Notion) as proof of architecture.
- Don't compete with Microsoft Copilot in their bundle. We're a distinct category (deep entity reasoning, not surface-level chat).
- Don't take HIPAA/SOX customers in pre-seed (compliance debt sinks startups).

---

## Sharper differentiation vs Glean (the Series-A pitch)

When we DO pivot at Series A, the answer to *"how is this not Glean?"* needs to be 30 seconds and devastating. Here's the shape:

> *"Glean is enterprise SEARCH. Company-brain is enterprise REASONING. Glean answers 'find me the doc that mentions X.' We answer 'why do we do X, what changes if we touch X, who would notice, and what did we promise about it.' The difference: Glean indexes documents; we extract structured entities and edges that span all your documents AND your code AND your conversations. Glean returns a list of links; we return a graph of facts with provenance, contradictions, and time-travel. Use Glean for retrieval; use us for reasoning. They're complementary; we'd recommend both for a year, then customers consolidate on us once they realize search isn't enough."*

This pitch works because it's TRUE. Glean is genuinely a search product (their best feature is "find this doc fast"). We're genuinely a reasoning product (entity graph + edge taxonomy + causal chains). **Different categories sharing the same buyer.**

---

## Architectural deltas needed for the company-wide pivot

Six new ADRs. Don't ship until Series A. Design now to validate the architecture supports them.

### ADR-0074 — Domain-Entity-First Architecture

Reframe the brain so DOMAIN ENTITIES are the spine; sources are commentary. Specifically:
- Every entity has a `domain_class` (Payer / Provider / Customer / Engagement / etc.)
- Every fact is attributed to a SOURCE (code/notion/slack/email/call/payroll)
- Queries can pivot by entity ("everything we know about Acme Corp") OR by source ("what did Slack say last week")
- DomainEntity inference (ADR-0059) becomes the most important primitive — runs across ALL sources, not just code

### ADR-0075 — Multi-Source Connector Framework

Extends ADR-0070 from Notion-only to a full connector library:
- Slack (channels + DMs + threads)
- Gmail / Outlook (with label-based scoping)
- Google Drive / OneDrive (docs)
- Notion / Confluence / Coda (pages)
- Salesforce / HubSpot (CRM)
- Zoom / Gong / Chorus (call transcripts)
- Linear / Jira / Asana (tickets)
- Workday / Gusto (HR — high-compliance gate)
- Stripe / NetSuite / QuickBooks (financial — SOX gate)

Each connector is a plugin (per ADR-0052 P6 marketplace) so the connector library can grow without core changes.

### ADR-0076 — Cross-Source Entity Resolution

The killer feature: the same Acme Corp mentioned in code (`customer_id='acme_42'`), in Notion ("Acme Corp PRD"), in Slack ("hey what's Acme's status?"), in Salesforce account record, and in client call transcript = ONE entity in our brain.

Mechanisms:
- Deterministic: exact string match on entity ID / name across sources
- Heuristic: fuzzy match + context proximity
- LLM-inferred: for ambiguous cases, ask Sonnet "are these two references the same entity?"
- Human-confirmed: surface low-confidence merges for human review (then learn from feedback per ADR-0066)

This is what makes the brain a UNIFIED entity graph instead of a federation of separate per-source indexes.

### ADR-0077 — Source-Aware Permissions

Per-source RBAC:
- Engineering data → read by engineers
- HR data → read by HR + execs only
- Financial data → read by finance + execs only
- Board materials → read by board members only
- Customer-specific data → read by account team only

Per-entity permissions:
- Public-by-default OR private-by-default per workspace
- Tag-based (tagged "confidential" → restricted)
- Inherited from source (anything pulled from a private Slack channel inherits the channel's audience)

### ADR-0078 — Cross-Source Salience and Recency

The user's "freshness" concern from earlier turns becomes 10× harder across sources. Slack message from 2 weeks ago is more recent than a Notion page from 6 months ago, but the Notion page is more authoritative. **Salience needs to be source-weighted.**

Per-source salience baselines:
- Confluence canonical doc: high baseline (authoritative, slow-changing)
- Slack message: low baseline (high recency boost)
- PRD: medium-high baseline (intent capture)
- Code commit: high baseline (action)
- Email: low baseline (often noise)
- Call transcript: medium baseline (high context, low precision)

Used in retrieval ranking (per ADR-0065 RRF + ADR-0072 M2 salience).

### ADR-0079 — Persona-Aware Query Templates

The CEO asks differently than the VP Eng asks differently than the account executive. Same brain; different query patterns + different answer formats.

Personas:
- **Engineer**: code-cited, technical, includes diff/risk
- **VP Eng**: aggregate metrics, bus-factor, drift, hiring signals
- **Account Executive**: customer-promises, contract terms, escalations
- **CEO / COO**: org-wide patterns, project status, strategic decisions
- **Compliance Officer**: audit trails, data flows, policy adherence
- **Recruiter**: who's good at what, hiring history, retention patterns

Each persona gets a query template + answer-format preset. Single brain; many faces.

---

## What to actually do this week (concrete)

Given seed pitch is imminent and the pivot is post-seed:

1. **Don't change the seed deck.** Stay with code-memory positioning. Add ONE backup slide: *"And the architecture extends naturally to Slack, Notion, Confluence, customer calls. Code is wedge 1 of 10. Series A expansion story."*

2. **Add Notion connector to the in-flight ADR-0070 work.** Ship by end of seed cycle. Have ONE non-code connector working = proof the architecture extends.

3. **In customer-development calls** (per SEED-FUNDING-PACKAGE), ASK the question: *"If your code knowledge base also indexed your Notion + Slack + customer calls, would you pay 3× what you'd pay for code-only?"* Get qualitative signal on the expansion story.

4. **Update PRODUCT-VISION.md** with the post-seed expansion roadmap. (I can do this if you confirm.)

5. **Keep ADR-0074 → 0079 in design state.** Don't implement before Series A. They're the runway markers for Series A pitch.

6. **Start a `connectors-backlog.md`** tracking which sources to prioritize based on customer-development signal. Notion is probably first; then Slack OR Confluence based on customer mix.

7. **Don't rebrand.** "Company-brain" is already broad enough; we don't need to change it. (This was a smart instinct earlier — the name doesn't pigeonhole us into code.)

---

## The risk we have to swallow

If we stay code-focused at seed and DON'T pivot at Series A, we cap at $300-500M ARR (dev tools ceiling). If we pivot too early, we lose to Glean and Microsoft. The window is narrow: pivot at Series A IF we have:
- Strong code-memory revenue ($1-3M ARR from VPs of Eng — proves the architecture)
- 1-2 design-partner customers using multi-source extraction (proves expansion works)
- Connectors framework shipped + 5+ connectors live (proves we can keep up with Glean)
- A defensible "reasoning vs search" differentiation pitch (proves we're not a Glean clone)

If those four exist at Series A → pivot wins. If even one is missing → stay focused on code; do another tuck-in expansion later.

**This is the strategic bet.** It's right directionally; timing is everything.

---

## TL;DR for the founder

1. **You're right that the brain should be the company's brain, not just the codebase's brain.** The architecture supports it; the market is bigger; the moat is deeper.

2. **Don't pivot at the seed pitch.** Investors will compare us to Glean and we lose without the connectors + compliance + customers to show. The pivot is a Series A story.

3. **Ship code memory at seed; build expansion ONE connector at a time during seed-to-Series-A.** Notion first. Slack second. Whatever your design partners ask for next.

4. **Six ADRs (0074-0079) define the post-seed architecture.** Designed now (this doc); shipped after Series A close.

5. **The Series A pitch becomes**: *"Code memory ARR is $X. Our architecture extends naturally to all company knowledge — Slack, Notion, Confluence, calls, PRDs. Series A unlocks the multi-source expansion. Path to $50M ARR via vertical-by-vertical (engineering → product → sales → exec) rollout in the same enterprise customers."*

6. **The Series B pitch becomes**: *"The Palantir Foundry for AI-native companies, but self-serve, fast-deploy, modern stack. Glean is search. We're reasoning. Different categories; we're winning the bigger one."*

7. **What to do NOW**: ship Notion connector in the seed window. Update PRODUCT-VISION.md to include the expansion roadmap. Don't change the seed deck framing. Customer-development calls validate willingness-to-pay for the expansion.

The pivot is correct. The timing matters more than the direction. **Win code memory first; expand from strength.**
