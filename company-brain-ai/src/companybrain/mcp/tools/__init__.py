"""Tool registry — every tool registers itself here at import time."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Awaitable, Callable, Any


@dataclass
class Tool:
    name: str
    description: str
    schema: dict
    handler: Callable[[dict], Awaitable[str]]


TOOL_REGISTRY: dict[str, Tool] = {}


def register(tool: Tool) -> None:
    TOOL_REGISTRY[tool.name] = tool


# Import each tool module so it registers
from companybrain.mcp.tools import query as _q          # noqa: F401
from companybrain.mcp.tools import get as _g            # noqa: F401
from companybrain.mcp.tools import search as _s         # noqa: F401
from companybrain.mcp.tools import blast_radius as _br  # noqa: F401
from companybrain.mcp.tools import rebuild as _rb       # noqa: F401
