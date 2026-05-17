# A1.1 SQL Deep Extractor — Task Checklist

## T1 — Add sqlglot dependency
- [x] Add `sqlglot>=25.0.0` to `company-brain-ai/pyproject.toml` dependencies

## T2 — sql_deep.py (sqlglot DDL+DML+DQL parser)
- [x] `EmbeddedSqlStatement` dataclass (raw_sql, stmt_type, tables, columns, confidence_tier, line_start, line_end, source_file, repo)
- [x] `SqlDeepExtractor.extract(path, content)` — supports `.sql` files
- [x] sqlglot multi-statement parse with dialect auto-detection
- [x] Statement type classification (DDL/DML/DQL)
- [x] Table + column extraction from AST
- [x] Column-level lineage via `sqlglot.lineage` for SELECT statements
- [x] Confidence tier: `literal_string` for .sql files
- [x] Feature flag: `SQL_DEEP_EXTRACTOR_ENABLED` check at top of extract()
- [x] Legacy bridge: when flag=false, delegate to `SchemaSqlExtractor`

## T3 — sql_patterns/ (focused pattern matchers)
- [x] `__init__.py`
- [x] `jpa_patterns.py` — `@Query(…)` and `@NamedQuery(…)` regex extractor, returns `(sql_text, line_no, tier)` list
- [x] `mybatis_patterns.py` — `@Select/@Insert/@Update/@Delete` annotations
- [x] `jdbc_patterns.py` — `prepareStatement(…)`, `executeQuery(…)`, `executeUpdate(…)` string literals

## T4 — sql_embedded_scanner.py (tree-sitter Java scanner)
- [x] Walk Java file with tree-sitter-java AST
- [x] Dispatch to pattern matchers
- [x] Return list of `EmbeddedSqlStatement`
- [x] Confidence tier assignment per pattern type

## T5 — Golden-set fixtures (30 examples)
- [x] 10x JPA @Query JPQL/native (from company-brain-backend patterns)
- [x] 5x MyBatis @Select/@Insert/@Update/@Delete
- [x] 5x JDBC prepareStatement
- [x] 5x raw .sql DDL (CREATE TABLE, ALTER TABLE, CREATE INDEX)
- [x] 5x raw .sql DML (INSERT, UPDATE, DELETE, SELECT)
- [x] Each fixture: input file + expected extraction JSON

## T6 — Acceptance test
- [x] `tests/acceptance/test_sql_deep_extraction.py`
- [x] Load all 30 fixtures
- [x] Run `SqlDeepExtractor` + `SqlEmbeddedScanner` on each
- [x] Assert coverage > 75%
- [x] Assert JPA @Query found in >= 5 files
- [x] Assert confidence tiers correctly assigned
- [x] Assert no regression: `SchemaSqlExtractor` still passes when flag=false
- [x] Assert all DML types present in results

## T7 — Evidence
- [x] `specs/2026-Q2-A1.1-sql-deep-extractor/03-evidence/coverage.md` — coverage number + example extractions
