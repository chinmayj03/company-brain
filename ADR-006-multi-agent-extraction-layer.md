# ADR-006: Multi-Agent Extraction Layer — LLM-Driven Universal Code Navigation

**Status:** Proposed  
**Date:** 2026-04-28  
**Deciders:** Chinmay  
**Supersedes:** ADR-005 (Hierarchical Context L1/L2/Main Memory)  
**Related:** ADR-003 (Code Tracing), ADR-005 (Context Hierarchy)

---

## Context

### What We Have Today

The current extraction pipeline uses a **regex + heuristic** approach to navigate codebases:

- `CodeTracer` scans for `@RestController`, `@GetMapping`, `@Service`, `@Repository` annotations
- `SmartMethodExtractor` does brace-balanced walking and `fieldName.method(` pattern matching
- Interface resolution (`port/out` → `adapters/db`) was hand-coded once we hit hexagonal architecture
- Result: 292 tokens of focused code extracted per endpoint — a 10× improvement over file-level reading

### Why This Is Still Not Enough

Every fix revealed the next hardcoding problem:

| Problem hit | Heuristic added | What breaks next |
|---|---|---|
| Files too large | Trim to 4,000 chars | Signal/noise 10% |
| Context overflow | SmartMethodExtractor method-level | Hexagonal arch not followed |
| Interface not resolved | `implements XInterface` scan | What about Kotlin, Python, TypeScript? |
| Repository not found | Remove `get*` getter filter | What about DDD aggregates, CQRS, event sourcing? |
| Spring-specific annotations | Works | FastAPI, NestJS, Django, Rails — not covered |

The root problem: **we are encoding architecture knowledge in regex**, which means every new pattern (hexagonal, CQRS, micro-frontend, GraphQL resolvers, gRPC, event sourcing) requires a new heuristic. This is the same trap static analysis tools have been stuck in for 20 years.

### The Insight

A senior engineer navigating an unfamiliar codebase doesn't look for `@RestController`. They:
1. Read the entry point and follow the intent of the code
2. Ask "what is this method actually doing, what does it depend on?"
3. Understand the business concept being served
4. Recognize patterns from experience ("this looks like a SAGA", "this is a read-model projection")

LLMs already have this experience baked in. We should be using them to navigate, not just to extract after a regex navigator has already done the work.

---

## Decision

Replace the regex-based `CodeTracer` + `SmartMethodExtractor` navigation stack with a **four-agent extraction pipeline** where LLMs drive every navigation decision. No architecture pattern is hardcoded. The system learns what is relevant through LLM reasoning over actual code.

---

## Architecture: The Four-Agent Extraction Council

```
Endpoint / Entry Point
        │
        ▼
┌───────────────────┐
│  NAVIGATOR AGENT  │  "Find everything relevant to this endpoint"
│                   │  – reads entry file, follows calls, discovers nodes
│  Role: BALANCED   │  – language-agnostic: reads any file, any framework
└────────┬──────────┘
         │  node list + file paths
         ▼
┌───────────────────┐
│ IMPLEMENTATION    │  "What does each node actually do?"
│ AGENT             │  – reads focused method/function bodies
│                   │  – extracts contracts: inputs, outputs, side-effects
│  Role: BALANCED   │  – identifies: DB calls, HTTP calls, events, cache
└────────┬──────────┘
         │  implementation summaries
         ▼
┌───────────────────┐
│ LAYER AGENT       │  "What layer does each node belong to?"
│                   │  – classifies: controller, service, repo, client, model
│  Role: FAST       │  – detects: hexagonal ports, DDD aggregates, CQRS reads
│                   │  – no hardcoding: inferred from code structure + LLM
└────────┬──────────┘
         │  layer-annotated nodes
         ▼
┌───────────────────┐
│ BUSINESS CONTEXT  │  "Why does this exist? What problem does it solve?"
│ AGENT             │  – reads git history, PR descriptions, comments
│                   │  – maps entities to domain concepts (Payer, NIQ, TAM)
│  Role: SYNTHESIS  │  – produces: intent summary, invariants, change risk
└────────┬──────────┘
         │  enriched entity graph
         ▼
        L2 Shared Context + Graph Store
```

---

## The Four Agents in Detail

### Agent 1: Navigator Agent

**Job:** Starting from an entry point (HTTP route, event handler, CLI command, GraphQL resolver — anything), discover the full set of nodes involved in serving that request.

**How it works:**
```
Input:  entry_file_path + entry_method_name
Output: list of (file_path, class/function name, reason_for_relevance)

Algorithm (LLM-driven):
1. Read the entry method
2. Ask LLM: "List every function call, dependency, or external reference in this code.
             For each, tell me which file/module it likely lives in."
3. For each reference LLM identifies:
   a. Search the codebase for that file/class/function
   b. Read it
   c. Recurse (depth-limited, typically 3 hops)
4. Stop when: depth limit hit, or LLM says "this is a leaf (DB, external API, stdlib)"
```

**Key property:** The LLM decides what to follow — not regex. If the code uses `@Component`, `@Injectable`, constructor injection, module imports, service locator, or any other DI pattern, the LLM reads the code and understands it.

**Language agnostic:** Works for Java, TypeScript, Python, Go, Kotlin, Scala — whatever the LLM was trained on.

---

### Agent 2: Implementation Agent

**Job:** For each node the Navigator found, extract the meaningful implementation — not the whole file, not a truncated file, but exactly what matters.

**How it works:**
```
Input:  file_path + class/function name
Output: ImplementationSummary {
  signature: "getPayerCompetitors(request, viewBy, basePayerName): PayerSummaryDTO"
  what_it_does: "Fetches competitor payer data filtered by period/market from DB"
  calls_out: ["competitivenessRepository.getPayerCompetitors()", "validateRequest()"]
  data_contracts: {
    inputs: ["NiqAPIRequest (body)", "VIEW_BY (param)", "String basePayerName (param)"]
    outputs: ["CompetitivenessPayerSummaryDTO"]
    side_effects: ["read-only DB query via repository"]
  }
  is_leaf: false
  raw_focused_code: "..."   ← trimmed method body, used by downstream agents
}
```

**Key difference from current approach:** Rather than extracting the method body mechanically (brace counting), the LLM reads the full file, identifies the target method, and produces a structured summary. The raw code is still included for downstream agents.

**Handles any pattern:** No special handling needed for interface default methods, lambdas, async/await, generators, decorators — the LLM reads them all.

---

### Agent 3: Layer Agent

**Job:** Classify each node into the architectural layer it belongs to, and detect patterns — without hardcoding any architecture.

**How it works:**
```
Input:  ImplementationSummary + file path + surrounding class context
Output: LayerClassification {
  layer: "controller" | "service" | "repository" | "model" | "client" | 
         "event_handler" | "projection" | "saga" | "gateway" | "component"
  pattern_detected: "hexagonal_output_port" | "cqrs_read_model" | "event_sourcing" 
                    | "standard_mvc" | "graphql_resolver" | "grpc_handler" | null
  confidence: 0.0–1.0
  reasoning: "This class implements an interface named XRepository and is annotated 
              @Component, suggesting it is the infrastructure adapter for the X output port"
}
```

**Key property:** The LLM infers architecture patterns from code structure, naming, and annotations — not from a lookup table. It works equally well on:
- Spring Boot hexagonal (port/out → adapters/db)
- NestJS (controllers → services → TypeORM repositories)
- FastAPI (routers → services → SQLAlchemy models)
- Rails (controllers → models → ActiveRecord)
- DDD (application services → domain aggregates → repositories)
- CQRS (command handlers → event store / query handlers → read models)

---

### Agent 4: Business Context Agent

**Job:** Answer "why does this code exist?" using git history, comments, PR titles, and the domain language visible in the code.

**How it works:**
```
Input:  node list + layer classifications + git commits touching these files
Output: BusinessContext {
  domain_concept: "Payer Competitiveness Analysis"
  business_intent: "Allows network managers to see how their payer mix compares 
                    to competitors in a given market period"
  key_invariants: ["Always filtered by workspace (tenant)", "Market period is required"]
  change_risk: "high"  ← many recent changes, multiple authors
  glossary_terms: {"NIQ": "Network Intelligence Quotient", "TAM": "Total Addressable Market"}
  owner_signals: ["recent commits by @alice", "referenced in JIRA NIQ-1234"]
}
```

This agent is what turns extracted code into **actual company brain** — domain knowledge that would otherwise only live in the heads of senior engineers.

---

## Navigation Strategy: Iterative Deepening with Agent Guidance

The Navigator uses **iterative deepening** rather than a fixed depth scan:

```
Round 1 (depth 1):  Entry point only — LLM reads it, lists immediate dependencies
Round 2 (depth 2):  For each dependency, read + follow one more hop
Round 3 (depth 3):  Follow repo/client calls — likely DB or external service (leaf)

After each round, Layer Agent classifies what was found:
- "leaf" nodes (DB, external API, stdlib) → stop, don't recurse
- "glue" nodes (DTOs, mappers, validators) → read but don't recurse deeper
- "core" nodes (services, aggregates) → recurse further
```

**Token budget per round:** Each LLM call in the Navigator stays small because it reads one file at a time and produces a structured list, not full code content. Typical: 300 tokens in, 200 tokens out per file visited.

---

## Options Considered

### Option A: Current Approach (Regex + Heuristics) — Baseline
| Dimension | Assessment |
|-----------|------------|
| Language coverage | Java Spring Boot only |
| Architecture patterns | MVC, partial hexagonal (hardcoded) |
| New pattern adaptation | Manual regex addition required |
| Token efficiency | 292 tokens (after SmartMethodExtractor) |
| Latency | ~0ms (pure regex, no LLM calls in navigation) |
| Accuracy | Medium — misses interfaces, misclassifies layers |

**Pros:** Fast (no LLM calls for navigation), deterministic  
**Cons:** Brittle, language-locked, architecture-locked, requires manual updates for every new pattern

---

### Option B: Multi-Agent LLM Navigation (This ADR) — Proposed
| Dimension | Assessment |
|-----------|------------|
| Language coverage | Any language the LLM knows |
| Architecture patterns | Any — detected by LLM reasoning |
| New pattern adaptation | Zero — the LLM handles it |
| Token efficiency | ~600–1200 tokens per navigation run |
| Latency | 2–5 LLM calls for navigation (parallelisable) |
| Accuracy | High — LLM follows semantic intent, not syntax |

**Pros:** Universal, self-adapting, understands intent not just structure  
**Cons:** More LLM calls, non-deterministic (but stable with low temp), slightly slower

---

### Option C: AST-Based Static Analysis + LLM Classification
| Dimension | Assessment |
|-----------|------------|
| Language coverage | Only languages with AST parsers (javalang, tree-sitter) |
| Architecture patterns | Framework-specific (Spring, Django, Express) |
| Setup cost | High — parser per language |
| Accuracy | High for syntax, Low for intent |

**Pros:** Deterministic, fast  
**Cons:** Still requires per-language tooling, still can't detect intent-level patterns

**Verdict:** Solves token efficiency but not universality. Build on top of Option B as an optional accelerator, not as the foundation.

---

## Trade-off Analysis

**The core trade-off** is navigation speed vs. navigation intelligence:

| | Regex (A) | Multi-Agent LLM (B) |
|---|---|---|
| Understands hexagonal arch | After hardcoding | Natively |
| Understands CQRS | Never | Yes |
| Understands TypeScript DI | Never | Yes |
| Adapts to new patterns | Never without code changes | Always |
| Time to first result | ~50ms | ~3–8s (parallelised LLM calls) |
| Correct code extraction | ~60% | ~90%+ |

**3–8 seconds** for navigation is acceptable because: (a) this runs once per endpoint ingestion, not on every query; (b) it replaces hours of manual documentation; (c) it's parallelisable across repos.

**Non-determinism** is addressed by: low temperature (0.0–0.1), structured JSON outputs, validation of outputs against actual filesystem (every file path the Navigator suggests is verified to exist before being read).

---

## Consequences

**What becomes easier:**
- Adding a new repo in any language works without code changes
- Hexagonal, CQRS, DDD, micro-frontends all handled identically
- The business context layer is dramatically richer
- New team members can immediately query "what does this endpoint do and why"

**What becomes harder:**
- Debugging navigation failures requires understanding LLM reasoning
- Testing is harder — outputs are probabilistic, not deterministic
- Higher Ollama load during ingestion (more LLM calls per pipeline run)

**What we'll need to revisit:**
- Caching: navigator outputs should be cached by (file_hash, entry_method) so re-ingestion is fast
- Confidence thresholds: when should Navigator Agent ask for human confirmation?
- Parallel vs serial: Navigator can visit multiple files in parallel; Layer Agent can run concurrently with Implementation Agent

---

## Implementation Plan

### Phase 1: Navigator Agent (Week 1)
Replace `CodeTracer._find_java_handler()` and `SmartMethodExtractor` with a Navigator Agent that:
- Takes (file_path, method_name) as entry point
- Uses LLM to list dependencies from that method's source
- Resolves each to a real file path
- Returns `list[NavigatorNode(file_path, class_name, method_name, role_hint)]`

Keep the regex handler-finder as the entry point discovery (it's fast and reliable for HTTP routes). The Agent takes over once the entry file is found.

### Phase 2: Implementation Agent (Week 1–2)
Replace `SmartMethodExtractor.extract_chain()`:
- For each NavigatorNode, ask LLM to extract the implementation summary
- Output: structured `ImplementationSummary` (replaces raw `focused_code`)
- The LLM-extracted summary is the `CodeUnit.content` fed to EntityExtractor

### Phase 3: Layer Agent (Week 2)
New agent, runs after Implementation Agent:
- Classifies each node into architectural layer
- Detects patterns (hexagonal, CQRS, etc.)
- Annotations fed into L2 context for EntityExtractor

### Phase 4: Business Context Agent (Week 3)
Replaces current `ContextSynthesizer` (which was git-only):
- Combines git history + code comments + domain terms
- Produces glossary, invariants, change risk per entity
- Persists to graph as `NodeContext` records

### Phase 5: Caching + Incremental Updates (Week 4)
- Cache navigator outputs by file hash
- Only re-run agents on changed files (git diff-aware)
- Support for multi-repo navigation (agent follows imports across repo boundaries)

---

## Data Model: NavigatorNode

```python
@dataclass
class NavigatorNode:
    """A single node discovered by the Navigator Agent."""
    file_path: str            # absolute path, verified to exist
    repo_name: str
    class_name: str           # class or module name
    method_name: str          # specific method/function of interest
    role_hint: str            # LLM's initial role guess: "service", "repository", etc.
    discovery_reason: str     # why the Navigator included this node
    hop_depth: int            # 0 = entry point, 1 = called by entry, etc.
    is_leaf: bool             # True = DB/external/stdlib — don't recurse further
    raw_source: str           # the actual source code of the method (read after discovery)
```

This replaces both `CodeUnit` (which held whole-file content) and `MethodExtract` (which held method-level content). `NavigatorNode` is richer — it carries the *reason* it was included, which the downstream agents use to prioritise their analysis.

---

## Prompt Templates (Sketches)

### Navigator Agent Prompt
```
You are navigating a codebase to understand how an API endpoint works.

Entry point:
  File: {file_path}
  Method: {method_name}
  Source:
  ```{language}
  {source_code}
  ```

Task: List every function, class, or dependency this method relies on that is 
relevant to understanding its behaviour. For each:
- Give the likely file path or module name
- Give the class/function name
- Say whether it's a "leaf" (DB, external API, stdlib) or "branch" (business logic to follow)
- Give a one-line reason why it matters

Return JSON: {"nodes": [{"file_hint": "...", "name": "...", "is_leaf": bool, "reason": "..."}]}
```

### Layer Agent Prompt
```
You are classifying the architectural role of a code module.

Module: {class_name} in {file_path}
Summary: {implementation_summary}

Classify this module:
- layer: controller | service | repository | model | client | event_handler | projection | saga | gateway | component | unknown
- pattern: hexagonal_port | cqrs_command | cqrs_query | event_sourcing | standard_mvc | graphql | grpc | null
- confidence: 0.0–1.0
- reasoning: one sentence explaining your classification

Return JSON: {"layer": "...", "pattern": "...", "confidence": 0.9, "reasoning": "..."}
```

---

## Action Items

1. [ ] Create `NavigatorAgent` class in `collectors/navigator_agent.py`
2. [ ] Create `ImplementationAgent` class in `collectors/implementation_agent.py`  
3. [ ] Create `LayerAgent` class in `pipeline/layer_agent.py`
4. [ ] Refactor `BusinessContextAgent` from current `ContextSynthesizer` (git-only → richer)
5. [ ] Replace `CodeTracer._trace_java()` with Navigator Agent call (keep handler finder)
6. [ ] Update `CodeUnit` → `NavigatorNode` data model across pipeline
7. [ ] Add file-path resolution and existence verification to Navigator outputs
8. [ ] Add navigator output caching keyed by (file_hash, method_name)
9. [ ] Write integration test against `network-iq-backend-java` `getPayerCompetitors` endpoint
10. [ ] Write ADR-007 for the caching and incremental update strategy
