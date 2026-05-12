# ADR-0056 — Verifier Sub-Agent + Self-Correction Loop

**Status:** Proposed
**Date:** 2026-05-11
**Builds on:** ADR-0048 (ContextAgent), ADR-0050 (extraction_recovery), ADR-0053 (PR-B verifier intent)
**Sequenced with:** ADR-0055/57/58/59/60 — six-ADR set, parallel-shippable.

---

## Context

Current extraction is **single-pass and trust-based**: the LLM emits an entity, the brain stores it, nobody re-checks. Failure modes that result:

- **Hallucinated `query_text`** — LLM emits SQL that's "the kind of SQL the method probably runs" but doesn't appear verbatim in source. The lob query failure two days ago was exactly this.
- **Wrong file/line citations** — LLM picks a nearby line as the "evidence", off by 5-50 lines. Citations look plausible but don't match.
- **Missed entities in batch responses** — when a 10-method batch truncates, ADR-0050 bisection recovers the structural list but doesn't re-verify the salvaged entities are actually correct.
- **Drift entities** — the navigator pulls in `JsonKeyMapping` (a constants table) and the LLM extracts it as `Function` with confidence 0.8. There's no re-check that "constants table is not a Function".
- **Inconsistent edge directions** — `EXTENDS` accidentally emitted reversed (subclass → superclass instead of the canonical reverse). Brain accepts it.

Claude Code mitigates these by: re-reading after writing, asking "does this still hold given the new file I just read?", and explicitly downgrading confidence when the second read disagrees with the first. Our brain doesn't do any of these.

---

## Decision

Insert a **VerifierLoop** between Stage 2.5 (cross-file pass, ADR-0055) and Stage 3 (BusinessContext synthesis). Its job: re-read source for every emitted claim and either CONFIRM, DOWNGRADE confidence, FLAG for human review, or DROP.

### Three verifier modes (cheap → expensive)

**Mode A — Deterministic verifier (no LLM)**

For each entity with a `query_text`, `code_snippet`, or `evidence` field:
1. Read the source file at the claimed line range.
2. Substring-match the claim. If exact match → confirm.
3. If fuzzy-match (Levenshtein < 5%) → confirm with `verified="fuzzy"`.
4. If no match → mark `verified="hallucinated"`, drop from public reads (still in DB for telemetry).

Cost: $0. Catches ~80% of hallucinations.

**Mode B — Sub-agent verifier (cheap LLM, batched)**

For entities flagged by Mode A as `verified="fuzzy"` or `verified="hallucinated"`:
1. Spawn a Haiku sub-agent with the entity's claim + the actual source range.
2. Prompt: "Does claim X hold in source Y? Answer YES/NO/PARTIAL with one-line reason."
3. Update entity's `verified` and `confidence` based on result.

Cost: $0.001 per ambiguous entity. ~10% of entities reach this mode.

**Mode C — Self-correction loop (the killer feature)**

When Mode B flags an entity as wrong AND the original extraction had high confidence (≥0.8), we don't just downgrade — we **re-extract**.

The original ContextAgent is invoked AGAIN, but with augmented context:
```
<retry_context reason="prior_extraction_disputed_by_verifier">
  Original extraction claimed: X
  Verifier disputed because: Y
  Source range to re-examine: file:line_a-line_b
  Adjacent context: file:line_a-50 to line_b+50
</retry_context>
```

The retry's output replaces the original. If it ALSO disagrees with the verifier, the entity is marked `verified="conflicting"` and surfaced in the verification report for human review.

Cost: ~$0.005 per disputed entity. ~2% of entities reach Mode C.

### Self-correction trigger criteria

Mode C fires when ALL of these hold:
- Original confidence ≥ 0.8 (the LLM was confident, so disagreement is meaningful)
- Verifier disagrees explicitly (not "uncertain", an actual NO)
- Entity is "high-stakes" — `query_text`, `READS_COLUMN`, `WRITES_COLUMN`, `CALLS` to an external service, or any annotation-driven security claim

### Where the verifier writes

Each entity gets new fields:

```python
@dataclass
class ExtractedEntity:
    # ... existing fields ...
    verified: Literal["confirmed", "fuzzy", "hallucinated", "conflicting", "skipped"] = "skipped"
    verifier_mode: Literal["deterministic", "subagent", "self_correction"] | None = None
    verifier_notes: str = ""
```

`/query` filters: by default returns only `verified IN ('confirmed', 'fuzzy', 'skipped')`. Hallucinated and conflicting entities are excluded unless `--include-unverified` is passed.

---

## File ownership for THIS PR (parallel-safe)

```
company-brain-ai/src/companybrain/pipeline/verifier_loop.py            # NEW — orchestrator
company-brain-ai/src/companybrain/pipeline/verifier_deterministic.py   # NEW — Mode A
company-brain-ai/src/companybrain/agents/verifier_agent.py             # NEW — Mode B sub-agent
company-brain-ai/src/companybrain/pipeline/self_correction.py          # NEW — Mode C loop
tests/unit/test_verifier.py                                              # NEW
tests/acceptance/test_verifier_drops_hallucinated_lob.py                 # NEW
```

Append-only edits to:

```
company-brain-ai/src/companybrain/models/entities.py    # add verified/verifier_mode/verifier_notes fields
company-brain-ai/src/companybrain/pipeline/orchestrator.py  # invoke verifier_loop after stage 2.5
company-brain-ai/src/companybrain/api/routes/query.py   # filter by verified status
```

Does NOT touch any file owned by ADR-0055/57/58/59/60.

---

## Acceptance test

```python
async def test_hallucinated_query_text_dropped():
    """Inject an entity with a fake query_text. Verifier Mode A must drop it."""
    fake = ExtractedEntity(
        entity_type="Method", name="getPayerCompetitors",
        file="apps/.../CompetitivenessPlanRepository.java",
        query_text="SELECT * FROM nonexistent_table WHERE never_in_source = 1",
        confidence=0.9,
    )
    result = await run_verifier([fake])
    assert result[0].verified == "hallucinated"


async def test_self_correction_fires_on_high_confidence_dispute():
    """High-confidence entity disputed by verifier triggers re-extraction."""
    # Setup: extract fixture; corrupt one entity's query_text post-hoc
    # Run verifier; assert Mode C fired (count_invocations) and the
    # entity's query_text is now correct.
    pass


async def test_query_excludes_unverified_by_default():
    """A hallucinated entity must NOT appear in /query responses unless flag is set."""
    response = await curl_query("what does getPayerCompetitors do?")
    sources = response["sources"]
    assert all(s["verified"] in ("confirmed", "fuzzy") for s in sources)
```

---

## Effort estimate

2 days. Mode A is pure Python + grep. Mode B is one new sub-agent. Mode C reuses the existing ContextAgent.

---

## Action items

1. [ ] `pipeline/verifier_deterministic.py` — substring + Levenshtein matching against source.
2. [ ] `agents/verifier_agent.py` — Haiku sub-agent with the YES/NO/PARTIAL prompt.
3. [ ] `pipeline/self_correction.py` — re-extraction path with retry context.
4. [ ] `pipeline/verifier_loop.py` — orchestrator: A → B → C; write `verified` field.
5. [ ] `models/entities.py` — append fields.
6. [ ] `query.py` — filter clause; expose `?include_unverified=true` flag.
7. [ ] Telemetry: per-run `verifier.confirmed_count`, `verifier.dropped_count`, `verifier.self_correction_fires`.
8. [ ] Acceptance tests — hallucination drop, self-correction fire, query filtering.
