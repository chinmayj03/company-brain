# companybrain/mcp — ADR-006 Week 3: MCP tool surface.
# Exports the FastMCP app so it can be imported by the entry-point or tests.
from companybrain.mcp.server import mcp_app

__all__ = ["mcp_app"]
