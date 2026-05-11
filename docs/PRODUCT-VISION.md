# Product Vision — How company-brain becomes a need, not a want

**Synthesised:** 2026-05-10
**Author:** strategic synthesis from the brainstorm thread
**Goal of this doc:** translate "where do we head so AI doesn't kill us"
into (a) a one-sentence vision, (b) a Now/Next/Later roadmap, (c) ten
non-negotiable architectural directives for engineering, (d) a defensible
five-product portfolio that gets to $1B+ valuation.

This is not a polite options memo. It takes positions. Disagree in PRs.

---

## TL;DR — the thesis in 60 seconds

**We are not a Claude Code clone. Claude Code is a coding agent; we
are the codebase's institutional memory.** A coding agent forgets the
moment its context window scrolls. We persist, govern, and serve that
memory to humans, agents, and auditors.

**AI does not kill us. AI commoditises one of our inputs (LLM
extraction quality) but supercharges one of our outputs (the brain
becomes the memory layer that AI agents query).** When Sonnet ships
10M-token context, "paste your repo" works for a $300 query but is
illegal for a regulated bank, impossible for an Anthropic competitor,
and unaffordable at 1000-engineer scale. We win on **governance**,
**linkage**, and **time** — three things context windows do not give
you.

**Founders won't pay; engineering leaders at 50-1000 person orgs
will, and so will AI platform vendors.** Founders hold the codebase
in their head. The buyer is the **VP of Engineering** (onboarding,
risk, compliance) and the **AI Platform team at AI-tool vendors**
(Cursor, Cognition, internal F500 copilots) who need an org-specific
brain to plug into.

**The unicorn path is multi-product on a single graph.** The graph
is the moat — code + Slack + Jira + git + PagerDuty + Datadog all in
one queryable substrate. We launch with one product (knowledge) and
ship four more on top (risk, compliance, AI-agent, workflow). Each
is a six-figure ACV add-on. Sourcegraph + Glean + Snyk + Datadog all
followed this playbook and got to $300M-$2B ARR.

---

## Vision (the one line)

> **The operating system for understanding software at organisational scale.**

We are the system of record for what your codebase does, how it got
that way, who knows it, and what changes when you touch it. Humans
query us via IDE and chat. Agents query us via MCP. Auditors query us
via reports.

---

## North Star metric

**Weekly Knowledge Queries Per Engineer (WKQE).**

A leading indicator of embedding into daily workflow. Targets:

- **Pilot** (≤50 eng): ≥ 3 WKQE → product-market fit signal
- **Year-1 enterprise** (200 eng): ≥ 10 WKQE → daily habit established
- **Mature** (1000+ eng): ≥ 25 WKQE → embedded across PR review,
  onboarding, on-call, planning

Why not "entities extracted" or "MAU"? Both are vanity. WKQE measures
that the brain is **load-bearing in the engineering org's day**. If
WKQE drops, churn is coming.

---

## Buyer / persona map (the three real customers)

### Persona 1 — VP of Engineering at 50-500-eng org

**Job to be done:** "Onboard new hires faster, manage tribal-knowledge
risk, pass compliance audits without a fire drill."

**Pain:** Senior engineer Sarah owns 35% of payments. She's pregnant.
The board asks "what's the bus factor?" — and there's no answer. New
hires take 3 months to ramp. SOC2 audit consumes 2 senior engineers
for 4 weeks every year writing system documentation by hand.

**Budget:** $100-300K/year for tools that solve this. Reports to CTO
or COO.

**Buying trigger:** A near-miss (key engineer announces they're
leaving), a failed audit, or hiring growth (Series B → 50→150
engineers).

**Pricing fit:** $50-200K/year platform license + per-seat IDE add-on.

### Persona 2 — Platform / AI team at an AI-tool vendor

**Job to be done:** "Make our agent good at THIS customer's codebase
without us having to host their code."

**Pain:** Cursor's autocomplete is brilliant on a public repo, mediocre
on the customer's monorepo because it doesn't know "in this codebase,
all SQL goes through jOOQ DSL chains, not raw strings". They want a
context layer they can call.

**Budget:** Either a platform line item (API costs, $100K-1M/year per
vendor) OR a per-query revenue share with us.

**Buying trigger:** Customer churn for "your AI doesn't understand
our code" reasons. Already happening at Cursor / Cognition / GitHub
Copilot enterprise.

**Pricing fit:** Per-API-call ($0.001-0.01/query) OR enterprise
agreement ($X per AI-vendor seat).

### Persona 3 — Compliance / Security officer at a regulated company

**Job to be done:** "Show the auditor what our system actually does,
in writing, with citations to the code."

**Pain:** SOC2/PCI/HIPAA/EU-AI-Act all require documented system
architecture. Today this is a Word doc someone wrote in 2022, never
updated. When the auditor asks "where does PII flow", nobody can
answer in less than 2 weeks.

**Budget:** Compliance line item, $50-200K/year. Doesn't report to
engineering — reports to CISO or General Counsel.

**Buying trigger:** Failed audit, near-miss data breach, regulatory
deadline (EU AI Act August 2026).

**Pricing fit:** Compliance-license model — flat fee per audit
cycle, $50-150K, sold via channel partners (Big 4 audit firms).

---

## The five-product vision

The base extraction pipeline is **product 0** — the foundation. On top,
five products on the same graph, sold to different buyers, each
adding $50-200K ACV.

### Product 1 — Knowledge layer (we have this)

**The base extraction + query system we already ship.** What it does:
"What tables does X read?" "What breaks if I rename column Y?"
"Explain this method in the context of the call chain."

**Buyer:** Persona 1 (VP Eng), Persona 2 (AI vendors), Persona 3 (compliance).

**Status:** ~80% built. ADRs 0048/49/50 live; 0051/52 in progress.

### Product 2 — Risk layer (Q3 2026)

**Departure risk, single-owner paths, drift detection, blast-radius
alerts.** Joins the code graph with the people graph (commit history
+ ownership) + the time graph (versions over months).

**Killer features:**

- **Bus-factor dashboard** — "These 12 critical paths have a single
  owner. Their PTO + departure schedules:"
- **Architecture drift** — "Designed: synchronous service-to-service.
  Observed: 3 services now talk via SQS. Architect notified."
- **Migration impact preflight** — "Renaming `lobName` to `lob`
  affects 47 files across 12 teams. Suggested rollout plan: …"

**Buyer:** Persona 1 (VP Eng) — extends the existing license.

**Why need not want:** A near-miss with a key-person departure makes
this a board-level conversation. Buying decision moves from
engineering to executive.

### Product 3 — Compliance layer (Q4 2026 / Q1 2027)

**Auto-generated SOC2/PCI/HIPAA/AI-Act control documentation from the
code graph.** Every quarter, the brain produces audit-ready reports:
data flow diagrams, system architecture documentation, access control
matrices, change-history evidence.

**Killer features:**

- **SOC2 Type II evidence pack** generated weekly, indexed for the
  auditor portal
- **PII data lineage** — "user.email enters at API X, flows to
  database Y, leaves via webhook Z, retained for 90 days"
- **AI Act Article 13 documentation** — model card per AI usage in
  the code, generated automatically
- **Auditor portal** — read-only access for external auditors;
  every claim links to a code citation

**Buyer:** Persona 3 (Compliance/CISO). Different budget than
engineering. Sold via Big 4 channel partners (Deloitte, PwC, EY).

**Why need not want:** Required for federal contracts, FedRAMP, EU
AI Act compliance. Failure to produce = failed audit = lost revenue.
This is the most defensible line because it has **regulatory
mandate** behind it.

### Product 4 — AI Agent Substrate (Q2 2026 — start NOW)

**Brain-as-MCP for any AI agent** — Cursor, Cognition, GitHub Copilot,
Sourcegraph Cody, internal F500 copilots, custom enterprise agents.
We are NOT competing with these tools — we are the org-specific
context layer they call.

**Killer features:**

- **Drop-in MCP server** — `cursor mcp add company-brain` and
  Cursor's autocomplete suddenly knows the user's codebase deeply
- **Model-agnostic** — Anthropic, OpenAI, Bedrock-hosted, on-prem
  Llama; same brain
- **Per-org tenanted** — each customer's brain is isolated,
  cross-customer queries impossible
- **Usage-priced** — $0.001-0.01 per query, scales with the
  vendor's customer base

**Buyer:** Persona 2 (AI-tool vendor platform team).

**Why need not want:** Without it, the AI vendor's customers churn
for "your AI doesn't understand our code". This is **defensive
infra** for any AI-tool vendor selling to enterprise.

**Why this is the strategic linchpin:** if Cursor / Cognition / Copilot
all pipe through us, we are the de-facto org-context layer for the
entire AI-coding ecosystem. Network effects + switching cost =
unicorn moat.

### Product 5 — Workflow layer (Q3 2027)

**PR briefs, onboarding curricula, on-call runbooks, post-mortem
assists.** The brain doesn't just answer questions — it **inserts
itself into existing workflows** unprompted.

**Killer features:**

- **Auto-PR-brief** — every PR gets a comment within 30 seconds:
  "This changes endpoint X, affects 14 downstream services owned by
  3 teams. Risk: HIGH. Suggested reviewers: A, B, C. Test impact: Y."
- **Onboarding mode** — new engineer's first 2 weeks: brain runs
  them through a curated tour of the system relevant to their team
- **On-call assistant** — when PagerDuty fires, brain attaches a
  "what changed in the last 24h on this endpoint, who deployed it,
  rollback command" comment
- **Post-mortem auto-draft** — incident closes; brain drafts the
  post-mortem with code-cited timeline + affected entities + RCA
  hypotheses

**Buyer:** Persona 1 (VP Eng), but expansion sale (existing customer).

**Why need not want:** Once auto-PR-briefs are in the workflow for
6 months, removing them feels like losing seatbelts. Switching cost
becomes prohibitive.

---

## Roadmap (Now / Next / Later)

### NOW — next 90 days (Q2 2026)

**Theme: ship Product 1 (Knowledge) at production quality, plant the
flag for Product 4 (AI Agent Substrate) before competitors do.**

| Workstream | Deliverable | Owner | Why now |
|---|---|---|---|
| Quality | Land ADR-0053 (verifier + prompt rewrites + plan mode) | Eng | Hallucinated answers are the #1 churn risk |
| Cost | Finish ADR-0049 caching wire-up; verify cache_read>0 in production | Eng | Without it, gross margin is < 50% |
| Harness | Land ADR-0051 P1+P2+P3 (loop, sub-agents, skills) | Eng | Foundation for everything else |
| AI substrate | Product 4 MVP — public MCP server with rate-limited free tier; aggressive demo on Cursor + Cognition + Cody | PM + DevRel | First-mover wins this category. 6-month window before Sourcegraph realizes. |
| GTM | 10 customer-development interviews with VPs of Eng at 50-500-eng orgs | PM | Validate pricing, validate buyer, find 3 design partners |
| GTM | 5 conversations with AI-tool vendors (Cursor / Cognition / Cody / GitHub / internal F500 platform teams) | PM | Validate per-API-call pricing, find 1 design-partner integration |

**Deliverable for the round:** 3 paid pilots ($25-50K each, 6-month).
1 AI-vendor LOI.

### NEXT — Q3-Q4 2026

**Theme: Ship Product 2 (Risk) and Product 4 (AI Substrate) as paid
products. Start Product 3 (Compliance) discovery.**

| Workstream | Deliverable | Why next |
|---|---|---|
| Product 2 | Bus-factor dashboard, departure risk, drift detection. Sold as add-on. | First expansion-revenue product, quick to build on existing graph |
| Product 4 | GA the MCP server. Sign 3 AI-vendor partnerships with rev-share. | Window closes when Sourcegraph + Glean ship competing context APIs |
| Product 3 discovery | 10 conversations with CISOs / compliance officers at regulated companies | Compliance is the highest-margin, longest-sales-cycle product. Start now to ship Q4. |
| Engineering | Multi-tenant graph with proper org → workspace → repo → branch RBAC | Required for enterprise sale; currently single-tenant |
| Engineering | SCIM + SAML + OIDC SSO | Required for any enterprise procurement |
| GTM | First true enterprise sale — $200-500K ACV at a 200+ eng org | Validates the mid-market thesis |

**Deliverable for the round:** $1-2M ARR. 10-15 paying logos. Series A.

### LATER — 2027+

**Theme: Ship Product 3 (Compliance) and Product 5 (Workflow). Become
the default org-context layer for AI agents.**

| Theme | Bet |
|---|---|
| Compliance | Become the auto-generation source for SOC2/SOX/PCI/HIPAA/AI-Act docs. Channel partnership with a Big-4 firm. |
| Workflow | Auto-PR briefs become the default in the GitHub/GitLab marketplace. On-call attachments become the default in PagerDuty/Opsgenie. |
| AI-agent | Win the standard. Be the default in Cursor's MCP marketplace, in Anthropic's MCP registry, in OpenAI's GPT actions registry. |
| Cross-source | Ship Slack / Jira / Notion ingestion on the same graph. The brain becomes the org's TIME-TRAVELING knowledge OS, not just a code OS. |
| Self-host | Air-gapped on-prem deployment for federal / financial / defence. Doubles ACV. |

**Deliverable:** $20-50M ARR by end of 2027. Series B → C trajectory.
Unicorn valuation if Product 4 + Product 3 take off.

---

## Architectural directives for engineering (10 non-negotiables)

These are **hard requirements** the architecture MUST satisfy to enable
the vision. Anything that conflicts with these gets refactored.

### A1 — Multi-tenant from the data model up

Today: `workspace_id` everywhere, but no `org_id`, no proper RBAC.
Required: `org_id → workspace_id → repo_id → branch_id` hierarchy
with per-level access controls. **Enterprise sales fail without
this.** Bolt-on multi-tenancy is the #1 reason B2B startups die in
their Series A.

### A2 — Audit log on EVERY write — append-only, immutable

Every write to PG/Neo4j/Qdrant/JSON-brain emits an `audit_event` with
`{actor, action, resource_urn, before, after, source_ip, timestamp}`.
Required for SOC2, AI Act, and customer trust. Cannot be added later.

### A3 — Graph schema unifies all sources, not just code

The `nodes` and `edges` tables today carry only code entities. Generalise
the schema to support `node_type ∈ {Code, Person, Document, Incident,
Conversation, …}` and `edge_type` extensible per source. Product 5
(workflow) and the cross-source vision require this.

### A4 — Time-travel queries first-class

Every entity carries a `valid_from / valid_to` time range. Queries can
ask "what did we know about endpoint X on date D". Required for drift
detection (Product 2), audit (Product 3), and post-mortem (Product 5).

### A5 — API-first; UI is one consumer of N

The brain is consumed by: VS Code extension, web UI, Slack bot, GitHub
Action, Datadog integration, Cursor MCP, Anthropic API, AI vendor
agents. UI must be a thin client over the same API. Today: tightly
coupled web UI. Refactor the API layer to be the canonical
interface; UI becomes one of N consumers.

### A6 — Webhook outbound + event bus

Brain detects drift / new owner / new risk → emit event. External
systems subscribe. Required for Product 2 (Slack alerts), Product 5
(GitHub PR comments), Product 3 (compliance dashboards). Use SQS or
Redis Streams initially; promote to Kafka at scale.

### A7 — Pluggable LLM backend with model lock-in resistance

Enterprises require their own model (Bedrock-hosted Claude, on-prem
Llama, internal model). Provider abstraction must be airtight.
Currently leaks Anthropic-specific assumptions in 4-5 places. Audit +
fix; add Bedrock + on-prem Llama + Vertex AI as first-class providers.

### A8 — Data residency: EU + US + on-prem

EU customers want EU-hosted (GDPR). Federal customers want on-prem.
Architecture must support per-org region pinning, including the LLM
provider call. Today: everything routes through whatever Anthropic
endpoint the env var points at.

### A9 — Cost telemetry per query, per tenant, per source

Per-query / per-tenant cost is the foundation of usage-based pricing
(Product 4 needs this). Bake into the harness from P1.

### A10 — Schemas + APIs versioned forever

Once the MCP server is public (Q2 2026), every external agent depends
on the schema. Breaking changes require version bumps + dual-serving
period (≥ 6 months). Adopt SemVer for the MCP tool catalog now;
discipline pays off year 2.

---

## Anti-patterns (what NOT to build)

### NA1 — A general-purpose coding assistant

We are not Cursor. We are not Claude Code. We are not GitHub Copilot.
**Every minute spent on "let me also edit code for you" is a minute
not spent on the moat (graph + governance + linkage).** If a customer
asks for it, refer them to Cursor + tell them we're the brain Cursor
plugs into.

### NA2 — A search engine

Glean is a search engine. Sourcegraph is a search engine. We are a
**reasoning** layer — we answer "what would break if…", not "where is
foo defined". Search is table-stakes; the differentiation is the
business-context layer (the 21-field BusinessContext per entity).

### NA3 — A graph database

Neo4j is the graph database. We use it; we don't compete with it. If
the conversation drifts toward "let's build our own graph engine",
stop.

### NA4 — A model trainer

We use Anthropic / OpenAI / Bedrock. We don't fine-tune. We don't host
models. The brain is **structured retrieval + LLM reasoning**, not
custom-model magic. (Exception: small specialised models for specific
extraction passes, e.g. tree-sitter classification, where they're
proven cheaper than LLMs.)

### NA5 — A "developer productivity tool"

That category is a graveyard. Tabnine, Snyk's old positioning, every
DevX startup that died. **We are an enterprise data platform that
happens to start with code.** Position as data infrastructure, sell
to executives, not as a dev tool sold to ICs.

---

## Funding milestones (what each round needs)

| Round | ARR | Logos | Story to the investor |
|---|---|---|---|
| **Pre-seed / Seed** | $0-500K | 3 design partners | "Knowledge OS for codebases. Cursor + Cognition need an org-context layer; we're the first builder of it." |
| **Series A** | $1-3M | 10-20 mid-market logos | "Product 1 + Product 4 shipping. 60% NRR via Product 2 add-on. AI-agent ecosystem standardising on us." |
| **Series B** | $10-20M | 50-100 logos, 5+ enterprise | "Product 3 (compliance) shipping with Big-4 channel. Per-AI-agent revenue visible. Net-revenue retention > 130%." |
| **Series C** | $50-100M | 200+ logos, 50+ enterprise, 10+ AI-vendor | "Multi-product, multi-source. Default brain for the AI-coding ecosystem. Compliance is regulated mandate." |
| **Unicorn** | $100M+ | … | A combination of: 30%+ market share of "AI-agent context", recurring compliance license at F500, expansion path into adjacent enterprise data products. |

---

## Key risks + mitigation

| Risk | Likelihood | Mitigation |
|---|---|---|
| Sonnet-class context windows hit 10M tokens, "paste the repo" works → we lose | High | Compete on governance + cross-source linkage + time-travel + cost. Big enterprise will not paste their code into raw API. |
| Sourcegraph or GitHub builds Product 4 (MCP context substrate) | Medium-high | Move now. 6-month head-start = locked-in AI-vendor partnerships before they ship. |
| Anthropic / OpenAI ship "give us your repo, we'll remember it" → built-in memory | Medium | Multi-source linkage (Slack/Jira/PD) is harder to displace than just-the-code memory. |
| Claude Code / Cursor obviate the IDE plugin (P7) | Medium | Pivot the IDE plugin to be a thin MCP client; the value is in the brain, not the plugin. |
| Cost stays high → gross margin < 50% | Medium | ADR-0049 caching is the keystone fix. If post-0049 GM is still < 50%, we have a model-cost problem and need to negotiate enterprise-tier Anthropic pricing. |
| We get distracted by Product 5 (workflow) before Product 4 (AI substrate) → competitor takes AI-substrate market | High | Sequence is non-negotiable: P4 lands BEFORE P5. P5 is sticky but doesn't define a new category. |
| Founder buyer assumption proves true (no enterprise budget at pilot stage) | Medium | We don't sell to founders. Validate VP-Eng buyer in NOW horizon (10 customer-dev calls). If validation fails, reconsider whole thesis. |
| Dev tools market collapses (every dev tool company missed Series B) | Low-medium | We position as enterprise data platform, not dev tool. Compliance + AI-agent revenue is not "dev tool revenue". |

---

## How this changes the engineering roadmap

Engineering should reorder current work to honor the vision:

| Currently planned | Adjusted priority | Why |
|---|---|---|
| ADR-0048 (two-agent extraction) | ✅ Keep — already shipped | Foundation for Product 1 quality |
| ADR-0049 (caching) | ✅ Top priority — finish now | Gross-margin requirement; blocks Series A |
| ADR-0050 (big-repo recovery) | ✅ Keep — already shipped | Enterprise sale requires reliability on big repos |
| ADR-0051 (harness migration P1-P4) | ✅ Keep, but reorder: **P3 (skills) before P4 (hooks)** | Adding new framework support is the #1 customer ask in pilot; ship skills first |
| ADR-0052 P5 (slash + MCP + workspace) | 🚀 **Promote to Q2 2026 critical-path** | MCP server is the foundation for Product 4 (AI substrate) — strategic linchpin |
| ADR-0052 P6 (marketplace + scheduled + notebook + image + verifier + notes) | ⬇ Defer to Q3 2026 | Nice-to-haves; don't block Series A |
| ADR-0052 P7 (IDE extension) | ⬇ Defer; ship MVP only | The VS Code extension is a marketing tool more than a revenue tool. Don't over-invest. |
| ADR-0053 (quality patterns) | ✅ Top priority — verifier kills churn risk | Hallucinated answers = lost design partners |
| **NEW: A1 multi-tenancy** | 🚀 Net-new, Q3 2026 | Enterprise blocker |
| **NEW: A2 audit log** | 🚀 Net-new, Q3 2026 | Compliance blocker |
| **NEW: A6 webhook event bus** | 🚀 Net-new, Q3 2026 | Required for Product 2 alerts |
| **NEW: Product 4 hardening (rate limits, auth, billing)** | 🚀 Net-new, Q2 2026 | Public MCP server needs this immediately |

---

## Open questions to close in the next two weeks

1. **Pricing validation** — does $50-200K/year sound right to a real
   VP of Eng? Run the customer-dev calls.
2. **AI-vendor channel** — Cursor and Cognition are the prize design
   partners. Who has a warm intro?
3. **Compliance channel** — which Big-4 firm has the most appetite
   for a brain-driven SOC2 evidence pack?
4. **Org structure** — when do we hire (a) DevRel for Product 4
   adoption, (b) compliance specialist for Product 3, (c) enterprise
   AE for first $500K logo?
5. **Brand positioning** — "Codebase brain" is a category-of-one
   today. Strong or confusing? Maybe "Knowledge OS for Engineering"?

---

## Recommended next step (the one most useful thing)

**Two weeks of customer development, ten conversations.** Five with VPs
of Eng (Persona 1), three with AI-vendor platform teams (Persona 2),
two with compliance officers (Persona 3). Same script for each:

1. Show them the brain on `network-iq-backend-java`.
2. Ask: "If this worked perfectly, what would you pay annually?"
3. Ask: "What other questions would you want it to answer?"
4. Ask: "Who else in your org would buy / use it?"
5. Listen for which of the five products lights them up.

The conversation answers: **(a)** which buyer is real today, **(b)**
which product's roadmap to pull forward, **(c)** what pricing fits.

Until those 10 calls happen, every engineering decision is a guess.
After them, the roadmap above gets confirmed or rewritten — and
either outcome is more valuable than another month of harness work.

---

*Disagreements with this doc are expected and welcome. Open a PR
that proposes the alternative thesis with evidence; the strongest
position wins. The thing we cannot afford is to drift through 2026
without picking a thesis at all.*
