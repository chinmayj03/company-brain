"""
BM25 indexes for the Company Brain retrieval stack.

Two classes are provided:
  Bm25Index  — ADR-0015: per-(workspace, entity_type) index persisted to
               .brain/.bm25/ using the bm25s library.
  BM25Index  — Legacy in-memory file-based index (kept for backward compat).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import bm25s
import structlog

from companybrain.retrieval.tokenize import tokenize_code

log = structlog.get_logger(__name__)


# ── ADR-0015: Bm25Index — entity-based, persisted ────────────────────────────

class Bm25Index:
    """One BM25 corpus per (workspace_slug, entity_type), persisted to disk.

    bm25s is significantly faster than rank_bm25 and supports incremental
    updates by re-saving the corpus.
    """

    def __init__(self, root: Path, workspace_slug: str, entity_type: str):
        self.root = Path(root) / ".brain" / ".bm25" / workspace_slug / entity_type
        self.root.mkdir(parents=True, exist_ok=True)
        self._corpus_path = self.root / "corpus.jsonl"
        self._bm25: Optional[bm25s.BM25] = None
        self._doc_ids: list[str] = []
        self._unsaved: list[tuple[str, str]] = []
        self._load_existing()

    def _load_existing(self) -> None:
        """Load and rebuild the BM25 index from a previously persisted corpus."""
        if not self._corpus_path.exists():
            return
        corpus: dict[str, str] = {}
        for line in self._corpus_path.read_text().splitlines():
            if line.strip():
                row = json.loads(line)
                corpus[row["id"]] = row["text"]
        if not corpus:
            return
        self._doc_ids = list(corpus.keys())
        tokenised = [tokenize_code(corpus[d]) for d in self._doc_ids]
        self._bm25 = bm25s.BM25()
        self._bm25.index(tokenised)

    def upsert(self, doc_id: str, text: str) -> None:
        """Stage a doc for indexing; call flush() to materialise."""
        self._unsaved.append((doc_id, text))

    def flush(self) -> None:
        """Persist staged upserts and rebuild the in-memory BM25 index."""
        corpus: dict[str, str] = {}
        if self._corpus_path.exists():
            for line in self._corpus_path.read_text().splitlines():
                if line.strip():
                    row = json.loads(line)
                    corpus[row["id"]] = row["text"]
        for doc_id, text in self._unsaved:
            corpus[doc_id] = text
        self._unsaved.clear()

        with self._corpus_path.open("w") as f:
            for doc_id, text in corpus.items():
                f.write(json.dumps({"id": doc_id, "text": text}) + "\n")

        self._doc_ids = list(corpus.keys())
        tokenised = [tokenize_code(corpus[d]) for d in self._doc_ids]
        self._bm25 = bm25s.BM25()
        self._bm25.index(tokenised)

    def search(self, query: str, top_k: int = 40) -> list[tuple[str, float]]:
        """Return list of (urn, score) sorted descending."""
        if not self._bm25 or not self._doc_ids:
            return []
        tokens = tokenize_code(query)
        if not tokens:
            return []
        k = min(top_k, len(self._doc_ids))
        # bm25s.BM25.retrieve returns a named tuple (documents, scores) — documents
        # first. Unpacking the other way silently produced garbage results
        # (scores treated as int indices via `int(3.29)=3`, real scores never used).
        idx, scores = self._bm25.retrieve([tokens], k=k)
        return [
            (self._doc_ids[int(idx[0][i])], float(scores[0][i]))
            for i in range(len(idx[0]))
        ]


# ── Legacy: BM25Index — in-memory file-based index ───────────────────────────

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
    """In-memory BM25 index for code file retrieval (legacy, file-based)."""

    def __init__(self):
        self._docs:   list[str]       = []
        self._corpus: list[list[str]] = []
        self._bm25 = None
        self._built = False

    def add(self, relative_path: str, content: str) -> None:
        tokens = _tokenize_code_legacy(content)
        path_tokens = _tokenize_code_legacy(relative_path.replace("/", " ").replace(".", " "))
        self._docs.append(relative_path)
        self._corpus.append(tokens + path_tokens)

    def build(self) -> None:
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
        if not self._built or self._bm25 is None or not query.strip():
            return []
        query_tokens = _tokenize_code_legacy(query)
        if not query_tokens:
            return []
        scores = self._bm25.get_scores(query_tokens)
        results = [
            BM25Result(relative_path=self._docs[i], score=float(s), matched_tokens=query_tokens)
            for i, s in enumerate(scores) if s > 0
        ]
        results.sort(key=lambda r: r.score, reverse=True)
        return results[:top_k]

    @property
    def size(self) -> int:
        return len(self._docs)


def _tokenize_code_legacy(text: str) -> list[str]:
    tokens: list[str] = []
    for token in _NON_WORD.split(text):
        if not token or len(token) < 2:
            continue
        parts = _CAMEL_RE.sub(r' ', token).split()
        all_parts = [token.lower()] + [p.lower() for p in parts if len(p) >= 2]
        for part in all_parts:
            if part not in _STOP_TOKENS and len(part) >= 2:
                tokens.append(part)
    seen: set[str] = set()
    deduped = []
    for t in tokens:
        if t not in seen:
            seen.add(t)
            deduped.append(t)
    return deduped
