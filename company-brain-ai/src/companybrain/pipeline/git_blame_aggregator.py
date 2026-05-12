"""
ADR-0059 Pass T1 support — git blame aggregator.

Reads ``git blame`` for a file and returns one ``BlameLine`` per source line.
Results are cached per ``(repo_root, relative_path)`` because Pass T1 hits the
same file many times (once per Method/Class/Endpoint that lives in it). The
cache lives in this module's process; a fresh pipeline run starts with an
empty cache.

Implementation backends, in order of preference:

  1. **pygit2** — fastest, no subprocess overhead. Used when the ``pygit2``
     wheel is importable AND the repo is openable through ``pygit2.Repository``.
     The ADR called this path out explicitly (``pyproject.toml: pygit2``).

  2. **subprocess git** — invoked with ``git blame --line-porcelain``. Used
     when pygit2 is unavailable or fails to open the repo. Required for CI
     environments that don't have the libgit2 native dependency installed.
     The temporal pass and its unit tests both work through this fallback
     transparently.

The aggregator never raises on a missing repo / missing file / non-tracked
file — it logs a warning and returns ``[]`` so Pass T1 simply skips entities
whose blame can't be read.
"""
from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import structlog

log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class BlameLine:
    """One line of git blame output."""
    line_no:    int          # 1-indexed line number in the current file content
    author:     str          # author name, falls back to author email when name absent
    author_mail: str         # author email (may be "<>" for some commits)
    commit_sha: str
    commit_time: datetime    # tz-aware UTC


@dataclass(frozen=True)
class CommitTouch:
    """A single commit that touched a file, used for churn windows."""
    sha: str
    author: str
    author_mail: str
    timestamp: datetime


# ── Module-level cache ────────────────────────────────────────────────────────
# Keyed on the absolute repo root + the file path relative to that root. The
# cache survives the pipeline run; clear_cache() resets it (tests use this).

_blame_cache: dict[tuple[str, str], list[BlameLine]] = {}
_commits_cache: dict[tuple[str, str], list[CommitTouch]] = {}
_author_last_active_cache: dict[tuple[str, str], Optional[datetime]] = {}


def clear_cache() -> None:
    """Reset all in-process caches. Tests call this between cases."""
    _blame_cache.clear()
    _commits_cache.clear()
    _author_last_active_cache.clear()


# ── Public API ────────────────────────────────────────────────────────────────

def blame_file(repo_root: Path | str, rel_path: str) -> list[BlameLine]:
    """Return the blame for ``rel_path`` inside ``repo_root``.

    Empty list (with a warning) if the file isn't tracked or the repo can't
    be opened. Cached per ``(repo_root, rel_path)``.
    """
    if not rel_path:
        return []
    root = str(Path(repo_root).resolve()) if repo_root else ""
    key = (root, rel_path)
    if key in _blame_cache:
        return _blame_cache[key]

    lines: list[BlameLine] = []
    try:
        lines = _blame_with_pygit2(root, rel_path)
    except Exception as exc:
        log.debug("git_blame_aggregator.pygit2 path skipped",
                  rel_path=rel_path, error=str(exc))

    if not lines:
        lines = _blame_with_subprocess(root, rel_path)

    _blame_cache[key] = lines
    return lines


def file_commits(repo_root: Path | str, rel_path: str) -> list[CommitTouch]:
    """Return every commit that touched ``rel_path``, newest first. Cached."""
    if not rel_path:
        return []
    root = str(Path(repo_root).resolve()) if repo_root else ""
    key = (root, rel_path)
    if key in _commits_cache:
        return _commits_cache[key]

    commits = _file_commits_subprocess(root, rel_path)
    _commits_cache[key] = commits
    return commits


def author_last_commit(repo_root: Path | str, author_email: str) -> Optional[datetime]:
    """Return the timestamp of the author's most recent commit anywhere in the
    repo. Used to detect ``stale_owner_left`` (author hasn't pushed in 90d).

    Returns ``None`` when the author has zero commits in the visible history
    or the repo isn't openable. Cached per ``(repo_root, author_email)``.
    """
    if not author_email:
        return None
    root = str(Path(repo_root).resolve()) if repo_root else ""
    key = (root, author_email.lower())
    if key in _author_last_active_cache:
        return _author_last_active_cache[key]

    result = _author_last_commit_subprocess(root, author_email)
    _author_last_active_cache[key] = result
    return result


# ── pygit2 backend ────────────────────────────────────────────────────────────

def _blame_with_pygit2(repo_root: str, rel_path: str) -> list[BlameLine]:
    """Read blame via the pygit2 wheel. Returns ``[]`` if pygit2 isn't
    installed or the repo / file can't be opened."""
    try:
        import pygit2  # type: ignore
    except ImportError:
        return []
    if not repo_root or not os.path.isdir(repo_root):
        return []
    try:
        repo = pygit2.Repository(repo_root)
    except Exception:
        return []

    file_abs = Path(repo_root) / rel_path
    if not file_abs.exists():
        return []

    try:
        blame = repo.blame(rel_path)
    except Exception:
        return []

    out: list[BlameLine] = []
    line_no = 0
    for hunk in blame:
        commit_id = hunk.final_commit_id
        try:
            commit = repo[commit_id]
        except Exception:
            continue
        ts = datetime.fromtimestamp(commit.commit_time, tz=timezone.utc)
        sha = str(commit_id)
        author_name = commit.author.name or commit.author.email or ""
        author_mail = commit.author.email or ""
        for _ in range(hunk.lines_in_hunk):
            line_no += 1
            out.append(BlameLine(
                line_no=line_no, author=author_name, author_mail=author_mail,
                commit_sha=sha, commit_time=ts,
            ))
    return out


# ── subprocess git backend ────────────────────────────────────────────────────

def _run_git(repo_root: str, *args: str) -> Optional[str]:
    """Run ``git -C <repo_root> <args>`` and return stdout, or None on error."""
    if not repo_root:
        return None
    try:
        proc = subprocess.run(
            ["git", "-C", repo_root, *args],
            capture_output=True, text=True, timeout=20,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout


def _blame_with_subprocess(repo_root: str, rel_path: str) -> list[BlameLine]:
    """Read blame via ``git blame --line-porcelain``. Robust to any working
    tree where the ``git`` binary is on PATH."""
    if not repo_root or not rel_path:
        return []
    file_abs = Path(repo_root) / rel_path
    if not file_abs.exists():
        return []
    out = _run_git(repo_root, "blame", "--line-porcelain", "--", rel_path)
    if out is None:
        return []
    return _parse_porcelain(out)


def _parse_porcelain(text: str) -> list[BlameLine]:
    """Parse ``git blame --line-porcelain`` output into BlameLine rows.

    Format: each line of source is preceded by a header block.  The first
    header line is ``<sha> <orig_line> <final_line> [<num_lines>]``; subsequent
    header lines start with field names (``author``, ``author-mail``,
    ``author-time``, etc.) until a source line prefixed with ``\\t``.
    """
    rows: list[BlameLine] = []
    cur_sha = ""
    cur_author = ""
    cur_mail = ""
    cur_time: Optional[datetime] = None
    line_no = 0

    for raw in text.splitlines():
        if raw.startswith("\t"):
            line_no += 1
            rows.append(BlameLine(
                line_no=line_no,
                author=cur_author or cur_mail,
                author_mail=cur_mail,
                commit_sha=cur_sha,
                commit_time=cur_time or datetime.fromtimestamp(0, tz=timezone.utc),
            ))
            continue
        if not raw:
            continue
        parts = raw.split(" ", 1)
        head = parts[0]
        rest = parts[1] if len(parts) > 1 else ""
        # First line of a header block is "<sha> <orig> <final> [<num>]" — sha
        # is 40 hex chars, so use that as a fingerprint.
        if len(head) == 40 and all(c in "0123456789abcdef" for c in head.lower()):
            cur_sha = head
            continue
        if head == "author":
            cur_author = rest
        elif head == "author-mail":
            cur_mail = rest.strip("<>")
        elif head == "author-time":
            try:
                cur_time = datetime.fromtimestamp(int(rest), tz=timezone.utc)
            except ValueError:
                cur_time = None
    return rows


def _file_commits_subprocess(repo_root: str, rel_path: str) -> list[CommitTouch]:
    """Return every commit that touched ``rel_path``, newest first."""
    if not repo_root or not rel_path:
        return []
    out = _run_git(
        repo_root, "log", "--follow",
        "--pretty=format:%H%x00%an%x00%ae%x00%at",
        "--", rel_path,
    )
    if not out:
        return []
    rows: list[CommitTouch] = []
    for raw in out.splitlines():
        if not raw:
            continue
        try:
            sha, name, mail, ts = raw.split("\x00", 3)
        except ValueError:
            continue
        try:
            t = datetime.fromtimestamp(int(ts), tz=timezone.utc)
        except ValueError:
            continue
        rows.append(CommitTouch(sha=sha, author=name or mail, author_mail=mail, timestamp=t))
    return rows


def _author_last_commit_subprocess(repo_root: str, author_email: str) -> Optional[datetime]:
    if not repo_root or not author_email:
        return None
    out = _run_git(
        repo_root, "log", "-1",
        "--author=" + author_email,
        "--pretty=format:%at",
    )
    if out is None or not out.strip():
        return None
    try:
        return datetime.fromtimestamp(int(out.strip()), tz=timezone.utc)
    except ValueError:
        return None
