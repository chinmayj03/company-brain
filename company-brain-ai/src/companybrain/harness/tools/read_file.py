"""read_file — read a source file, FileCache-backed when context provides one."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from companybrain.harness.tools import register_tool
from companybrain.llm.base import ToolParameter
from companybrain.util.file_cache import FileCache


@register_tool(
    name="read_file",
    description=(
        "Read a source file's content. Use only for orientation — extraction tools "
        "read the file themselves via the shared cache. Output is capped to "
        "max_chars characters; the tail is replaced with a truncation marker."
    ),
    parameters=[
        ToolParameter("path", "string", "Absolute path to the file to read."),
        ToolParameter("max_chars", "integer",
                      "Maximum characters to return (default 8000).", required=False),
    ],
)
async def handler(args: dict[str, Any], context: dict[str, Any]) -> str:
    path = args["path"]
    max_chars = int(args.get("max_chars") or 8000)

    cache = context.get("file_cache")
    if isinstance(cache, FileCache):
        content = cache.read(path)
    else:
        try:
            content = Path(path).read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return f"ERROR: could not read {path}: {exc}"

    if len(content) > max_chars:
        return content[:max_chars] + f"\n... (truncated at {max_chars} chars)"
    return content
