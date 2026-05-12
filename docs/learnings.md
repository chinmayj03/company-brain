# Learnings — Where the brain currently fails on network-iq-backend-java

**Companion to:** `docs/BENCHMARK-NETWORK-IQ.md` (60 deep questions, 53% predicted to fail).
**Purpose:** scratchpad for the founder. Each entry has the symptom, the root cause traced through the actual codebase, and the concrete fix. Use this to drive the next sprint of brain improvements.
**Honesty bar:** every "fix" below references either an existing ADR/file or proposes a specific new module. No vague "improve the prompt" handwaves.

---

## Failure mode catalog (organised by root cause, not by question)

### Root cause 1 — Truncation in the chunked extractor's `code_snippet` field

**Affects:** A1, A2, A10, A14, A15

**Symptom:** the `getPayerCompetitors` method is ~3 KB. The chunked extractor's `code_snippet` field is bounded at ~1500 chars by `MAX_TOKENS_SINGLE` indirectly. Result: only the first 2 of 6 CTEs are captured. Sonnet on `/query` answers "the method uses CTEs" but can't enumerate them.

**Where to look:**
- `company-brain-ai/src/companybrain/pipeline/chunk_extractor.py:35` — `MAX_TOKENS_SINGLE = 1_200`
- `company-brain-ai/src/companybrain/agents/context_agent.py` — the response is parsed and `code_snippet` is whatever the LLM emitted, which it caps to fit `max_tokens=1200`
- `company-brain-ai/src/companybrain/models/entities.py` — no max length on `code_snippet` field; the cap is upstream

**Fix:**
1. **Bump `MAX_TOKENS_SINGLE` to 3000** for "rich" methods (detected by body > 1KB). Cost impact: ~$0.001 extra per such method.
2. **OR**: stop putting the full source in `code_snippet`; instead, store a `code_snippet_excerpt` (first 500 chars) PLUS a `source_file:line_start:line_end` reference; downstream tooling reads the actual source from disk on demand. This is the design the harness P5 rooms layer should use.
3. **Add a verifier**: after extraction, count distinct `WITH` clauses in the source method and assert that `code_snippet` includes at least N-1. Add as ADR-0054 G3 sub-check.

### Root cause 2 — No SQL DDL / migration file extraction

**Affects:** A8, A9, C19 (and indirectly A3 — column-type detection)

**Symptom:** the brain has no awareness of the `db/migration/V1__baseline.sql` file. Asked "which tables have a `lob` column", it can't answer. Asked "what's the type of `comp_providers.payer_id`" (the `text[]` array column that drives the whole array-overlap query pattern), it has no clue.

**Where to look:**
- `company-brain-ai/src/companybrain/pipeline/code_chunker.py` — `_LANGUAGE_MAP` does not include `.sql`
- `company-brain-ai/src/companybrain/pipeline/file_walker.py` — walks `*.java`, `*.py`, `*.ts`; `*.sql` is in `extractable` but no chunker handles it

**Fix:**
1. **New `pipeline/sql_chunker.py`** that extracts:
   - `DatabaseTable` entities from `CREATE TABLE` statements
   - `DatabaseColumn` entities from columns within (with type)
   - `DatabaseIndex` entities from `CREATE INDEX`
   - `MIGRATION_CREATES` / `MIGRATION_ALTERS` edges linking migration files to tables
2. Wire into `code_chunker.chunk_repo` for `*.sql` paths.
3. Output entities should match the schema the SmartZoneAssembler already expects (it queries for `DatabaseTable` URNs in lob-style queries).
4. **Bonus**: parse `*.sql` test seeds to detect "this column has values like COMMERCIAL/MEDICARE" — gives BusinessContext hints for free.

**Effort estimate:** 2 days. Tree-sitter has a SQL grammar.

### Root cause 3 — DTO field-level edges not extracted

**Affects:** A4, A16, B7

**Symptom:** the brain extracts DTOs as `Class` entities (per ADR-0048 SpecialistAgent's `skip_dto` fast-path) but ignores their fields. Asked "which DTOs have a lob field", the brain returns 0 — because the `@JsonProperty(JsonKeyMapping.LOB)` annotation on a field never became an edge.

**Where to look:**
- `company-brain-ai/src/companybrain/pipeline/entity_extractor.py:1348` — `_entities_from_trivial_pojo` produces a `Class` entity but no `Field` sub-entities
- `company-brain-ai/src/companybrain/agents/specialist_agent.py` — `skip_dto` list is populated, the DTOs themselves never get an LLM call

**Fix:**
1. The DTO fast-path needs to emit `Field` entities for each declared property:
   ```python
   def _entities_from_trivial_pojo(unit):
       parsed = ast_cache.parse(...)
       cls = make_class_entity(parsed)
       fields = [
           make_field_entity(f, parent=cls,
                             json_property=extract_annotation(f, "JsonProperty"))
           for f in parsed.fields
       ]
       return [cls] + fields
   ```
2. Add `READS_FIELD` / `BINDS_TO` edges so the global query "which DTOs have lob" becomes a single graph traversal.
3. Field entity schema: `{ entity_type: "Field", name, type, parent_class_urn, annotations: {...}, json_property: "lob" if present }`.

**Effort estimate:** 1.5 days. Tree-sitter Java grammar already exposes field nodes.

### Root cause 4 — No global graph rollups

**Affects:** B2, B3, B5, B6, B8, B12, B15, B17, B19, B20, C5

**Symptom:** the brain answers per-entity questions ("what does X do?") well but per-graph questions ("how many @RestController classes?", "which @Service classes are unused?") fail. There's no aggregation layer.

**Where to look:**
- `company-brain-ai/src/companybrain/mcp/server.py` — exposes 10 tools, none of them are "count by entity_type" or "find orphans"
- `company-brain-ai/src/companybrain/api/routes/query.py` — Sonnet answers based on retrieved entities; can't issue a graph query

**Fix:**
1. **Add 5 new MCP tools** (in `mcp/tools/`):
   - `count_by_type(entity_type)` — `SELECT count(*) FROM nodes WHERE node_type = $1`
   - `find_orphans(node_type)` — find nodes with no inbound CALLS edges
   - `longest_call_path()` — Cypher: `MATCH p=(:Method)-[:CALLS*]->(:Method) RETURN p ORDER BY length(p) DESC LIMIT 1`
   - `find_by_annotation(annotation_name)` — `MATCH (n)-[:ANNOTATES]-(a:Annotation {name:$1}) RETURN n`
   - `module_dependencies()` — derived view of which top-level packages depend on which
2. SmartZoneAssembler should auto-invoke these for "list / count / which" style questions. Pattern: detect aggregation intent in the user query, route to the right tool.

**Effort estimate:** 2 days. Most of the SQL/Cypher is one-liners.

### Root cause 5 — BusinessContext fields too generic for engineering nuance

**Affects:** A5 (anti-pattern), A6 (null-handling), A14 (LATERAL semantic), A15 (soft-delete), A19 (idempotent), B11 (defensive copy), B14 (transactional readOnly)

**Symptom:** the 21-field BusinessContext captures `purpose`, `change_risk`, `data_sensitivity`, `invariants[]`, `side_effects[]`, etc. — but engineering-specific facts (idempotency, null-safety per parameter, SQL-semantics like "LATERAL is required because…") don't fit any field. They get flattened into `purpose` as natural-language prose, which Sonnet can't reliably extract for a structured query.

**Where to look:**
- `company-brain-ai/src/companybrain/models/entities.py` — `BusinessContext` dataclass field list
- `company-brain-ai/src/companybrain/pipeline/context_synthesizer.py` — the prompt that asks the LLM to populate them

**Fix:** add 5 typed fields to BusinessContext:
1. `is_idempotent: Optional[bool]`
2. `null_handling: dict[param_name, "checked" | "throws" | "tolerates" | "unchecked"]`
3. `transaction_mode: Optional["read_only" | "read_write" | "no_transaction"]`
4. `anti_patterns: list[str]` — flagged inconsistencies (e.g., "uses literal string instead of constant")
5. `engineering_notes: list[str]` — free-form for things like "LATERAL needed because unnest references outer column"

Update the ContextSynthesizer prompt with examples per field. Update the JSON schema in tests.

**Effort estimate:** 1 day for fields, 1-2 days for prompt iteration + golden-output regression suite.

### Root cause 6 — No domain-entity layer (Java class ≠ business concept)

**Affects:** C2, C3, C13, C19

**Symptom:** the brain extracts `CompetitivenessPayerSummaryDTO` as a Class entity. The actual domain concept is "Payer". There's no mapping. Asked "what are the 5 most important domain entities?", the brain returns Java class names, not "Payer / Plan / Provider / Network / Coverage".

**Where to look:**
- `company-brain-ai/src/companybrain/models/entities.py` — entity types are CODE-LEVEL: Class, Method, Field, ApiEndpoint, DatabaseTable, DatabaseColumn. No `DomainEntity`.
- No `domain_entity_extractor.py` exists.

**Fix:**
1. **Add `DomainEntity` entity_type** with fields `{name, aliases[], owning_module, anchor_class_urns[]}`.
2. **Add a "domain extraction" pass** — runs once per `brain index` after structural extraction. Asks Sonnet: "Given these 50 Java classes and their package structure, what are the 5-15 distinct business concepts? For each, list the Java classes that represent it."
3. Output: `Payer { aliases: ["payer_id"], anchor_classes: [PayerPlan, CompetitivenessPayerSummaryDTO, ...] }`.
4. New edge type: `REPRESENTS` (Class → DomainEntity).

**Effort estimate:** 3 days. Custom extraction pass + UI for human curation (optional but useful).

### Root cause 7 — No config / Dockerfile / pom.xml / yml extraction

**Affects:** C7, C8, C11 (and indirectly C9, C10, C12)

**Symptom:** the brain only reads source code. Critical project facts live in `application.yml`, `pom.xml`, `Dockerfile`, `docker-compose.infra.yml`, `logback-spring.xml`. Asked "what database is this?", "what's the deployment story?", "are there feature flags?" — the brain has no answer.

**Where to look:**
- `company-brain-ai/src/companybrain/pipeline/code_chunker.py` — `_LANGUAGE_MAP` covers source; ignores `.yml`, `.xml`, `.toml`, `.dockerfile`
- `company-brain-ai/src/companybrain/pipeline/file_walker.py` — `extractable` list

**Fix:**
1. **Extend the file walker** to include config files: `application*.yml`, `pom.xml`, `build.gradle*`, `package.json`, `Dockerfile*`, `docker-compose*.yml`, `.env.example`, `logback*.xml`.
2. **Add a `ConfigEntity` type** with `{file, format, key_path, value, semantic_tag}`.
3. Examples after extraction:
   - `{file: "application.yml", key_path: "spring.datasource.url", value: "jdbc:postgresql://...", semantic_tag: "database_url"}`
   - `{file: "pom.xml", key_path: "dependencies/dependency[artifactId='postgresql']/version", value: "42.7.0", semantic_tag: "database_driver_version"}`
   - `{file: "Dockerfile", key_path: "FROM", value: "openjdk:21-jdk-slim", semantic_tag: "runtime"}`
4. SmartZoneAssembler queries these for infra/deploy questions.

**Effort estimate:** 2-3 days. YAML/XML/properties parsers are stdlib.

### Root cause 8 — Single-endpoint extraction misses 21 sibling endpoints

**Affects:** A8 (lob rename impact), B5 (count endpoints in controller), B7 (cross-controller LOB usage)

**Symptom:** when you run `brain index --endpoints "POST /competitiveness/summary/competitors/payer"`, the brain extracts only the call chain for that ONE endpoint. The other 20 endpoints in `CompetitivenessController.java` are seen by the chunker (the file is in scope) but their bodies aren't extracted because the SpecialistAgent's plan focuses on the called methods.

**Where to look:**
- `company-brain-ai/src/companybrain/agents/specialist_agent.py` — plan output focuses on the entry endpoint's call chain
- `company-brain-ai/src/companybrain/collectors/manifest_filter.py` — caps candidates at 20 but doesn't include all sibling endpoints in the entry controller

**Fix:**
1. **When the entry handler is in a controller, ALWAYS include all other endpoints in that controller in the SpecialistAgent's plan** (with a flag like `include_reason: "sibling_endpoint"`). Cost: small (controllers usually have 5-30 endpoints; methods are short).
2. Alternatively: tell users "for repo-level questions, run `brain index` without `--endpoints`"; on extraction-time error, suggest the full-repo command.

**Effort estimate:** Half day. Just expand the manifest filter's candidate list.

### Root cause 9 — Reachability filter is per-endpoint, not per-repo

**Affects:** B3 (dead service detection), B19 (cross-module dependencies), C5 (most-coverage module)

**Symptom:** the reachability filter (ADR-0043) is invoked per-endpoint. It drops entities not on the current endpoint's call graph. After 50 endpoints, you have 50 reachable subsets — but no UNION of them, and no "everything reachable from any controller" view. So "find dead @Service classes" returns no result.

**Where to look:**
- `company-brain-ai/src/companybrain/pipeline/reachability_filter.py` — operates on a single FocalContext at a time
- No post-`brain index` "global reachability aggregator"

**Fix:**
1. After `brain index` completes, run a one-shot **`global_reachability_aggregator.py`**:
   - Union of all per-endpoint `reachable` sets
   - Mark entities not in any reachable set as `dead_code: true`
   - Surface as `find_orphans` MCP tool result
2. ~50 LOC of post-processing; runs in seconds.

**Effort estimate:** 1 day.

### Root cause 10 — No git ownership extraction

**Affects:** C13 (bus factor), and Product 2 in PRODUCT-VISION (Risk layer) entirely

**Symptom:** the brain extracts code but not who wrote it. The Git collector grabs commits per-endpoint, not per-entity ownership. Asked "who owns the lob code?" — no answer.

**Where to look:**
- `company-brain-ai/src/companybrain/collectors/git_collector.py` — fetches commit history but doesn't compute per-entity authorship
- `company-brain-ai/src/companybrain/models/entities.py` — entity has `last_modified_commit` but no `top_author` / `co_authors[]`

**Fix:**
1. After extraction, run `git blame` on each entity's source range; aggregate `(author, line_count, last_touched_at)` per entity.
2. Add fields to `ExtractedEntity`: `top_author: str`, `co_authors: list[(str, int)]`, `bus_factor: int` (count of authors with >10% lines).
3. New MCP tool: `find_owner(entity_urn)`, `bus_factor_report()`.

**Effort estimate:** 2 days. `pygit2` or `subprocess git blame` per file.

---

## Top-5 fixes ranked by leverage (do these first)

In order of "biggest improvement to the 60-question pass rate":

| Priority | Fix | Questions improved | Effort | Cost impact |
|---|---|---|---|---|
| 1 | **Add SQL DDL chunker** (Root cause 2) | A8, A9, C19, C9 (4 → ✅) | 2 days | $0 |
| 2 | **Always extract all sibling endpoints in entry controller** (Root cause 8) | A8, B5, B7 (3 → ✅) | 0.5 days | +$0.05/run |
| 3 | **DTO field-level extraction with @JsonProperty edges** (Root cause 3) | A4, A16, B7 (3 → ✅) | 1.5 days | $0 |
| 4 | **5 new MCP graph-rollup tools** (Root cause 4) | B2, B3, B5, B6, B12, B15 (6 → ✅) | 2 days | $0 |
| 5 | **Bump `MAX_TOKENS_SINGLE` to 3000 for rich methods + add verifier** (Root cause 1) | A1, A2, A10, A14, A15 (5 → ✅) | 0.5 days | +$0.001/method |

**Total effort: 6.5 days. Total questions improved: 21 (from 5% → 40% pass rate).**

---

## What this benchmark proves about the demo

The current brain answers ~3 of the 60 deep questions cleanly. The other 57 either degrade or fail.

**For an investor demo**, this is the pivotal observation: **don't pick deep questions** for the demo. Pick the 3 questions the brain ACTUALLY answers well:

- ✅ A13 — "what's the default page size?" (literal extraction)
- ✅ A18 — "what does the empty-result branch return?" (short-circuit short bodies)
- ✅ B9 — "which classes implement CompetitivenessService?" (structural pre-pass IMPLEMENTS edge)

PLUS the lob query (A8) — but ONLY after fixes #1, #2, #3, #5 land. Without those, the lob demo is fragile.

For the seed-pitch demo: **pre-bake the brain on Stripe / Vercel / Anthropic-MCP** (per the SEED-FUNDING-PACKAGE doc) where you control the question set, AND ship fixes 1, 2, 3 in the next 5 days so the lob demo on `network-iq-backend-java` works reliably as the secondary deep-cut.

---

## What the user should do with this file

1. **Triage the 10 root causes** — pick which 3 to fix this sprint (recommend: 1, 2, 8 for biggest demo lift).
2. **Open one ADR per root cause** that you decide to fix — each one already has a concrete fix paragraph above; copy it into an ADR.
3. **For the seed demo**, freeze the question set to the 3 PASS-list questions plus the lob query post-fix. Don't let an investor pick from the 57-FAIL list.
4. **Re-run the benchmark monthly**. If the pass rate isn't growing 5-10% per sprint, the brain isn't improving. Surface this as a board-ready engineering metric.

This file is a living scratchpad — append new failure modes here as they're discovered, and remove entries as they're fixed (with the ADR/PR that closed them).
