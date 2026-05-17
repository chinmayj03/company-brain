# Lean Plan — Reconciled with Existing Unshipped ADRs + Research-Grade Cuts

**Driver**: the user flagged (a) "there are old ADRs that haven't been implemented" and (b) "we can skip research-grade fine tuning if not necessary." Audit revealed five ADR-number COLLISIONS with parallel work the user authored that I didn't know about. This doc resolves the conflicts, folds in the user's unshipped ADRs, prunes the research-grade tier, and produces a leaner plan.

---

## 1 — What I missed (ADR-number collisions)

I had been planning ADRs 0072-0078 covering memory primitives / event-stream / domain-entity-first / connector framework / cross-source resolution / permissions / salience. Audit found the user already wrote ADRs with the same numbers covering DIFFERENT (and more concrete) work:

| ADR # | Mine (now to be renumbered) | User's existing (canonical) | Status |
|---|---|---|---|
| 0072 | General-Purpose Memory Primitives | **Frontend Product Completion APIs** (6 broken surfaces; minimal backend) | User's exists, Proposed, not shipped |
| 0073 | Event-Stream Memory Architecture | **Frontend Demo Live-Up** (kill mocks; wire brain) | User's exists, has branch `feature/adr-0073-frontend-liveup`, Proposed |
| 0074 | Domain-Entity-First Architecture | **Source Registry & Source-First Ingestion Pivot** | User's exists, Proposed |
| 0075 | Multi-Source Connector Framework | **UX Navigation & Product Surface Redesign** | User's exists, Proposed, has DESIGN-HANDOFF |
| 0076 | Cross-Source Entity Resolution | **Frontend Rendering & Library Architecture** (BlastRadius graph, Markdown, react-query, etc.) | User's exists, Proposed |

The user's ADRs 0072-0076 are **concrete, demo-blocking, product-completion work** dated 2026-05-17 (just written). They are MORE IMMEDIATELY VALUABLE than my abstract architecture plans because they make the demo actually work.

**Resolution**: my proposed ADRs renumber as follows:

| Was → Becomes | Title |
|---|---|
| ADR-0072 (mine) → **ADR-0089** | General-Purpose Memory Primitives |
| ADR-0073 (mine) → **ADR-0090** | Event-Stream Memory Architecture |
| ADR-0074 (mine) → **ADR-0091** | Domain-Entity-First Architecture |
| ADR-0075 (mine) → **ADR-0092** | Multi-Source Connector Framework |
| ADR-0076 (mine) → **ADR-0093** | Cross-Source Entity Resolution |
| ADR-0077 (mine) → **ADR-0094** | Source-Aware Permissions |
| ADR-0078 (mine) → **ADR-0095** | Cross-Source Salience & Recency |
| ADR-0085 (mine, no conflict) | Architecture V2 (stays as 0085) |
| ADR-0086, 0087, 0088 (mine, no conflicts) | stay as numbered |

Note: ADR-0075 (UX redesign user-authored) PARTIALLY overlaps with parts of my ADR-0092 (Multi-Source Connector Framework) because the user's source registry (0074-user) already defines connector kinds. **ADR-0092 should EXTEND 0074-user rather than replace it**.

Also: my ADR-0091 (Domain-Entity-First) is a FRAMING ADR; it doesn't conflict with anything code-wise. It can stay as-is.

---

## 2 — Old ADRs status audit (foundation 0001-0070)

The brain works in production today (modulo the diagnosed quality bugs), so the foundation ADRs are largely shipped. Specific unshipped/partial ones worth noting:

| ADR | Title | Likely status | Action |
|---|---|---|---|
| 0005 | Confidence-scoring rubric | **Spec only, never implemented** (confirmed via E2E diagnosis: `query.py:567` is `bool(context)`) | Implicitly addressed by V2 Wave A1.4 verbalized confidence — close 0005 as superseded by ADR-0085 |
| 0007 | Drift detection v1 | Shipped basic check; superseded by ADR-0082 P1 (drift as first-class entity) | Mark 0007 as superseded by 0082 |
| 0015 | Qdrant hybrid retriever | Shipped but the "hybrid" is dense-only — superseded by V2 Wave A1.2 BM25+dense+RRF+BGE | Mark 0015 as superseded by ADR-0085 |
| 0029 | Pipeline reliability hardening | Likely partial; check ship-log | Verify; if gaps, add a P0-bug-bundle followup |
| 0043 | Query-time quality and prompts | Likely shipped; V2 Wave A1.4 may further refine | Verify, no action |
| 0050 P5 | Drift detection (re-shipped) | Shipped | Closed by ADR-0082 |
| 0052 P5-P7 | MCP server, feature adoption | Shipped per summary | Closed |
| 0062 | Ecosystem calibration packs | **PROPOSED-NOT-SHIPPED** — universality fix for Java/Spring monoculture | Defer to post-customer-trial; not seed-blocking |
| 0063 | Convention inference augmentation | **PROPOSED-NOT-SHIPPED** | Defer — only matters for messy-repo handling |
| 0065 | Multi-graph RRF fusion | **PROPOSED-NOT-SHIPPED** | **Superseded by V2 Wave A1.2** (mark closed) |
| 0066 | Experiential memory tier | **PROPOSED-NOT-SHIPPED** | Move to Wave A2 (needed by per-workspace tuning) |
| 0067 | Brain evolution background process | **PROPOSED-NOT-SHIPPED** | Move to Wave A2 (needed by glossary/few-shot weekly refresh) |
| 0068 | Native agent framework integrations | **PROPOSED-NOT-SHIPPED** | Distribution play; keep deferred (post-Series-A) |
| 0069 | companybrain-lite SQLite distribution | **PROPOSED-NOT-SHIPPED** | Distribution; defer (post-Series-A) |
| 0070 | PRD + document ingestion | **PROPOSED-NOT-SHIPPED** | Partially overlaps with user's ADR-0074 (Source Registry) — fold into Wave B1 |
| 0072 (user) | Frontend Product Completion APIs | **PROPOSED-NOT-SHIPPED** | **Move into Tier-1 (demo blocker)** |
| 0073 (user) | Frontend Demo Live-Up | **PROPOSED-NOT-SHIPPED** (has branch) | **Move into Tier-1 (demo blocker; 4-6h work)** |
| 0074 (user) | Source Registry Pivot | **PROPOSED-NOT-SHIPPED** | **Move into Tier-1** (replaces multi-source connector framework planning) |
| 0075 (user) | UX Navigation Redesign | **PROPOSED-NOT-SHIPPED** | **Move into Tier-1 (demo polish blocker)** |
| 0076 (user) | Frontend Rendering & Library Architecture | **PROPOSED-NOT-SHIPPED** | **Move into Tier-1 (Ask uses dangerouslySetInnerHTML; react-query unused)** |

---

## 3 — Research-grade cuts (per user direction)

Wave A3 research-grade items that get CUT or made optional:

| Sub-session | Original status | New status | Rationale |
|---|---|---|---|
| A3.1 Speculative decoding | 2w / $40 | **DEFER / optional** | Real latency win, but Wave A2 alone likely brings P50 < 3.5s; only revisit if customers demand sub-2s P50 |
| A3.2 HippoRAG | 2w / $40 | **CUT** | LazyGraphRAG (Wave A2.5) already handles multi-hop well enough; HippoRAG is incremental |
| A3.3 AGSER | 1.5w / $30 | **CUT** | CoVe (Wave A2.6) is sufficient for hallucination cut at our quality bar |
| A3.4 Per-workspace embedding fine-tune | 3w / $60 | **CUT** | Research-grade, needs ≥1000 labels per workspace, marginal lift; per-workspace calibrator + LTR (Wave A2.7/8) deliver most of the workspace-tuning gain |
| A3.5 Persona auto-variants | 2.5w / $50 | **CUT** | Needs ≥3 months of M5 telemetry; ADR-0083 manual curation is sufficient until customer data justifies |

**Wave A3 net effect**: 5 sub-sessions → 1 (optional). Savings: ~10 engineer-weeks and ~$220.

Wave B3 trims:

| Sub-session | Status | Rationale |
|---|---|---|
| B3.5 Cross-source multi-hop reasoning | **Simplify** — use LazyGraphRAG only (no HippoRAG) | A3.2 is cut; LazyGraphRAG alone gets us to ~60% multi-hop accuracy which is good enough for Series-A demo |

---

## 4 — The Lean Plan (consolidated)

### Tier 1 — Seed-window MUST-HAVES (now 11 sub-sessions, was 6)

The user's frontend ADRs are NOW in Tier 1 because the demo doesn't work without them.

| # | Item | Track | ew | $ | Why T1 |
|---|---|---|---|---|---|
| T1.1 | P0 Bug Bundle | A | 1 | $20 | Demo broken |
| T1.2 | ADR-0061 P1 iterative exploration | A | 1 | $15 | Every answer improves |
| T1.3 | **NEW: ADR-0073 (user) Frontend Live-Up** (kill mocks, wire brain) | A | 0.5 | $15 | Already has branch; 4-6h |
| T1.4 | **NEW: ADR-0072 (user) Frontend Product Completion APIs** | A | 1.5 | $20 | 6 broken surfaces |
| T1.5 | **NEW: ADR-0074 (user) Source Registry Pivot** | A+B | 2 | $30 | Replaces ADR-0071 partially; multi-source plumbing |
| T1.6 | **NEW: ADR-0075 (user) UX Navigation Redesign** | A | 1.5 | $25 | Empty states, Add Source CTA, icons |
| T1.7 | **NEW: ADR-0076 (user) Frontend Rendering & Library** | A | 2 | $25 | BlastRadius real graph, Markdown render, react-query wired |
| T1.8 | ADR-0064 P1 privacy + audit | A+B | 2 | $25 | Enterprise readiness |
| T1.9 | ADR-0079 P1 persona templates (3 personas) | A+B | 2 | $30 | Differentiator |
| T1.10 | ADR-0082 P1 drift entity | A | 1.5 | $20 | Cheap VP appeal |
| T1.11 | ADR-0090 P1 event-stream (was 0073-mine; M1+M2) | B substrate | 2 | $50 | Shared substrate for cross-source |

**Tier 1 totals**: 17 engineer-weeks / $275 / ~8-9 calendar weeks with 2 engineers.

(Old Tier 1 was 9.5 ew / $140 / 5 weeks; new Tier 1 is +7.5 ew because we're absorbing the user's frontend completion work — but those screens are demo-blockers that have to ship anyway.)

### Track A Wave 1 — Code Quality Foundations (8 sub-sessions, unchanged, parallel to T1)

| # | Item | ew | $ |
|---|---|---|---|
| A1.1 | SQL Deep Extractor | 2 | $25 |
| A1.2 | Hybrid Retrieval (BM25+dense+RRF+BGE) | 2 | $20 |
| A1.3 | Anthropic Prompt Caching + GPTCache | 1 | $10 |
| A1.4 | Verbalized Confidence + Multi-Signal Aggregator | 1 | $15 |
| A1.5 | Streaming + Parallel Retrieval | 0.5 | $10 |
| A1.6 | Glossary Auto-Discovery | 1.5 | $20 |
| A1.7 | Few-Shot Bank | 1 | $15 |
| A1.8 | Quality Regression Harness | 0.5 | $10 |

Subtotal: 9.5 ew / $125.

### Track B Wave 1 — Breadth Foundations (REDUCED from 5 to 4 sub-sessions)

Because user's ADR-0074 (Source Registry) absorbs much of what my proposed ADR-0092 (connector framework) was going to do, Wave B1 shrinks.

| # | Item | ew | $ |
|---|---|---|---|
| B1.1 | ADR-0091 (was 0074-mine) Domain-Entity-First (framing ADR only) | 0.5 | $10 |
| B1.2 | ADR-0092 (was 0075-mine) Multi-Source Connector Framework — **scoped as extension of user's ADR-0074** | 1 | $15 |
| B1.3 | ADR-0093 (was 0076-mine) Cross-Source Entity Resolution P1 | 2 | $40 |
| B1.4 | Notion connector (first real connector built on user's 0074 + my 0092) | 2 | $40 |

Subtotal: 5.5 ew / $105.

(Old B1 was 8 ew / $165 — saved ~2.5 ew because the connector framework piggybacks on ADR-0074 user.)

### Track A Wave 2 — Deep Code (post-seed, unchanged from prior plan)

| # | Item | ew | $ |
|---|---|---|---|
| A2.1 | SCIP Indexers (Java only in Wave 2; Python+TS later) | 1.5 | $20 |
| A2.2 | Stack Graphs (Python + JS) | 1.5 | $20 |
| A2.3 | Joern CPG (critical paths) | 2 | $30 |
| A2.4 | Blast Radius V2 (annealed + dataflow) | 1.5 | $20 |
| A2.5 | LazyGraphRAG Layer | 2 | $25 |
| A2.6 | Chain-of-Verification (CoVe) | 1 | $20 |
| A2.7 | Per-Workspace Confidence Calibrator | 1.5 | $25 |
| A2.8 | Per-Workspace Reranker LTR | 2 | $30 |
| A2.9 | **NEW: ADR-0066 Experiential Memory + ADR-0067 Brain Evolution** (needed by 0066 for tuning store maintenance) | 1.5 | $25 |

Subtotal: 14.5 ew / $215. (Added 0066+0067 because A2.7/A2.8 actually require them.)

### Track B Wave 2 — Cross-Source Connectors + Permissions/Salience/Provenance (unchanged + my-ADR renumbers)

| # | Item | ew | $ |
|---|---|---|---|
| B2.1 | Slack Connector | 2 | $40 |
| B2.2 | Confluence Connector | 1.5 | $30 |
| B2.3 | Salesforce Connector | 2 | $40 |
| B2.4 | ADR-0094 (was 0077-mine) Source-Aware Permissions | 1.5 | $30 |
| B2.5 | ADR-0095 (was 0078-mine) Cross-Source Salience | 1.5 | $25 |
| B2.6 | ADR-0086 Cross-Source Provenance | 1 | $20 |
| B2.7 | Persona Answers Span Sources | 2 | $40 |

Subtotal: 11.5 ew / $225.

### Track A Wave 3 — CUT (research-grade)

Original: 5 sub-sessions, 11 ew, $220.
Pruned: **0 sub-sessions required.** A3.1 speculative decoding kept as "optional, only if customer demands < 2s P50."

### Track B Wave 3 — Slimmed (calls + email + health + quality; NO research-grade multi-hop)

| # | Item | ew | $ |
|---|---|---|---|
| B3.1 | Call-Transcript Connector | 2 | $40 |
| B3.2 | Email Connector | 2 | $40 |
| B3.3 | ADR-0087 Source Health & Freshness | 1.5 | $30 |
| B3.4 | ADR-0088 Non-Code Extraction Quality Framework | 2 | $40 |
| B3.5 | Cross-Source Multi-Hop (LazyGraphRAG only; no HippoRAG) | 2 | $40 |
| B3.6 | Per-Persona Source-Mix Budgets | 1.5 | $30 |

Subtotal: 11 ew / $220.

---

## 5 — Revised cost & wall-time envelope

| Phase | Sub-sessions | Engineer-weeks | Sonnet $ | Calendar weeks (2 engineers) |
|---|---|---|---|---|
| Tier 1 (seed) | 11 | 17 | $275 | ~8-9 |
| Track A Wave 1 | 8 | 9.5 | $125 | parallel with T1 |
| Track B Wave 1 | 4 | 5.5 | $105 | parallel with T1 |
| **GATE 1 — Seed PR to main** | | | | week 9 |
| Track A Wave 2 | 9 | 14.5 | $215 | weeks 10-17 |
| Track B Wave 2 | 7 | 11.5 | $225 | weeks 10-17 |
| **GATE 2 — Seed-to-Series-A PR to main** | | | | week 17 |
| Track A Wave 3 | 0 (cut) | 0 | $0 | — |
| Track B Wave 3 | 6 | 11 | $220 | weeks 18-24 |
| **GATE 3 — Series-A PR to main** | | | | week 24 |
| **TOTALS** | **45 → 45** but lean | **~69** | **~$1,165** | **~24 weeks (5.5 months)** |

Old plan: 75 ew / $1,310 / 26 weeks.
New plan: **69 ew / $1,165 / 24 weeks** with the SAME deliverables minus the research-grade tier — and now includes the user's demo-blocking frontend ADRs.

Savings: ~6 ew, ~$145, ~2 calendar weeks; plus a demo that actually works in Week 9 (vs Week 7 without user's 0072-0076 frontend work — but it would have been a demo on a half-broken UI).

---

## 6 — What to actually do this week

**Recommended sequence (parallel where independent)**:

```
Week 1   ── T1.1 P0 Bug Bundle (single engineer, blocks everything)
            └─ ON COMPLETION: ──>
Week 2-3 ── T1.3 ADR-0073 (user) Frontend Live-Up (4-6h, already-branched)
            T1.5 ADR-0074 (user) Source Registry Pivot (2 weeks)
            T1.4 ADR-0072 (user) Frontend Product Completion APIs (1.5w)
            A1.1 SQL Deep Extractor (parallel; 2w)
            A1.2 Hybrid Retrieval (parallel; 2w)
            
Week 4-5 ── T1.6 ADR-0075 (user) UX Redesign
            T1.7 ADR-0076 (user) Frontend Rendering & Library
            T1.2 ADR-0061 iterative exploration
            T1.8 ADR-0064 P1 privacy
            A1.3-A1.5 caching/streaming/confidence
            B1.1 ADR-0091 framing
            B1.2 ADR-0092 connector framework
            
Week 6-7 ── T1.9 ADR-0079 P1 personas
            T1.10 ADR-0082 P1 drift
            T1.11 ADR-0090 P1 event-stream
            A1.6-A1.8 glossary/few-shot/quality harness
            B1.3 ADR-0093 entity resolution
            B1.4 Notion connector
            
Week 8-9 ── Integration + acceptance + demo polish
            ════════════════════════════════════════════════
              GATE 1: SEED PR TO MAIN (release/v2-seed-window)
            ════════════════════════════════════════════════
```

---

## 7 — Wave A3 explicit cut rationale (so we don't second-guess)

Each cut item is here in case you want to revisit later:

- **A3.1 Speculative decoding**: Only revisit if customer says "P50 must be < 2s." Wave A2 baseline likely gets us to < 3.5s, which is acceptable for Series-A.
- **A3.2 HippoRAG**: LazyGraphRAG (Wave A2.5) provides multi-hop wins; HippoRAG is incremental. Cut unless quality eval shows multi-hop accuracy < 55%.
- **A3.3 AGSER**: CoVe (Wave A2.6) gives ~40% hallucination cut; AGSER would add ~15pp at significant complexity cost. Cut.
- **A3.4 Per-workspace embedding fine-tune**: Marginal lift; only meaningful with ≥1000 labels per workspace (so only the largest customers); per-workspace calibrator (A2.7) + LTR (A2.8) deliver most of the workspace-fit. Cut.
- **A3.5 Persona auto-variants**: ADR-0083 manual curation is sufficient until late Series-A. Cut.

If a Series-A customer specifically demands one, we can ramp it. Don't pre-build.

---

## 8 — TL;DR

1. **Five ADR-number collisions** with user's existing unshipped frontend/product-completion ADRs (0072-0076). My proposed ones renumber to **0089-0095**. User's ADRs are CANONICAL for 0072-0076.

2. **User's frontend ADRs join Tier 1** because they're demo-blocking. Tier 1 grows from 6 → 11 sub-sessions (~17 ew vs 9.5 ew). But this is unavoidable — without those, the demo isn't real.

3. **Wave A3 effectively eliminated** per "skip research-grade fine tuning":
   - A3.1 speculative decoding → optional
   - A3.2 HippoRAG → cut (LazyGraphRAG sufficient)
   - A3.3 AGSER → cut (CoVe sufficient)
   - A3.4 embedding fine-tune → cut (LTR+calibrator sufficient)
   - A3.5 persona auto-variants → cut (manual curation sufficient)
   - Savings: ~10 ew, ~$220

4. **Wave A2 gains 0066+0067** (experiential memory + brain evolution) because per-workspace tuning literally requires them. Was deferred to T3; now in A2.

5. **Wave B3 trim**: cross-source multi-hop uses LazyGraphRAG only (no HippoRAG dependency since A3.2 is cut). Acceptable for Series-A demo.

6. **Old foundation ADRs status**: 0001-0050 largely shipped; 0005/0007/0015/0065 explicitly **superseded** by ADR-0085. 0062/0063/0068/0069 stay deferred. 0066/0067/0070 move into the plan.

7. **Old SHIP-LOG.md doesn't exist** — recommend creating one as part of this consolidation work so we don't lose track of what's shipped vs designed.

8. **Net envelope**: 69 ew / $1,165 / ~24 calendar weeks (5.5 months) with 2 engineers — down from 75 ew / $1,310 / 26 weeks while now including the user's demo-blocking frontend work AND cutting research-grade items.

9. **What to do this week**: P0 BUG BUNDLE first; then in parallel kick off user's ADR-0073 frontend live-up (already branched), ADR-0074 source registry, A1.1 SQL deep extractor, A1.2 hybrid retrieval. Those are the highest-leverage items with dependencies cleared.
