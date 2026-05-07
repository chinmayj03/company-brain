# ADR-004: Tiered Memory & Context Assembly Architecture

**Status:** Proposed  
**Date:** 2026-04-28  
**Deciders:** Chinmay (tech lead)  
**Depends on:** ADR-003 (FunctionContext schema)

---

## Context

### The Context Overflow Problem

ADR-003 defines a rich `FunctionContext` schema per entity. That is the right *extraction* architecture. But it creates a retrieval problem:

A workspace with 3 repos and 40 API endpoints might have:

```
40 endpoints × 8 entities each = ~320 entities
Each entity with FunctionContext JSON ≈ 600–900 tokens
Total stored knowledge ≈ 200,000–290,000 tokens
```

No LLM context window can hold that. Even a single endpoint:

```
1 endpoint × 8 entities × 700 tokens = 5,600 tokens
+ System prompt + conversation history + the answer = easily 8,000–10,000 tokens
```

That leaves almost nothing for reasoning — and it doesn't scale. A real codebase has hundreds of endpoints.

### The Deeper Problem: All Context is Not Equal

When a developer asks *"how does `getPayerCompetitors` work?"*, they need:
- **The controller method's purpose** in one sentence — always
- **The service's data access intent** — always
- **The repository's filter logic** — always
- **The 15 unrelated entities on the same endpoint** — never

The current design has no way to express this priority. Everything is stored at the same verbosity, retrieved in bulk, or not at all.

### What "Meaningful Results" Actually Requires

A knowledge system that serves AI well has three properties:

1. **Compressed representations** — each piece of knowledge has a short form (for bulk retrieval) and a detailed form (retrieved on demand)
2. **Semantic retrievability** — find relevant knowledge by meaning, not by exact keyword match
3. **Budget-aware assembly** — the context assembled for any given question fits within the LLM's window while maximising information density

This ADR designs all three.

---

## Decision Drivers

1. **Context window constraint** — assembled context must fit in 4,000–6,000 tokens (leaves room for system prompt, question, and answer)
2. **Answer quality** — the most relevant entities get the most detail; tangential entities get summaries
3. **Graph awareness** — call chains matter: controller → service → repository should be retrieved together, not by coincidence
4. **No embeddings dependency for MVP** — the architecture must work without a vector database initially (fall back to graph traversal + keyword ranking)
5. **Progressive enhancement** — add vector search later without changing the storage schema

---

## Architecture: Three Orthogonal Concerns

This is not one problem. It is three separate concerns that must be designed together:

```
┌─────────────────────────────────────────────────────────┐
│  CONCERN 1: STORAGE TIERS                               │
│  What granularity of knowledge do we store per entity?  │
└─────────────────────────────────────────────────────────┘
┌─────────────────────────────────────────────────────────┐
│  CONCERN 2: RETRIEVAL                                   │
│  Given a question, which entities are relevant?         │
└─────────────────────────────────────────────────────────┘
┌─────────────────────────────────────────────────────────┐
│  CONCERN 3: ASSEMBLY                                    │
│  How do we pack relevant knowledge into a budget?       │
└─────────────────────────────────────────────────────────┘
```

---

## Concern 1: Storage Tiers

Every entity has **four tiers of representation** stored at different granularity:

### T0 — Memory Token (always in RAM, ~15 tokens)
A single dense sentence encoding the entity's identity and key signals. This is what gets embedded and searched.

```
"getPayerCompetitors → reads payer_competitors+network_plans filtered by network/plan, 
 HIGH change risk, used by competitiveness dashboard"
```

Format: `{name} → {dataReads summary}, {changeRisk} change risk, {purpose fragment}`

Stored as: plain text in `nodes.memory_token` column. Also embedded as a vector.

### T1 — Structured Summary (~80–120 tokens)
The key `FunctionContext` fields flattened to a compact, human-readable block. Retrieved for most relevant entities.

```
[Repository] PayerCompetitorsRepository.findByNetworkAndPlan
Purpose: Returns competing payers for a network/plan, ranked by market_share
Reads: payer_competitors, network_plans | Writes: none
Filter: networkId + planId, effective_date ≤ now
Risk: MEDIUM performance (unbounded result set), HIGH change (breaks dashboard)
```

Stored as: generated text in `nodes.summary_t1` column. Regenerated whenever FunctionContext changes.

### T2 — Full FunctionContext (~400–700 tokens)
The complete structured JSON from ADR-003. Retrieved only for the 2–3 most directly relevant entities.

```json
{
  "purpose": "...", "dataReads": [...], "filterLogic": "...",
  "invariants": [...], "performanceRisk": "HIGH", "changeRisk": "HIGH",
  "gaps": ["unknown if soft-deleted records excluded"]
}
```

Stored as: JSONB in `nodes.metadata.functionContext`

### T3 — Raw Artifacts (on explicit request only)
Source code, full git history, raw SQL strings, call graph trace. Never included automatically.

Stored as: file system (source), Postgres (git clusters), computed on demand (graph trace)

---

### Storage Schema Changes

```sql
-- Add to existing graph_nodes table (no breaking changes)
ALTER TABLE graph_nodes ADD COLUMN memory_token   TEXT;
ALTER TABLE graph_nodes ADD COLUMN summary_t1     TEXT;
ALTER TABLE graph_nodes ADD COLUMN embedding      vector(768);  -- optional, add when ready

-- Index for fast token retrieval by workspace
CREATE INDEX idx_nodes_workspace_token ON graph_nodes(workspace_id) 
  WHERE memory_token IS NOT NULL;

-- Vector index (add when pgvector is available)
-- CREATE INDEX idx_nodes_embedding ON graph_nodes USING ivfflat (embedding vector_cosine_ops);
```

The `metadata` JSONB column already exists — `functionContext` lives inside it.

---

## Concern 2: Retrieval

Given a question, find the K most relevant entities. Two strategies, in order of preference:

### Strategy A: Endpoint-Scoped Graph Traversal (always available)

When the question references a known endpoint or entity by name, the graph itself is the index:

```
Question: "how does getPayerCompetitors work?"
  → Identify anchor: getPayerCompetitors (exact match in graph)
  → Traverse outbound edges: CALLS, READS_COLUMN, WRITES_COLUMN (depth ≤ 3)
  → Collect all reachable nodes in call chain
  → Score by: depth (closer = higher score) + edge_type weight
  → Return top-15 nodes
```

Edge weights for scoring:
- `CALLS` at depth 1 → score 1.0 (direct call chain, most relevant)
- `CALLS` at depth 2 → score 0.7
- `READS_COLUMN` → score 0.6 (data access, relevant for data questions)
- `CALLS_ENDPOINT` → score 0.5

This requires no embeddings. The graph structure captures the call chain exactly.

### Strategy B: Semantic Search via Memory Tokens (with embeddings)

When the question is conceptual ("which endpoints touch payer data?", "what has HIGH change risk?"):

```
Question → embed (768-dim) → cosine similarity against nodes.embedding
→ Return top-20 nodes by similarity score
→ Filter by workspace_id
```

Without pgvector: fall back to full-text search against `memory_token` column using Postgres `tsvector`:

```sql
SELECT *, ts_rank(to_tsvector('english', memory_token), query) AS rank
FROM graph_nodes
WHERE workspace_id = :wid
  AND to_tsvector('english', memory_token) @@ plainto_tsquery('english', :question)
ORDER BY rank DESC
LIMIT 20;
```

This covers most questions without a vector database.

### Strategy C: Hybrid (anchor + semantic expansion)

Best quality: start with graph traversal to find the call chain, then expand with semantic search to find related entities not directly connected.

```
anchor_nodes   = graph_traversal(question)      # call chain
semantic_nodes = semantic_search(question)       # conceptual matches
combined       = rank_and_merge(anchor_nodes, semantic_nodes)
```

---

## Concern 3: Assembly — Context Budget Manager

Given a ranked list of relevant nodes, assemble a context string that:
- Fits within `CONTEXT_BUDGET` tokens (default: 4,000)
- Maximises information density
- Is structured so the LLM can reason about it

### Budget Allocation

```
Total budget: 4,000 tokens
─────────────────────────────────────────────
System prompt (fixed):         ~400 tokens
Question (variable):           ~100 tokens
Graph path summary:            ~200 tokens
─────────────────────────────────────────────
Remaining for entity context:  ~3,300 tokens
─────────────────────────────────────────────
Top 2–3 entities (T2 detail):  ~600t × 3 = 1,800 tokens
Next 8–10 entities (T1 summary): ~100t × 10 = 1,000 tokens
Remaining for T0 tokens:         ~500 tokens (up to 30 more entities as one-liners)
```

### Assembly Algorithm

```python
def assemble_context(
    ranked_nodes: list[Node],
    budget: int = 4000,
    top_k_detail: int = 3,
) -> str:
    sections = []
    used = 0
    
    # 1. Graph path (always included)
    path = build_call_chain_summary(ranked_nodes)       # "Controller → Service → Repo"
    sections.append(("CALL CHAIN", path))
    used += estimate_tokens(path)
    
    # 2. Top entities at T2 detail
    for node in ranked_nodes[:top_k_detail]:
        block = render_t2(node)                          # full FunctionContext
        if used + estimate_tokens(block) < budget * 0.7:
            sections.append(("DETAIL", block))
            used += estimate_tokens(block)
    
    # 3. Next tier at T1 summary
    for node in ranked_nodes[top_k_detail:top_k_detail + 10]:
        block = node.summary_t1
        if used + estimate_tokens(block) < budget * 0.9:
            sections.append(("SUMMARY", block))
            used += estimate_tokens(block)
    
    # 4. Remaining as T0 one-liners
    remaining = ranked_nodes[top_k_detail + 10:]
    if remaining:
        oneliner_block = "\n".join(n.memory_token for n in remaining[:30])
        if used + estimate_tokens(oneliner_block) < budget:
            sections.append(("RELATED", oneliner_block))
    
    return format_context(sections)
```

### Context Output Format (what the LLM sees)

```
## CALL CHAIN
ApiEndpoint: GET /v1/mcheck/niq/competitiveness/summary/competitors/payer
  → CALLS PayerCompetitivenessController.getPayerCompetitors
  → CALLS PayerCompetitivenessService.getPayerCompetitors
  → CALLS PayerCompetitorsRepository.findByNetworkAndPlan
  → READS payer_competitors, network_plans

## DETAIL: PayerCompetitivenessService.getPayerCompetitors
Purpose: Orchestrates payer competitiveness lookup for a network/plan combination.
Reads: payer_competitors, network_plans
Filter: networkId, planId passed from controller; effective_date <= now enforced in repo
Side Effects: none
Performance Risk: MEDIUM — result set unbounded; caller paginates
Change Risk: HIGH — shape changes break competitiveness dashboard contract
Invariants: [always filtered by tenantId via RLS, always sorted by market_share DESC]
Gaps: [unclear if inactive plans are excluded]

## DETAIL: PayerCompetitorsRepository.findByNetworkAndPlan
Purpose: Data access layer for payer competitor records.
Reads: payer_competitors JOIN network_plans
Filter: network_id + plan_id, effective_date window
Performance Risk: MEDIUM — no pagination enforcement at DB level
Change Risk: HIGH — 3 upstream callers depend on return shape

## SUMMARY: PayerCompetitivenessController.getPayerCompetitors
[Controller] Maps HTTP params to service call, returns ResponseEntity<List<...>>.
Risk: LOW | Owner: payer-analytics

## RELATED (one-liners)
NetworkPlan → model entity for network/plan hierarchy, LOW risk
PayerCompetitor → core domain entity, MEDIUM change risk
...
```

This format is dense, structured, and explicit about what each entity is. The LLM can reason over it without needing to infer structure.

---

## Pipeline Changes

### New Pipeline Stage: T0/T1 Generation

After `ContextSynthesizer` (Pass 3), add a cheap local pass that generates `memory_token` and `summary_t1` from the `FunctionContext` output. **No LLM call needed** — these are deterministic template renders.

```python
def generate_memory_token(entity: ExtractedEntity, ctx: FunctionContext) -> str:
    reads = ", ".join(ctx.data_reads[:3]) or "no data reads"
    return (
        f"{entity.name} → {reads}, "
        f"{ctx.change_risk} change risk, "
        f"{ctx.purpose[:80]}"
    )

def generate_summary_t1(entity: ExtractedEntity, ctx: FunctionContext) -> str:
    return (
        f"[{entity.entity_type}] {entity.name}\n"
        f"Purpose: {ctx.purpose}\n"
        f"Reads: {', '.join(ctx.data_reads) or 'none'} | "
        f"Writes: {', '.join(ctx.data_writes) or 'none'}\n"
        f"Filter: {ctx.filter_logic or 'none specified'}\n"
        f"Risk: {ctx.performance_risk} perf, {ctx.change_risk} change"
    )
```

Both are written to the graph node alongside `FunctionContext`.

### New Java Service: ContextAssembler

A new `ContextAssemblerService` in the Java backend handles retrieval and assembly:

```java
public class ContextAssemblerService {

    // Called by QueryController when AI Ask receives a question
    public AssembledContext assembleForQuestion(
        UUID workspaceId,
        String question,
        int budgetTokens
    ) {
        // 1. Try to identify an anchor entity by name match in the question
        Optional<GraphNode> anchor = identifyAnchor(workspaceId, question);
        
        // 2. Retrieve relevant nodes
        List<ScoredNode> candidates = anchor
            .map(a -> graphTraversal(a, depth = 3))           // Strategy A
            .orElseGet(() -> fullTextSearch(workspaceId, question)); // Strategy B
        
        // 3. Assemble within budget
        return budgetAssembler.assemble(candidates, budgetTokens);
    }
}
```

This replaces the current approach of dumping the entire graph into the AI Ask prompt.

---

## Options Not Taken

### "Just use a vector database (Pinecone / Weaviate)"

Introducing an external vector DB adds operational complexity (another service to run, sync to keep, cost to pay) for marginal improvement over Postgres `tsvector` + graph traversal. Postgres `pgvector` extension gives 90% of the benefit without a new service. Deferred until scale demands it.

### "Store everything, let the LLM summarise on the fly"

Sending 50k tokens to a cloud LLM and asking it to "use what's relevant" is expensive ($0.15–$0.50 per question), slow, and wastes most of the context on irrelevant content. The assembly layer is the correct place to make this decision — not the LLM.

### "One universal embedding per entity, flat retrieval"

Flat retrieval ignores the graph structure. `getPayerCompetitors` and `findByNetworkAndPlan` might not be semantically similar (different vocabulary) but they are directly connected in the call chain. Graph traversal catches this; flat embedding search misses it.

---

## Consequences

**Easier:**
- AI Ask answers will be focused and grounded — the LLM gets exactly what it needs
- Adding a new entity to the graph automatically makes it retrievable (memory_token generation is in the pipeline)
- The same assembly logic serves: chat, diff review, PR context, onboarding docs
- No external dependency needed for MVP (pure Postgres)

**Harder:**
- Two new text columns per node to keep in sync with FunctionContext changes
- Need a `ContextAssemblerService` in Java backend (new class but not complex)
- Budget tuning is empirical — the right `top_k_detail` and token splits need testing

**What we'll revisit:**
- ADR-005: Add `pgvector` embeddings when full-text search proves insufficient for conceptual questions
- ADR-006: Human annotation loop — corrections to FunctionContext should invalidate and regenerate T0/T1 immediately
- ADR-007: Caching assembled contexts per (workspaceId, question_hash) — most questions recur

---

## Implementation Phases

### Phase 1 — Storage (0.5 day)
1. [ ] Add `memory_token TEXT` and `summary_t1 TEXT` columns to `graph_nodes`
2. [ ] Add `generate_memory_token()` and `generate_summary_t1()` functions in Python pipeline
3. [ ] Wire into `JavaGraphClient._entity_to_dict()` → post alongside `functionContext`
4. [ ] Java: `PipelineService` writes both columns on pipeline result

### Phase 2 — Retrieval (1 day)
5. [ ] Java: `GraphTraversalService.getCallChain(nodeId, depth)` — already partial, extend to return scored nodes
6. [ ] Java: Full-text search on `memory_token` using Postgres `tsvector` (fast to add, no new dependency)
7. [ ] Java: `ContextAssemblerService` — assembles T2/T1/T0 blocks within budget

### Phase 3 — AI Ask Integration (0.5 day)
8. [ ] Replace current "dump all node data" approach in `QueryController` with `ContextAssemblerService.assembleForQuestion()`
9. [ ] Test with 3–5 real questions against the competitiveness endpoint

### Phase 4 — Validation (0.5 day)
10. [ ] Measure: does the assembled context fit in <4,000 tokens for a typical question?
11. [ ] Measure: does the AI Ask answer quality improve vs current approach?
12. [ ] Identify gaps — which question types still produce poor answers → feeds ADR-005

---

## Summary

The fundamental insight is that a company-brain is a **retrieval system**, not a database dump. Storage quality (ADR-003) and retrieval quality (this ADR) are separate concerns that must both be solved.

The three-tier memory model (T0 memory token → T1 summary → T2 full context) mirrors how human experts recall knowledge: a name + signal (instant), a working summary (on request), full detail (when needed). The context assembly algorithm makes the LLM act like an expert who has already skimmed the relevant docs, not one who has to read the entire codebase.
