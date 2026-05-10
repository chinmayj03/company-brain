# ADR-0044 — Chunked Extraction, No Truncation

**Status:** Proposed  
**Date:** 2026-05-10  
**Authors:** pipeline-team  
**Supersedes:** portions of ADR-0010, ADR-0011 (entity extraction path)

---

## Context

The current extraction pipeline processes one `CodeUnit` (one file) per LLM call.
For small files (<2 000 chars) this works well. For large files (10k–100k chars)
two failure modes occur:

1. **Truncation.** The LLM context window is finite. Files are silently sliced
   with `content[:N]` before the call, dropping the tail.
2. **Output overflow.** An LLM call for a 500-line class asks for 20+ entities
   and 80+ edges in one response. The JSON is frequently cut mid-field.

Both failures are silent: the pipeline logs "done" but 40–60% of the knowledge
is missing. The lob-rename query test case confirms this: the `plan_info.lob`
column's `READS_COLUMN` edge never surfaces because the method containing it
falls in the truncated tail.

---

## Decision

Replace the per-file LLM call with a **per-method chunk queue**:

- A **code chunker** (`code_chunker.py`) uses tree-sitter grammars to split
  every file into individual method/declaration chunks, each with rich header
  context (class signature, fields, deduped imports).
- Chunks are written to an **extraction queue** (Postgres table) before any LLM
  call begins. Workers pull one chunk at a time, run a tiny focused LLM call
  (max_tokens=600), and write the result back.
- The pipeline is **resumable**: a killed worker resumes from the queue; a
  re-run with the same `body_hash` is a no-op.

The transition is gated on `BRAIN_USE_CHUNK_QUEUE` (default `false` until
PR-0044-6 flips it to `true`). The legacy path remains available via
`BRAIN_LEGACY_EXTRACT=true`.

---

## Implementation plan (6 PRs)

| PR | Scope | Files |
|----|-------|-------|
| 0044-1 | DB queue table + Python wrapper | `V10__extraction_queue.sql`, `pipeline/queue.py` |
| 0044-2 | Code chunker + MethodChunk model | `pipeline/code_chunker.py` |
| 0044-3 | Per-chunk extractor + lookup tool | `pipeline/chunk_extractor.py`, `pipeline/lookup_tool.py` |
| 0044-4 | Worker loop + orchestrator gate | `pipeline/worker.py`, `pipeline/orchestrator.py` |
| 0044-5 | Merger + cross-chunk resolution | `pipeline/merger.py` |
| 0044-6 | Flag flip + acceptance test | `config.py`, `tests/acceptance/` |

---

## Consequences

**Good:**
- Files of any size processed completely, no truncation.
- Each LLM call is bounded (max_tokens=600) — JSON never cut.
- Cost is predictable: ~$0.0005 per chunk on Haiku.
- Resumable: crash recovery at chunk granularity.
- Per-chunk telemetry queryable by `grep extraction_chunk`.

**Bad:**
- More DB rows per pipeline run (one per method).
- Slight latency increase for small files (queue overhead).
- Requires Postgres `gen_random_uuid()` (pg14+; already a requirement).

---

## Alternatives considered

- **Larger context window models**: Claude Sonnet supports 200K tokens but
  output truncation persists when asking for 80 edges; doesn't fix the
  output-side problem.
- **File-level streaming**: split the output JSON server-side. Rejected:
  adds LLM-provider-specific complexity and doesn't reduce cost.
