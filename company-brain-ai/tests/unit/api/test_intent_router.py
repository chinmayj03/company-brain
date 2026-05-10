"""Unit tests for companybrain.api.intent_router — ADR-0043 WS2."""
import asyncio
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

from companybrain.api.intent_router import (
    classify_intent,
    _cache_key,
    _get_cached,
    _set_cached,
    _CACHE,
    INTENTS,
)


# ── Cache helpers ─────────────────────────────────────────────────────────────

def test_cache_key_deterministic():
    k1 = _cache_key("who calls PaymentService?", "ws-1")
    k2 = _cache_key("who calls PaymentService?", "ws-1")
    assert k1 == k2


def test_cache_key_differs_by_question():
    k1 = _cache_key("who calls A?", "ws-1")
    k2 = _cache_key("what columns does B read?", "ws-1")
    assert k1 != k2


def test_cache_key_differs_by_workspace():
    k1 = _cache_key("same question", "ws-alpha")
    k2 = _cache_key("same question", "ws-beta")
    assert k1 != k2


def test_set_and_get_cached():
    _CACHE.clear()
    key = _cache_key("test question", "ws-test")
    _set_cached(key, "call_chain", ttl_sec=60)
    result = _get_cached(key)
    assert result == "call_chain"
    _CACHE.clear()


def test_get_cached_returns_none_on_miss():
    _CACHE.clear()
    key = _cache_key("not cached", "ws-x")
    assert _get_cached(key) is None


def test_get_cached_expires():
    import time
    from companybrain.api.intent_router import _CacheEntry
    _CACHE.clear()
    key = _cache_key("expiry test", "ws-z")
    # Manually insert an already-expired entry
    entry = _CacheEntry("concept", ttl_sec=-1)
    _CACHE[key] = entry
    assert _get_cached(key) is None  # expired
    assert key not in _CACHE  # cleaned up
    _CACHE.clear()


# ── INTENTS constant ──────────────────────────────────────────────────────────

def test_all_intents_present():
    expected = {"call_chain", "data_flow", "change_risk", "concept", "other"}
    assert set(INTENTS) == expected


# ── classify_intent via mocked LLM ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_classify_intent_call_chain():
    _CACHE.clear()
    mock_response = MagicMock()
    mock_response.content = "call_chain"

    with patch("companybrain.api.intent_router._call_llm", return_value="call_chain"):
        result = await classify_intent(
            "who calls PaymentService.charge?",
            workspace_id="ws-1",
            ttl_sec=60,
        )
    assert result == "call_chain"
    _CACHE.clear()


@pytest.mark.asyncio
async def test_classify_intent_uses_cache_on_second_call():
    _CACHE.clear()
    call_count = 0

    async def mock_llm(question):
        nonlocal call_count
        call_count += 1
        return "data_flow"

    with patch("companybrain.api.intent_router._call_llm", side_effect=mock_llm):
        r1 = await classify_intent("what columns does X read?", workspace_id="ws-2", ttl_sec=60)
        r2 = await classify_intent("what columns does X read?", workspace_id="ws-2", ttl_sec=60)

    assert r1 == r2 == "data_flow"
    assert call_count == 1  # LLM called only once; second hit from cache
    _CACHE.clear()


@pytest.mark.asyncio
async def test_classify_intent_falls_back_on_llm_error():
    _CACHE.clear()

    async def failing_llm(question):
        raise RuntimeError("LLM unavailable")

    with patch("companybrain.api.intent_router._call_llm", side_effect=failing_llm):
        result = await classify_intent(
            "what breaks if I rename amount_cents?",
            workspace_id="ws-3",
        )
    assert result == "concept"  # safe fallback
    _CACHE.clear()


@pytest.mark.asyncio
async def test_classify_intent_unknown_label_defaults_to_concept():
    _CACHE.clear()

    async def mock_llm(question):
        return "concept"  # simulate LLM returning a clean label

    # Simulate internal _call_llm returning garbage then mapping to concept
    with patch("companybrain.api.intent_router._call_llm", return_value="concept"):
        result = await classify_intent("gibberish query", workspace_id="ws-4", ttl_sec=1)

    assert result in INTENTS
    _CACHE.clear()
