# Analysis: code-review-graph (tirth8205) → company-brain Integration

**Date:** 2026-04-28
**Source repo:** https://github.com/tirth8205/code-review-graph
**Repo size:** ~27,000 lines Python; ships as MCP server + VS Code extension; pip-installable

---

## TL;DR

`code-review-graph` (CRG) and our company-brain solve overlapping problems with very different bets:

| | code-review-graph | company-brain |
|---|---|---|
| **Storage** | SQLite (per repo, local) | Postgres + RLS (multi-tenant cloud) |
| **Parsing** | tree-sitter, 23 languages | Regex + LLM (currently) |
| **Blast radius** | BFS via SQLite recursive CTE (≤2 hops by default) on SHA-hashed import/call edges | Postgres recursive CTE, ≤5 hops, confidence + freshness gated |
| **Update model** | Incremental, hash-diff, dependent expansion, sub-2s on 2,900 files | Full re-extraction per run |
| **Risk scoring** | Multi-factor: flow membership, community crossing, test coverage, security keywords, caller count | Edge `confidence` field; no node-level risk |
| **Knowledge layer** | Pure structural — no business context | LLM-extracted business context, annotations, owners |
| **Distribution** | Pip + MCP + VS Code | Self-hosted backend + frontend + VS Code |

CRG is **the structural layer we're missing**. We have **the semantic layer they don't have**. Plugging the right pieces of CRG into company-brain gives us, in one shot, what proposed-ADR-007 (merkle/tree-sitter) was trying to design from scratch — but with a battle-tested, benchmarked implementation we can lift.

The most valuable things to steal: **the parser + qualified-name scheme, the SHA-256 dependent expansion, the multi-factor risk scoring, the flow detection, and the bridge/hub centrality**. Skip: **the SQLite store, the eval framework, and the MCP plumbing** — we have replacements.

---

## What CRG actually does

### 1. Tree-sitter parser → `nodes` + `edges` in SQLite

`code_review_graph/parser.py` is 4,750 lines. It walks AST, pattern-matches against per-language `_CLASS_TYPES` / `_FUNCTION_TYPES` / `_IMPORT_TYPES` mappings, and emits two record types:

```sql
CREATE TABLE nodes (
    id INTEGER PRIMARY KEY,
    kind TEXT,              -- File | Class | Function | Test | Type
    qualified_name TEXT UNIQUE,
    file_path TEXT,
    line_start INTEGER, line_end INTEGER,
    language TEXT,
    file_hash TEXT,         -- sha256 of file content
    is_test INTEGER,
    extra TEXT              -- JSON
);

CREATE TABLE edges (
    kind TEXT,              -- CALLS | IMPORTS_FROM | INHERITS | IMPLEMENTS | CONTAINS | TESTED_BY
    source_qualified TEXT,
    target_qualified TEXT,
    file_path TEXT, line INTEGER
);
```

Qualified names are the key: `path/to/file.py::ClassName.method_name`. This is a portable, parser-derived primary key — no UUIDs, no DB-generated IDs needed for correlation.

23 languages supported via tree-sitter grammars: Python, TS/TSX, JS, Vue, Svelte, Go, Rust, Java, Scala, C#, Ruby, Kotlin, Swift, PHP, Solidity, C/C++, Dart, R, Perl, Lua, Zig, PowerShell, Julia, plus Jupyter.

### 2. Incremental update via SHA-256 hashing + dependent expansion

`incremental.py:818` (`incremental_update`) is the key routine:

```python
# 1. Get changed files from git diff
changed_files = get_changed_files(repo_root, base)

# 2. For each changed file, find dependents up to max_hops=2
for rel_path in changed_files:
    deps = find_dependents(store, full_path)   # walks IMPORTS_FROM/CALLS in reverse
    dependent_files |= deps

# 3. For each (changed ∪ dependent) file, hash it
#    Skip if hash unchanged
fhash = hashlib.sha256(raw).hexdigest()
if existing_nodes[0].file_hash == fhash:
    continue  # truly unchanged, skip parse

# 4. Re-parse the survivors with ProcessPoolExecutor (8 workers)
```

This is exactly the merkle invalidation pattern proposed-ADR-007 wanted to invent — already implemented, with concurrency, error tolerance, and dependent-expansion caps (`_MAX_DEPENDENT_FILES = 500`).

### 3. Blast radius via SQLite recursive CTE

`graph.py:625` (`get_impact_radius_sql`):

```sql
WITH RECURSIVE impacted(node_qn, depth) AS (
    SELECT qn, 0 FROM _impact_seeds
    UNION
    -- Forward: things this affects
    SELECT e.target_qualified, i.depth + 1
    FROM impacted i
    JOIN edges e ON e.source_qualified = i.node_qn
    WHERE i.depth < ?
    UNION
    -- Reverse: things that depend on this
    SELECT e.source_qualified, i.depth + 1
    FROM impacted i
    JOIN edges e ON e.target_qualified = i.node_qn
    WHERE i.depth < ?
)
SELECT DISTINCT node_qn, MIN(depth) FROM impacted
GROUP BY node_qn LIMIT ?
```

Two important differences from our `BlastRadiusService`:

1. **Bidirectional traversal in a single CTE.** Both forward (CALLS) and reverse (CALLED_BY) walked simultaneously. Our current Java service only walks forward (`e.source_id = br.id`). For "what breaks if I change this?" you want both directions — code downstream is impacted, and code upstream that handed it data is impacted.
2. **Seeds via temp table.** Instead of `WHERE id IN (...)` with N placeholders, they `CREATE TEMP TABLE _impact_seeds` and `INSERT ... ON CONFLICT IGNORE`. Avoids the SQLite/Postgres parameter limit (default ~32K) when you have many seeds and gives a cleaner query plan.

### 4. Multi-factor risk scoring (this is the gold)

`changes.py:217` (`compute_risk_score`) — for any changed node, scores 0.0–1.0 by combining:

| Factor | Weight | Logic |
|---|---|---|
| Flow participation | up to 0.25 | sum of criticality across flows the node is in |
| Cross-community calls | up to 0.15 | callers from different community → architectural coupling |
| Test coverage gap | up to 0.30 | untested = +0.30, 5+ tests = +0.05; transitive `TESTED_BY` |
| Security keyword match | +0.20 | name contains `auth`, `password`, `token`, `secret`, etc. |
| Caller count | up to 0.10 | popular nodes are riskier to change |

This is materially better than a single `confidence` number on edges. Our blast-radius response gives you nodes; CRG's gives you nodes *with risk scores* — which is what reviewers and AI assistants actually need.

### 5. Flow detection

`flows.py:150` (`detect_entry_points`) finds entry points by three heuristics:

1. No incoming `CALLS` edges (true root)
2. Framework decorator pattern (`@app.get`, `@router.post`, `@Scheduled`, `@RequestMapping`, `@Component`, etc. — 30+ regexes covering Spring, FastAPI, Express, Django, Celery, Click, Pydantic, Android, Kotlin)
3. Conventional name (`main`, `handler`, etc.)

From each entry point, BFS forward through `CALLS` edges → a `flow` (named execution path with depth, node count, criticality). Stored in dedicated `flows` and `flow_memberships` tables. The decorator regex list alone is worth lifting — covers more frameworks than our current `_TS_API_CALL_RE` / `_PY_ROUTE_RE`.

### 6. Communities + hubs + bridges (graph topology)

- **`communities.py`** uses Leiden algorithm (via igraph) to cluster the call graph into modules, with a fallback to file-based grouping. Each community gets cohesion score, dominant language, auto-generated description.
- **`analysis.py:14`** (`find_hub_nodes`) — top-N most-connected nodes by in+out degree.
- **`analysis.py:58`** (`find_bridge_nodes`) — top-N by betweenness centrality (NetworkX, sampled at >5K nodes).

These give "architectural" answers: "what's the chokepoint of this codebase?" "Which 5 functions, if they break, take everything down?" Today company-brain has no answer to those questions.

### 7. MCP server + VS Code extension

22 MCP tools wrap every store query: `get_review_context`, `get_impact_radius`, `query_graph` (callers_of, callees_of, etc.), `semantic_search_nodes`, `large_functions`, `affected_flows`, `community_architecture`, `find_hubs`, `find_bridges`. The VS Code extension hits these via stdio. We don't need this layer — we have our own. But the **tool surface as a contract** is worth studying: it's exactly the API surface AI assistants actually use.

---

## Where it falls short

CRG is great at *structure* and bad at *meaning*. Specifically:

- **No business context.** No annotations, no PR text linkage, no ticket integration. The graph knows `chargePayment` calls `creditCardClient` but cannot tell you *why*, who owns it, or what policy it implements.
- **No multi-tenancy.** SQLite per repo, no workspace isolation. Reviews against a single repo at a time.
- **No cross-repo edges.** Backend ↔ frontend ↔ shared schemas all need separate graph builds; no native joins.
- **No staleness / confidence model on edges.** All edges treated equal; no decay.
- **No LLM extraction at all.** Pure structural — which is the strength *and* the ceiling.
- **Single-machine deployment.** No API server, no multi-user collaboration. The graph is a local artifact.

The intersection looks like this:

```
        Structure (CRG strong)              Meaning (we're strong)
           ↓                                       ↓
  ┌──────────────────────┐              ┌──────────────────────┐
  │  tree-sitter parse   │              │  4-pass LLM extract  │
  │  call/import edges   │              │  business context    │
  │  hash-diff merkle    │              │  annotations         │
  │  bidirectional BFS   │              │  owner metadata      │
  │  risk scoring        │              │  multi-tenant Postgres│
  │  flow detection      │              │  encrypted node_context│
  │  hubs/bridges        │              │  Skills File API plan │
  └──────────────────────┘              └──────────────────────┘
                  ↘                          ↙
                   ┌────────────────────────┐
                   │   What we want:        │
                   │   structure × meaning  │
                   └────────────────────────┘
```

---

## What we should adopt

I rank these by ratio of value-to-effort.

### Tier 1 — Adopt directly (high value, low effort)

**1. Tree-sitter parser, ported to our pipeline as a Python module.** Replace `_TS_API_CALL_RE`, `_PY_ROUTE_RE`, `_TS_IMPORT_RE` etc. in `CodeTracer` with the CRG parser's output. We get 23 languages and structural correctness for free. The parser file is self-contained — the only dependency is `tree-sitter-languages`. Keep our existing `nodes`/`edges` Postgres schema; it's a superset of CRG's. Add a thin adapter that maps `(kind, qualified_name)` from CRG into our `(node_type, external_id)`.

**2. SHA-256 file hashes + `find_dependents` reverse-import expansion.** This is the merkle layer proposed-ADR-007 wanted. CRG already wrote it, debugged it, and benchmarked it (sub-2s on 2,900 files). Lift `incremental.py`'s `find_dependents`, `_single_hop_dependents`, `incremental_update` algorithms; rewrite for Postgres + our schema; keep the `ProcessPoolExecutor` parallelism pattern.

**3. Multi-factor risk scoring on nodes.** Add a `risk_score FLOAT` column to `nodes` (computed, not LLM-derived). Run CRG's `compute_risk_score` formula — security keyword match, caller count, test gap, cross-community calls — and store the result. `BlastRadiusResponse.affectedNodes[].riskScore` becomes a queryable, sortable field. This is one of the most user-visible improvements we can ship in <1 week.

**4. Bidirectional traversal in `BlastRadiusService`.** Our current CTE only walks forward. Add a `UNION` branch for reverse edges. Behavior change: the response now includes both downstream impact and upstream callers. Wire a `direction` parameter (`forward` | `reverse` | `both`, default `both`) so existing callers can opt in.

### Tier 2 — Adopt with adaptation (high value, medium effort)

**5. Flow detection.** Lift the entry-point heuristics — especially the 30+ framework decorator regexes — into our pipeline. After tree-sitter parse, run `detect_entry_points`, then BFS to build named flows. Store in a new `flows` + `flow_memberships` table per ADR-005 artifact-centric pattern. Flows become first-class graph nodes with criticality scores. AI Ask gets a new question type: *"what's the user signup flow?"* answered by retrieving the named flow rather than guessing from imports.

**6. Hub + bridge analysis.** Add a periodic job (nightly) that computes top-N hubs (by degree) and top-N bridges (by betweenness centrality) per workspace, stores them in a `graph_metrics` table. Surface in the frontend as "architectural critical path." The implementation is ~120 lines of Python using NetworkX; CRG's sampling logic for >5K nodes graphs is the only nontrivial part.

**7. Communities (Leiden).** Lower priority — useful for visualization and for the "cross-community calls" risk factor (#3 above). If we adopt #3, we either need community detection or a cheap proxy (file-prefix grouping is the fallback CRG already implements).

### Tier 3 — Architectural patterns to borrow but not lift directly

**8. Qualified-name scheme as `external_id`.** Today our `nodes.external_id` is whatever the collector chooses. Standardize on CRG's `path/to/file::ClassName.method_name` for all code-derived nodes. Stable across runs, parser-independent, human-readable. Non-code nodes (Policy, Ticket) keep their own conventions.

**9. MCP tool surface as our API contract.** CRG's 22 MCP tools (`callers_of`, `callees_of`, `imports_of`, `tests_for`, `inheritors_of`, `file_summary`, `large_functions`, `affected_flows`) are a great API design study. Most of these are missing from our backend. Add them to `GraphController` as REST endpoints; they become the substrate AI Ask queries against.

**10. Eval framework (`code_review_graph/eval/`).** They run automated benchmarks against 6 real open-source repos, scoring token reduction and impact recall/precision. We should build the equivalent: a fixture set of "real changes with known business outcomes," then measure AI Ask answer quality against it as we evolve the system.

### Skip

- **The SQLite store** — we're Postgres + RLS for multi-tenant.
- **The MCP server stdio plumbing** — we expose REST + WebSocket.
- **The VS Code extension** — we have our own.
- **The pip-install / one-command install** — different distribution model.
- **The whole `embeddings.py`** — we're already planning pgvector via ADR-008/RETRIEVAL-ARCHITECTURE.

---

## How this changes our pending ADRs

**Proposed ADR-007 (merkle + tree-sitter):** *Mostly written for us already.* The proposal becomes "adopt CRG's parser and incremental engine" rather than "design from scratch." Implementation drops from ~3 weeks to ~1 week. The artifact-centric framing in ADR-005 still applies — source files are one `kind` of artifact, and CRG's hash-diff is the file-kind dirty-set computation.

**Proposed ADR-008 (tiered memory):** *Unchanged but enriched.* The T0 memory token can now include risk score and flow membership ("getPayerCompetitors → reads payer_competitors, **risk 0.78**, in flow 'competitiveness-dashboard'") — both fields come from CRG-derived metrics. Better signal at zero LLM cost.

**Existing ADR-004 (universal knowledge schema):** *Validated.* CRG's qualified-name scheme is exactly the kind of `external_id` convention the universal schema needs to formalize. The `(domain, entity_type)` taxonomy gets `(code, function)`, `(code, class)`, `(code, flow)` for free.

**ADR-005 (artifact-centric pipeline):** *Unchanged.* CRG's parser becomes a "structural extractor" that runs after `ArtifactWriter` for `kind=source_file` artifacts. It feeds the graph independent of, and earlier than, the four-pass LLM extractor.

---

## Recommended implementation order

If we agree to adopt, the sequencing is:

1. **Week 1:** Ship the tree-sitter parser as a new Python module in `company-brain-ai`. Verify it produces equivalent or better output on a known Java Spring Boot test workspace vs. current regex.
2. **Week 1–2:** Add SHA-256 hashing to source-file artifacts (per ADR-005). Implement `find_dependents` against our Postgres edges. Wire dirty-set into orchestrator.
3. **Week 2:** Compute and store `risk_score` on every node. Add the field to `BlastRadiusNode` DTO and frontend display. This is the most demo-able win.
4. **Week 2–3:** Bidirectional CTE in `BlastRadiusService`. Behind a feature flag for rollback.
5. **Week 3:** Flow detection, stored as first-class entities. New AI Ask question type.
6. **Week 4:** Hub + bridge nightly job. Frontend "architectural overview" panel.

Total: ~4 weeks for a step-change in structural intelligence, with all four pending ADRs benefiting.

---

## The one-line summary

**code-review-graph is the structural substrate proposed-ADR-007 wanted to build, already shipped and benchmarked. Lift its parser, hash-diff, risk scoring, and flow detection into our Postgres schema; keep our LLM-derived semantic layer and multi-tenancy on top. The combination — structural correctness from CRG plus business meaning from us — is what neither system has alone.**
