"""
BM25Index — lexical retrieval over code symbols and file contents.

Uses rank-bm25 (BM25Okapi) for fast lexical scoring.
Index is built per-repo at trace time (not persisted — fast enough for <10k files).

Tokenization is code-aware:
  - Split on camelCase, snake_case, PascalCase boundaries
  - Include original token AND split parts
  - Index: file path + class names + method names + top-level identifiers

Usage::
    index = BM25Index()
    # Add files
    for info in walker.walk_extractable():
        index.add(info.relative_path, info.path.read_text())
    index.build()

    # Query
    results = index.search("getPayerCompetitors competitiveness", top_k=20)
    # results: list of (relative_path, score)
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional
import structlog

log = structlog.get_logger(__name__)

_CAMEL_RE  = re.compile(r'(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])')
_NON_WORD  = re.compile(r'[^a-zA-Z0-9_]')
_STOP_TOKENS = frozenset({
    "the", "a", "an", "is", "in", "of", "to", "for", "and", "or", "not",
    "import", "return", "public", "private", "protected", "static", "void",
    "class", "interface", "extends", "implements", "new", "this", "super",
    "if", "else", "try", "catch", "finally", "throw", "throws",
    "true", "false", "null", "var", "let", "const", "def", "self",
})


@dataclass
class BM25Result:
    relative_path: str
    score: float
    matched_tokens: list[str]


class BM25Index:
    """In-memory BM25 index for code file retrieval."""

    def __init__(self):
        self._docs:   list[str]       = []    # relative paths
        self._corpus: list[list[str]] = []    # tokenized documents
        self._bm25 = None
        self._built = False

    def add(self, relative_path: str, content: str) -> None:
        """Add a file to the index. Call build() after adding all files."""
        tokens = _tokenize_code(content)
        # Boost path tokens: include path parts as tokens
        path_tokens = _tokenize_code(relative_path.replace("/", " ").replace(".", " "))
        self._docs.append(relative_path)
        self._corpus.append(tokens + path_tokens)

    def build(self) -> None:
        """Build the BM25 index. Must be called before search()."""
        if not self._corpus:
            return
        try:
            from rank_bm25 import BM25Okapi  # type: ignore
            self._bm25 = BM25Okapi(self._corpus)
            self._built = True
            log.debug("BM25 index built", documents=len(self._docs))
        except ImportError:
            log.warning("rank-bm25 not installed — BM25 retrieval disabled")

    def search(self, query: str, top_k: int = 50) -> list[BM25Result]:
        """
        Search the BM25 index. Returns top_k results sorted by score descending.
        Returns empty list if index not built or query is empty.
        """
        if not self._built or self._bm25 is None or not query.strip():
            return []

        query_tokens = _tokenize_code(query)
        if not query_tokens:
            return []

        scores = self._bm25.get_scores(query_tokens)

        results = []
        for i, score in enumerate(scores):
            if score > 0:
                results.append(BM25Result(
                    relative_path=self._docs[i],
                    score=float(score),
                    matched_tokens=query_tokens,
                ))

        results.sort(key=lambda r: r.score, reverse=True)
        return results[:top_k]

    @property
    def size(self) -> int:
        return len(self._docs)


def _tokenize_code(text: str) -> list[str]:
    """
    Code-aware tokenizer:
    1. Split on non-word characters
    2. Split camelCase / PascalCase
    3. Lowercase
    4. Remove stop tokens and single chars
    5. Deduplicate while preserving order
    """
    tokens: list[str] = []
    # Split on non-word boundaries
    raw_tokens = _NON_WORD.split(text)

    for token in raw_tokens:
        if not token or len(token) < 2:
            continue
        # Split camelCase
        parts = _CAMEL_RE.sub(r' ', token).split()
        # Add both original and parts
        all_parts = [token.lower()] + [p.lower() for p in parts if len(p) >= 2]
        for part in all_parts:
            if part not in _STOP_TOKENS and len(part) >= 2:
                tokens.append(part)

    # Deduplicate preserving order
    seen = set()
    deduped = []
    for t in tokens:
        if t not in seen:
            seen.add(t)
            deduped.append(t)
    return deduped
