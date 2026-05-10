"""Pipeline hooks (ADR-0051 P4).

The harness fires shell scripts at well-defined points in the run. Each hook
sits at ``<repo>/.brain/hooks/<event>.sh`` and is invoked with one JSON object
on stdin. Its stdout — if any — is parsed as JSON and merged into the run by
the caller (e.g. ``pre_extraction.sh`` can return ``{"drop_files": ["*_test.java"]}``
to filter the manifest).

Design notes
------------

* **Best-effort, never load-bearing.** A missing hook is normal. A hook that
  fails, times out, or returns garbage is logged and the run continues with
  the empty modification dict. We never want a misconfigured shell script to
  break extraction.
* **Async subprocess.** The harness loop is async. Hooks run via
  ``asyncio.create_subprocess_exec`` so they don't block the event loop while
  the script does its own I/O.
* **Hard timeout.** Default 30s. Long hooks must offload work elsewhere.
* **Executable bit required.** A non-executable script is treated as absent —
  a sentinel for "I'm checked into git but not yet enabled." This matches
  how Claude Code, git, and pre-commit treat hooks.

Public surface
--------------

* ``EVENTS`` — the canonical list of hook events fired by the harness.
* ``fire(event, payload, *, repo_path, timeout_s) -> dict`` — invoke one hook;
  return the parsed JSON modification (empty on absence/error/timeout).
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

import structlog

log = structlog.get_logger(__name__)


# Canonical hook events. Order matches the lifecycle of a pipeline run.
EVENTS: tuple[str, ...] = (
    "session_start",     # job created, before any tool runs
    "pre_extraction",    # before list_candidate_files / extract_methods_*
    "post_extraction",   # after extraction finishes (per file or batch)
    "on_truncation",     # when a chunk is split or output is truncated
    "pre_storage",       # before write_to_brain
    "post_storage",      # after write_to_brain
    "pre_query",         # before a /query LLM call
    "post_query",        # after a /query LLM call
    "session_end",       # job finalised (success or failure)
)


async def fire(
    event: str,
    payload: dict[str, Any],
    *,
    repo_path: Path | str,
    timeout_s: int = 30,
) -> dict[str, Any]:
    """Invoke ``<repo>/.brain/hooks/<event>.sh`` with ``payload`` on stdin.

    Returns the JSON object the hook printed on stdout, or ``{}`` if:
      * the script does not exist or is not executable,
      * ``event`` is not in :data:`EVENTS`,
      * the script returned non-zero,
      * the script exceeded ``timeout_s``,
      * the stdout is empty or not valid JSON.

    Telemetry is emitted via ``structlog`` at ``debug`` (success) or ``warning``
    (failure) so a misbehaving hook is visible without aborting the run.
    """
    if event not in EVENTS:
        # A typo in caller code — fail loudly, this isn't a hook author's fault.
        raise ValueError(f"Unknown hook event: {event!r}. Valid: {EVENTS}")
    # `event` is also structlog's first positional kwarg, so we use `hook` as
    # the field name in log calls to avoid collisions.

    script = Path(repo_path) / ".brain" / "hooks" / f"{event}.sh"
    if not script.exists() or not os.access(script, os.X_OK):
        return {}

    try:
        proc = await asyncio.create_subprocess_exec(
            str(script),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as exc:
        log.warning("hook.spawn_error", hook=event, error=str(exc))
        return {}

    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(input=json.dumps(payload, default=str).encode()),
            timeout=timeout_s,
        )
    except TimeoutError:
        log.warning("hook.timeout", hook=event, timeout_s=timeout_s,
                    script=str(script))
        # Kill the subprocess so it doesn't linger past the run.
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        return {}

    if proc.returncode != 0:
        log.warning(
            "hook.failed",
            hook=event,
            returncode=proc.returncode,
            stderr=stderr.decode(errors="replace")[:500],
        )
        return {}

    if not stdout.strip():
        log.debug("hook.ran_no_output", hook=event)
        return {}

    try:
        parsed = json.loads(stdout)
    except json.JSONDecodeError as exc:
        log.warning("hook.bad_json", hook=event, error=str(exc),
                    stdout=stdout.decode(errors="replace")[:200])
        return {}

    if not isinstance(parsed, dict):
        log.warning("hook.non_object_output", hook=event, type=type(parsed).__name__)
        return {}

    log.debug("hook.ran", hook=event, keys=sorted(parsed.keys()))
    return parsed
