# ContextDB Analysis — Competitive Brief + Integration Plan

**Subject:** [ContextDB](https://github.com/atomsai/contextdb) — open-source Python lib by Gaurav Sharma (saaslabs.co), Apache 2.0, on PyPI as `pycontextdb` v0.1.1, ~4,300 LOC, 82 tests, mypy strict.
**Tagline:** *"The unified context layer for AI agents — replace Pinecone + Redis + Postgres + glue code with one system that understands memory."*
**Their positioning:** *"Databricks Lakebase gives agents a hard drive. ContextDB gives agents a brain."*
**Date:** 2026-05-11
**Method:** read the actual `contextdb/` package end-to-end (5 modules, 4 sub-packages, full architecture doc).

---

## TL;DR

**Are they a competitor?** Adjacent, not direct. They sell **agent memory infrastructure** (Python lib for any agent builder); we sell **codebase context infrastructure** (multi-service platform for engineering orgs). Different buyer, different ICP, different pricing model.

**Is their architecture better than ours?** In several specific places, **yes** — they're cleaner, more orthogonal, and the framing is sharper. They have ideas we should steal. Specifically: multi-graph retrieval with RRF fusion, the working/factual/experiential memory split, PII as a layer not an afterthought, the dynamics layer (formation/evolution/retrieval), and SQLite-default for low friction.

**Where we win:** code-specific entity types (50-edge taxonomy, 28-field BusinessContext, jOOQ bindings, schema awareness, blast radius), enterprise multi-tenancy (RBAC, audit, data residency), git-ownership temporal model, the agentic-harness extraction layer. Our moat is **vertical depth**; theirs is **horizontal generality**.

**Strategic risk:** if ContextDB adds a "code memory plugin" they could land in our space cheaply. Mitigation: the depth of code-specific extraction (ADR-0058 schema awareness, ADR-0055 cross-file pass) is 9-12 months of work; not easy to bolt on.

**Strategic opportunity:** their integrations layer (langchain / crewai / autogen / openai_tools) is the GTM playbook for the AI Agent Substrate (Product 4 in PRODUCT-VISION). They've already shipped to that market. We can sell complementarity, not competition: *"ContextDB for agent runtime memory; company-brain for codebase memory; both query each other via MCP."*

---

## Their architecture (verified by reading the code)

```
┌───────────────────────────────────────────────────┐
│ Client: contextdb.init()                          │
├───────────────────────────────────────────────────┤
│ Memory APIs                                       │
│   factual.py       — long-term facts              │
│   experiential.py  — trajectories, reflections    │
│   working.py       — session-scoped, FIFO evict   │
├───────────────────────────────────────────────────┤
│ Dynamics                                          │
│   formation.py    — conversation → memories       │
│   evolution.py    — auto-link, consolidate, prune │
│   retrieval.py    — query → multi-graph ranking   │
├───────────────────────────────────────────────────┤
│ Graphs (over the same memories table)             │
│   semantic.py     — embedding similarity          │
│   temporal.py     — recency / time-window queries │
│   causal.py       — cause→effect chains           │
│   entity.py       — person/place/thing biographies│
├───────────────────────────────────────────────────┤
│ Store                                             │
│   sqlite_store.py — default (zero deps)           │
│   vector_index.py — NumPy / FAISS optional        │
│                     Postgres + pgvector optional   │
└───────────────────────────────────────────────────┘
       Privacy: PII detection, typed TTLs, hash-chained audit
       Agents: rl_manager.py (RL-trained ADD/UPDATE/DELETE/NOOP)
       Integrations: langchain.py, crewai.py, autogen.py, openai_tools.py
```

**4,274 lines of Python.** That's the entire system — and it's shipped, tested, benchmarked. For comparison, our `companybrain/` package is ~30,000 LOC.

### Their numbers

- **Write throughput**: 1,900+ memories/sec
- **Search latency at 1K**: p50 3.4ms, p95 4.5ms
- **Search latency at 5K**: p50 3.9ms, p95 5.0ms
- **Vector search 10K × 1,536d**: p50 0.8ms, p95 1.0ms
- **PII detection**: 100,000+ texts/sec
- **Default deps**: SQLite + NumPy. Optional: FAISS, Postgres, Neo4j, Redis, sentence-transformers, torch+trl (for RL)

For context: our `/query` is 5-10s end-to-end. Their search is 5ms. Different operation (we run Sonnet, they don't), but it's worth being honest about the gap in the underlying retrieval layer.

---

## Side-by-side: where we differ structurally

| Concern | ContextDB | Company-Brain | Who's better and why |
|---|---|---|---|
| **What it stores** | Generic agent memories: text + metadata + embeddings | Code entities: Method, Class, ApiEndpoint, DatabaseColumn, etc. (50-edge taxonomy) | Brain (for code-context); CDB (for general agent memory) |
| **Storage layers** | 1 (SQLite default; Postgres / FAISS optional) | 5 (Postgres + Neo4j + Qdrant + Redis + S3 + JSON brain) | CDB cleaner; brain has accumulated complexity |
| **Retrieval** | 4 graphs fused via RRF (semantic + temporal + causal + entity) | Hybrid search (BM25 + Qdrant) + Neo4j traversal + JSON brain reads, NOT fused via RRF | **CDB sharper.** RRF fusion is something we should steal |
| **Memory tiers** | Working / Factual / Experiential — orthogonal lifetimes | T0 / T1 / T2 zones — orthogonal abstraction levels | **Different framings; both valid.** Their EXPERIENTIAL has no equivalent in ours and is a great idea |
| **Lifecycle** | Formation → Evolution → Retrieval pipelines, with consolidation + pruning | Extraction → Storage → Query, with no automatic consolidation/pruning | **CDB sharper.** Their `evolution.py` is a real pattern we don't have |
| **Privacy** | PII detection BEFORE embedder; typed TTLs; hash-chained audit log | None today (some logging; no PII scrubbing; no audit chain) | **CDB ships, we don't** |
| **RL memory manager** | `rl_manager.py` decides ADD/UPDATE/DELETE/NOOP for new info | Deterministic rules (per-source precedence in ADR-0063 M3) | CDB is more sophisticated; ours is more auditable |
| **Concurrency model** | async/await throughout, 1900 writes/sec | async + Postgres + queue workers; throughput unmeasured | CDB has measured numbers; we don't |
| **Integrations** | langchain, crewai, autogen, openai_tools — first-class | MCP server (10 tools); no native langchain/crewai/autogen | **CDB has GTM advantage**: agent ecosystems already use them |
| **Multi-tenancy** | Single user_id (no org → workspace hierarchy) | workspace_id everywhere; org/RBAC planned (ADR-0063 area) | **Brain stronger** for enterprise |
| **Distribution** | Pip install one package | Docker compose + Spring Boot + FastAPI + Bun + Vite + 5 stores | **CDB radically simpler** to deploy |
| **Pricing implication** | Per-seat / per-API call / open-source freemium | Per-org enterprise license $50-200K | Different markets entirely |
| **Code-specific intelligence** | Generic text memory; treats code as text | Tree-sitter, structural pre-pass, jOOQ bindings, schema awareness, blast radius, 28-field BusinessContext, call chains | **Brain is dramatically deeper for code** |

---

## Six things from contextdb worth incorporating into the brain

### #1 — Multi-graph retrieval with RRF fusion (the keystone idea)

**What they do**: 4 graphs (semantic, temporal, causal, entity) each produce a ranking; fuse with Reciprocal Rank Fusion (k=60).

**Where it maps for us**: today our query path runs hybrid BM25 + Qdrant separately, plus Neo4j traversal, plus JSON brain reads. We never FUSE the rankings — we use one or the other based on heuristics.

**Proposed**: `assembly/multi_graph_retrieval.py` that produces rankings from:
- **Semantic** (Qdrant embeddings — already exist)
- **Structural** (Neo4j graph: callers, callees, depends-on — already exist)
- **Temporal** (git-ownership recency — coming in ADR-0059)
- **Domain** (REPRESENTS edges from DomainEntity inference — coming in ADR-0055/0059)
- **Cross-cutting** (Pattern + SharedInvariant edges — coming in ADR-0055)

Then fuse via RRF before passing to SmartZoneAssembler. The zone gets richer signal than any single graph could provide.

**Effort**: 2 days. Pure addition; no breaking changes.

### #2 — The Working / Factual / Experiential memory split

**What they do**: three memory APIs with different lifetimes:
- `working` — session-scoped, FIFO evict, token-budget paged
- `factual` — long-term static facts
- `experiential` — trajectories, reflections, "what worked"

**Where it maps for us**: we have T0/T1/T2 zones (orthogonal abstraction levels) but NOT orthogonal LIFETIMES. We don't have an "experiential" tier at all — and that's a brilliant pattern: when the brain runs against the same repo monthly, it should accumulate "what answers worked" / "what extractions were corrected" / "what the customer asked next time" as a learnable memory.

**Proposed**: add an `ExperientialMemory` to the brain. Stores:
- Past `/query` (question, answer, confidence, user_feedback) tuples
- `KnowledgeConflict` resolutions
- Verifier-correction outcomes
- Extracted-pattern instances that turned out to be useful (or not)

Used at query time as a third T1/T2 tier alongside structural + temporal context.

**Effort**: 3 days. New table, new write path, new read path.

### #3 — PII detection + typed TTLs + hash-chained audit (the compliance moat)

**What they do**: PII detection runs BEFORE the embedder. Typed TTLs (different retention by data class). Audit log hash-chains every read/write/delete (Merkle-chain style — tamper-evident).

**Where it maps for us**: PRODUCT-VISION calls out compliance (Product 3 — SOC2/AI Act/HIPAA evidence packs) as the highest-margin product. We can't ship that without these primitives.

**Proposed**: ADR-0064 — `Privacy & Audit Layer`. Lift their pattern wholesale:
- `privacy/pii_detector.py` — runs on extracted entities; flags fields containing emails / SSNs / API keys / credit cards. (Critical for the brain because we extract from source code which often has hardcoded secrets.)
- `privacy/retention_manager.py` — typed TTLs per entity class (e.g., test data 30 days, production data 7 years).
- `audit/hash_chain.py` — every brain write produces an audit event with `prev_hash + this_event_hash`; chain is tamper-evident.

**Effort**: 4 days. Net-new ADR. Critical for enterprise sale + compliance.

### #4 — The `dynamics/evolution.py` pattern (auto-link, consolidate, prune)

**What they do**: a background process continuously:
- **Auto-links** new memories into the right graphs (semantic neighbours, temporal cluster, etc.)
- **Consolidates** dense semantic clusters into summaries (5 similar memories → 1 summary memory)
- **Prunes** stale / redundant memories per policy

**Where it maps for us**: today our brain accumulates entities forever. Re-running extraction overwrites in place but never CONSOLIDATES "this method had 5 different name versions over 6 months". Brain bloat is coming.

**Proposed**: `pipeline/brain_evolution.py` — runs as a scheduled job (not in the hot path):
- **Auto-link**: nightly, recompute embeddings for entities created in the last 24h; add SIMILAR_TO edges where cosine > threshold.
- **Consolidate**: if 3+ Pattern entities (from ADR-0055) describe the same shape, merge into one with `instance_count` incremented.
- **Prune**: entities with `verified="hallucinated"` AND no inbound edges AND no `human_brain_md` provenance — drop after 30 days.

**Effort**: 2 days. Scheduled task; uses ADR-0052 P6 scheduler primitive.

### #5 — SQLite-default for radically lower friction

**What they do**: zero-deps default. `pip install pycontextdb`, `import contextdb`, three lines, working.

**Where this matters**: our setup is Docker compose + Spring Boot + FastAPI + Bun + Vite + 5 stores. **It's a barrier to evaluation.** A developer who wants to try us out has a 30-minute infra setup before they see anything.

**Proposed**: a `companybrain-lite` distribution that:
- Single Python package (`pip install companybrain-lite`)
- SQLite for nodes/edges (instead of Postgres + Neo4j)
- DuckDB for vector search (instead of Qdrant)
- No JVM, no Bun, no Docker, no Spring backend
- Loses: multi-tenancy, the agentic harness, scale beyond ~10K entities
- Wins: 5-minute eval, "kicks the tires" experience, viral growth on Twitter/HN

**Strategic implication**: this is how you go top-down (enterprise) AND bottom-up (developer eval). ContextDB is purely bottom-up; we add bottom-up.

**Effort**: 1-2 weeks. Real work but high-leverage for developer adoption.

### #6 — Native integrations (langchain, crewai, autogen, openai_tools)

**What they do**: ship a Python adapter per major agent framework. CrewAI's memory module can `pip install pycontextdb` and use it transparently.

**Where it maps for us**: we ship MCP. MCP is the right long-term API, but in 2026 most agent frameworks DON'T speak MCP yet — they have their own memory abstractions. To reach AI-vendor platform teams (Persona 2 from PRODUCT-VISION), we need to land in their existing surface area.

**Proposed**: `companybrain-integrations/` — sibling package with:
- `from companybrain.langchain import BrainMemory` — drop-in `langchain.memory.BaseMemory` subclass
- `from companybrain.crewai import BrainMemory` — same shape for CrewAI
- `from companybrain.autogen import BrainMemory` — for AutoGen
- These ALL talk to the brain via MCP under the hood; the integration layer is just a shim

**Effort**: 1 day per integration × 4 = 4 days. Massive GTM lift for Product 4 (AI Agent Substrate).

---

## Where we already win (don't underweight this)

ContextDB is brilliant for **general agent memory**. It is **not designed for code understanding**. Things we have they would need 9-12 months to bolt on:

1. **50-edge taxonomy specific to code** (CALLS, READS_COLUMN, EXTENDS, IMPLEMENTS, INSTANTIATES, …). Their entity graph is biographies of "Alice" — useful but generic.
2. **28-field BusinessContext** including engineering-specific fields (idempotent, transaction_mode, security_class, anti-patterns). They have generic memory metadata.
3. **Schema awareness** (ADR-0058 — DDL parsing, jOOQ bindings, OpenAPI cross-edges). Zero analog in CDB.
4. **Blast radius / call chain reasoning**. Their causal graph is "X caused Y" reflections; ours is "renaming column X breaks methods Y, Z, W with line citations".
5. **Cross-file cross-cutting pass** (ADR-0055 — Pattern, SharedInvariant, anti-pattern detection). They have entity biographies; we have codebase-level conventions.
6. **Tree-sitter chunked extraction at scale** (ADR-0044/0047/0050 — handles 30+ method classes, big-repo bisection recovery). They store text memories; chunking is unnecessary for their use case.
7. **Multi-tenant org → workspace → repo → branch hierarchy** with proper RBAC (planned). They're single-user.
8. **Git-ownership temporal model** (ADR-0059 — bus_factor RiskAlerts, ownership rollups). Their temporal graph is "when was this memory created"; ours is "who's been writing this and what's the bus factor".
9. **Specialised agentic harness** (ADR-0051) — sub-agents, skills, hooks, permissions. Their RL manager is one model; we have an architecture.
10. **Calibration packs per language ecosystem** (ADR-0062). They're framework-agnostic generic memory; we have framework-specific code intelligence.

**Vertical depth vs horizontal generality.** This is the structural difference and our defensibility.

---

## Strategic positioning — competitive vs complementary

### Frame 1: Competitive (NOT recommended)

If we tried to compete head-on as "general agent memory":
- We'd lose. They have 6+ months head start, real benchmarks, an open-source community, integrations shipped.
- Their architecture is cleaner than ours for the GENERIC use case.
- They'd outrun us on the dev-evangelism game (single-package install, Twitter-friendly).

### Frame 2: Complementary (RECOMMENDED)

Position as **"the code-specific memory layer; works alongside ContextDB for agent memory":**

> *"ContextDB is the runtime memory for what your agent has done. Company Brain is the persistent memory of what your codebase IS. They solve different problems and compose: your agent's working memory points at the brain via MCP for code understanding; the brain's `query_brain_other_repos` learns from accumulated context across the customer base."*

Tactical moves:
1. **Public collaboration**: blog post co-authored with Gaurav comparing the two. Sends a "we play nice" signal to the agent community.
2. **MCP bridge**: ship `companybrain-contextdb` adapter that lets a ContextDB user query the brain via the brain's MCP server.
3. **Joint demo**: a CrewAI agent that uses ContextDB for memory + brain for code questions. One slide in our deck.
4. **Differentiation copy**: every comparison in our deck explicitly says *"ContextDB is great at general agent memory. We are great at code-specific context. Use both."*

This positioning is way safer than "ContextDB is our competitor" — they're an established open-source player with developer mindshare; punching at them loses goodwill we need from the agent community.

### Frame 3: Acquisition target / acqui-hire (long shot, worth considering)

If they're bootstrapped (looks like it — solo maintainer "Gaurav Sharma, saaslabs.co"), we could acquire-and-merge in a Series A or B timeframe. They become our agent-memory product line; we become their go-to-market and enterprise wedge. **Don't do this now**; revisit in 12-18 months if both are still independent.

---

## Sales/competitive intelligence — where we WIN against them in pitches

If a prospect mentions ContextDB:

| If they say… | Our response |
|---|---|
| "We're using ContextDB for agent memory; do we still need you?" | "Use both — different layers. ContextDB stores what your agent learned; we explain what your codebase IS. Our MCP server plugs into ContextDB-backed agents; we feed CDB the structured code understanding it doesn't compute." |
| "ContextDB is open-source; you're proprietary" | "Yes — we're enterprise-shaped (multi-tenant, audit, SSO, data residency, compliance); ContextDB is single-user library. Different markets. We're free for ≤10 engineers (companybrain-lite); enterprise license at scale." (after we ship lite per Section #5) |
| "ContextDB is faster — 5ms search" | "Apples and oranges. They retrieve text memories; we synthesise multi-hop code reasoning with citations. Our synthesis is 5-10s end-to-end (Sonnet-grade answer); their retrieval is 5ms (vector lookup). Both are necessary; neither replaces the other." |
| "ContextDB has temporal + causal + entity graphs; you don't have causal" | True today; in our roadmap (ADR-0055 cross-file pass adds shared invariants + implicit contracts, which is functionally causal for code). |
| "ContextDB has RL-trained memory manager" | "They use RL to decide ADD/UPDATE/DELETE for individual memories. We use deterministic provenance precedence (ADR-0063) for accountable enterprise audit. Different optimisation: their RL is for fluid agent context; our determinism is for enterprise compliance." |
| "Why is your stack so complex?" | "Two reasons: (1) we serve enterprise — multi-tenant, SSO, audit, RBAC, compliance — not single-user. (2) We extract code with 50+ edge types and 28-field business context — a single SQLite isn't enough. We're shipping companybrain-lite for the simple case." (need to actually ship this!) |

---

## Concrete recommendations (ranked)

### Definitely steal — 4 high-leverage adoptions

1. **RRF multi-graph retrieval fusion** — 2 days work, dramatic query quality lift. Should land before the seed demo.
2. **PII + audit + typed TTLs (ADR-0064)** — 4 days work, unblocks Product 3 (compliance) entirely. Land before first enterprise pilot.
3. **ExperientialMemory tier** — 3 days work, builds the "brain learns over time" moat. Land before Series A.
4. **`companybrain-lite` SQLite distribution** — 1-2 weeks work, opens bottom-up developer adoption. Land before Series B.

### Consider — 3 medium-leverage adoptions

5. **`evolution.py` background process** (auto-link / consolidate / prune) — 2 days work; matters when brain hits 50K+ entities (post first pilot). Defer to month 6.
6. **Native integrations** (langchain, crewai, autogen) — 4 days work; massive GTM win for Product 4 (AI Agent Substrate). Sequence after MCP server hardening.
7. **Hermetic benchmarks suite** — half day work; lets us claim concrete numbers in the deck. Should ship now.

### Skip — 2 things they have we don't need

8. **RL-trained memory manager** — their innovation; not appropriate for our enterprise / audit / compliance positioning where deterministic provenance is the SELLING point. ADR-0063 M3 covers our equivalent.
9. **Generic entity graph for non-code biographies** — they cover "Alice's life across memories"; we extract code. Different domain.

---

## Recommended next ADR (if we want to act on this)

**ADR-0064 — Privacy & Audit Layer (lift contextdb's pattern)**:
- `privacy/pii_detector.py` — Microsoft Presidio or homegrown regex catalog
- `privacy/retention_manager.py` — typed TTLs per entity_type
- `audit/hash_chain.py` — Merkle-chain audit events
- Unblocks Product 3 (compliance) entirely; required for SOC2 + EU AI Act
- Cost: 4 days
- Could ship in parallel with the 0055-0061 set

**Plus a smaller addendum**: append to ADR-0055 and the SmartZoneAssembler the RRF fusion approach. ~2 days, pure additive, dramatic query quality lift.

---

## Brainstorming addendum — the bigger strategic question

What if we **explicitly target the AI agent memory market** instead of (or in addition to) the engineering knowledge market?

Their market = developers building agents (large + growing fast).
Our market = engineering teams understanding codebases (smaller + slower-growing).

If brain becomes both:
- "Engineering memory" (what we are today) — VPs of Eng, $100K ACV
- "Agent code-context substrate" (Product 4 in PRODUCT-VISION) — AI tool vendors, $100K-1M annual deals
- "General agent memory" (NEW — direct competition with ContextDB) — agent builders, $20-100/mo SaaS

The third line is the bottom-up developer market. **Risky** — we'd compete head-on with an established open-source incumbent. **Rewarding** — total addressable market is ~10× our enterprise market.

I'd recommend NOT adding the third line in the seed-stage pitch. After Series A, when we have product-market fit on engineering org buyers, revisit. The "complementary" frame is the right immediate stance.

---

## Open questions for the team

1. **Should we reach out to Gaurav?** A friendly intro positions us as collaborators not competitors. Could yield: a comparison blog post, mutual pointers from each project's docs, possible joint customer.
2. **Do we ship `companybrain-lite` before or after the seed close?** Before = stronger demo (developers can try it themselves); after = focused execution on enterprise.
3. **Do we ship native langchain/crewai/autogen integrations before MCP becomes universal?** Probably yes — MCP adoption is months away from being default in those frameworks; first-mover wins relationships.
4. **Should the deck explicitly mention ContextDB?** Probably yes if we have any AI-agent-buyer in the room. "Here's the comparison table" demonstrates we know the landscape and aren't trying to mislead. Hides nothing the smart investor would ask.

---

## Action items

1. [ ] **Right now**: write ADR-0064 (Privacy & Audit Layer) — required for Product 3, lifts CDB's pattern.
2. [ ] **Right now**: amend SmartZoneAssembler design to add RRF fusion across the existing graphs (~2 days).
3. [ ] **This week**: cold-email Gaurav (saaslabs.co) — friendly intro; "we're complementary; want to coordinate?"
4. [ ] **Q3 2026**: ship ExperientialMemory tier (the "brain learns over time" feature).
5. [ ] **Before seed close**: hermetic benchmark suite for the brain — get our own numbers to compare against CDB's published metrics.
6. [ ] **Q3-Q4 2026**: ship `companybrain-lite` (single-package SQLite-default distribution).
7. [ ] **Q4 2026**: ship 4 native integrations (langchain, crewai, autogen, openai_tools).
8. [ ] **Defer** (revisit Series A): explicit targeting of the bottom-up agent-memory market.
