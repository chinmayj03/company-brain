"""list_candidate_files — bounded extraction manifest for an endpoint (ADR-0050)."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from companybrain.collectors.manifest_filter import build_filtered_manifest
from companybrain.harness.tools import register_tool
from companybrain.llm.base import ToolParameter


@register_tool(
    name="list_candidate_files",
    description=(
        "Return the bounded list of source files worth extracting for an endpoint. "
        "Wraps ADR-0050's three-layer manifest filter — already drops pure DTOs and "
        "caps at ~20 files. Each entry: {path, role, size_kb, package_score, bm25_score}."
    ),
    parameters=[
        ToolParameter("endpoint", "string", "Endpoint path, e.g. '/users/{id}'."),
        ToolParameter("http_method", "string",
                      "HTTP verb. One of GET / POST / PUT / DELETE / PATCH.",
                      enum=["GET", "POST", "PUT", "DELETE", "PATCH"]),
        ToolParameter("repo_path", "string", "Absolute path to the repository root."),
        ToolParameter("max_files", "integer",
                      "Hard cap on returned files (default 20).", required=False),
    ],
)
async def handler(args: dict[str, Any], context: dict[str, Any]) -> list[dict[str, Any]]:
    candidates = await build_filtered_manifest(
        repo_path=Path(args["repo_path"]),
        endpoint=args["endpoint"],
        method=args["http_method"],
        max_files=int(args.get("max_files") or 20),
    )
    return [
        {
            "path":          c.path,
            "role":          c.role,
            "size_kb":       c.size_kb,
            "package_score": c.package_score,
            "bm25_score":    c.bm25_score,
        }
        for c in candidates
    ]
