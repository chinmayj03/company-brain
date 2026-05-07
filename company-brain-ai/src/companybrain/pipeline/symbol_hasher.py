"""
SymbolHasher — per-symbol content hashing for incremental re-extraction.

Instead of hashing whole files (which re-extracts all methods when any line changes),
SymbolHasher extracts the content of each named symbol (method/class/function)
and hashes them individually.

A change event is emitted only for symbols whose hash changed.

Usage::
    hasher = SymbolHasher()
    current = hasher.hash_file(path, language)
    # current: dict[str, str] = {symbol_name: sha256_hash}

    changed = hasher.diff(previous_hashes, current)
    # changed: set of symbol names that need re-extraction
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import NamedTuple
import structlog

log = structlog.get_logger(__name__)


class SymbolHash(NamedTuple):
    name: str
    hash: str
    start_line: int
    end_line: int
    symbol_type: str    # "method" | "class" | "function"


class SymbolHasher:
    """
    Extracts per-symbol content hashes from source files.
    Uses tree-sitter when available, regex fallback otherwise.
    """

    def hash_file(self, path: Path, language: str | None = None) -> dict[str, SymbolHash]:
        """
        Return a dict of {symbol_name: SymbolHash} for all named symbols in the file.
        Returns empty dict on parse failure.
        """
        if not path.exists():
            return {}
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return {}

        lang = language or self._detect_language(path)
        try:
            return self._hash_with_treesitter(content, lang, path)
        except Exception:
            return self._hash_with_regex(content, lang, path)

    def diff(
        self,
        previous: dict[str, SymbolHash],
        current: dict[str, SymbolHash],
    ) -> set[str]:
        """
        Return set of symbol names that need re-extraction:
        - Hash changed
        - New symbol (added)
        Does NOT include deleted symbols (caller handles invalidation).
        """
        changed: set[str] = set()
        for name, sym in current.items():
            if name not in previous or previous[name].hash != sym.hash:
                changed.add(name)
        return changed

    def _hash_with_treesitter(self, content: str, language: str, path: Path) -> dict[str, SymbolHash]:
        """Use tree-sitter to extract precise symbol boundaries."""
        try:
            from tree_sitter import Parser
            # Try to get a language grammar
            parser, ts_lang = self._get_parser(language)
            if parser is None:
                raise ValueError("No parser")

            tree = parser.parse(content.encode("utf-8"))
            symbols: dict[str, SymbolHash] = {}

            # Walk tree for method/function/class declarations
            def walk(node, depth=0):
                if depth > 10:
                    return
                if node.type in (
                    "method_declaration", "function_declaration",
                    "constructor_declaration", "class_declaration",
                    "function_definition", "decorated_definition",
                    "arrow_function",
                ):
                    name = self._extract_node_name(node, content)
                    if name:
                        symbol_content = content[node.start_byte:node.end_byte]
                        sym_hash = hashlib.sha256(symbol_content.encode()).hexdigest()[:16]
                        start_line = node.start_point[0] + 1
                        end_line = node.end_point[0] + 1
                        sym_type = "class" if "class" in node.type else "method"
                        symbols[name] = SymbolHash(name, sym_hash, start_line, end_line, sym_type)
                for child in node.children:
                    walk(child, depth + 1)

            walk(tree.root_node)
            return symbols
        except Exception:
            raise

    def _hash_with_regex(self, content: str, language: str, path: Path) -> dict[str, SymbolHash]:
        """Regex fallback: extract method/function names and hash line ranges."""
        symbols: dict[str, SymbolHash] = {}
        lines = content.split("\n")

        # Pattern: capture method/function name and line number
        patterns = {
            "java":       re.compile(r'^\s+(?:public|private|protected|static|\s)+[\w<>\[\]]+\s+(\w+)\s*\('),
            "python":     re.compile(r'^(?:    |\t)?def\s+(\w+)\s*\('),
            "typescript": re.compile(r'^\s+(?:async\s+)?(?:public|private|protected|\s)*(\w+)\s*\('),
            "javascript": re.compile(r'(?:function\s+(\w+)|(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s+)?\()'),
        }
        pat = patterns.get(language, patterns["python"])

        for i, line in enumerate(lines):
            m = pat.search(line)
            if m:
                name = m.group(1) or (m.group(2) if m.lastindex and m.lastindex >= 2 else None)
                if name and len(name) > 2:
                    # Hash from this line to the next method/end of block (approximate)
                    end = min(i + 50, len(lines))
                    symbol_content = "\n".join(lines[i:end])
                    sym_hash = hashlib.sha256(symbol_content.encode()).hexdigest()[:16]
                    symbols[name] = SymbolHash(name, sym_hash, i + 1, end, "method")

        return symbols

    def _get_parser(self, language: str):
        """Return (Parser, language_obj) or (None, None) if unavailable."""
        try:
            from tree_sitter import Parser as TSParser
            import tree_sitter_languages as tsl
            lang_map = {
                "java": "java", "python": "python",
                "typescript": "typescript", "javascript": "javascript",
                "go": "go",
            }
            ts_lang_name = lang_map.get(language)
            if not ts_lang_name:
                return None, None
            lang_obj = tsl.get_language(ts_lang_name)
            parser = TSParser()
            parser.set_language(lang_obj)
            return parser, lang_obj
        except Exception:
            return None, None

    @staticmethod
    def _extract_node_name(node, content: str) -> str | None:
        """Extract the identifier name from a declaration node."""
        for child in node.children:
            if child.type == "identifier":
                return content[child.start_byte:child.end_byte]
        return None

    @staticmethod
    def _detect_language(path: Path) -> str:
        return {
            ".java": "java", ".kt": "kotlin",
            ".py": "python",
            ".ts": "typescript", ".tsx": "typescript",
            ".js": "javascript", ".jsx": "javascript",
            ".go": "go",
        }.get(path.suffix.lower(), "other")
