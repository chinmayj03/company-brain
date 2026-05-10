"""Per-job FileCache — read each file once, share across all consumers.

Today the same 30 KB Java file is read 4-5× per job (navigator, chunker,
structural pre-pass, hybrid searcher index, embedding payload). FileCache
de-dupes those reads with a bounded LRU.
"""
from __future__ import annotations

from collections import OrderedDict
from pathlib import Path


class FileCache:
    """Bounded LRU cache for file reads within a single pipeline job.

    Pass one instance per job and thread it through all consumers
    (code_chunker, navigator_agent, code_tracer, structural_prepass).
    Do NOT share across jobs — the cache is not invalidated on file writes.
    """

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
        return {
            "hits": self._hits,
            "misses": self._misses,
            "size": len(self._cache),
        }
