# Rebalancing V2 — Code Is the Wedge, Not the Product

**The user's correction (verbatim)**: *"are we going more code specific because we are mostly aiming the product to be a brain/knowledge memory layer of the whole company. the codebase is just a way to extract most company specific information"*

The user is right. The V2 Wave prompts I just wrote are heavily code-centric (SCIP, Joern, Stack Graphs, blast radius, SQL deep extractor). The actual product is the company-wide brain where **code is one source among many** — Slack, PRDs, Confluence, calls, email, Salesforce, payroll all feed the same entity graph. This doc:

1. Owns the drift honestly
2. Re-articulates the product framing
3. Restructures V2 as **two parallel tracks** (Depth = code; Breadth = cross-source) instead of one code-heavy track
4. Maps every existing ADR to a track and flags what's missing
5. Proposes the rebalanced prioritization for the seed cycle

---

## 1 — The drift, named

V2 Waves 1-3 as written:

| Wave | Code-focused work | Cross-source work |
|---|---|---|
| Wave 1 | SQL deep, hybrid retrieval, prompt cache, streaming, blast radius prep | Glossary (could apply to any source but mostly used for code terms today); few-shot bank (any source) |
| Wave 2 | SCIP, Stack Graphs, Joern CPG, blast radius v2 (all code) | Per-workspace calibrator + LTR (source-agnostic but reranks code retrieval today); LazyGraphRAG (graph layer is general but seeded with code entities) |
| Wave 3 | Speculative decoding (general), HippoRAG (general but built on code graph), AGSER (general), per-workspace embedding fine-tune (general), persona auto-variants (general) | none explicit |

**Estimated weight**: ~75% code-specific, ~25% source-agnostic mechanics that happen to apply across sources.

The diagnosis-driven work (P0 bugs, code extractor, blast radius) is real and needed — those are demo-blocking issues today. But the **explicit cross-source pipeline** (the connectors, the entity resolution across sources, the source-aware salience, the source-aware permissions) is largely absent from Waves 1-3 even though it's the actual product.

---

## 2 — The product framing, re-articulated

```
SEED PITCH (today):
"Company-brain for engineering teams. Best-in-class code memory.
 Defensible vs. Sourcegraph 7.0."
                                ↓
                       wedge into the buyer
                                ↓
SERIES-A PITCH (12-18 months out):
"Company-brain. Same architecture extends to Slack, Notion, Confluence,
 PRDs, calls, Salesforce, payroll. Per-company tuned across all sources.
 Code memory was wedge 1 of 10."
                                ↓
                       buyer expands to PM/CS/VP/CFO/CEO
                                ↓
SERIES-B PITCH:
"Palantir Foundry for AI-native institutional memory. Glean is enterprise
 search; we're enterprise reasoning. Single brain; six personas; every
 source."
```

**Code is the wedge because**:
- It's the most structured, highest-fidelity company signal (explicit edges, atomic commits, type info)
- VP Eng has budget, decides fast, validates value quickly
- Defensibility against Sourcegraph 7.0 forces us to be excellent here
- Code Quality is demoable in 4-6 weeks

**Code is not the product because**:
- TAM is $5B (dev tools) vs $50B+ (enterprise data fabric)
- Real "company-wide" answers (CFO cost, CEO strategic, CS root cause across customer history) require non-code sources
- The pitch flywheel ("brain knows your company better every week") only compounds when learning from ALL signals, not just code

This was the whole point of `STRATEGIC-PIVOT-COMPANY-WIDE-BRAIN.md`. The V2 waves under-honored it.

---

## 3 — The rebalanced V2: Two Tracks, parallel

Restructure V2 into two tracks that run concurrently and share substrate:

```
┌─────────────────────────────────────────────────────────────────────────┐
│                     PILLAR 5 — PER-COMPANY DOMAIN TUNING                │
│       (workspace adapter; tunes BOTH tracks across all sources)         │
└──────────────────────────────────┬──────────────────────────────────────┘
                                   │
            ┌──────────────────────┴──────────────────────────┐
            │                                                 │
   ┌────────▼────────────────┐                  ┌─────────────▼─────────────┐
   │   TRACK A — DEPTH        │                  │   TRACK B — BREADTH        │
   │   (Code as flagship)     │                  │   (Cross-source as goal)   │
   │                          │                  │                            │
   │  Quality fix for the     │                  │  Connectors, ingestion,    │
   │  highest-fidelity source │                  │  cross-source entity       │
   │                          │                  │  resolution, source-aware  │
   │  → Wave 1: SQL/retrieval │                  │  salience, source-aware    │
   │    /caching/streaming    │                  │  permissions               │
   │  → Wave 2: SCIP/Joern/   │                  │                            │
   │    Stack Graphs/blast    │                  │  → Wave B1: Notion + Slack │
   │    radius v2             │                  │    connectors + ingest    │
   │  → Wave 3: spec dec/     │                  │  → Wave B2: cross-source   │
   │    embedding FT          │                  │    entity resolution +    │
   │                          │                  │    source-aware salience  │
   │  ↓ ANCHOR ↓              │                  │  → Wave B3: persona       │
   │  Defensible code-memory  │                  │    answers spanning ALL    │
   │  product for seed pitch  │                  │    sources                 │
   └──────────────────────────┘                  └────────────────────────────┘
                                   │
                ┌──────────────────┴──────────────────┐
                │   SHARED SUBSTRATE                  │
                │   (per ADR-0083, ADR-0084)          │
                │                                     │
                │   - Entity catalog (cross-source)   │
                │   - Canonical card schema           │
                │   - Event-stream (ADR-0073)         │
                │   - Compression/expansion           │
                │   - Catalog evolution + audit       │
                └─────────────────────────────────────┘
```

**Both tracks share the same underlying memory substrate**. A Payer entity is the same entity whether mentioned in `PayerService.java` (Track A) or in a Salesforce account record or a Slack message in `#payer-ops` (Track B). The persona templates (ADR-0079) and per-company tuning (V2 Pillar 5) operate across both tracks.

---

## 4 — Track B (Breadth): what's already designed vs what's missing

### Already designed (ADRs exist; not yet shipped)

| ADR | Track-B relevance | Status |
|---|---|---|
| **ADR-0070** PRD + document ingestion (Notion, Confluence, Linear, Jira, PDF, Slack, Loom, email) | The breadth pipeline | Designed; T2 in priority order — needs to MOVE UP |
| **ADR-0073** Event-stream memory + materialized views (multi-source events) | The shared substrate | Designed; ship Phase 1 alongside Track A |
| **ADR-0079** Persona-aware query templates | Cross-source persona answers | Designed; in T1 of biggest-wins order |
| **ADR-0083** Catalog evolution + vertical packs | Per-vertical seeding for breadth | Designed; in T2 |
| **ADR-0084** Compression + expansion of domain graph | Holds at multi-source scale | Designed; in T2 |

### Named in pivot doc but NEVER WRITTEN

These were sketched in `STRATEGIC-PIVOT-COMPANY-WIDE-BRAIN.md` as post-Series-A but, given the user's reframe, **at least ADR-0076 needs to be designed now** since it's the substrate everything else stands on:

| ADR slot | What it covers | Urgency given the reframe |
|---|---|---|
| **ADR-0074** Domain-Entity-First Architecture | Entities are the universe; everything else is commentary | DESIGN NOW (foundational framing) |
| **ADR-0075** Multi-Source Connector Framework | One contract for any source (Slack, Salesforce, etc.); rate limiting; idempotency; backpressure | DESIGN NOW (enables T2 breadth work) |
| **ADR-0076** Cross-Source Entity Resolution | "Aetna" in code = "Aetna Inc." in Salesforce = "aetna" in Slack = ONE entity | DESIGN NOW (the actual hard problem) |
| **ADR-0077** Source-Aware Permissions | A Salesforce contract should not be visible to all queries by default | DESIGN NOW (enterprise blocker otherwise) |
| **ADR-0078** Cross-Source Salience & Recency | Confluence canonical vs Slack ephemeral; per-source baselines | DESIGN at start of Wave B (not yet) |

### Genuinely missing

These weren't even sketched. The reframe makes them necessary:

| Slot | What | Why it matters |
|---|---|---|
| **ADR-0086** Cross-Source Provenance & Citation | Citations span code/Slack/Notion/calls; UX shows source heterogeneity; legal/audit lineage | Without this, multi-source answers lose trust |
| **ADR-0087** Source Health & Freshness Monitoring | Connector lag, gap detection, "this answer based on data 14 days old" warnings | Without this, brain hallucinates from stale sources |
| **ADR-0088** Non-Code Extraction Quality Framework | Same quality discipline (golden sets, ECE, recall) applied to Slack/PRD/call extraction | Without this, breadth is "throw RAG at it" and quality collapses |

---

## 5 — Rebalanced Wave structure

Replace the current "Waves 1-3 mostly-code" sequence with a two-track parallel structure. **Both tracks run concurrently**; resourcing splits accordingly.

### Track A (Depth) — keep the V2 waves as written, slightly trimmed

V2 Wave 1 / 2 / 3 prompts remain valid. Two adjustments:

- **De-prioritize within Wave 2**: SCIP for Java only (not Python + TS in Wave 2; defer to Wave 3); Joern stays as research-grade nightly job (not blocking)
- **Cap Track A at 50-60% of engineering capacity** so Track B isn't starved

### Track B (Breadth) — new Wave structure

**Wave B1 — Bootstrap connector + cross-source substrate (4-6 weeks, parallel to Track-A Wave 1)**

Goal: prove the architecture extends. One real non-code connector + one real cross-source entity working end-to-end.

| Item | Effort | Why first |
|---|---|---|
| ADR-0074 (Domain-Entity-First) written | 0.5 week design | Sets the framing for everything below |
| ADR-0075 (Multi-Source Connector Framework) written + skeleton implemented | 1.5 weeks | Generic contract for all future connectors |
| ADR-0076 (Cross-Source Entity Resolution) written + Phase 1 implemented | 2 weeks | The hard problem; resolve at least 2 sources reliably |
| Notion connector built on ADR-0075 (ADR-0070 P1) | 2 weeks | First non-code source live |
| ADR-0073 P1 event-stream (M1 event store + M2 EntityState view) | 2 weeks | Shared substrate for cross-source |

**Wave B1 demo**: open frontend → ask "what did we decide about Aetna's PA latency in the last 60 days" → brain pulls evidence from BOTH Java code (PayerService) AND Notion PRD (PriorAuth-v3 doc) AND surfaces both with citations.

**Wave B2 — Slack/Confluence/Salesforce + multi-source persona answers (6-8 weeks, after B1)**

| Item | Effort | Why next |
|---|---|---|
| Slack connector (workspace-scoped channels) | 2 weeks | Highest-signal non-code source for most companies |
| Confluence connector | 1.5 weeks | Where canonical decisions often live |
| Salesforce connector (Account, Opportunity, Contact) | 2 weeks | Customer + Contract entities materialize |
| ADR-0077 (Source-Aware Permissions) implemented | 1.5 weeks | Salesforce makes permissions urgent |
| ADR-0078 (Cross-Source Salience & Recency) written + implemented | 1.5 weeks | Per-source baselines so Slack-noise doesn't drown PRD-canonical |
| ADR-0086 (Cross-Source Provenance) | 1 week | Citations span sources cleanly |
| Persona answers actually span sources (no longer code-only) | 2 weeks | The CS/CFO/CEO personas now work |

**Wave B2 demo**: CS persona — "why did customer HealthcareSystemX escalate last week" → answer traces from Slack incident channel → Salesforce contract → Notion runbook → code commit → all cited, one coherent narrative.

**Wave B3 — Call transcripts + email + cross-source reasoning at depth (10-12 weeks, post-Series-A bucket)**

| Item | Effort |
|---|---|
| Fireflies / Otter / Gong call-transcript connector | 2 weeks |
| Email connector (Gmail / Outlook) | 2 weeks |
| ADR-0087 Source Health & Freshness Monitoring | 1.5 weeks |
| ADR-0088 Non-Code Extraction Quality Framework | 2 weeks |
| Cross-source multi-hop reasoning (HippoRAG + LazyGraphRAG over heterogeneous graph) | 3 weeks |
| Per-persona evidence-budget tuning per source mix (extends ADR-0084 M5) | 1.5 weeks |

**Wave B3 demo**: CFO persona — "what is feature F costing us, and is that justified given customer revenue?" → answer spans code (build cost via engineering hours), call transcripts (customer Y said they'd pay 30% more), Salesforce (Y's contract value), payroll (loaded engineer rate), cloud bill (infra spend). One question, five sources, calibrated confidence per claim.

---

## 6 — Rebalanced seed-cycle priority (replaces TECHDEBT-BIGGEST-WINS-ORDER table)

Old Tier-1 (six items, all code-leaning):
1. P0 BUG BUNDLE
2. ADR-0061 P1 iterative exploration
3. ADR-0071 P1 frontend
4. ADR-0064 P1 privacy + audit
5. ADR-0079 P1 personas
6. ADR-0082 P1 drift

New Tier-1 (eight items — two breadth additions; keeps the demo working AND proves the architecture extends):

| # | Item | Track | Why included |
|---|---|---|---|
| 1 | P0 BUG BUNDLE | A | Demo blocked; non-negotiable |
| 2 | ADR-0071 P1 frontend | A | No UI no demo |
| 3 | ADR-0079 P1 personas (3 personas) | A+B | Multi-persona surface IS the pitch differentiator |
| 4 | ADR-0061 P1 iterative exploration | A | Every demo answer improves |
| 5 | ADR-0064 P1 privacy + audit minimum | A+B | Enterprise readiness for any source |
| 6 | ADR-0082 P1 drift entity | A | Cheap VP appeal |
| 7 | **NEW: ADR-0074/0075/0076 written + Notion connector (Wave B1)** | B | Proves architecture extends beyond code; Series-A-flywheel slide |
| 8 | **NEW: ADR-0073 P1 event-stream M1 + M2** | B (shared substrate) | Cross-source entities need this foundation |

Estimated additional cost: ~5-6 engineer-weeks (Wave B1 minus the parts that overlap with already-planned T1 items). Total seed-cycle envelope grows from ~9.5 ew to ~14-15 ew. Two engineers, ~7 calendar weeks instead of 5.

**This is a real tradeoff**. Adding Track B to the seed cycle costs ~2 calendar weeks. The win: the seed pitch's Series-A-flywheel slide is no longer hand-waving — it's demoable.

---

## 7 — The pitch story, reframed

The seed pitch deck has one slide that needs to change:

**Old (code-only)**: "We're the brain for engineering teams. Best code memory in the market."

**New (code-as-wedge)**: "We're the company brain. We start with code because it's the highest-fidelity company signal — and code memory is a $5B market we can win against Sourcegraph today. But the same architecture extends to Slack, Notion, calls, email, Salesforce — code is wedge 1 of 10. We have ONE non-code source (Notion) working today as proof; series-A unlocks the multi-source expansion to PM, CS, VP, CFO, CEO buyers."

The second framing reads to investors as "thesis-driven company with proof of architecture, not a code-tool company hoping to expand." That's worth 1.5-2× the valuation multiple per stage.

---

## 8 — What to write next (in order of urgency, given the reframe)

1. **ADR-0074 — Domain-Entity-First Architecture** (foundational framing; ~400 lines; 0.5 week)
2. **ADR-0075 — Multi-Source Connector Framework** (contract for all connectors; ~500 lines; 1 week for design + skeleton)
3. **ADR-0076 — Cross-Source Entity Resolution** (the hard problem; ~600 lines; 1.5-2 weeks for design + Phase 1 algorithm)
4. **SONNET-IMPLEMENTATION-PROMPT-V2-WAVE-B1.md** — Wave B1 master prompt (5 sub-sessions: ADR-0074/0075/0076 written + Notion connector + ADR-0073 P1 event-stream)
5. **Updated TECHDEBT-BIGGEST-WINS-ORDER** — restructure with the rebalanced 8-item T1
6. **Updated ARCHITECTURE-V2-QUALITY-AND-DOMAIN-TUNING** — rename to ARCHITECTURE-V2-CODE-WEDGE-COMPANY-BRAIN and add Track A / Track B explicitly throughout
7. **Eventually**: ADR-0077 (Source-Aware Permissions), ADR-0078 (Cross-Source Salience), ADR-0086 (Cross-Source Provenance), ADR-0087 (Source Health), ADR-0088 (Non-Code Extraction Quality)

Items 1-4 are seed-cycle work. Items 5-6 update existing docs. Item 7 is post-seed.

---

## TL;DR

1. **You're right** — V2 Waves 1-3 are ~75% code-specific. That mirrors the diagnosed bugs but under-honors the product vision.
2. **Code is the wedge, not the product.** The seed pitch is "best-in-class code memory" because it's defensible vs Sourcegraph 7.0 in 6 weeks. The product is the company brain.
3. **Restructure V2 as TWO TRACKS**: Track A (Depth — current Waves 1-3, slightly trimmed) and Track B (Breadth — new Waves B1-B3 covering connectors, cross-source entity resolution, source-aware salience/permissions/provenance, non-code extraction quality).
4. **Add 2 items to Tier-1 seed-cycle priority**: ADR-0074/0075/0076 written + Notion connector (Wave B1) AND ADR-0073 P1 event-stream foundation. Grows seed-cycle from 9.5 ew → ~14-15 ew (~2 extra calendar weeks).
5. **The seed-pitch flywheel slide becomes demoable** (one non-code source live) instead of hand-wavy.
6. **Next concrete writes** (on signal): ADR-0074, ADR-0075, ADR-0076, then Wave B1 implementation prompt. ~3-4 weeks of design work before Wave B1 implementation kicks off.
7. **Track A (code) doesn't go away** — it remains the seed-pitch anchor. It just shares the engineering budget instead of consuming all of it.

The pitch becomes: *"Company brain. Wedge into code because code is the highest-fidelity company signal. Same architecture extends to every source. Demoable proof: one non-code source live in week 4."*
