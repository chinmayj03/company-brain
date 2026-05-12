# ADR-0058 — Generated-Code & Schema-Format Awareness (jOOQ tables, OpenAPI, Proto, GraphQL, DDL)

**Status:** Proposed
**Date:** 2026-05-11
**Builds on:** ADR-0057 (universal file walker — owns the FILE-existence; this ADR owns the DEEP extraction of schema-shaped files)
**Sequenced with:** ADR-0055/56/57/59/60 — six-ADR set, parallel-shippable.

---

## Context

The benchmark exposed three classes of file the brain ignores entirely:

1. **DDL / SQL migrations** (`apps/.../db/migration/V*.sql`, `tests/.../db/seed/R*.sql`). The brain never knows what `comp_providers.payer_id` is (a `text[]` array column — central to the lob query). 9 questions in the benchmark depend on this.

2. **Generated code** (`target/generated-sources/jooq/**/Tables.java`, `Records.java`). These ARE the schema, materialised. They contain the exact column types and table names jOOQ DSL references. Without them, the brain doesn't know that `PLAN_INFO.PAYER_PLAN_ID` is `VARCHAR(64)`.

3. **API & RPC schemas** — OpenAPI YAML, Protobuf `.proto`, GraphQL `.graphqls`, Avro `.avsc`. These define cross-service contracts. Asked "what's the contract for /v1/payers", the brain has no clue if the OpenAPI spec exists in the repo.

ADR-0057 generalises the file walker to NOTICE these files and emit basic entities. ADR-0058 (this one) adds the **deep, structured extraction** specific to each schema format.

---

## Decision

Five typed extractors, each parsing its format with the right tooling (no LLM — these are unambiguous formal specs).

### Extractor S1 — SQL DDL (PostgreSQL-flavoured first; extensible)

Parser: tree-sitter `tree-sitter-sql` grammar.

Emits:

```python
@dataclass
class DatabaseTable:
    entity_type: str = "DatabaseTable"
    name: str
    schema: str = "public"
    source_file: str
    line_range: tuple[int, int]
    primary_key_columns: list[str]
    is_partitioned: bool = False
    partition_strategy: str | None = None  # "RANGE", "LIST", "HASH"


@dataclass
class DatabaseColumn:
    entity_type: str = "DatabaseColumn"
    name: str
    table_urn: str
    type: str            # "text", "text[]", "varchar(64)", "jsonb", "uuid"
    nullable: bool
    default_value: str | None
    is_primary_key: bool
    is_foreign_key: bool
    fk_references: str | None    # "schema.table.column" if FK


@dataclass
class DatabaseIndex:
    entity_type: str = "DatabaseIndex"
    name: str
    table_urn: str
    columns: list[str]
    is_unique: bool
    where_clause: str | None     # for partial indexes
```

Edges:
- `MIGRATION_CREATES` (Migration file → DatabaseTable)
- `MIGRATION_ALTERS` (Migration file → DatabaseTable)
- `INDEXES` (DatabaseIndex → DatabaseTable)
- `FOREIGN_KEY` (DatabaseColumn → DatabaseColumn)

Also handles `CREATE TYPE`, `CREATE EXTENSION`, `CREATE FUNCTION`, `CREATE TRIGGER` for completeness.

### Extractor S2 — Generated jOOQ Tables.java

When walker sees `target/generated-sources/jooq/**/Tables.java`, parse the generated Java to map jOOQ field constants to DDL columns.

Why: jOOQ DSL code uses `PLAN_INFO.PAYER_PLAN_ID` (a generated constant). Without this mapping, the brain can't link a code reference to a `DatabaseColumn` entity.

Emits:
- `JooqTableBinding { jooq_class: "Tables", java_constant: "PLAN_INFO", db_table_urn: "..." }`
- `JooqFieldBinding { jooq_constant: "PLAN_INFO.PAYER_PLAN_ID", db_column_urn: "..." }`

Edges:
- `BINDS_TO_TABLE` (JooqTableBinding → DatabaseTable)
- `BINDS_TO_COLUMN` (JooqFieldBinding → DatabaseColumn)

Now when extraction code says `READS_COLUMN PLAN_INFO.PAYER_PLAN_ID`, it can be resolved to the actual DB column.

### Extractor S3 — OpenAPI / Swagger

Parser: `openapi-pydantic` (or `prance` for validation).

Emits:

```python
@dataclass
class OpenAPIOperation:
    entity_type: str = "OpenAPIOperation"
    operation_id: str
    method: str
    path: str
    summary: str
    description: str
    tags: list[str]
    request_schema_ref: str | None
    response_schemas: dict[int, str]   # status_code → schema_ref


@dataclass
class OpenAPISchema:
    entity_type: str = "OpenAPISchema"
    name: str
    type: str
    properties: dict[str, dict]        # field_name → {type, format, ...}
    required: list[str]
```

Edges:
- `DOCUMENTS` (OpenAPIOperation → ApiEndpoint, when path+method match an extracted Spring controller)
- `SCHEMA_REQUEST` / `SCHEMA_RESPONSE` (OpenAPIOperation → OpenAPISchema)

The DOCUMENTS edge is the killer feature: it links the spec to the implementation. Drift detection becomes trivial — any spec without an implementation, or implementation without spec.

### Extractor S4 — Protobuf

Parser: `betterproto` lib or grpc_tools. Emits `ProtoMessage`, `ProtoService`, `ProtoRpc` entities; edges link to gRPC implementations in code.

### Extractor S5 — GraphQL SDL

Parser: `graphql-core`. Emits `GraphQLType`, `GraphQLField`, `GraphQLQuery`, `GraphQLMutation`. Edges link to resolver implementations.

Avro / Thrift handled by the same dispatch pattern; included only when target repo uses them.

---

## Cross-edges that unlock benchmark questions

Once these extractors run, edges that previously were invisible become first-class:

- `READS_COLUMN` (Method → DatabaseColumn) — was guessed from query_text strings, now resolved precisely via JooqFieldBinding.
- `MIGRATION_CREATES` (V1__baseline.sql → comp_providers) — now A9 ("which tables have a lob column") is a direct query.
- `DOCUMENTS` (OpenAPIOperation → ApiEndpoint) — surfaces "spec without impl" / "impl without spec" drift.
- `IMPLEMENTS_RPC` (Method → ProtoRpc) — for gRPC backends.
- `RESOLVES` (Method → GraphQLField) — for GraphQL backends.

---

## File ownership for THIS PR (parallel-safe)

```
company-brain-ai/src/companybrain/extractors/schema_sql.py            # NEW (S1)
company-brain-ai/src/companybrain/extractors/jooq_binding.py          # NEW (S2)
company-brain-ai/src/companybrain/extractors/schema_openapi.py        # NEW (S3)
company-brain-ai/src/companybrain/extractors/schema_proto.py          # NEW (S4)
company-brain-ai/src/companybrain/extractors/schema_graphql.py        # NEW (S5)
company-brain-ai/src/companybrain/extractors/schema_resolver.py       # NEW — cross-edges resolver
tests/unit/test_schema_extractors.py                                    # NEW
tests/acceptance/test_schema_lob_column_resolved.py                     # NEW
```

Append-only edits to:

```
company-brain-ai/src/companybrain/extractors/dispatch.py    # owned by ADR-0057; we add 5 new entries
company-brain-ai/src/companybrain/models/entities.py        # add DatabaseTable/Column/Index, JooqBindings,
                                                             # OpenAPI*, ProtoMessage*, GraphQL* + edges
company-brain-ai/src/companybrain/pipeline/orchestrator.py  # invoke schema_resolver after universal extraction
pyproject.toml                                                # tree-sitter-sql, openapi-pydantic, betterproto
```

If ADR-0057 is being implemented in parallel: coordinate via a tiny shared file (`extractors/dispatch.py` is owned by 0057; this ADR APPENDS entries via a registration call). No real conflict.

---

## Acceptance test

```python
async def test_lob_column_resolved_to_real_column():
    """A9 from the benchmark — must now PASS after S1 + S2."""
    await run_pipeline_harness(repo="fixtures/network-iq-snapshot")
    answer = await brain_query("which tables in the database have a lob column? list them.")
    # Should be backed by DatabaseColumn entities, not just code references
    assert any(t in answer for t in ("plan_info", "comp_providers", "payer_plan"))


async def test_jooq_binding_links_code_to_column():
    """When code says READS_COLUMN PLAN_INFO.PAYER_PLAN_ID, the brain can
    follow JooqFieldBinding → DatabaseColumn."""
    await run_pipeline_harness(repo="fixtures/network-iq-snapshot")
    edges = await brain_query("READS_COLUMN edges from getPayerCompetitors")
    # Must include the actual db column URN, not just the jOOQ constant name
    assert any("plan_info.payer_plan_id" in e.target.lower() for e in edges)


async def test_openapi_drift_detection():
    """If repo has openapi.yaml with /payers endpoint but no @PostMapping('/payers'),
    surface the drift as a HasOrphanedSpec entity."""
    drift = await brain_query("OpenAPIOperation entities with no DOCUMENTS edge")
    # In the test fixture, mark an intentional drift; assert it surfaces
    assert len(drift) > 0


async def test_text_array_column_type_known():
    """The brain must know comp_providers.payer_id is text[] (not text)."""
    col = await brain_query("DatabaseColumn comp_providers.payer_id")
    assert col.type == "text[]"
```

---

## Effort estimate

4 days. SQL is the longest (~1.5 day) because tree-sitter-sql output needs nontrivial mapping to typed entities. OpenAPI is ~half day (mature library). jOOQ Tables.java is ~half day. Proto + GraphQL are ~half day each.

---

## Action items

1. [ ] `extractors/schema_sql.py` — tree-sitter-sql; emit DatabaseTable/Column/Index + Migration edges.
2. [ ] `extractors/jooq_binding.py` — parse `target/generated-sources/jooq/**/Tables.java`; emit JooqTableBinding/FieldBinding.
3. [ ] `extractors/schema_openapi.py` — openapi-pydantic; emit OpenAPIOperation/Schema; resolve DOCUMENTS edges.
4. [ ] `extractors/schema_proto.py` — betterproto; emit ProtoMessage/Service/Rpc.
5. [ ] `extractors/schema_graphql.py` — graphql-core; emit GraphQLType/Field/Query/Mutation.
6. [ ] `extractors/schema_resolver.py` — second pass that resolves string-named READS_COLUMN edges to actual DatabaseColumn URNs via JooqFieldBinding.
7. [ ] Add deps to `pyproject.toml`.
8. [ ] Acceptance: A9 (lob columns) + JooqBinding test + OpenAPI drift + text[] type all PASS.
