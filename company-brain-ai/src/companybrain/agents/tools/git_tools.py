"""
Git tools — expose git history and commit context as agent-callable tools.

These let the Business Context Agent pull "why" signals (commit messages,
PR references, author patterns) without reading the entire git log itself.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Optional

import structlog

log = structlog.get_logger(__name__)


def get_recent_commits(file_path: str, limit: int = 10) -> list[dict]:
    """
    Return recent commits that touched a specific file.
    Returns: [{"hash": "abc1234", "author": "alice", "date": "2024-01", "message": "..."}]
    """
    try:
        result = subprocess.run(
            ["git", "log", f"-{limit}", "--pretty=format:%H|%an|%ad|%s",
             "--date=short", "--", file_path],
            capture_output=True, text=True, timeout=15,
            cwd=str(Path(file_path).parent),
        )
        commits = []
        for line in result.stdout.splitlines():
            parts = line.split("|", 3)
            if len(parts) == 4:
                commits.append({
                    "hash": parts[0][:8],
                    "author": parts[1],
                    "date": parts[2],
                    "message": parts[3],
                })
        return commits
    except Exception as e:
        log.warning("get_recent_commits failed", file=file_path, error=str(e))
        return []


def get_file_contributors(file_path: str) -> list[str]:
    """
    Return the list of authors who have committed to a file, most recent first.
    """
    try:
        result = subprocess.run(
            ["git", "log", "--pretty=format:%an", "--", file_path],
            capture_output=True, text=True, timeout=10,
            cwd=str(Path(file_path).parent),
        )
        seen: dict[str, None] = {}
        for author in result.stdout.splitlines():
            seen[author.strip()] = None
        return list(seen.keys())
    except Exception as e:
        log.warning("get_file_contributors failed", error=str(e))
        return []


def search_commit_messages(keyword: str, repo_path: str, limit: int = 20) -> list[dict]:
    """
    Search commit messages for a keyword (useful for finding why something was added).
    Returns: [{"hash": "...", "date": "...", "message": "...", "files_changed": [...]}]
    """
    try:
        result = subprocess.run(
            ["git", "log", f"-{limit}", "--pretty=format:%H|%ad|%s",
             "--date=short", f"--grep={keyword}"],
            capture_output=True, text=True, timeout=15,
            cwd=repo_path,
        )
        commits = []
        for line in result.stdout.splitlines():
            parts = line.split("|", 2)
            if len(parts) == 3:
                commits.append({
                    "hash": parts[0][:8],
                    "date": parts[1],
                    "message": parts[2],
                })
        return commits
    except Exception as e:
        log.warning("search_commit_messages failed", error=str(e))
        return []


def get_commit_diff_summary(commit_hash: str, repo_path: str) -> str:
    """
    Return the diff summary (files changed + stat) for a specific commit.
    Useful for understanding what a change actually touched.
    """
    try:
        result = subprocess.run(
            ["git", "show", "--stat", "--no-patch", commit_hash],
            capture_output=True, text=True, timeout=10,
            cwd=repo_path,
        )
        return result.stdout[:2000]
    except Exception as e:
        return f"ERROR: {e}"


def get_blame_summary(file_path: str, method_name: str) -> str:
    """
    Return git blame lines around a method name — shows who last touched it and when.
    Useful for identifying ownership of a piece of code.
    """
    try:
        content = Path(file_path).read_text(errors="ignore")
        lines = content.splitlines()
        # Find the line number of the method
        target_line = None
        import re
        for i, line in enumerate(lines, 1):
            if re.search(rf'\b{re.escape(method_name)}\s*\(', line):
                target_line = i
                break

        if not target_line:
            return f"Method '{method_name}' not found in {file_path}"

        # Run blame around that line
        start = max(1, target_line - 2)
        end = min(len(lines), target_line + 10)
        result = subprocess.run(
            ["git", "blame", f"-L{start},{end}", "--date=short",
             "--porcelain", file_path],
            capture_output=True, text=True, timeout=10,
            cwd=str(Path(file_path).parent),
        )
        # Extract summary: hash + author + date + code
        summary_lines = []
        current_hash = ""
        for line in result.stdout.splitlines():
            if line.startswith("author "):
                author = line[7:]
            elif line.startswith("author-time ") or line.startswith("summary "):
                pass
            elif line.startswith("committer-mail"):
                pass
            elif len(line) >= 40 and line[:40].isalnum() and not line.startswith("\t"):
                current_hash = line[:8]
            elif line.startswith("\t"):
                summary_lines.append(f"{current_hash} | {line[1:]}")
        return "\n".join(summary_lines[:15])
    except Exception as e:
        return f"ERROR: {e}"
