# Implementation prompt for ADR-0042 — Language-Agnostic Extraction Enhancements

> Paste this into a fresh Claude Code session inside the `company-brain` repo.
> The prompt is self-contained — Claude Code does not need this conversation's
> history to act on it.

---

You are implementing ADR-0042 (`docs/adrs/ADR-0042-language-agnostic-extraction-enhancements.md`).
Read that ADR first, then this prompt, then begin.

## Working agreement

1. Every change must work for **Java, Python, and TypeScript** repositories
   without per-language branching. If you find yourself writing
   `if file.endswith('.java')` in any orchestrator path (extractors are OK
   to delegate to, but the orchestrator must be language-blind), STOP and
   factor the framework knowledge into the LLM prompt instead.
2. Every new LLM pass uses the existing provider via `chat_json()` with
   `cache_control:ephemeral` already wired by `AnthropicProvider.chat()`.
3. Every new LLM pass returns a JSON object validated by a Pydantic model
   you define alongside the pass.
4. Every new LLM pass logs `<pass_name> ENTER rows=N` and either
   `<pass_name> OK edges=N` or `<pass_name> FAILED error=…` so we can
   diagnose silent regressions (today's ALL-batches-failed lessons).
5. Every new pass has a `BRAIN_SKIP_<pass_name>` env flag so operators
   can disable individual passes for cost / debug.
6. Every new pass has at least one fixture-based test under
   `tests/passes/test_<pass>.py` covering Java, Python, TypeScript inputs.

## Workstream 1 — E2 / E3 / E5 / E6 / E7 (LLM pattern-recognizer passes)

Five passes share a common shape. Build them in this order so each can
piggyback the previous's output:

### 1.1 Add `companybrain/pipeline/passes/` package

```
src/companybrain/pipeline/passes/
  __init__.py
  base.py                       # ExtractionPass abstract base
  annotation_pass.py            # E2
  storage_target_pass.py        # E3
  schema_migration_pass.py      # E5
  client_call_pass.py           # E6
  test_coverage_pass.py         # E7
```

### 1.2 `base.py`

```python
class ExtractionPass(ABC):
    name: ClassVar[str]                           # used for logs + skip flag
    role: TaskRole = TaskRole.FAST                # default to Haiku
    max_tokens: int                               # per-call output cap
    schema: type[BaseModel]                       # Pydantic response shape

    async def run(self, units: list[CodeUnit],
                  entities: list[ExtractedEntity]) -> list[ExtractedRelationship]:
        if os.environ.get(f"BRAIN_SKIP_{self.name.upper()}", "").lower() == "true":
            log.info(f"{self.name} SKIPPED via env")
            return []
        # 1. Build per-call user message (subclass)
        # 2. await provider.chat_json() with the class-level system prompt
        # 3. Parse via self.schema, retry once on JSONDecodeError
        # 4. Convert to list[ExtractedRelationship] (subclass)
        # 5. Log ENTER / OK / FAILED with edge counts
```

### 1.3 Each pass's system prompt

**E2 — annotation_pass**

> *"You are a code analyst. For each entity in the input, identify framework
> annotations / decorators / pragmas that describe its lifecycle, transactionality,
> security, scheduling, caching, retry, observability, validation, or routing
> behaviour. Languages and frameworks may include but are not limited to:
> Java (Spring `@Transactional` / `@Cacheable` / `@Async` / `@Scheduled` /
> `@PreAuthorize`), Python (Flask `@route` / FastAPI `@app.get` / Django
> `@login_required` / `@cached_property` / `@retry`), TypeScript (NestJS
> `@Controller` / `@UseGuards` / NextJS `'use client'` / route handlers),
> Go (struct tags), C# (`[Authorize]`), Rust (`#[tokio::main]`).
>
> Emit one edge per annotation: edge_type=ANNOTATES, from=annotation name in
> CamelCase (e.g. 'Transactional', 'Cacheable', 'PreAuthorize'),
> to=entity_external_id."*

**E3 — storage_target_pass**

> *"You are a code analyst. List every persistent-storage target referenced
> in this file. Targets include: relational tables (jOOQ TABLE constants,
> Hibernate `@Table`, SQLAlchemy `__tablename__`, Prisma `model`, Drizzle
> `pgTable()`, Knex schema), NoSQL collections (MongoDB, DynamoDB, Firestore),
> caches (Redis keys), object stores (S3 buckets), or message-queue topics.
> Normalise the target name to lower_snake_case. Emit one DatabaseTable
> entity per target with name=normalised_name."*

**E5 — schema_migration_pass**

> *"This file appears to be a database schema or migration. List every
> table CREATED or ALTERED, every COLUMN ADDED or MODIFIED, and every
> INDEX created. Frameworks may include Flyway, Liquibase, Alembic, Prisma
> Migrate, Knex Migrate, Rails Migrations, Goose, Atlas, or raw SQL.
> Emit DatabaseTable + DatabaseColumn entities with column type and
> nullability."*

**E6 — client_call_pass**

> *"For each network call in this file (HTTP / gRPC / GraphQL / WebSocket /
> message queue / fetch / axios / RTK / Apollo / urllib / requests / native
> http client), identify the URL pattern, HTTP method, and request body type.
> Emit CALLS_ENDPOINT edges from the calling function/component to the
> ApiEndpoint name (use the URL path with parameters normalised to ':param'
> form so 'GET /users/123' and 'GET /users/${id}' both become
> 'GET /users/:id')."*

**E7 — test_coverage_pass**

> *"For each test method in the input, identify which production entity it
> primarily exercises. Test frameworks include but are not limited to:
> JUnit, TestNG (Java); pytest, unittest (Python); Mocha, Jest, Vitest
> (JS/TS); RSpec (Ruby); xUnit (.NET); Go testing.
> Emit TESTED_BY edges FROM the production entity TO the test entity
> (never reversed). Confidence: 1.0 if the test body explicitly calls the
> production entity by name; 0.85 if test class name follows
> `Foo*Test` → `Foo*` convention; skip otherwise."*

Each prompt has 3 few-shot examples spanning Java + Python + TypeScript.

## Workstream 2 — E1 (cross-file call graph)

`src/companybrain/pipeline/extraction_loop.py` already exists with `max_hops=2`.
Bump the default to 3 and improve the "what file owns symbol X" resolver:

```python
async def _resolve_symbol_to_file(symbol_name: str, repo_root: Path) -> Path | None:
    # 1. Tier 1 — ripgrep for `class <name>` / `def <name>` / `function <name>`
    #    across ALL languages with one regex.
    # 2. Tier 2 — if multiple matches, ask the LLM to pick the right one given
    #    the calling context (file path + caller name).
    ...
```

The LLM resolver call uses ~$0.0005 each. Cap at 5 LLM resolves per pipeline
run via a class-level counter.

## Workstream 3 — E4 (method-level freshness)

`src/companybrain/pipeline/structural_prepass.py` and
`src/companybrain/graph/java_client.py::check_freshness` need to grow
per-method content hashes.

1. tree-sitter already gives method byte ranges per language. Extend
   `SymbolTable.to_method_chunks()` to also emit `body_hash = sha256(body)`.
2. `check_freshness` payload grows a per-unit `method_hashes: dict[qname → hash]`.
3. Java's `ArtifactWriterService.checkFreshness` returns per-method `fresh`
   booleans alongside the existing artifact-level result.
4. `EntityExtractor` consumes the per-method map: fresh methods skip LLM
   and reload existing entity rows; dirty methods go to LLM as before.

## Workstream 4 — E8 (multi-pass relationships)

`relationship_extractor.py` already supports a single LLM call up to 80
relationships. Add a `_chunk_entities_for_extraction` helper:

```python
def _chunk_entities_for_extraction(entities: list[ExtractedEntity]) -> list[list[ExtractedEntity]]:
    """Co-locality chunking — group by file, then package, then call-graph
    proximity, batch-size ~25 entities so each batch fits the 60k char input
    budget with ~1500 chars per snippet."""
```

Then `extract()` runs the existing single-call logic per batch and merges +
dedups results.

## Workstream 5 — E9 (reverse-edge index)

Pure Java change. Add a `pipeline_jobs` post-step in `PipelineService`:

```sql
CREATE MATERIALIZED VIEW IF NOT EXISTS edges_reverse AS
SELECT workspace_id, target_id AS source_id, source_id AS target_id,
       edge_type, confidence, source, last_seen
FROM edges WHERE NOT is_pruned;
CREATE UNIQUE INDEX … ON edges_reverse (workspace_id, source_id, target_id, edge_type);
```

`REFRESH MATERIALIZED VIEW edges_reverse;` after every Phase 5 commit.

`brain blast-radius` then queries `edges_reverse` for the upstream side.

## Workstream 6 — E10 (question-aware retrieval)

`src/companybrain/api/routes/query.py` adds a `_classify_intent()` helper
called BEFORE `SmartZoneAssembler.assemble()`:

```python
class IntentRouterResponse(BaseModel):
    intent: Literal["impact-analysis", "trace-flow", "explain-purpose",
                    "find-callers", "find-tests", "schema-question", "general"]
    anchor_entities: list[str]
    edge_types_needed: list[str]
    max_hops: int = 2
    include_test_coverage: bool = False
```

The intent then drives a SmartZonePolicy that the assembler reads:

| intent             | hops | edge filter                            | tests |
|--------------------|------|----------------------------------------|-------|
| impact-analysis    | 4    | (no filter — all edges)                | yes   |
| trace-flow         | 5    | CALLS, CALLS_ENDPOINT, READS_COLUMN, WRITES_COLUMN | no |
| explain-purpose    | 1    | CONTAINS, USES, EXTENDS                | no    |
| find-callers       | 2    | reverse CALLS                          | no    |
| find-tests         | 1    | TESTED_BY                              | yes   |
| schema-question    | 2    | READS_COLUMN, WRITES_COLUMN, CONTAINS  | no    |

## Cross-cutting work

### Acceptance test fixtures

Add minimal repos under `tests/fixtures/`:

- `tests/fixtures/java_jpa/` — Spring Boot + JPA + jOOQ; one controller →
  one service → one repository → one query reading `users.email`.
- `tests/fixtures/python_sqlalchemy/` — FastAPI + SQLAlchemy; one router →
  one service → one repository → one query reading `users.email`.
- `tests/fixtures/typescript_drizzle/` — Next.js + Drizzle; one route →
  one service → one query reading `users.email`.

For each fixture, `tests/passes/test_e2e_per_language.py` asserts:
- ≥ 1 ApiEndpoint, ≥ 1 Function, ≥ 1 DatabaseQuery, ≥ 1 DatabaseTable,
  ≥ 1 DatabaseColumn entity.
- ≥ 1 CALLS edge from controller→service.
- ≥ 1 READS_COLUMN edge from query→`users.email`.
- ≥ 1 ANNOTATES edge for the framework annotation on the controller.

### Logging contract

Every new pass MUST include in its stage_summary entry:

```json
{
  "stage": "<pass_name>",
  "label": "...",
  "edges_emitted": N,
  "skipped_via_env": false,
  "duration_ms": M,
  "input_tokens": K,
  "output_tokens": K
}
```

So the operator can attribute cost per pass.

### Cost guard

Add a cumulative cost guard in `orchestrator.py`:

```python
if cumulative_cost_usd > settings.brain_job_budget_usd:
    log.error("Job exceeded budget — aborting before Stage X",
              spent=cumulative_cost_usd, budget=settings.brain_job_budget_usd)
    raise JobBudgetExceeded(...)
```

`brain_job_budget_usd` env var defaults to `0.50`.

### Documentation

Update these as you go:
- `docs/MIGRATION-mono-to-multirepo-to-company.md` — add ADR-0042 to the
  Stage 2 plan.
- `docs/POST-MERGE-RUNBOOK.md` — add the new BRAIN_SKIP_* env flags.
- `README.md` — add a "Supported languages" table listing Java / Python /
  TypeScript with the framework matrix.

## Definition of done

1. `make test` passes (existing + new fixture tests).
2. A fresh extraction run on each fixture produces the asserted entities + edges.
3. The lob-rename query against `network-iq-backend-java` returns a specific
   answer naming `plan_info.lob` and the affected call chain.
4. Total cost on a 110-entity Java run stays ≤ $0.20 with cost-cut flags ON.
5. ADR-0042 status changes from "Proposed" to "Accepted" with the merge SHA.
6. PR description lists every BRAIN_SKIP_* flag added.

## Non-goals (do NOT do these in this PR)

- C / C++ / Rust support (separate ADR).
- Real-time file-watcher mode (separate ADR).
- Custom DSL extraction beyond what the LLM identifies natively.
- Multi-tenant workspace isolation changes (already fine).
- Frontend UI changes (the new edges flow into the existing graph UI for free).

## Suggested PR breakdown

Split into 4 PRs to keep reviews small:

1. **PR-0042-1: passes/base.py + E2 (annotations)** — establishes the
   pass framework with one concrete pass.
2. **PR-0042-2: E3 + E5 + E6 + E7** — add the four LLM-driven passes.
3. **PR-0042-3: E1 + E4 + E8** — extraction-loop hop bump, method-level
   freshness, multi-pass relationships.
4. **PR-0042-4: E9 + E10 + acceptance tests + docs** — reverse-edge
   index, intent router, fixture tests, runbook updates.

Each PR ships independently and is value-add on its own.
