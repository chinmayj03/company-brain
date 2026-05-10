# ADR-0047 — Unified Language-Agnostic Adaptive Chunking

**Status:** Accepted
**Date:** 2026-05-10
**Deciders:** Chinmay (product), pipeline-team
**Supersedes:** ADR-0045 (Chunker reads files directly), ADR-0046 (Adaptive strategies)
**Implements:** PR — `feat(extraction): unified language-agnostic adaptive chunking`

---

## Context

Two open PRs proposed complementary but conflicting designs:

- **PR #41 (ADR-0045):** Chunker reads files directly from disk; `CodeUnit` carries a
  path, not content; defensive assert on truncated content at 6019 chars.
- **PR #42 (ADR-0046):** Three extraction strategies (WHOLE_FILE / BATCHED_METHODS /
  PER_METHOD) + relevance filter with Java-specific class name checks.

The conflict: WHOLE_FILE mode in PR #42 re-introduces the exact truncation PR #41 fixed.
The Java-specific relevance filter in PR #42 cannot be applied to Python/TS/Go/Rust repos.

Both PRs were closed. This ADR captures the unified design that replaces both.

---

## Decisions

### U1 — CodeUnit carries no file content; chunker reads from disk

`CodeUnit` no longer has a `content: str` field. It carries `file_path`, `repo_name`,
`role`, `class_name`, `language`, `discovery_reason`, and `relevance_score`.

A lazy `@property content` exists solely as a backward-compat shim for the
`BRAIN_LEGACY_EXTRACT` path; it reads the file on first access and caches the result.
All new code uses `unit.file_path` directly.

`CodeChunker.chunk_file(fp)` reads the full file with
`Path(fp).read_text(errors="ignore")` and raises `TruncatedContentError` if it receives
content that matches the known-truncation sentinel (len == 6019 or ends with "(truncated)"
and len < 8000). Loud failure beats silent data loss.

### U2 — Always chunk via tree-sitter; group small siblings via ChunkBatcher

WHOLE_FILE extraction mode is **permanently removed**. Every file goes through the
tree-sitter chunker regardless of language or size.

`ChunkBatcher` groups the resulting `MethodChunk` list into `ChunkBatch` objects:

- **Small chunk:** body < 800 characters.
- **Grouped batch:** up to 8 consecutive small chunks from the same class.
- **Solo batch:** any large chunk (body ≥ 800 chars) gets its own batch.
- **Class boundary:** the class changes → flush the current group.

A batched call sends N method bodies to the LLM in one request and expects a JSON array
of N entities back. This reduces LLM calls and cost for getter-heavy or utility classes.

### U3 — Legacy extraction path is gated; not removed yet

`BRAIN_LEGACY_EXTRACT=true` still routes to the old per-file EntityExtractor path.
The default is `false`. The legacy path will be deleted once the chunk-queue path is
confirmed stable in production (post-ADR-0044).

### U4 — Three-tier language-agnostic relevance filter

`ChunkRelevanceFilter` runs before enqueueing. All three tiers are deterministic and
language-agnostic.

**Tier 1 — AST-pattern triviality (no LLM):**
Drop chunks whose body matches trivial patterns:
- Empty body (only braces, `pass`, or whitespace)
- Single-field getter/setter (body ≤ 4 non-blank lines + accessor pattern)
- Known boilerplate method names: `equals`, `hashCode`, `toString`, `__eq__`,
  `__hash__`, `__repr__`, `__str__`, `compareTo`
- Super-only delegation (`super()` alone in the body)

**Tier 2 — Reachability BFS (optional; uses existing graph):**
When the orchestrator has a reachability set from the navigator's import-graph traversal,
chunks whose `qname` is not reachable from the entry-point are dropped.
If no reachability set is available, this tier is skipped.

**Tier 3 — LLM manifest screen (not in this module):**
A future step may run one LLM call per file to produce a short manifest of "interesting
methods," allowing a third filtering pass before the per-method queue. Not implemented
in this ADR; noted here for completeness.

Filtered chunks are inserted into `extraction_queue` with `status='filtered'` and a
`filter_reason` string for telemetry.

### U5 — Telemetry with language/strategy/batch_size/filter_reason

Every chunker run emits `chunker.read_file_directly path=… len=…` at DEBUG.

Every ChunkExtractor call emits:
- `language` — detected from file extension via `_LANGUAGE_MAP`
- `strategy` — `"single"` or `"batched"`
- `batch_size` — number of chunks in the batch
- `filter_reason` — non-empty for filtered chunks

`drain_queue.complete` now includes `filtered` in its stats breakdown.

---

## Consequences

**What becomes easier**

- No file truncation anywhere in the pipeline — the field literally doesn't exist.
- Full method bodies reach the LLM regardless of file size or position in the file.
- Adding a new language requires only adding an entry to `_LANGUAGE_MAP` in
  `code_chunker.py`; no strategy selection code changes.
- The relevance filter is safe to apply to any language without false positives from
  Java-specific terms.

**What becomes harder / must be monitored**

- Disk I/O moves to the chunker. On cold runs this adds milliseconds per file; the OS
  page cache amortises this on warm runs.
- The lazy `content` property on `CodeUnit` is a transitional shim. It must be removed
  when the legacy extract path is deleted.

---

## Files Changed

| File | Change |
|------|--------|
| `collectors/code_tracer.py` | `CodeUnit` — lazy property, no content field |
| `pipeline/code_chunker.py` | `chunk_file()` with disk read + assert; `_LANGUAGE_MAP` |
| `pipeline/chunk_batcher.py` | NEW — `ChunkBatch`, `ChunkBatcher` |
| `pipeline/chunk_relevance_filter.py` | NEW — `ChunkRelevanceFilter`, three tiers |
| `pipeline/chunk_extractor.py` | Batch-aware; language-agnostic prompts; telemetry |
| `pipeline/queue.py` | `filtered` status; `language`/`filter_reason` columns; `mark_filtered()` |
| `pipeline/worker.py` | Fix hardcoded `language="java"`; emit `language` in telemetry |
| `pipeline/orchestrator.py` | Fix NameError; integrate filter + batcher before enqueue |
| `agents/navigator_agent.py` | Remove Java-specific items from `_UTILITY_OBJECTS`/`_SKIP_SUFFIXES` |
| `db/migration/V11__extraction_queue_adr0047.sql` | `language`, `filter_reason` columns; `filtered` index |
| `docs/adrs/ADR-0045-chunker-reads-files-directly.md` | Status → Superseded |
| `docs/adrs/ADR-0046-adaptive-chunking-and-relevance.md` | NEW (tombstone for the closed PR) |

---

## Acceptance Criteria

1. A synthetic 120k-char file (1 class, 30 methods, 30 distinct columns referenced)
   produces 30 `extraction_queue` rows with `status='pending'` or `'done'`, none
   truncated.
2. All 30 distinct columns appear as `READS_COLUMN` edges after drain.
3. `chunker.read_file_directly len=120000` appears in the logs (not 6019).
4. Running against a Python repo (`.py` files) produces `language='python'` in queue
   rows and extractor telemetry, never `language='java'`.
5. Trivial `__init__(self)` / `equals()` / empty-body chunks appear in
   `extraction_queue` with `status='filtered'` and non-empty `filter_reason`.
