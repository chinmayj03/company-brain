# A1.3 Implementation Plan

## Step 1 — `companybrain/llm/prompt_cache.py` (NEW)

`PromptCacheWrapper` provides a single method:

```
add_cache_breakpoints(provider_name, messages) -> messages
```

Logic:
- If `provider_name != "anthropic"` → return messages unchanged (no-op)
- Scan user messages in reverse order for the first message whose content
  length exceeds `~2 000 chars` (≈500 tokens at ~4 chars/token).
- If found, convert its `content` to a content block list and append
  `cache_control: {type: "ephemeral"}` to the last block.
- The system prompt already has a breakpoint in `AnthropicProvider` — this
  wrapper adds a second breakpoint on the context block so up to two cache
  segments are active per call.

## Step 2 — `companybrain/cache/query_cache.py` (NEW)

`QueryResultCache`:
- `_cache: OrderedDict[str, tuple[QueryResponse, float]]`
  — key = SHA-256(question + workspace_id), value = (response, unix_timestamp)
- `get(question, workspace_id) -> Optional[QueryResponse]`
  — returns None if key missing or `time.time() - ts > TTL`
  — evicts stale entry on cache miss
- `put(question, workspace_id, response)` — inserts and enforces `maxsize` via
  LRU eviction (pop the oldest entry when at capacity)
- `_make_key(question, workspace_id) -> str` — `hashlib.sha256(...).hexdigest()`

## Step 3 — `companybrain/config.py` additions

```python
# ── A1.3: Prompt + query cache ────────────────────────────────────────────────
anthropic_cache_enabled: bool = True
query_cache_enabled: bool = True
query_cache_ttl_seconds: int = 300
query_cache_maxsize: int = 256
```

## Step 4 — `api/routes/query.py` wiring (APPEND-ONLY)

Module-level singleton:

```python
_query_result_cache: QueryResultCache | None = None

def _get_query_cache() -> QueryResultCache | None:
    ...
```

In `query_graph()`, before `t0 = time.monotonic()`:

```python
if settings.query_cache_enabled:
    cached = _get_query_cache().get(request.question, str(request.workspace_id))
    if cached is not None:
        return cached
```

After `render_to_markdown(query_response)`:

```python
if settings.query_cache_enabled:
    _get_query_cache().put(request.question, str(request.workspace_id), query_response)
```

## Step 5 — Tests

`test_prompt_cache.py`:
- `test_adds_breakpoint_on_anthropic_large_message`
- `test_no_breakpoint_on_small_message`
- `test_no_op_for_non_anthropic_provider`
- `test_preserves_existing_content_structure`

`test_query_cache.py`:
- `test_get_returns_none_on_miss`
- `test_put_then_get_returns_response`
- `test_ttl_expiry_returns_none`
- `test_different_workspace_is_cache_miss`
- `test_different_question_is_cache_miss`
- `test_lru_eviction_at_maxsize`

## Cost Impact

Prompt caching savings (estimated, Sonnet pricing):
- System prompt: ~1 200 tokens → cache read saves $0.0000036 per call
- Context block: ~8 000 tokens → cache read saves $0.000024 per call
- At 500 queries/day with 80% cache hit rate: **~$0.011/day saved** (≈$4/year)
  — meaningful at scale, breakeven day 1 for free via Anthropic's server cache

Query result cache savings:
- Eliminates full LLM call on repeated questions
- Typical /query call: 8 000 input + 500 output tokens ≈ $0.0315 per call (Sonnet)
- At 10% repeat rate across 500 queries/day: **~$1.57/day saved**
