"""ADR-0061 E4 — diff_since MCP tool.

Return entities whose source file changed since a given date or commit. Uses
``git log`` combined with the brain's per-entity ``file`` metadata so a code
review or post-mortem can ask "what entities were touched since 2026-04-01?"
without re-running extraction.

Resolution rules (in order):
  1. ``commit`` supplied → ``git diff --name-only <commit>..HEAD``
  2. ``date`` supplied (ISO YYYY-MM-DD) → ``git log --since=<date> --name-only``
  3. Neither → defaults to 7 days back.

For each touched file we look up brain entities whose ``file`` matches, then
return them with the latest commit metadata (sha, author, date, subject) for
that file.
"""
from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

import structlog

from companybrain.mcp.tools import Tool, register

log = structlog.get_logger(__name__)


_SCHEMA = {
    "name": "diff_since",
    "description": (
        "List brain entities whose file changed since a date or commit. "
        "Pass either 'date' (YYYY-MM-DD) or 'commit' (sha/ref); if neither is "
        "given, defaults to the last 7 days. Returns up to 'limit' entities "
        "with last-touched metadata."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "date":   {"type": "string", "description": "ISO date (YYYY-MM-DD)."},
            "commit": {"type": "string", "description": "Git ref or SHA."},
            "limit":  {"type": "integer", "default": 50,
                       "description": "Max entities to return."},
            "repo":   {"type": "string",
                       "description": "Repo root (defaults to BRAIN_REPO_ROOT)."},
        },
    },
}


async def diff_since(
    *,
    date: Optional[str] = None,
    commit: Optional[str] = None,
    limit: int = 50,
    repo: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Programmatic entry point — returns the same list of dicts the MCP
    handler serialises. Each dict carries ``last_touched_at`` so the
    acceptance test can assert on monotonic recency."""
    repo_root = _resolve_repo_root(repo)
    if not repo_root:
        return []
    touched = _files_touched_since(repo_root, date=date, commit=commit)
    if not touched:
        return []
    entities = _load_entities(repo_root)
    if not entities:
        return []
    matches: list[dict[str, Any]] = []
    seen_urns: set[str] = set()
    for rel, last_commit in touched.items():
        for ent in entities.get(rel, []):
            if ent["urn"] in seen_urns:
                continue
            seen_urns.add(ent["urn"])
            matches.append({
                **ent,
                "last_touched_at": last_commit["date"],
                "last_touched_by": last_commit["author"],
                "last_commit_sha": last_commit["sha"],
                "last_commit_subject": last_commit["subject"],
            })
            if len(matches) >= limit:
                break
        if len(matches) >= limit:
            break
    matches.sort(key=lambda r: r["last_touched_at"], reverse=True)
    return matches


# ── Helpers ───────────────────────────────────────────────────────────────────

def _resolve_repo_root(repo: Optional[str]) -> Optional[Path]:
    if repo and Path(repo).is_dir():
        return Path(repo).resolve()
    env = os.environ.get("BRAIN_REPO_ROOT")
    if env and Path(env).is_dir():
        return Path(env).resolve()
    return None


def _files_touched_since(
    repo_root: Path,
    *,
    date: Optional[str],
    commit: Optional[str],
) -> dict[str, dict[str, str]]:
    """Return ``{rel_path: {sha, author, date, subject}}`` for files changed
    since the supplied anchor (commit > date > 7-day default)."""
    if commit:
        files = _git_diff_files(repo_root, commit)
    else:
        since = date or _seven_days_ago()
        files = _git_log_files(repo_root, since=since)
    out: dict[str, dict[str, str]] = {}
    for rel in files:
        info = _last_commit(repo_root, rel)
        if info is not None:
            out[rel] = info
    return out


def _git_diff_files(repo_root: Path, commit: str) -> list[str]:
    cmd = ["git", "-C", str(repo_root), "diff", "--name-only",
           f"{commit}..HEAD"]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        log.debug("diff_since.git_diff failed", error=str(e))
        return []
    return [line.strip() for line in out.stdout.splitlines() if line.strip()]


def _git_log_files(repo_root: Path, *, since: str) -> list[str]:
    cmd = ["git", "-C", str(repo_root), "log", f"--since={since}",
           "--name-only", "--pretty=format:"]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        log.debug("diff_since.git_log failed", error=str(e))
        return []
    seen: set[str] = set()
    files: list[str] = []
    for line in out.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        if line in seen:
            continue
        seen.add(line)
        files.append(line)
    return files


def _last_commit(repo_root: Path, rel_path: str) -> Optional[dict[str, str]]:
    cmd = ["git", "-C", str(repo_root), "log", "-1",
           "--pretty=format:%H|%an|%aI|%s", "--", rel_path]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    line = out.stdout.strip()
    if not line:
        return None
    parts = line.split("|", 3)
    if len(parts) != 4:
        return None
    return {"sha": parts[0], "author": parts[1],
            "date": parts[2], "subject": parts[3][:200]}


def _load_entities(repo_root: Path) -> dict[str, list[dict]]:
    """Build a ``{rel_file_path: [entity_summary, ...]}`` index from .brain/.

    We read JSON files under .brain/<entity_type>/*.json with shallow parsing
    — only the fields we need (id, file, qualified_name, entity_type)."""
    brain = repo_root / ".brain"
    if not brain.is_dir():
        return {}
    by_file: dict[str, list[dict]] = {}
    for entity_dir in brain.iterdir():
        if not entity_dir.is_dir() or entity_dir.name.startswith("."):
            continue
        for jf in entity_dir.glob("*.json"):
            try:
                blob = json.loads(jf.read_text())
            except (OSError, json.JSONDecodeError):
                continue
            urn = blob.get("id") or blob.get("urn")
            file_path = blob.get("file") or (blob.get("metadata") or {}).get("file") or ""
            if not urn or not file_path:
                continue
            by_file.setdefault(file_path, []).append({
                "urn": urn,
                "name": blob.get("qualified_name") or blob.get("name") or "",
                "entity_type": blob.get("entity_type") or entity_dir.name,
                "file": file_path,
            })
    return by_file


def _seven_days_ago() -> str:
    return (datetime.now(timezone.utc) - timedelta(days=7)).date().isoformat()


def _format(entities: list[dict[str, Any]]) -> str:
    if not entities:
        return "(no entities changed since the requested anchor)"
    lines = [f"{len(entities)} entities touched:"]
    for e in entities:
        lines.append(
            f"  {e.get('last_touched_at', ''):24s} "
            f"{e.get('entity_type', ''):14s} "
            f"{e.get('name', '')}"
        )
    return "\n".join(lines)


async def _handle(args: dict) -> str:
    try:
        results = await diff_since(
            date=args.get("date"),
            commit=args.get("commit"),
            limit=int(args.get("limit", 50)),
            repo=args.get("repo"),
        )
    except Exception as e:
        return f"ERROR: diff_since failed: {e}"
    return _format(results)


register(Tool(
    name=_SCHEMA["name"],
    description=_SCHEMA["description"],
    schema=_SCHEMA,
    handler=_handle,
))
