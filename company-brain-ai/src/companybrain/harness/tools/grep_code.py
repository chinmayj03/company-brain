"""grep_code — keyword search across a repo, returning {file, line, match} hits."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from companybrain.harness.tools import register_tool
from companybrain.llm.base import ToolParameter

_SKIP_DIRS = frozenset({
    "node_modules", ".git", "dist", "build", "generated",
    "target", "__pycache__", ".gradle", ".idea", ".vscode", ".brain",
})

_DEFAULT_EXTS = (".java", ".kt", ".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs")


@register_tool(
    name="grep_code",
    description=(
        "Search a repository for a regex (default plain-text). Returns up to "
        "max_results hits as {file, line, match}. Use this when you need to find "
        "where a method or symbol is referenced before deciding which file to extract."
    ),
    parameters=[
        ToolParameter("repo_path", "string", "Absolute path to the repository root."),
        ToolParameter("pattern", "string",
                      "Regex pattern (Python re syntax). Anchor with \\b for whole-word."),
        ToolParameter("extensions", "string",
                      "Comma-separated file extensions to scan, e.g. '.java,.kt'. "
                      "Default covers common backend + frontend extensions.",
                      required=False),
        ToolParameter("max_results", "integer",
                      "Hard cap on returned hits (default 50).", required=False),
        ToolParameter("ignore_case", "boolean",
                      "Match case-insensitively (default false).", required=False),
    ],
)
async def handler(args: dict[str, Any], context: dict[str, Any]) -> list[dict[str, Any]]:
    root = Path(args["repo_path"])
    pattern = args["pattern"]
    max_results = int(args.get("max_results") or 50)
    flags = re.IGNORECASE if args.get("ignore_case") else 0

    raw_exts = args.get("extensions") or ""
    if raw_exts:
        exts = tuple(e.strip() if e.strip().startswith(".") else f".{e.strip()}"
                     for e in raw_exts.split(",") if e.strip())
    else:
        exts = _DEFAULT_EXTS

    try:
        regex = re.compile(pattern, flags)
    except re.error as exc:
        return [{"error": f"invalid regex: {exc}"}]

    hits: list[dict[str, Any]] = []
    for file_path in root.rglob("*"):
        if not file_path.is_file() or file_path.suffix not in exts:
            continue
        if any(skip in file_path.parts for skip in _SKIP_DIRS):
            continue
        try:
            text = file_path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for ln, line in enumerate(text.splitlines(), start=1):
            if regex.search(line):
                hits.append({
                    "file":  str(file_path),
                    "line":  ln,
                    "match": line.strip()[:240],
                })
                if len(hits) >= max_results:
                    return hits
    return hits
