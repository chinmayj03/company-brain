"""find_entry_handler — locate the controller method that handles an endpoint."""
from __future__ import annotations

from typing import Any

from companybrain.agents.tools.code_tools import find_entry_handler as _find
from companybrain.harness.tools import register_tool
from companybrain.llm.base import ToolParameter


@register_tool(
    name="find_entry_handler",
    description=(
        "Find the HTTP handler (Spring Boot @Mapping, FastAPI @router.X, Express "
        "router.X) for a specific endpoint+method. Returns "
        "{file, class, method, matched_path} or {} when no match is found."
    ),
    parameters=[
        ToolParameter("endpoint", "string", "Endpoint path, e.g. '/users/{id}'."),
        ToolParameter("http_method", "string",
                      "HTTP verb. One of GET / POST / PUT / DELETE / PATCH.",
                      enum=["GET", "POST", "PUT", "DELETE", "PATCH"]),
        ToolParameter("repo_path", "string", "Absolute path to the repository root."),
    ],
)
async def handler(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    return _find(
        endpoint=args["endpoint"],
        http_method=args["http_method"],
        repo_path=args["repo_path"],
    )
