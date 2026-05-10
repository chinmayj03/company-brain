# ADR-0046 — Adaptive Chunking Strategies and Relevance Filtering

**Status:** Superseded by ADR-0047
**Date:** 2026-05-09
**Superseded by:** ADR-0047 (Unified Chunked Extraction)

---

## Context

ADR-0044 introduced per-method chunking. A follow-on design (this ADR, open as PR #42)
proposed three extraction *strategies*: WHOLE_FILE, BATCHED_METHODS, and PER_METHOD,
selected per-file based on language and file size.

Additionally, PR #42 introduced a relevance filter with Java-specific class name checks
(`_should_skip_class` with `ResponseEntity`, `Pageable`, `HttpStatus`, `"Entity"` suffix
and `"Mapping"` suffix) intended to reduce LLM calls on boilerplate.

## Why This ADR Is Superseded

Two design conflicts with ADR-0045 (PR #41) were identified after both PRs were open:

1. **WHOLE_FILE mode re-introduces truncation.** ADR-0045 explicitly prohibits passing
   pre-read file content to the LLM; WHOLE_FILE mode would send the raw file bytes in
   one call, defeating the structural fix in ADR-0045. Sending entire large files to an
   LLM context window is the exact problem ADR-0044 was created to solve.

2. **Java-specific class name filters are not language-agnostic.** Terms like
   `ResponseEntity`, `Pageable`, `HttpStatus`, `"Entity"` suffix, and `"JsonKeyMapping"`
   are Spring Boot/JPA idioms. They would incorrectly filter or mis-classify Python,
   TypeScript, Go, or Rust classes that share those suffixes by coincidence.

## Decision

Both PRs were closed and their valid elements were unified into ADR-0047:

- The batching concept from this ADR was kept and generalised into `ChunkBatcher`
  (small adjacent same-class chunks, up to 8 per batch, < 800 chars each).
- The relevance filter concept was kept but made language-agnostic (structural pattern
  matching only — empty bodies, trivial accessors, `equals`/`hashCode` names).
- WHOLE_FILE mode was dropped entirely.
- Language-specific class name checks were removed from `_UTILITY_OBJECTS` and
  `_SKIP_SUFFIXES` in `NavigatorAgent`.

See ADR-0047 for the unified design.
