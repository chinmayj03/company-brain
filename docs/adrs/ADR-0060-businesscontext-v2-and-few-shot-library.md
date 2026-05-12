# ADR-0060 — BusinessContext v2 + Few-Shot Anchor Library

**Status:** Proposed
**Date:** 2026-05-11
**Builds on:** existing 21-field `BusinessContext` (from ADR-005), ContextSynthesizer (Stage 3), ADR-0053 prompt-pattern groundwork
**Sequenced with:** ADR-0055/56/57/58/59 — six-ADR set, parallel-shippable.

---

## Context

The current `BusinessContext` has 21 fields (`purpose`, `change_risk`, `data_sensitivity`, `invariants[]`, `side_effects[]`, `failure_modes[]`, `owners`, …). Three problems showed up in the benchmark:

**Problem 1 — fields too generic.** Engineering nuances (idempotency, null-handling per-parameter, transaction mode, anti-pattern detection) get flattened into `purpose` as prose — Sonnet can't reliably extract them for structured queries.

**Problem 2 — no anchor for "what good looks like".** The Stage 3 prompt describes the schema, then expects the model to infer good population. Result: most `purpose` fields are bland ("queries the database for payer info") and most `invariants[]` arrays are empty.

**Problem 3 — no calibration.** Without a few-shot library, every prompt revision is a roll of the dice. Adding "extract this field" doesn't help if the model has no example of done-well.

This ADR adds 7 new typed fields, plus a curated 30-example few-shot library that anchors every Stage 3 call.

---

## Decision

### D1 — Seven new typed fields on BusinessContext

```python
@dataclass
class BusinessContext:
    # ... existing 21 fields ...

    # NEW — engineering rigour fields
    is_idempotent: Optional[bool] = None
    null_handling: dict[str, Literal["checked", "throws", "tolerates", "unchecked"]] = field(default_factory=dict)
    transaction_mode: Optional[Literal["read_only", "read_write", "no_transaction"]] = None
    anti_patterns: list[str] = field(default_factory=list)
    engineering_notes: list[str] = field(default_factory=list)
    performance_class: Optional[Literal["O(1)", "O(log n)", "O(n)", "O(n log n)", "O(n²)", "unbounded"]] = None
    security_class: Optional[Literal["public", "authenticated", "authorised", "internal_only", "admin_only"]] = None
```

Why these 7 specifically:
- **`is_idempotent`** — answers "is it safe to retry?" Critical for resilience, every read is idempotent (default true for SELECT-only methods).
- **`null_handling`** — per-param contract; answers "what happens if I pass null for X?" The benchmark Q A20.
- **`transaction_mode`** — extracted from `@Transactional` annotation; answers "is this a read replica candidate?".
- **`anti_patterns`** — flags inconsistencies with codebase conventions (literal `"lob"` instead of `JsonKeyMapping.LOB`). Surfaced in Risk dashboard (Product 2).
- **`engineering_notes`** — free-form for things like "uses LATERAL because unnest references outer column", "uses .asMaterialized() to avoid scanning comp_providers twice".
- **`performance_class`** — rough complexity; answers "is this an N+1 risk?".
- **`security_class`** — extracted from auth annotations; answers "is this endpoint public?".

### D2 — Few-shot anchor library

`pipeline/few_shot_library.py` ships with **30 worked examples** (one per ENTITY_TYPE × COMMON_SHAPE combination). Each example is a (input_method_body, expected_full_BusinessContext_v2) pair.

Examples cover:
- A simple GET handler with auth → security_class="authenticated", is_idempotent=true
- A repository SELECT with filters → all-null mutation fields, transaction_mode="read_only"
- A repository UPSERT → transaction_mode="read_write", is_idempotent based on UPSERT semantics
- A complex method like `getPayerCompetitors` → engineering_notes mentions CTE materialisation
- A DTO field setter → trivial entry, performance_class="O(1)"
- A controller with `@PreAuthorize` → security_class="authorised"
- A method with no null check → null_handling reflects unchecked params
- A method with `@Transactional(readOnly=true)` → transaction_mode set
- A loop-with-DB-call (N+1) → performance_class="O(n)" + anti_patterns=["potential_n_plus_1"]
- A method using literal string instead of constant → anti_patterns=["literal_should_use_constant"]
- … 20 more covering edge types, async, error handlers, etc.

The examples are KEPT SHORT (avg 200 tokens each, capped at 6KB total) and cached via prompt-cache so they cost ~$0.0003 per Stage 3 call after the first.

### D3 — Prompt structure refactor

The Stage 3 prompt becomes:

```
SYSTEM (cached):
  You are a code-context synthesiser. For each entity, populate the
  21+7=28-field BusinessContext.

  Field rubric (with hard rules):
  - is_idempotent: TRUE if no INSERT/UPDATE/DELETE; FALSE if ANY mutation.
    Set NULL only if you cannot tell from the body.
  - null_handling: map every parameter name to one of {checked, throws,
    tolerates, unchecked}. "checked" = explicit if-null. "throws" = if-null then throw.
    "tolerates" = passed through to a method that handles null. "unchecked" = NPE risk.
  - transaction_mode: extract from @Transactional. Default for repository
    SELECT methods is "read_only" even without annotation.
  - anti_patterns: flag any of the 12 patterns in the rubric below
  - performance_class: O(N) only when there's a confirmed loop over a
    user-controlled collection. Don't guess; use NULL when unsure.
  - security_class: from @PreAuthorize / @RolesAllowed / @PermitAll. Default
    "authenticated" if no annotation but the controller is behind auth filter.

  EXAMPLES (30 worked pairs — see few_shot_library):
  ...

  Return a single line of compact JSON.

USER:
  {entity body + relevant context}
```

### D4 — Schema versioning

`BusinessContext` gets a `schema_version: int = 2` field. Old entries (v1) are tagged; query path handles both. Migration script in `cli_helpers/upgrade_business_context.py` re-runs Stage 3 on v1 entities to upgrade them.

---

## File ownership for THIS PR (parallel-safe)

```
company-brain-ai/src/companybrain/pipeline/few_shot_library.py        # NEW — the 30 anchors
company-brain-ai/src/companybrain/pipeline/business_context_v2_prompt.py  # NEW — prompt + rubric
company-brain-ai/src/companybrain/cli_helpers/upgrade_business_context.py # NEW — v1 → v2 migration
tests/unit/test_business_context_v2.py                                  # NEW
tests/regression/golden_business_context/                                # NEW — 30 fixtures matching the library
tests/acceptance/test_business_context_v2_quality.py                     # NEW
```

Append-only edits to:

```
company-brain-ai/src/companybrain/models/entities.py                # add 7 new fields + schema_version
company-brain-ai/src/companybrain/pipeline/context_synthesizer.py   # use new prompt, attach few-shot library
```

Does NOT touch any file owned by ADR-0055/56/57/58/59. The 30 few-shot examples are this ADR's exclusive territory.

---

## Acceptance test

```python
async def test_idempotent_field_populated():
    """SELECT-only methods should have is_idempotent=true."""
    await run_pipeline_harness(repo="fixtures/network-iq-snapshot")
    bc = await brain_get_context("CompetitivenessPlanRepository.getPayerCompetitors")
    assert bc.is_idempotent is True


async def test_null_handling_per_param():
    """getPayerCompetitors null-checks basePayerName but not request."""
    bc = await brain_get_context("CompetitivenessPlanRepository.getPayerCompetitors")
    assert bc.null_handling["basePayerName"] == "throws"
    assert bc.null_handling["request"] in ("unchecked", "tolerates")


async def test_anti_pattern_literal_lob():
    """CompetitivenessReportRequestDTO uses literal 'lob' — anti-pattern."""
    bc = await brain_get_context("CompetitivenessReportRequestDTO")
    assert any("literal" in p.lower() for p in bc.anti_patterns)


async def test_few_shot_library_loads_and_anchors():
    """The few-shot library must be < 6KB total to fit cache."""
    from companybrain.pipeline.few_shot_library import EXAMPLES
    serialized = "\n".join(json.dumps(e) for e in EXAMPLES)
    assert len(serialized) < 6_000
    assert len(EXAMPLES) >= 30


async def test_v1_to_v2_migration():
    """Old v1 entries upgrade cleanly with no data loss."""
    # seed brain with a v1 entry; run migration; assert v2 fields populated
    pass


async def test_quality_lift_on_benchmark_questions():
    """A19 (idempotent), A20 (null handling), A5 (anti-pattern), B14 (transaction mode)
    all PASS after this ADR."""
    await run_pipeline_harness(repo="fixtures/network-iq-snapshot")
    for qid in ["A19", "A20", "A5", "B14"]:
        result = await run_benchmark_question(qid)
        assert result.status == "PASS", f"{qid} expected PASS, got {result.status}"
```

---

## Effort estimate

3 days. Field additions are 1 hour. The 30 few-shot examples are the bulk of the work — each needs careful curation (~30 min × 30 = 15 hours). Migration is half a day.

---

## Action items

1. [ ] Append 7 fields to `BusinessContext` in `models/entities.py` + `schema_version=2`.
2. [ ] Build the 30-example library in `few_shot_library.py`. Each example is 150-250 tokens.
3. [ ] Rewrite the Stage 3 prompt in `business_context_v2_prompt.py` with hard rubric.
4. [ ] Update `context_synthesizer.py` to use new prompt + library.
5. [ ] Migration script `upgrade_business_context.py` — replays Stage 3 on v1 entities.
6. [ ] Golden fixtures: 30 (input, expected v2 output) pairs in `tests/regression/golden_business_context/`.
7. [ ] Acceptance: 5 benchmark questions move from FAIL/DEGRADED to PASS.
8. [ ] Telemetry: per-run count of fields populated (out of 28); track over time.
