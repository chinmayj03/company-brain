# ADR-0055 — Cross-File Cross-Cutting Extraction Pass

**Status:** Proposed
**Date:** 2026-05-11
**Builds on:** the per-file/per-method chunked extraction (ADR-0044/47/48)
**Sequenced with:** ADR-0056/57/58/59/60 — six-ADR set, parallel-shippable per IMPLEMENTATION-ORDER-V2

---

## Context

The benchmark (60 questions, 53% predicted FAIL) exposed the architectural ceiling of the current pipeline: **tree-sitter and the chunked extractor see one method or one file at a time. They never reason across files together.** Cross-file phenomena that humans (and Claude Code) reason about easily are invisible to the brain:

- **Defensive-copy idiom** (`niqAPIRequestPrototype` called 13 times across 7 files) — looks like a USES edge from each caller; the SHARED PATTERN ("this codebase always defensive-copies before mutating filters") is never extracted.
- **Soft-delete via `is_current=true` filter** — every read of `plan_info` filters this; the convention is never named as a fact.
- **CTE-materialisation optimisation** — `getPayerCompetitors` uses `.asMaterialized()` to scan `comp_providers` once; this is a CROSS-METHOD optimisation pattern (not a single-method fact).
- **Anti-pattern detection** — `CompetitivenessReportRequestDTO.java:25` uses literal `"lob"` while 16 other DTOs use `JsonKeyMapping.LOB`; the inconsistency requires comparing across 17 files.
- **Domain entity inference** — "Payer / Plan / Provider" emerges from naming patterns across 50+ classes; no single class IS the domain entity.
- **Implicit contracts** — caller pre-conditions ("the controller layer guarantees non-null request") only visible by reading caller AND callee.

These are NOT bugs in extraction; they are an architectural absence. The fix is a **third extraction pass** that runs AFTER per-method extraction (Stage 1) and AFTER relationship extraction (Stage 2) and BEFORE BusinessContext synthesis (Stage 3). Its input is a *window of related entities*; its output is a new entity type (`Pattern`) and new edge types (`IMPLEMENTS_PATTERN`, `VIOLATES_PATTERN`, `SHARED_INVARIANT`).

---

## Decision

Add **Stage 2.5 — Cross-File Cross-Cutting Pass** between Stage 2 (relationships) and Stage 3 (context synthesis).

### Pipeline placement

```
Stage 1   per-method ContextAgent                  (existing, ADR-0048)
Stage 2   relationship + structural edge merging   (existing, ADR-0011/0042)
Stage 2.5 cross-file cross-cutting pass            ← NEW (this ADR)
Stage 3   BusinessContext synthesis                (existing, ADR-005)
```

### How it works

The pass runs the following sub-passes, each operating on a windowed view of the brain (not just per-entity):

**SP-1 — Idiom & pattern detection (deterministic)**

Scan the relationship graph for repeating call shapes. Heuristic:
- Same callee called from ≥ N (default 5) distinct callers, with similar argument patterns → emit a `Pattern { name: inferred, instances: [callers] }`.
- Same field-filter applied across ≥ N callers (`.where(X.eq(true))`) → emit `Pattern { name: "soft_delete_filter", field: X }`.
- Same try/catch wrapper structure across ≥ N callers → emit `Pattern { name: "exception_translation" }`.

Output: `Pattern` entities + `IMPLEMENTS_PATTERN` edges from each instance to the pattern.

**SP-2 — Anti-pattern / inconsistency detection (deterministic)**

For each `Pattern` of strength > 80% (i.e., 17 of 21 sites use the convention), the 4 sites that DON'T are flagged with `VIOLATES_PATTERN` edges. The inconsistency is the signal.

**SP-3 — Cross-method invariant inference (LLM-batched)**

For windows of 5-10 related methods (same class OR same call chain), one ContextAgent batch call:

```xml
<context>
  These methods all read from plan_info. Look across all of them.
  Identify invariants that hold ACROSS the method set, not just within
  one method. Example: "all reads filter is_current=true (5/5 methods)".
</context>
<methods>
  <method>...full body of getPayerCompetitors...</method>
  <method>...full body of getPayerInfo...</method>
  ...
</methods>
```

Output: `SharedInvariant` entities, attached to all participating methods via `SHARES_INVARIANT` edge.

**SP-4 — Implicit contract inference (LLM-batched)**

For each method M with N callers, batch-extract: "given M's body and the call sites of M, what pre-conditions do callers seem to assume? What post-conditions does M provide?". Stored on M's `BusinessContext.implicit_contract: { preconditions: [], postconditions: [] }`.

**SP-5 — Domain entity inference (LLM, one-shot per repo)**

After Stage 1 completes, ONE LLM call gets:
- The full list of extracted Class entity names (just names + roles)
- The package structure
- A few representative DTO field lists

Returns: 5-15 `DomainEntity` entities with `aliases` and `anchor_class_urns`. Edge: Class → DomainEntity via `REPRESENTS`.

### New entity + edge types

```python
# models/entities.py — additions

@dataclass
class Pattern:
    entity_type: str = "Pattern"
    name: str
    description: str
    instance_count: int
    confidence: float
    inferred_from: str  # "deterministic" | "llm"


@dataclass
class SharedInvariant:
    entity_type: str = "SharedInvariant"
    name: str
    statement: str          # "all reads filter is_current=true"
    affected_method_urns: list[str]
    evidence_method_urns: list[str]


@dataclass
class DomainEntity:
    entity_type: str = "DomainEntity"
    name: str               # "Payer"
    aliases: list[str]      # ["payer_id", "PayerPlan", "BasePayer"]
    description: str
    anchor_class_urns: list[str]


# New edge types — append to taxonomy
IMPLEMENTS_PATTERN
VIOLATES_PATTERN
SHARES_INVARIANT
REPRESENTS
HAS_IMPLICIT_CONTRACT
```

---

## File ownership for THIS PR (parallel-safe)

```
company-brain-ai/src/companybrain/pipeline/cross_file_pass.py     # NEW — orchestrator
company-brain-ai/src/companybrain/pipeline/idiom_detector.py      # NEW — SP-1
company-brain-ai/src/companybrain/pipeline/antipattern_detector.py # NEW — SP-2
company-brain-ai/src/companybrain/pipeline/invariant_inferrer.py  # NEW — SP-3 + SP-4
company-brain-ai/src/companybrain/pipeline/domain_inferrer.py     # NEW — SP-5
tests/unit/test_cross_file_pass.py                                  # NEW
tests/acceptance/test_cross_file_pass_lob.py                        # NEW — checks the lob anti-pattern detection
```

Append-only edits to:

```
company-brain-ai/src/companybrain/models/entities.py    # add Pattern, SharedInvariant, DomainEntity, new edge consts
company-brain-ai/src/companybrain/pipeline/orchestrator.py  # invoke run_cross_file_pass between stages 2 and 3
company-brain-ai/src/companybrain/config.py             # tunables: pattern_min_instances, invariant_window_size
```

Does NOT touch the chunker, ContextAgent, or any other file owned by parallel ADRs (0056/57/58/59/60).

---

## Acceptance test (the lob anti-pattern smoke test)

```python
async def test_lob_antipattern_detected():
    """16 DTOs use JsonKeyMapping.LOB, one (CompetitivenessReportRequestDTO) uses literal "lob".
    The cross-file pass must detect this as a Pattern + 1 violation."""
    await run_pipeline_harness(repo="fixtures/network-iq-snapshot")

    # Find the JsonProperty-LOB pattern
    patterns = await brain_query("list all Pattern entities mentioning LOB")
    lob_pattern = next(p for p in patterns if "LOB" in p.name or "lob" in p.name.lower())
    assert lob_pattern.instance_count >= 16

    # Find the violation
    violators = await brain_query(f"which entities VIOLATES_PATTERN {lob_pattern.urn}?")
    assert any("CompetitivenessReportRequestDTO" in v.name for v in violators)


async def test_soft_delete_invariant_detected():
    """All reads of plan_info filter is_current=true. Detect this as a SharedInvariant."""
    await run_pipeline_harness(repo="fixtures/network-iq-snapshot")
    invariants = await brain_query("SharedInvariant where statement contains 'is_current'")
    assert any("is_current" in inv.statement for inv in invariants)
    assert len(invariants[0].affected_method_urns) >= 3


async def test_domain_entity_payer_inferred():
    """Domain inference must produce a 'Payer' entity linked to >=5 anchor classes."""
    await run_pipeline_harness(repo="fixtures/network-iq-snapshot")
    domains = await brain_query("list DomainEntity")
    payer = next(d for d in domains if d.name == "Payer")
    assert len(payer.anchor_class_urns) >= 5
```

---

## Effort estimate

3 days. Most of it is SP-1 (idiom detection) which is pure deterministic graph analysis. SP-3 + SP-5 each cost ~$0.005 per repo per run.

---

## Action items

1. [ ] `pipeline/idiom_detector.py` — graph scanner emitting `Pattern` entities for repeating call shapes.
2. [ ] `pipeline/antipattern_detector.py` — flag pattern violators.
3. [ ] `pipeline/invariant_inferrer.py` — batched LLM call per method-window.
4. [ ] `pipeline/domain_inferrer.py` — one-shot LLM call per repo.
5. [ ] `models/entities.py` — append new types.
6. [ ] `orchestrator.py` — invoke between stages 2 and 3.
7. [ ] Acceptance test on the lob anti-pattern + soft-delete invariant + Payer domain entity.
