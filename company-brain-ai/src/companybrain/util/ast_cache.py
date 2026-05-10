"""Tree-sitter parse cache keyed by (file_path, body_hash).

Avoids re-parsing the same source file multiple times per job when both the
chunker and the structural pre-pass need the AST.  Pass one AstCache instance
per job and share it across all consumers.
"""
from __future__ import annotations

from typing import Any


class AstCache:
    """In-memory tree-sitter parse cache for a single pipeline job."""

    def __init__(self) -> None:
        self._cache: dict[tuple[str, str], Any] = {}

    def parse(
        self,
        lang_parser: Any,
        source_bytes: bytes,
        key: tuple[str, str],
    ) -> Any:
        """Return cached tree if available, otherwise parse and cache."""
        if key in self._cache:
            return self._cache[key]
        tree = lang_parser.parse(source_bytes)
        self._cache[key] = tree
        return tree

    def clear(self) -> None:
        self._cache.clear()

    def __len__(self) -> int:
        return len(self._cache)
