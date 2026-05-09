# ADR-0042 — Language-Agnostic Extraction Enhancements

Status:      Proposed
Date:        2026-05-10
Supersedes:  partial of ADR-0006 (heuristic extractors), ADR-0040 (Tier 1.B caps)
Companion:   See `SONNET-IMPLEMENTATION-PROMPT-ADR-0042.md` for the implementation
             brief that can be handed to Claude Code.

---

## 1. Context

After the persistence + cost-cut work shipped between commits `4d2d56093…52c547337`,
the brain produces:

- 110 entities + 33 edges (uses=21, calls=6, contains=6) for `network-iq-backend-java`
- 27-node blast-radius from `getPayerCompetitors`
- 21-field BusinessContext per entity, 100% rendering through to the LLM after
  the compressor fix in ADR-0042-precursor (commit pending push).

**It still cannot answer "what tables does X read"** for two reasons:

1. **Coverage gaps** — only ~6 CALLS edges, no READS_COLUMN / WRITES_COLUMN edges,
   no cross-file call chain beyond two hops, no annotation-derived edges
   (`@Transactional`, `@Cacheable`, `@Async`), no test→prod links.
2. **Java-only heuristics** — every fast-path we have today (interface fast-path,
   trivial-POJO fast-path, JPA query extractor, jOOQ DSL detection) is keyed on
   Java/Kotlin syntax. The same codebase in Python (SQLAlchemy / Django ORM) or
   TypeScript (Prisma / Drizzle / Knex) gets none of those wins.

We need a set of enhancements that **the LLM drives** (not regex-per-framework)
so the same code path works for every language, ORM, framework.

## 2. Decision

### 2.1 The core principle — "LLM-as-pattern-recognizer"

Whenever a heuristic would be language- or framework-specific, **delegate the
recognition to the LLM** with a focused prompt and let its training carry the
ORM / framework / DSL knowledge.

> Bad:   `if file.endswith('.java') and '@Query' in content: extract_jpa_query()`
>
> Good:  Send the file to the LLM with a prompt that says
>        *"Identify any database access in this code. The code may be ORM
>        (JPA, SQLAlchemy, ActiveRecord, Drizzle, …), raw SQL, or query
>        builders (jOOQ, Knex, QueryDSL). For each, emit a DatabaseQuery
>        entity with the equivalent SQL written out."*

This single principle replaces ~6 framework-specific regex extractors with
one LLM call that handles every framework the model knows about — which is
basically every framework that's been on Stack Overflow.

The fast-paths that remain (interface, trivial-POJO) **must be re-implemented
language-agnostically** — see §3.4.

### 2.2 The eight enhancements

| #    | Enhancement                                       | LLM or deterministic | Cost impact |
|------|----------------------------------------------------|----------------------|-------------|
| E1   | Cross-file call graph (anchored multi-hop)        | LLM (one focused pass per hop) | +$0.05 |
| E2   | Annotation-derived edges                          | LLM intent classifier | +$0.02 |
| E3   | Storage-target / table-constant extractor         | LLM (read storage refs) | +$0.03 |
| E4   | Method-level freshness (per-method content hash)  | Deterministic (AST hashing) | -$0.10 |
| E5   | Migration-derived schema entities                 | LLM (read migrations) | +$0.02 |
| E6   | Frontend ↔ backend route linking                  | LLM intent classifier | +$0.02 |
| E7   | Test ↔ prod TESTED_BY edges                       | LLM intent classifier | +$0.02 |
| E8   | Multi-pass relationship extraction                | LLM (chunked) | -$0.05 (smaller per-call) |
| E9*  | Reverse-edge pre-computation                      | Deterministic | -$0.02 |
| E10* | Question-aware retrieval                          | LLM (router) | +$0.005 per query |

`*` = additions not in the user's original list but high-leverage.

Net cost change for a 110-entity run: roughly cost-neutral.
Net edge density change: estimated **3-5×** (33 → 100-160 edges).

### 2.3 Each enhancement, briefly

#### E1 — Cross-file call graph (anchored multi-hop)

**Today**: Stage 1 extracts entities per file. Stage 2 sends ~30 entity bodies
to the LLM for relationship extraction. Anything beyond hop 2 of the call chain
is invisible.

**Change**: After Stage 2 produces the initial relationship set, walk every
CALLS / DELEGATES_TO / USES edge whose target name does NOT yet exist as an
entity. For each such "ghost target", look up the file via the symbol index
and add it to a "follow-up extraction queue". Run Stage 1 again on the queue.
Repeat for `max_hops` (default 3).

**Language-agnostic**: The follow-up extractor is the SAME Stage 1 LLM call —
just on more files. The "find the file for symbol X" step uses ripgrep (works
on any language) plus the LLM's symbol-resolution if ambiguous.

#### E2 — Annotation-derived edges

**Today**: `@Transactional` / `@Cacheable` / `@Scheduled` (Java), `@cached`
/ `@retry` (Python), `Authorize=` (C#), `defineAction()` (frontend) are all
ignored — they're treated as part of the method signature.

**Change**: Add an "annotation pass" that asks the LLM:
> *"For each entity, list the framework annotations / decorators that
> describe its lifecycle, transactionality, security, scheduling, caching,
> or retry behaviour. Emit edges of type ANNOTATES with the annotation
> name as the to_entity (e.g. ANNOTATES → 'Transactional', → 'Cacheable')."*

**Language-agnostic**: The prompt names broad categories, not specific syntaxes.
The LLM identifies the right tokens for each language.

#### E3 — Storage-target / table-constant extractor

**Today**: `Tables.PLAN_INFO` (jOOQ-generated constants) are seen as Class
references, not as DatabaseTable entities. Same for SQLAlchemy `Base.metadata.tables`,
Prisma `prisma.payer`, Drizzle `pgTable("...")`.

**Change**: Add a "storage-target pass" that asks the LLM:
> *"This file contains references to data storage. For each table /
> collection / index referenced (regardless of how — code-generated
> constants, ORM model classes, raw SQL, schema definitions), emit one
> DatabaseTable entity with the canonical lower-case snake_case table name."*

**Language-agnostic**: The prompt asks the LLM to enumerate "any data storage
target referenced". jOOQ, SQLAlchemy, Prisma, raw SQL, MongoDB collections —
all handled by the same pass.

#### E4 — Method-level freshness

**Today**: `check_freshness` hashes the whole file's content. If a 500-line
class has 1 method changed, all 14 entities get re-extracted via LLM.

**Change**: Hash each method body separately during structural pre-pass.
`check_freshness` returns a per-method `fresh / dirty` map; Stage 1 then
fetches existing entity rows for the fresh methods and only LLM-extracts
the dirty ones. Re-uses the existing `nodeIds` map shape (key by qname).

**Language-agnostic**: tree-sitter (already in use) gives per-method byte
ranges for every language with a grammar. Hashing is just `sha256(body)`.

#### E5 — Migration-derived schema entities

**Today**: DatabaseTable entities only get created when the LLM happens to
notice them in repository code. Schema definitions (Flyway, Liquibase,
Alembic, Prisma migrations, Knex migrations, Rails migrations) are ignored.

**Change**: Add a "schema-source pass" that asks the LLM:
> *"This file looks like a database schema or migration. List every table
> created/altered, every column added/modified, and every index created
> or dropped. For each, emit DatabaseTable + DatabaseColumn entities with
> column type and nullability."*

The orchestrator routes any file whose path contains `migrations/`,
`db/migrate/`, `prisma/`, `flyway/`, `liquibase/`, or whose name matches
`V*__.*\.sql` / `*.prisma` / `*alembic*` to this pass. The LLM then
verifies it's actually a migration before producing entities.

**Language-agnostic**: Path heuristics use a generous allowlist; the LLM is the
final arbiter of whether a file is a migration.

#### E6 — Frontend ↔ backend route linking

**Today**: A React component calling `axios.get('/api/v1/competitors')` is
extracted as a FrontendComponent but the call to the API endpoint is invisible.

**Change**: Add a "client-call pass" that asks the LLM:
> *"For each network call in this file (HTTP, gRPC, GraphQL, WebSocket,
> message queue, fetch / axios / RTK / Apollo / native http client),
> identify the URL pattern, HTTP method, and any request body type.
> Emit CALLS_ENDPOINT edges from the calling function/component to the
> matching ApiEndpoint entity."*

The matcher then tries to resolve `'/api/v1/competitors'` against ApiEndpoint
entities by URL pattern (with parameter normalisation `:id` ↔ `{id}` ↔ `*`).

**Language-agnostic**: The prompt names broad categories of network call;
the LLM handles specific libraries.

#### E7 — Test ↔ prod TESTED_BY edges

**Today**: Test files extract entities (test methods) but no edges link them
to the production methods they exercise.

**Change**: After all entities are extracted, run a single LLM call:
> *"For each test method in this codebase, identify which production
> entity (function, class, endpoint) it primarily exercises. Emit
> TESTED_BY edges from the production entity to the test entity."*

The LLM matches by call-site evidence in the test body. Confidence
calibration already in the relationship-extraction prompt covers this.

**Language-agnostic**: JUnit, pytest, Mocha, Jest, RSpec, xUnit — all
handled by the same prompt.

#### E8 — Multi-pass relationship extraction

**Today**: One LLM call sees the first 30 entity bodies (capped). Files
beyond that get name-only treatment, so call-site inference for those
entities is impossible.

**Change**: Chunk entities into batches of ~25 (sized to fit the new
60k char input budget) by **co-locality** — group by file, then by
package, then by call-graph proximity. Run the relationship extractor
once per batch. Merge + dedup at the end.

**Language-agnostic**: Already is.

#### E9 — Reverse-edge pre-computation

**Today**: Blast-radius is computed at query time via Cypher. Asking
"who calls X" walks the full graph.

**Change**: After Stage 5 (graph population), pre-compute a reverse-edge
table in Postgres (`edges_reverse`) keyed by target_id. Blast-radius
queries hit this with a single index lookup instead of a graph walk.

**Language-agnostic**: Pure DB work; no language dependence.

#### E10 — Question-aware retrieval

**Today**: SmartZoneAssembler classifies the task into one of READ /
WRITE / DEBUG / AUDIT / ONBOARD. The classification only changes which
fields the compressor keeps — it does NOT change which subgraph is fetched.

**Change**: Add an "intent router" — one ~$0.001 LLM call before
retrieval — that returns:

```json
{
  "intent": "impact-analysis | trace-flow | explain-purpose | find-callers | find-tests | …",
  "anchor_entities": ["lob_column", "getPayerCompetitors", …],
  "edge_types_needed": ["READS_COLUMN", "CALLS", "USES"],
  "max_hops": 3,
  "include_test_coverage": true
}
```

SmartZoneAssembler then fetches the EXACT subgraph that intent needs.

**Language-agnostic**: The router prompt is about question semantics, not code.

### 2.4 Cross-cutting language-agnostic invariants

1. **No regex with framework-specific tokens in the orchestrator path.**
   All such regex moves into prompt text the LLM reads.
2. **All path heuristics are allowlists** (Python suffix list, JS suffix
   list, etc.). The LLM verifies whether a file actually is what its path
   suggests.
3. **The fast-paths (`_is_pure_interface`, `_is_trivial_pojo`) get a
   language-agnostic upgrade** — the LLM is asked
   *"Does this file contain only data declarations and no behaviour?"*
   for files we can't classify by pure tree-sitter inspection.
4. **All emit-time logic uses only the canonical 50-edge taxonomy.** No
   new edge types are added per-language. If a Python decorator semantically
   matches `@Cacheable`, both emit ANNOTATES → "Cacheable".
5. **All entity types come from the canonical taxonomy**: Function, Class,
   ApiEndpoint, DatabaseQuery, DatabaseTable, DatabaseColumn, SchemaField,
   FrontendComponent, ExternalService, Annotation, Test. No SQLAlchemyModel
   or PrismaModel — they all map to Class with `metadata.framework` field.

### 2.5 LLM-quality tactics

To keep inference quality high while we add more passes:

- **Each pass has its own focused system prompt** (one task per call), with
  3–5 few-shot examples spanning at least 3 languages each.
- **All passes use cache_control:ephemeral** so the static system prompt
  amortises across the run (~$0.001 per cached call after first).
- **Each pass returns strict JSON** validated against a Pydantic model;
  malformed responses retry once with a corrective prompt fragment.
- **Token budgets per pass are pinned in `config.py`** (E1: 2500, E2: 800,
  E3: 1500, E5: 2500, E6: 1500, E7: 2500, E10: 300) so cost is bounded.
- **Confidence weighting**: the relationship extractor's existing
  confidence scale (1.0 / 0.9 / 0.7 / SKIP-anything-below) is used for
  every new pass. Edges below 0.7 are dropped.
- **Per-language acceptance tests** (Java, Python, TypeScript, Go) — see
  §4 for the test fixture set.

## 3. Consequences

### 3.1 Positive

- Same code path works for any language the LLM knows.
- Edge density increases ~3–5×; "what tables does X read" / "what calls X"
  become real first-class queries.
- Test coverage and frontend ↔ backend links unlock new query categories
  (impact analysis, dead-code detection, missing-test detection).
- Method-level freshness reduces re-extraction cost on edits to large files
  by ~80–95%.

### 3.2 Negative

- More LLM calls per run (8 new passes). Net cost change is small (~$0.00
  to +$0.05 per first run, then ~−$0.10 per subsequent run thanks to
  method-level freshness).
- More moving parts → more failure surface. Each new pass MUST log its
  enter/ok/fail counts (we learned that the hard way today).
- Question-aware retrieval (E10) adds latency to every query (~200ms for
  the router call). Default ON; users can opt out.

### 3.3 Migration

- All changes are additive at the persistence layer (no schema changes,
  same edge types).
- Existing `.brain/` JSONs are valid input; new fields are populated on
  next enrich.
- No Java DTO changes required.

## 4. Acceptance criteria

- A fresh extraction run on `network-iq-backend-java` (Java + jOOQ + JPA)
  produces ≥ 100 edges across ≥ 6 distinct edge types AND READS_COLUMN
  edges to specific table columns referenced in repository SQL.
- A fresh extraction run on a Python repo (e.g. a SQLAlchemy + FastAPI
  app, fixture provided in `tests/fixtures/python_sqlalchemy/`) produces
  the equivalent edges (READS_COLUMN, CALLS_ENDPOINT, ANNOTATES).
- A fresh extraction run on a TypeScript repo (Drizzle + Next.js) produces
  the equivalent edges.
- The lob-rename query returns a specific answer naming the affected
  columns and call chain.
- Total cost per run stays ≤ $0.20 with cost-cut flags ON.

## 5. Out of scope

- **C / C++ / Rust** — the LLM can handle them but tree-sitter integration
  for body extraction needs work; defer to a follow-up ADR.
- **Custom DSLs** (in-house query languages) — the LLM will treat them as
  unknown ORMs and emit lower-confidence edges; manual prompt tuning per
  customer.
- **Real-time updates** (file watcher → incremental re-extract); ADR-0042
  is batch-mode only. Streaming is a separate ADR.
