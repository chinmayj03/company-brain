# A1.1 SQL Deep Extractor — Implementation Plan

## Problem

The existing `schema_sql.py` handles only DDL (CREATE/ALTER TABLE, CREATE INDEX). Java
and Python codebases embed SQL in JPA `@Query`, `entityManager.createQuery`, JDBC
`PreparedStatement`, jOOQ DSL chains, and MyBatis annotations. This leaves ~85% of
real-world SQL queries undetected. Gate 1 target is >75% coverage on the golden set.

## Approach

### Layer 1 — sqlglot-based DDL+DML+DQL parser (`sql_deep.py`)

Replace `schema_sql.py:116-127` DDL-only dispatch with sqlglot's multi-dialect AST
parser, which handles:
- DDL: `CREATE TABLE`, `ALTER TABLE`, `CREATE INDEX` (parity with existing)
- DML: `INSERT`, `UPDATE`, `DELETE`
- DQL: `SELECT` (including CTEs, subqueries, window functions)

Extract per-statement: raw SQL text, statement type, tables referenced, columns
referenced, confidence tier, line range in source file.

Column-level lineage via `sqlglot.lineage` on parseable SELECT statements.

### Layer 2 — Tree-sitter embedded SQL scanner (`sql_embedded_scanner.py`)

Walk Java source files with tree-sitter-java AST to find:
1. `@Query("…")` annotation values on interface methods
2. `entityManager.createQuery("…")` / `createNativeQuery("…")` call arguments
3. `PreparedStatement` constructor + `connection.prepareStatement("…")` arguments
4. String variables whose name contains `sql`/`query`/`SQL`/`QUERY` assigned a literal

Each found string is then handed to `sql_deep.py` for parsing. Confidence tier
assigned based on context.

### Layer 3 — Pattern matchers (`sql_patterns/`)

Separate focused modules for:
- `jpa_patterns.py`: regex + AST scanning for `@Query`, `@NamedQuery`, JPQL/HQL
- `mybatis_patterns.py`: `@Select`, `@Insert`, `@Update`, `@Delete` annotations
- `jdbc_patterns.py`: `prepareStatement`, `executeQuery` string literals

### Layer 4 — Confidence tier assignment

Three tiers (highest to lowest):
| Tier | Meaning |
|---|---|
| `literal_string` | Complete SQL in one string literal; no concatenation |
| `prepared_statement` | SQL with `?` or `:name` placeholders; type-safe |
| `dynamic_concat` | SQL built via `+` or `StringBuilder`; best-effort |

### Layer 5 — Golden-set fixtures + acceptance test

30 SQL examples drawn from real patterns observed in `company-brain-backend`
repository files. Each fixture has expected: SQL text, statement type, tables, columns,
tier. Acceptance gate: >75% extracted correctly.

### Feature flag

`SQL_DEEP_EXTRACTOR_ENABLED` (env var + `config.py` setting, default `true`).
When `false`, `SchemaSqlExtractor` runs as before (legacy path, no regression).

## File ownership

| File | Action |
|---|---|
| `extractors/sql_deep.py` | NEW — sqlglot-based parser + column lineage |
| `extractors/sql_embedded_scanner.py` | NEW — tree-sitter Java embedded SQL scanner |
| `extractors/sql_patterns/__init__.py` | NEW |
| `extractors/sql_patterns/jpa_patterns.py` | NEW — JPA @Query, JPQL |
| `extractors/sql_patterns/mybatis_patterns.py` | NEW — MyBatis annotations |
| `extractors/sql_patterns/jdbc_patterns.py` | NEW — JDBC PreparedStatement |
| `extractors/schema_sql.py` | KEEP as legacy path (no changes) |
| `tests/fixtures/sql_golden_set/` | NEW — 30 fixture files |
| `tests/acceptance/test_sql_deep_extraction.py` | NEW — acceptance gate |
| `company-brain-ai/pyproject.toml` | ADD sqlglot dependency |

## Dependencies to add

- `sqlglot>=25.0.0` — multi-dialect SQL AST parser + lineage engine
  (tree-sitter and tree-sitter-java already present in pyproject.toml)

## What is NOT in scope

- Python embedded SQL patterns (Wave 2 / SCIP session)
- MyBatis XML mapper files (Wave 2)
- Stored procedure / function bodies
- Any changes to `schema_sql.py` (kept strictly as legacy path)
- Any changes to retrieval, query, or API layers
