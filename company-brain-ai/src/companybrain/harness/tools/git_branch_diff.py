"""git_branch_diff — list files changed between two refs (ADR-0052 P5).

The huge cost win on big repos: instead of extracting every endpoint, narrow
the next extraction step to the files a PR actually touched. A 5-file PR's
worth of work then costs $0.001–0.005 instead of $0.05+.

The agent calls this with two refs (``main``, ``feature/x``); the tool runs
``git diff --name-only A...B`` and returns the changed-files list. Filtering
by extension keeps `.gitignore`-tracked binaries out of the result.

Capability: ``EXEC_SHELL`` (subprocess gate). Read-only.
"""
from __future__ import annotations

import asyncio
from typing import Any

import structlog

from companybrain.harness.permissions import Capability
from companybrain.harness.tools import register_tool
from companybrain.llm.base import ToolParameter

log = structlog.get_logger(__name__)


_DEFAULT_TIMEOUT_S = 15.0
# Narrow the result to source-bearing extensions by default. The agent can
# override with `extensions=[]` to see binaries / docs too.
_DEFAULT_EXTENSIONS = (
    ".java", ".kt", ".scala", ".groovy",
    ".py",
    ".ts", ".tsx", ".js", ".jsx",
    ".go",
    ".rb",
    ".cs",
    ".rs",
    ".sql",
)


@register_tool(
    name="git_branch_diff",
    description=(
        "Return the list of files changed between two git refs (e.g. main and "
        "feature/x). Useful for narrowing extraction to a PR's changed files. "
        "Capability EXEC_SHELL."
    ),
    parameters=[
        ToolParameter("branch_a", "string", "Base ref (typically 'main')."),
        ToolParameter("branch_b", "string", "Head ref (typically the feature branch)."),
        ToolParameter("repo_path", "string",
                      "Path to the repo; defaults to context['repo_path'].",
                      required=False),
        ToolParameter("extensions", "array",
                      "File extensions to keep (e.g. ['.java','.py']). "
                      "Pass an empty list to see all changed files. "
                      "Defaults to the common source extensions.",
                      required=False),
    ],
    requires=(Capability.EXEC_SHELL, Capability.READ_REPO),
)
async def handler(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    branch_a = (args.get("branch_a") or "").strip()
    branch_b = (args.get("branch_b") or "").strip()
    if not branch_a or not branch_b:
        return {"ok": False, "error": "git_branch_diff requires branch_a and branch_b"}

    repo = str(args.get("repo_path") or context.get("repo_path") or ".")
    extensions: list[str] | None = args.get("extensions")
    if extensions is None:
        extensions = list(_DEFAULT_EXTENSIONS)

    try:
        proc = await asyncio.create_subprocess_exec(
            "git", "-C", repo, "diff", "--name-only", f"{branch_a}...{branch_b}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=_DEFAULT_TIMEOUT_S,
        )
    except TimeoutError:
        return {"ok": False, "error": f"git_branch_diff timed out after {_DEFAULT_TIMEOUT_S:.0f}s"}
    except (OSError, asyncio.CancelledError) as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    if proc.returncode != 0:
        return {
            "ok":         False,
            "returncode": proc.returncode,
            "error":      stderr.decode(errors="replace").strip(),
        }

    files = [
        line for line in stdout.decode(errors="replace").splitlines()
        if line.strip()
    ]
    if extensions:
        ext_lower = {e.lower() for e in extensions}
        files = [f for f in files if any(f.lower().endswith(e) for e in ext_lower)]

    log.debug("git_branch_diff", branch_a=branch_a, branch_b=branch_b,
              repo=repo, files=len(files))
    return {
        "ok":            True,
        "branch_a":      branch_a,
        "branch_b":      branch_b,
        "files":         files,
        "files_count":   len(files),
        "extensions":    extensions,
    }
