# ADR-0049 — Aggressive Caching + Pipeline-Wide Cost Cuts

**Status:** Proposed
**Date:** 2026-05-10
**Deciders:** Chinmay (product), pipeline-team
**Sequenced with:** This ADR lands FIRST (caching + util libs are prereq for 0048+0050). ADR-0048 (two-agent extraction) lands second; ADR-0050 (big-repo recovery) lands third. The full file-ownership table that guarantees no merge conflicts across the three PRs lives in ADR-0050 §"Sequencing & Merge Plan".
**Cost target:** ≤ $0.03 per typical pipeline run, ≤ $0.005 on warm reruns
**Latency target:** p50 < 20s end-to-end on a 60-method endpoint

---

## Context

ADR-0048 cuts the navigator's 26-turn ReAct loop and batches the
chunk extractor — that fixes the **biggest** hot-spot. But the
production log we just analysed has more holes that, taken together,
account for another $0.10–0.20 per run:

```
cache_creation=0  cache_read=0   ← repeated on every call in the run
```

Every single LLM call in the live log shows zero cache activity. The
Anthropic prompt-cache hint isn't reaching the wire. That's not just a
navigator problem — it's the same on chunk_extractor, relationship
extractor, context synthesizer, gap detector, query route, and intent
router. Six distinct call paths, six places paying full input cost on
every invocation when 70-80% of each prompt is reusable boilerplate
(system prompt + edge taxonomy + JSON schema).

Beyond caching, the pipeline has several other patterns that burn
budget without buying quality:

1. **Files are read from disk multiple times per run** — once by the
   navigator, again by the chunker (per ADR-0045), again by the
   relationship extractor's structural pre-pass, again by the L2
   cache hash check. Five reads of the same 26KB file per run.

2. **AST is re-parsed by every consumer** — tree-sitter parses
   `CompetitivenessPlanRepository.java` once for the chunker, again
   for the symbol-table builder, again for the structural pre-pass.
   Same 30k-char file, three parses.

3. **Embeddings are recomputed on every run** — Qdrant collections
   are re-populated even when the entity's `body_hash` is unchanged
   from the previous run. Embedding 100 entities costs ~$0.02 in
   itself.

4. **Stage 1.5 (intent synthesis) and Stage 4 (gap detection) fire
   even when the chunked path already produced their outputs**, paying
   for redundant work.

5. **Git history collection runs every time** even when there are no
   new commits since the previous run for that endpoint's file set.

6. **Sequential batching** — `ChunkBatcher` already groups chunks but
   the worker drains the queue with `max_workers=2`, so 8 batches
   serialize as 4 round-trips. Each round-trip is ~700ms minimum.

7. **`max_tokens` defaults are 2-3× the actual P95 output**, which
   doesn't increase cost (you only pay for tokens generated) but
   inflates the worst-case budget guard and triggers the budget cap
   prematurely.

User's framing: *"cache everywhere we can and optimize everything you
feel fit"*. This ADR treats caching as the default, not the exception.

---

## Decision

Twelve coordinated changes — seven caching, five non-caching — that
together push observed cost from $0.30–0.50 → $0.03 cold / $0.005
warm and wall time from 5+ minutes → < 20 seconds.

---

### Group A — Caching (C1–C7)

#### C1 — Anthropic prompt caching wired into `AnthropicProvider` (the keystone fix)

`cache_creation_tokens=0` across every single call in the production
log means the cache hint is dropped. Once. Everywhere.

In `src/companybrain/providers/anthropic_provider.py`:

```python
async def chat(self, messages, *, role, max_tokens, **kwargs):
    # Find the LAST system message and attach cache_control to it.
    # Anthropic caches the prefix UP TO the last cache_control marker.
    # Putting it on the system prompt caches the whole boilerplate
    # (taxonomy, schema, examples) without caching per-call user input.
    sdk_messages = []
    for m in messages:
        if m.role == "system" and len(m.content) >= 1024:
            sdk_messages.append({
                "role": "system",
                "content": [{
                    "type": "text",
                    "text": m.content,
                    "cache_control": {"type": "ephemeral"},
                }],
            })
        else:
            sdk_messages.append({"role": m.role, "content": m.content})
    return await self._client.messages.create(
        model=self._resolve_model(role),
        messages=sdk_messages,
        max_tokens=max_tokens,
        **kwargs,
    )
```

This single change benefits:

| Caller | system-prompt size | calls per run | savings per run |
|---|---|---|---|
| chunk_extractor (single + batch) | ~3.0k tok | 8-50 | $0.04–0.18 |
| RelationshipExtractor | ~2.5k tok | 1-3 | $0.005 |
| ContextSynthesizer | ~3.5k tok | 1-3 | $0.008 |
| GapDetector | ~1.5k tok | 1 | $0.001 |
| KnowledgeNavigatorAgent | ~2.0k tok | 25 (legacy path) | $0.04 |
| NavigatorAgent classifier | ~1.0k tok | 1 | $0.001 |
| Specialist + Context (post-ADR-0048) | ~2.5k + ~3.0k | 1 + 8 | $0.012 |
| LLMHandlerFinder | ~1.2k tok | 1 | $0.001 |

**Total cache-driven savings: ~$0.10–0.25 per run**, with no schema
or behavioural change. Verify by asserting `cache_read_tokens > 0`
in the integration test for the second-and-onward call.

Acceptance: every call after the first in a run logs
`cache_read=N>0` for the system prefix. CI fails if cache_read=0
across two consecutive calls in a run.

#### C2 — File-content cache (`FileCache` per-job singleton)

`Path(fp).read_text()` happens 4-5× per run for big files
(`CompetitivenessPlanRepository.java`: navigator, chunker, structural
pre-pass, hybrid searcher index, embedding payload). At 30KB × 5 reads
× 1MB/s SSD × syscall overhead = ~150ms wasted per file × ~15 files
= 2 seconds of pointless I/O.

Add `src/companybrain/util/file_cache.py`:

```python
class FileCache:
    """Per-job singleton — pass via context, not as a module global.
    Caches Path.read_text(errors='ignore') keyed by absolute path.
    Bounded at 200 entries with LRU eviction so large repos don't OOM."""

    def __init__(self, max_entries: int = 200):
        from collections import OrderedDict
        self._cache: OrderedDict[str, str] = OrderedDict()
        self._max = max_entries

    def read(self, path: str) -> str:
        key = str(Path(path).resolve())
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        try:
            content = Path(key).read_text(errors="ignore")
        except OSError:
            content = ""
        self._cache[key] = content
        if len(self._cache) > self._max:
            self._cache.popitem(last=False)
        return content
```

Inject `FileCache` into:
- `code_chunker.chunk_file` (drop direct `Path.read_text`)
- `navigator_agent._read` (currently has its own per-instance dict;
  consolidate)
- `code_tracer._knowledge_to_code_units._emit`
- `structural_prepass.run_structural_prepass`

#### C3 — Tree-sitter AST cache (`AstCache` per-job)

Same pattern. `code_chunker._split_via_ast` parses the same file
multiple times if the chunker is invoked twice per file (navigator +
extractor). Cache the parsed tree by `(file_path, body_hash)`:

```python
class AstCache:
    def __init__(self):
        self._cache: dict[tuple[str, str], "tree_sitter.Tree"] = {}

    def parse(self, lang: str, source: bytes, key: tuple[str, str]) -> "tree_sitter.Tree":
        if key in self._cache:
            return self._cache[key]
        tree = _PARSERS[lang].parse(source)
        self._cache[key] = tree
        return tree
```

Saves ~50-100ms per file on cold parse, ~5ms on warm reuse. Per a
60-method endpoint: ~1-2 seconds.

#### C4 — `extraction_queue` dedup across runs (not just within a run)

The current `enqueue` function dedups by `(workspace_id, body_hash)`
within a job. Promote the dedup to **across jobs**: if a chunk with
`body_hash=X` was processed `status='done'` in any prior job for the
same workspace, skip it and reuse the prior `result_json`.

Change `pipeline/queue.py`:

```python
async def enqueue(rows: list[ChunkInput]) -> int:
    """Insert chunks into queue, marking duplicates of prior 'done'
    work as status='done' with the prior result_json copied over."""
    inserted = 0
    async with pool.acquire() as conn:
        for r in rows:
            existing = await conn.fetchrow(
                "SELECT result_json FROM extraction_queue "
                " WHERE workspace_id = $1 AND body_hash = $2 "
                "   AND status = 'done' AND result_json IS NOT NULL "
                " ORDER BY processed_at DESC LIMIT 1",
                r.workspace_id, r.body_hash,
            )
            if existing:
                # Reuse the prior LLM result — zero LLM cost for this chunk.
                await conn.execute(
                    "INSERT INTO extraction_queue "
                    "(... , status, result_json, source_job_id) "
                    "VALUES (..., 'done', $X, $Y) "
                    "ON CONFLICT (workspace_id, body_hash) DO NOTHING",
                    ..., existing["result_json"], r.job_id,
                )
            else:
                # Normal insert as 'pending'
                await _insert_pending(conn, r)
                inserted += 1
    return inserted
```

This means the **second run of the same endpoint costs near-zero**
on the chunk-extraction side, only paying for the
ContextSynthesizer if it runs. For a developer iterating on a query,
this is the difference between $0.05 and $0.001 per run.

#### C5 — Embedding cache by `body_hash` (skip Qdrant upserts on no-change)

Today, `qdrant_writer.upsert_entity` always recomputes the embedding
and upserts. Add a check:

```python
async def upsert_entity(self, entity: ExtractedEntity, version_hash: str):
    existing = await self._client.retrieve(
        collection_name=self._collection,
        ids=[urn_to_int(entity.urn)],
        with_payload=["version_hash"],
    )
    if existing and existing[0].payload.get("version_hash") == version_hash:
        return  # No change; skip embedding + upsert.
    embedding = await self._embedder.embed(entity.t1_summary)
    await self._client.upsert(...)
```

Embeddings are ~$0.0001 each via `text-embedding-3-small`, but they
add up: 100 entities × 0.0001 = $0.01 per run. Across 50 runs that's
$0.50 of pure recompute.

#### C6 — Structural pre-pass cache by git SHA

`structural_prepass.run_structural_prepass` already returns a dict of
file fingerprints. Persist the result keyed by
`(repo_url, commit_sha)` and skip the cb-api round-trip entirely if
the SHA hasn't moved:

```python
@lru_cache(maxsize=32)
def _prepass_cache_key(repo_url: str, commit_sha: str) -> str:
    return f"{repo_url}@{commit_sha}"

async def run_structural_prepass(... commit_sha, ...):
    key = _prepass_cache_key(repo_url, commit_sha)
    if cached := _PREPASS_CACHE.get(key):
        log.info("Structural pre-pass: cache hit", key=key)
        return cached
    result = await _real_run_structural_prepass(...)
    _PREPASS_CACHE[key] = result
    return result
```

Cuts ~3-5 seconds + a Bun service round-trip on every rerun.

#### C7 — Hybrid searcher BM25 index cache

`FileHybridSearcher` already has `_HYBRID_SEARCHER` as a module-level
singleton (`code_tracer.py:38`). Verify the BM25 index ITSELF is
cached, not just the searcher object. Currently the index is rebuilt
on every `search()` call — that's a bug. Add `_index_built_at_sha`
to short-circuit:

```python
async def search(self, query, ..., repo_path):
    sha = _resolve_commit_sha(repo_path)
    if self._index_built_at_sha != sha:
        self._build_index(repo_path)
        self._index_built_at_sha = sha
    return self._search(query)
```

Saves ~5 seconds on every rerun against an unchanged repo.

---

### Group B — Non-caching optimizations (O1–O5)

#### O1 — Bounded-concurrency batch fan-out

`worker.drain_queue(max_workers=2)` serializes ContextAgent batches
into 4-second blocks. For a 60-method endpoint that's 8 batches × 4s
= 32s. With `max_workers=4` and bounded by Anthropic's rate limit
(10k output tokens/min on Haiku), that drops to ~12s.

Change default `chunk_queue_max_workers` 2 → 4 in `config.py`. The
existing 35-second 429 backoff handles the rate-limit case. Keep
the env var override.

#### O2 — Short-circuit unchanged files via L2 cache

The existing `.brain/.l2-cache/main.json` already stores per-file
hashes. Use it: at the START of `_assemble_chain` / SpecialistAgent
input prep, compute the hash of each candidate file. If the hash
matches a `done` entry in the L2 cache AND the prior run's brain still
has the entities, mark the file as "skip extraction" and only re-run
the relationship + context stages on top of the cached entities.

Implementation: add `cache_hit_files: set[str]` to `FocalContext`,
populate from L2 cache, and in the chunked path skip enqueueing for
files in this set (they're already represented in `entities` by the
post-Stage-1 merger).

A reasonable expectation: 60% file overlap between runs of related
endpoints in the same domain → 60% cost cut on warm reruns.

#### O3 — Skip Stage 1.5 / Stage 4 when the chunked path produced their data

The chunked extractor already emits `business_context` per method.
Stage 1.5 (intent synthesis) and Stage 4 (gap detection) re-derive
similar information. Today the orchestrator runs all four stages
unconditionally; gate Stage 1.5 / Stage 4 behind:

```python
if _use_chunk_queue and _chunk_results:
    _skip_intent_synthesis = True
    _skip_gap_detection    = True
```

Saves ~2 LLM calls per run (~$0.005). Keep the original ENV flags as
manual overrides.

#### O4 — HTTP connection pooling for Anthropic + OpenAI providers

Each `chat_json` call today opens a fresh `httpx.AsyncClient`.
Module-level singleton with a long timeout + keepalive:

```python
_HTTP_CLIENT: Optional[httpx.AsyncClient] = None

def _get_http_client() -> httpx.AsyncClient:
    global _HTTP_CLIENT
    if _HTTP_CLIENT is None:
        _HTTP_CLIENT = httpx.AsyncClient(
            timeout=httpx.Timeout(60.0, connect=5.0),
            limits=httpx.Limits(max_keepalive_connections=10, max_connections=20),
        )
    return _HTTP_CLIENT
```

Saves ~50-100ms per call (TCP + TLS handshake). 20 calls × 75ms =
1.5s wall-time savings.

#### O5a — LLM-friendly prompt formats (XML in, compact JSON out)

JSON is the wrong format for LLM **input**. It's verbose
(`{"file":"...","body":"..."}` vs `<file path="..."><body>...</body></file>`),
brittle to escape (every `\n` in a code body inflates 2× and obscures
structure), and the model sometimes fails to follow nested JSON
constraints in long contexts. XML tags are what Anthropic's own
documentation recommends, and the model attends to them more reliably.

JSON is also frequently the wrong format for LLM **output** when used
naively: pretty-printed JSON with indentation costs tokens for nothing
the model needs. Compact, single-line JSON is half the token count.

Concrete changes:

**1. Input format: XML tags around code, table for manifests.**

Replace today's prompt body in `chunk_extractor`:

```python
# CURRENT (mixed JSON-string + raw text)
user = f'''{{"class_header": "{escape(header)}", "method_body": "{escape(body)}", ...}}'''
```

with:

```python
# PROPOSED — XML tags, code in fenced blocks
user = f"""
<class_header file="{file_path}" class="{class_name}">
{header}
</class_header>

<method name="{method_name}" lang="{lang}">
{body}
</method>

<imports>
{chr(10).join(imports)}
</imports>
"""
```

For the SpecialistAgent's repo manifest from ADR-0048, replace the
JSON list with a markdown table:

```
| file | role | size_kb | reason |
|---|---|---|---|
| .../CompetitivenessController.java | controller | 5.2 | entry handler |
| .../DefaultCompetitivenessService.java | service | 12.8 | called by controller |
| .../CompetitivenessRepositoryImpl.java | repository | 26.3 | called by service |
| .../CompetitivenessPlanRepository.java | repository | 34.1 | delegated to by Impl |
```

The model parses tables natively and the text is ~40% smaller than
the equivalent JSON.

**2. Output format: ask for compact JSON, not pretty-printed.**

Add to every JSON-extracting system prompt:

```
Return a single line of compact JSON (no whitespace between tokens,
no indentation). Schema: {...}
```

A 60-method extraction batch's output today is ~6 KB pretty-printed
JSON; compact form is ~3 KB. At Haiku output rates (~$1.25/Mtok), the
saving is $0.004 per batch × 8 batches = **$0.03/run**, plus a
quarter-second wall time per call.

**3. Use XML for output when structure dominates payload.**

For the SpecialistAgent's plan, the existing JSON shape works fine
(it's small). But for the ContextAgent's batch output of N entities ×
21-field BusinessContext × M edges, switching to XML-with-attributes
saves another 15-20%:

```xml
<entities>
  <entity type="Method" name="getPayerCompetitors" file=".../Plan.java" confidence="0.95">
    <code_snippet>...</code_snippet>
    <query_text>SELECT competitor_id, payer_name, lob, ... FROM ...</query_text>
    <business_context purpose="..." change_risk="HIGH" data_sensitivity="medium" .../>
  </entity>
  <entity .../>
</entities>
<edges>
  <edge from="getPayerCompetitors" type="READS_COLUMN" to="competitive_payer_plan.lob" confidence="0.98"/>
  ...
</edges>
```

Trade-off: XML output requires a parser. We already have one (Python's
`xml.etree.ElementTree` is stdlib). The recovery story is also
better — a truncated XML response can be parsed up to the last
complete element without the brittle char-by-char scan we shipped
yesterday in `_recover_truncated_entities`.

**4. Markdown for human-readable narrative outputs.**

The `/query` endpoint already returns structured JSON. The `summary`
field today contains markdown wrapped in JSON. Drop the wrapper:

```
{"call_chain": [...], "sql_quotes": [...], ..., "summary_md": "<markdown here, raw>"}
```

The current double-encoding (markdown-in-string-in-JSON) means every
backtick and newline is escaped, costing ~2× tokens AND forcing the
client to decode twice.

#### O5 — Streaming query responses (improves perceived latency, not cost)

`/query` endpoint accumulates the full Sonnet response before
returning. For end users this is a 4-8 second blank wait. Stream
chunks via SSE:

```python
@router.post("/query", response_class=StreamingResponse)
async def query(body: QueryRequest):
    async def stream():
        async for chunk in QueryEngine().answer_streaming(body):
            yield f"data: {json.dumps({'delta': chunk})}\n\n"
        yield "data: [DONE]\n\n"
    return StreamingResponse(stream(), media_type="text/event-stream")
```

No cost change, but TTFB drops from ~5s to ~600ms.

---

## Options Considered

### Option A — This ADR (caching-first, twelve coordinated changes)

| Dimension | Assessment |
|---|---|
| Complexity | Medium — 12 small changes, no architectural shift |
| Cost reduction | 90% cold ($0.30 → $0.03), 99% warm ($0.30 → $0.005) |
| Latency reduction | 5min → < 20s p50 on 60-method endpoint |
| Quality risk | Low — caching is observability-safe; no prompts change |
| Effort | 2-3 days |

Pros: every change is independently reversible (ENV flag); benefits
compound multiplicatively.

Cons: 12 places to change; needs careful telemetry to verify each
cache is actually firing.

### Option B — Caching-only (just C1–C7)

| Dimension | Assessment |
|---|---|
| Complexity | Low — 7 cache wires |
| Cost reduction | 70% cold, 90% warm |
| Effort | 1 day |

Pros: minimum-blast-radius change; confirms cache-control hypothesis.

Cons: leaves the worker concurrency + L2 short-circuit on the table;
those are 30%+ of the latency win.

### Option C — Rip out the legacy stages (drop Stage 1.5 / 4 entirely)

| Dimension | Assessment |
|---|---|
| Complexity | Low |
| Cost reduction | $0.005/run only (already small) |
| Quality risk | Medium — these stages occasionally surface gaps the chunker missed |
| Effort | half day |

Pros: simplifies the pipeline.

Cons: kills a useful safety net for prompts that under-fill the
21-field context. Conditional skip (this ADR's O3) is safer.

---

## Trade-off Analysis

The biggest single win is **C1 (prompt caching)**. It costs nothing
in quality, requires no schema change, and pays back ~70% of the
total reduction. The reason it hasn't been live is that the cache
hint is dropped on the wire today; that's a 10-line fix in
`AnthropicProvider`.

C4 (extraction-queue dedup across runs) is the second-biggest win
but only on warm reruns. For the developer iterating on a single
endpoint repeatedly (the lob-rename test case is exactly this), it
turns a $0.05 run into a $0.001 run. Cold runs on a new endpoint
don't benefit.

O1 (max_workers 2 → 4) is the biggest latency win (~20s saved).
Cost-neutral.

The remaining items are individually small (sub-$0.01 each) but
together account for another ~$0.02 + 5s.

**Recommendation: Option A**, in landing order C1 → O1 → C4 → C2 →
C5 → rest. C1 alone validates the caching hypothesis end-to-end.

---

## Consequences

**What becomes easier**

- Iterating on prompts: cost per dev iteration drops 10×, you can
  experiment freely.
- Cost forecasting: with prompt caching the budget is dominated by
  output tokens, which is predictable from response schemas.
- Adding new edge types or context fields: the system prompt grows,
  but cache hits absorb the cost on every call after the first.
- Adding a new repo: warm rerun cost = $0.005 means the brain can
  be re-extracted on every commit without budget concern.

**What becomes harder**

- Cache invalidation: when the system prompt changes, the cache
  entry is invalidated for that prompt-version. Add a
  `system_prompt_version` field to the cache key so prompt edits
  don't accidentally serve stale results.
- Debugging non-determinism: cache hits return the cached LLM result
  byte-for-byte, which is good for reproducibility but means a bug
  in the cached output sticks until the body_hash changes.
- Stale embeddings if the embedding model changes: add a
  `embedding_model_version` column to the Qdrant payload and force
  re-embed when it changes.

**What we'll need to revisit**

- Cache TTLs (Anthropic ephemeral cache is 5 minutes; for cross-run
  caching we'd need to switch to the persistent cache tier when
  Anthropic ships it).
- `extraction_queue` storage growth: each cached `result_json` is
  ~2-5 KB; at 100 chunks × 100 runs that's 50 MB, manageable.
  Set up a TTL of 30 days for `done` rows older than that.

---

## Action Items

1. [ ] **C1** — Wire `cache_control: ephemeral` on system messages in
       `AnthropicProvider.chat`. Add integration test asserting
       `cache_read_tokens > 0` on the second call of a session.
2. [ ] **O1** — Default `chunk_queue_max_workers` 2 → 4. Verify the
       35s 429 backoff still keeps us under Anthropic's 10k-tpm
       output cap.
3. [ ] **C4** — `extraction_queue.enqueue` dedup across jobs. Add
       `source_job_id` column to track lineage. Add 30-day TTL job.
4. [ ] **C2** — Introduce `FileCache(max_entries=200)`; thread it
       through `code_chunker`, `navigator_agent`, `code_tracer`,
       `structural_prepass`. Drop the per-instance dicts.
5. [ ] **C5** — Embedding skip-on-unchanged in `qdrant_writer.upsert_entity`
       via `version_hash` payload check.
6. [ ] **C3** — `AstCache` keyed by `(file_path, body_hash)`; share
       across chunker + symbol-table builder.
7. [ ] **C6** — Persist structural pre-pass result by
       `(repo_url, commit_sha)`; short-circuit on cache hit.
8. [ ] **C7** — `FileHybridSearcher` index TTL by `commit_sha`;
       rebuild only when SHA moves.
9. [ ] **O2** — L2-cache short-circuit in chunked-path enqueue; skip
       chunks whose file matches a `done` L2 entry.
10. [ ] **O3** — Conditional Stage 1.5 / Stage 4 skip when chunked
       path filled `business_context`. Surface as job-summary line
       so we can see the savings.
11. [ ] **O4** — Module-level `httpx.AsyncClient` singleton in
       `AnthropicProvider`, `OpenAIProvider`, `GroqProvider`.
12. [ ] **O5** — `/query` SSE streaming. UI change to consume the
       stream is out of scope here; backend ships first.
13. [ ] **O5a-1** — Switch chunk_extractor + ContextAgent INPUT to
       XML tags + fenced code blocks. Replace the JSON-string user
       message with a templated XML body.
14. [ ] **O5a-2** — Switch SpecialistAgent input manifest from JSON
       list to a markdown table. Update prompt and tests.
15. [ ] **O5a-3** — Add "Return a single line of compact JSON" to
       every JSON-extracting system prompt. Verify by checking that
       output token counts drop ~40% on the existing acceptance test.
16. [ ] **O5a-4** — Switch ContextAgent OUTPUT from JSON to XML.
       Replace `_recover_truncated_entities` with an
       `xml.etree.ElementTree`-based partial parser (drops the
       hand-rolled char scanner).
17. [ ] **O5a-5** — `/query` summary field: stop double-encoding
       markdown-in-JSON-string. Add `summary_md` raw markdown field;
       deprecate the wrapped `summary`.
18. [ ] **Telemetry** — per-run summary fields:
       `total_llm_calls`, `cache_read_tokens_total`,
       `cache_creation_tokens_total`, `total_cost_usd`,
       `total_wall_time_seconds`, `cache_hit_rate`. Surface in
       `/pipeline/jobs/{id}` response.
14. [ ] **Acceptance test** —
       `tests/acceptance/test_pipeline_cost_targets.py`:
       - Cold run: `total_cost_usd < 0.03`, `cache_read_total > 5_000`
       - Warm rerun (same endpoint): `total_cost_usd < 0.005`,
         `total_llm_calls < 4`
       - `getPayerCompetitors.query_text` contains `lob`
         (extraction quality unchanged)
       - Output token total ≥ 30% lower than pre-O5a baseline
         (compact JSON / XML wins).

---

## Companion implementation prompt

A self-contained Claude Code prompt for landing this ADR will live at
`docs/adrs/SONNET-IMPLEMENTATION-PROMPT-ADR-0049.md`, sequenced so C1
ships first (validates the cache wire-up), then O1 + C4 (biggest
win/cost ratio), then the rest in any order.

The prompt must include:

- The exact `cache_control` SDK call shape for the Anthropic Python
  SDK version pinned in `pyproject.toml`.
- A Postgres migration for the `source_job_id` + 30-day TTL on
  `extraction_queue`.
- The `cache_read_tokens > 0` assertion location in the existing
  acceptance test harness.
- A telemetry schema diff for the `/pipeline/jobs/{id}` response.
