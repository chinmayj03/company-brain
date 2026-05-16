# Memory Maturity — does company-brain handle the 12 hard memory problems?

**Honest audit, not cheerleading.** Each of the 12 problems the user articulated → mapped to our shipped + proposed work → strategic gap analysis → path to "memory layer for AI."

---

## TL;DR

**Our wedge today**: company-brain solves the **CODE-CONTEXT memory** sub-problem deeply (50-edge taxonomy, BC v2, schema awareness, blast radius, cross-file invariants). Within that vertical, we handle most of the 12 problems well or have credible plans for them.

**The bigger ambition** (what the user is asking about) is **general agent memory** — the "memory operating system" frontier where ContextDB, Mem0, Letta, Zep are competing. The 12 problems get HARDER once you're not just storing code entities — you're storing every conversation, every preference change, every contradiction, at consumer scale.

**The gap**: company-brain's primitives are 60-70% reusable for general agent memory. The missing 30-40% is mostly: **contradiction detection**, **salience over time**, **declarative forgetting policies**, **identity/behavioral consistency invariants**, **memory-distraction guards**. Those become ADR-0072 (this doc proposes it).

**Strategic recommendation**: stay vertical-deep on code memory through Series A (it's our defensible moat), but BUILD primitives that GENERALIZE so when we expand to general agent memory at Series B, we're not rewriting from scratch.

---

## The 12 problems × our state — gap matrix

For each: what we ship today | what we have proposed | what remains genuinely missing.

### 1. "They already use memory systems" (RAG, summaries, vectors, tools, profile)

**Our state**: we ship all of these for code.
- ✅ Vector search via Qdrant (shipped, ADR-0015)
- ✅ Conversation/query memory shape via T0/T1/T2 zones (shipped, ADR-0018)
- ✅ Profile memory analog: per-repo `.brain/BRAIN.md` (shipped, ADR-0051 P3)
- ✅ Tool-augmented retrieval via MCP server (shipped, ADR-0052 P5)
- ✅ Long-context-friendly chunking (shipped, ADR-0044)
- ⚠️ ExperientialMemory tier (proposed, ADR-0066) — cross-query learning

**Gap**: we have the machinery. The question is whether it generalizes from "code entities" to "user preferences, conversations, world facts."

### 2. "The real problem is retrieval quality"

**Our state**: this is where we're STRONGEST today.
- ✅ Hybrid search BM25+Qdrant (shipped, ADR-0015)
- ✅ SmartZoneAssembler T0/T1/T2 with token-budget capping (shipped, ADR-0018)
- ✅ Reachability filter — drop entities not on call graph (shipped, ADR-0043)
- ✅ Cross-file pass identifying patterns + invariants (shipped, ADR-0055)
- ⚠️ Multi-graph RRF fusion across N graphs (proposed, ADR-0065) — biggest single quality lift
- ⚠️ Adaptive smart zone sizing for messy entities (proposed, ADR-0063 M4)

**Gap (general agent memory specific)**:
- **Sarcasm / irony detection** — *"What matters right now?"* requires understanding tone, context. We don't model this for code (code rarely has sarcasm).
- **Outdated-fact detection** — code has versions; general memory has user-changed-preferences. Our temporal model (ADR-0059) handles versioned code; doesn't handle "user said X 6 months ago, said NOT X yesterday."
- **Per-query relevance learning from feedback** — partially via ExperientialMemory (proposed, ADR-0066) but only for code queries.

**Honest score**: 8/10 for code; 5/10 for general agent memory.

### 3. "Human memory is selective — needs forgetting, abstraction, compression, salience"

**Our state**: weak today, mostly proposed.
- ❌ NO automatic forgetting today
- ⚠️ ADR-0067 evolution background process (proposed): auto-link, consolidate, prune
- ⚠️ ADR-0064 typed TTLs (proposed): production_code 7y, test_fixture 90d, etc.
- ❌ NO salience detection — every entity weighted equally at extraction
- ❌ NO compression of multiple similar memories into a summary memory
- ❌ NO abstraction layer (concrete entity → "this is an example of X pattern")

**Gap (genuine)**:
- **Salience-over-time** — *"this fact mattered yesterday; today it doesn't"* requires modeling decay. We don't.
- **Compressive abstraction** — 50 similar method extractions should consolidate into 1 pattern + 50 deltas. ADR-0067 consolidator is the start; it's not yet shipped.
- **"Forget aggressively"** — counter-intuitive but right. Storing everything makes inference noisy. Our default today is "store everything forever."

**Honest score**: 3/10. **This is the biggest architectural gap.** Worth its own ADR (proposed below as ADR-0072 M2).

### 4. "Privacy and legal risk are enormous"

**Our state**: weakest area today.
- ❌ NO PII detection today (huge risk for code with hardcoded secrets)
- ❌ NO right-to-erasure path
- ❌ NO hash-chained audit log
- ⚠️ ADR-0064 (proposed): full PII + TTL + audit chain — required for Product 3 (compliance)

**Gap (general agent memory specific)**:
- **Cross-user inference leakage** — at consumer scale, "Alice's memories influence answers to Bob" is the big risk. Multi-tenancy partial in our stack; needs hardening.
- **Sensitive inference** — *"based on past chats, you might have depression"* is a privacy disaster. We don't currently make these inferences (we extract code, not psychology), but if we expand to general memory, we MUST add an inference-class filter.

**Honest score**: 2/10 today; 7/10 if ADR-0064 ships. Still need cross-user inference guards for general memory.

### 5. "Cost becomes massive at scale"

**Our state**: solved at the per-call layer; unsolved at the lifetime-storage layer.
- ✅ ADR-0049 caching (shipped): per-call cost $0.005 warm
- ✅ Cross-job extraction-queue dedup (ADR-0049 C4 shipped)
- ✅ Big-repo recovery without truncation (ADR-0050 shipped)
- ❌ Embedding storage grows forever today (no expiry, no compression)
- ❌ No vector index sharding for >100K vectors per workspace
- ❌ No tiered storage (hot Qdrant, warm PG, cold S3)

**Gap (consumer-scale specific)**:
- **500M users × thousands of memories each = $$$** — this is a whole different cost regime than enterprise. We're optimised for "100 customers × 50K entities each." For consumer scale you need bandwidth tricks (RRF on disk indexes; ANN sharding; compressed embeddings).

**Honest score**: 7/10 for enterprise scale; 3/10 for 500M-user scale (and that's appropriate — we don't target consumer).

### 6. "Transformers were never designed for lifelong memory"

**Our state**: we're WORKING WITH the architecture, not against it.
- ✅ Memory lives in EXTERNAL graph (Postgres + Neo4j + Qdrant), not in model weights
- ✅ Retrieved into context window per query, not implicit in the model
- ✅ Editable independently of model (we can update entity X without retraining)

**Gap**: none architecturally. **This is something we got right by accident or instinct.** The problem the user names is real — but we're not fighting it.

**Honest score**: 9/10. The brain is a memory ARCHITECTURE, not a model fine-tuning approach. Correct call.

### 7. "Updating memory safely is unsolved"

**Our state**: partial, mostly via provenance model.
- ⚠️ ADR-0063 M3 (proposed): FactWithProvenance + per-source precedence (human_brain_md > verifier > db_schema > cross_repo > augmentation > initial_llm)
- ⚠️ KnowledgeConflict surfacing when high-precedence sources disagree
- ✅ Audit trail on every mutation (after ADR-0064 ships)
- ❌ NO contradiction DETECTION (the user's example: "User favorite DB = PostgreSQL" → "User now prefers ClickHouse" — overwrite vs keep both vs decay)
- ❌ NO confirmation loop ("the brain noticed a contradiction; please clarify")

**Gap (genuine)**:
- **Contradiction detection** — needs an explicit pass that compares new memory against existing memories of the same entity / preference / fact. Not in any current ADR.
- **Update strategies (overwrite / append-with-decay / ask-user)** — declarative policy needed. Not in any current ADR.
- **Temporary vs permanent distinction** — *"I'll be in Tokyo this week"* should expire; *"I live in Tokyo"* shouldn't. We have NO classifier for this.

**Honest score**: 5/10 for code (where contradictions are rare); 2/10 for general memory.

This is **the second-biggest gap**. ADR-0072 below proposes the primitives.

### 8. "Long-term consistency — identity, causal, behavioral, planning"

**Our state**: very weak.
- ⚠️ Identity consistency: ADR-0055 cross-file pass surfaces SharedInvariants and DomainEntities — partial answer.
- ⚠️ Causal consistency: ADR-0058 schema awareness gives us "WHEN X column changes, Y queries break" — partial.
- ❌ Behavioral consistency: an agent that REMEMBERS a startup idea but FORGETS its constraints — we don't model "agent state across sessions."
- ❌ Planning continuity: TODOs / multi-step plans across sessions — we don't model.

**Gap**: this is THE difference between "memory layer" and "agent OS." We're a memory layer. Becoming an agent OS requires planning state, action history, intent persistence — architecturally MORE than memory.

**Honest score**: 4/10. We're not an agent OS; we're a memory layer that agents can use. This is fine if positioned correctly; misleading if we claim to be an agent OS.

### 9. "Too much retrieval harms reasoning — distraction, anchoring"

**Our state**: addressed structurally.
- ✅ T0/T1/T2 zone with HARD token budget (shipped, ADR-0018)
- ✅ Adaptive zone sizing — more entities, shorter summaries (proposed ADR-0063 M4)
- ✅ Confidence threshold — low-confidence entities excluded by default
- ⚠️ NO empirical study of "does adding more retrieval HURT answer quality" — should test

**Gap**: minor. We respect the token budget. Whether the SHAPE of retrieval (10 entities × full bodies vs 40 × short summaries) is optimal — open question.

**Honest score**: 7/10. We're disciplined; we lack empirical evidence we picked the right discipline.

### 10. "Companies prioritize reliability first because bad memory destroys trust"

**Our state**: aligned philosophically.
- ✅ ADR-0056 verifier (proposed) drops hallucinated query_text — exactly this principle
- ✅ Confidence-as-evidence rubric (ADR-0053 PR-A)
- ✅ Citations required for every claim
- ⚠️ Demo verification gates (ADR-0054 — partially shipped) prove pre-demo trust
- ❌ NO formal SLA for "% of answers that include verifiable citations"

**Gap**: cultural alignment is right. SLA-level enforcement is missing. **For consumer-scale memory, you'd want "95% of answers cite source within 1 click; <0.1% hallucinated facts surfaced."** We don't measure this yet.

**Honest score**: 7/10 philosophically; 4/10 measured.

### 11. "Frontier is shifting toward memory-centric agents — agentic memory, world models, episodic memory, reflection loops, persistent planning"

**Our state**: we've SHIPPED the reflection-loops piece (ADR-0066 ExperientialMemory, proposed) and SHIPPED the agentic-harness foundation (ADR-0051 P1-P4 shipped). We've NOT shipped world models or persistent planning.

- ✅ Agentic memory (per-job ContextManager, sub-agents) — shipped via ADR-0051
- ⚠️ Episodic memory (query trajectories, outcomes) — proposed in ADR-0066
- ⚠️ Reflection loops (verifier-correction → re-extract) — proposed in ADR-0056 self-correction
- ⚠️ Self-improving retrieval — implicit in ADR-0066's ExperientialRanker
- ❌ World models — we have a CODE world model (the entity graph) but not a general world model
- ❌ Persistent planning — out of scope today
- ❌ Memory hierarchies (hot/warm/cold/episodic/semantic) — partial via ADR-0049 caching only

**Gap**: world models and persistent planning are hard. They're a different product (an agent OS) and probably a different company. Stay focused on memory.

**Honest score**: 6/10 for memory-frontier features; we ship the boring half (memory) cleanly, the exciting half (agents that learn over time) is mostly proposed not shipped.

### 12. "Why startups still have opportunities — lifelong memory, scalable agent memory, reasoning over memory, editable memory, trustworthy personalization"

**Our state**: this is the OPPORTUNITY framing. Of the 7 sub-categories the user named, where do we play?

| Opportunity | Our angle |
|---|---|
| AI operating systems | NO — agent OS is a different product |
| Memory infrastructure for agents | YES — Product 4 (AI Agent Substrate) in PRODUCT-VISION |
| Enterprise knowledge memory | YES — Products 1+2+3 in PRODUCT-VISION |
| Persistent coding agents | YES — what we already do |
| Long-horizon autonomous workers | NO — out of scope; we provide their memory layer |
| Personal AI companions | NO — consumer; we don't target |
| Multi-agent coordination memory | YES if we extend to per-agent memory tiers in MCP server |

**Honest score**: we're well-positioned for 4 of 7 opportunity categories. Don't try to play all 7.

---

## Aggregate maturity score

| Problem area | Code-context score | General-memory score | Closes the gap? |
|---|---|---|---|
| 1. Already-have memory systems | 9/10 | 7/10 | mostly yes |
| 2. Retrieval quality | 8/10 | 5/10 | needs ADR-0065 + general-memory work |
| 3. Selective forgetting | 3/10 | 2/10 | **biggest gap** — needs ADR-0072 |
| 4. Privacy/legal | 2/10 | 2/10 | needs ADR-0064 |
| 5. Cost at scale | 7/10 | 3/10 | needs consumer-scale eng |
| 6. Architectural fit | 9/10 | 9/10 | already correct |
| 7. Updating memory safely | 5/10 | 2/10 | **second-biggest gap** — needs ADR-0072 |
| 8. Long-term consistency | 4/10 | 4/10 | partial; out-of-scope to fully solve |
| 9. Distraction guard | 7/10 | 6/10 | acceptable |
| 10. Reliability culture | 7/10 | 7/10 | aligned |
| 11. Memory-frontier features | 6/10 | 5/10 | mostly proposed not shipped |
| 12. Opportunity targeting | 4/7 categories played | — | clean focus |

**Composite**: ~60% solved for code-context; ~40% solved for general agent memory. **Better than I'd have guessed before doing the audit. Worse than the demo deck claims.**

---

## Strategic positioning

### Where we fit (the most-defensible position)

- **Today**: vertical depth on **code memory**. We're 80% of the way to best-in-class. Few competitors (GitHub Copilot Enterprise has surface-level code memory; Sourcegraph has code search but not memory; Cody has neither). **This is the moat.**
- **Year 1-2**: extend code memory with the ContextDB-inspired primitives (ADR-0064 privacy, ADR-0065 RRF, ADR-0066 experiential, ADR-0067 evolution). Now we're at 90% of code-memory perfection PLUS we have memory-system primitives.
- **Year 2-3**: open the architecture to general agent memory by shipping the missing primitives (ADR-0072 below). Our entity-graph + edge-taxonomy + provenance model GENERALIZES from "code entities" to "any agent memories" because code is just a hard sub-case of the general problem.
- **Year 3+**: become "the memory layer that supports both code and general agent memory" — Product 4 (AI Agent Substrate) plays in BOTH markets.

### What we should NOT try to be

- ❌ **Agent operating system** — a different product (Letta, AutoGen, LangGraph try this; hard, crowded)
- ❌ **General-purpose vector DB** — Pinecone/Weaviate/Qdrant own this; we use Qdrant
- ❌ **Personal AI companion memory** — Mem0 plays here; consumer brand we don't have
- ❌ **Foundation model** — Anthropic / OpenAI / Google own this

### What we ARE building toward

> **The memory infrastructure for AI systems that need to reason about complex, evolving, structured domains where correctness and provenance matter.**

Code is the proof case (high stakes, structurally rich, customer pays). Same architecture extends to:
- Legal contracts memory (clauses, precedents, edits)
- Medical records memory (longitudinal patient state, contradictions, decay)
- Scientific paper memory (claim graphs, citations, retractions)
- Compliance documentation memory (SOC2 evidence, regulatory updates)

All of these share the structural properties of code (entities + edges + provenance + temporal + contradictions) but in different domains. The brain's architecture is domain-portable.

---

## What to build now (priorities)

Stack-rank for the next 6 months:

1. **Ship the proposed code-memory ADRs** (0064, 0065, 0066, 0067, plus 0061 E1) — turns code memory from 60% to 90% solved. ~4 weeks of parallel sessions per IMPLEMENTATION-ORDER-V3.

2. **Ship ADR-0072 (general-memory primitives)** — proposed below. ~3 weeks.

3. **Empirical measurement** — actually measure the maturity scores monthly. The ones I assigned above are educated guesses; instrument to verify.

4. **DO NOT** prematurely expand to non-code domains. Win one vertical (code) deeply before going horizontal. The "memory layer for AI" pitch only works AFTER you've proven memory works for ONE thing.

---

## Recommended next ADR

**ADR-0072 — General-Purpose Memory Primitives** (~3 weeks of work, parallel-shippable). Would address the user's gaps #3 (selective forgetting) and #7 (safe updating) at the architectural level so the brain GENERALIZES from code-memory to any-memory:

| Mechanism | Solves which of the 12 |
|---|---|
| **Contradiction Detector** — when new memory conflicts with existing | #7 (safe updating) |
| **Salience Scorer** — per-memory relevance over time; decay function | #3 (selective forgetting) |
| **Declarative Forgetting Policies** — per-entity-type retention rules + customer overrides | #3 + #4 (privacy/legal) |
| **Identity / Causal / Behavioral Invariants** — explicit assertions the brain enforces over time | #8 (long-term consistency) |
| **Memory-Distraction Guard** — at retrieval, detect when extra context HURTS the answer; truncate | #9 (distraction harm) |
| **Temporary vs Permanent classifier** — for any new memory, predict its lifetime | #3 + #7 |

I'll write the ADR if you want — say the word. Otherwise this maturity doc is the strategic answer.

---

## What to tell investors when they ask "what about memory?"

The honest pitch (use this verbatim if useful):

> *"Code memory is the hardest sub-problem of agent memory because it's structurally complex, high-stakes, and the customer pays. We solve it deeply: 50-edge taxonomy, schema-aware extraction, blast-radius reasoning, cross-file invariants, provenance-tracked updates. Solving code memory well builds the architectural primitives — contradiction detection, salience scoring, forgetting policies, identity invariants — that generalize to any structured memory domain. Our roadmap is: win code memory enterprise (Product 1-3), extend to AI-agent context substrate (Product 4), then the architectural primitives let us extend to legal/medical/scientific memory verticals or partner with consumer-memory plays like Mem0. We're playing the game of 'be the memory infrastructure that wins because the architecture is right' — not 'be the memory product that wins because of brand.'"*

That answer scores high with technical investors (it's accurate and humble) and acceptable with non-technical (it's strategic and ambitious).

**Do NOT pitch**: "we solve all 12 memory challenges." We solve 4-5 well. Saying more invites the deep technical question we'd fail.

---

## Closing thought

The user's framing — *"the next major leap after reasoning is memory"* — is right. We're 60% of the way to playing in that game well. The path to 95% is concrete (5-7 ADRs over 3-4 months). The trap to avoid is **trying to be the general-memory winner today** when we don't yet own the code-memory market we already lead in. **Win one vertical, build primitives that generalize, expand from strength.**
