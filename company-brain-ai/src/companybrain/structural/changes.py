# Algorithm ported from tirth8205/code-review-graph (MIT License).
# Original: code_review_graph/incremental.py — get_changed_files()
#
# Key changes from the original:
#   - Returns Path objects relative to repo_root rather than absolute strings.
#   - Added full_scan() helper for first-time indexing (no prior commit).
#   - Filters to source-code extensions only (same set as parser.py).
#   - Added support for comparing any two arbitrary refs (from_ref, to_ref).
"""ADR-006 §5: Git-diff dirty-set detection.

Given a repository, produces the set of source files that changed between
two git refs (default: HEAD~1..HEAD).  The indexer feeds this dirty set to
dependents.py for reverse-import expansion, then to the parser for re-parse.

Usage::

    from companybrain.structural.changes import get_changed_files, full_scan

    # Incremental: files changed in last commit
    dirty = get_changed_files("/path/to/repo")

    # Incremental: files changed between two specific refs
    dirty = get_changed_files("/path/to/repo", from_ref="main", to_ref="feature/xyz")

    # Full scan: all source files (used for first-time index)
    all_files = full_scan("/path/to/repo")
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)

# Source file extensions that the structural parser handles.
# Must stay in sync with parser.py _LANGUAGE_MAP keys.
_SOURCE_EXTENSIONS: frozenset[str] = frozenset({
    ".java",
    ".py",
    ".ts", ".tsx",
    ".js", ".jsx",
    ".go",
    ".kt",          # Kotlin — grammar available via tree-sitter-languages
    ".rs",          # Rust
    ".c", ".h",
    ".cpp", ".cc", ".cxx", ".hpp",
    ".cs",          # C#
    ".rb",          # Ruby
    ".php",
    ".swift",
    ".scala",
})


def get_changed_files(
    repo_root: str | Path,
    from_ref: str = "HEAD~1",
    to_ref: str = "HEAD",
) -> set[Path]:
    """Return the set of source files changed between two git refs.

    Paths are relative to *repo_root*.  Only files whose extension is in
    _SOURCE_EXTENSIONS are returned — generated files, assets, config, etc.
    are excluded.

    If the repository has only one commit (from_ref would be invalid) or
    if the git command fails for any reason, falls back to full_scan().

    Args:
        repo_root: Absolute path to the git repository root.
        from_ref:  Start of the diff range (exclusive).  Default: HEAD~1.
        to_ref:    End of the diff range (inclusive).    Default: HEAD.

    Returns:
        Set of repo-relative Paths for changed source files.
    """
    repo_root = Path(repo_root).resolve()

    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", "--diff-filter=ACMRT", from_ref, to_ref],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError as e:
        # Likely a single-commit repo or invalid ref range.
        log.warning(
            "git diff failed (from=%s to=%s): %s — falling back to full scan",
            from_ref, to_ref, e.stderr.strip(),
        )
        return full_scan(repo_root)

    changed: set[Path] = set()
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        p = Path(line)
        if p.suffix in _SOURCE_EXTENSIONS:
            full = repo_root / p
            if full.exists():               # file might have been deleted in this diff
                changed.add(p)

    log.info(
        "git diff %s..%s → %d changed source files",
        from_ref, to_ref, len(changed),
    )
    return changed


def get_changed_files_since(
    repo_root: str | Path,
    since_sha: str,
) -> set[Path]:
    """Return source files changed in all commits since *since_sha* (exclusive).

    Useful when the pipeline stores the last-indexed commit SHA and wants
    to catch up with multiple commits since the last run.

    Args:
        repo_root: Absolute path to the repository root.
        since_sha: The last commit that was fully indexed (exclusive start).

    Returns:
        Set of repo-relative Paths for changed source files.
    """
    return get_changed_files(repo_root, from_ref=since_sha, to_ref="HEAD")


def full_scan(repo_root: str | Path) -> set[Path]:
    """Return all source files in the repo — used for first-time indexing.

    Respects .gitignore by using ``git ls-files`` so vendor trees, generated
    files, and hidden directories are excluded automatically.

    Args:
        repo_root: Absolute path to the repository root.

    Returns:
        Set of repo-relative Paths for all tracked source files.
    """
    repo_root = Path(repo_root).resolve()

    try:
        result = subprocess.run(
            ["git", "ls-files"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError as e:
        log.error("git ls-files failed: %s", e.stderr.strip())
        # Last-resort: walk the filesystem
        return _walk_filesystem(repo_root)

    files: set[Path] = set()
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        p = Path(line)
        if p.suffix in _SOURCE_EXTENSIONS:
            full = repo_root / p
            if full.exists():
                files.add(p)

    log.info("full scan → %d source files in %s", len(files), repo_root)
    return files


def current_head_sha(repo_root: str | Path) -> str | None:
    """Return the current HEAD commit SHA, or None if not a git repo."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=Path(repo_root),
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()
    except subprocess.CalledProcessError:
        return None


# ── Internal helpers ──────────────────────────────────────────────────────────

def _walk_filesystem(repo_root: Path) -> set[Path]:
    """Fallback: walk the filesystem if git is unavailable."""
    files: set[Path] = set()
    for p in repo_root.rglob("*"):
        if p.is_file() and p.suffix in _SOURCE_EXTENSIONS:
            # Skip obvious non-project directories
            parts = p.relative_to(repo_root).parts
            if any(part.startswith(".") or part in ("node_modules", "vendor", "__pycache__", "target", "build", "dist") for part in parts):
                continue
            files.add(p.relative_to(repo_root))
    log.warning("Used filesystem walk (git unavailable) → %d files", len(files))
    return files
