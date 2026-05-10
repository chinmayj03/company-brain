"""run_repo_command — sandboxed bash execution for harness tools (ADR-0052 P5).

The agent occasionally needs to shell out — ``mvn test``, ``git log``,
``npm run build``, ``psql -c '\\d table'`` — for verification or quick
inspection. Until P5 the harness had no way to do this; now it does, but with
a tight safety net:

  * **Capability gate** — declares ``EXEC_SHELL``. The default workspace
    grants table denies that, so this tool is dormant unless an operator
    flips it on (CLI ``--yes``, ``BRAIN_GRANTS=exec_shell:auto``, or a
    per-repo settings file).
  * **Timeout** — wall-clock cap (default 30 s, configurable per call up to
    300 s) keeps a hung Maven from stalling a job.
  * **Output truncation** — combined stdout/stderr is capped at 10 KB so a
    chatty `npm install` cannot blow up the agent's context window.
  * **Sandbox attempt** — when ``bwrap`` (bubble-wrap) is on PATH we wrap
    the command in a read-only mount of the repo. When it isn't (most macOS
    laptops), we run with ``cwd=repo_path`` and trust the capability gate.
    The bubble-wrap path is the production target; the bare-subprocess path
    keeps developer ergonomics intact.

The handler always returns a structured dict so the agent can branch on
``ok`` / ``returncode`` without parsing free-text. Stderr is appended to
stdout under a ``--- stderr ---`` marker for readability.
"""
from __future__ import annotations

import asyncio
import shutil
from typing import Any

import structlog

from companybrain.harness.permissions import Capability
from companybrain.harness.tools import register_tool
from companybrain.llm.base import ToolParameter

log = structlog.get_logger(__name__)


_DEFAULT_TIMEOUT_S = 30.0
_MAX_TIMEOUT_S    = 300.0
_MAX_OUTPUT_BYTES = 10_240


@register_tool(
    name="run_repo_command",
    description=(
        "Run a bash command inside the workspace repo. Wall-clock timeout "
        "(default 30s, max 300), output truncated to 10 KB. Requires the "
        "EXEC_SHELL capability — denied by default; an operator must enable it. "
        "Use for `mvn test`, `git log`, `npm run build`, etc."
    ),
    parameters=[
        ToolParameter("command", "string", "Bash command to execute, e.g. 'git log -n 5'."),
        ToolParameter("timeout", "integer",
                      "Per-call wall-clock cap in seconds (default 30, max 300).",
                      required=False),
        ToolParameter("cwd", "string",
                      "Working directory; defaults to context['repo_path'].",
                      required=False),
    ],
    requires=(Capability.EXEC_SHELL,),
)
async def handler(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    command = (args.get("command") or "").strip()
    if not command:
        return {"ok": False, "error": "run_repo_command: empty 'command'"}

    timeout = float(args.get("timeout") or _DEFAULT_TIMEOUT_S)
    timeout = max(1.0, min(timeout, _MAX_TIMEOUT_S))

    cwd = args.get("cwd") or context.get("repo_path") or "."
    cwd = str(cwd)

    argv = _wrap_with_bwrap(command, cwd)
    log.debug("run_repo_command.start", argv=argv, cwd=cwd, timeout=timeout)

    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=cwd if argv[0] != "bwrap" else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        return {"ok": False, "error": f"shell not found: {exc}"}
    except OSError as exc:
        return {"ok": False, "error": f"failed to spawn subprocess: {exc}"}

    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        return {
            "ok":         False,
            "timed_out":  True,
            "timeout_s":  timeout,
            "command":    command,
            "error":      f"timed out after {timeout:.0f}s",
        }

    out = _combine_and_truncate(stdout, stderr)
    return {
        "ok":         proc.returncode == 0,
        "returncode": proc.returncode,
        "command":    command,
        "cwd":        cwd,
        "output":     out,
        "truncated":  len(stdout) + len(stderr) > _MAX_OUTPUT_BYTES,
    }


# ── helpers ────────────────────────────────────────────────────────────────


def _wrap_with_bwrap(command: str, cwd: str) -> list[str]:
    """Wrap `command` in `bwrap` if available, else fall back to `bash -c`.

    The bubble-wrap recipe gives the command a read-only view of the repo and
    a fresh /tmp. Production deployments install bwrap; developer laptops
    typically do not. The fallback keeps behaviour identical so the test suite
    is OS-agnostic.
    """
    if shutil.which("bwrap"):
        return [
            "bwrap",
            "--ro-bind", cwd, cwd,
            "--ro-bind", "/usr", "/usr",
            "--ro-bind", "/lib", "/lib",
            "--ro-bind", "/lib64", "/lib64",
            "--ro-bind", "/bin", "/bin",
            "--proc", "/proc",
            "--dev", "/dev",
            "--tmpfs", "/tmp",
            "--chdir", cwd,
            "--unshare-net",
            "bash", "-c", command,
        ]
    return ["bash", "-c", command]


def _combine_and_truncate(stdout: bytes, stderr: bytes) -> str:
    out = stdout.decode(errors="replace")
    err = stderr.decode(errors="replace")
    combined = out
    if err:
        combined = (out.rstrip() + "\n\n--- stderr ---\n" + err).strip()
    if len(combined) > _MAX_OUTPUT_BYTES:
        return combined[:_MAX_OUTPUT_BYTES] + f"\n... (truncated at {_MAX_OUTPUT_BYTES} bytes)"
    return combined
