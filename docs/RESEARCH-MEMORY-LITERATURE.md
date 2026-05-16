# Research Literature — Memory, Freshness, Causal Reasoning, Federated Indexing

**Synthesised from real research published in 2024-2026.** Each finding mapped to which mechanism in ADR-0072/0073 it supports OR refines OR contradicts. Honest about what's solved, what's open, and where company-brain can innovate vs adopt.

**Method**: searched for production papers, benchmarks, and engineering write-ups in five areas — agent memory architectures, temporal knowledge graphs, code intelligence at scale, continual learning / forgetting, and salience/attention. Identified the dominant approaches per area and how they map to our problems.

---

## Executive summary

The good news: **almost every problem you named in your two messages has been studied; most have working solutions in production at companies like Meta, Sourcegraph, Mem0, Letta, and Zep**.

The bad news: **no single system solves all of them at once for code memory specifically.** The gap company-brain can fill:

> *"Code-memory architecture that combines: (1) Glean-style incremental diff indexing, (2) Zep-style temporal knowledge graphs with bi-temporal validity, (3) Mem0-style salience-based retrieval, (4) MemGPT-style memory hierarchy, (5) Letta v1 native-tool-loop, (6) FOREVER-style forgetting curve consolidation, with our own innovation: branch-aware materialized views + causal event chains specific to code."*

Nobody has shipped this combination. It's our differentiated technical positioning.

---

## What the research landscape shows (5 key insights)

### 1. **Glean (Meta, December 2024)**: incremental code indexing with `O(diff)` cost is solved

**Key paper**: ["Indexing code at scale with Glean" — Engineering at Meta](https://engineering.fb.com/2024/12/19/developer-tools/glean-open-source-code-indexing/)

Meta open-sourced Glean — the system that indexes their entire codebase incrementally. The breakthrough is the **diff sketch**: a machine-readable summary of a changeset listing things like "introduced new class X", "removed method Y", "added field Z to type T", "added a call from A to B". Diff sketches drive:
- Incremental index updates (only re-process facts touching changed units)
- Code review augmentation (live go-to-definition during PR review)
- Lint rules ("you added a field; did you update the migration?")
- Semantic search over commits (not just files)

**Architecture detail**: facts are labelled with a "unit" (typically filename). Index is layered: a base database + stacked deltas for changed units. Multiple versions can coexist (e.g., main + each PR's view). When a unit re-indexes, only its facts in the top stack are replaced.

**What this validates in ADR-0073**:
- M1 event-stream + M2 V5 branch overlay are exactly the Glean pattern. Confirmed direction.
- M3 incremental refresh via webhook → diff → re-extract is exactly the Glean approach.

**What this teaches us new**:
- The "unit" labelling pattern is cleaner than what I described in ADR-0073. **Adopt**: every entity carries a `unit_id` (file or module); incremental refresh operates at the unit granularity.
- Stacking deltas instead of merging them lets us keep MULTIPLE branch views simultaneously without combinatorial storage explosion.
- "Index diffs, not full files" gives O(diff_size) extraction cost. Critical for the freshness problem.

**Action**: rewrite ADR-0073 M1 + M2 + M3 to explicitly adopt Glean's unit-stacking architecture. Cite the paper.

### 2. **Zep + EvoKG + T-GRAG (2025)**: temporal knowledge graphs are now production-ready, with measured 18-47% accuracy gains

**Key papers**:
- [Zep: A Temporal Knowledge Graph Architecture for Agent Memory (Jan 2025)](https://arxiv.org/pdf/2501.13956)
- [Temporal Reasoning over Evolving Knowledge Graphs — EvoReasoner / EvoKG (Sep 2025)](https://arxiv.org/abs/2509.15464)
- [T-GRAG: Dynamic GraphRAG Framework for Temporal Questions (Aug 2025)](https://arxiv.org/pdf/2508.01680)

**Zep's contribution**: a **bi-temporal knowledge graph** — every fact has both an event-time (when did the thing happen?) and a transaction-time (when did the brain learn about it?). This lets the brain answer "what did we know last Tuesday vs what's true today" cleanly. Reports 18.5% accuracy improvement + 90% latency reduction over baselines on temporal reasoning tasks.

**EvoReasoner's contribution**: multi-route decomposition + context-informed global search + temporal-aware local exploration. 23.3% gain on temporal reasoning benchmarks. The key insight: **time-sensitive queries need different retrieval strategies than time-insensitive queries**.

**T-GRAG's contribution**: dynamic GraphRAG specifically for temporal-evolving questions. Improved 22-47 points over GraphRAG baseline on multi-temporal questions.

**What this validates in ADR-0073**:
- M2 V4 (TimelineWindow view) + V1 (EntityState at_time) are the bi-temporal pattern. Confirmed.
- M6 causal edges align with EvoReasoner's "context-informed search" principle.

**What this teaches us new**:
- Bi-temporal (event-time + transaction-time) is more rigorous than what I described in ADR-0073. **Adopt**: every fact has BOTH timestamps.
- "Different retrieval per query intent" — already hinted at in ADR-0065 RRF intent classifier; should explicitly include temporal intent ("when did X happen?" vs "what is X today?") as a routing dimension.
- Zep's specific 18.5% improvement gives us a quantitative target — **if our temporal queries don't improve by ≥15% after shipping ADR-0073 V4, our implementation is wrong.**

**Action**: cite Zep's bi-temporal model in ADR-0073; add a benchmark target to V4 acceptance test.

### 3. **Mem0 + Letta v1 (2024-2026)**: production agent memory has converged on a memory hierarchy with salience-based eviction

**Key papers**:
- [Mem0: Building Production-Ready AI Agents with Scalable Long-Term Memory (April 2025)](https://arxiv.org/abs/2504.19413)
- [Mem0 Research Page (2025)](https://mem0.ai/research)
- [Letta — Rearchitecting the Agent Loop (2025)](https://www.letta.com/blog/letta-v1-agent)
- [Cognee AI Memory Tools Evaluation (2025)](https://www.cognee.ai/blog/deep-dives/ai-memory-tools-evaluation)

**Mem0's numbers** (LOCOMO benchmark): 91.6% accuracy with <7K tokens per retrieval call. 93.4% on LongMemEval. 64.1/48.6 on BEAM (1M/10M memories). **These are the SOTA targets we should be benchmarking against.**

**Mem0's mechanism**: dynamic extraction (when something matters, write it; ignore noise) + consolidation (merge similar memories) + salience-based retrieval (relevance scores include time decay + recency boost). Token-efficient: most queries return ≤5 memories at <7K total tokens.

**Letta's transition**: original MemGPT used heartbeats + send_message tool. Letta v1 (2025) drops both — uses native reasoning + direct generation. The lesson: **as base models improve, the memory framework should get LESS structured, not more.** Don't over-engineer agent loops; let the model's reasoning do more.

**Cognee's evaluation**: AI Memory eval shows Cognee/Mem0/Zep/Graphiti as the four-horse race in 2025. Each has different strengths (Cognee for graph; Mem0 for token-efficiency; Zep for temporal; Graphiti for incremental).

**What this validates in ADR-0072 / 0073**:
- ADR-0072 M2 salience scoring is mainstream — Mem0 ships it.
- ADR-0067 evolution consolidation is mainstream — Mem0 ships it.
- ADR-0066 ExperientialMemory tier (queries, corrections, utility) aligns with Mem0's "dynamic extraction".

**What this teaches us new**:
- Mem0's token-efficiency (<7K per retrieval) is a benchmark we should HIT. Today our `/query` likely uses 4000-8000 tokens of zone context — comparable. Verify.
- The LOCOMO + LongMemEval benchmarks are STANDARD. We should run them against company-brain to position ourselves credibly. (Not for code-memory specifically — but proves the architecture handles general cases.)
- Letta v1's "less framework, more model reasoning" lesson — reduce ContextManager complexity as Sonnet/Haiku improve.

**Action**:
1. Run LOCOMO + LongMemEval benchmarks against company-brain. If we hit >85%, we're competitive.
2. Add token-budget per retrieval as an explicit telemetry metric (target <7K).
3. Re-evaluate ContextManagerAgent — does its scaffolding still help with newer models?

### 4. **Continual learning literature (2025-2026)**: "stability-plasticity dilemma" is the unifying frame; no perfect solution exists

**Key papers**:
- [FOREVER: Forgetting Curve-Inspired Memory Replay (2026)](https://arxiv.org/abs/2601.03938)
- [Continual Knowledge Updating in LLM Systems — Memini / Benna-Fusi model (2026)](https://arxiv.org/abs/2605.05097)
- [Forget Forgetting: Continual Learning in a World of Abundant Memory (Feb 2025)](https://arxiv.org/html/2502.07274v5)
- [The Future of Continual Learning in the Era of Foundation Models (June 2026)](https://arxiv.org/pdf/2506.03320)

**Stability-plasticity dilemma**: the central tension. Models that maintain prior knowledge well (high stability) struggle to incorporate new (low plasticity), and vice versa. **There is no optimal point — it's a tradeoff.**

**FOREVER**: aligns memory replay schedule to MODEL TIME (magnitude of optimizer updates), not wall clock time. Mirrors Ebbinghaus's human forgetting curve. Important nuance: **time alone isn't the right axis for forgetting; it's "how much has the model changed since."**

**Memini (Benna-Fusi)**: multi-timescale memory dynamics — coupled internal variables that handle episodic sensitivity, gradual consolidation, AND selective forgetting as facets of ONE mechanism. Biology-inspired.

**"Forget Forgetting" (Feb 2025)**: when memory is abundant (storage is cheap), you DON'T need to actively forget. Just store everything; let salience-weighted retrieval handle relevance. **This contradicts the "forgetting is essential" school.**

**The honest current state of the field**: no consensus. Some papers say forgetting is essential (FOREVER); others say you can store everything if storage is cheap and retrieval is good ("Forget Forgetting"). Memini tries to unify.

**What this teaches us**:
- **The user's claim "we cannot store redundant or stale information" is the AGGRESSIVE-FORGETTING school.** ADR-0067 (evolution prune) + ADR-0072 M3 (forgetting policies) implement this view.
- **The other school's claim** ("storage is cheap; retrieval is the lever") would say keep everything, just rank salience aggressively. ADR-0072 M2 + M5 + M7 (salience + distraction-guard + query-context-aware) implement THIS view.
- **Best architecture**: BOTH. Store generously; rank aggressively; prune ONLY entities that are demonstrably noise (high salience-score over weeks AND zero retrieval references). This is the safer position.

**What this contradicts in our ADRs**:
- ADR-0067 M3 prune step is currently aggressive ("hallucinated + no edges + 30 days → drop"). Adopt the "Forget Forgetting" lesson: be less aggressive with deletion. **Mark as "demoted" — exclude from default retrieval — but keep around for forensics.**

**Action**: revise ADR-0067 pruner from "delete" to "demote/mark as low-salience". Recoverability matters when our predictions are wrong.

### 5. **Salience and attention research (2025)**: LLM saliency is FRAGILE; misattribution >90% in 10K-token contexts

**Key papers**:
- [The Fragile Truth of Saliency: Improving LLM Input Attribution (NeurIPS 2025)](https://openreview.net/pdf?id=DrUR87D4Hj)
- [Behavioral Analysis of Information Salience in LLMs (ACL 2025)](https://aclanthology.org/2025.findings-acl.1204.pdf)
- [Scaling to Emphasize Attention for Long-Context Retrieval (ACL 2025)](https://aclanthology.org/2025.acl-long.1405.pdf)

**The bombshell finding**: existing saliency methods assign substantial importance to IRRELEVANT tokens, with misattribution growing as context length grows. **At 10K-token prompts, misattribution can exceed 90% of the saliency score.** That means most "explanations" of LLM attention are wrong.

**Implication for retrieval**: even if our distraction guard (ADR-0072 M5) thinks an entity is "salient", the LLM's actual use of that entity may be unrelated. Saliency-as-the-LLM-sees-it ≠ relevance-as-we-measure-it.

**LLM salience hierarchy**: LLMs have a nuanced internal salience hierarchy that's hard to access through introspection AND only weakly correlates with human salience. **The salience the model uses is NOT the salience we'd assign by inspection.**

**The Attention Bias Optimization (ABO) approach**: optimize attention bias per token to quantify causal impact. New (NeurIPS 2025) and untested at scale.

**What this teaches us**:
- **Be humble about salience scoring.** ADR-0072 M2's heuristic-based salience score is "best effort" — not the model's actual salience. Don't oversell it.
- **The right move is to WATCH model behavior, not predict it.** ADR-0066 ExperientialMemory ("which entities did the model actually cite?") is closer to truth than any prediction.
- **Distraction guard (ADR-0072 M5)**: less optimistic about it. It might add noise rather than reduce it. **A/B test rigorously before enabling by default.**

**Action**: add a guard rail to ADR-0072 M5: "ship behind feature flag; enable only if A/B shows ≥10% improvement on a benchmark like LOCOMO."

---

## Mapping research → our ADRs (the integration table)

| Research finding | Maps to | Action |
|---|---|---|
| Glean diff sketch + unit stacking | ADR-0073 M1, M2 V5, M3 | **Adopt directly**; cite the paper |
| Bi-temporal KG (event-time + transaction-time) | ADR-0073 M2 V1, V4 | Refine ADR; cite Zep |
| EvoReasoner per-intent retrieval routing | ADR-0065 intent classifier | Add temporal intent dimension |
| T-GRAG dynamic GraphRAG | ADR-0073 M2 V4 + V2 | Architecture validated |
| Mem0 token-efficient retrieval (<7K) | ADR-0049 caching + ADR-0065 RRF | Add as benchmark target |
| LOCOMO + LongMemEval benchmarks | ADR-0066 ExperientialMemory | Run against our brain; publish results |
| Letta v1 "less framework more reasoning" | ADR-0051 P1 HarnessLoop + ContextManager | Audit; reduce overhead as models improve |
| FOREVER forgetting curve | ADR-0072 M2 salience decay | Use "model time" not wall time as decay variable |
| Memini multi-timescale dynamics | ADR-0072 M1+M2+M3 unified | Future work — refactor as one mechanism |
| "Forget Forgetting" (storage abundant) | ADR-0067 prune | **Refine: demote ≠ delete** |
| Saliency is fragile (>90% misattribution) | ADR-0072 M2 + M5 | A/B test rigorously; humility |
| Vector clocks for causal ordering | ADR-0073 M6 causal edges | Adopt for branch-aware causality |

---

## What's GENUINELY OPEN (where company-brain can innovate)

The literature solves many sub-problems but leaves these gaps:

### Gap 1 — Code-specific causal reasoning

Zep has temporal KGs but for general agent memory. EvoReasoner has time-aware reasoning but not domain-specific. **No published work** specifically on causal reasoning for code: PR → commit → deploy → incident → fix chains. Our M6 (ADR-0073) is novel for the code domain.

### Gap 2 — Branch-aware federated indexing

Glean has incremental + multi-version indexing. Sourcegraph has multi-repo. **Nobody combines branch-awareness with federation.** Our M5 (federated planner) + M2 V5 (branch overlay) is novel.

### Gap 3 — Working-tree extraction

The IDE-plugin pattern (extract uncommitted diffs as a virtual branch) isn't in any published research I found. **This is genuine differentiation.** Sourcegraph's IDE integration only works on indexed branches; doesn't show your working tree to the brain.

### Gap 4 — Cross-repo institutional knowledge transfer

Mem0 talks about cross-conversation memory. We talk about cross-repo memory (the ADR-0061 query_brain_other_repos concept). **No published work specifically on "institutional patterns from customer A's brain inform customer B's brain (anonymously)".**

### Gap 5 — Code memory benchmarks

LOCOMO + LongMemEval are GENERAL conversational benchmarks. **There's no standardised "code memory" benchmark** like our `BENCHMARK-NETWORK-IQ.md`. Opportunity: publish our benchmark (with the lob-rename test, etc.) as a public standard. **Can become an industry-standard benchmark we author.**

### Gap 6 — Stability-plasticity dilemma for code

Continual learning research focuses on model-weight updates (stability vs plasticity). **For external graph memory, the dilemma is different**: how do we update without losing prior context AND without spurious accumulation? Less studied.

### Gap 7 — The federated branch + causal + temporal trifecta

No system combines: federated multi-repo + branch-aware + causal-event-tracking + bi-temporal + working-tree-overlay + salience-adaptive. **That combination is our defensible moat.**

---

## Specific recommendations to refine ADR-0073

Based on the research, here are concrete refinements (suggested ADR-0073 v2):

### R1 — Adopt Glean's "unit" abstraction for incremental refresh

Replace ADR-0073 M2 V5 (BranchOverlay) implementation detail to use Glean-style unit stacking:
- Each fact carries `unit_id` (file path or module name)
- Unit-level facts can be replaced atomically; unaffected units survive
- Multiple unit stacks coexist (one per active branch)
- Garbage collection runs per-unit, not per-fact

This is more efficient than per-fact branch tagging when most branches differ from main on only ~5% of units.

### R2 — Adopt bi-temporal model from Zep

Every fact carries TWO timestamps:
- `event_time`: when the thing happened in the world (commit timestamp, incident start time)
- `transaction_time`: when the brain learned about it (extraction time)

Time-travel queries can use either: `?at_event_time=YYYY-MM-DD` (state of world at that date) OR `?at_transaction_time=YYYY-MM-DD` (what the brain THOUGHT it knew at that date). Useful for incident retrospectives ("what would the brain have said at the time?").

### R3 — Add LOCOMO + LongMemEval benchmark targets

To ADR-0066 ExperientialMemory acceptance test, add: brain must achieve ≥85% on LOCOMO (Mem0 ships 91.6%) and ≥80% on LongMemEval. **Quantitative positioning** for investor pitches.

### R4 — Demote, don't delete (Refine ADR-0067 prune)

Pruner shouldn't `DROP` rows; should `UPDATE entity SET demoted_at = now()`. Demoted entities excluded from default retrieval; recoverable via `?include_demoted=true`. Aligns with "Forget Forgetting" school + reduces risk of false-positive deletion.

### R5 — Vector clocks for causal ordering across branches

For ADR-0073 M6, when events span multiple branches (PR merged from feature/x into main), use **Lamport timestamps + vector clocks** to establish partial ordering. This handles concurrent commits across branches without forcing a total order.

### R6 — A/B test the distraction guard

ADR-0072 M5 should not enable by default. Behind feature flag; A/B against LOCOMO + our benchmark. Only enable if measurable improvement. The salience-fragility research suggests this might HURT.

### R7 — Open-source the benchmark

Publish `BENCHMARK-NETWORK-IQ.md` as `docs/CODE-MEMORY-BENCH-v1.md` on GitHub. **Becomes the "LOCOMO for code memory"** — investors see us as the framework owner of code-memory benchmarking. **Free positioning win.**

---

## Citations to weave into ADRs

When the ADRs ship as PRs, citation footers should include:

```markdown
## Research foundations

- Glean (Meta, 2024) — incremental indexing + diff sketches: https://engineering.fb.com/2024/12/19/developer-tools/glean-open-source-code-indexing/
- Zep (2025) — bi-temporal knowledge graph for agent memory: https://arxiv.org/pdf/2501.13956
- Mem0 (2025) — production scalable agent memory: https://arxiv.org/abs/2504.19413
- EvoReasoner (Sep 2025) — temporal reasoning over evolving KGs: https://arxiv.org/abs/2509.15464
- T-GRAG (Aug 2025) — dynamic GraphRAG for temporal questions: https://arxiv.org/pdf/2508.01680
- FOREVER (2026) — forgetting curve memory replay: https://arxiv.org/abs/2601.03938
- Memini (2026) — multi-timescale memory dynamics: https://arxiv.org/abs/2605.05097
- Letta v1 — rearchitecting the agent loop: https://www.letta.com/blog/letta-v1-agent
- "The Fragile Truth of Saliency" (NeurIPS 2025) — saliency misattribution in LLMs: https://openreview.net/pdf?id=DrUR87D4Hj
- "Forget Forgetting" (Feb 2025) — abundant-memory continual learning: https://arxiv.org/html/2502.07274v5
- Vector Clocks (Lamport, 1978) — partial event ordering in distributed systems
- LOCOMO benchmark — long-term conversational memory: https://snap-research.github.io/locomo/
```

These citations make ADRs intellectually honest AND strengthen the technical positioning when investors do diligence.

---

## Strategic positioning for the seed pitch

Use this verbatim if asked "how does this differ from Mem0/Letta/Zep/Cognee?":

> *"Mem0 and Letta are best-in-class for general agent conversational memory — that's their wedge. Zep extends with bi-temporal reasoning. Cognee focuses on graph construction. Each is excellent in their domain. Company-brain takes a different vertical: code memory, where the entities are structurally rich (50-edge taxonomy), the freshness problem is acute (push every minute), and the moat is in cross-file invariants + schema awareness + causal change-tracking — none of which the general-memory players solve. Architecture-wise we adopt their innovations (Glean's incremental indexing, Zep's bi-temporal KG, Mem0's salience-based retrieval, Memini's consolidation primitives) and combine them with code-specific extraction primitives nobody else has. We're not competing with Mem0; we'd RECOMMEND Mem0 for runtime agent memory and pair with company-brain for code memory."*

That answer reads as well-researched (you cite five specific papers) and humble (you don't claim to dethrone Mem0). Investors give partial credit for landscape literacy even if your product doesn't yet match a specific competitor.

---

## What to do next (action items)

1. [ ] **Refine ADR-0073** with the 7 R-recommendations above. Cite Glean + Zep specifically.
2. [ ] **Add R7 (publish code-memory benchmark)** as ADR-0074 — write it as a 1-week project after seed close. Free PR / inbound conversion.
3. [ ] **Run LOCOMO + LongMemEval against our brain** — even though they're general not code, getting ≥85% positions us as production-ready. If we score <70%, we have a problem to fix.
4. [ ] **Update MEMORY-MATURITY.md scores** based on this research — some of my earlier "this is unsolved" claims are wrong (Glean solves incremental indexing; Zep solves bi-temporal; Mem0 solves salience).
5. [ ] **Add citations to all in-flight ADRs** — refit ADR-0048 through 0073 with research-paper references where applicable. Makes them publication-quality.
6. [ ] **Email Gaurav Sharma at ContextDB** + the Mem0 team — start a "code memory" community-of-practice. Friendly framing per LEGAL-CONTEXTDB-INTEGRATION.md.

---

## TL;DR for the founder

**The good news**: every problem you named has been studied, most have solutions in production at Meta / Mem0 / Zep / Letta. **You're not chasing the impossible — you're chasing a known-tractable problem.**

**Our edge isn't in inventing new memory primitives.** It's in:
1. Combining 5 proven approaches (Glean + Zep + Mem0 + MemGPT + Memini) into one coherent system
2. Specialising for the code-memory vertical where general agent memory players don't go
3. Adding code-specific innovations: branch-aware federation, working-tree extraction, schema-coupled events, cross-repo institutional patterns

**What to read** if you want to go deeper (1-2 hours):
- Glean blog post (Meta, Dec 2024) — operational details of incremental indexing
- Zep paper (Jan 2025) — bi-temporal KG mechanics
- Mem0 paper (Apr 2025) — salience + token efficiency
- "Forget Forgetting" (Feb 2025) — challenges the active-pruning orthodoxy

**What to ignore** (academic but not actionable for us):
- Memini's biology-inspired model — too theoretical for our 6-month roadmap
- Saliency fragility paper — interesting but doesn't change what we'd ship
- Most continual-learning papers — they're about model-weight updates, not external memory; tangential

---

## Sources

- [Letta GitHub](https://github.com/letta-ai/letta)
- [Letta — MemGPT concepts](https://docs.letta.com/concepts/memgpt/)
- [Letta — Rearchitecting the Agent Loop (Lessons from ReAct, MemGPT, Claude Code)](https://www.letta.com/blog/letta-v1-agent)
- [MemGPT research site](https://research.memgpt.ai/)
- [Letta — Benchmarking AI Agent Memory](https://www.letta.com/blog/benchmarking-ai-agent-memory)
- [Zep — Temporal Knowledge Graph Architecture for Agent Memory (Jan 2025)](https://arxiv.org/pdf/2501.13956)
- [arXiv 2509.15464 — EvoReasoner: Temporal Reasoning over Evolving Knowledge Graphs](https://arxiv.org/abs/2509.15464)
- [arXiv 2508.01680 — T-GRAG: Dynamic GraphRAG Framework](https://arxiv.org/pdf/2508.01680)
- [Sourcegraph — Cross-repository code navigation](https://sourcegraph.com/blog/cross-repository-code-navigation)
- [Sourcegraph — Optimizing a code intelligence indexer](https://sourcegraph.com/blog/optimizing-a-code-intel-indexer)
- [Sourcegraph — CodeScaleBench: large codebases + multi-repo tasks](https://sourcegraph.com/blog/codescalebench-testing-coding-agents-on-large-codebases-and-multi-repo-software-engineering-tasks)
- [arXiv 2601.03938 — FOREVER: Forgetting Curve-Inspired Memory Replay](https://arxiv.org/abs/2601.03938)
- [arXiv 2605.05097 — Continual Knowledge Updating via Memini / Benna-Fusi](https://arxiv.org/abs/2605.05097)
- [arXiv 2502.07274 — Forget Forgetting: Continual Learning in a World of Abundant Memory](https://arxiv.org/html/2502.07274v5)
- [arXiv 2506.03320 — The Future of Continual Learning in the Era of Foundation Models](https://arxiv.org/pdf/2506.03320)
- [Engineering at Meta — Indexing code at scale with Glean (Dec 2024)](https://engineering.fb.com/2024/12/19/developer-tools/glean-open-source-code-indexing/)
- [Glean — Incremental indexing](https://glean.software/blog/incremental/)
- [Towards Data Science — RAG Is Blind to Time](https://towardsdatascience.com/rag-is-blind-to-time-i-built-a-temporal-layer-to-fix-it-in-production/)
- [News from Generation RAG — Knowledge Decay Problem](https://ragaboutit.com/the-knowledge-decay-problem-how-to-build-rag-systems-that-stay-fresh-at-scale/)
- [arXiv 2504.19413 — Mem0: Production-Ready AI Agents with Scalable Long-Term Memory](https://arxiv.org/abs/2504.19413)
- [Mem0 Research](https://mem0.ai/research)
- [Mem0 — State of AI Agent Memory 2026](https://mem0.ai/blog/state-of-ai-agent-memory-2026)
- [Cognee — AI Memory Tools Evaluation](https://www.cognee.ai/blog/deep-dives/ai-memory-tools-evaluation)
- [Snap Research — LOCOMO benchmark](https://snap-research.github.io/locomo/)
- [SD Times — Demystifying Differential and Incremental Static Code Analysis](https://sdtimes.com/devops/demystifying-differential-and-incremental-analysis-for-static-code-analysis-within-devops/)
- [Berkeley TR 1997-946 — Practical Algorithms for Incremental Software Development](https://www2.eecs.berkeley.edu/Pubs/TechRpts/1997/CSD-97-946.pdf)
- [Lamport — Time, Clocks, and the Ordering of Events in a Distributed System](https://lamport.azurewebsites.net/pubs/time-clocks.pdf)
- [Vector clock — Wikipedia](https://en.wikipedia.org/wiki/Vector_clock)
- [Clocks and Causality — Ordering Events in Distributed Systems](https://www.exhypothesi.com/clocks-and-causality/)
- [NeurIPS 2025 — The Fragile Truth of Saliency](https://openreview.net/pdf?id=DrUR87D4Hj)
- [ACL 2025 — Behavioral Analysis of Information Salience in LLMs](https://aclanthology.org/2025.findings-acl.1204.pdf)
- [ACL 2025 — Scaling to Emphasize Attention for Long-Context Retrieval](https://aclanthology.org/2025.acl-long.1405.pdf)
