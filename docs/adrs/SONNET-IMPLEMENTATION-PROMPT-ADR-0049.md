# Implementation Prompt — ADR-0049 (caching everywhere + LLM-friendly formats)

**You are landing ADR-0049 in this repo. Read this prompt fully before writing any code. ADR-0049 is the FIRST of three coordinated ADRs (0049 → 0048 → 0050) that together cut pipeline cost from $0.30 → $0.005 warm and eliminate big-repo truncation. This ADR is the foundation.**

---

## Pre-flight

1. Read `docs/adrs/ADR-0049-aggressive-caching-and-pipeline-cost-cuts.md` start-to-finish. This prompt is a build instruction; the ADR is the spec.
2. Read `docs/adrs/ADR-0050-big-repo-safe-adaptive-extraction.md` §"Sequencing & Merge Plan" — that table tells you which files you OWN and which files you must NOT touch (those belong to ADR-0048 / 0050).
3. `git checkout -b feature/adr-0049-caching-and-format` from `main`.
4. Confirm prerequisites:
   - `pyproject.toml` pins `anthropic >= 0.39.0` (cache_control SDK support)
   - `httpx >= 0.27` (HTTP/2 pool)
   - `lxml` is **NOT** required; we use stdlib `xml.etree.ElementTree`

---

## File ownership for THIS PR (do not touch anything else)

You exclusively own and may modify:

```
src/companybrain/providers/anthropic_provider.py     # C1 + O4
src/companybrain/util/file_cache.py                  # C2 (NEW FILE)
src/companybrain/util/ast_cache.py                   # C3 (NEW FILE)
src/companybrain/pipeline/queue.py                   # C4
src/companybrain/pipeline/structural_prepass.py      # C6
src/companybrain/retrieval/qdrant_writer.py          # C5
src/companybrain/retrieval/hybrid_search.py          # C7
src/companybrain/api/routes/query.py                 # O5
src/companybrain/config.py                           # tunables
db/migrations/V11__extraction_queue_dedup.sql        # NEW
tests/acceptance/test_pipeline_cost_targets.py       # NEW
```

You MAY add **append-only** code (new functions, new env-flag-gated branches) to:

```
src/companybrain/pipeline/code_chunker.py            # use FileCache + AstCache
src/companybrain/agents/navigator_agent.py           # use FileCache
src/companybrain/collectors/code_tracer.py           # use FileCache
src/companybrain/pipeline/orchestrator.py            # add stage-skip flags (O3)
src/companybrain/pipeline/chunk_extractor.py         # XML in/out (O5a-1, O5a-3, O5a-4)
```

Do NOT modify any other file. ADR-0048 and ADR-0050 own the rest.

---

## Implementation steps (land in this order)

### Step 1 — C1: wire `cache_control` into `AnthropicProvider`

**The single most important change in this PR.** Production logs show
`cache_creation=0 cache_read=0` on every LLM call — the cache hint is
dropped. Fix:

In `src/companybrain/providers/anthropic_provider.py`, locate the
`chat`/`chat_json` method that builds `sdk_messages` for
`self._client.messages.create(...)`. Today it likely does:

```python
sdk_messages = [{"role": m.role, "content": m.content} for m in messages]
```

Replace with the cache-aware builder:

```python
def _build_sdk_messages(messages: list[ChatMessage]) -> tuple[Optional[list], list]:
    """Returns (system_param, user_messages_param).

    Anthropic SDK takes `system` as a top-level parameter, not a message.
    Attach cache_control to the system block so the system prefix
    (taxonomy + schema + examples) caches across the session.
    """
    system_blocks = []
    user_messages = []
    for m in messages:
        if m.role == "system":
            block = {"type": "text", "text": m.content}
            # Cache anything ≥ 1024 tokens — Anthropic's minimum.
            # Use a cheap char approximation (4 chars ≈ 1 token).
            if len(m.content) >= 4 * 1024:
                block["cache_control"] = {"type": "ephemeral"}
            system_blocks.append(block)
        else:
            user_messages.append({"role": m.role, "content": m.content})
    return (system_blocks or None), user_messages
```

Update the call site:

```python
system_param, user_messages = _build_sdk_messages(messages)
resp = await self._client.messages.create(
    model=self._resolve_model(role),
    system=system_param,
    messages=user_messages,
    max_tokens=max_tokens,
    **kwargs,
)
```

Then in the response handler, log `cache_creation_input_tokens` and
`cache_read_input_tokens` (they're on the response's `usage` object):

```python
log.info(
    "llm_call",
    ...,
    cache_creation_tokens=getattr(resp.usage, "cache_creation_input_tokens", 0),
    cache_read_tokens=getattr(resp.usage, "cache_read_input_tokens", 0),
)
```

### Step 2 — O1: bump default worker concurrency

In `src/companybrain/config.py`:

```python
chunk_queue_max_workers: int = 4   # was 2; safe under 35s 429 backoff
```

### Step 3 — C4: cross-job extraction_queue dedup

**Migration first.** Create `db/migrations/V11__extraction_queue_dedup.sql`:

```sql
-- Track which job FIRST produced a result so we can attribute reused work.
ALTER TABLE extraction_queue
    ADD COLUMN IF NOT EXISTS source_job_id UUID;

-- Index for the cross-job lookup that the new enqueue() does on every insert.
CREATE INDEX IF NOT EXISTS idx_extraction_queue_done_by_hash
    ON extraction_queue(workspace_id, body_hash)
    WHERE status = 'done' AND result_json IS NOT NULL;

-- TTL — keep done rows 30 days for warm-rerun cache, then drop.
-- (Run as a periodic job; the index above keeps the lookup fast even with TTL backlog.)
```

In `src/companybrain/pipeline/queue.py::enqueue`, before inserting each
new row as `pending`, check:

```python
existing = await conn.fetchrow(
    "SELECT result_json FROM extraction_queue "
    " WHERE workspace_id = $1 AND body_hash = $2 "
    "   AND status = 'done' AND result_json IS NOT NULL "
    " ORDER BY processed_at DESC LIMIT 1",
    r.workspace_id, r.body_hash,
)
if existing:
    # Reuse — copy result_json from the prior run, mark new row 'done'.
    await conn.execute(
        "INSERT INTO extraction_queue "
        "  (id, workspace_id, job_id, repo, file_path, qname, body_hash, "
        "   chunk_kind, header_context, import_context, body, language, "
        "   status, result_json, source_job_id, created_at, processed_at) "
        "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, "
        "        'done', $13, $14, now(), now()) "
        "ON CONFLICT (workspace_id, body_hash) DO NOTHING",
        ..., existing["result_json"], r.job_id,
    )
    continue   # skip the normal pending insert
```

Worker `drain_queue` should also check `source_job_id IS NOT NULL` and
skip LLM call entirely for those rows (their `result_json` is already
populated).

### Step 4 — C2: FileCache (job-scoped LRU)

Create `src/companybrain/util/file_cache.py`:

```python
"""Per-job FileCache — read each file once, share across all consumers.

Today the same 30 KB Java file is read 4-5× per job (navigator, chunker,
structural pre-pass, hybrid searcher index, embedding payload). FileCache
de-dupes those reads with a bounded LRU.
"""
from collections import OrderedDict
from pathlib import Path


class FileCache:
    def __init__(self, max_entries: int = 200):
        self._cache: OrderedDict[str, str] = OrderedDict()
        self._max = max_entries
        self._hits = 0
        self._misses = 0

    def read(self, path: str) -> str:
        key = str(Path(path).resolve())
        if key in self._cache:
            self._cache.move_to_end(key)
            self._hits += 1
            return self._cache[key]
        try:
            content = Path(key).read_text(errors="ignore")
        except OSError:
            content = ""
        self._cache[key] = content
        self._misses += 1
        if len(self._cache) > self._max:
            self._cache.popitem(last=False)
        return content

    @property
    def stats(self) -> dict:
        return {"hits": self._hits, "misses": self._misses,
                "size": len(self._cache)}
```

Thread it through (append-only changes — keep the old direct
`Path.read_text` paths as a fallback when `file_cache` is None):

- `code_chunker.chunk_file(self, fp, file_cache=None)` — use cache if
  passed, otherwise fall through to direct read.
- Same for `navigator_agent._read`, `code_tracer._knowledge_to_code_units._emit`.

The orchestrator constructs a `FileCache()` per job and passes it down.

### Step 5 — C5: skip Qdrant upsert when version_hash unchanged

In `src/companybrain/retrieval/qdrant_writer.py::upsert_entity`:

```python
async def upsert_entity(self, entity, version_hash):
    existing = await self._client.retrieve(
        collection_name=self._collection,
        ids=[urn_to_int(entity.urn)],
        with_payload=["version_hash"],
    )
    if existing and existing[0].payload.get("version_hash") == version_hash:
        log.debug("qdrant.skip_unchanged", urn=entity.urn, version_hash=version_hash)
        return
    embedding = await self._embedder.embed(entity.t1_summary)
    await self._client.upsert(...)   # existing code
```

### Step 6 — C3: AstCache

Create `src/companybrain/util/ast_cache.py`:

```python
"""Tree-sitter parse cache keyed by (file_path, body_hash)."""
from typing import Any


class AstCache:
    def __init__(self):
        self._cache: dict[tuple[str, str], Any] = {}

    def parse(self, lang_parser, source_bytes: bytes,
              key: tuple[str, str]):
        if key in self._cache:
            return self._cache[key]
        tree = lang_parser.parse(source_bytes)
        self._cache[key] = tree
        return tree

    def clear(self):
        self._cache.clear()
```

Thread through `code_chunker._split_via_ast` (append-only).

### Step 7 — C6: structural pre-pass cache by SHA

In `src/companybrain/pipeline/structural_prepass.py`, add a
module-level `_PREPASS_CACHE: dict[tuple[str, str], dict] = {}`
keyed by `(repo_path, commit_sha)`. Skip the cb-api round-trip on
cache hit.

### Step 8 — C7: hybrid search index TTL by SHA

In `src/companybrain/retrieval/hybrid_search.py::FileHybridSearcher`,
add `self._index_built_at_sha: Optional[str] = None`. In `search()`:

```python
sha = _resolve_commit_sha(repo_path)
if self._index_built_at_sha != sha:
    self._build_index(repo_path)
    self._index_built_at_sha = sha
```

### Step 9 — O2: L2-cache short-circuit

In the orchestrator's chunked path, BEFORE building the
`_method_chunks` list, query `.brain/.l2-cache/main.json` for
matching file hashes. Files with a hit are added to
`focal_context.cache_hit_files: set[str]`. Skip enqueueing chunks
whose `file_path` is in this set.

### Step 10 — O3: conditional Stage 1.5 / 4 skip

In `pipeline/orchestrator.py`, after the chunked path completes
successfully, set:

```python
if _use_chunk_queue and _chunk_results:
    _skip_intent_synthesis = True
    _skip_gap_detection    = True
```

(These flags already exist; this just sets them automatically when
the chunked path provides equivalent data.)

### Step 11 — O4: httpx connection pooling

In `src/companybrain/providers/anthropic_provider.py`, replace any
`httpx.AsyncClient()` instantiation with a module-level singleton:

```python
_HTTP_CLIENT: Optional[httpx.AsyncClient] = None


def _get_http_client() -> httpx.AsyncClient:
    global _HTTP_CLIENT
    if _HTTP_CLIENT is None or _HTTP_CLIENT.is_closed:
        _HTTP_CLIENT = httpx.AsyncClient(
            timeout=httpx.Timeout(60.0, connect=5.0),
            limits=httpx.Limits(
                max_keepalive_connections=10, max_connections=20,
            ),
        )
    return _HTTP_CLIENT
```

Pass it to the Anthropic SDK constructor via `http_client=_get_http_client()`.

### Step 12 — O5: SSE streaming for /query

In `src/companybrain/api/routes/query.py`, add a new
`StreamingResponse` variant of the existing endpoint behind a
`stream=True` query param. Accumulate Sonnet's stream deltas and emit
SSE frames. The client-side change is out of scope.

### Step 13 — O5a: XML inputs + compact JSON / XML outputs

**O5a-1** (chunk_extractor user message → XML): in
`src/companybrain/pipeline/chunk_extractor.py`, replace the
JSON-stringified user content with:

```python
user = (
    f'<class_header file="{file_path}" class="{class_name}">\n'
    f'{header}\n'
    f'</class_header>\n\n'
    f'<method name="{method_name}" lang="{lang}">\n'
    f'{body}\n'
    f'</method>\n\n'
    f'<imports>\n'
    f'{chr(10).join(imports)}\n'
    f'</imports>'
)
```

**O5a-3** (compact JSON output): append to every JSON-extracting
system prompt:

```
Return a single line of compact JSON (no whitespace between tokens,
no indentation). The schema is exactly:
```

**O5a-4** (XML output for ContextAgent — stub only). Add
`OUTPUT_FORMAT="xml"` constant. Do not yet switch — ADR-0050 finishes
the XML iterparse partial parser. Land the constant + a no-op JSON path
so ADR-0050's PR can flip the switch.

**O5a-5** (drop double-encoded markdown in /query): add a `summary_md`
raw markdown field to the response schema. Keep the wrapped `summary`
for one release as deprecated.

### Step 14 — Telemetry surface

In the orchestrator's job-result builder, add:

```python
result["telemetry"] = {
    "total_llm_calls": _llm_call_count,
    "cache_read_tokens_total": _cache_read_total,
    "cache_creation_tokens_total": _cache_creation_total,
    "total_input_tokens": _input_total,
    "total_output_tokens": _output_total,
    "total_cost_usd": _cost_total,
    "total_wall_time_seconds": time.perf_counter() - _start,
    "cache_hit_rate": (_cache_read_total / max(1, _input_total)),
    "file_cache_stats": file_cache.stats if file_cache else None,
}
```

Surface via `/pipeline/jobs/{id}` response.

### Step 15 — Acceptance test

Create `tests/acceptance/test_pipeline_cost_targets.py`. Two scenarios:

```python
async def test_cold_run_cost_target():
    """Cold run on the canonical lob endpoint must cost < $0.03 and
    show cache_read > 5_000 tokens (proves the cache wire-up works)."""
    result = await run_pipeline(
        endpoint="/competitiveness/summary/competitors/payer",
        method="POST",
        repo="fixtures/network-iq-backend-java-snapshot",
    )
    assert result.telemetry["total_cost_usd"] < 0.03
    assert result.telemetry["cache_read_tokens_total"] > 5_000


async def test_warm_rerun_uses_extraction_cache():
    """Same endpoint twice in a row: second run should be < $0.005 and
    < 4 LLM calls (only the SpecialistAgent + 1-2 ContextAgent calls
    because all chunk results are cached in extraction_queue)."""
    await run_pipeline(...)   # warm cache
    result = await run_pipeline(...)
    assert result.telemetry["total_cost_usd"] < 0.005
    assert result.telemetry["total_llm_calls"] < 4


async def test_extraction_quality_unchanged():
    """Cost-cutting must not drop extraction quality. The lob query
    must still surface the .lob(r.value4()) chain."""
    await run_pipeline(...)
    plan_repo_entity = await brain.read_entity(
        "CompetitivenessPlanRepository.getPayerCompetitors",
    )
    assert "lob" in (plan_repo_entity.metadata.get("query_text") or "")
```

---

## Verification

```bash
# Type-check
.venv/bin/mypy src/companybrain
# Lint
.venv/bin/ruff check src/companybrain
# Unit + integration
.venv/bin/pytest tests/unit tests/integration
# Acceptance (requires Postgres + Anthropic key)
.venv/bin/pytest tests/acceptance/test_pipeline_cost_targets.py -v
```

All three acceptance assertions must pass before opening the PR.

---

## PR description

```
feat(pipeline): aggressive caching + LLM-friendly format swap (ADR-0049)

Cuts cold-run cost $0.30 → $0.03 and warm-rerun cost → $0.005 by:
- C1: wiring cache_control:ephemeral into AnthropicProvider (was dropped on the wire)
- C2/C3: per-job FileCache + AstCache (de-dups 4-5× file reads, 3× AST parses)
- C4: cross-job extraction_queue dedup (warm reruns skip LLM entirely)
- C5: Qdrant skip-on-unchanged via version_hash
- C6/C7: structural prepass + hybrid index cached by commit SHA
- O1: chunk_queue_max_workers 2 → 4 (4× parallel batches)
- O2: L2-cache short-circuit on unchanged files
- O3: conditional Stage 1.5/4 skip when chunked path provides their data
- O4: httpx connection pooling (~75ms saved per call)
- O5: SSE streaming for /query (TTFB 5s → 600ms)
- O5a: XML tags for inputs, compact JSON outputs, summary_md raw

Acceptance test asserts cold < $0.03, warm < $0.005, extraction quality
unchanged (lob query still finds .lob(r.value4())).

Coordinated with ADR-0048 (two-agent extraction) and ADR-0050 (big-repo
recovery) — file-ownership table in ADR-0050 §"Sequencing & Merge Plan".
```
