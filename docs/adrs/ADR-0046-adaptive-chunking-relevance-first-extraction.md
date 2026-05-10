# ADR-0046 — Adaptive Chunking + Relevance-First Extraction

**Status:** Accepted
**Date:** 2026-05-10
**Deciders:** Chinmay (product), pipeline-team
**Supersedes:** parts of ADR-0044 (uniform per-method chunking)
**Builds on:** ADR-0042, ADR-0043, ADR-0044, ADR-0045

---

## Context

After ADR-0044's chunk queue and ADR-0045's read-from-disk fix, three new
problems appeared in the actual run trace:

1. **Both paths run in parallel.** The chunk-queue extractor AND the legacy
   per-file `EntityExtractor` both fired for the same files. Root cause: a
   `UnboundLocalError` on `extractor._deduplicate()` inside the chunk-queue
   `try:` block (extractor was created *after* this call) caused a silent
   fallback to the legacy path every time. Cost: double-charged.
2. **Per-method-per-call is needlessly expensive for small methods.** A
   typical Java service has 5-10 trivial methods (~100-300 chars each)
   alongside 1-2 fat ones. Spending 1 full LLM call per trivial method
   costs ~$0.0005 × 10 = $0.005 per file just for boilerplate.
3. **Irrelevant methods still reach the LLM.** The chunk path didn't apply
   ANY relevance filter — every method in every navigated file became a
   chunk, including Lombok-generated, Object overrides, and pure delegations.

---

## Decision

Three coordinated changes:

### D1 — Kill the parallel paths

Moved `extractor = EntityExtractor()` (and L2/CM-Agent setup) to **before**
the `if _use_chunk_queue:` block so the `_deduplicate()` call inside the
chunk-queue `try:` clause always succeeds. The legacy `EntityExtractor`
extraction loop (lines 516+) was already guarded by `if not _skip_extraction`
and remains guarded. The two paths are now truly mutually exclusive.

**Files changed:** `pipeline/orchestrator.py`

### D2 — Adaptive chunk sizing per file shape

New `pipeline/chunk_strategy.py` with three strategies:

| File size         | Strategy        | LLM calls         |
|-------------------|-----------------|--------------------|
| < 4 000 chars     | WHOLE_FILE      | 1 per file         |
| 4 000–15 000 chars | BATCHED_METHODS | 1 per group of ≤8 methods |
| > 15 000 chars    | PER_METHOD      | 1 per method       |

Within BATCHED_METHODS, methods ≥ 500 chars go solo; smaller methods are
grouped into batches of up to `MAX_METHODS_PER_BATCH` (default 8).

`MethodChunk` gains a `strategy: str` field. `CodeChunker.chunk_unit()` now
applies the strategy router and emits `kind="whole_file"` or `kind="batch"`
chunks in addition to existing `kind="method"`.

`ChunkExtractor.extract()` dispatches on `chunk.strategy`:
- `per_method` → original focused single-method prompt (`MAX_TOKENS=600`)
- `batched_methods` → new `_BATCHED_SYSTEM_PROMPT`, keyed by method name (`MAX_TOKENS=2000`)
- `whole_file` → new `_WHOLE_FILE_SYSTEM_PROMPT` (`MAX_TOKENS=3000`)

Batch and whole-file responses return `{"methods": {"ClassName.method": {"entity": ..., "edges": [...]}}}`.
`ChunkResult` gains `entities: list[ExtractedChunkEntity]` and `all_entities()` helper.
`collect_entities_and_edges()` in `worker.py` uses `all_entities()`.

**Files changed:**
- `pipeline/chunk_strategy.py` (new)
- `pipeline/code_chunker.py`
- `pipeline/chunk_extractor.py`
- `pipeline/worker.py`
- `pipeline/queue.py` (strategy column in ChunkInput/QueueChunk/SQL)
- `db/migration/V11__adaptive_chunking.sql` (new)

### D3 — Relevance-first chunk filtering

New `pipeline/chunk_relevance_filter.py` with `filter_chunks()` that runs
after chunking and **before** `enqueue()`. Filtered chunks are not sent to
the LLM and are not enqueued.

Static filters (deterministic, no LLM):
1. **Lombok-trivial** — `@Data`/`@Getter`/`@Setter` header + getter/setter/equals/hashCode/toString/builder method name
2. **Object-method overrides** — `@Override` of toString/equals/hashCode/clone/finalize
3. **Empty or stub bodies** — empty braces or `throw new UnsupportedOperationException()`
4. **Pure delegations** — single-line `return this.field;` / `return delegate.method(args);` / one-line setter
5. **@Deprecated** methods
6. **@Test** methods (handled by TESTED_BY pass, ADR-0042 E7)

Batch/whole-file chunks bypass method-level filtering (kind check).

Telemetry logged in orchestrator:
- `strategy_counts` — per-strategy chunk count before filtering
- `filter_reasons` — count per filter reason after filtering

**Files changed:**
- `pipeline/chunk_relevance_filter.py` (new)
- `pipeline/orchestrator.py`

---

## Cost Picture

For a 110-entity Java run (network-iq-backend-java lob test):

| Path                          | LLM calls | Est. cost |
|-------------------------------|-----------|-----------|
| Before (both paths in parallel)| ~250     | ~$0.30    |
| D1 only (kill legacy)         | ~130      | ~$0.15    |
| D1 + D2 (adaptive sizing)     | ~50       | ~$0.07    |
| D1 + D2 + D3 (relevance)      | ~25       | ~$0.04    |

---

## Consequences

**Easier**
- Run cost drops 5-7x (≈ $0.30 → $0.04 per typical Java run).
- Small files get a single coherent extraction instead of many uncorrelated per-method calls.
- Relevance filtering is transparent: `filter_reasons` log shows what was dropped and why.
- Operators can grep `relevance_skipped=true` rows in `extraction_queue` to audit filtering.

**Harder**
- BATCHED_METHODS prompt engineering: the system prompt must enforce one entity per method; the parser splits by method name key.
- WHOLE_FILE mode reintroduces output-truncation risk for files near the 4000-char threshold; max_tokens capped at 3000 (we learned this lesson from ADR-0044).
- Strategy thresholds are initial guesses. A `strategy_chosen` log field on every `extraction_chunk` event enables A/B tuning.

**To revisit**
- The 4k / 15k / 500-char thresholds — tune based on `extraction_chunk` telemetry.
- Filter false-negatives: add `--include-filtered` CLI flag for ops to bypass D3 on a single run when investigating.
- `@Deprecated`-method filter may be too aggressive for repos that haven't cleaned up yet.
