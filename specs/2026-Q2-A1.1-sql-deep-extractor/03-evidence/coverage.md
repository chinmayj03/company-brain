# A1.1 SQL Deep Extractor — Coverage Evidence

## Golden-Set Coverage

**Coverage: 30/30 = 100.0%**
Gate 1 target: > 75% ✓ (exceeded by 25pp)

All 30 fixture files yielded at least one extracted SQL statement.

## Acceptance Test Results

```
45 passed, 0 failed in 0.49s
```

Run command:
```
pytest tests/acceptance/test_sql_deep_extraction.py -q
```

## Regression Test Results

```
22 passed, 0 failed in 0.69s   (tests/unit/test_schema_extractors.py)
```

Zero regressions on existing schema extraction tests.

## Coverage Breakdown by Category

| Category | Files | Statements Extracted | Pass |
|---|---|---|---|
| JPA @Query (JPQL + native) | 10 | 13 | 10/10 |
| MyBatis @Select/@Insert/@Update/@Delete | 5 | 6 | 5/5 |
| JDBC PreparedStatement / JdbcTemplate | 5 | 5 | 5/5 |
| Raw DDL .sql (CREATE TABLE/INDEX, ALTER) | 3 | 12 | 3/3 |
| Raw DML .sql (INSERT/UPDATE/DELETE/SELECT) | 5 | 7 | 5/5 |
| entityManager.createQuery/createNativeQuery | 1 | 2 | 1/1 |
| JDBC dynamic string concat | 1 | 1 | 1/1 |
| **Total** | **30** | **46** | **30/30** |

## DML Types Found

| Type | Count |
|---|---|
| SELECT | 15 |
| INSERT | 3 |
| UPDATE | 14 |
| DELETE | 3 |
| CREATE | 7 (DDL) |
| ALTER | 5 (DDL) |

All required DML types (SELECT, INSERT, UPDATE, DELETE) present. ✓

## JPA @Query Files

Found in 10 of 10 JPA fixture files (gate: ≥5). ✓

Fixtures with JPA @Query extracted:
- `01_jpa_select_by_id.java` — `SELECT a FROM Artifact a WHERE a.id IN :ids`
- `02_jpa_select_by_workspace_kind.java` — SELECT with :wid, :kind, :externalIds
- `03_jpa_search_by_name.java` — 2 queries incl. LIKE CONCAT + ORDER BY
- `04_jpa_multiline_select.java` — Java text block with multi-join SELECT
- `05_jpa_native_update.java` — nativeQuery=true UPDATE with CAST(:logs AS jsonb)
- `06_jpa_native_multiline_update.java` — multi-column UPDATE text block
- `07_jpa_modifying_update.java` — 2 @Modifying UPDATE queries
- `08_jpa_prune_edges.java` — UPDATE ... WHERE lastSeen < :cutoff
- `09_jpa_dirty_nodes.java` — 3-table JOIN text block
- `10_jpa_mark_consumed.java` — UPDATE ... WHERE id IN :ids text block

## Confidence Tier Assignment

| Tier | Example | Verified |
|---|---|---|
| `literal_string` | Raw .sql files without placeholders | ✓ |
| `prepared_statement` | @Query with :param / JDBC with ? | ✓ |
| `dynamic_concat` | `sql = sql + " AND node_type = ?"` | ✓ |

## Feature Flag

- `SQL_DEEP_EXTRACTOR_ENABLED=true` (default): deep extraction runs; `_sql_deep_batch` attached.
- `SQL_DEEP_EXTRACTOR_ENABLED=false`: legacy `SchemaSqlExtractor` runs; no `_sql_deep_batch`.
- Both paths verified by `TestFeatureFlag` class in acceptance test.

## Example Extractions

### JPA @Query with text block (04_jpa_multiline_select.java)

```
EmbeddedSqlStatement(
  stmt_type='SELECT',
  tables=['node', 'artifactlink', 'artifact'],
  confidence_tier='prepared_statement',
  raw_sql='SELECT DISTINCT n FROM Node n JOIN ArtifactLink al ON al.nodeId = n.id ...',
  line_start=12
)
```

### Native UPDATE with JSONB cast (05_jpa_native_update.java)

```
EmbeddedSqlStatement(
  stmt_type='UPDATE',
  tables=['pipeline_jobs'],
  confidence_tier='prepared_statement',
  raw_sql='UPDATE pipeline_jobs SET progress_logs = CAST(:logs AS jsonb) WHERE id = :jobId'
)
```

### SELECT with CTE + window function (27_dql_select_complex.sql)

```
EmbeddedSqlStatement(
  stmt_type='SELECT',
  tables=['workspaces', 'nodes', 'edges', 'workspace_stats', 'ranked'],
  columns=['id', 'name', 'node_count', 'edge_count', 'rank_by_nodes'],
  confidence_tier='literal_string'
)
```

### Dynamic concat (30_jdbc_dynamic_concat.java)

```
EmbeddedSqlStatement(
  stmt_type='SELECT',
  tables=['nodes'],
  confidence_tier='dynamic_concat',
  raw_sql='SELECT id, name FROM nodes WHERE workspace_id = ?'
)
```
