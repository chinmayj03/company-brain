# Compression & Expansion for the Domain Knowledge Graph

**The user's observation** (correctly): once we move beyond code, entities get redundant, edges become implicit/unspecified, and the graph drifts. We need a **compression layer** (so the brain doesn't drown in raw evidence) and a **context expander** (so a query can re-fetch raw fidelity when needed).

This is the hardest problem in the company-wide pivot. Code has explicit edges (imports, calls, types); prose doesn't. The previous docs (PERSONA-DRIVEN-EXTRACTION, ENTITY-AND-QUERY-REFINEMENT-LOOP) assumed clean entities + clean edges; reality is messy entities + implicit edges drowning in prose.

---

## TL;DR

The brain needs a **two-state representation per entity**:

1. **Compressed canonical card** (always-loaded, ~500-2000 tokens): a curated summary of the entity — its role, key edges, recent state, top citations. This is what enters every persona query.
2. **Raw evidence pool** (lazy-loaded, ~MB-GB): every mention, source-tagged, time-stamped, with original verbatim text. The expander pulls from this on demand.

Plus three pipelines to keep the graph honest:

- **Entity Compactor** — dedup/merge/tombstone to fight redundancy and staleness
- **Implicit Edge Inferer** — extract `X impacts Y` / `X depends on Y` / `X discussed in context of Y` from prose, with confidence
- **Context Expander** — at query time, given a compressed card and the persona's question, decide what raw evidence to pull and re-rank

This becomes **ADR-0084** in the post-seed roadmap. It's the Series-A unlock for cross-source reasoning — without it, the brain's domain graph rots within 60 days of multi-source ingestion.

---

## Why code-style graphs don't work for prose

| | Code | Prose (Slack/PRD/calls/email) |
|---|---|---|
| **Edge sources** | Imports, calls, types, inheritance — explicit | "X impacts Y" buried in sentences — implicit |
| **Entity boundaries** | Class/function names, file paths — unambiguous | "the Payer" / "Aetna" / "our biggest customer" — coreferent |
| **Update signal** | Git commit — atomic | Slack message at 2am — granular but messy |
| **Staleness signal** | Code deleted = explicit | Topic stops being mentioned = implicit |
| **Dedup signal** | Identical AST hash = duplicate | "PriorAuth" vs "prior_auth" vs "PA" = same? |
| **Edge cardinality** | Bounded (function has ~20 callees) | Unbounded (a Payer is mentioned in 50K Slack messages) |

The brain can't treat domain entities like code entities. **Code-graph thinking breaks at the seams** — that's what the user is calling out.

---

## The two-state representation

Every entity in the domain graph exists in two parallel forms:

### Form 1 — Compressed Canonical Card (always loaded)

A structured ~500-2000 token summary of the entity. This is what the persona templates pull when assembling a query. It's the brain's "working memory" of the entity.

```yaml
entity: Payer:Aetna
canonical_card:
  identity:
    canonical_name: "Aetna"
    aliases: [aetna, AET, Aetna Inc., Aetna Health]
    type: Payer
    confidence: 0.94
    last_validated: 2026-05-10
    sources_seen_in: [code:5, prd:3, salesforce:1, slack:127, calls:18, email:34]

  role_summary: |
    Tier-1 commercial Payer. Integrated since 2024-Q3 via X12 837P. Handles ~18% of
    inbound claims volume. Known issues with Prior Auth latency.
    [auto-generated from top 50 mentions; refreshed weekly]

  key_edges:
    - {to: PriorAuth, type: has_quirk, confidence: 0.88, evidence_count: 14}
    - {to: ClaimSubmission, type: integrates_via, confidence: 0.96, evidence_count: 47}
    - {to: customer:HealthcareSystemX, type: serves, confidence: 0.81, evidence_count: 8}
    - {to: incident:I-2026-04-02, type: caused_by, confidence: 0.74, evidence_count: 3}

  recent_state:
    last_30_days_mentions: 23
    trend: stable
    open_issues: [PA-latency-spike-may-2026]
    recent_decisions: [adr:0061-aetna-retry-policy]

  salience: 0.78  # high — frequently queried, recent activity
  storage_tier: hot

  top_citations:
    - {source: prd, doc_id: PRD-2024-Q3-aetna-launch, anchor: §3.2}
    - {source: slack, channel: #payer-ops, ts: 2026-04-15, msg_id: 12345}
    - {source: code, file: payer_adapters/aetna.py, line: 47}
```

The card is **derived**, not authoritative. It's regenerated periodically from the underlying evidence pool. It's compressed enough to fit dozens of entity cards into a single LLM context window.

### Form 2 — Raw Evidence Pool (lazy-loaded)

Every original mention, kept in append-only event store (per ADR-0073). Indexed by entity, time, source, salience. Never enters a prompt directly — only via expander.

```
evidence_pool[entity:Payer:Aetna]:
  - {ts: 2024-08-12, source: prd, full_text: "...", salience: 0.92}
  - {ts: 2024-09-03, source: slack, full_text: "...", salience: 0.31}
  ... (hundreds to thousands of records)
  - {ts: 2026-05-12, source: call, full_text: "...", salience: 0.74}
```

---

## The three pipelines

### Pipeline 1 — Entity Compactor

**Job**: keep the entity catalog clean. Fight redundancy. Mark staleness. Merge aliases. Tombstone the dead.

Tasks (all run in ADR-0067 background process):

**A. Alias clustering**: nightly job runs semantic similarity over discovered entity names + structural co-occurrence. Candidates with similarity > 0.92 + co-occurrence overlap > 0.7 → merge proposal. Auto-merge if confidence > 0.97; else queue for human review.

**B. Coreference resolution**: when a single source uses "the Payer", "Aetna", and "they" interchangeably, the inferer marks them coreferent and folds into one entity. Cross-source coreference (Salesforce account "Aetna Inc." = Slack "aetna" = code `PayerAetna`) is harder — handled by ADR-0076 (cross-source entity resolution).

**C. Staleness scoring**: every entity gets a `last_useful_at` timestamp updated on:
- New mention (any source)
- Query that touched it
- Edge update (an edge to/from it was modified)

Entities not touched in 90 days → demoted to `cold` tier. 180 days → `archived` (still searchable, doesn't load by default). 365 days → tombstoned (kept for audit/legal; excluded from active graph).

**D. Tombstoning, not deletion**: per "Forget Forgetting" research (Feb 2025), demote rather than delete. Keeps audit trail. Allows rehydration if entity becomes relevant again (rare but it happens — old customer comes back, deprecated feature gets revived).

### Pipeline 2 — Implicit Edge Inferer

**Job**: extract relational edges from prose with confidence scores. This is where most of the LLM cost lives.

**Edge types** (kept small — too many edge types kills usability):

| Edge type | Example trigger phrase |
|---|---|
| `impacts` | "the Aetna change broke claim submission" |
| `depends_on` | "PriorAuth requires the eligibility service" |
| `causes` | "the PA latency spike caused the Aetna escalation" |
| `discussed_in` | (any co-occurrence in same chunk) |
| `decision_about` | (PRD/ADR mentions entity as the subject) |
| `owned_by` | "Sarah owns the Aetna integration" |
| `serves` | "we onboarded HealthcareSystemX as an Aetna customer" |

**Extraction recipe**:
1. Chunk source (Slack thread, PRD section, call transcript paragraph)
2. Identify entity mentions (already done by inference layer)
3. If 2+ entities co-occur in chunk, run LLM edge classifier (small model — Haiku-class — to keep cost down)
4. Output: `(entity_A, edge_type, entity_B, confidence, evidence_chunk_id)`
5. Fold into edge index with confidence accumulation

**Confidence accumulation**: an edge inferred from one Slack message = 0.45 confidence. Same edge corroborated by a PRD = +0.30. Mentioned in 5 calls = +0.20. Code structure confirms = +0.40 (cap at 1.0). This is **ADR-0072 M1 Contradiction Detector** flipped — instead of resolving conflicts, accumulating support.

**Decay**: edges decay over time (per ADR-0072 salience decay). An edge inferred from a call 18 months ago, never re-corroborated, decays to near-zero. Visible but not used in retrieval.

### Pipeline 3 — Context Expander

**Job**: at query time, given (a) the persona's query, (b) the compressed cards already loaded, decide which raw evidence to pull and how much.

Architecture:

```
query → persona template match (ADR-0079) →
  identify involved entities → load their canonical cards →
  identify gaps:
    - "card says edge exists with conf 0.74; query is high-stakes; need verification"
      → pull top-N evidence supporting that edge
    - "card says recent_state stable; query asks 'why did X change last week'"
      → pull recent evidence (last 30 days) regardless of card
    - "card has no edge to entity Y; query mentions Y"
      → run edge inferer on raw evidence at query time
  → assemble context with citations →
  generate answer
```

**Evidence budget**: query persona declares budget (PM = 5K tokens evidence; CFO = 20K; Dev = 10K). Expander retrieves up to budget, ranked by:
- Salience (ADR-0072 M2)
- Recency (ADR-0073 M2 TimelineWindow)
- Source authority (ADR-0078 cross-source salience)
- Specific relevance to the query template

**Reciprocal rank fusion** across these signals (ADR-0065).

**Re-rank step**: pull 3× the budget; let a small LLM re-rank for query-specific relevance; trim to budget. This is the Mem0 retrieval pattern.

---

## How this changes the existing ADRs

### ADR-0073 (event-stream memory) — already aligned

The event store is the raw evidence pool. M2 materialized views (EntityState, etc.) are essentially what the canonical card is. ADR-0084 formalizes the card schema and the compactor/expander pipelines.

### ADR-0072 (general-purpose memory primitives) — extends naturally

M1 Contradiction Detector becomes the foundation for confidence accumulation. M2 Salience Scorer drives storage tiering. M3 Forgetting Policies drive tombstoning. M5 Memory Distraction Guard becomes a sanity check on the expander (don't pull evidence that distracts).

### ADR-0076 (cross-source entity resolution, in pivot doc) — becomes a sub-component

Coreference at the cross-source level (Aetna in Salesforce = aetna in Slack = PayerAetna in code) is one specific case of the Compactor's alias-clustering job.

### ADR-0083 (catalog evolution & curation, just proposed) — converges

Catalog evolution and entity compaction are basically the same pipeline at different scopes. May want to merge into a single ADR with two views, or split as: 0083 = catalog/curation (admin-facing); 0084 = compaction/expansion (runtime-facing).

---

## Phased implementation (be honest about what's hard)

### Phase 1 — Cheap wins (seed → end of seed cycle)

**What**: alias clustering with high-precision rules; obvious dedup; tombstones for entities not touched in 180 days; canonical card schema (even if cards are stub-quality at first).

**Cost**: 1.5-2 weeks. Mostly engineering, low novel research.

**Value**: graph stays clean for the demo period. Card schema unblocks ADR-0079 (persona templates) since templates need a stable shape for entity references.

### Phase 2 — Moderate complexity (seed-to-Series-A)

**What**: implicit edge inference at ingest time; confidence accumulation; basic context expander pulling top-K evidence per persona; storage tiering (hot/cold/archived).

**Cost**: 4-6 weeks. Novel work in edge classifier prompt engineering; cost optimization on LLM calls (batch inference); evidence ranking.

**Value**: domain graph holds up at multi-source scale. Persona queries get fidelity from real evidence, not just summaries. This is the demo for "we're the brain that reasons over your whole company."

### Phase 3 — Research-grade (Series-A and after)

**What**: adaptive compression ratios per entity (hot entities get richer cards; cold get tighter); query-time edge inference (when card edges are insufficient); multi-hop evidence retrieval (follow causal chains across N entities); per-persona compression style.

**Cost**: 2-3 months. This is where the real research lives — graph compression, retrieval-augmented reasoning, multi-hop QA. Either we hire a research engineer or we partner / open-source / use vendor.

**Value**: this is the moat. By this point competitors (Glean, Notion AI) are still doing per-doc RAG; we're doing graph-aware compressed reasoning. Series-A defensibility lives here.

---

## What's hard (be honest)

**1. Entity merging at scale is brittle.** False merges destroy data ("we merged customer Acme Inc with Acme LLC — they're different companies; an entire revenue stream got mis-attributed"). Need merge proposals to go through human review for any high-salience entity, automatic merges only for low-stakes cases, and full rollback (snapshot + restore).

**2. Edge inference is noisy.** "Aetna broke claim submission" might mean the integration broke, the API broke, a specific endpoint broke, or an engineer's PR broke something the day after Aetna had an outage and the human conflated them. Need edge confidence to be probabilistic, queryable, and citable. Never present an edge as fact without source.

**3. Context expansion is a budget problem.** Pull too little → hallucination via under-specification. Pull too much → distraction (saliency fragility, NeurIPS 2025: 90% misattribution at 10K+ tokens). Per-persona budget tuning is real engineering work.

**4. Compression is lossy.** A canonical card cannot represent everything. Some queries (forensic CS investigations, audit-grade compliance answers) MUST go to raw evidence. Persona templates need a "raw evidence required" flag.

**5. The "two-state" architecture means consistency is hard.** Cards drift from underlying evidence between regenerations. Need a stale-card detector (when N new mentions arrive, regenerate card) and a "card-stale" warning surfaced in answers ("this card was last regenerated 14 days ago; recent evidence may not be reflected").

These are real, expensive problems. Most are solvable with effort. **None are blockers for the seed pitch** — they're Series-A engineering.

---

## How this maps to the pitch

**Seed pitch**: "We have an entity graph for your code, and we're building toward your whole company."

**Pitch with compression-expansion (post-Series-A)**: "Glean answers questions by retrieving documents and asking an LLM to summarize. We answer questions by maintaining a compressed knowledge graph of your company's entities — Customers, Features, Decisions, Incidents, Engineers — and expanding to raw evidence only when the query needs it. This means our answers stay consistent across queries, surface causal chains other tools miss, and don't break when your Slack archive grows by 10× in 12 months. Every quarter your brain compresses better and reasons deeper."

The technical narrative (compression + expansion + graph reasoning) is the same one Palantir has used to defend $90B+ market cap for a decade. It's the "we built infrastructure, not a wrapper" story.

---

## TL;DR for the founder

1. **You're right that domain knowledge breaks code-graph assumptions.** Edges are implicit, entities are coreferent, signal drowns in prose.

2. **Two-state representation per entity**: compressed canonical card (~500-2000 tokens, always loaded) + raw evidence pool (lazy-loaded). Cards enter prompts; expander pulls evidence on demand.

3. **Three pipelines**: Compactor (dedup/merge/tombstone), Edge Inferer (implicit edges with confidence), Context Expander (query-time evidence retrieval within a budget).

4. **This becomes ADR-0084.** Sits alongside ADR-0083 (catalog/curation) — they share state. Could be one ADR; cleaner as two.

5. **Three-phase build**: Phase 1 (seed cycle, 2 weeks, cheap wins). Phase 2 (seed-to-Series-A, 4-6 weeks, real value). Phase 3 (Series-A+, 2-3 months, research-grade — the moat).

6. **Hard parts are real**: false merges, noisy edges, expansion-budget tuning, lossy compression, two-state consistency. None are seed blockers; all are Series-A engineering.

7. **The pitch unlock**: "Glean does per-document RAG. We maintain a compressed knowledge graph of your company entities and expand to raw evidence only when the query needs it. Reasoning vs search. Different category." This is the Palantir-style moat narrative.

8. **Recommend writing now**: ADR-0083 (catalog/curation) + ADR-0084 (compression/expansion) as a paired set. Both designable in seed window; Phase 1 of each implementable before Series-A close. I can draft on signal.
