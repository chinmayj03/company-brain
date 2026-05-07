# ADR-001: Enhanced Extraction Pipeline for Deep Codebase Understanding

**Status:** Proposed  
**Date:** 2026-05-07  
**Deciders:** Platform Engineering, company-brain maintainers  
**Stack in scope:** TypeScript/React · Python · Java/Kotlin  
**Supersedes:** Initial extraction design in `harness-system-design.md § 4`

---

## Context

The current extraction pipeline (v1) operates at **entity level**: it extracts components, screens, API contracts, data models, assumptions, and business context — and stores one JSON document per entity. This is a solid foundation. However, the queries we care most about require a finer-grained understanding of the codebase that entity-level extraction cannot provide.

### Gap analysis by query type

| Query we need to answer | What v1 can do | What v1 is missing |
|---|---|---|
| "What breaks if I change X?" | Component-level blast radius | Function-level impact — which specific callers break and why |
| "Generate code that fits our style" | Knows entity names and prop shapes | Coding patterns, naming conventions, structural idioms |
| "How does this data get from UI to DB?" | Knows which component calls which API | The full typed transformation chain: state → handler → API call → service → repo → DB model |
| "Explain this codebase to me" | Can list entities | No hierarchical summaries — dumps everything at the same level |
| "Find all code that might violate assumption X" | Stores the assumption text | Cannot trace which call sites could reach a state that violates it |
| "Who else depends on this API across all repos?" | Knows consumers at component level | Cannot trace which specific functions are the actual callers |

### What is missing from the extraction

Three classes of information are absent:

**1. Function call graphs.** The pipeline knows "UserCard calls GET /users/{id}" at the component level. It does not know *which function* in UserCard makes that call, what arguments it passes, or what the transitive callers of that function are. Without function-level edges, impact analysis is imprecise and codegen has no call-site examples.

**2. Type flows.** TypeScript, Python (with annotations), and Java/Kotlin all carry rich type information. The pipeline currently only extracts the declared shape of entities (props, fields). It does not track how data *transforms* as it moves across layers — a `UserDTO` received from the API goes through a selector, gets partially mapped into component state, then a subset is rendered. That transformation chain is invisible to v1.

**3. State management structure.** React applications using Redux, Zustand, or Context have a structured state graph that v1 does not see. Actions, selectors, reducers, and store subscriptions are implicit in the current entity model. Without this, the brain cannot answer "what triggers a re-render of UserCard" or "which actions can mutate this slice of state."

### Decision drivers

- Extraction must stay **incremental** — a single changed file should trigger at most a partial re-index, not a full rebuild.
- Extraction must remain **language-agnostic in structure** — the same JSON schemas work for TypeScript, Python, and Java/Kotlin, even if the extraction mechanics differ per language.
- The new extraction must **not degrade query latency** — the smart-zone assembler must still respond in <500ms.
- **Cost sensitivity** — LLM calls during extraction are acceptable if cached aggressively. Running tsc/mypy/kotlinc on every query is not acceptable.

---

## Options Considered

### Option A: Pure deep AST extraction

Extract everything from source files using tree-sitter (syntax) and language compilers (semantics): function call graphs, type annotation chains, state management patterns. Store all extracted data in brain JSON files and index into Qdrant.

| Dimension | Assessment |
|---|---|
| Completeness | High for structural facts (who calls whom, declared types) |
| Semantic quality | Low — no understanding of *intent*, only syntax |
| Cost | Zero per-file LLM cost |
| Maintenance | High — requires language-specific tree-sitter queries per framework version |
| Latency (incremental) | Fast — AST parse is sub-second per file |
| State management | Requires hard-coded pattern matching per library (Redux, Zustand, etc.) |

**Pros:** Fully deterministic. Fast. No API cost. Works offline.  
**Cons:** Misses semantic meaning entirely. Type alias resolution is brittle without the full compiler. State management libraries require bespoke extractors per library version. Call graphs from AST alone are imprecise for dynamic dispatch and closures.

---

### Option B: LLM-driven semantic summarization

For each changed file, call an LLM (Claude Haiku) with the file content and a structured extraction prompt. The LLM produces a rich semantic summary: call relationships, type flows, state mutations, coding patterns — all in one pass.

| Dimension | Assessment |
|---|---|
| Completeness | High — LLM infers intent, not just syntax |
| Semantic quality | Very high — understands patterns, idioms, implicit contracts |
| Cost | ~$0.002–0.01 per file (Haiku); significant at 10K+ files |
| Maintenance | Low — no grammar maintenance, prompt updates instead |
| Latency (incremental) | Acceptable (2–8s per file, parallelizable) |
| State management | LLM understands Redux/Zustand/Context naturally |

**Pros:** Rich semantic output with minimal code. Handles any framework version. Extracts implicit knowledge (why a pattern is used, not just what it is).  
**Cons:** Non-deterministic outputs — two runs of the same file may produce different schemas. LLM hallucination risk for call graphs (may invent plausible-sounding but wrong edges). Expensive at full-rebuild scale. Slow for large monorepos without aggressive parallelism.

---

### Option C: Hybrid — compiler-grade structure + selective LLM enrichment (recommended)

Use the right tool for each layer:

- **Structural facts** (call edges, type annotations, state shape): extracted by the TypeScript Compiler API, mypy/pyright, and java-callgraph — ground truth, deterministic, cheap.
- **Semantic enrichment** (intent summaries, pattern extraction, business-relevant type flows): LLM (Claude Haiku) invoked once per entity when its hash changes, output cached in brain JSON.
- **Pattern library** (coding idioms, naming conventions, structural templates): built from aggregating extracted structural facts + LLM-identified patterns across the codebase.

| Dimension | Assessment |
|---|---|
| Completeness | Very high — structural completeness from compiler, semantic richness from LLM |
| Semantic quality | High — LLM enriches, compiler grounds |
| Cost | Low — LLM only called on changed files, cached |
| Maintenance | Medium — compiler adapters per language, but tree-sitter is the fallback |
| Latency (incremental) | Fast — compiler analysis is incremental, LLM only for changed hashes |
| State management | Hybrid: pattern-matched by AST + LLM names slices and describes mutations |

**Pros:** Best accuracy. Incremental by design. LLM enrichment is optional (works without it). Compiler type information is exact, not inferred.  
**Cons:** Higher initial implementation complexity. Requires integrating three language-specific compiler tools (tsc, pyright/mypy, kotlin compiler). Compiler APIs change across language versions.

---

## Trade-off Analysis

The critical question is whether to trust the LLM for **structural facts** (call edges, types) or only for **semantic interpretation**. Option B trusts LLM for both; Options A and C use it only for semantics.

For impact analysis and cross-repo contract queries, structural facts must be exact. A wrong call edge in the blast radius graph means a developer is told the wrong thing about what breaks. LLM hallucination is acceptable for a summary description; it is not acceptable for a call graph. This eliminates Option B as the primary extraction mechanism.

Option A produces correct call graphs but incomplete semantic context, which limits codegen matching and onboarding queries significantly. The brain becomes a structural index without meaning.

Option C accepts more implementation cost in exchange for correctness on structural queries and richness on semantic queries. The per-language compiler adapters are the main ongoing maintenance cost, but each adapter is isolated and only needs updating when the language toolchain changes significantly.

**Recommendation: Option C.** The implementation cost is front-loaded; the runtime system is cheap and precise.

---

## Decision

Adopt **Option C: Hybrid compiler-grade structure + selective LLM enrichment**.

The extraction pipeline is upgraded to three parallel stages per file, in addition to the existing entity-level extraction:

1. **Stage E1 — Function call graph extraction** (compiler + tree-sitter)
2. **Stage E2 — Type flow extraction** (TypeScript Compiler API / pyright / kotlin compiler)
3. **Stage E3 — State management extraction** (AST pattern matching + LLM naming)

Two new cross-file aggregation stages run after per-file extraction:

4. **Stage A1 — Call path pre-computation** (BFS on call graph, for common query patterns)
5. **Stage A2 — Code pattern library** (aggregation + LLM summarization of recurring idioms)

The result is five new brain entity types, described below.

---

## Enhanced Architecture

### New Entity Types

The existing six entity types (component, screen, api_contract, data_model, assumption, business_context) are retained unchanged. Five new types are added.

#### `function_node`

One node per named function, method, arrow function, or lambda. The atomic unit of the call graph.

```json
{
  "id": "web-app::function_node::UserCard.fetchUserData",
  "type": "function_node",
  "repo": "web-app",
  "file": "src/components/UserCard.tsx",
  "qualified_name": "UserCard.fetchUserData",
  "t1_summary": "Fetches user profile from API on mount. Handles 401 by redirecting to login.",
  "signature": "(userId: string) => Promise<UserDTO>",
  "is_async": true,
  "is_exported": false,
  "calls": [
    {
      "target": "api-service::api_contract::GET /users/{id}",
      "call_site_line": 42,
      "args_passed": ["userId"],
      "is_conditional": false
    }
  ],
  "called_by": [
    "web-app::function_node::UserCard.useEffect[mount]"
  ],
  "reads_state": ["web-app::state_slice::authStore.token"],
  "writes_state": [],
  "param_types": ["string"],
  "return_type": "Promise<UserDTO>",
  "throws": ["AuthError", "NetworkError"],
  "assumptions_relied_on": ["shared-lib::assumption::user-always-has-one-role"],
  "parent_entity": "web-app::component::UserCard",
  "last_updated": "2026-05-07T00:00:00Z",
  "version_hash": "abc123"
}
```

#### `type_flow`

A traced chain showing how a specific piece of data transforms as it moves from a source to a sink. Extracted by combining the call graph with type information at each step.

```json
{
  "id": "web-app::type_flow::api-to-render::UserCard.name",
  "type": "type_flow",
  "repo": "web-app",
  "qualified_name": "api-to-render::UserCard.name",
  "t1_summary": "User name flows from GET /users/{id} response → UserDTO.name → UserCard state → <span> render. No transformation, raw string.",
  "source": "api-service::api_contract::GET /users/{id}",
  "sink": "web-app::function_node::UserCard.render",
  "chain": [
    {
      "step": 1,
      "entity": "api-service::api_contract::GET /users/{id}",
      "field": "response.body.name",
      "type": "string",
      "transformation": "none"
    },
    {
      "step": 2,
      "entity": "shared-lib::data_model::UserDTO",
      "field": "name",
      "type": "string",
      "transformation": "assigned to UserDTO.name"
    },
    {
      "step": 3,
      "entity": "web-app::function_node::UserCard.fetchUserData",
      "field": "userData.name",
      "type": "string",
      "transformation": "setState({ userData })"
    },
    {
      "step": 4,
      "entity": "web-app::function_node::UserCard.render",
      "field": "props or state.userData.name",
      "type": "string",
      "transformation": "rendered as JSX text node"
    }
  ],
  "cross_repo": true,
  "data_leaves_system": false,
  "last_updated": "2026-05-07T00:00:00Z"
}
```

#### `state_slice`

One node per logical unit of managed state: a Redux slice, a Zustand store, a React Context value, or a Python class with mutable state.

```json
{
  "id": "web-app::state_slice::authStore",
  "type": "state_slice",
  "repo": "web-app",
  "file": "src/store/authStore.ts",
  "qualified_name": "authStore",
  "library": "zustand",
  "t1_summary": "Authentication state. Holds token, user identity, and login/logout actions. Subscribed to by any component that needs auth.",
  "shape": {
    "token": "string | null",
    "userId": "string | null",
    "isAuthenticated": "boolean"
  },
  "actions": [
    { "name": "setToken", "mutates": ["token", "isAuthenticated"], "called_by": ["web-app::function_node::LoginForm.handleSubmit"] },
    { "name": "logout", "mutates": ["token", "userId", "isAuthenticated"], "called_by": ["web-app::function_node::Header.handleLogout"] }
  ],
  "selectors": [
    { "name": "useAuthToken", "reads": ["token"], "used_by": ["web-app::function_node::UserCard.fetchUserData"] }
  ],
  "components_subscribed": ["web-app::component::UserCard", "web-app::component::Header"],
  "assumptions": ["web-app::assumption::token-null-means-logged-out"],
  "last_updated": "2026-05-07T00:00:00Z",
  "version_hash": "def456"
}
```

#### `code_pattern`

A reusable coding pattern extracted from the codebase. Stores concrete examples and the structural template. This is the primary enabler for codegen-matching queries.

```json
{
  "id": "web-app::code_pattern::async-fetch-on-mount",
  "type": "code_pattern",
  "repo": "web-app",
  "qualified_name": "async-fetch-on-mount",
  "t1_summary": "Standard pattern for fetching data on component mount: useEffect with async inner function, loading/error state, cleanup via AbortController.",
  "category": "data-fetching",
  "language": "typescript",
  "frequency": 23,
  "canonical_example": {
    "file": "src/components/UserCard.tsx",
    "lines": "38-61",
    "code_summary": "useEffect → fetchData() → setLoading(true) → try/catch → setState → setLoading(false) → AbortController cleanup"
  },
  "structural_template": {
    "hooks_used": ["useEffect", "useState"],
    "state_vars": ["data: T | null", "isLoading: boolean", "error: Error | null"],
    "effect_deps": ["entityId"],
    "cleanup": "AbortController"
  },
  "naming_conventions": {
    "fetch_fn": "fetch{EntityName}",
    "loading_state": "isLoading",
    "error_state": "error"
  },
  "related_patterns": ["web-app::code_pattern::error-boundary-wrapper", "web-app::code_pattern::loading-skeleton"],
  "entities_using_pattern": ["web-app::component::UserCard", "web-app::component::ActivityFeed", "web-app::component::Dashboard"],
  "last_updated": "2026-05-07T00:00:00Z"
}
```

#### `call_path`

A pre-computed multi-hop call chain between two significant entities. Expensive to compute at query time; stored for common paths.

```json
{
  "id": "web-app::call_path::Dashboard-to-users-db",
  "type": "call_path",
  "qualified_name": "Dashboard-to-users-db",
  "t1_summary": "Full call chain from Dashboard render to the users DB table, through UserCard, fetch, API route, service, and repository.",
  "source": "web-app::screen::Dashboard",
  "sink": "api-service::data_store::users_table",
  "hops": 6,
  "chain": [
    { "step": 1, "entity": "web-app::screen::Dashboard", "fn": "render", "edge": "renders" },
    { "step": 2, "entity": "web-app::component::UserCard", "fn": "useEffect[mount]", "edge": "calls" },
    { "step": 3, "entity": "web-app::function_node::UserCard.fetchUserData", "fn": "fetchUserData", "edge": "calls" },
    { "step": 4, "entity": "api-service::api_contract::GET /users/{id}", "fn": "route handler", "edge": "calls" },
    { "step": 5, "entity": "api-service::function_node::UserService.getById", "fn": "getById", "edge": "calls" },
    { "step": 6, "entity": "api-service::function_node::UserRepository.findById", "fn": "findById", "edge": "reads" },
    { "step": 7, "entity": "api-service::data_store::users_table", "fn": "SELECT", "edge": "reads" }
  ],
  "cross_repo": true,
  "repos_traversed": ["web-app", "api-service"],
  "last_updated": "2026-05-07T00:00:00Z"
}
```

---

### Enhanced Extraction Pipeline (per language)

#### TypeScript / React

```
Source .ts / .tsx file
         │
         ▼
[E1] TypeScript Compiler API (tsc programmatic)
│  - Full type resolution (not just annotations — inferred types too)
│  - Call graph: every call expression → target function signature
│  - Type at each call site: actual resolved types, not generic
│  - Output: FunctionNode[] with typed call edges
│
│  ts.createProgram([file], compilerOptions)
│  checker = program.getTypeChecker()
│  For each CallExpression node:
│    symbol = checker.getSymbolAtLocation(node.expression)
│    type = checker.getTypeAtLocation(node)
         │
         ▼
[E2] React state management AST patterns (tree-sitter-typescript)
│  - Detect library: import from 'zustand' | 'redux' | 'react' (createContext)
│  - Redux: find createSlice() → extract name, initialState, reducers, extraReducers
│  - Zustand: find create<T>() → extract state shape, actions
│  - Context: find createContext() → extract value type, Provider location
│  - Track useSelector(), useDispatch(), useStore() call sites → component subscriptions
         │
         ▼
[E3] LLM enrichment (Claude Haiku, only if file hash changed)
│  Input: extracted structural facts (NO source code — just the facts)
│  Output:
│    - t1_summary per function_node
│    - Identified code_pattern instances
│    - Semantic description of type transformations
│    - Flag assumptions (invariants the code silently relies on)
         │
         ▼
Write function_node[], state_slice[], assumptions[] to .brain/
```

**Critical detail — TypeScript Compiler API vs tree-sitter for types:**

tree-sitter parses syntax only — it sees `: string` but cannot resolve what `UserDTO["name"]` actually is. The TypeScript Compiler API resolves the full type graph. For type flow extraction, `tsc` is mandatory; tree-sitter is used only for patterns that don't require type resolution (imports, JSX structure, hook usage).

```typescript
// Example: extracting call graph with resolved types via tsc API
import ts from "typescript";

function extractCallGraph(filePath: string, program: ts.Program) {
  const checker = program.getTypeChecker();
  const sourceFile = program.getSourceFile(filePath)!;
  const calls: CallEdge[] = [];

  function visit(node: ts.Node) {
    if (ts.isCallExpression(node)) {
      const symbol = checker.getSymbolAtLocation(node.expression);
      const signature = checker.getResolvedSignature(node);
      if (symbol && signature) {
        const decl = symbol.valueDeclaration;
        calls.push({
          caller: getCurrentFunctionName(node),
          callee: checker.getFullyQualifiedName(symbol),
          callee_file: decl?.getSourceFile().fileName,
          return_type: checker.typeToString(checker.getReturnTypeOfSignature(signature)),
          arg_types: signature.parameters.map(p =>
            checker.typeToString(checker.getTypeOfSymbolAtLocation(p, node))
          )
        });
      }
    }
    ts.forEachChild(node, visit);
  }
  visit(sourceFile);
  return calls;
}
```

#### Python

```
Source .py file
         │
         ▼
[E1] pyright (via pyright --outputjson) or mypy daemon (dmypy)
│  - Full type inference including unannotated code
│  - Call graph: resolved function references (not just names)
│  - Class method call chains
│  - Async/await chains
│
│  Invoke: pyright --outputjson --pythonpath . src/service/user_service.py
│  Parse: diagnostics + type map → build FunctionNode[]
         │
         ▼
[E2] AST patterns (Python ast module or tree-sitter-python)
│  - Detect: class methods, staticmethods, classmethods
│  - Detect: dataclass fields, Pydantic model fields
│  - Detect: FastAPI route decorators (@router.get, @app.post)
│  - Detect: SQLAlchemy models (Base subclasses, Column definitions)
│  - Detect: state-like patterns (class with __init__ self.x = ...)
         │
         ▼
[E3] LLM enrichment (same as TypeScript)
```

#### Java / Kotlin

```
Source .java / .kt file
         │
         ▼
[E1] java-callgraph2 (bytecode analysis on compiled .class files)
│  - Most accurate — analyzes bytecode, not source
│  - Resolves interfaces, abstract classes, generics
│  - Output: CSV of (caller class:method) → (callee class:method) edges
│
│  java -jar java-callgraph2.jar --format json ./build/classes
│
│  Alternative for source-only: tree-sitter-java / tree-sitter-kotlin
│  (lower fidelity but no compilation step required)
         │
         ▼
[E2] JVM type extraction
│  - For Kotlin: kotlinp (Kotlin metadata extractor) → function signatures
│  - For Java: javap -verbose → method descriptors
│  - Spring Boot: detect @RestController, @Service, @Repository, @Entity annotations
│  - Kotlin data classes: extract properties as data model fields
         │
         ▼
[E3] LLM enrichment (same pattern)
```

---

### Stage A1 — Call Path Pre-computation

After per-file extraction, a cross-file aggregation stage builds the full platform call graph and pre-computes the most important call paths.

```python
def precompute_call_paths(
    platform_graph: dict,
    priority_pairs: list[tuple[str, str]]
) -> list[CallPath]:
    """
    Pre-compute call paths for high-value source→sink pairs.
    priority_pairs: e.g. [(screen, data_store), (api_contract, data_store)]
    """
    paths = []
    for source_type, sink_type in priority_pairs:
        sources = [n for n in platform_graph["nodes"] if n["type"] == source_type]
        sinks = [n for n in platform_graph["nodes"] if n["type"] == sink_type]

        for source in sources:
            for sink in sinks:
                path = bfs_path(platform_graph, source["id"], sink["id"], max_hops=10)
                if path:
                    paths.append(build_call_path_entity(source, sink, path))

    return paths

# Default priority pairs (covers all 6 query patterns)
DEFAULT_PRIORITY_PAIRS = [
    ("screen", "data_store"),          # data flow trace
    ("component", "api_contract"),     # impact analysis
    ("api_contract", "data_model"),    # cross-repo contracts
    ("function_node", "state_slice"),  # state mutation tracing
]
```

---

### Stage A2 — Code Pattern Library

Runs after all per-file extractions complete. Groups structurally similar patterns across the codebase.

```python
PATTERN_CATEGORIES = {
    "data-fetching": [
        "useEffect + async fetch + loading state",
        "SWR / React Query usage",
        "Class-based service fetch method",
    ],
    "error-handling": [
        "try/catch with typed error",
        "Result<T, E> pattern",
        "Global error boundary",
    ],
    "state-mutation": [
        "Redux action dispatch chain",
        "Zustand set() with immer",
        "Context value update pattern",
    ],
    "api-layer": [
        "Repository pattern (Java/Kotlin)",
        "FastAPI route with dependency injection",
        "Express middleware chain",
    ],
}

def extract_patterns(all_function_nodes: list[FunctionNode]) -> list[CodePattern]:
    """
    Group function nodes by structural similarity.
    Use LLM to name and describe each cluster.
    """
    # 1. Embed all function node summaries
    embeddings = encoder.encode([fn.t1_summary for fn in all_function_nodes])

    # 2. Cluster by structural similarity (k-means or HDBSCAN)
    clusters = cluster(embeddings, min_cluster_size=3)

    # 3. For each cluster: LLM names the pattern
    patterns = []
    for cluster_id, members in clusters.items():
        pattern = llm_name_pattern(members)  # Claude Haiku
        patterns.append(CodePattern(
            members=members,
            frequency=len(members),
            **pattern
        ))

    return patterns
```

---

### How Each Query Pattern Benefits

#### 1. Impact analysis: "What breaks if I change X?"

With function-level call graphs, blast radius is now **function-precise**, not component-approximate.

Before: "Changing GET /users/{id} affects UserCard"  
After: "Changing the `roles` field in the response affects:
- `UserCard.fetchUserData` (line 42) — currently maps `response.roles[0]` without null check
- `ProfileHeader.render` in mobile-app — assumes `roles.length > 0` (see assumption `user-always-has-one-role`)
- `UserService.enrichWithPermissions` in api-service — joins on roles, would fail with empty array"

**Smart-zone additions for impact analysis queries:**
- Include `function_node` entities for the primary entity's direct callers
- Include any `assumption` entities linked to those function nodes
- Include cross-repo `call_path` chains that pass through the changed entity

#### 2. Codegen matching: "Generate code that fits our style"

The `code_pattern` entity directly feeds the smart zone. When a codegen task is detected, the assembler loads the relevant pattern with its `canonical_example` and `naming_conventions`.

Before: LLM guesses naming conventions from vague component summaries  
After: LLM receives: "Your codebase uses this exact pattern 23 times — here is the canonical example with naming conventions. Match it."

**Smart-zone additions for codegen queries:**
- Match query to most relevant `code_pattern` category
- Include `canonical_example` code summary and `naming_conventions`
- Include 2-3 `function_node` entities that demonstrate the pattern in context

#### 3. Data flow trace: "How does X get from UI to DB?"

`call_path` entities are pre-computed for screen → data_store pairs. The assembler returns the full chain as a structured trace.

Before: Assembler returns individual entity summaries; engineer must mentally connect them  
After: "Here is the complete 7-step traced path from Dashboard render to the users table, with the type at each step and the transformation applied."

**Smart-zone additions for data flow trace queries:**
- Detect source and sink entities in the query
- Look up pre-computed `call_path` for that pair
- If not pre-computed: BFS on the call graph at query time (capped at 8 hops)
- Include the `type_flow` chain for the specific field being traced

#### 4. New engineer onboarding: "Explain this codebase to me"

The `t1_summary` fields on all entity types compose into a hierarchical overview. The assembly algorithm for onboarding queries uses a RAPTOR-style approach: load domain summaries, then service summaries, then screen summaries — never going to function-level unless asked.

Before: Flat list of entity summaries at the same level  
After: Hierarchical explanation — "The platform has 4 domains: Auth, User Management, Billing, Dashboard. Here is what each does, its main screens, key APIs, and the patterns used in each domain."

**Smart-zone additions for onboarding queries:**
- Set task type to `ONBOARD` → only T1 summaries, no T2 detail
- Group entities by domain (from `tags` field)
- Include 1 `code_pattern` per domain as the representative idiom
- Include top-level `business_context` entities

#### 5. Assumption violation: "Find all code that might break Y"

With function-level call graphs and type flows, the brain can trace which functions are at risk of violating an assumption.

Before: Returns the assumption text and the entity it applies to  
After: "Assumption `user-always-has-one-role` is relied on by 4 function nodes. Of these, `UserCard.fetchUserData` does not null-check `roles`, and `createUser` in `api-service` has a code path that could produce an empty roles array if the IAM service is unavailable."

**Smart-zone additions for assumption violation queries:**
- Load the assumption entity (T1 + full detail)
- Load all `function_node` entities with `assumptions_relied_on` containing this assumption
- Include type flows that involve the fields referenced in the assumption
- Flag any function node where the call chain could reach a violating state

#### 6. Cross-repo contracts: "Who else depends on this API?"

`function_node` entities store cross-repo call edges explicitly. The `call_path` entities capture multi-hop cross-repo chains.

Before: Returns the component-level consumers of an API  
After: "GET /users/{id} is called directly by: `UserCard.fetchUserData` (web-app, 1 call site), `ProfileHeader.loadUser` (mobile-app, 2 call sites), `AdminPanel.refreshUser` (admin-app, 1 call site). The mobile-app call passes a hardcoded timeout of 5s that will fail if your SLA drops below that."

**Smart-zone additions for cross-repo contract queries:**
- Load `function_node` entities that have cross-repo `calls` edges to the queried API
- Include the type they expect in return (may differ from what the API currently returns)
- Flag any mismatch between caller-expected type and API-declared response schema

---

## Updated Storage Layout

Five new subdirectories added to each repo's `.brain/`:

```
repo-name/.brain/
├── function_nodes/          ← NEW: one JSON per function/method
│   ├── UserCard.fetchUserData.json
│   └── ...
├── type_flows/              ← NEW: one JSON per traced transformation chain
│   ├── api-to-render__UserCard.name.json
│   └── ...
├── state_slices/            ← NEW: one JSON per state management unit
│   ├── authStore.json
│   └── ...
├── call_paths/              ← NEW (platform-level): pre-computed chains
│   └── Dashboard-to-users-db.json
└── code_patterns/           ← NEW (platform-level): extracted idiom library
    ├── async-fetch-on-mount.json
    └── ...
```

The Qdrant collections are extended with three new collections:

```python
NEW_COLLECTIONS = ["brain_function_node", "brain_state_slice", "brain_code_pattern"]
# call_path and type_flow are query-time computed or looked up by exact ID — not searched
```

`type_flow` and `call_path` entities are retrieved by traversal, not by similarity search, so they do not need Qdrant collections.

---

## Updated Smart-Zone Assembler

The assembler gains a task-type classifier and entity-type routing table:

```python
QUERY_TYPE_ENTITY_ROUTING = {
    "impact_analysis":   ["component", "function_node", "assumption", "call_path"],
    "codegen_matching":  ["code_pattern", "component", "function_node"],
    "data_flow_trace":   ["call_path", "type_flow", "api_contract", "data_model"],
    "onboarding":        ["screen", "component", "business_context", "code_pattern"],
    "assumption_violation": ["assumption", "function_node", "type_flow"],
    "cross_repo_contract":  ["api_contract", "function_node", "call_path"],
}

def route_query(task: str, token_budget: int) -> SmartZonePayload:
    query_type = classify_query(task)             # classifier (regex + LLM fallback)
    entity_types = QUERY_TYPE_ENTITY_ROUTING[query_type]
    candidates = hybrid_search(task, entity_types=entity_types)
    blast = compute_blast_radius(candidates[:5], hops=2)
    return assemble_smart_zone(candidates, blast, token_budget, query_type)
```

---

## Consequences

**What becomes easier:**

- Impact analysis is now function-precise — engineers get exact lines at risk, not just "this component is affected."
- Codegen quality improves significantly — the LLM generates code that matches actual patterns in the repo, not generic patterns from training data.
- Data flow questions become answerable in a single query — the full typed trace from UI event to DB operation is pre-computed.
- Onboarding a new engineer can be done interactively with the brain as the primary guide.
- Assumption violations become detectable before production — the brain can scan for at-risk call sites when an assumption entity is modified.
- Cross-repo contract changes surface all downstream callers with their type expectations.

**What becomes harder:**

- Initial extraction is significantly slower — tsc, pyright, and java-callgraph2 are expensive to run on a full codebase. Phase 1 full rebuild may take 10–30 minutes for a large platform. Subsequent incremental runs are fast (<5s per changed file).
- The brain grows substantially in size — function nodes alone could be 10–50× the entity count of v1. A platform with 200 components may have 5,000+ function nodes. Qdrant collection sizing and Qdrant RAM requirements must be revisited.
- LLM enrichment adds ~$5–20 per full rebuild at platform scale. This is acceptable if rebuilds are nightly; it is not acceptable if they run on every commit.
- Type information is language-version sensitive — upgrading TypeScript or Python means re-running the full extraction to pick up type changes.

**What we will need to revisit:**

- Token budget allocation — function nodes are short (~50–100 tokens each) but numerous. The assembler's T2 budget may need to distinguish between entity-level T2 and function-level T2.
- Graph database threshold — the dependency graph will grow to 10,000+ nodes at function level. The adjacency JSON approach will become slow for multi-hop traversal above ~50K nodes. Memgraph migration should be triggered at 10K function nodes.
- LLM enrichment trigger policy — running Haiku on every changed file is cheap per-file but adds up. Consider only enriching function nodes that are on call paths (i.e., connected to at least one screen or API contract endpoint), skipping internal utility functions.

---

## Action Items

### Phase 1 — TypeScript / React extraction (Weeks 1–3)
- [ ] Integrate TypeScript Compiler API for function-level call graph extraction
- [ ] Implement `function_node` JSON schema and writer
- [ ] Implement React state management AST patterns (Zustand, Redux, Context)
- [ ] Implement `state_slice` JSON schema and writer
- [ ] LLM enrichment stage (Haiku) with hash-based caching
- [ ] Extend Qdrant with `brain_function_node` and `brain_state_slice` collections
- [ ] Extend `brain_query` MCP tool to return function nodes in blast radius

### Phase 2 — Python + Java/Kotlin extraction (Weeks 4–6)
- [ ] Integrate pyright `--outputjson` for Python call graph extraction
- [ ] Integrate java-callgraph2 for Java/Kotlin (requires build artifact access)
- [ ] Implement tree-sitter-kotlin fallback for source-only analysis
- [ ] Extend entity schemas to capture Spring Boot / FastAPI / SQLAlchemy patterns

### Phase 3 — Type flows + call paths (Weeks 7–9)
- [ ] Implement type flow chain extraction (combine call graph + tsc type info)
- [ ] Implement `type_flow` JSON schema and writer
- [ ] Implement call path BFS pre-computation for priority pairs
- [ ] Implement `call_path` JSON schema and writer
- [ ] Wire `data_flow_trace` query type into smart-zone assembler

### Phase 4 — Code pattern library (Weeks 10–11)
- [ ] Implement function node embedding + clustering for pattern detection
- [ ] LLM pattern naming and description (Haiku, one call per cluster)
- [ ] Implement `code_pattern` JSON schema and writer
- [ ] Wire `codegen_matching` query type into smart-zone assembler
- [ ] Extend `brain_function_node` Qdrant collection with `brain_code_pattern`

### Phase 5 — Query routing + validation (Week 12)
- [ ] Implement query type classifier (regex rules + LLM fallback for ambiguous)
- [ ] Implement entity-type routing table
- [ ] Run end-to-end validation across all 6 query patterns
- [ ] Benchmark: measure smart-zone token efficiency vs v1 for each query type
- [ ] Benchmark: measure extraction time on a real repo (incremental vs full)

---

## Appendix: Extraction Tool Reference

| Language | Call graph tool | Type extraction | State patterns |
|---|---|---|---|
| TypeScript | TypeScript Compiler API (`ts.createProgram`) | `checker.getTypeAtLocation()` | tree-sitter-typescript (hooks, context) |
| Python | pyright `--outputjson` or dmypy | pyright type map | tree-sitter-python (dataclass, pydantic) |
| Java | java-callgraph2 (bytecode) | javap `-verbose` | tree-sitter-java (Spring annotations) |
| Kotlin | java-callgraph2 (bytecode) | kotlinp metadata | tree-sitter-kotlin (data class, coroutines) |

| Tool | Install |
|---|---|
| TypeScript Compiler API | `npm install typescript` (already a dev dep) |
| pyright | `pip install pyright` |
| java-callgraph2 | `wget https://github.com/gousiosg/java-callgraph/releases/...` |
| tree-sitter | `pip install tree-sitter` + grammar packages |
| Claude Haiku (enrichment) | Anthropic API — `claude-haiku-4-5-20251001` |

---

*Reviewed against: TypeScript Compiler API docs, pyright JSON output spec, java-callgraph2 GitHub, tree-sitter grammar documentation — 2026-05-07*
