"""
ADR-0044 PR-0044-3: Lookup tool — symbol declaration finder for chunk extraction.

Implements look_up(symbol) used by ChunkExtractor when the LLM needs to see
the definition of a referenced symbol that isn't in the current chunk.

No LLM involved — pure tree-sitter / regex scan over the repo file index.
Caps at LOOKUP_QUOTA_PER_CHUNK calls; further calls return a quota message.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

import structlog

log = structlog.get_logger(__name__)

LOOKUP_QUOTA_PER_CHUNK = 5
_EXTENSIONS = (".java", ".kt", ".py", ".ts", ".tsx", ".go", ".rb", ".rs", ".cs")


class SymbolIndex:
    """
    Repo-level index of symbol declarations.
    Built once at orchestrator start; reused across all chunk workers.
    """

    def __init__(self):
        self._index: dict[str, str] = {}  # symbol_name → declaration text

    def build(self, repo_roots: list[str | Path]) -> None:
        """
        Walk repo roots and index every named declaration.
        Called once before workers start.
        """
        for root in repo_roots:
            root_path = Path(root)
            if not root_path.exists():
                continue
            for ext in _EXTENSIONS:
                for fpath in root_path.rglob(f"*{ext}"):
                    try:
                        self._index_file(fpath)
                    except Exception as e:
                        log.debug("[lookup] index error", file=str(fpath), error=str(e))
        log.info("[lookup] index built", symbols=len(self._index))

    def _index_file(self, fpath: Path) -> None:
        content = fpath.read_text(errors="replace")
        ext = fpath.suffix.lower()
        if ext in (".java", ".kt"):
            self._index_java(content)
        elif ext == ".py":
            self._index_python(content)
        elif ext in (".ts", ".tsx", ".js", ".jsx"):
            self._index_typescript(content)
        elif ext == ".go":
            self._index_go(content)

    # ── Language-specific scanners (extract first ~10 lines of each decl) ──────

    def _index_java(self, content: str) -> None:
        lines = content.splitlines()
        # Class/interface declarations
        for i, line in enumerate(lines):
            m = re.search(
                r'(?:public|protected|private)?\s*(?:class|interface|enum|record)\s+(\w+)',
                line,
            )
            if m:
                name = m.group(1)
                decl = "\n".join(lines[i : i + 8])
                self._index.setdefault(name, decl)

        # Field declarations with @Query or constants
        for i, line in enumerate(lines):
            m = re.search(r'(?:public|private|protected|static|final)\s+\w+\s+(\w+)\s*[=;]', line)
            if m:
                name = m.group(1)
                self._index.setdefault(name, line.strip())

    def _index_python(self, content: str) -> None:
        lines = content.splitlines()
        for i, line in enumerate(lines):
            m = re.match(r'^(?:class|def|async def)\s+(\w+)', line)
            if m:
                name = m.group(1)
                decl = "\n".join(lines[i : i + 5])
                self._index.setdefault(name, decl)

    def _index_typescript(self, content: str) -> None:
        lines = content.splitlines()
        for i, line in enumerate(lines):
            m = re.search(
                r'(?:export\s+)?(?:class|interface|type|enum|function|const|let)\s+(\w+)',
                line,
            )
            if m:
                name = m.group(1)
                decl = "\n".join(lines[i : i + 6])
                self._index.setdefault(name, decl)

    def _index_go(self, content: str) -> None:
        lines = content.splitlines()
        for i, line in enumerate(lines):
            m = re.match(r'^(?:func|type|var|const)\s+', line)
            if m:
                name_m = re.search(r'\s(\w+)', line)
                if name_m:
                    name = name_m.group(1)
                    decl = "\n".join(lines[i : i + 5])
                    self._index.setdefault(name, decl)

    def look_up(self, symbol: str) -> Optional[str]:
        """Return the declaration text for a symbol, or None if not found."""
        # Exact match first
        if symbol in self._index:
            return self._index[symbol]
        # Try stripping common prefixes (Tables.PLAN_INFO → PLAN_INFO)
        parts = symbol.rsplit(".", 1)
        if len(parts) == 2 and parts[1] in self._index:
            return self._index[parts[1]]
        return None


class LookupTool:
    """
    Per-chunk lookup tool — wraps SymbolIndex with a per-call quota counter.
    Instantiate one per chunk extraction call; do not share across chunks.
    """

    def __init__(self, index: SymbolIndex):
        self._index = index
        self._calls = 0

    def look_up(self, symbol: str) -> str:
        """
        Return the declaration text for `symbol`.
        Returns a quota-exhausted message after LOOKUP_QUOTA_PER_CHUNK calls.
        """
        if self._calls >= LOOKUP_QUOTA_PER_CHUNK:
            return "lookup quota exhausted"
        self._calls += 1
        result = self._index.look_up(symbol)
        if result is None:
            return f"symbol not found: {symbol}"
        log.debug("[lookup] hit", symbol=symbol, calls=self._calls)
        return result

    @property
    def calls_used(self) -> int:
        return self._calls


# ── Global singleton index (built once per process) ────────────────────────────

_GLOBAL_INDEX: Optional[SymbolIndex] = None


def get_symbol_index(repo_roots: list[str | Path] | None = None) -> SymbolIndex:
    """
    Return (or lazily build) the global SymbolIndex.
    Pass repo_roots on first call to populate; omit on subsequent calls.
    """
    global _GLOBAL_INDEX
    if _GLOBAL_INDEX is None:
        _GLOBAL_INDEX = SymbolIndex()
        if repo_roots:
            _GLOBAL_INDEX.build(repo_roots)
    return _GLOBAL_INDEX


def reset_symbol_index() -> None:
    """Reset the global index (used in tests)."""
    global _GLOBAL_INDEX
    _GLOBAL_INDEX = None
