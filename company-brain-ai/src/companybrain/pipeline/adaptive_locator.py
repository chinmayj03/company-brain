"""
AdaptiveLocator — ADR-0041 Phase 2.

Maps a class/type name → candidate file paths in a repo, WITHOUT hardcoding
any framework conventions (no @Query, no JpaRepository, no @RequestMapping).

Strategy (generalized):
  1. Name-based heuristic: for a class name like "CompetitorRepository",
     look for files named CompetitorRepository.java (or .kt, .py, .ts) in
     the repo directory tree.
  2. Package-path heuristic: if import statements in known files tell us
     "com.company.foo.CompetitorRepository", look for a file at path
     com/company/foo/CompetitorRepository.java.
  3. Import-graph fallback: if the import graph already has an entry for
     this class, use its recorded file path.

Returns a list of candidate Paths ordered by confidence (most likely first).
The caller (ExtractionLoop) tries each path in order and stops at the first
one that parses without error.
"""

from __future__ import annotations

import re
from pathlib import Path

import structlog

log = structlog.get_logger(__name__)

# Extension priority for each language
_LANG_EXTS = {
    "java":       [".java", ".kt"],
    "python":     [".py"],
    "typescript": [".ts", ".tsx"],
    "javascript": [".js", ".jsx"],
}

# Regex to pull a qualified class name from an import statement
_JAVA_IMPORT_RE   = re.compile(r'import\s+([\w.]+);')
_PYTHON_IMPORT_RE = re.compile(r'from\s+([\w.]+)\s+import\s+(\w+)')
_TS_IMPORT_RE     = re.compile(r"import\s+\{([^}]+)\}\s+from\s+'([^']+)'")


class AdaptiveLocator:
    """
    Resolves type names to candidate file paths within a repo directory.

    Usage:
        locator = AdaptiveLocator(repo_root=Path("/path/to/repo"))
        locator.build_index(code_units)  # optional warm-up
        paths = locator.locate("CompetitorRepository")
    """

    def __init__(self, repo_root: "str | Path") -> None:
        self._root = Path(repo_root)
        # class_name → set of known file paths (from imports + analysis)
        self._import_index: dict[str, set[str]] = {}
        self._file_cache: dict[str, list[Path]] = {}

    def build_index(self, code_units: list) -> None:
        """
        Warm the import index from code_units. Scans import statements in each
        unit's content to map class names → file paths.
        """
        for unit in code_units:
            if not unit.content or not unit.file_path:
                continue
            self._index_imports(unit.content, unit.file_path)

    def locate(self, class_name: str, language: str = "java") -> list[Path]:
        """
        Return candidate Paths for class_name, ordered by confidence.

        Checks:
          1. Import index (already seen in code_units)
          2. Direct name-match file scan under repo root
          3. Nothing found → []
        """
        if class_name in self._file_cache:
            return self._file_cache[class_name]

        candidates: list[tuple[int, Path]] = []  # (score, path)

        # ── Priority 1: import index ──────────────────────────────────────────
        if class_name in self._import_index:
            for rel_path in self._import_index[class_name]:
                full = self._root / rel_path
                if full.exists():
                    candidates.append((100, full))

        # ── Priority 2: name-based file scan ─────────────────────────────────
        if not candidates:
            exts = _LANG_EXTS.get(language, [".java", ".kt", ".py", ".ts"])
            for ext in exts:
                found = list(self._root.rglob(f"{class_name}{ext}"))
                for f in found:
                    # Prefer src/main over test directories
                    score = 80 if "test" not in str(f).lower() else 40
                    score += 10 if "main" in str(f) else 0
                    candidates.append((score, f))

        # ── Priority 3: fuzzy suffix scan (CamelCase → snake_case for Python) ─
        if not candidates and language == "python":
            snake = _camel_to_snake(class_name)
            found = list(self._root.rglob(f"{snake}.py"))
            for f in found:
                candidates.append((60, f))

        candidates.sort(key=lambda t: -t[0])
        result = [p for _, p in candidates]

        if result:
            log.debug(
                "[adaptive-locator] Resolved class to candidate paths",
                class_name=class_name,
                paths=[str(p) for p in result[:3]],
            )
        else:
            log.debug("[adaptive-locator] Could not locate class", class_name=class_name)

        self._file_cache[class_name] = result
        return result

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _index_imports(self, content: str, file_path: str) -> None:
        """Parse import statements and add class → file_path mappings."""
        # Java: import com.company.foo.Bar; → Bar → foo/Bar.java (infer)
        for m in _JAVA_IMPORT_RE.finditer(content):
            fqn = m.group(1)
            short = fqn.split(".")[-1]
            self._import_index.setdefault(short, set()).add(file_path)

        # Python: from foo.bar import Baz → Baz → file_path
        for m in _PYTHON_IMPORT_RE.finditer(content):
            class_name = m.group(2)
            self._import_index.setdefault(class_name, set()).add(file_path)

        # TypeScript: import { Foo, Bar } from './foo-bar' → Foo, Bar → file_path
        for m in _TS_IMPORT_RE.finditer(content):
            for name in m.group(1).split(","):
                name = name.strip()
                if name:
                    self._import_index.setdefault(name, set()).add(file_path)


def _camel_to_snake(name: str) -> str:
    s1 = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", name)
    return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s1).lower()
