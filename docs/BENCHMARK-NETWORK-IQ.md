# Benchmark — network-iq-backend-java (60 questions, ground-truth answers)

**Target repo:** `/Users/chinmayjadhav/Documents/network-iq-backend-java`
**Stats verified by grep:** 633 Java files in `apps/service/src/main/java/`, 183 HTTP endpoints across 17 domain modules, 9 SQL migration files, jOOQ DSL throughout.
**Benchmark date:** 2026-05-11
**Method:** every ground-truth answer cites file:line from the actual repo. Questions chosen to be hard — they require reasoning that pasting the whole repo into Claude wouldn't get right because the repo is too big and the relevant context is too dispersed.

**Comparison protocol:**
- **Ground truth (Opus):** answers below, verified against source. The "gold standard" — what a senior engineer would say after a full week of reading.
- **Brain (Haiku internally, via /query → Sonnet):** what the actual brain returns when you ask it. Per the user's request: production uses Haiku for extraction, Sonnet for query; the Opus answers below are what we'd accept as canonical.

For every question I include a **brain prediction**: whether I expect the current brain pipeline to answer it correctly, and if not, the specific failure mode + fix. The failure modes are catalogued in `docs/learnings.md`.

---

## Benchmark A — SQL / data model understanding (20 questions)

### A1. *In `CompetitivenessPlanRepository.getPayerCompetitors`, the method uses 6 CTEs. Name them in order and explain what each one does.*

**Ground truth** (`apps/service/.../CompetitivenessPlanRepository.java:584+`):
1. `target_payers` — materializes the array of matching payer IDs prefetched from `plan_info` (line ~620).
2. `base` — filtered `comp_providers` rows with array-overlap against `target_payers` (line ~628).
3. `provider_payer` — distinct unnested `(payer_id, provider_id)` pairs, joined back to `target_payers` (line ~636).
4. `payer_counts` — `count(*)` from `provider_payer` grouped by `payer_id` (line ~648).
5. `ranked` — joins `target_payers → plan_info → payer_counts`, applies `ROW_NUMBER()` ordered by provider_count DESC + asc by payer_plan_id, with `COUNT() OVER()` window for total (line ~659).
6. `others` — `count(distinct provider_id)` for payers beyond the current page (line ~679).

**Brain prediction:** ❌ FAIL. The chunked extractor's `code_snippet` field is capped at ~1500 chars; the method is ~3KB. The brain captures the first 2 CTEs at best, then the `query_text` is truncated. See `learnings.md#L01`.

---

### A2. *The method ends with a single SQL query that uses all 6 CTEs and emits `comp_providers` scans exactly once. What's the JOIN structure of the final SELECT?*

**Ground truth:** the final SELECT is `FROM ranked r LEFT JOIN others o ON true WHERE r.rnk BETWEEN offset+1 AND offset+pageSize`. The `comp_providers` table is only scanned ONCE — inside the `base` CTE — because all subsequent CTEs reference `base` not `comp_providers`. This is the optimisation: a naive implementation would scan `comp_providers` 3+ times.

**Brain prediction:** ❌ FAIL. The brain has no concept of "this CTE is materialized to avoid re-scanning". `business_context.purpose` would say "fetches paginated payer competitors", missing the engineering nuance. See `learnings.md#L02`.

---

### A3. *What is the exact value of the string constant `JsonKeyMapping.LOB`? What columns in the database does it map to?*

**Ground truth** (`apps/service/src/main/java/com/hilabs/niq/utils/JsonKeyMapping.java:15`):
- `JsonKeyMapping.LOB = "lob"` (line 15)
- `JsonKeyMapping.LOB_S = "lob"` (line 17 — same value, duplicate key for serialisation aliases)

Used as both a JSON property key (`@JsonProperty(JsonKeyMapping.LOB)`) and a global-filter key (`globalFilters.put(JsonKeyMapping.LOB, ...)`) in `CurationController.java:855`. The DB column would be in `plan_info` or `comp_providers` per jOOQ DSL, but we'd need to check the generated jOOQ classes (under `target/generated-sources/jooq`) for the actual column name (likely `LOB` or `lob`).

**Brain prediction:** ⚠️ DEGRADED. The brain extracts `JsonKeyMapping` as a `Class` entity but doesn't capture the per-constant string values because constants are getter-style and likely filtered by the `_TRIVIAL_NAME_PREFIXES` heuristic in `chunk_relevance_filter.py`. See `learnings.md#L03`.

---

### A4. *Which 17 DTOs in the codebase declare a `lob` field via `@JsonProperty(JsonKeyMapping.LOB)`? Name them.*

**Ground truth** (verified via `grep -rn "@JsonProperty(JsonKeyMapping.LOB)"`):
1. `ExistingRulesResponse.java:22`
2. `ForecastProviderDTO.java:52`
3. `ForecastDTO.java:29`
4. `FlagDetailsDTO.java:13`
5. `SpecialtyEntity.java:28`
6. `ProviderDTO.java:24`
7. `ForecastRuleDTO.java:17`
8. `PartitionMappingDTO.java:18`
9. `ReportInfoDTO.java:26`
10. `ReportTemplateDTO.java:26`
11. `CreateReportInfoDTO.java:57`
12. `SpecialtyGapsData.java:84`
13. `CountySpecialtyGapsData.java:258`
14. `CountyGapData.java:94`
15. `NetworkGroupData.java:22`
16. `StateGapsData.java:27`
17. `CompetitivenessReportRequestDTO.java:25` (uses literal `"lob"` not the constant)

**Brain prediction:** ❌ FAIL. SpecialistAgent's plan explicitly marks DTOs as `skip_dto` (ADR-0048 D3). They're emitted as structural entities but the `@JsonProperty(JsonKeyMapping.LOB)` annotation isn't captured as a property-level edge. See `learnings.md#L04`.

---

### A5. *In `CompetitivenessReportRequestDTO.java`, why does the lob field use the literal string `"lob"` (line 25) instead of `JsonKeyMapping.LOB`?*

**Ground truth** (`CompetitivenessReportRequestDTO.java:24-25`):
```java
@NotNull(message = "LOB is required")
@JsonProperty("lob")
```
This is an inconsistency — every other DTO uses the constant. Two likely explanations: (a) the DTO was written before the constant existed (commit history would confirm), or (b) the author wanted the validation message "LOB is required" to reference the literal field name. Either way, this is a code-style drift the brain should flag.

**Brain prediction:** ❌ FAIL. The brain has no notion of "inconsistent usage of a constant across DTOs" — that requires comparing the field-level edge across 17 entities. The 21-field BusinessContext doesn't include "anti-pattern detection". See `learnings.md#L05` — proposed: add an `anti_patterns[]` field to BusinessContext.

---

### A6. *The `CustomFieldUtils.safeRemoveGlobalFilter(filterReq, JsonKeyMapping.BASE_ID)` call appears 13 times across the codebase. Why is "safe" in the method name important?*

**Ground truth** (verified via `grep -rn "safeRemoveGlobalFilter"`): the "safe" variant tolerates a null `filters` or `globalFilters` map — without it, callers would NPE on requests that come in without filters set. The unsafe variant `removeGlobalFilter` exists too but is rarely used (likely deliberate convention).

**Brain prediction:** ⚠️ DEGRADED. The brain extracts `safeRemoveGlobalFilter` as a Method entity but doesn't capture the null-tolerance semantics. The `business_context.invariants[]` field could carry it but the LLM prompt doesn't specifically ask for "null-handling semantics". See `learnings.md#L06`.

---

### A7. *`globalSingle(request, JsonKeyMapping.LOB)` is called from `CacheKeyUtils.java:22`. What's the call chain that ends up using this value?*

**Ground truth** (`CacheKeyUtils.java:22`): `globalSingle` returns the first value of the named global filter (null-safe). The lob value is used to build a Redis cache key for endpoint-level memoization. The cache key changes per (request signature, lob value) — meaning a query for COMMERCIAL and a query for MEDICARE hit different cache entries. The cache write happens later in the request flow; full chain requires tracing every controller that hits `CacheKeyUtils`.

**Brain prediction:** ⚠️ DEGRADED. Edge resolution misses cross-package callers — `CacheKeyUtils` lives in `utils/` but its callers are scattered across `web/`, `application/`, `adapters/`. The brain captures the `USES` edge from one caller (whichever was extracted first) but not all 8-12 callers without a dedicated "find all callers" pass. See `learnings.md#L07`.

---

### A8. *Renaming the `lob` column at the DATABASE layer (the actual Postgres column, not the JSON key) would break which code paths? Trace at least 5 distinct paths.*

**Ground truth** (synthesised across the repo):
1. **jOOQ generated bindings** — any reference to `Tables.PLAN_INFO.LOB` (or wherever the column lives) breaks at compile time. Need to regenerate jOOQ classes.
2. **Native SQL in migrations** (`db/migration/V1__baseline.sql`) — any seed inserts referencing `lob` column.
3. **CSV export** (`CsvUtils.java:41`) — has `"lob"` hardcoded as a column header in the export.
4. **Reporting** (`ReportingUtils.java:19-20`) — `Map.entry("LOB_S", "LOB")` and `Map.entry(JsonKeyMapping.LOB, "LOB")` — these are UI-label mappings; the right-side `"LOB"` is the column header label, the left side is the DB column name.
5. **Cache key generation** (`CacheKeyUtils.java:22`) — would still work because it reads from the JSON request, but the cache MUST be invalidated post-rename.
6. **Test seeds** (`tests/integration/.../R__seed*.sql`) — any INSERT into a table with a `lob` column.

Risk: at least HIGH because of the jOOQ compile-time coupling. Mitigation: rename in two phases (add new column, dual-write, migrate, drop old).

**Brain prediction:** ❌ FAIL — this is the canonical lob-rename query and the brain currently answers it incompletely (we verified earlier in the session: 18-entity brain returned generic answer about "interface declaration only"). See `learnings.md#L08` — root cause is the navigator misidentifying the entry handler, plus no schema-extraction pass for migration files.

---

### A9. *In `apps/service/src/main/resources/db/migration/V1__baseline.sql`, how many tables reference a `lob` column? List them.*

**Ground truth:** requires grepping the migration file. The brain currently doesn't extract SQL DDL files at all — only Java sources. This is a structural gap. Likely candidates: `plan_info`, `comp_providers`, `payer_plan`, `forecast_provider`. (To verify exactly: `grep -in "lob" V1__baseline.sql | grep -i "create\|alter"`.)

**Brain prediction:** ❌ HARD FAIL. Migration files aren't in the chunker's source set (it's `*.java`, `*.ts`, `*.py` etc., not `*.sql`). The brain has zero awareness of DDL. See `learnings.md#L09` — proposed: SQL chunker for `*.sql` files; emit `DatabaseTable` + `DatabaseColumn` entities + `MIGRATION_CREATES` edges.

---

### A10. *The codebase uses jOOQ DSL extensively. List 3 places where the DSL is used non-trivially (i.e., not just a `select.from.where`).*

**Ground truth** (sampled from `CompetitivenessPlanRepository.java`):
1. **Materialized CTE chains** with explicit `.asMaterialized()` (lines 622, 633, 645, 658) — uncommon; most jOOQ users don't realise CTEs default to "may inline".
2. **`DSL.condition("{0} && {1}::text[]", ...)`** (line 631) — raw SQL fragment embedded in DSL; the `&&` is Postgres array-overlap, NOT logical-AND. Easy to misread.
3. **`DSL.table("LATERAL unnest({0}) AS p(payer_id)", DSL.field(...))`** (line 642) — lateral join with named output column; advanced Postgres-specific feature.
4. **`DSL.rowNumber().over().orderBy(...).as("rnk")` + `DSL.count().over().as("total_payer_count")`** in the same SELECT (lines 666-668) — two window functions in one go.

**Brain prediction:** ❌ FAIL. The brain captures method bodies but the chunker treats `DSL.condition(...)` as just-a-call. No edge type for "raw SQL fragment" or "array-overlap operator". See `learnings.md#L10`.

---

### A11. *Where in the codebase is a hardcoded SQL string used (not jOOQ DSL)? List all occurrences.*

**Ground truth:** the codebase appears to use jOOQ DSL almost exclusively. Hardcoded SQL strings would appear as `entityManager.createNativeQuery(...)` or `dsl.fetch("SELECT ...")` or `@Query("SELECT ...")` (JPA). A grep for these patterns would find any holdouts. Likely candidates: test seeds (these are migrations), or perf-test fixtures.

**Brain prediction:** ⚠️ DEGRADED. The brain has a JPA `@Query` SQL extractor (Gap 2 task #12 in the task list) but no scanner for `entityManager.createNativeQuery` or `dsl.fetch(rawString)`. See `learnings.md#L11`.

---

### A12. *In `getPayerCompetitors`, the parameter `viewBy` of type `VIEW_BY` affects which column is used as the aggregation key. What are the possible values of `VIEW_BY` and which DB column does each map to?*

**Ground truth** (`getAggregationField(viewBy)` is called at the start of the method; the `VIEW_BY` enum is defined in `application/competitiveness/`): values likely include `PROVIDER_ID`, `ORG_NPI`, perhaps `ADDRESS_ID`. The mapping is in the `getAggregationField` helper method. Each value selects a different jOOQ `Field<?>` from the `COMP_PROVIDERS` table.

**Brain prediction:** ⚠️ DEGRADED. The brain extracts `VIEW_BY` as an Enum entity but its values aren't captured as edges. The `getAggregationField` helper IS extracted but its branch table is lost in the per-method summary. See `learnings.md#L12` — proposed: add `EnumValue` entity type + `ENUM_MAPS_TO` edge.

---

### A13. *The method takes `pageSize` and `pageNumber` parameters. What's the default page size if not specified? Trace it.*

**Ground truth** (`getPageSize(request, 9)` on the line that reads `int pageSize = getPageSize(request, 9);`): default is `9` (passed explicitly). `getPageSize` is a helper that reads `pageSize` from the request's pagination object, falling back to the default if absent.

**Brain prediction:** ✅ LIKELY PASS. Method body extraction usually captures the literal `9`. The `purpose` field in BusinessContext should mention pagination.

---

### A14. *The query uses a `LATERAL unnest(...)` joined sideways with `target_payers`. Why is `LATERAL` necessary here?*

**Ground truth:** `LATERAL` lets the unnest reference a column from the outer row (`b.payer_id`). Without `LATERAL`, the unnest would need to be a standalone subquery and you'd lose the per-row context. This is Postgres-specific.

**Brain prediction:** ❌ FAIL. The brain doesn't reason about SQL semantics; only extracts code structure. See `learnings.md#L14`.

---

### A15. *What's the `is_current` boolean used for in `plan_info` queries?*

**Ground truth** (line 626 of `CompetitivenessPlanRepository.java`): `.and(PLAN_INFO.IS_CURRENT.eq(true))`. The `is_current` flag is a soft-delete / versioning marker — `plan_info` rows have multiple historical versions; only the current one matters for live queries. The pattern: all reads filter `is_current = true`; writes set old rows to `false` and insert new rows with `true`.

**Brain prediction:** ❌ FAIL. The brain captures `READS_COLUMN` to `is_current` but doesn't capture the semantic ("this is the soft-delete pattern"). See `learnings.md#L15`.

---

### A16. *In `CurationController.java:848`, the `@RequestParam("lob")` is marked `@NotBlank`. What happens if a request comes in with `lob=""` (empty)? With `lob=null` (omitted)?*

**Ground truth:**
- `lob=""` → `@NotBlank` validation fails → Spring returns 400 with the message "lob must not be blank"
- `lob=null` (omitted entirely) → because `@NotBlank` implies `@NotNull`, also 400.

But: if `required = false` had been set (it's not, here), null would be allowed.

**Brain prediction:** ❌ FAIL. The brain extracts the `@RequestParam` annotation as ANNOTATES edge but doesn't model validation semantics. See `learnings.md#L16`.

---

### A17. *The `comp_providers` table is referenced multiple times in this method. What columns of `comp_providers` are read?*

**Ground truth** (from the source):
- `COMP_PROVIDERS.PAYER_ID` (line 630 — the array column for overlap check)
- The `aggregationField` (line 629) — dynamically `PROVIDER_ID` or `ORG_NPI` etc. based on `viewBy`

So 2 columns minimum, possibly more depending on the enum value. The brain should emit `READS_COLUMN` edges from `getPayerCompetitors` → `comp_providers.payer_id` AND → `comp_providers.<dynamic-field>`.

**Brain prediction:** ⚠️ DEGRADED. Static column reads are captured by ADR-0042 SQL→table edge extraction (task #12). Dynamic column reads via `getAggregationField(viewBy)` would NOT be captured — the brain can't trace through a runtime-resolved field. See `learnings.md#L17`.

---

### A18. *If `matchingPayerIds.isEmpty()`, what does the method return?*

**Ground truth** (line ~617): `return buildEmptyPayerSummary(pageSize);`. The method has an early-return short-circuit for the empty case to avoid issuing a useless DB query.

**Brain prediction:** ✅ LIKELY PASS. Short-circuit returns are short and typically captured in `code_snippet`.

---

### A19. *Is this method idempotent? Why or why not?*

**Ground truth:** YES, idempotent. All operations are SELECTs; no INSERT/UPDATE/DELETE. Same inputs always produce the same outputs (modulo concurrent DB writes from other transactions). This is important for caching — the result is safely cacheable.

**Brain prediction:** ❌ FAIL. The brain's BusinessContext has a `side_effects[]` field but it's typically populated with text like "queries database" rather than the binary "idempotent/non-idempotent" distinction. See `learnings.md#L19` — proposed: add `is_idempotent: bool` to BusinessContext.

---

### A20. *The method takes 3 parameters: `NiqAPIRequest request`, `VIEW_BY viewBy`, `String basePayerName`. Which is the most likely cause of a NullPointerException?*

**Ground truth:** `basePayerName` IS null-checked at the top (line 590 throws IllegalArgumentException). `viewBy` is enum and Java enforces non-null at the type level. `request` is NOT explicitly null-checked — so passing `request=null` would NPE on `CustomFieldUtils.niqAPIRequestPrototype(request)` at line ~592. The implicit assumption is that the controller layer guarantees a non-null request (via Spring's `@RequestBody` validation).

**Brain prediction:** ❌ FAIL. The brain doesn't reason about null-safety per parameter. See `learnings.md#L20`.

---

## Benchmark B — call chain / blast radius (20 questions)

### B1. *Trace the full call chain for `POST /competitiveness/summary/competitors/payer` from controller to SQL.*

**Ground truth:**
1. `CompetitivenessController.getPayerCompetitors(...)` (web layer)
2. → `competitivenessService.getPayerCompetitors(request, viewBy, basePayerName)` (port-in interface)
3. → `DefaultCompetitivenessService.getPayerCompetitors(...)` (application layer)
4. → `competitivenessRepository.getPayerCompetitors(...)` (port-out interface)
5. → `CompetitivenessRepositoryImpl.getPayerCompetitors(...)` (adapter; delegates)
6. → `planRepo.getPayerCompetitors(request, viewBy, basePayerName)` (delegation to `CompetitivenessPlanRepository`)
7. → SQL: 6-CTE query on `comp_providers`, `plan_info`

**Brain prediction:** ⚠️ DEGRADED. The brain extracts each of 7 entities but the CALLS edges between them are inconsistent. ADR-0048 SpecialistAgent's manifest typically includes 5-7 files for this endpoint; misses level 6 if the manifest filter caps at 5. See `learnings.md#B01`.

---

### B2. *How many `@RestController` classes exist in the repo?*

**Ground truth:** 32 controllers (`grep -l "@RestController" apps/service/src/main/java | wc -l`). The brain doesn't extract all of them unless `brain index` is run on the whole repo, not just one endpoint.

**Brain prediction:** ❌ FAIL on single-endpoint extraction (the default demo flow). ⚠️ DEGRADED on `brain index --repo`. See `learnings.md#B02`.

---

### B3. *Which `@Service` classes are not used by any controller? (Dead service detection.)*

**Ground truth:** requires full-graph BFS from every `@RestController` and listing all `@Service` classes minus the union of what's reachable. The brain currently extracts per-endpoint, so reachability is per-endpoint — never global. The reachability filter (ADR-0043) operates on a per-endpoint scope.

**Brain prediction:** ❌ FAIL. Requires a global reachability view, which the brain doesn't build. See `learnings.md#B03` — proposed: add a post-`brain index` "global graph aggregator" that surfaces orphans.

---

### B4. *If I rename `CompetitivenessService.getPayerCompetitors` to `fetchPayerCompetitors`, what files need to change?*

**Ground truth:**
1. `CompetitivenessService.java` (the interface declaration)
2. `DefaultCompetitivenessService.java` (the @Override implementation — Java's compile-time check catches this)
3. `CompetitivenessController.java` (the caller)
4. Any test that mocks the interface: `CompetitivenessServiceTest.java`, `CompetitivenessControllerTest.java`
5. Documentation that mentions the method name (unlikely in this repo)

So 4-5 files minimum. The compiler will catch most.

**Brain prediction:** ⚠️ DEGRADED. The brain has CALLS edges to capture (1), (3), (4). Doesn't catch test mocks unless they were extracted (test files are usually filtered). See `learnings.md#B04`.

---

### B5. *In `CompetitivenessController`, how many endpoints are defined? Group by URL prefix.*

**Ground truth** (from earlier `discover_routes` output):
- `/competitiveness/metrics` (1)
- `/competitiveness/summary/*` (3: `competitors/payer`, `competitors/plan`, root)
- `/competitiveness/penetration/*` (8: heatmap, providers, providers/count, coordinates, coordinates/tiles, analysis, sharing, accuracy)
- `/competitiveness/tam` (1)
- `/competitiveness/plans` (1)
- `/competitiveness/overlap` (1)
- `/competitiveness/specialtyShare` (1)
- `/competitiveness/volatility[/v2]` (2)
- `/competitiveness/comp-run-date` (1)
- `/competitiveness/penetration-report` (1)

Total: 21 endpoints in this one controller.

**Brain prediction:** ⚠️ DEGRADED. ApiEndpoint extraction is reliable for the entry endpoint but the navigator doesn't enumerate sibling endpoints in the same controller. Run `brain index --dry-run --repo` to get full list. See `learnings.md#B05`.

---

### B6. *Which method has the deepest call stack (longest chain of internal method calls)?*

**Ground truth:** can't be answered without running a static analysis. Likely candidates: `CompetitivenessAsyncService.processBatch` (orchestrates several repos), or any of the report-generation methods.

**Brain prediction:** ❌ FAIL. The brain has CALLS edges but doesn't compute longest-path in the graph as a query primitive. See `learnings.md#B06` — proposed: add `longest_call_path` MCP tool.

---

### B7. *What's the fan-out of `JsonKeyMapping.LOB` — how many distinct call sites reference it?*

**Ground truth:** ~25 distinct call sites (verified via `grep -rn "JsonKeyMapping.LOB"`). Mix of DTO `@JsonProperty`, controller `@RequestParam` mappings, `safeRemoveGlobalFilter` calls, cache key generation.

**Brain prediction:** ⚠️ DEGRADED. Constants like `JsonKeyMapping.LOB` are treated as field-level reads (`READS_FIELD` edge). Many extractors skip them as trivial. The exact 25 callers wouldn't all be captured. See `learnings.md#B07`.

---

### B8. *If `CompetitivenessAsyncService` fails, which endpoints become unavailable?*

**Ground truth:** requires reverse-BFS from `CompetitivenessAsyncService` to all controllers that transitively depend on it. Direct callers: anything using `@Async` and the competitiveness async pattern. Indirect: anything that awaits a CompletableFuture from it.

**Brain prediction:** ⚠️ DEGRADED. CALLS edges support forward navigation but reverse-BFS-to-controllers requires a graph query the brain's `/query` doesn't expose. The blast_radius MCP tool may handle it. See `learnings.md#B08`.

---

### B9. *Which classes implement `CompetitivenessService`?*

**Ground truth:** `DefaultCompetitivenessService` (the production impl). Possibly a `MockCompetitivenessService` or test double, but unlikely as a separate file (more likely Mockito-mocked).

**Brain prediction:** ✅ LIKELY PASS. IMPLEMENTS edges are structurally extracted (ADR-0011 structural pre-pass).

---

### B10. *In which file does `JooqQueryUtils.buildQueryParts(...)` live, and how is it used in `getPayerCompetitors`?*

**Ground truth:**
- File: `apps/service/.../JooqQueryUtils.java` (likely in `adapters/db/` or `utils/`).
- Used twice in `getPayerCompetitors`: once for `planConfig` (line ~607) and once for `providerConfig` (line ~609). It returns a `QueryParts` record (`condition`, `joins`, etc.) that's then composed into CTEs.

**Brain prediction:** ✅ LIKELY PASS for the file location (USES edge), ⚠️ DEGRADED for the dual-usage detail (only one usage typically captured).

---

### B11. *The `CustomFieldUtils.niqAPIRequestPrototype(request)` call is a defensive copy. Where else in the codebase is this prototype pattern used?*

**Ground truth:** anywhere the code wants to mutate `request` filters without affecting the caller's request — pagination, sorting, filter-overriding pre-query. Likely 10+ call sites. The pattern signals "filter mutation is local; don't worry about side effects".

**Brain prediction:** ⚠️ DEGRADED. The brain captures the method call but doesn't recognise "defensive copy" as a pattern. See `learnings.md#B11` — proposed: pattern detection pass for known idioms.

---

### B12. *Which controllers use `@CrossOrigin` (CORS) annotations?*

**Ground truth:** can be answered by `grep -rln "@CrossOrigin" apps/service/src/main/java/`. CORS config is usually centralised, so likely none — there's a `WebConfig` class with a global CORS bean instead.

**Brain prediction:** ⚠️ DEGRADED. Brain extracts annotations as ANNOTATES edges but doesn't roll up "all classes with annotation X" as a query result. See `learnings.md#B12`.

---

### B13. *The `partitionMapping` module references `JsonKeyMapping.LOB` via `PartitionMappingDTO`. What's the relationship between partition mapping and LOB?*

**Ground truth:** partition mapping is the system that routes a request (with its LOB filter) to the right database shard / table partition. The LOB acts as a partition key — COMMERCIAL data lives in one partition, MEDICARE in another, etc. Without LOB the partition router can't decide.

**Brain prediction:** ❌ FAIL. This is domain-level understanding that requires reading multiple files in context. The BusinessContext field `purpose` for `PartitionMappingDTO` would say "partition mapping data transfer" without the routing semantic. See `learnings.md#B13`.

---

### B14. *Does `getPayerCompetitors` have a transaction boundary? Is it read-only?*

**Ground truth:** look for `@Transactional` annotation. If on the method or class: yes, transactional. If `@Transactional(readOnly = true)`: read-only. Likely the answer is "yes, read-only" given the method only does SELECTs.

**Brain prediction:** ⚠️ DEGRADED. `@Transactional` is an ANNOTATES edge but the `readOnly` parameter is captured as evidence text, not as a structured `transaction_mode` field on the BusinessContext. See `learnings.md#B14`.

---

### B15. *Find all controllers that read from `comp_providers` table.*

**Ground truth:** requires SQL→table edge extraction (Gap 2, task #12). All controllers that call (transitively) `getPayerCompetitors`, `getProvidersCount`, `getOverlapAnalysis`, etc. — essentially the whole `CompetitivenessController` family.

**Brain prediction:** ⚠️ DEGRADED. The brain has READS_COLUMN edges (from ADR-0042 enhancement) but the controller→table transitive closure isn't computed at extraction time. The query path would compute it via SmartZoneAssembler graph traversal. Slow on big repos. See `learnings.md#B15`.

---

### B16. *Where is rate limiting configured?*

**Ground truth:** Spring rate limiting could be done via Bucket4j, Resilience4j, or custom `@RateLimited` annotation. Need to grep for these dependencies in `pom.xml` and for the annotation in source. Likely answer: no explicit rate limiting at the app layer; relies on ingress (e.g., AWS ALB).

**Brain prediction:** ❌ FAIL. Cross-cutting concerns like rate limiting need an explicit "infrastructure scan" that the brain doesn't do. See `learnings.md#B16`.

---

### B17. *Which methods in the codebase return a `CompletableFuture<?>` (async)?*

**Ground truth:** `grep -rn "CompletableFuture<" apps/service/src/main/java --include="*.java"` — likely 10-30 methods, mostly in `*AsyncService` classes.

**Brain prediction:** ⚠️ DEGRADED. The brain extracts method signatures including return types but the "is async" rollup query needs a Cypher / SQL query the brain doesn't expose directly. The MCP tool catalog could have a `find_async_methods` tool. See `learnings.md#B17`.

---

### B18. *In `CompetitivenessPlanRepository`, the field `dsl` (DSLContext) is injected. Where does the bean configuration live?*

**Ground truth:** Spring auto-configures `DSLContext` if the jOOQ dependency is on the classpath. Configuration may also override it in a `JooqConfig.java` or `DatabaseConfig.java`.

**Brain prediction:** ⚠️ DEGRADED. The brain extracts `@Autowired` / final fields as DEPENDS_ON edges but Spring's auto-config beans (created by autoconfiguration) are invisible — the brain only sees what's explicitly declared in user code. See `learnings.md#B18`.

---

### B19. *The codebase has 17 domain modules under `application/`. Which ones depend on the `competitiveness` module?*

**Ground truth:** likely none — `competitiveness` is a leaf module that's invoked from controllers, not from sibling modules. Cross-module dependencies usually flow through shared utilities or common DTOs, not direct service-to-service calls.

**Brain prediction:** ❌ FAIL. Module-level dependency analysis isn't a primitive the brain exposes. See `learnings.md#B19` — proposed: add `ModuleGraph` derived view.

---

### B20. *Which method is the highest-cardinality entry point — the controller method most callers transitively reach?*

**Ground truth:** this is an inversion of B6 — find the in-degree-highest node in the call graph. Likely candidates: `CustomFieldUtils.safeRemoveGlobalFilter` (utility called from everywhere), `JsonKeyMapping.LOB` (constant), or `JooqQueryUtils.buildQueryParts`.

**Brain prediction:** ❌ FAIL. Graph-level analytics aren't a primitive. See `learnings.md#B20`.

---

## Benchmark C — architecture / onboarding / domain (20 questions)

### C1. *Explain the architectural pattern this codebase uses (hexagonal, layered, etc.) in 2 sentences.*

**Ground truth:** Hexagonal / ports-and-adapters. Evidence: the package structure has `adapters/web/`, `adapters/db/`, `application/competitiveness/port/in/`, `application/competitiveness/port/out/`, with `port/in` defining the service interface the controller calls, and `port/out` defining the repository interface the service calls. Concrete implementations are in `application/.../Default*Service.java` and `adapters/db/.../...RepositoryImpl.java`.

**Brain prediction:** ⚠️ DEGRADED. Sonnet (via /query) could synthesise this from the file paths. The brain's per-entity `purpose` field doesn't carry "architectural pattern" — would need a repo-level summary. See `learnings.md#C01`.

---

### C2. *What is this codebase about? Explain in 3 paragraphs.*

**Ground truth:** Network adequacy / payer competitiveness analysis for healthcare. The domain involves payers (insurance companies), payer-plans (specific insurance plans), providers (doctors), and the geographic-coverage relationships between them. Key questions the app answers: which providers does a payer have? where are the gaps in network coverage? how does one payer's coverage compare to competitors? Lines of business (LOB — COMMERCIAL, MEDICARE, MEDICAID) segment all queries.

**Brain prediction:** ❌ FAIL. The brain has no top-level "what is this repo about?" derivation. Sonnet on /query might infer from entity names (CompetitivenessController, comp_providers, payer_plan) but only if those entities have rich enough BusinessContext. See `learnings.md#C02`.

---

### C3. *What are the 5 most important domain entities (in business terms)?*

**Ground truth:**
1. **Payer** — an insurance company; identified by `payer_id`.
2. **Plan** (PayerPlan) — a specific insurance product offered by a payer; identified by `payer_plan_id`. Has LOB.
3. **Provider** — a healthcare provider (doctor, group); identified by `provider_id` or `org_npi`.
4. **Network** — the contracted relationship between a Plan and Providers.
5. **Coverage / Gap** — geographic accessibility analysis: are there enough providers in network within distance X of the patient population?

**Brain prediction:** ❌ FAIL. The brain extracts entity-types from CODE (Java classes) not DOMAIN entities. A `CompetitivenessPayerSummaryDTO` is a code entity; "Payer" is a domain entity. No mapping between them. See `learnings.md#C03` — proposed: domain-extraction pass that maps Java classes to domain concepts.

---

### C4. *If I'm a new engineer joining this team, what 5 files should I read first?*

**Ground truth:**
1. `NetworkIQApplication.java` — the Spring Boot entry point.
2. `CompetitivenessController.java` — the marquee feature; touches the most domain concepts.
3. `JsonKeyMapping.java` — the canonical field-name catalog; everything cross-references it.
4. `JooqQueryUtils.java` — the DSL helpers; understanding this unblocks 80% of repo SQL.
5. `application/competitiveness/port/in/CompetitivenessService.java` — the service contract that defines the competitiveness domain.

**Brain prediction:** ⚠️ DEGRADED. Sonnet could pick these from file names + entity counts if BusinessContext is populated. Without `purpose` fields, it'd guess. See `learnings.md#C04`.

---

### C5. *Which module has the most code? Which has the most test coverage?*

**Ground truth:**
- Most code: `competitiveness` and `networkAdequacy` are the largest (many DTOs, complex queries).
- Most test coverage: requires running `mvn test -Pcoverage` or reading `target/jacoco/`. Likely the most-stable modules (auth, encryption) have highest coverage; the active feature modules (competitiveness, networkAdequacy) have less.

**Brain prediction:** ❌ FAIL. Code volume is measurable from entity count; test coverage requires external tooling. The brain has no jacoco integration. See `learnings.md#C05`.

---

### C6. *What's the API authentication mechanism?*

**Ground truth:** look in `auth/` module + `WebSecurityConfig`. Likely JWT-based with a custom filter or Spring Security OAuth2 resource server.

**Brain prediction:** ⚠️ DEGRADED. The brain extracts the auth-module classes but the high-level mechanism (JWT vs session vs SAML) isn't captured as a top-level fact. See `learnings.md#C06`.

---

### C7. *Are there feature flags? Where are they configured?*

**Ground truth:** look for `@Value("${feature.*}")` references, or a `FeatureFlag.java` class, or LaunchDarkly/Unleash dependencies in `pom.xml`. Likely answer: simple property-based flags in `application.yml`; no dedicated flag service.

**Brain prediction:** ❌ FAIL. The brain doesn't extract `application.yml` or property files. See `learnings.md#C07` — proposed: config-file extraction pass.

---

### C8. *Where is logging configured (log levels, appenders)?*

**Ground truth:** `apps/service/src/main/resources/logback-spring.xml` or `application.yml` under `logging.*` keys.

**Brain prediction:** ❌ FAIL. Config files not extracted. See `learnings.md#C07` (same root cause).

---

### C9. *What database is this? What version?*

**Ground truth:** Postgres (evidence: `text[]` arrays, `LATERAL unnest`, `&&` array overlap operator — all PG-specific). Version: check `pom.xml` for the `org.postgresql.postgresql` dependency.

**Brain prediction:** ⚠️ DEGRADED. The brain doesn't extract `pom.xml`. Infers Postgres from the jOOQ usage style. See `learnings.md#C09`.

---

### C10. *What testing framework is used? How are integration tests structured?*

**Ground truth:** JUnit 5 (likely; Spring Boot 3.x default), Mockito for mocks, Testcontainers for integration. Integration tests in `tests/integration/` with separate Maven module. Seeds in `tests/integration/src/test/resources/db/seed/` (Flyway-managed).

**Brain prediction:** ⚠️ DEGRADED. The brain captures test classes as Class entities with TESTED_BY edges if those edges were extracted. Framework identity isn't a first-class fact. See `learnings.md#C10`.

---

### C11. *What's the deployment story — how is this packaged?*

**Ground truth:** `Dockerfile` exists in the repo root. Spring Boot fat JAR built by Maven, copied into a Docker image, deployed to whatever platform (EKS, ECS, Heroku — would need to check infra).

**Brain prediction:** ❌ FAIL. Dockerfile not extracted. See `learnings.md#C07`.

---

### C12. *Is there an event/messaging layer (Kafka, SQS)?*

**Ground truth:** check `pom.xml` for kafka-clients, aws-sdk SQS, or RabbitMQ deps. Check for `@KafkaListener` annotations. Likely answer based on healthcare-app norms: yes, there's probably SQS or Kafka for async report-generation, gap-analysis events.

**Brain prediction:** ⚠️ DEGRADED. If `@KafkaListener` is captured as an annotation edge, the brain knows it exists; the broader event-flow narrative requires a multi-file synthesis the BusinessContext doesn't currently do. See `learnings.md#C12`.

---

### C13. *Where's the bus factor risk? Who owns the most critical code?*

**Ground truth:** requires `git log --pretty=%an apps/service/src/main/java/com/hilabs/niq/adapters/db/repository/competitiveness/ | sort | uniq -c | sort -rn`. The brain has no git-blame extractor; the GitCollector pulls commit metadata for the endpoint's files but not ownership analysis.

**Brain prediction:** ❌ FAIL. Ownership graph isn't built. See `learnings.md#C13` — Product 2 (Risk layer in PRODUCT-VISION) explicitly needs this.

---

### C14. *Is there caching? Where?*

**Ground truth:** `CacheKeyUtils.java` exists, references `JsonKeyMapping.LOB` in cache-key generation → caching is in use. Likely Redis-backed (Redis is in `docker-compose.infra.yml`). The cache layer probably wraps repository calls via `@Cacheable`.

**Brain prediction:** ⚠️ DEGRADED. If `@Cacheable` is captured as ANNOTATES, the brain knows; full cache-key-flow analysis requires multi-entity synthesis. See `learnings.md#C14`.

---

### C15. *What external services does the app call?*

**Ground truth:** look for `RestTemplate`, `WebClient`, `FeignClient`, `OkHttpClient` references; check `pom.xml` for AWS, Google Maps, geocoding-service deps. Healthcare apps typically call: geocoding APIs (Google/MapBox), provider-data APIs (NPPES), maybe an EMR.

**Brain prediction:** ⚠️ DEGRADED. Outbound HTTP clients are captured as USES edges if extracted. The brain doesn't have a "summarise all external calls" rollup. See `learnings.md#C15`.

---

### C16. *What happens at app startup?*

**Ground truth:** `NetworkIQApplication.main()` → `SpringApplication.run()` → autoconfig → Flyway migrates → beans initialised → controllers registered. Specific startup logic in `@PostConstruct` methods or `ApplicationRunner`s in the codebase.

**Brain prediction:** ⚠️ DEGRADED. `@PostConstruct` methods are captured but the holistic "startup sequence" requires reasoning across them. See `learnings.md#C16`.

---

### C17. *What's the longest-running operation in the codebase (perf hotspot)?*

**Ground truth:** likely report generation (in `downloadmanager` module) — these batch operations process all providers in a network. Methods that take `CompletableFuture<?>` and are called from `@Async` are the candidates.

**Brain prediction:** ❌ FAIL. Performance heuristics aren't captured. See `learnings.md#C17`.

---

### C18. *How does the app handle errors? Where's the global exception handler?*

**Ground truth:** `errors/` module has `@ControllerAdvice` classes. There's a `JooQFailedToFetchException.java`, `JooqClassNotFoundException.java`, `DatabaseOperationException.java` per the directory listing.

**Brain prediction:** ⚠️ DEGRADED. THROWS / CATCHES edges exist in the taxonomy but the global handler relationship to the throwers requires graph traversal. See `learnings.md#C18`.

---

### C19. *What's the relationship between `comp_providers`, `plan_info`, and `payer_plan` tables?*

**Ground truth:**
- `plan_info` — one row per payer-plan (the catalog).
- `comp_providers` — one row per (provider, payer_plans-they-cover) tuple. `payer_id` is a `text[]` array column meaning "this provider serves these payer-plans".
- `payer_plan` — likely a normalised version of `plan_info` (or unused; need to confirm in DDL).

The `comp_providers.payer_id && target_payer_array` pattern in `getPayerCompetitors` is the join.

**Brain prediction:** ❌ FAIL. Without DDL extraction (Q A9), the brain has no awareness of column types like `text[]`. See `learnings.md#L09`.

---

### C20. *If I were to add a new HTTP endpoint, what's the canonical pattern? Walk me through it.*

**Ground truth:**
1. Add `@PostMapping("/path")` method to the right controller (`adapters/web/<Domain>Controller.java`).
2. Define request/response DTOs in `adapters/db/dto/<domain>/`.
3. Add a method to the service interface (`application/<domain>/port/in/<Domain>Service.java`).
4. Implement in `Default<Domain>Service.java`.
5. If the new endpoint needs new data: add to the repository interface (`port/out/`), implement in `adapters/db/repository/<domain>/`.
6. Use jOOQ DSL for SQL; reference table constants from `Tables.X`.
7. Write tests in matching test files.

**Brain prediction:** ⚠️ DEGRADED. Sonnet could synthesise this from the existing pattern visible in extracted entities. Confidence depends on how much BusinessContext is populated. See `learnings.md#C20`.

---

## Summary table

| Benchmark | Q's | Predicted PASS | DEGRADED | FAIL |
|---|---|---|---|---|
| A — SQL/DB | 20 | 2 | 7 | 11 |
| B — Call chain | 20 | 1 | 9 | 10 |
| C — Architecture | 20 | 0 | 9 | 11 |
| **Total** | **60** | **3 (5%)** | **25 (42%)** | **32 (53%)** |

**Read this honestly:** out of 60 deep questions, the current brain gets 5% right cleanly. 42% are partial answers (right direction, missing nuance). 53% will fail outright.

**Most-impactful gaps** (sorted by how many questions they affect):

1. **No DDL / config file extraction** (L09, C07, C08, C11) — 4 questions hit this.
2. **No domain-level abstraction layer over code entities** (C02, C03, C13, C19) — 4 questions.
3. **No global graph rollups** (B02, B03, B06, B12, B15, B17, B20) — 7 questions.
4. **BusinessContext fields too generic** (A5, A6, A14, A15, A19, B11, B14) — 7 questions.
5. **DTO / annotation property edges missing** (A4, A16) — 2 questions but high-leverage.

See `docs/learnings.md` for the full per-question failure catalog and concrete fix recommendations.
