"""Walk a repo, return source files. Honor .gitignore + standard skip dirs."""
from __future__ import annotations
from pathlib import Path
from typing import Iterable

_SKIP_DIRS = frozenset({
    "node_modules", ".git", ".idea", ".vscode", "dist", "build",
    "target", "out", "__pycache__", ".pytest_cache", ".venv", "venv",
    ".gradle", ".next", ".turbo", ".mypy_cache", ".ruff_cache",
    "vendor", "coverage", ".brain", ".bm25",
})

_SOURCE_EXTS = frozenset({
    ".java", ".kt", ".py", ".ts", ".tsx", ".js", ".jsx",
    ".go", ".rb", ".cs", ".rs", ".php", ".swift",
})


def walk_repo(root: Path) -> Iterable[Path]:
    """Yield source files under root, skipping common build dirs."""
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in _SKIP_DIRS for part in path.parts):
            continue
        if path.suffix in _SOURCE_EXTS:
            yield path
