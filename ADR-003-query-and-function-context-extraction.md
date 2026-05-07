# ADR-003: Query & Function Context Extraction — Intent-First Architecture

**Status:** Proposed  
**Date:** 2026-04-28  
**Deciders:** Chinmay (tech lead)  
**Supersedes:** Current regex/pattern approach in `code_tools.py`

---

## Context

Company-brain's value proposition is **translating code into business knowledge** — not producing a code mirror. The current pipeline (LLM Pass 1 → Pass 2 → Pass 3) extracts entities and relationships, then synthesises a `BusinessContext` record per entity.

Two quality gaps are blocking that goal:

### Gap 1: Query Extraction is a Pattern-Matching Arms Race

We currently detect queries by looking for specific ORM signatures:
- JPA `@Query(...)` annotations
- Spring Data derived method names (`findBy...`)
- jOOQ terminal calls (`.fetch()`, `.execute()`)
- JDBC `prepareStatement("...")`
- MyBatis `@Select(...)`

This is already 5 patterns. Real codebases use:
- Hibernate Criteria API / CriteriaBuilder
- Spring Data Specifications
- QueryDSL (not jOOQ)
- Stored procedure calls via `@Procedure`
- R2DBC reactive queries
- Custom `@RepositoryFragment` implementations
- Native `EntityManager.createNativeQuery()`
- Spring JDBC `SimpleJdbcCall`
- Embedded SQL in XML mappers (MyBatis)
- Dynamic SQL builders (`StringBuilder` + `jdbc.query(...)`)
- Vendor-specific APIs (Cassandra CQL, Mongo `Aggregation`, etc.)

A complete regex registry for all of these is unmaintainable, fragile, and perpetually incomplete. The real problem is deeper: **even if we extract the raw SQL or DSL chain, that is not what engineers need.** Nobody needs to read `SELECT c.payer_name, COUNT(*)...` in a sidebar — they need to know *"finds all competing payers for a given network plan, used by the competitiveness dashboard."*

### Gap 2: Function-Level Business Context is Shallow or Missing

The `ContextSynthesizer` currently gets:
- A method name + signature
- Up to 5 commits that touched the file
- Human annotations (rare, high-signal)

For stable functions that haven't been touched in months, it gets almost nothing. The result is low-confidence, generic context like *"this function processes data for the endpoint."* That is not useful on the UI.

---

## Decision Drivers

1. **Accuracy over coverage** — 20 deeply understood functions beat 200 shallow ones.
2. **ORM-agnostic** — must work without knowing which data access library is in use.
3. **UI-surfaceable** — extracted context must map 1:1 to UI components (purpose, risk badge, data touched, invariants, gaps).
4. **Incrementally improvable** — human annotations should upgrade confidence without a full re-run.
5. **Local-model compatible** — must work with Ollama/llama3.1:8b, not require GPT-4.

---

## Options Considered

### Option A: Exhaustive Pattern Registry (Current Direction)

Keep adding ORM-specific regex detectors. Add Hibernate Criteria, QueryDSL, R2DBC, etc. one by one.

| Dimension | Assessment |
|---|---|
| Completeness | Never complete — new frameworks always emerge |
| Accuracy | Only captures what was written, not what it means |
| Maintenance | High — each new ORM requires new patterns |
| UI value | Low — raw SQL is not useful business context |
| Local model compat | ✅ no LLM needed for extraction |

**Verdict:** Solves the wrong problem. The output (raw SQL strings) has no direct UI value. Will always be one new framework behind.

---

### Option B: LLM-First Intent Extraction (Recommended)

Stop trying to extract query syntax. Instead, treat every `Function` and `DatabaseQuery` entity as a unit of business intent, and let the LLM synthesise a structured `FunctionContext` from the method body regardless of how the data access is implemented.

**Core idea:** The LLM sees the full method body and asks *"What does this do, what data does it touch, and why does it exist?"* — not *"What SQL did it run?"*

#### Structured Output Schema

Define a new `FunctionContext` model that replaces / augments the current `BusinessContext`:

```json
{
  "purpose": "one sentence: what this does and why it exists",
  "data_reads": ["table_or_concept it reads from"],
  "data_writes": ["table_or_concept it writes to"],
  "filter_logic": "human-readable: e.g. filtered by network plan and date range",
  "side_effects": ["sends email", "publishes event", "calls ExternalService"],
  "performance_risk": "LOW | MEDIUM | HIGH",
  "performance_risk_reason": "e.g. unbounded join on large table",
  "invariants": ["rule that must hold — e.g. always filtered by tenant"],
  "change_risk": "LOW | MEDIUM | HIGH",
  "change_risk_reason": "why this is risky to change",
  "owner_team": "team name or null",
  "confidence": "high | medium | low",
  "gaps": ["what the LLM could not determine — flag for human annotation"]
}
```

**Key difference from current schema:**
- `data_reads` / `data_writes` are *semantic concepts* ("competitor payer records"), not raw SQL column names
- `filter_logic` describes the business filtering rule, not the WHERE clause
- `side_effects` captures non-DB effects (events, HTTP calls)
- `performance_risk` is a first-class field — drives query profiler use case

#### How it Works

1. **Code collection (existing):** `CodeTracer` already collects full method bodies into `CodeUnit.content`.
2. **Snippet extraction (existing):** `_extract_snippet()` already pulls 400-char method bodies.
3. **Intent synthesis (new Pass 1.5):** Before relationship extraction, run a targeted LLM pass over each Function/ApiEndpoint entity with its full method body and ask for the `FunctionContext` schema above.
4. **Storage:** Store `FunctionContext` as a JSON blob in the node's `metadata` JSONB column. No schema migration needed.
5. **UI rendering:** The existing entity detail panel can render all fields from `FunctionContext` with zero backend changes.

#### What the LLM Prompt Looks Like

```
You are extracting the business intent of a Java method.

Given the method body below, return ONLY valid JSON matching this schema:
{ "purpose": "...", "data_reads": [...], "data_writes": [...], "filter_logic": "...",
  "side_effects": [...], "performance_risk": "LOW|MEDIUM|HIGH", 
  "performance_risk_reason": "...", "invariants": [...], "change_risk": "...",
  "change_risk_reason": "...", "owner_team": null, "confidence": "low", "gaps": [...] }

Rules:
- data_reads / data_writes: describe WHAT data, not HOW it's fetched (ORM-agnostic)
- filter_logic: describe the business rule in plain English
- If you cannot determine something, add it to gaps — do NOT guess
- Confidence is "low" unless you also have git history or annotations

## Method: getPayerCompetitors
## File: PayerCompetitivenessController.java

```java
[method body here]
```
```

This works for jOOQ, JDBC, Hibernate, or a hand-rolled cursor — the LLM doesn't need to know which.

| Dimension | Assessment |
|---|---|
| Completeness | ✅ Works for any data access pattern |
| Accuracy | Medium–High (depends on method body quality) |
| Maintenance | Low — one prompt schema, no per-ORM code |
| UI value | ✅ High — all fields map directly to UI components |
| Local model compat | ⚠️ Needs good prompt engineering; llama3.1:8b handles structured JSON well at function scope |

---

### Option C: Static Type Resolution + LLM (Gold Standard, Deferred)

Use JavaParser / LSP to resolve types at compile time — know that `ctx.select(COMPETITORS.PAYER_NAME)` maps to `competitors.payer_name` with certainty.

| Dimension | Assessment |
|---|---|
| Completeness | ✅ Full AST understanding |
| Accuracy | Highest possible |
| Maintenance | High — requires Java tooling in Python pipeline |
| UI value | ✅ Highest |
| Local model compat | ✅ reduces LLM load |
| Feasibility now | ❌ 2–4 weeks of engineering |

**Verdict:** Right long-term direction; wrong for current phase. Revisit at ADR-004 when basic context quality is proven.

---

## Decision: Option B — LLM-First Intent Extraction

### What We're Building

**1. `FunctionContext` model** — replaces the current `BusinessContext` with a richer schema that explicitly models data access intent.

**2. Pass 1.5: Intent Synthesis Pass** — a new pipeline stage between entity extraction and relationship extraction. Runs one focused LLM call per Function/ApiEndpoint entity.

**3. Storage: `metadata` JSONB** — store `FunctionContext` in the node's existing `metadata` column. The graph node already has this field; the Java backend just needs to write the extra keys.

**4. UI: Function Detail Panel** — render all `FunctionContext` fields. Group into sections: Purpose / Data Access / Risk / Gaps.

### What We're NOT Building

- Per-ORM pattern matchers (jOOQ tables, JDBC strings, etc.) as the primary extraction mechanism.
- Raw SQL display in the UI (raw SQL is an implementation detail, not business context).
- Full JavaParser AST resolution (deferred to ADR-004).

### The Role of `extract_db_queries` Going Forward

The current `extract_db_queries` / `extract_jpa_queries` tool is demoted from "primary extraction" to "optional enrichment signal." It can still detect query boundaries (which method is a data-access method) and feed that signal into Pass 1.5, but it no longer needs to extract columns or SQL strings — that job moves to the LLM.

---

## Data Model

### Node Metadata Schema (stored in JSONB)

```json
{
  "role": "repository",
  "functionContext": {
    "purpose": "Returns all competing payer plans for a given network, filtered by effective date",
    "dataReads": ["payer_competitors", "network_plans"],
    "dataWrites": [],
    "filterLogic": "filtered by networkId and planId, ordered by market_share descending",
    "sideEffects": [],
    "performanceRisk": "MEDIUM",
    "performanceRiskReason": "unbounded result set — no pagination enforced at query level",
    "invariants": ["always filtered by tenant_id via row-level security"],
    "changeRisk": "HIGH",
    "changeRiskReason": "used by competitiveness dashboard; shape change breaks frontend contract",
    "ownerTeam": "payer-analytics",
    "confidence": "medium",
    "gaps": ["unclear if soft-deleted records are excluded"]
  }
}
```

### UI Mapping

| FunctionContext field | UI Component |
|---|---|
| `purpose` | Hero description (top of entity panel) |
| `dataReads` / `dataWrites` | "Data Access" chip group |
| `filterLogic` | "Filter Logic" prose block |
| `sideEffects` | "Side Effects" warning chips |
| `performanceRisk` | Coloured badge (green/amber/red) |
| `changeRisk` | Coloured badge |
| `invariants` | Invariant list |
| `gaps` | "Needs annotation" prompts |
| `confidence` | Confidence indicator |

---

## Trade-off Analysis

**LLM accuracy on small local models:** llama3.1:8b performs well on structured JSON extraction from a single method body (small prompt, constrained schema). The risk is hallucinated table names. Mitigation: include actual field/variable names from the method body; ask the model to use `gaps` rather than guessing.

**Cost vs. depth:** Running Pass 1.5 per entity adds LLM calls. For 50 entities that is 50 additional calls. With local Ollama this is free; with cloud APIs (Claude Sonnet) at ~$0.003/call this is $0.15 per pipeline run — acceptable.

**ORM blindness:** The LLM sees `dsl.select(...).from(...).where(...)` and understands this is data access even if it can't resolve the DSL. It can still describe the intent. False negatives (missing data access entirely) are the risk; address with a few-shot examples in the prompt that include a jOOQ snippet.

---

## Consequences

**Easier:**
- Adding support for a new ORM requires zero code changes — the LLM handles it
- UI context quality improves across the board (not just for annotated entities)
- `gaps` field surfaces annotation opportunities directly to users
- Performance risk assessment is free — no separate analysis pass needed

**Harder:**
- Non-deterministic output — same method may produce slightly different `purpose` on re-run. Mitigation: only re-synthesise on code change (hash-based invalidation)
- Testing — can't unit test LLM output. Use golden-file snapshots for regression

**What we'll need to revisit:**
- ADR-004: Add JavaParser-based type resolution to eliminate guessed table names
- ADR-005: Hash-based incremental synthesis (only re-run Pass 1.5 when method body changes)
- ADR-006: Human annotation UI — let engineers correct `gaps` inline; feed back into next synthesis

---

## Action Items

### Phase 1 — Schema & Storage (1 day)
1. [ ] Add `FunctionContext` dataclass to `entities.py` (mirrors JSON schema above)
2. [ ] Update `java_client.py` → `_entity_to_dict()` to serialize `functionContext` into entity metadata
3. [ ] Update Java `PipelineResultRequest` DTO + `PipelineService` to write `functionContext` keys into node `metadata` JSONB

### Phase 2 — Pass 1.5 Pipeline Stage (1–2 days)
4. [ ] Create `companybrain/pipeline/intent_synthesizer.py` — new `IntentSynthesizer` class
5. [ ] Prompt: one call per Function/ApiEndpoint entity, full method body, structured JSON schema
6. [ ] Few-shot examples: one JPA method, one jOOQ method, one plain JDBC method
7. [ ] Wire into orchestrator between `entity_extractor` and `relationship_extractor`
8. [ ] Store result in `entity.function_context` field

### Phase 3 — UI (1 day)
9. [ ] Update entity detail panel in `Dashboard.jsx` to render `FunctionContext` fields
10. [ ] Add `performanceRisk` and `changeRisk` badges
11. [ ] Render `gaps` as "Needs annotation" prompts with a CTA

### Phase 4 — Validation (0.5 day)
12. [ ] Re-run pipeline on `/api/v1/mcheck/niq/competitiveness/summary/competitors/payer`
13. [ ] Verify `getPayerCompetitors` gets a meaningful `purpose`, `dataReads`, `filterLogic`
14. [ ] Check that jOOQ-heavy repository entities produce non-empty `FunctionContext`
15. [ ] Compare quality vs. current `BusinessContext` output

---

## Appendix: Why Not Store Raw SQL?

Raw SQL / jOOQ chains have low information density for a business context tool:

```sql
-- What the code does (machine-readable)
SELECT c.payer_name, c.market_share, np.plan_code
FROM payer_competitors c
JOIN network_plans np ON np.id = c.plan_id
WHERE c.network_id = :networkId AND c.effective_date <= NOW()
ORDER BY c.market_share DESC
```

```
-- What engineers need (human-readable)
"Returns competing payer plans for a network, ranked by market share. 
 No pagination — caller must limit results. Performance risk: MEDIUM."
```

The second representation is what goes on the UI. The first can always be found by opening the source file. Company-brain's job is the second, not a SQL mirror.

Raw SQL *is* useful for a dedicated **query profiler** feature (execution plans, index analysis) — that is a separate product surface and should be designed separately when needed.
