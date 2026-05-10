"""glob_files — list paths in a repo matching a glob pattern."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from companybrain.harness.tools import register_tool
from companybrain.llm.base import ToolParameter

_SKIP_DIRS = frozenset({
    "node_modules", ".git", "dist", "build", "generated",
    "target", "__pycache__", ".gradle", ".idea", ".vscode", ".brain",
})


@register_tool(
    name="glob_files",
    description=(
        "List files in a repo whose path matches a glob pattern (e.g. '**/*.java', "
        "'src/**/*Service.kt'). Output is sorted; build-output and VCS dirs are "
        "always excluded. Capped at 200 paths."
    ),
    parameters=[
        ToolParameter("repo_path", "string", "Absolute path to the repository root."),
        ToolParameter("pattern", "string",
                      "rglob-style pattern relative to repo_path, e.g. '**/*.java'."),
        ToolParameter("max_results", "integer",
                      "Hard cap on returned paths (default 200).", required=False),
    ],
)
async def handler(args: dict[str, Any], context: dict[str, Any]) -> list[str]:
    root = Path(args["repo_path"])
    pattern = args["pattern"]
    max_results = int(args.get("max_results") or 200)

    out: list[str] = []
    for path in root.glob(pattern):
        if not path.is_file():
            continue
        if any(skip in path.parts for skip in _SKIP_DIRS):
            continue
        out.append(str(path))
        if len(out) >= max_results:
            break
    out.sort()
    return out
