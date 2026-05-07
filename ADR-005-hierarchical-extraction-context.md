# ADR-005: Hierarchical Extraction Context Architecture (L1/L2/Main Memory)

**Status:** Proposed  
**Date:** 2026-04-28  
**Deciders:** Chinmay (architect, lead)  
**Area:** AI extraction layer — `company-brain-ai`

---

## Context

The current extraction pipeline is **node-blind**: each `EntityExtractor` LLM call sees only the
single `CodeUnit` it was handed. It has no memory of what was extracted from the previous class,
no sense of the business vocabulary being built up, and no access to historical runs for the same
workspace. The result is:

- Repeated discovery of the same domain terms per call (wasted tokens)
- Inconsistent entity naming across classes in the same run
- Zero cross-pollination between the handler extraction and the service/repository it calls
- No improvement over successive runs on the same codebase

The extraction layer is the **highest-value layer** of the entire system — it is where raw code
becomes structured knowledge. It deserves the most sophisticated context management.

The inspiration is the CPU memory hierarchy: L1/L2 cache + main memory managed by an OS
scheduler. The OS doesn't give every process everything — it pages in exactly what is needed,
when it is needed, subject to a budget (cache lines / token window).

---

## Decision

Introduce a **three-tier context hierarchy** with a **Context Manager Agent** acting as the OS
that assembles the optimal prompt for every LLM extraction call.

```
┌─────────────────────────────────────────────────────────────────┐
│                     Context Hierarchy                           │
│                                                                 │
│   ┌──────────────────────────────────────────────────────┐     │
│   │  L1: Node-Local Context           ~3,200 tokens      │     │
│   │  ─────────────────────────────────────────────────   │     │
│   │  • Current code unit (trimmed)                       │     │
│   │  • Direct call chain (1-hop from CodeTracer)         │     │
│   │  • Imports collapsed to type names                   │     │
│   │  • Last 3 git commits for this exact file            │     │
│   │  • Node-specific user annotations                    │     │
│   └──────────────────────────────────────────────────────┘     │
│           ↑ always present                                      │
│   ┌──────────────────────────────────────────────────────┐     │
│   │  L2: Pipeline Shared Context      ~2,400 tokens      │     │
│   │  ─────────────────────────────────────────────────   │     │
│   │  • Domain glossary (terms discovered this run)       │     │
│   │  • Service registry (class → role, seen so far)      │     │
│   │  • Architecture pattern library (SAGA, CQRS…)        │     │
│   │  • Cross-cutting concerns (auth, audit, retry…)      │     │
│   │  • Field name → business meaning map                 │     │
│   └──────────────────────────────────────────────────────┘     │
│           ↑ grows across the run; top-k injected per call       │
│   ┌──────────────────────────────────────────────────────┐     │
│   │  Main Memory: Graph Store         ~1,200 tokens      │     │
│   │  ─────────────────────────────────────────────────   │     │
│   │  • Existing high-confidence nodes for this workspace │     │
│   │  • Historical context entries for related nodes      │     │
│   │  • Prior pipeline run results (what worked)          │     │
│   └──────────────────────────────────────────────────────┘     │
│           ↑ selective; only fetched when confidence < threshold │
└─────────────────────────────────────────────────────────────────┘
                              ▼
         ┌────────────────────────────────────────┐
         │       Context Manager Agent            │
         │  Fast model (Haiku / Phi-3-mini)       │
         │  ─────────────────────────────────     │
         │  ① Profile: node type, role, language  │
         │  ② Score candidates from all 3 tiers   │
         │  ③ Fit token budget (evict lowest ROI) │
         │  ④ Emit optimised system prompt        │
         │  ⑤ Post-extraction: update L2          │
         └────────────────────────────────────────┘
                              ▼
              ┌───────────────────────────┐
              │  Main Extraction LLM Call │
              │  (entity / relationship / │
              │   context synthesis)      │
              └───────────────────────────┘
```

---

## Components

### 1. `L1NodeContext` — always in the prompt

Built by `CodeTracer` (already exists). Holds:

| Field | Content | Token budget |
|---|---|---|
| `current_unit` | Source of the class being extracted (trimmed to 1,500t) | ~1,500 |
| `call_chain` | 1-hop callers/callees — signatures only, no bodies | ~500 |
| `git_summary` | Last 3 commits touching this file (message + author) | ~300 |
| `annotations` | User annotations anchored to this node | ~400 |
| `imports` | Collapsed to fully-qualified type names | ~200 |

Eviction policy (when over budget):
1. Inner class bodies → collapsed to signatures
2. Method bodies > 30 lines → first 10 lines + `// ... trimmed`
3. Commit messages → first line only
4. Annotations oldest-first

### 2. `L2SharedContext` — the accumulating brain

One instance per `run_pipeline()` invocation. Serialised to Redis at job completion for
follow-up queries.

```python
@dataclass
class L2SharedContext:
    # Domain vocabulary extracted so far
    domain_glossary: dict[str, str]        # "niq" → "Network IQ — a competitiveness scoring service"
    
    # Services seen so far (class_name → role, description)
    service_registry: dict[str, ServiceEntry]
    
    # Architecture patterns detected
    pattern_library: list[ArchPattern]     # SAGA, CQRS, Event Sourcing, etc.
    
    # Cross-cutting concerns (auth, audit, retry decorators, etc.)
    cross_cutting: list[CrossCuttingEntry]
    
    # Field-level business semantics
    field_semantics: dict[str, str]        # "niq_score" → "0–100 competitiveness rank"
    
    # High-confidence entities discovered (for reference by later calls)
    entity_catalog: list[EntitySummary]   # top-20 by confidence, trimmed for injection
    
    def top_k_for_injection(self, node_role: str, budget_tokens: int) -> str:
        """Context Manager Agent calls this to get the most relevant L2 slice."""
        ...
    
    def update_from_extraction(self, result: ExtractionResult) -> None:
        """Called by SharedContextAccumulator after every successful extraction."""
        ...
```

**Accumulation rules:**

| Trigger | L2 update |
|---|---|
| New entity with `entity_type=Service` | Add to `service_registry` |
| Entity name contains unknown abbreviation | Add to `domain_glossary` (CM Agent guesses expansion) |
| 3+ entities reference the same class | That class promoted to `entity_catalog` |
| `@Transactional` + event publishing detected | Pattern: SAGA candidate added |
| `@PreAuthorize` or `SecurityContext` seen | Cross-cutting: auth concern logged |
| Field with `risk`, `score`, `rate` suffix | Add to `field_semantics` |

### 3. `GraphStoreContext` — main memory paging

Fetched from the Java backend (existing `NodeRepository`). Only paged in when:

- Node type is `Service` or `ApiEndpoint` (highest value, most context-dependent)
- Extraction confidence on previous pass was < 0.6
- CM Agent requests it (scored as high-ROI)

```python
class GraphStoreContext:
    async def fetch_related(
        self,
        workspace_id: str,
        node_name: str,
        node_type: str,
        limit: int = 5,
    ) -> list[GraphContextEntry]:
        """
        Calls Java backend GET /v1/nodes/search?q=node_name&nodeType=node_type&limit=k
        Returns existing nodes' context entries as seed context.
        """
```

### 4. `ContextManagerAgent` — the OS

This is the key innovation. A **separate, fast LLM call** (Haiku or Phi-3-mini, ~500ms)
that runs before the main extraction call. It receives:

```
You are a Context Manager for an LLM extraction pipeline.
You must assemble the optimal context for extracting entities from a {node_role} class.

Available context candidates:
--- L1 (always included, DO NOT evict) ---
{l1_summary}

--- L2 candidates (pick top-3 most relevant for a {node_role}) ---
{l2_candidates_with_relevance_scores}

--- Main Memory candidates (include only if extraction confidence risk is HIGH) ---
{mm_candidates}

Token budget remaining: {remaining_tokens}

Respond with:
1. INJECT: list of L2/MM items to include (by ID)
2. EVICT: list of L1 items to trim (e.g. "trim method bodies > 20 lines")
3. SYSTEM_PROMPT_PATCH: 1-2 sentences to append to the extraction system prompt
   based on what you see (e.g. "This service implements the SAGA pattern; model 
   compensating transactions as separate entities.")
4. CONFIDENCE_PRIOR: estimated extraction difficulty (LOW/MEDIUM/HIGH)
```

The `SYSTEM_PROMPT_PATCH` is the high-value output: it injects domain-specific guidance
**derived from accumulated L2 knowledge** into the extraction prompt, making later extractions
richer than earlier ones.

### 5. `SharedContextAccumulator` — post-extraction updater

Runs after every successful `_extract_from_code_unit()`. Lightweight, rule-based (no LLM call).
Updates L2 with discoveries from the just-completed extraction.

---

## Data Flow

```
for each CodeUnit in FocalContext:

  1.  L1NodeContext.build(code_unit, focal_context)           # always
  2.  L2SharedContext.top_k_for_injection(role, budget)       # accumulated
  3.  GraphStoreContext.fetch_related(workspace, name, type)  # conditional
  4.  ContextManagerAgent.assemble(l1, l2, mm, budget)        # fast LLM call
       → returns: injected_context + system_prompt_patch
  5.  EntityExtractor.extract(l1 + injected_context,          # main extraction
                               system_prompt_patch)
  6.  SharedContextAccumulator.update(l2, extraction_result)  # updates L2
  7.  ExtractionResult → entities, contexts, relationships
```

---

## Options Considered

### Option A: Current (flat per-node, no shared context)
| Dimension | Assessment |
|---|---|
| Complexity | Low |
| Context quality | Poor — each call starts cold |
| Cross-node consistency | None |
| Improvement over time | None |

**Pros:** Simple. No overhead per call.  
**Cons:** 5th extraction knows nothing the 1st found. Entity names drift. Domain vocab re-discovered per call.

### Option B: Full context injection (send everything to every call)
| Dimension | Assessment |
|---|---|
| Complexity | Low |
| Context quality | High initially, degrades as context grows |
| Token cost | Prohibitive (40k+ tokens per call after 20 nodes) |
| Improvement over time | Diminishing returns fast |

**Pros:** Simple. No management layer needed.  
**Cons:** Context window overflow. Irrelevant context dilutes signal. Unscalable.

### Option C: This ADR — Tiered hierarchy with Context Manager Agent
| Dimension | Assessment |
|---|---|
| Complexity | Medium |
| Context quality | High and improving — L2 grows, CM Agent optimises |
| Token cost | Controlled — budget-managed per call |
| Improvement over time | Yes — L2 accumulates, MM seeds re-runs |

**Pros:** Extraction quality improves as the run progresses. Budget-controlled. CM Agent patches the
system prompt with domain knowledge. Re-runs on the same workspace benefit from prior results.  
**Cons:** One extra fast LLM call per extraction (~$0.0001 per call at Haiku pricing). Slightly more code.

### Option D: Single shared context prefix (no CM Agent)
Inject L2 as a fixed prefix to every extraction call, without the CM Agent.

| Dimension | Assessment |
|---|---|
| Complexity | Low |
| Context quality | Medium — static injection, not adaptive |
| Optimisation | None — no system prompt patching |

**Verdict:** Reasonable halfway step, but misses the `SYSTEM_PROMPT_PATCH` — the most valuable
output of the CM Agent. We'd get shared vocabulary but not adaptive guidance.

---

## Trade-off Analysis

**Token cost of CM Agent call vs. benefit:**  
A Haiku call costs ~$0.00025 for 1k input + 200 output tokens. For 20 CodeUnits per pipeline run,
that's $0.005 per run — negligible against the value of better extraction.

**L2 staleness:**  
L2 is in-memory per pipeline run. It cannot be stale within a run. Between runs, it's seeded from
the graph store (Main Memory). Risk: low.

**CM Agent latency:**  
Haiku p50 latency is ~300ms. For a sequential pipeline with 10 code units, that adds 3s to a
pipeline that already takes 30–120s. Acceptable. Can be made async (fire CM for unit N+1 while
extracting unit N).

**Ordering sensitivity:**  
The quality of L2 depends on extraction order. The handler/controller (extracted first via
CodeTracer ordering) sets up the best L2 seed for the services that follow. CodeTracer already
returns units in call-graph order — this is a free win.

---

## Consequences

**What becomes easier:**
- Entity names stay consistent across a run (L2 glossary enforces vocabulary)
- Service boundaries are detected earlier (pattern library)
- Re-runs on the same codebase get a head start (Main Memory seeding)
- System prompts are dynamically tuned per node type without manual prompt engineering

**What becomes harder:**
- Debugging: a bad extraction may be due to CM Agent's context selection, not the extractor
- L2 can propagate wrong assumptions if the first extraction misidentifies a pattern
- Need a fallback if CM Agent call fails (degrade to flat extraction, not abort)

**What we'll need to revisit:**
- CM Agent model choice: start with Haiku, evaluate vs. Phi-3-mini (local, free)
- L2 serialisation format (JSON for Redis; needs to be compact)
- Budget allocation per node type (controllers may need more L1, models need more L2)

---

## Implementation Plan

### Phase 1 — L2 Shared Context (no CM Agent yet)
1. Create `L2SharedContext` dataclass with `update_from_extraction()` and `top_k_for_injection()`
2. Thread L2 through `run_pipeline()` — pass to each `_extract_from_code_unit()` call
3. Inject top-5 L2 items as a `### Workspace Context` section in the extraction user prompt
4. Add `SharedContextAccumulator` (rule-based, no LLM)
5. Verify: later extractions in a run should have richer context than earlier ones

**Acceptance test:** Run pipeline on `competitiveness` endpoint. Entity #8 (`NiqScoreCalculator`)
should have `niq` defined as "Network IQ" in its extraction, sourced from L2 — even though it
wasn't in L1 for that class.

### Phase 2 — Context Manager Agent
1. Create `ContextManagerAgent` class (Haiku/Phi-3-mini call)
2. Replace manual L2 injection with CM Agent assembly
3. Add `SYSTEM_PROMPT_PATCH` output → injected into extraction system prompt
4. Add `CONFIDENCE_PRIOR` → skip Main Memory fetch if LOW

**Acceptance test:** Extract a `@Service` class that has no git history. CM Agent should inject
the handler's call-chain context from L2, and patch the system prompt with
"This service is called by CompetitivenessController; model its outputs as competitiveness scores."

### Phase 3 — Main Memory Paging
1. Add `GraphStoreContext.fetch_related()` — calls Java `/v1/search`
2. CM Agent gates the fetch behind `CONFIDENCE_PRIOR = HIGH`
3. Re-run pipeline on same workspace — existing nodes seed extraction of changed nodes

### Phase 4 — Async CM Agent
1. Pipeline pre-fetches CM assembly for unit N+1 while extracting unit N
2. Effectively removes CM Agent latency from critical path

---

## Files to Create/Modify

| File | Action |
|---|---|
| `pipeline/context_hierarchy.py` | New — `L1NodeContext`, `L2SharedContext`, `GraphStoreContext` |
| `pipeline/context_manager_agent.py` | New — CM Agent LLM call + prompt assembly |
| `pipeline/shared_context_accumulator.py` | New — rule-based L2 updater |
| `pipeline/entity_extractor.py` | Modify — accept assembled context from CM Agent |
| `pipeline/orchestrator.py` | Modify — thread L2 through pipeline loop |
| `llm/prompts.py` | Modify — extraction prompt accepts `system_prompt_patch` param |

---

## Appendix: Example CM Agent Output

**Input node:** `NiqScoreCalculatorService.java` — role: `service`, called by `CompetitivenessController`

**CM Agent output:**
```json
{
  "inject": ["l2:domain_glossary.niq", "l2:service_registry.PayerDataService", "l2:field_semantics.niq_score"],
  "evict": ["trim method bodies > 25 lines in current unit"],
  "system_prompt_patch": "This service computes NIQ (Network IQ) competitiveness scores for payers. Model 'niq_score' as a 0-100 rank. PayerDataService is a known dependency that provides raw payer metrics.",
  "confidence_prior": "MEDIUM"
}
```

**Result:** The extraction LLM now knows what `niq` means, what `PayerDataService` does, and how
`niq_score` should be modelled — knowledge it could only have gotten from previous extractions
in this run.
