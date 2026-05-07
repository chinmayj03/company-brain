# ADR-007: Merkle-Tree Change Detection + tree-sitter AST Parsing for Incremental, Language-Agnostic Code Indexing

**Status:** Proposed  
**Date:** 2026-04-28  
**Deciders:** Chinmay  
**Supersedes:** Portions of ADR-003 (regex pattern matching in `CodeTracer`) and ADR-006 (structural discovery phase of NavigatorAgent)  
**Related:** ADR-005 (L1/L2/Main Memory Hierarchy), ADR-006 (Multi-Agent Extraction)

---

## Context

### What the current pipeline does well

ADR-006 established a four-agent extraction council that replaced hardcoded regex heuristics with LLM-driven code navigation. This was a major step — the NavigatorAgent can now traverse Spring Boot, hexagonal architecture, CQRS, and DDD patterns without any framework-specific logic. ADR-005's L1/L2/Main memory hierarchy gives the extraction council a growing shared vocabulary across a pipeline run.

### Three gaps that are now the binding constraint

**Gap 1: No incremental indexing — every run re-processes everything.**

The pipeline today is stateless with respect to file changes. Every time you trigger extraction on a workspace, it re-runs the full LLM stack on every endpoint, regardless of whether the underlying source files changed. At 20 endpoints this is tolerable. At 200 it becomes expensive and slow. At 2,000 it is unshipable.

The root cause: there is no content-addressable index of what has already been processed. The system has no way to ask "has this file changed since I last extracted from it?"

**Gap 2: Structural code discovery is still regex for TypeScript, JavaScript, and Python.**

`CodeTracer` delegates Java navigation to the NavigatorAgent, but TypeScript/JS/Python entry-point detection still uses regex: `_TS_API_CALL_RE`, `_PY_ROUTE_RE`, `_TS_IMPORT_RE`. This means:

- Renamed decorators break it (`@app.route` vs `@bp.get` vs `@router.get`)
- Dynamic routes (e.g. `app.use('/api', router)`) are invisible
- Import resolution is pattern-matched, not structurally resolved
- Call graph extraction within a file is impossible with regex alone

This is the same arms-race problem ADR-003 identified for ORM detection — just in the navigation layer instead of the query layer.

**Gap 3: The NavigatorAgent wastes tokens on structural discovery it shouldn't need to do.**

Today, the NavigatorAgent uses LLM reasoning to answer questions that are fundamentally structural and deterministic: "what does this file import?", "what class implements this interface?", "what method does this annotation apply to?" These questions have exact, unambiguous answers that a parser — not an LLM — should be answering. Using an LLM for this is both expensive and unreliable (hallucination risk on symbol names, import paths, etc.).

The insight from Cursor, Claude Code, and VS Code's language server is that LLMs should reason about *semantics*, not *structure*. Structure is what parsers are for.

---

## Decision

Introduce two new infrastructure layers below the existing multi-agent extraction pipeline:

1. **A Merkle Index** — content-addressable hashing of every file and directory, stored in SQLite, enabling O(changed files) incremental re-indexing instead of O(all files).

2. **A tree-sitter Symbol Index** — a structural parse of every file using tree-sitter grammars, producing a language-agnostic symbol table (functions, classes, methods, imports, exports, decorators) without any regex or LLM involvement.

Together these become a **structural pre-computation layer** that makes the expensive LLM agents faster, cheaper, and more accurate — by giving them precise structural context rather than making them discover structure themselves.

---

## Options Considered

### Option A: Full re-index on every run (current state)

| Dimension | Assessment |
|---|---|
| Complexity | Low — no incremental logic |
| Cost | High and growing — every run is O(all files × LLM tokens) |
| Scalability | Breaks at ~200 endpoints |
| Accuracy | Acceptable — NavigatorAgent is robust |
| Language coverage | Partial — regex for TS/Python entry points |

**Verdict:** Acceptable at current scale. Becomes the binding constraint within months as workspace counts grow. Does not solve the language coverage gap.

---

### Option B: Git-diff based incremental re-indexing

Track the last-indexed git commit SHA. On re-run, only process files changed since that commit (`git diff --name-only <last_sha> HEAD`).

| Dimension | Assessment |
|---|---|
| Complexity | Medium — git integration required |
| Cost | Better — skips unchanged files |
| Scalability | Good for committed changes |
| Accuracy | Misses uncommitted changes; misses downstream dependents of changed files |
| Language coverage | Does not address the regex/structural gap |

**Verdict:** Solves the token cost problem for most runs. Critically, it cannot propagate invalidation: if `UserService.java` changed, all files that call `UserService` may also need re-extraction — but git diff only sees `UserService.java`. Also breaks for workspaces not under git, or for users who want to index unsaved in-progress work.

---

### Option C: Merkle Tree + tree-sitter (recommended)

Content-address every file with a SHA-256 hash. Build a tree-sitter symbol table for every file. Maintain a reverse dependency index. On re-run, compare current hashes to stored hashes, derive the dirty set (changed files + their dependents), and only send the dirty set through the LLM pipeline.

| Dimension | Assessment |
|---|---|
| Complexity | Medium-high — upfront investment, then simpler maintenance |
| Cost | Low steady-state — only changed files consume LLM tokens |
| Scalability | O(dirty files × LLM tokens) — scales to thousands of files |
| Accuracy | Best — structural index eliminates hallucination risk for symbol resolution |
| Language coverage | Any language with a tree-sitter grammar (Java, TS, JS, Python, Go, Kotlin, Rust, Swift, C#, Ruby, PHP, all covered) |
| VCS independence | Works without git — content-addressed, not commit-addressed |

**Verdict:** Highest implementation cost up front, best long-term properties. Aligns with how every production-grade language tooling system works (VS Code, Cursor, GitHub Copilot all maintain a local symbol index; none re-parse everything on every request).

---

## Architecture

The new system introduces two layers below the existing pipeline. The LLM agents remain unchanged in interface — they receive richer, more precise context as a result.

```
┌─────────────────────────────────────────────────────────────────────────┐
│                          Workspace File System                          │
└─────────────────────────────────┬───────────────────────────────────────┘
                                  │ file walk
                                  ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  Layer 0: Merkle Index (SQLite)                                         │
│  ─────────────────────────────────────────────────────────────────────  │
│  Per-file:  sha256(content) → compare to stored hash → clean / dirty   │
│  Per-dir:   sha256(child hashes) → subtree-level change detection       │
│  Result:    DIRTY_SET — files whose hash changed since last index run   │
│             + their downstream dependents (via reverse import index)    │
└─────────────────────────────────┬───────────────────────────────────────┘
                    only dirty files pass through
                                  │
                                  ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  Layer 1: tree-sitter Symbol Index (SQLite)                             │
│  ─────────────────────────────────────────────────────────────────────  │
│  Per dirty file:                                                        │
│    • Parse → CST (concrete syntax tree)                                 │
│    • Extract: functions, classes, methods, interfaces, types            │
│    • Extract: import edges (from → to, imported symbol names)           │
│    • Extract: decorators / annotations (@GetMapping, @router.get, etc.) │
│    • Store in symbol_index and import_edges tables                      │
│  Cross-file:                                                            │
│    • Resolve import paths → actual file paths (reverse dependency map)  │
│    • Build call graph stubs (call sites within the parsed file)         │
└─────────────────────────────────┬───────────────────────────────────────┘
                    structural context ready — no LLM needed yet
                                  │
                                  ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  Layer 2: Multi-Agent Extraction (ADR-006, unchanged interface)         │
│  ─────────────────────────────────────────────────────────────────────  │
│  NavigatorAgent now receives pre-built symbol context in L1:            │
│    • "Here are the 3 functions in this file that match @GetMapping"     │
│    • "Here are the 5 imports and where they resolve to"                 │
│    • "Here is the call graph within this method (from tree-sitter)"     │
│  Agent focuses on SEMANTIC reasoning: what does this actually do?       │
│  Not structural discovery: what does this import? (already answered)    │
│                                                                         │
│  L2 SharedContext (ADR-005) still accumulates domain vocabulary across  │
│  the run, now seeded with symbol names from the structural index        │
└─────────────────────────────────┬───────────────────────────────────────┘
                                  │
                                  ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  Layer 3: Graph DB (existing — Neo4j / JavaGraphClient)                 │
│  ─────────────────────────────────────────────────────────────────────  │
│  Now populated with two signal types:                                   │
│    • Structural edges: IMPORTS, CALLS, EXTENDS, IMPLEMENTS (from tree-  │
│      sitter, high confidence, no LLM cost)                              │
│    • Semantic nodes: BusinessContext, Entity, Relationship (from agents, │
│      high value, LLM cost justified)                                    │
└─────────────────────────────────────────────────────────────────────────┘
```

### Merkle Index — detailed design

The Merkle Index is a content-addressed snapshot of the workspace stored in a lightweight SQLite database alongside the existing graph DB.

**Schema:**

```sql
CREATE TABLE file_index (
    file_path        TEXT PRIMARY KEY,
    content_hash     TEXT NOT NULL,   -- sha256(file bytes)
    language         TEXT,            -- detected by extension / tree-sitter
    last_indexed_at  DATETIME,
    symbol_count     INTEGER DEFAULT 0
);

CREATE TABLE dir_index (
    dir_path         TEXT PRIMARY KEY,
    subtree_hash     TEXT NOT NULL,   -- sha256(sorted child content_hashes)
    last_scanned_at  DATETIME
);
```

**Walk algorithm:**

```
for each file in workspace:
    current_hash = sha256(read(file))
    stored = SELECT content_hash FROM file_index WHERE file_path = file
    if stored is null OR stored.content_hash != current_hash:
        mark file as DIRTY
        upsert file_index SET content_hash = current_hash

dirty_set = DIRTY files ∪ {
    files that import any DIRTY file   ← reverse dependency lookup
}
```

The reverse dependency lookup is the key insight that separates content-based invalidation from simple git-diff: when `UserService.java` changes, any `*Controller.java` that calls into it may need its BusinessContext re-synthesised. The dependency index makes this propagation cheap.

**Directory-level fast path:**

For large workspaces (500K+ lines), scanning individual files on every run is still O(n). The directory-level hash provides an O(log n) shortcut: if `src/com/userservice/` subtree hash hasn't changed, skip all files inside it without opening them. This mirrors exactly how Merkle trees are used in git's object store and IPFS.

### tree-sitter Symbol Index — detailed design

tree-sitter provides incremental, error-tolerant concrete syntax tree (CST) parsing for 100+ languages via a unified API. It is the same parsing engine used by Neovim, Helix, and GitHub's code navigation features.

**Language coverage via `tree-sitter-languages` (Python package):**

| Language | Grammar | What we extract |
|---|---|---|
| Java | `java` | classes, methods, annotations, import declarations, interface implementations |
| TypeScript | `typescript` | classes, functions, arrow fns, decorators, import statements, export declarations |
| JavaScript | `javascript` | same as TS, plus `module.exports` |
| Python | `python` | class defs, function defs, decorators, import stmts, async functions |
| Go | `go` | struct defs, function defs, method receivers, import paths |
| Kotlin | `kotlin` | class defs, fun defs, annotations, import directives |
| Rust | `rust` | struct/enum/impl/trait/fn defs, use declarations |

**Symbol extraction queries** use tree-sitter's built-in query language (S-expression patterns), not regex. Example for Python route detection:

```scheme
; tree-sitter query — Python route detection
(decorated_definition
  (decorator
    (call
      (attribute
        object: (identifier) @obj
        attribute: (identifier) @method)
      arguments: (argument_list
        (string) @route_path)))
  definition: (function_definition
    name: (identifier) @handler_name))
```

This matches `@router.get("/users")`, `@app.post("/orders")`, `@bp.delete("/items/{id}")` — and any future pattern — without enumerating object or method names. The tree-sitter grammar handles all the syntax variation.

**Symbol Index schema:**

```sql
CREATE TABLE symbol_index (
    id           INTEGER PRIMARY KEY,
    file_path    TEXT NOT NULL,
    symbol_name  TEXT NOT NULL,
    symbol_kind  TEXT NOT NULL,  -- 'function' | 'class' | 'method' | 'interface' | 'type'
    line_start   INTEGER,
    line_end     INTEGER,
    exported     BOOLEAN,
    decorator    TEXT,           -- first decorator annotation if present
    signature    TEXT            -- parameter types, return type
);

CREATE TABLE import_edges (
    from_file      TEXT NOT NULL,
    to_file        TEXT,         -- null if unresolved (external package)
    raw_import     TEXT NOT NULL, -- the import string as written
    imported_names TEXT,          -- comma-separated symbols imported
    import_kind    TEXT           -- 'named' | 'default' | 'star' | 'side-effect'
);

CREATE INDEX idx_imports_to_file ON import_edges(to_file);
CREATE INDEX idx_symbols_file ON symbol_index(file_path);
```

### Integration with existing pipeline

**`CodeTracer` change:** Replace the five regex blocks (`_JAVA_MAPPING_RE`, `_TS_IMPORT_RE`, `_TS_API_CALL_RE`, `_PY_ROUTE_RE`, `_PY_IMPORT_RE`) with a single query to `symbol_index`:

```python
# Before
for match in _PY_ROUTE_RE.finditer(content):
    ...

# After
entry_points = db.query(
    "SELECT file_path, symbol_name, decorator, line_start "
    "FROM symbol_index "
    "WHERE file_path LIKE ? AND decorator IS NOT NULL",
    (f"{workspace_path}/%",)
)
```

The symbol index already knows which functions have route decorators because tree-sitter extracted them with structural certainty — no pattern matching required.

**`NavigatorAgent` context enrichment:** Before the agent's first LLM call, pre-populate L1 context with the tree-sitter-derived call graph:

```python
# Inject into L1 before NavigatorAgent starts
l1_prefix = build_structural_context(
    symbol_index=symbol_index,
    import_edges=import_edges,
    root_file=handler_file,
    max_tokens=800  # part of L1's ~3,200 token budget
)
# l1_prefix looks like:
# "File: UserController.java
#  Symbols: createUser(UserRequest req) → POST /users [line 42]
#  Imports: UserService (UserService.java, line 8), UserRepository (internal)
#  createUser calls: userService.create(), validator.validate()
#  UserService.create() is in UserService.java [line 91]"
```

The agent now starts with the import graph already resolved. It can reason about *what the code means* rather than spending LLM tokens answering *what does this import?*

**`Orchestrator` change:** Add a dirty-file gate before the extraction council is invoked:

```python
# In run_pipeline(), before Stage 0a
merkle = MerkleIndexer(workspace_path)
dirty_files = merkle.compute_dirty_set()

if not dirty_files.intersection(endpoint_files):
    # All files this endpoint depends on are clean — serve from cache
    return cached_result(endpoint_path)

# Only dirty endpoints go through the full LLM pipeline
```

---

## Trade-off Analysis

### Structural certainty vs. LLM flexibility

tree-sitter gives 100% accurate symbol extraction from syntactically valid code. The NavigatorAgent gives flexible reasoning over code that is architecturally complex. These are complementary, not competing. The right split:

- tree-sitter owns: "what symbols exist in this file, what does it import, what are the call sites"
- LLM agents own: "what does this function *mean* in business terms, what is the intent, what breaks if it changes"

Attempting to use LLMs for structural questions (current state for TS/Python) adds latency, cost, and hallucination risk to a question that has an exact answer.

### Merkle tree invalidation vs. git-diff

Git-diff is simpler and sufficient for 80% of use cases. Merkle hashing is better in the cases that matter most:

- Uncommitted work-in-progress (a developer actively editing code wants live context updates)
- Dependency propagation (a changed `UserService` correctly invalidates all its callers)
- VCS-agnostic workspaces (Perforce, SVN, or no VCS at all)
- Refactors that don't change git-visible state (e.g. a pure in-place rename)

The implementation overhead of Merkle vs. git-diff is approximately one day of engineering.

### SQLite vs. an additional graph DB for the symbol index

The symbol index is structural, not semantic. It does not need graph traversal queries — it needs fast point lookups by file path and symbol name. SQLite with appropriate indexes is the right choice. Adding a second graph DB for this would increase operational complexity without a performance benefit.

The existing Neo4j graph DB retains its role for semantic edges (CALLS_BUSINESS_SERVICE, EXPOSES_ENDPOINT, PART_OF_AGGREGATE) — edges that carry business meaning extracted by the LLM agents.

---

## Consequences

**What becomes easier:**
- Incremental re-indexing: re-running the pipeline after a code change takes seconds, not minutes
- Language coverage: any language with a tree-sitter grammar (100+) is immediately supported without a new regex heuristic
- NavigatorAgent precision: the agent starts each extraction with a pre-built structural context, reducing token usage in the exploration phase by an estimated 30–50%
- Confidence scoring: structural facts (symbol names, import paths) get `confidence: 1.0` by definition; only the LLM-derived semantic annotations carry uncertainty
- Cross-language workspaces: TypeScript frontend + Python backend + Java service can all be indexed by the same symbol extractor

**What becomes harder:**
- Initial implementation: tree-sitter grammar queries need to be written and tested per language; the Merkle walker needs to handle symlinks, `.gitignore`-style exclusions, and binary files
- Schema migration: existing workspaces need a one-time full re-index to populate the symbol and file index tables
- Grammar maintenance: when a language adds syntax (e.g. Python 3.12 type parameter syntax), the tree-sitter grammar needs to be updated (though this is handled upstream by the tree-sitter community)

**What we will need to revisit:**
- Token budget allocation in ADR-005: the L1 structural prefix (800 tokens of pre-built context) reduces the room available for the code unit itself; the 3,200-token L1 budget may need rebalancing
- NavigatorAgent tool definitions: the `read_file` and `find_class` tools can be replaced with direct symbol index queries, eliminating one round-trip per navigation step
- The `SharedContextAccumulator`'s domain glossary seeding: rather than waiting for the first LLM call to discover domain terms, the symbol index's exported symbol names can pre-seed the L2 glossary with class and function names at zero cost

---

## Action Items

1. [ ] Install `tree-sitter-languages` Python package; verify grammars available for Java, TS, JS, Python (covers current codebase)
2. [ ] Implement `MerkleIndexer` class: file walk → sha256 hash → SQLite upsert → dirty set computation
3. [ ] Implement `SymbolExtractor` class: tree-sitter parse → S-expression queries per language → `symbol_index` + `import_edges` tables
4. [ ] Implement reverse dependency index: on every `import_edges` write, update `reverse_deps` mapping `(to_file → [from_file, ...])`
5. [ ] Refactor `CodeTracer`: remove five regex blocks; replace with `symbol_index` queries for entry-point detection
6. [ ] Update `Orchestrator.run_pipeline()`: add Merkle dirty-set gate before Stage 0a; return cached result for clean endpoints
7. [ ] Update `NavigatorAgent` L1 context builder: prepend structural context (symbols + import edges for the handler file) before the first agent call
8. [ ] Write tree-sitter S-expression queries for: Java annotations, Python decorators, TypeScript decorators, TS/JS import resolution
9. [ ] Migrate existing workspace records: add one-time full re-index task that populates `file_index` and `symbol_index` for all previously processed workspaces
10. [ ] Benchmark: compare tokens consumed per endpoint extraction before and after, on the same Java Spring Boot test workspace

---

## References

- [tree-sitter documentation](https://tree-sitter.github.io/tree-sitter/)
- [tree-sitter-languages Python package](https://github.com/grantjenks/py-tree-sitter-languages) — pre-compiled grammars for 100+ languages
- Cursor's architecture (public): uses tree-sitter for local symbol index + embedding-based retrieval; LLM is never used for structural discovery
- Claude Code's context building: uses Merkle-style content hashing to track which files have changed and require re-summarisation
- [git's Merkle DAG](https://git-scm.com/book/en/v2/Git-Internals-Git-Objects): SHA-1 content hashing of blobs → trees → commits; same principle applied to the symbol index
