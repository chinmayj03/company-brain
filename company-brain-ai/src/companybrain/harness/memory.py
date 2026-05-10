"""Per-repo BRAIN.md memory file (ADR-0051 P3).

`BRAIN.md` is a small markdown file living at `<repo>/.brain/BRAIN.md`. It is
auto-loaded into the harness system prompt on every run so the agent has the
repo's gotchas, anti-patterns, and pipeline observations in front of it.

Two sections, separated by a sentinel comment:

  ## Curated notes (human-edited)
      The team writes things like "JsonKeyMapping is a constants table — never
      extract it as a code entity".

  <!-- AUTO-APPENDED — managed by company-brain. Do not edit by hand. -->
      The pipeline appends timestamped observations here. Recurrent drops,
      consistently-skipped DTOs, framework-detection upgrades, etc.

Public surface:
    brain_md_path(repo_path)     -> Path
    load(repo_path)              -> str
    auto_append(repo_path, obs)  -> None
"""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import structlog

log = structlog.get_logger(__name__)


_AUTO_SECTION_MARKER = "<!-- AUTO-APPENDED — managed by company-brain. Do not edit by hand. -->"

# Templates/<...>/BRAIN.md sits one level above src/, alongside frameworks/.
_BRAIN_TEMPLATE_PATH = (
    Path(__file__).resolve().parent.parent.parent.parent
    / ".brain-template" / "BRAIN.md"
)


def brain_md_path(repo_path: Path | str) -> Path:
    """`<repo>/.brain/BRAIN.md` — created on first auto_append, never required."""
    return Path(repo_path) / ".brain" / "BRAIN.md"


def load(repo_path: Path | str) -> str:
    """Return the BRAIN.md contents, or "" if no file exists.

    A missing file is the common case for a fresh repo and must not be an
    error — the harness prompt simply has no extra repo-specific guidance.
    """
    p = brain_md_path(repo_path)
    if not p.exists():
        return ""
    try:
        return p.read_text()
    except OSError as exc:
        log.warning("memory.load.error", path=str(p), error=str(exc))
        return ""


def auto_append(
    repo_path: Path | str,
    observation: str,
    *,
    dedupe_window_chars: int = 4_000,
) -> None:
    """Append `observation` to BRAIN.md's auto section, with dedupe.

    Skips the write when the same observation already appears in the trailing
    `dedupe_window_chars` of the file — that suppresses the obvious form of
    pipeline spam ("JsonKeyMapping was dropped" repeated every run for weeks).

    Creates `.brain/` and seeds BRAIN.md from the package template on first
    call. Truncated/empty files are tolerated; the marker is appended if
    missing.
    """
    if not observation or not observation.strip():
        return

    p = brain_md_path(repo_path)
    p.parent.mkdir(parents=True, exist_ok=True)

    if p.exists():
        try:
            current = p.read_text()
        except OSError as exc:
            log.warning("memory.append.read_error", path=str(p), error=str(exc))
            return
    else:
        current = _initial_template()

    if observation in current[-dedupe_window_chars:]:
        log.debug("memory.append.dedupe_skip", path=str(p), observation=observation[:80])
        return

    if _AUTO_SECTION_MARKER not in current:
        if not current.endswith("\n"):
            current += "\n"
        current += f"\n{_AUTO_SECTION_MARKER}\n"

    timestamp = datetime.now(UTC).isoformat(timespec="seconds")
    if not current.endswith("\n"):
        current += "\n"
    current += f"- {timestamp} — {observation}\n"

    try:
        p.write_text(current)
    except OSError as exc:
        log.warning("memory.append.write_error", path=str(p), error=str(exc))


def _initial_template() -> str:
    """Read the bundled BRAIN.md template; fall back to a minimal stub."""
    try:
        return _BRAIN_TEMPLATE_PATH.read_text()
    except OSError:
        return (
            "# BRAIN.md — repo-specific brain memory\n\n"
            "## Curated notes (human-edited)\n\n"
            f"{_AUTO_SECTION_MARKER}\n"
        )
