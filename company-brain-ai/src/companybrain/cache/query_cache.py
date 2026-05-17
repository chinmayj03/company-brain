"""
A1.3 — QueryResultCache

LRU in-memory cache for /query results.  Identical (question, workspace_id)
pairs within the TTL window are served from cache without touching the LLM,
eliminating the full token cost of repeated queries.

Design choices:
  - SHA-256 key avoids storing raw question strings as dict keys (privacy +
    memory).
  - OrderedDict provides O(1) LRU eviction without an additional dependency.
  - TTL is checked on every get(); stale entries are evicted lazily on access
    rather than via a background timer, keeping the implementation simple and
    free of thread/async complexity.
  - Thread-safety: a threading.Lock guards all mutations so the cache is safe
    in multi-threaded FastAPI workers.

Configuration (via config.py / env vars):
  QUERY_CACHE_TTL_SECONDS   default 300  (5 minutes)
  QUERY_CACHE_MAXSIZE       default 256  entries
"""
from __future__ import annotations

import hashlib
import os
import threading
import time
from collections import OrderedDict
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from companybrain.models.query_response import QueryResponse

# Fallback defaults when config is not yet initialised (e.g. during unit tests).
_DEFAULT_TTL: int = int(os.environ.get("QUERY_CACHE_TTL_SECONDS", "300"))
_DEFAULT_MAXSIZE: int = int(os.environ.get("QUERY_CACHE_MAXSIZE", "256"))


class QueryResultCache:
    """Thread-safe LRU cache for QueryResponse objects.

    Keys are SHA-256 hashes of ``(question, workspace_id)``; values are
    ``(QueryResponse, unix_timestamp_float)`` tuples.  Entries older than
    ``ttl_seconds`` are treated as cache misses and evicted on access.
    When the cache reaches ``maxsize`` the least-recently-used entry is
    evicted to make room.

    Example::

        cache = QueryResultCache(maxsize=256, ttl_seconds=300)
        hit = cache.get("What does PaymentService do?", "ws-123")
        if hit is None:
            result = await llm_query(...)
            cache.put("What does PaymentService do?", "ws-123", result)
    """

    def __init__(
        self,
        maxsize: int = _DEFAULT_MAXSIZE,
        ttl_seconds: int = _DEFAULT_TTL,
    ) -> None:
        self._maxsize = maxsize
        self._ttl = ttl_seconds
        # OrderedDict preserves insertion order → last accessed = most recent.
        self._cache: OrderedDict[str, tuple[object, float]] = OrderedDict()
        self._lock = threading.Lock()

    # ── Public API ────────────────────────────────────────────────────────────

    def get(self, question: str, workspace_id: str) -> Optional["QueryResponse"]:
        """Return a cached QueryResponse, or None on miss / expiry.

        On a cache hit the entry is moved to the end (most-recently-used)
        so it is the last to be evicted.  Stale entries are evicted on access.
        """
        key = self._make_key(question, workspace_id)
        with self._lock:
            if key not in self._cache:
                return None
            response, ts = self._cache[key]
            if time.time() - ts > self._ttl:
                # Entry is stale — evict and report a miss.
                del self._cache[key]
                return None
            # Move to end to mark as most-recently-used.
            self._cache.move_to_end(key)
            return response  # type: ignore[return-value]

    def put(
        self,
        question: str,
        workspace_id: str,
        response: "QueryResponse",
    ) -> None:
        """Store a QueryResponse in the cache, evicting LRU entry if at capacity."""
        key = self._make_key(question, workspace_id)
        with self._lock:
            if key in self._cache:
                # Overwrite and refresh timestamp; move to most-recently-used.
                self._cache.move_to_end(key)
            self._cache[key] = (response, time.time())
            # Enforce maxsize — evict the least-recently-used entry (first item).
            while len(self._cache) > self._maxsize:
                self._cache.popitem(last=False)

    def clear(self) -> None:
        """Evict all entries.  Useful in tests and on workspace reset."""
        with self._lock:
            self._cache.clear()

    def size(self) -> int:
        """Return the current number of entries in the cache."""
        with self._lock:
            return len(self._cache)

    # ── Internals ─────────────────────────────────────────────────────────────

    def _make_key(self, question: str, workspace_id: str) -> str:
        """Return a stable SHA-256 hex digest for the (question, workspace_id) pair."""
        raw = f"{workspace_id}\x00{question}".encode("utf-8")
        return hashlib.sha256(raw).hexdigest()


# ── Process-level singleton ───────────────────────────────────────────────────

_singleton: Optional[QueryResultCache] = None
_singleton_lock = threading.Lock()


def get_query_cache() -> QueryResultCache:
    """Return the process-level QueryResultCache singleton.

    Lazily initialised on first call using config.py values (or env-var
    fallbacks when config is unavailable).  The same instance is returned on
    every subsequent call so callers share state across the process lifetime.
    """
    global _singleton
    if _singleton is not None:
        return _singleton
    with _singleton_lock:
        if _singleton is not None:
            return _singleton
        try:
            from companybrain.config import settings
            maxsize = settings.query_cache_maxsize
            ttl = settings.query_cache_ttl_seconds
        except Exception:
            maxsize = _DEFAULT_MAXSIZE
            ttl = _DEFAULT_TTL
        _singleton = QueryResultCache(maxsize=maxsize, ttl_seconds=ttl)
    return _singleton
