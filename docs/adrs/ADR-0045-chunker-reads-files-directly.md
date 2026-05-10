# ADR-0045 — Chunker Must Read Files Directly From Disk

**Status:** Proposed
**Date:** 2026-05-10
**Deciders:** Chinmay (product), pipeline-team
**Companion:** `SONNET-IMPLEMENTATION-PROMPT-ADR-0045.md`
**Builds on:** ADR-0044 (chunked extraction)
**Fixes:** the residual truncation observed AFTER ADR-0044 shipped

---

## Context

ADR-0044 introduced per-method chunking with the goal of making truncation
structurally impossible. After PR-0044-1 through PR-0044-6 merged, the
following symptom remained:

```
_assemble_chain: visited  class=CompetitivenessPlanRepository  source_len=6019
_assemble_chain: visited  class=CompetitivenessProvidersRepository  source_len=6019
_assemble_chain: visited  class=CompetitivenessAsyncService  source_len=6019
_assemble_chain: visited  class=PostgresFunctions  source_len=6019
```

Six different files reporting the **same** `source_len=6019`. The chunker is
running, the queue is being filled, the workers are processing — yet the
output of every "what tables does X read" query is still empty.

Investigation: `code_chunker.py` chunks `focal_context.code_units`. Those
`CodeUnit` objects are populated by `NavigatorAgent._assemble_chain`, which
**already truncated each file's content** to 6000 chars before placing it
in the unit. The chunker then operates on the truncated string, slices it
into per-method chunks, and the LLM sees per-method chunks of a file that's
85% missing.

The architectural error: **two layers conflate "what to look at" with "what
to read"**. NavigatorAgent's job is discovery (which files matter for this
endpoint). It should not also be the file reader. The chunker's job is
splitting (turn a file into method-sized pieces). It currently trusts
whatever string the navigator hands it.

User's verbatim framing: *"truncation can never be scalable and lose
context"*. ADR-0044 closed the LLM-call truncation. ADR-0045 closes the
content-pipeline truncation that ADR-0044 missed.

---

## Decision

Two coordinated changes that enforce a clean separation:

### D1 — `CodeUnit` no longer carries content; it carries a path + metadata

The `CodeUnit` dataclass becomes:

```python
@dataclass
class CodeUnit:
    file_path: str        # absolute or repo-relative
    repo_name: str
    role: str             # "controller" | "service" | "repository" | …
    class_name: str
    language: str
    # No `content` field. Content is read on demand from `file_path`.
    # NavigatorAgent populates `discovery_reason: str` and `relevance_score`
    # only — never reads file bytes itself.
    discovery_reason: str = ""
    relevance_score: float = 0.0
```

NavigatorAgent's job ends at "here is the list of relevant files + why". It
no longer reads file content for inclusion in the unit. Its existing
classification LLM call still receives a small content snippet for the
LLM's understanding, but that snippet is **never** stored on `CodeUnit`
or used downstream.

### D2 — Chunker reads files directly from disk, in full

`CodeChunker.chunk_repo(units: list[CodeUnit])` opens each `unit.file_path`
with `Path(fp).read_text(errors="ignore")` — full content, no cap, no
intermediate string. Tree-sitter parses the full file, extracts every
method's exact byte range, emits one `MethodChunk` per method.

Files >50,000 chars are not special-cased — tree-sitter's parser handles
them; the per-method chunks are still bounded by the method size (typically
<2k chars), which is what the LLM call needs.

If a file is unreadable (binary, permission denied, missing), the chunker
emits a `MethodChunk(kind='unreadable_file', ...)` so the operator can see
the gap rather than silently lose the file.

### D3 — Backwards-compat shim for the legacy extract path

The legacy path (`BRAIN_LEGACY_EXTRACT=true`) still exists and still expects
`CodeUnit.content`. Add a property:

```python
@property
def content(self) -> str:
    """Lazy file read. Used only by the legacy extractor. Cached after first read."""
    if self._content_cache is None:
        try:
            self._content_cache = Path(self.file_path).read_text(errors="ignore")
        except OSError as exc:
            log.warning("CodeUnit.content read failed", path=self.file_path, error=str(exc))
            self._content_cache = ""
    return self._content_cache
```

Existing callers (NavigatorAgent's classification LLM call, legacy
EntityExtractor) use the property and get full content. New code (the
chunker, the worker) reads `unit.file_path` directly to bypass any caching.

### D4 — Sanity assert in the chunker

Add a defensive assert in the chunker so the bug can never regress
silently:

```python
def chunk_file(self, fp: str) -> list[MethodChunk]:
    raw = Path(fp).read_text(errors="ignore")
    if len(raw) == 6019 or (raw.endswith("(truncated)") and len(raw) < 8000):
        log.error(
            "Chunker received truncated content — refusing to chunk; "
            "this means an upstream caller still slices file content. "
            "Pass file_path through unmodified.",
            path=fp, raw_len=len(raw),
        )
        raise TruncatedContentError(fp)
    # …chunk…
```

Loud failure beats silent data loss every time.

---

## Options Considered

### Option A — Fix `_assemble_chain` to also bump its truncation cap

| Dimension | Assessment |
|---|---|
| Complexity | Low |
| Scalability | Bounded — any cap is the wrong cap for some file |
| Quality | Partial — only helps when the cap happens to be high enough |
| Future regressions | Likely — caps drift with each new "important" feature |

Pros: one-line change.
Cons: doesn't fix the layering problem; the next file > new-cap chars
silently truncates again.

### Option B — Make CodeUnit a path+metadata-only struct (this ADR)

| Dimension | Assessment |
|---|---|
| Complexity | Medium — touches CodeUnit + 4-6 callers + chunker |
| Scalability | Unlimited file size at the I/O layer |
| Quality | Total — no string slice anywhere in the chunker path |
| Future regressions | Loud failure via D4 assert |

Pros: principled separation; no future surprises from cap drift.
Cons: small refactor of `CodeUnit` consumers.

### Option C — Stream files via tree-sitter `parse_chunk` API

| Dimension | Assessment |
|---|---|
| Complexity | High — re-architect tree-sitter integration |
| Scalability | Handles files of any size with bounded memory |
| Quality | Same as Option B |

Pros: bounded memory for hypothetical 1GB files.
Cons: massive overkill; we don't have files >100k chars in practice.

---

## Trade-off Analysis

Option A is what we'd do under deadline pressure. It fixes the immediate
symptom but leaves the layering wrong.

Option B is the structurally correct answer. The cost is a one-day
refactor that touches a single dataclass and ~6 callers. The benefit is
that no future code path can re-introduce truncation by mistake — the
field literally doesn't exist on CodeUnit anymore.

Option C is a future-proof for a problem we don't have. Reject.

**Recommendation: Option B**, with the loud-failure assert from D4 as
a guard rail.

---

## Consequences

**What becomes easier**

- Chunker output for any file is the full set of methods. Period.
- Every method's body reaches the chunk-extractor LLM call in full.
- The lob-rename query has the verbatim jOOQ DSL chain available
  because every method body in `CompetitivenessPlanRepository.java`
  reaches an LLM call regardless of where in the file it sits.
- Content-truncation regressions become impossible — the field is gone.

**What becomes harder**

- Mild refactor: NavigatorAgent's `_assemble_chain` returns
  `list[CodeUnit]` without content. Its internal classification LLM
  call uses the lazy `content` property for the small-context snippet.
- Disk I/O happens in a different place (chunker's `chunk_file`
  instead of NavigatorAgent's `_read`). On hot reruns this is mitigated
  by the OS page cache; for cold reruns it adds milliseconds per file.

**What we'll need to revisit**

- The lazy `content` property is a transitional shim. Once the legacy
  `BRAIN_LEGACY_EXTRACT` path is deleted (post-ADR-0044 stable), we
  should remove the property entirely so there's exactly one way to
  read file content (the chunker).

---

## Action Items

1. [ ] Refactor `CodeUnit`: remove `content: str` field; add
       `discovery_reason`, `relevance_score`; add lazy `content`
       property for the legacy shim only.
2. [ ] Update `NavigatorAgent._assemble_chain` to never set
       `unit.content`; pass `discovery_reason` instead. Its
       classification LLM call uses `unit.content` (lazy property)
       only at the call site.
3. [ ] Update `CodeChunker.chunk_file` to call
       `Path(file_path).read_text(errors="ignore")` directly. No
       `unit.content`. Add the D4 assert.
4. [ ] Update `EntityExtractor` (legacy path) to use the lazy
       `content` property — works because property reads the same
       file the chunker does.
5. [ ] Acceptance test
       `tests/acceptance/test_no_truncation_e2e.py`:
       - Synthetic 120k-char file (1 class, 30 methods, 30 distinct
         columns referenced).
       - Run full pipeline with `BRAIN_USE_CHUNK_QUEUE=true`.
       - Assert `extraction_queue` row count == 30 for that file.
       - Assert all 30 query_text bodies recovered verbatim.
       - Assert all 30 distinct columns appear as READS_COLUMN edges.
6. [ ] Telemetry: every chunker run emits a
       `chunker.read_file_directly path=… len=…` debug line so the
       operator can confirm the full file size is being processed.
