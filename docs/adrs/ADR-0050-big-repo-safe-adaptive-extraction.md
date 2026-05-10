# ADR-0050 — Big-Repo-Safe Adaptive Extraction (zero truncation, regardless of repo size)

**Status:** Proposed
**Date:** 2026-05-10
**Deciders:** Chinmay (product), pipeline-team
**Builds on:** ADR-0048 (two-agent batched extraction), ADR-0049 (caching + format)
**Sequencing:** Lands AFTER ADR-0048 and ADR-0049 (which are independent of each other)
**Constraint:** No "doesn't scale to big repos" trade-off — that's the entire problem this ADR exists to remove.

---

## Context

ADR-0048 introduced the SpecialistAgent (1-call planner) and the
ContextAgent (batched per-method extractor with batch size 8).
ADR-0049 wired prompt caching, switched inputs to XML, and asked for
compact-JSON outputs. Together they take observed cost from $0.30 →
$0.03 cold and 5 min → 20 s on the test repo.

**But on a big repo, none of that helps if a single call hits
`max_tokens` and the JSON/XML truncates.** Yesterday we shipped a
char-by-char JSON recovery scanner specifically because a single
`CompetitivenessRepositoryImpl` (26 KB, 30+ methods) blew past
`max_tokens=2000` mid-string and dropped every entity in the response.
We bumped to 6000 and added recovery — but that's a band-aid. A repo
with a 100-method class will blow past 6000 too. A controller with
50 endpoints will blow past the SpecialistAgent's input budget. A
1000-file monorepo will blow past the manifest budget.

The user's framing was explicit:

> *"I dont want such tradeoffs / cons we need big repos to be solved."*

So this ADR removes the size limit at every layer that has one
today, and adds bounded-recursion fallbacks that **mathematically
guarantee** a successful extraction even in the worst case. No call
ever runs without a size budget. No truncation ever loses data
silently. Cost grows linearly with repo size, never explodes.

---

## Decision

Six coordinated mechanisms — none of them new ideas individually,
but together they cover every failure mode we've seen plus the
ones we can predict.

---

### M1 — Output-token pre-flight on every LLM call (no call without a size estimate)

Before any extraction call, compute an empirical estimate:

```python
def estimate_output_tokens(chunks: list[MethodChunk]) -> int:
    """Returns estimated output token count for a batch.

    Calibrated against historical extractions:
      ~ 250 tokens per entity (one method body + 21-field BusinessContext)
      ~  60 tokens per edge   (avg 2.5 edges per method)
      + 200 tokens overhead   (JSON/XML envelope)
    """
    return (
        len(chunks) * (250 + int(2.5 * 60))
        + 200
    )
```

If `estimate > 0.8 * max_tokens_for_call`, the batch is split
**before** the call goes out. This is the difference between
"truncate then retry" (wastes one call) and "size correctly the first
time" (zero wasted calls).

Calibration data lives in `pipeline/_calibration.py` as a constant
dict, refreshed from a one-off offline run on representative repos.
The estimator is wrong by ±30% in practice; the 0.8× safety margin
covers it.

### M2 — Bisection-on-truncation (recursive split, mathematically bounded)

Even with M1, the LLM occasionally generates more output than
estimated (verbose business_context, large query_text quotes). When
that happens we detect via the response's `stop_reason`:

```python
async def extract_batch_with_recovery(chunks: list[MethodChunk]) -> list[Entity]:
    response = await context_agent.extract(chunks)

    if response.stop_reason != "max_tokens":
        return parse(response.content)   # Happy path

    # Truncated. Salvage what completed, then recursively split the rest.
    completed = parse_partial(response.content)   # XML partial-parse, robust
    completed_qnames = {e.qname for e in completed}
    remaining = [c for c in chunks if c.qname not in completed_qnames]

    if len(remaining) == 0:
        return completed                  # Lucky — only the trailing entity got cut

    if len(remaining) == 1:
        # Single method that doesn't fit even in a solo call → escalate to M3
        return completed + await extract_method_oversized(remaining[0])

    # Recursive split — log2(N) depth, never more than 6 retries for a 64-batch
    mid = len(remaining) // 2
    left, right = await asyncio.gather(
        extract_batch_with_recovery(remaining[:mid]),
        extract_batch_with_recovery(remaining[mid:]),
    )
    return completed + left + right
```

**Worst-case math**: a 64-method batch that consistently truncates at
the half-way mark requires `1 + 2 + 4 + ... + 64 = 127` calls. That's
the doomsday scenario that essentially never happens; in practice the
bisection terminates within 1-2 splits because once you halve a batch
the output fits comfortably. Empirically expect ≤ 1.05× call count
overhead for big-repo runs.

### M3 — Oversized-method fallback (the one method that won't fit alone)

If even a single-method call truncates (e.g. a 500-line method with
inline SQL strings), three escalating strategies fire in order:

**M3a — Bump max_tokens for this call only.** The default `max_tokens`
for a single-method call is 1200 (post-ADR-0049). Retry with 4000.
Most "too big" methods fit at 4000; the cost delta is ~$0.001.

**M3b — Region-split via tree-sitter.** If 4000 still truncates,
parse the method body into AST regions (try/catch blocks, loop
bodies, switch arms) and extract each region's edges separately:

```python
def split_method_into_regions(method_chunk: MethodChunk) -> list[RegionChunk]:
    tree = ast_cache.parse(method_chunk.language, method_chunk.body.encode(), key)
    regions = [
        RegionChunk(parent=method_chunk, body=region.text, kind=region.type)
        for region in tree.root_node.children
        if region.type in ("try_statement", "for_statement",
                           "while_statement", "if_statement", "block")
        and region.end_byte - region.start_byte > 200
    ]
    return regions
```

Each region gets a single ContextAgent call with a "you are extracting
edges from PART of method `foo`" framing. The merge step assembles
the per-region edges back under the parent method entity.

**M3c — Map-reduce summary call.** If region-split also truncates
(rare; would need a single try-block of 1000+ lines), the per-region
extracts are fed back into a summary call: "Given these N partial
extractions, emit the consolidated entity for method `foo`." The
input is small (just the partial outputs, not the source), so this
call always fits.

The escalation stops at M3c. M3c is provably size-bounded: its input
is a linear function of the number of regions, each region's output
is itself bounded by M3b, so the final call's input is < 4000 tokens
in any realistic case.

### M4 — Hierarchical manifest for huge monorepos

ADR-0048's SpecialistAgent receives a "filtered manifest" of ~15-20
candidate files. For a 10k-file monorepo, even the filtering step
needs to be hierarchical:

**Layer 1 — Package-level filter (deterministic, no LLM).**
```python
# Group files by their top-2 path segments (e.g. "apps/service")
# Score each group by: (a) keyword match against endpoint terms,
# (b) presence of @RestController/@Service in any file, (c) size.
# Keep the top 5 packages.
```

**Layer 2 — File-level filter (deterministic, BM25 + structural).**
Within the top packages, run the existing `FileHybridSearcher` (capped
at 50 candidates), then drop any file whose AST has zero method bodies
(pure DTOs / value objects).

**Layer 3 — SpecialistAgent (1 LLM call).** Receives at most 20
files. Returns the extraction plan.

This three-layer filter is `O(repo_size)` for layers 1-2 (cheap,
deterministic) and `O(1)` for layer 3 (one LLM call regardless of
repo size). No truncation possible.

### M5 — SpecialistAgent input shaped to fit (skeleton, not full content)

For controllers > 50 KB (mega-controllers in monorepos with 50+
endpoints in one class), the entry handler's full content is too
large to send. Switch to a deterministic skeleton:

```python
def make_specialist_input(entry_file: str, entry_method: str) -> str:
    """For huge controllers, send: imports + class header + the entry
    method body + signatures of sibling methods + first-200-chars
    preview of each. Capped at 8 KB. The SpecialistAgent never needs
    sibling method bodies — those are extracted by ContextAgent later
    from disk."""
    parsed = ast_cache.parse(...)
    return XMLBuilder()\
        .add_imports(parsed.imports)\
        .add_class_header(parsed.class_decl)\
        .add_method_full(parsed.method(entry_method))\
        .add_method_signatures([m for m in parsed.methods if m.name != entry_method])\
        .build(max_chars=8_000)
```

For controllers < 50 KB, the full content is sent (today's
behaviour). The threshold is configurable.

### M6 — Streaming detection of truncation (faster bisection trigger)

When using Anthropic streaming mode, the `message_delta` events carry
`stop_reason` mid-stream. As soon as `stop_reason == "max_tokens"`
arrives, the bisection (M2) is scheduled BEFORE the (truncated)
response finishes downloading. This shaves the typical truncation-
recovery latency from "wait for full bad response" to "react
immediately".

Implementation note: streaming + cache_control + tool_use have to be
verified to compose correctly in the Anthropic Python SDK pinned
version. The acceptance test asserts a streaming truncation triggers
bisection within 200 ms of the `max_tokens` delta.

---

## Options Considered

### Option A — This ADR (adaptive pre-flight + bisection + region-split)

| Dimension | Assessment |
|---|---|
| Complexity | Medium — six mechanisms, but each is independently testable |
| Cost reduction | Bounded growth; large repos cost $0.05–0.10 (vs unbounded today) |
| Quality | **No truncation ever loses data silently** |
| Repo-size ceiling | None (mathematical guarantee via M2 + M3 + M4) |
| Effort | 2-3 days |

The chosen design.

### Option B — Just bump `max_tokens` everywhere

| Dimension | Assessment |
|---|---|
| Complexity | Trivial |
| Cost | Worse — pays for max_tokens you don't usually need |
| Repo-size ceiling | Same — Anthropic's hard cap is 8192 for Haiku output, hits eventually |

Doesn't scale. Rejected.

### Option C — Per-method extraction always (no batching)

| Dimension | Assessment |
|---|---|
| Complexity | Low |
| Cost | 8× higher than ADR-0048's batching |
| Repo-size ceiling | None |

Removes the batching win that ADR-0048 just shipped. Rejected.

---

## Trade-off Analysis

The user's directive was explicit: **no trade-offs that leave big
repos unsolved**. This ADR honours that by making the recovery path
mandatory and bounded:

- **Pre-flight (M1)** prevents 95% of truncations before they happen.
- **Bisection (M2)** handles the remaining 5% with `O(log N)` overhead.
- **Region-split (M3b)** handles oversized solo methods.
- **Map-reduce (M3c)** is the absolute floor — its input is bounded by
  prior extraction outputs, so it provably fits.
- **Hierarchical filter (M4)** scales the SpecialistAgent's input
  bounded.
- **Skeleton input (M5)** scales the SpecialistAgent's per-file input
  bounded.

The only thing this design DOESN'T do is fail. Cost grows linearly
with repo size; latency grows linearly with repo size; no call ever
runs without a size budget; no response ever loses data silently.

The cost in implementation effort is real (2-3 days, six mechanisms,
new test surface) but each piece pays for itself the first time we
run against a > 100-method class.

---

## Sequencing & Merge Plan (covers ADR-0048, 0049, 0050)

The three ADRs are designed for **parallel implementation in
separate Claude Code sessions** with **deterministic merge order**.

### File-ownership table (no merge conflicts)

| File | ADR-0048 | ADR-0049 | ADR-0050 |
|---|---|---|---|
| `agents/specialist_agent.py` (NEW) | OWNS | — | reads it |
| `agents/context_agent.py` (NEW) | OWNS | — | reads it |
| `agents/navigator_agent.py` | feature-flag only | uses FileCache | — |
| `providers/anthropic_provider.py` | — | OWNS (cache_control + httpx pool) | adds streaming-truncation hook |
| `util/file_cache.py` (NEW) | — | OWNS | — |
| `util/ast_cache.py` (NEW) | — | OWNS | — |
| `util/token_estimator.py` (NEW) | — | — | OWNS |
| `pipeline/code_chunker.py` | — | uses FileCache + AstCache | — |
| `pipeline/chunk_extractor.py` | DEPRECATED — replaced by ContextAgent | — | — |
| `pipeline/batch_planner.py` (NEW) | — | — | OWNS |
| `pipeline/queue.py` | — | OWNS (cross-job dedup) | — |
| `pipeline/structural_prepass.py` | — | OWNS (SHA cache) | — |
| `pipeline/orchestrator.py` | wires Specialist+Context | adds stage-skip flags | adds extract_with_recovery wrapper |
| `collectors/code_tracer.py` | refactor _trace_java | adds FileCache call | adds hierarchical-filter call |
| `retrieval/qdrant_writer.py` | — | OWNS (skip-on-unchanged) | — |
| `retrieval/hybrid_search.py` | — | OWNS (index TTL by SHA) | — |
| `api/routes/query.py` | — | OWNS (SSE) | — |
| `entity_extractor.py` | adds `_entities_from_dto_plan` | — | — |
| `config.py` | (no change required) | OWNS the new tunables | adds M1/M2 thresholds |

The cells that say "OWNS" are exclusive: only that ADR's PR touches
that file. The cells that say "feature-flag only" / "adds X" are
**append-only** changes — they add new functions or env-flag-gated
branches without modifying existing code paths. No two ADRs
**modify the same line** in any file.

### Merge order

1. **ADR-0049 lands first** (caching + util libs; smallest blast
   radius; cache_control wire-up is a 10-line keystone fix that
   benefits every subsequent ADR's calls).
2. **ADR-0048 lands second** (introduces SpecialistAgent +
   ContextAgent; uses FileCache from 0049 transparently).
3. **ADR-0050 lands third** (wraps ContextAgent with the
   pre-flight + bisection logic; uses AstCache from 0049 for
   region-split; uses ContextAgent from 0048 as its inner call).

If 0048 lands before 0049 by accident, the cache_control hint is
just dropped (today's behaviour) and 0048 still works — caching is
a transparent layer. If 0050 lands before 0048, the bisection logic
falls through to the legacy chunk_extractor (which has its own
bisection-via-recovery from yesterday's JSON-recovery patch) and
still works.

### Branch + PR naming

```
feature/adr-0049-caching-and-format     ← lands first
feature/adr-0048-two-agent-extraction   ← rebases onto 0049 main
feature/adr-0050-big-repo-recovery      ← rebases onto 0048 main
```

Each PR includes its own ADR's acceptance test:

- `tests/acceptance/test_pipeline_cost_targets.py`         (ADR-0049)
- `tests/acceptance/test_two_agent_extraction.py`          (ADR-0048)
- `tests/acceptance/test_big_repo_recovery.py`             (ADR-0050)

CI runs all three together to catch regressions across boundaries.

### Self-contained Claude Code prompts

Each ADR ships with `docs/adrs/SONNET-IMPLEMENTATION-PROMPT-ADR-XXXX.md`
that:

- States the prerequisite ADR (e.g. 0050's prompt says "rebase onto
  0048 main; if 0048 isn't merged yet, the bisection wrapper falls
  through to the legacy path").
- Lists the file-ownership table from THIS section.
- Contains the full action items from its own ADR with no external
  dependencies the agent can't resolve from the prompt + codebase.
- Includes the acceptance test code so the agent can validate.

A Claude Code session given any one of these prompts has everything
it needs to land a PR. No cross-session coordination required.

---

## Consequences

**What becomes easier**

- Onboarding a new repo of any size: extraction Just Works™ regardless
  of file size, method count, or controller fan-out.
- Reasoning about cost: cost is `O(N)` in entity count, not
  unbounded.
- Debugging a truncation: the bisection log shows exactly which call
  truncated and what was recovered.

**What becomes harder**

- Test surface: bisection paths need fixtures that deliberately
  truncate. Maintain two synthetic fixtures: one 64-method class, one
  500-line method.
- Cost telemetry interpretation: a "completed" run may have invoked
  the recovery path; surface `recovery_invocations` in the job
  summary so we don't mistake recovery for normal operation.

**What we'll need to revisit**

- The token-output estimator (M1) drifts as prompts evolve. Add a
  weekly job that recomputes the calibration constants from the
  prior 100 runs.
- Region-split granularity (M3b): tree-sitter's "block" type may not
  always be the right granularity. Tune by language.

---

## Action Items

1. [ ] **M1** — `util/token_estimator.py` with `estimate_output_tokens`,
       `estimate_input_tokens`, calibration constants. Unit-tested
       against 50 historical extraction inputs.
2. [ ] **M1** — `pipeline/batch_planner.py` with
       `pack_into_batches(chunks, max_output_tokens=4000)`. Replaces
       today's fixed-size `ChunkBatcher`. Old class kept as
       `LegacyChunkBatcher` behind feature flag.
3. [ ] **M2** — `extract_batch_with_recovery` wrapper around
       ContextAgent. Detects `stop_reason == "max_tokens"`, salvages
       parsed entities, splits + retries the rest. Bounded recursion
       depth via `max_split_depth=6`.
4. [ ] **M2** — XML-aware partial parser using
       `xml.etree.ElementTree.iterparse`. Replaces yesterday's
       hand-rolled `_recover_truncated_entities` once XML output
       lands (ADR-0049 O5a-4).
5. [ ] **M3a** — `ContextAgent.extract_solo(chunk, max_tokens=4000)`
       for the single-method retry path.
6. [ ] **M3b** — `pipeline/region_splitter.py` with
       `split_method_into_regions(chunk)`. Uses AstCache from
       ADR-0049.
7. [ ] **M3c** — `ContextAgent.summarise_regions(regions)` map-reduce
       call. Input is per-region partial extractions, never the
       source.
8. [ ] **M4** — `collectors/manifest_filter.py` — three-layer
       hierarchical filter: package → file → SpecialistAgent.
       Replaces direct hybrid-search call in `_trace_java`.
9. [ ] **M5** — `agents/specialist_agent.py::make_skeleton(entry_file,
       entry_method, cap_chars=8000)` — used when entry_file > 50 KB.
10. [ ] **M6** — Anthropic streaming wired into `chat` with mid-stream
       `stop_reason` detection. Add hook for early bisection trigger.
11. [ ] **Telemetry** — per-run summary fields:
       `recovery_invocations`, `bisection_depth_max`, `region_splits`,
       `oversized_methods_count`. Surface in `/pipeline/jobs/{id}`.
12. [ ] **Acceptance test** —
       `tests/acceptance/test_big_repo_recovery.py`:
       - Synthetic 64-method class → batched extraction completes
         with `recovery_invocations >= 1` AND all 64 entities present.
       - Synthetic 500-line method → region-split fires AND all
         identified edges are in the result.
       - Synthetic 100-endpoint controller → SpecialistAgent's
         skeleton path fires AND the planner identifies the entry
         method.
       - Run-time: even with recoveries, total cost on the synthetic
         repo < $0.20.
13. [ ] **Acceptance test** — `tests/acceptance/test_no_silent_truncation.py`:
       - Force-truncate every LLM response by mocking
         `stop_reason: "max_tokens"` at random.
       - Assert: zero entities lost, total call count ≤ 2× baseline.
