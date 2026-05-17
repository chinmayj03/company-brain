"""
Unit tests for companybrain.cache.query_cache.QueryResultCache (A1.3).

Verifies:
  - get() returns None on cache miss.
  - put() then get() returns the stored response within TTL.
  - get() returns None after TTL expires.
  - Different workspace_id is a cache miss.
  - Different question is a cache miss.
  - LRU eviction happens at maxsize.
  - clear() empties the cache.
  - size() reports the correct count.
  - get_query_cache() returns a singleton.
"""
from __future__ import annotations

import time
from unittest.mock import patch, MagicMock

import pytest

from companybrain.cache.query_cache import QueryResultCache, get_query_cache


# ── Fixtures ────────────────────────────────────────────────────────────────────

def _make_response(summary: str = "Test answer") -> MagicMock:
    """Return a minimal mock that behaves like a QueryResponse."""
    r = MagicMock()
    r.summary = summary
    return r


# ── Tests ──────────────────────────────────────────────────────────────────────

class TestQueryResultCache:

    # ── Basic get/put ──────────────────────────────────────────────────────────

    def test_get_returns_none_on_miss(self):
        cache = QueryResultCache(maxsize=10, ttl_seconds=60)
        result = cache.get("What does Foo do?", "ws-123")
        assert result is None

    def test_put_then_get_returns_response(self):
        cache = QueryResultCache(maxsize=10, ttl_seconds=60)
        response = _make_response("Foo is a controller")
        cache.put("What does Foo do?", "ws-123", response)
        hit = cache.get("What does Foo do?", "ws-123")
        assert hit is response

    def test_put_overwrites_existing_entry(self):
        cache = QueryResultCache(maxsize=10, ttl_seconds=60)
        r1 = _make_response("v1")
        r2 = _make_response("v2")
        cache.put("Q", "ws-1", r1)
        cache.put("Q", "ws-1", r2)
        assert cache.get("Q", "ws-1") is r2

    # ── TTL expiry ────────────────────────────────────────────────────────────

    def test_get_returns_none_after_ttl_expires(self):
        cache = QueryResultCache(maxsize=10, ttl_seconds=1)
        cache.put("Q", "ws-1", _make_response())
        # Fake the passage of time past TTL
        with patch("companybrain.cache.query_cache.time") as mock_time:
            # First call (put) at t=0
            mock_time.time.return_value = 0.0
            cache2 = QueryResultCache(maxsize=10, ttl_seconds=5)
            cache2.put("Q", "ws-1", _make_response("fresh"))
            # Second call (get) at t=6 — past TTL
            mock_time.time.return_value = 6.0
            result = cache2.get("Q", "ws-1")
        assert result is None

    def test_get_returns_value_within_ttl(self):
        cache = QueryResultCache(maxsize=10, ttl_seconds=300)
        response = _make_response("within TTL")
        with patch("companybrain.cache.query_cache.time") as mock_time:
            mock_time.time.return_value = 1000.0
            cache.put("Q", "ws-1", response)
            mock_time.time.return_value = 1299.9   # just under 300s TTL
            result = cache.get("Q", "ws-1")
        assert result is response

    def test_stale_entry_is_evicted_on_access(self):
        """After a TTL miss the entry should be removed from the cache."""
        cache = QueryResultCache(maxsize=10, ttl_seconds=5)
        with patch("companybrain.cache.query_cache.time") as mock_time:
            mock_time.time.return_value = 0.0
            cache.put("Q", "ws-1", _make_response())
            assert cache.size() == 1
            mock_time.time.return_value = 10.0
            cache.get("Q", "ws-1")   # stale hit → evict
        assert cache.size() == 0

    # ── Cache key isolation ────────────────────────────────────────────────────

    def test_different_workspace_id_is_cache_miss(self):
        cache = QueryResultCache(maxsize=10, ttl_seconds=60)
        cache.put("Q", "ws-AAA", _make_response("answer A"))
        result = cache.get("Q", "ws-BBB")
        assert result is None

    def test_different_question_is_cache_miss(self):
        cache = QueryResultCache(maxsize=10, ttl_seconds=60)
        cache.put("What is A?", "ws-1", _make_response("A"))
        result = cache.get("What is B?", "ws-1")
        assert result is None

    def test_same_question_different_case_is_cache_miss(self):
        """Keys are case-sensitive — different casing is a different question."""
        cache = QueryResultCache(maxsize=10, ttl_seconds=60)
        cache.put("what is foo?", "ws-1", _make_response())
        result = cache.get("What is foo?", "ws-1")
        assert result is None

    # ── LRU eviction ──────────────────────────────────────────────────────────

    def test_lru_eviction_at_maxsize(self):
        """When cache is full the oldest (least-recently-used) entry is evicted."""
        cache = QueryResultCache(maxsize=3, ttl_seconds=60)
        cache.put("Q1", "ws", _make_response("1"))
        cache.put("Q2", "ws", _make_response("2"))
        cache.put("Q3", "ws", _make_response("3"))
        assert cache.size() == 3

        # Access Q1 to refresh its LRU position
        cache.get("Q1", "ws")

        # Adding Q4 should evict Q2 (the now-least-recently-used entry).
        cache.put("Q4", "ws", _make_response("4"))
        assert cache.size() == 3
        assert cache.get("Q2", "ws") is None  # evicted
        assert cache.get("Q1", "ws") is not None  # still present
        assert cache.get("Q3", "ws") is not None  # still present
        assert cache.get("Q4", "ws") is not None  # just inserted

    def test_maxsize_one_always_replaces(self):
        """maxsize=1 means each put evicts the previous entry."""
        cache = QueryResultCache(maxsize=1, ttl_seconds=60)
        r1 = _make_response("first")
        r2 = _make_response("second")
        cache.put("Q1", "ws", r1)
        cache.put("Q2", "ws", r2)
        assert cache.size() == 1
        assert cache.get("Q1", "ws") is None
        assert cache.get("Q2", "ws") is r2

    # ── Utility methods ────────────────────────────────────────────────────────

    def test_clear_empties_cache(self):
        cache = QueryResultCache(maxsize=10, ttl_seconds=60)
        cache.put("Q1", "ws", _make_response())
        cache.put("Q2", "ws", _make_response())
        cache.clear()
        assert cache.size() == 0
        assert cache.get("Q1", "ws") is None

    def test_size_reflects_current_entries(self):
        cache = QueryResultCache(maxsize=10, ttl_seconds=60)
        assert cache.size() == 0
        cache.put("Q1", "ws", _make_response())
        assert cache.size() == 1
        cache.put("Q2", "ws", _make_response())
        assert cache.size() == 2

    # ── _make_key stability ────────────────────────────────────────────────────

    def test_make_key_is_deterministic(self):
        cache = QueryResultCache()
        k1 = cache._make_key("hello", "ws-1")
        k2 = cache._make_key("hello", "ws-1")
        assert k1 == k2

    def test_make_key_differs_for_different_inputs(self):
        cache = QueryResultCache()
        assert cache._make_key("Q", "ws-1") != cache._make_key("Q", "ws-2")
        assert cache._make_key("Q1", "ws") != cache._make_key("Q2", "ws")

    def test_make_key_is_sha256_hex(self):
        import hashlib
        cache = QueryResultCache()
        key = cache._make_key("question", "workspace")
        raw = "workspace\x00question".encode("utf-8")
        expected = hashlib.sha256(raw).hexdigest()
        assert key == expected

    # ── Singleton ─────────────────────────────────────────────────────────────

    def test_get_query_cache_returns_same_instance(self):
        """get_query_cache() must return the same object on repeated calls."""
        # Reset the singleton so test isolation is clean.
        import companybrain.cache.query_cache as qc_mod
        original = qc_mod._singleton
        qc_mod._singleton = None
        try:
            a = get_query_cache()
            b = get_query_cache()
            assert a is b
        finally:
            qc_mod._singleton = original

    def test_get_query_cache_uses_config_values(self):
        """Singleton is built with maxsize/ttl from config when available."""
        import companybrain.cache.query_cache as qc_mod
        original = qc_mod._singleton
        qc_mod._singleton = None
        try:
            with patch("companybrain.cache.query_cache.QueryResultCache") as MockCache:
                MockCache.return_value = MagicMock()
                from companybrain.config import settings
                get_query_cache()
                MockCache.assert_called_once_with(
                    maxsize=settings.query_cache_maxsize,
                    ttl_seconds=settings.query_cache_ttl_seconds,
                )
        finally:
            qc_mod._singleton = original
