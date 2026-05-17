# A1.3 — Anthropic Prompt Caching + LRU Query Result Cache

**Status:** Implemented  
**Budget:** $10 / 1 engineer-week  
**Branch:** `feature/v2-prompt-caching`

## Problem

Every `/query` call against an Anthropic provider re-charges the full system-prompt
token cost (~800–2 000 tokens) even when the system prompt is byte-for-byte identical
across calls.  Repeated questions about the same codebase (common during demos and
in CI acceptance tests) re-run the full LLM pipeline, wasting latency and cost.

## Solution

Two complementary optimisations:

### 1. Anthropic `cache_control` breakpoints (server-side cache)

Anthropic's Prompt Caching API caches tokens up to a designated breakpoint at the
server level for 5 minutes (ephemeral).  Cache reads cost 10× less than full input
tokens.  Adding `{"type": "ephemeral"}` cache_control to the system prompt block
ensures that every call after the first in a 5-minute window pays only the read
rate for the system prompt.

The `AnthropicProvider` already adds `cache_control` on the system block.
`PromptCacheWrapper` adds a second breakpoint on large user-message context blocks
(>500 estimated tokens) so the assembled codebase context is also cached when the
same context block appears across consecutive calls (common in iterative exploration).

### 2. LRU in-process query result cache (client-side cache)

Identical `(question, workspace_id)` pairs within a 5-minute window get an
immediate cache hit without touching the LLM at all.  Uses an in-process dict
with SHA-256 key and LRU eviction (maxsize 256 entries).

## Scope

| File | Action |
|------|--------|
| `companybrain/llm/prompt_cache.py` | NEW — `PromptCacheWrapper` |
| `companybrain/cache/__init__.py` | NEW — package |
| `companybrain/cache/query_cache.py` | NEW — `QueryResultCache` |
| `companybrain/config.py` | APPEND — four cache tunables |
| `companybrain/api/routes/query.py` | APPEND — cache lookup + store |
| `tests/unit/test_prompt_cache.py` | NEW |
| `tests/unit/test_query_cache.py` | NEW |

## Acceptance Criteria

- `QueryResultCache.get()` returns cached result for same question+workspace within TTL
- `QueryResultCache.get()` returns `None` after TTL expires
- `PromptCacheWrapper.add_cache_breakpoints()` adds breakpoints only on `anthropic` provider
- Config flag `query_cache_enabled=False` disables the LRU cache in `/query`
- All unit tests pass
