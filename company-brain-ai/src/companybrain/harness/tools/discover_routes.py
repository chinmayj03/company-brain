"""discover_routes — list every controller route in a repo."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from companybrain.collectors.code_tracer import discover_routes as _discover
from companybrain.harness.tools import register_tool
from companybrain.llm.base import ToolParameter


@register_tool(
    name="discover_routes",
    description=(
        "List every HTTP route in a repository. Returns one entry per route as "
        "{method, path, file}. Call this first to confirm the target endpoint exists."
    ),
    parameters=[
        ToolParameter("repo_path", "string", "Absolute path to the repository root."),
    ],
)
async def handler(args: dict[str, Any], context: dict[str, Any]) -> list[dict[str, Any]]:
    repo_path = Path(args["repo_path"])
    routes = _discover(repo_path)
    return [{"method": m, "path": p, "file": f} for (m, p, f) in routes]
